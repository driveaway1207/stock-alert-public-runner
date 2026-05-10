# -*- coding: utf-8 -*-
"""
A股全历史K线缓存 + 数据缓存体检表

目标：
1. 缓存层尽量保存所有A股：ST、新股、低质量股都缓存。
2. 缓存层不做选股过滤，只做数据获取和健康标记。
3. 全历史起点：1990-01-01，让数据源返回股票上市以来全部可得日K。
4. 不能只判断“缓存新鲜”，必须同时判断“历史是否足够长”。
5. rows=807 这种即使最新日期新鲜，也不能当作全量缓存。
6. FULL_REBUILD=1 时，强制回补历史不足缓存。
"""

import os
import time
import signal
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta

import requests
import pandas as pd
import baostock as bs
import akshare as ak


OUT_DIR = "outputs"
KLINE_CACHE_DIR = "kline_cache"

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(KLINE_CACHE_DIR, exist_ok=True)

FULL_HISTORY_START = "1990-01-01"
FULL_HISTORY_START_EM = "19900101"
INCREMENTAL_DAYS = 90

FULL_REBUILD = os.getenv("FULL_REBUILD", "0").strip() in ("1", "true", "True", "yes", "YES")
MIN_CACHE_FILES_REQUIRED = int(os.getenv("MIN_CACHE_FILES_REQUIRED", "3000"))

EASTMONEY_TIMEOUT = int(os.getenv("EASTMONEY_TIMEOUT", "20"))
AKSHARE_TIMEOUT = int(os.getenv("AKSHARE_TIMEOUT", "35"))
BAOSTOCK_TIMEOUT = int(os.getenv("BAOSTOCK_TIMEOUT", "60"))

SOURCE_FAIL_THRESHOLD = int(os.getenv("SOURCE_FAIL_THRESHOLD", "5"))
SOURCE_COOLDOWN_SECONDS = int(os.getenv("SOURCE_COOLDOWN_SECONDS", "900"))

PROGRESS_EVERY = int(os.getenv("PROGRESS_EVERY", "50"))
DETAIL_FIRST_N = int(os.getenv("DETAIL_FIRST_N", "20"))
DETAIL_EVERY = int(os.getenv("DETAIL_EVERY", "20"))

FULL_MIN_ROWS_STRONG = int(os.getenv("FULL_MIN_ROWS_STRONG", "1800"))
FULL_MIN_ROWS_MEDIUM = int(os.getenv("FULL_MIN_ROWS_MEDIUM", "1200"))
FULL_EARLY_DATE = os.getenv("FULL_EARLY_DATE", "2016-01-01")

BAOSTOCK_READY = False
FAILED_ROWS = []

SOURCE_STATE = {
    "EastMoney": {"success": 0, "failed": 0, "consecutive_fail": 0, "open_until": 0.0, "skipped": 0},
    "AkShare": {"success": 0, "failed": 0, "consecutive_fail": 0, "open_until": 0.0, "skipped": 0},
    "BaoStock": {"success": 0, "failed": 0, "consecutive_fail": 0, "open_until": 0.0, "skipped": 0},
}


class FetchTimeoutError(Exception):
    pass


@contextmanager
def time_limit(seconds, label="operation"):
    if seconds <= 0:
        yield
        return

    def handler(signum, frame):
        raise FetchTimeoutError(f"{label} timeout after {seconds}s")

    old = signal.signal(signal.SIGALRM, handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def log(msg):
    print(msg, flush=True)


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def fmt_seconds(sec):
    sec = int(max(sec, 0))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h:
        return f"{h}h{m}m{s}s"
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def stock_code(code):
    code = str(code).zfill(6)
    return ("SH." if code.startswith("6") else "SZ.") + code


def baostock_code(code):
    code = str(code).zfill(6)
    return ("sh." if code.startswith("6") else "sz.") + code


def eastmoney_secid(code):
    code = str(code).zfill(6)
    return ("1." if code.startswith("6") else "0.") + code


def cache_path(code):
    return os.path.join(KLINE_CACHE_DIR, f"{str(code).zfill(6)}.csv")


def count_cache_files():
    try:
        return len([f for f in os.listdir(KLINE_CACHE_DIR) if f.lower().endswith(".csv")])
    except Exception:
        return 0


def source_available(source):
    state = SOURCE_STATE[source]
    now = time.time()
    if now < state["open_until"]:
        state["skipped"] += 1
        return False
    return True


def record_source_success(source):
    state = SOURCE_STATE[source]
    state["success"] += 1
    state["consecutive_fail"] = 0
    state["open_until"] = 0.0


def record_source_failure(source, err):
    state = SOURCE_STATE[source]
    state["failed"] += 1
    state["consecutive_fail"] += 1

    if state["consecutive_fail"] >= SOURCE_FAIL_THRESHOLD:
        state["open_until"] = time.time() + SOURCE_COOLDOWN_SECONDS
        log(
            f"[CIRCUIT OPEN] source={source} "
            f"consecutive_fail={state['consecutive_fail']} "
            f"cooldown={fmt_seconds(SOURCE_COOLDOWN_SECONDS)} "
            f"last_err={repr(err)}"
        )


def normalize_daily_columns(df):
    if df is None or df.empty:
        return None

    rename_map = {
        "日期": "date",
        "交易日期": "date",
        "trade_date": "date",
        "Date": "date",
        "datetime": "date",
        "time": "date",
        "开盘": "open",
        "开盘价": "open",
        "Open": "open",
        "收盘": "close",
        "收盘价": "close",
        "Close": "close",
        "最高": "high",
        "最高价": "high",
        "High": "high",
        "最低": "low",
        "最低价": "low",
        "Low": "low",
        "成交量": "volume",
        "成交量(手)": "volume",
        "vol": "volume",
        "Volume": "volume",
        "成交额": "amount",
        "成交额(元)": "amount",
        "turnover": "amount",
        "Amount": "amount",
    }

    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})

    required = ["date", "open", "high", "low", "close", "volume"]
    if not all(c in df.columns for c in required):
        return None

    if "amount" not in df.columns:
        df["amount"] = 0

    df = df[["date", "open", "high", "low", "close", "volume", "amount"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date"])
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)

    if df.empty:
        return None

    return df


def read_cache(code):
    path = cache_path(code)
    if not os.path.exists(path):
        return None

    try:
        return normalize_daily_columns(pd.read_csv(path))
    except Exception as e:
        log(f"[WARN] read cache failed code={code} err={repr(e)}")
        return None


def save_cache(code, df):
    df = normalize_daily_columns(df)
    if df is None or df.empty:
        return False

    try:
        df.to_csv(cache_path(code), index=False, encoding="utf-8-sig")
        return True
    except Exception as e:
        log(f"[WARN] save cache failed code={code} err={repr(e)}")
        return False


def merge_kline(old_df, new_df):
    old_df = normalize_daily_columns(old_df)
    new_df = normalize_daily_columns(new_df)

    if old_df is None or old_df.empty:
        return new_df
    if new_df is None or new_df.empty:
        return old_df

    df = pd.concat([old_df, new_df], ignore_index=True)
    return normalize_daily_columns(df)


def cache_is_fresh(df):
    df = normalize_daily_columns(df)
    if df is None or df.empty:
        return False

    last_date = pd.to_datetime(df["date"].max()).date()
    now_date = datetime.now().date()
    return (now_date - last_date).days <= 5


def cache_history_is_full_enough(df):
    """
    判断缓存是否足够接近“上市以来全历史”。

    注意：
    - 不能只看最新日期。
    - rows=807 这种即使最新日期新鲜，也属于历史不足，需要 FULL_REBUILD=1 回补。
    - 对真正新股，回补后仍可能不足1800根，体检表会标记“限制使用”。
    """
    df = normalize_daily_columns(df)
    if df is None or df.empty:
        return False, "无缓存"

    n = len(df)
    first_date = pd.to_datetime(df["date"].min()).date()
    last_date = pd.to_datetime(df["date"].max()).date()
    early_date = datetime.strptime(FULL_EARLY_DATE, "%Y-%m-%d").date()

    if n >= FULL_MIN_ROWS_STRONG:
        return True, f"历史充足 rows={n} first={first_date} last={last_date}"

    if first_date <= early_date and n >= FULL_MIN_ROWS_MEDIUM:
        return True, f"历史基本充足 rows={n} first={first_date} last={last_date}"

    return False, f"历史不足 rows={n} first={first_date} last={last_date}"


def baostock_login_once():
    global BAOSTOCK_READY

    if BAOSTOCK_READY:
        return True

    lg = bs.login()
    log(f"[BAOSTOCK LOGIN] {lg.error_code} {lg.error_msg}")

    if lg.error_code == "0":
        BAOSTOCK_READY = True
        return True

    BAOSTOCK_READY = False
    return False


def baostock_logout_once():
    global BAOSTOCK_READY

    if BAOSTOCK_READY:
        try:
            bs.logout()
            log("[BAOSTOCK LOGOUT] success")
        except Exception as e:
            log(f"[WARN] baostock logout failed: {repr(e)}")

    BAOSTOCK_READY = False


def fetch_stock_list_from_akshare():
    with time_limit(AKSHARE_TIMEOUT, "akshare stock list"):
        df = ak.stock_info_a_code_name()

    df["code"] = df["code"].astype(str).str.zfill(6)
    df["name"] = df["name"].astype(str)
    df = df[df["code"].str.match(r"^\d{6}$", na=False)].copy()
    df = df[df["code"].str.startswith(("0", "3", "6"))].copy()

    return df[["code", "name"]]


def fetch_stock_list_from_baostock():
    if not baostock_login_once():
        raise RuntimeError("baostock login failed")

    with time_limit(BAOSTOCK_TIMEOUT, "baostock stock list"):
        rs = bs.query_all_stock(day=today_str())
        rows = []
        fields = rs.fields
        while rs.next():
            rows.append(rs.get_row_data())

    if not rows:
        raise RuntimeError("baostock query_all_stock returned empty")

    df = pd.DataFrame(rows, columns=fields)
    df = df[df["code"].str.startswith(("sh.6", "sz.0", "sz.3"))].copy()
    df["code"] = df["code"].str[-6:]
    df["name"] = df.get("code_name", df["code"])

    return df[["code", "name"]]


def fetch_stock_list():
    for attempt in range(3):
        try:
            log(f"[FETCH LIST] source=akshare attempt={attempt + 1}")
            df = fetch_stock_list_from_akshare()
            if df is not None and not df.empty:
                df = df.drop_duplicates("code").reset_index(drop=True)
                log(f"[FETCH LIST OK] source=akshare stocks={len(df)}")
                return df
        except Exception as e:
            log(f"[WARN] akshare stock list failed attempt={attempt + 1}: {repr(e)}")
            time.sleep(3)

    for attempt in range(3):
        try:
            log(f"[FETCH LIST] source=baostock attempt={attempt + 1}")
            df = fetch_stock_list_from_baostock()
            if df is not None and not df.empty:
                df = df.drop_duplicates("code").reset_index(drop=True)
                log(f"[FETCH LIST OK] source=baostock stocks={len(df)}")
                return df
        except Exception as e:
            log(f"[WARN] baostock stock list failed attempt={attempt + 1}: {repr(e)}")
            time.sleep(3)

    raise RuntimeError("all stock list sources failed")


def fetch_daily_from_eastmoney(code, beg=FULL_HISTORY_START_EM):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    params = {
        "secid": eastmoney_secid(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "101",
        "fqt": "1",
        "beg": beg.replace("-", ""),
        "end": datetime.now().strftime("%Y%m%d"),
        "lmt": "1000000",
    }

    r = requests.get(
        url,
        params=params,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
            "Accept": "application/json,text/plain,*/*",
        },
        timeout=EASTMONEY_TIMEOUT,
    )
    r.raise_for_status()

    js = r.json()
    klines = (js.get("data") or {}).get("klines") or []

    rows = []
    for item in klines:
        p = item.split(",")
        if len(p) >= 7:
            rows.append({
                "date": p[0],
                "open": p[1],
                "close": p[2],
                "high": p[3],
                "low": p[4],
                "volume": p[5],
                "amount": p[6],
            })

    return normalize_daily_columns(pd.DataFrame(rows))


def fetch_daily_from_akshare(code):
    with time_limit(AKSHARE_TIMEOUT, f"akshare kline {code}"):
        df = ak.stock_zh_a_hist(
            symbol=str(code).zfill(6),
            period="daily",
            adjust="qfq",
        )
    return normalize_daily_columns(df)


def fetch_daily_from_baostock(code, start_date=FULL_HISTORY_START):
    if not BAOSTOCK_READY:
        if not baostock_login_once():
            return None

    with time_limit(BAOSTOCK_TIMEOUT, f"baostock kline {code}"):
        rs = bs.query_history_k_data_plus(
            baostock_code(code),
            "date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=today_str(),
            frequency="d",
            adjustflag="2",
        )

        rows = []
        fields = rs.fields

        while rs.next():
            rows.append(rs.get_row_data())

    if not rows:
        return None

    return normalize_daily_columns(pd.DataFrame(rows, columns=fields))


def fetch_from_source(code, source, mode, start_str, em_start):
    if not source_available(source):
        log(f"[FETCH KLINE SKIP] code={code} source={source} reason=circuit_open")
        return None

    try:
        t0 = time.time()

        if source == "EastMoney":
            log(f"[FETCH KLINE START] code={code} source=EastMoney mode={mode} start={em_start}")
            df = fetch_daily_from_eastmoney(code, em_start)
        elif source == "AkShare":
            log(f"[FETCH KLINE START] code={code} source=AkShare mode={mode}")
            df = fetch_daily_from_akshare(code)
        elif source == "BaoStock":
            log(f"[FETCH KLINE START] code={code} source=BaoStock mode={mode} start={start_str}")
            df = fetch_daily_from_baostock(code, start_str)
        else:
            raise ValueError(f"unknown source={source}")

        cost = fmt_seconds(time.time() - t0)

        if df is not None and not df.empty:
            record_source_success(source)
            log(f"[FETCH KLINE OK] code={code} source={source} rows={len(df)} cost={cost}")
            return df

        record_source_failure(source, "empty dataframe")
        log(f"[FETCH KLINE EMPTY] code={code} source={source} cost={cost}")
        return None

    except Exception as e:
        record_source_failure(source, e)
        log(f"[WARN] {source.lower()} fetch failed code={code} err={repr(e)}")
        return None


def fetch_incremental_or_full(code, cache_df):
    if cache_df is not None and not cache_df.empty:
        last_date = pd.to_datetime(cache_df["date"].max()).date()
        start_date = last_date - timedelta(days=INCREMENTAL_DAYS)
        start_str = start_date.strftime("%Y-%m-%d")
        em_start = start_str.replace("-", "")
        mode = "incremental"
    else:
        start_str = FULL_HISTORY_START
        em_start = FULL_HISTORY_START_EM
        mode = "full"

    for source in ["EastMoney", "AkShare", "BaoStock"]:
        df = fetch_from_source(code, source, mode, start_str, em_start)
        if df is not None and not df.empty:
            return df, source

    return None, "全部失败"


def fetch_full_history(code):
    start_str = FULL_HISTORY_START
    em_start = FULL_HISTORY_START_EM
    mode = "force_full"

    for source in ["EastMoney", "AkShare", "BaoStock"]:
        df = fetch_from_source(code, source, mode, start_str, em_start)
        if df is not None and not df.empty:
            return df, source

    return None, "全部失败"


def get_daily_kline(code):
    code = str(code).zfill(6)

    cache_df = read_cache(code)
    cache_exists = cache_df is not None and not cache_df.empty
    fresh = cache_is_fresh(cache_df) if cache_exists else False
    full_enough, history_reason = cache_history_is_full_enough(cache_df)

    # 只有“新鲜 + 历史足够长”才直接使用缓存
    if cache_exists and fresh and full_enough:
        return cache_df, "有", "新鲜", f"无需更新:{history_reason}", "缓存"

    # 缓存存在但历史不足：FULL_REBUILD=1 时强制全历史回补
    if cache_exists and not full_enough:
        log(
            f"[CACHE PARTIAL] code={code} fresh={fresh} "
            f"reason={history_reason} action=force_full_backfill"
        )

        if not FULL_REBUILD:
            return cache_df, "有", "非全量", f"历史不足但FULL_REBUILD=0:{history_reason}", "缓存"

        full_df, source = fetch_full_history(code)
        merged = merge_kline(cache_df, full_df)

        if merged is not None and not merged.empty:
            save_cache(code, merged)
            freshness = "新鲜" if cache_is_fresh(merged) else "偏旧"
            full_after, reason_after = cache_history_is_full_enough(merged)
            status = "全历史回补完成" if full_after else "已回补但历史仍不足"
            return merged, "有", freshness, f"{status}:{source}:{reason_after}", source

        return cache_df, "有", "非全量", f"全历史回补失败，保留旧缓存:{history_reason}", "缓存"

    # 缓存存在、历史够长、但最新日期偏旧：增量更新
    if cache_exists and full_enough and not fresh:
        log(f"[CACHE STALE] code={code} reason={history_reason} action=incremental_update")
        new_df, source = fetch_incremental_or_full(code, cache_df)
        merged = merge_kline(cache_df, new_df)

        if merged is not None and not merged.empty:
            save_cache(code, merged)
            freshness = "新鲜" if cache_is_fresh(merged) else "偏旧"
            full_after, reason_after = cache_history_is_full_enough(merged)
            return merged, "有", freshness, f"已增量更新:{source}:{reason_after}", source

        return cache_df, "有", "偏旧", f"增量更新失败，保留旧缓存:{history_reason}", "缓存"

    # 没缓存：FULL_REBUILD=1 才允许首次建立
    if not cache_exists:
        if not FULL_REBUILD:
            return None, "无", "待建立", "跳过建立:FULL_REBUILD=0且无本地缓存", "未联网"

        full_df, source = fetch_full_history(code)

        if full_df is not None and not full_df.empty:
            save_cache(code, full_df)
            freshness = "新鲜" if cache_is_fresh(full_df) else "偏旧"
            full_after, reason_after = cache_history_is_full_enough(full_df)
            status = "首次全历史建立完成" if full_after else "首次建立但历史仍不足"
            return full_df, "无", freshness, f"{status}:{source}:{reason_after}", source

        return None, "无", "不可用", "首次全历史建立失败", "全部失败"

    return cache_df, "有", "未知", "未命中逻辑分支", "缓存"


def max_calendar_gap_days(df):
    if df is None or df.empty or len(df) < 2:
        return ""

    dates = pd.to_datetime(df["date"]).sort_values()
    gaps = dates.diff().dt.days.dropna()

    if gaps.empty:
        return ""

    return int(gaps.max())


def latest_gap_days(df):
    if df is None or df.empty:
        return ""

    last_date = pd.to_datetime(df["date"].max()).date()
    return int((datetime.now().date() - last_date).days)


def first_bad_date(df, mask):
    bad = df[mask]
    if bad.empty:
        return ""
    return bad.iloc[0]["date"].strftime("%Y-%m-%d")


def inspect_kline(df, code, name, cache_exists, freshness, update_status, fetch_source):
    item = {
        "股票代码": stock_code(code),
        "股票名称": name,
        "市场": "沪市" if str(code).startswith("6") else "深市",
        "是否ST": "是" if "ST" in str(name).upper() else "否",
        "是否N/C新股": "是" if str(name).startswith(("N", "C")) else "否",

        "缓存是否存在": cache_exists,
        "缓存是否新鲜": freshness,
        "缓存更新状态": update_status,
        "本次使用数据源": fetch_source,

        "K线起始日期": "",
        "K线最新日期": "",
        "距离今天自然日": "",
        "K线总根数": 0,
        "最大自然日断档": "",

        "数据长度等级": "数据不可用",
        "可用于日线短周期": "否",
        "可用于年度结构": "否",
        "可用于长周期时间模型": "否",
        "可用于月线季线模型": "否",

        "开高低收缺失数": 0,
        "价格为0数量": 0,
        "high_low错误数": 0,
        "high小于开收数量": 0,
        "low大于开收数量": 0,
        "价格异常日期示例": "",

        "成交量缺失数": 0,
        "成交量为0数量": 0,
        "成交额缺失数": 0,
        "最近20根K零成交天数": 0,
        "成交量异常日期示例": "",

        "数据体检结论": "不通过",
        "机器备注": "K线数据不可用",
    }

    df = normalize_daily_columns(df)

    if df is None or df.empty:
        if freshness == "待建立":
            item["数据体检结论"] = "待建立缓存"
            item["机器备注"] = update_status
        return item

    n = len(df)
    first_date = pd.to_datetime(df["date"].min())
    last_date = pd.to_datetime(df["date"].max())

    item["K线起始日期"] = first_date.strftime("%Y-%m-%d")
    item["K线最新日期"] = last_date.strftime("%Y-%m-%d")
    item["距离今天自然日"] = latest_gap_days(df)
    item["K线总根数"] = n
    item["最大自然日断档"] = max_calendar_gap_days(df)

    if n < 120:
        item["数据长度等级"] = "少于120根，仅保留缓存"
    elif n < 250:
        item["数据长度等级"] = "120-249根，仅短周期观察"
        item["可用于日线短周期"] = "是"
    elif n < 2000:
        item["数据长度等级"] = "250-1999根，可做基础结构"
        item["可用于日线短周期"] = "是"
        item["可用于年度结构"] = "是"
        item["可用于月线季线模型"] = "视月线数量"
    else:
        item["数据长度等级"] = "2000根以上，适合长周期模型"
        item["可用于日线短周期"] = "是"
        item["可用于年度结构"] = "是"
        item["可用于长周期时间模型"] = "是"
        item["可用于月线季线模型"] = "是"

    missing_ohlc = df[["open", "high", "low", "close"]].isna().any(axis=1)
    zero_price = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    high_low_error = df["high"] < df["low"]
    high_less_oc = (df["high"] < df["open"]) | (df["high"] < df["close"])
    low_greater_oc = (df["low"] > df["open"]) | (df["low"] > df["close"])

    price_error = missing_ohlc | zero_price | high_low_error | high_less_oc | low_greater_oc

    item["开高低收缺失数"] = int(missing_ohlc.sum())
    item["价格为0数量"] = int(zero_price.sum())
    item["high_low错误数"] = int(high_low_error.sum())
    item["high小于开收数量"] = int(high_less_oc.sum())
    item["low大于开收数量"] = int(low_greater_oc.sum())
    item["价格异常日期示例"] = first_bad_date(df, price_error)

    volume_missing = df["volume"].isna()
    volume_zero = df["volume"] <= 0
    amount_missing = df["amount"].isna()

    recent20 = df.tail(20)
    recent20_zero = int((recent20["volume"] <= 0).sum()) if not recent20.empty else 0

    volume_error = volume_missing | volume_zero | amount_missing

    item["成交量缺失数"] = int(volume_missing.sum())
    item["成交量为0数量"] = int(volume_zero.sum())
    item["成交额缺失数"] = int(amount_missing.sum())
    item["最近20根K零成交天数"] = recent20_zero
    item["成交量异常日期示例"] = first_bad_date(df, volume_error)

    hard_errors = []
    warnings = []

    full_enough, history_reason = cache_history_is_full_enough(df)

    if price_error.any():
        hard_errors.append("存在价格硬错误")
    if n == 0:
        hard_errors.append("无K线")
    if freshness == "不可用":
        hard_errors.append("缓存不可用")

    if not full_enough:
        warnings.append(f"历史长度不足或疑似非全量：{history_reason}")

    if "ST" in str(name).upper():
        warnings.append("ST，仅缓存，模型层过滤")
    if str(name).startswith(("N", "C")):
        warnings.append("N/C新股，仅缓存，模型层过滤")
    if n < 120:
        warnings.append("K线少于120根")
    elif n < 250:
        warnings.append("K线少于250根")
    elif n < 2000:
        warnings.append("K线少于2000根，长周期时间模型慎用")

    if recent20_zero >= 10:
        warnings.append("最近20根K中零成交过多")
    if item["距离今天自然日"] != "" and item["距离今天自然日"] > 10:
        warnings.append("最新K距离今天超过10个自然日，可能停牌或数据未更新")

    if hard_errors:
        item["数据体检结论"] = "不通过"
        item["机器备注"] = "；".join(hard_errors + warnings)
    elif warnings:
        item["数据体检结论"] = "通过但限制使用"
        item["机器备注"] = "；".join(warnings)
    else:
        item["数据体检结论"] = "通过"
        item["机器备注"] = "数据层可用；历史长度充足"

    return item


def save_outputs(rows, failed_rows, elapsed, success, failed):
    today = today_str()

    out_path = os.path.join(OUT_DIR, f"data_cache_health_full_{today}.csv")
    failed_path = os.path.join(OUT_DIR, f"data_cache_failed_{today}.csv")

    df = pd.DataFrame(rows)

    cols = [
        "股票代码",
        "股票名称",
        "市场",
        "是否ST",
        "是否N/C新股",

        "缓存是否存在",
        "缓存是否新鲜",
        "缓存更新状态",
        "本次使用数据源",

        "K线起始日期",
        "K线最新日期",
        "距离今天自然日",
        "K线总根数",
        "最大自然日断档",

        "数据长度等级",
        "可用于日线短周期",
        "可用于年度结构",
        "可用于长周期时间模型",
        "可用于月线季线模型",

        "开高低收缺失数",
        "价格为0数量",
        "high_low错误数",
        "high小于开收数量",
        "low大于开收数量",
        "价格异常日期示例",

        "成交量缺失数",
        "成交量为0数量",
        "成交额缺失数",
        "最近20根K零成交天数",
        "成交量异常日期示例",

        "数据体检结论",
        "机器备注",
    ]

    if not df.empty:
        df = df[cols]
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame([{
            "诊断": "未生成数据缓存体检结果",
            "success": success,
            "failed": failed,
            "elapsed": fmt_seconds(elapsed),
        }]).to_csv(out_path, index=False, encoding="utf-8-sig")

    if failed_rows:
        pd.DataFrame(failed_rows).to_csv(failed_path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame([{"诊断": "无失败股票"}]).to_csv(failed_path, index=False, encoding="utf-8-sig")

    log(f"[CSV] {out_path}")
    log(f"[FAILED CSV] {failed_path}")


def save_cache_restore_guard(existing_cache_count):
    path = os.path.join(OUT_DIR, f"cache_restore_guard_{today_str()}.csv")
    pd.DataFrame([{
        "诊断": "缓存恢复数量过少，已停止，避免从零全量重建",
        "existing_csv": existing_cache_count,
        "required": MIN_CACHE_FILES_REQUIRED,
        "FULL_REBUILD": FULL_REBUILD,
        "建议": "先确认 actions/cache 是否恢复了 a-kline-cache。如果确认要从0重建，手动运行时设置 full_rebuild=1。",
    }]).to_csv(path, index=False, encoding="utf-8-sig")
    log(f"[GUARD CSV] {path}")


def main():
    start_ts = time.time()

    rows = []
    success = 0
    failed = 0

    try:
        existing_cache_count = count_cache_files()

        log(f"[CONFIG] FULL_REBUILD={FULL_REBUILD}")
        log(f"[CONFIG] MIN_CACHE_FILES_REQUIRED={MIN_CACHE_FILES_REQUIRED}")
        log(f"[CONFIG] FULL_MIN_ROWS_STRONG={FULL_MIN_ROWS_STRONG}")
        log(f"[CONFIG] FULL_MIN_ROWS_MEDIUM={FULL_MIN_ROWS_MEDIUM}")
        log(f"[CONFIG] FULL_EARLY_DATE={FULL_EARLY_DATE}")
        log(f"[CACHE FILES] dir={KLINE_CACHE_DIR} existing_csv={existing_cache_count}")

        if existing_cache_count < MIN_CACHE_FILES_REQUIRED and not FULL_REBUILD:
            log(
                f"[FATAL] restored cache too small: existing_csv={existing_cache_count}, "
                f"required>={MIN_CACHE_FILES_REQUIRED}. "
                f"This run will NOT rebuild all stocks from scratch. "
                f"Set FULL_REBUILD=1 only if you really want full rebuild."
            )
            save_cache_restore_guard(existing_cache_count)
            return

        stocks = fetch_stock_list()
        total = len(stocks)

        log(f"[START] full-history data cache health stocks={total}")
        log(
            f"[NOTE] detail policy: first {DETAIL_FIRST_N} stocks print every stock, "
            f"then every {DETAIL_EVERY}; summary every {PROGRESS_EVERY}"
        )

        for idx, row in stocks.iterrows():
            done = idx + 1
            code = str(row["code"]).zfill(6)
            name = str(row["name"])

            elapsed = time.time() - start_ts
            avg = elapsed / max(done, 1)
            remain = avg * (total - done)

            should_print_detail = done <= DETAIL_FIRST_N or done % DETAIL_EVERY == 0 or done == total

            if should_print_detail:
                log(
                    f"[CACHE CHECK START] {done}/{total} "
                    f"code={code} name={name} "
                    f"elapsed={fmt_seconds(elapsed)} eta={fmt_seconds(remain)}"
                )

            try:
                t0 = time.time()
                df, cache_exists, freshness, update_status, fetch_source = get_daily_kline(code)
                item = inspect_kline(df, code, name, cache_exists, freshness, update_status, fetch_source)
                rows.append(item)
                success += 1

                if should_print_detail:
                    row_count = 0 if df is None else len(df)
                    log(
                        f"[CACHE CHECK OK] {done}/{total} "
                        f"code={code} name={name} "
                        f"source={fetch_source} cache={cache_exists} "
                        f"freshness={freshness} status={update_status} "
                        f"rows={row_count} cost={fmt_seconds(time.time() - t0)}"
                    )

            except Exception as e:
                failed += 1
                FAILED_ROWS.append({
                    "股票代码": stock_code(code),
                    "股票名称": name,
                    "失败原因": repr(e),
                })
                log(f"[ERROR] {done}/{total} code={code} name={name}: {repr(e)}")
                traceback.print_exc(limit=1)

            if done % PROGRESS_EVERY == 0 or done == total:
                elapsed = time.time() - start_ts
                avg = elapsed / max(done, 1)
                remain = avg * (total - done)
                pct = done / total * 100

                log(
                    f"[PROGRESS] {done}/{total} ({pct:.2f}%) | "
                    f"success={success} failed={failed} | "
                    f"elapsed={fmt_seconds(elapsed)} eta={fmt_seconds(remain)} | "
                    f"cache_files={count_cache_files()}"
                )

            time.sleep(0.003)

    finally:
        elapsed = time.time() - start_ts
        save_outputs(rows, FAILED_ROWS, elapsed, success, failed)
        baostock_logout_once()

        log(f"[SOURCE STATE] {SOURCE_STATE}")
        log(
            f"[DONE] success={success}, failed={failed}, "
            f"elapsed={fmt_seconds(elapsed)}, cache_files={count_cache_files()}"
        )


if __name__ == "__main__":
    main()
