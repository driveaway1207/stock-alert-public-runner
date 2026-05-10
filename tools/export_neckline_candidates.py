# -*- coding: utf-8 -*-
"""
A股上市以来全历史K线缓存建设 + 数据缓存体检表

核心逻辑：
1. 缓存层尽量保存所有A股：ST、新股、低质量股都缓存。
2. 缓存层不做选股过滤，只做数据获取、增量更新、全历史回补、健康标记。
3. 全历史起点：1990-01-01，让数据源返回股票上市以来全部可得日K。
4. 不能只判断“缓存新鲜”，必须同时判断“历史是否足够长 / 是否已确认全历史”。
5. rows=807 这种旧股近几年缓存，即使最新日期新鲜，也进入全历史回补队列。
6. 新股/短历史股票：一旦执行过从1990开始的全历史拉取，会写入 meta，后续不反复误判为非全量。
7. 已确认全历史的股票，后续每天只做增量更新，不会从1990重复拉。
8. GitHub Actions 单次不贴近6小时上限，本脚本默认小批次试运行。
9. 单线程低速请求，保护 BaoStock。
"""

import os
import re
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
META_PATH = os.path.join(KLINE_CACHE_DIR, "_full_history_meta.csv")

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(KLINE_CACHE_DIR, exist_ok=True)

FULL_HISTORY_START = "1990-01-01"
FULL_HISTORY_START_EM = "19900101"
INCREMENTAL_DAYS = int(os.getenv("INCREMENTAL_DAYS", "90"))

FULL_REBUILD = os.getenv("FULL_REBUILD", "1").strip() in ("1", "true", "True", "yes", "YES")
MIN_CACHE_FILES_REQUIRED = int(os.getenv("MIN_CACHE_FILES_REQUIRED", "3000"))

# 小批次测试默认值：30只 / 60分钟
MAX_FULL_BACKFILL_PER_RUN = int(os.getenv("MAX_FULL_BACKFILL_PER_RUN", "30"))
MAX_RUNTIME_MINUTES = int(os.getenv("MAX_RUNTIME_MINUTES", "60"))
SAFE_STOP_MINUTES = int(os.getenv("SAFE_STOP_MINUTES", "10"))

BAOSTOCK_SLEEP_SECONDS = float(os.getenv("BAOSTOCK_SLEEP_SECONDS", "1.5"))

EASTMONEY_TIMEOUT = int(os.getenv("EASTMONEY_TIMEOUT", "20"))
AKSHARE_TIMEOUT = int(os.getenv("AKSHARE_TIMEOUT", "35"))
BAOSTOCK_TIMEOUT = int(os.getenv("BAOSTOCK_TIMEOUT", "70"))

SOURCE_FAIL_THRESHOLD = int(os.getenv("SOURCE_FAIL_THRESHOLD", "5"))
SOURCE_COOLDOWN_SECONDS = int(os.getenv("SOURCE_COOLDOWN_SECONDS", "900"))

PROGRESS_EVERY = int(os.getenv("PROGRESS_EVERY", "50"))
DETAIL_FIRST_N = int(os.getenv("DETAIL_FIRST_N", "20"))
DETAIL_EVERY = int(os.getenv("DETAIL_EVERY", "20"))

FULL_MIN_ROWS_STRONG = int(os.getenv("FULL_MIN_ROWS_STRONG", "1800"))
FULL_MIN_ROWS_MEDIUM = int(os.getenv("FULL_MIN_ROWS_MEDIUM", "1200"))
FULL_EARLY_DATE = os.getenv("FULL_EARLY_DATE", "2016-01-01")

# 0 = 要求最新日期至少是今天；适合A股收盘后晚间跑，确保每日新K能补齐。
# 如果节假日/周末运行，可临时调成 3~5。
FRESH_MAX_NATURAL_DAYS = int(os.getenv("FRESH_MAX_NATURAL_DAYS", "0"))

BAOSTOCK_READY = False
FAILED_ROWS = []

RUN_STATE = {
    "processed": 0,
    "success": 0,
    "failed": 0,

    "cache_full_ready": 0,
    "cache_full_but_stale": 0,
    "cache_partial": 0,
    "no_cache": 0,

    "incremental_attempted": 0,
    "incremental_success": 0,
    "incremental_failed": 0,

    "backfill_attempted": 0,
    "backfill_success": 0,
    "backfill_failed": 0,
    "backfill_skipped_limit": 0,
    "backfill_skipped_time": 0,
    "backfill_skipped_full_rebuild_off": 0,

    "safe_stop": False,
    "safe_stop_reason": "",
}

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
        return len([
            f for f in os.listdir(KLINE_CACHE_DIR)
            if re.match(r"^\d{6}\.csv$", f.lower())
        ])
    except Exception:
        return 0


def should_safe_stop(start_ts):
    elapsed_minutes = (time.time() - start_ts) / 60
    if elapsed_minutes >= max(1, MAX_RUNTIME_MINUTES - SAFE_STOP_MINUTES):
        RUN_STATE["safe_stop"] = True
        RUN_STATE["safe_stop_reason"] = (
            f"elapsed={elapsed_minutes:.1f}m reached safe stop window "
            f"(MAX_RUNTIME_MINUTES={MAX_RUNTIME_MINUTES}, SAFE_STOP_MINUTES={SAFE_STOP_MINUTES})"
        )
        return True
    return False


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


def load_meta():
    if not os.path.exists(META_PATH):
        return {}

    try:
        df = pd.read_csv(META_PATH, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        meta = {}
        for _, r in df.iterrows():
            code = str(r["code"]).zfill(6)
            meta[code] = {
                "code": code,
                "full_confirmed": int(r.get("full_confirmed", 0) or 0),
                "last_full_fetch_date": str(r.get("last_full_fetch_date", "")),
                "full_source": str(r.get("full_source", "")),
                "first_date": str(r.get("first_date", "")),
                "last_date": str(r.get("last_date", "")),
                "rows": int(r.get("rows", 0) or 0),
                "updated_at": str(r.get("updated_at", "")),
            }
        return meta
    except Exception as e:
        log(f"[WARN] load meta failed err={repr(e)}")
        return {}


def save_meta(meta):
    rows = []
    for code, item in sorted(meta.items()):
        rows.append({
            "code": str(code).zfill(6),
            "full_confirmed": int(item.get("full_confirmed", 0) or 0),
            "last_full_fetch_date": item.get("last_full_fetch_date", ""),
            "full_source": item.get("full_source", ""),
            "first_date": item.get("first_date", ""),
            "last_date": item.get("last_date", ""),
            "rows": int(item.get("rows", 0) or 0),
            "updated_at": item.get("updated_at", ""),
        })

    try:
        pd.DataFrame(rows).to_csv(META_PATH, index=False, encoding="utf-8-sig")
        log(f"[META] saved {META_PATH} rows={len(rows)}")
    except Exception as e:
        log(f"[WARN] save meta failed err={repr(e)}")


def update_full_meta(meta, code, df, source):
    df = normalize_daily_columns(df)
    if df is None or df.empty:
        return

    code = str(code).zfill(6)
    meta[code] = {
        "code": code,
        "full_confirmed": 1,
        "last_full_fetch_date": today_str(),
        "full_source": source,
        "first_date": pd.to_datetime(df["date"].min()).strftime("%Y-%m-%d"),
        "last_date": pd.to_datetime(df["date"].max()).strftime("%Y-%m-%d"),
        "rows": int(len(df)),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def update_meta_after_increment(meta, code, df):
    df = normalize_daily_columns(df)
    if df is None or df.empty:
        return

    code = str(code).zfill(6)
    old = meta.get(code, {})
    if int(old.get("full_confirmed", 0) or 0) != 1:
        return

    old.update({
        "first_date": pd.to_datetime(df["date"].min()).strftime("%Y-%m-%d"),
        "last_date": pd.to_datetime(df["date"].max()).strftime("%Y-%m-%d"),
        "rows": int(len(df)),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    meta[code] = old


def cache_is_current(df):
    df = normalize_daily_columns(df)
    if df is None or df.empty:
        return False

    last_date = pd.to_datetime(df["date"].max()).date()
    now_date = datetime.now().date()
    return (now_date - last_date).days <= FRESH_MAX_NATURAL_DAYS


def cache_history_is_full_enough(code, df, meta):
    """
    判断缓存是否接近“上市以来全历史”。

    关键：
    1. meta full_confirmed=1 的股票，说明已经从1990发起过全历史拉取；
       即使是新股/短历史，也不再反复回补。
    2. 没有 meta 的旧缓存，必须靠 rows / first_date 判断。
    3. rows=807 且 first_date很晚，属于历史不足，需要回补。
    """
    code = str(code).zfill(6)
    df = normalize_daily_columns(df)
    if df is None or df.empty:
        return False, "无缓存"

    n = len(df)
    first_date = pd.to_datetime(df["date"].min()).date()
    last_date = pd.to_datetime(df["date"].max()).date()
    early_date = datetime.strptime(FULL_EARLY_DATE, "%Y-%m-%d").date()

    m = meta.get(code, {})
    if int(m.get("full_confirmed", 0) or 0) == 1:
        return True, f"meta已确认全历史 rows={n} first={first_date} last={last_date}"

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
            if BAOSTOCK_SLEEP_SECONDS > 0:
                time.sleep(BAOSTOCK_SLEEP_SECONDS)

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


def fetch_incremental(code, cache_df):
    cache_df = normalize_daily_columns(cache_df)
    if cache_df is None or cache_df.empty:
        return None, "全部失败"

    last_date = pd.to_datetime(cache_df["date"].max()).date()
    start_date = last_date - timedelta(days=INCREMENTAL_DAYS)
    start_str = start_date.strftime("%Y-%m-%d")
    em_start = start_str.replace("-", "")
    mode = "incremental"

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


def can_backfill_more(start_ts):
    if not FULL_REBUILD:
        RUN_STATE["backfill_skipped_full_rebuild_off"] += 1
        return False, "FULL_REBUILD=0"

    if should_safe_stop(start_ts):
        RUN_STATE["backfill_skipped_time"] += 1
        return False, RUN_STATE["safe_stop_reason"]

    if RUN_STATE["backfill_attempted"] >= MAX_FULL_BACKFILL_PER_RUN:
        RUN_STATE["backfill_skipped_limit"] += 1
        return False, f"本轮全历史回补额度已满 MAX_FULL_BACKFILL_PER_RUN={MAX_FULL_BACKFILL_PER_RUN}"

    return True, "允许回补"


def get_daily_kline(code, name, meta, start_ts):
    code = str(code).zfill(6)

    cache_df = read_cache(code)
    cache_exists = cache_df is not None and not cache_df.empty
    current = cache_is_current(cache_df) if cache_exists else False
    full_enough, history_reason = cache_history_is_full_enough(code, cache_df, meta)

    # A类：已全历史 + 最新
    if cache_exists and full_enough and current:
        RUN_STATE["cache_full_ready"] += 1
        return cache_df, "有", "最新", f"无需更新:{history_reason}", "缓存"

    # B类：已全历史 + 不最新 -> 增量更新
    if cache_exists and full_enough and not current:
        RUN_STATE["cache_full_but_stale"] += 1

        if should_safe_stop(start_ts):
            return cache_df, "有", "偏旧", f"安全收尾，跳过增量:{history_reason}", "缓存"

        log(f"[CACHE STALE] code={code} reason={history_reason} action=incremental_update")
        RUN_STATE["incremental_attempted"] += 1

        new_df, source = fetch_incremental(code, cache_df)
        merged = merge_kline(cache_df, new_df)

        if merged is not None and not merged.empty:
            save_cache(code, merged)
            update_meta_after_increment(meta, code, merged)
            RUN_STATE["incremental_success"] += 1
            freshness = "最新" if cache_is_current(merged) else "偏旧"
            full_after, reason_after = cache_history_is_full_enough(code, merged, meta)
            return merged, "有", freshness, f"已增量更新:{source}:{reason_after}", source

        RUN_STATE["incremental_failed"] += 1
        return cache_df, "有", "偏旧", f"增量更新失败，保留旧缓存:{history_reason}", "缓存"

    # C类：缓存存在但历史不足 -> 限量全历史回补
    if cache_exists and not full_enough:
        RUN_STATE["cache_partial"] += 1
        log(
            f"[CACHE PARTIAL] code={code} name={name} current={current} "
            f"reason={history_reason} action=full_backfill_queue"
        )

        allowed, reason = can_backfill_more(start_ts)
        if not allowed:
            return cache_df, "有", "非全量", f"待全历史回补:{reason}:{history_reason}", "缓存"

        RUN_STATE["backfill_attempted"] += 1
        full_df, source = fetch_full_history(code)
        merged = merge_kline(cache_df, full_df)

        if merged is not None and not merged.empty:
            save_cache(code, merged)
            update_full_meta(meta, code, merged, source)
            RUN_STATE["backfill_success"] += 1
            freshness = "最新" if cache_is_current(merged) else "偏旧"
            full_after, reason_after = cache_history_is_full_enough(code, merged, meta)
            status = "全历史回补完成" if full_after else "已回补但仍需复核"
            return merged, "有", freshness, f"{status}:{source}:{reason_after}", source

        RUN_STATE["backfill_failed"] += 1
        return cache_df, "有", "非全量", f"全历史回补失败，保留旧缓存:{history_reason}", "缓存"

    # D类：无缓存 -> 限量首次全历史建档
    if not cache_exists:
        RUN_STATE["no_cache"] += 1
        allowed, reason = can_backfill_more(start_ts)
        if not allowed:
            return None, "无", "待建立", f"待首次全历史建立:{reason}", "未联网"

        RUN_STATE["backfill_attempted"] += 1
        full_df, source = fetch_full_history(code)

        if full_df is not None and not full_df.empty:
            save_cache(code, full_df)
            update_full_meta(meta, code, full_df, source)
            RUN_STATE["backfill_success"] += 1
            freshness = "最新" if cache_is_current(full_df) else "偏旧"
            full_after, reason_after = cache_history_is_full_enough(code, full_df, meta)
            status = "首次全历史建立完成" if full_after else "首次建立但仍需复核"
            return full_df, "无", freshness, f"{status}:{source}:{reason_after}", source

        RUN_STATE["backfill_failed"] += 1
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


def inspect_kline(df, code, name, cache_exists, freshness, update_status, fetch_source, meta):
    item = {
        "股票代码": stock_code(code),
        "股票名称": name,
        "市场": "沪市" if str(code).startswith("6") else "深市",
        "是否ST": "是" if "ST" in str(name).upper() else "否",
        "是否N/C新股": "是" if str(name).startswith(("N", "C")) else "否",

        "缓存是否存在": cache_exists,
        "缓存是否最新": freshness,
        "缓存更新状态": update_status,
        "本次使用数据源": fetch_source,

        "K线起始日期": "",
        "K线最新日期": "",
        "距离今天自然日": "",
        "K线总根数": 0,
        "最大自然日断档": "",

        "是否已确认全历史": "否",
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

    full_enough, history_reason = cache_history_is_full_enough(code, df, meta)

    item["K线起始日期"] = first_date.strftime("%Y-%m-%d")
    item["K线最新日期"] = last_date.strftime("%Y-%m-%d")
    item["距离今天自然日"] = latest_gap_days(df)
    item["K线总根数"] = n
    item["最大自然日断档"] = max_calendar_gap_days(df)
    item["是否已确认全历史"] = "是" if full_enough else "否"

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
        warnings.append("N/C新股，仅缓存，模型层限制使用")
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
        item["机器备注"] = f"数据层可用；{history_reason}"

    return item


def save_outputs(rows, failed_rows, elapsed, success, failed, progress_rows):
    today = today_str()

    out_path = os.path.join(OUT_DIR, f"data_cache_health_full_{today}.csv")
    failed_path = os.path.join(OUT_DIR, f"data_cache_failed_{today}.csv")
    progress_path = os.path.join(OUT_DIR, f"full_history_progress_{today}.csv")

    df = pd.DataFrame(rows)

    cols = [
        "股票代码",
        "股票名称",
        "市场",
        "是否ST",
        "是否N/C新股",

        "缓存是否存在",
        "缓存是否最新",
        "缓存更新状态",
        "本次使用数据源",

        "K线起始日期",
        "K线最新日期",
        "距离今天自然日",
        "K线总根数",
        "最大自然日断档",
        "是否已确认全历史",

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

    pd.DataFrame(progress_rows).to_csv(progress_path, index=False, encoding="utf-8-sig")

    log(f"[CSV] {out_path}")
    log(f"[FAILED CSV] {failed_path}")
    log(f"[PROGRESS CSV] {progress_path}")


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


def make_progress_row(total, elapsed):
    ready = RUN_STATE["cache_full_ready"] + RUN_STATE["cache_full_but_stale"] + RUN_STATE["backfill_success"]
    pending_est = max(total - ready, 0)

    avg_backfill_seconds = ""
    estimated_remaining_hours = ""
    if RUN_STATE["backfill_success"] > 0:
        avg_backfill_seconds = elapsed / max(RUN_STATE["backfill_success"], 1)
        estimated_remaining_hours = round((pending_est * float(avg_backfill_seconds)) / 3600, 2)

    estimated_remaining_runs = ""
    if MAX_FULL_BACKFILL_PER_RUN > 0:
        estimated_remaining_runs = (pending_est + MAX_FULL_BACKFILL_PER_RUN - 1) // MAX_FULL_BACKFILL_PER_RUN

    return {
        "日期": today_str(),
        "全市场股票数": total,
        "已处理数量": RUN_STATE["processed"],
        "成功体检数量": RUN_STATE["success"],
        "失败数量": RUN_STATE["failed"],
        "缓存文件数量": count_cache_files(),

        "已全历史且最新数量": RUN_STATE["cache_full_ready"],
        "已全历史但需增量数量": RUN_STATE["cache_full_but_stale"],
        "历史不足数量": RUN_STATE["cache_partial"],
        "无缓存数量": RUN_STATE["no_cache"],

        "本轮增量尝试": RUN_STATE["incremental_attempted"],
        "本轮增量成功": RUN_STATE["incremental_success"],
        "本轮增量失败": RUN_STATE["incremental_failed"],

        "本轮全历史回补尝试": RUN_STATE["backfill_attempted"],
        "本轮全历史回补成功": RUN_STATE["backfill_success"],
        "本轮全历史回补失败": RUN_STATE["backfill_failed"],
        "回补因额度跳过": RUN_STATE["backfill_skipped_limit"],
        "回补因时间跳过": RUN_STATE["backfill_skipped_time"],
        "回补因FULL_REBUILD关闭跳过": RUN_STATE["backfill_skipped_full_rebuild_off"],

        "预计仍待全历史处理数量": pending_est,
        "预计剩余轮数": estimated_remaining_runs,
        "预计剩余BaoStock小时": estimated_remaining_hours,

        "是否安全收尾": RUN_STATE["safe_stop"],
        "安全收尾原因": RUN_STATE["safe_stop_reason"],
        "已运行时间": fmt_seconds(elapsed),
        "SOURCE_STATE": str(SOURCE_STATE),
        "RUN_STATE": str(RUN_STATE),
    }


def main():
    start_ts = time.time()

    rows = []
    progress_rows = []
    success = 0
    failed = 0

    meta = load_meta()

    try:
        existing_cache_count = count_cache_files()

        log(f"[CONFIG] FULL_REBUILD={FULL_REBUILD}")
        log(f"[CONFIG] MIN_CACHE_FILES_REQUIRED={MIN_CACHE_FILES_REQUIRED}")
        log(f"[CONFIG] MAX_FULL_BACKFILL_PER_RUN={MAX_FULL_BACKFILL_PER_RUN}")
        log(f"[CONFIG] MAX_RUNTIME_MINUTES={MAX_RUNTIME_MINUTES}")
        log(f"[CONFIG] SAFE_STOP_MINUTES={SAFE_STOP_MINUTES}")
        log(f"[CONFIG] BAOSTOCK_SLEEP_SECONDS={BAOSTOCK_SLEEP_SECONDS}")
        log(f"[CONFIG] FRESH_MAX_NATURAL_DAYS={FRESH_MAX_NATURAL_DAYS}")
        log(f"[CONFIG] FULL_MIN_ROWS_STRONG={FULL_MIN_ROWS_STRONG}")
        log(f"[CONFIG] FULL_MIN_ROWS_MEDIUM={FULL_MIN_ROWS_MEDIUM}")
        log(f"[CONFIG] FULL_EARLY_DATE={FULL_EARLY_DATE}")
        log(f"[CACHE FILES] dir={KLINE_CACHE_DIR} existing_csv={existing_cache_count}")
        log(f"[META] loaded rows={len(meta)} path={META_PATH}")

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

        log(f"[START] full-history cache build/health stocks={total}")
        log(
            f"[NOTE] run policy: single-thread, max backfill={MAX_FULL_BACKFILL_PER_RUN}, "
            f"safe stop before {SAFE_STOP_MINUTES}m, target runtime={MAX_RUNTIME_MINUTES}m"
        )

        for idx, row in stocks.iterrows():
            done = idx + 1
            code = str(row["code"]).zfill(6)
            name = str(row["name"])

            RUN_STATE["processed"] = done

            elapsed = time.time() - start_ts
            avg = elapsed / max(done, 1)
            remain = avg * (total - done)

            should_print_detail = done <= DETAIL_FIRST_N or done % DETAIL_EVERY == 0 or done == total

            if should_print_detail:
                log(
                    f"[CACHE CHECK START] {done}/{total} "
                    f"code={code} name={name} "
                    f"elapsed={fmt_seconds(elapsed)} eta_scan={fmt_seconds(remain)} "
                    f"backfill={RUN_STATE['backfill_success']}/{MAX_FULL_BACKFILL_PER_RUN}"
                )

            try:
                t0 = time.time()
                df, cache_exists, freshness, update_status, fetch_source = get_daily_kline(code, name, meta, start_ts)
                item = inspect_kline(df, code, name, cache_exists, freshness, update_status, fetch_source, meta)
                rows.append(item)
                success += 1
                RUN_STATE["success"] = success

                if should_print_detail:
                    row_count = 0 if df is None else len(df)
                    log(
                        f"[CACHE CHECK OK] {done}/{total} "
                        f"code={code} name={name} "
                        f"source={fetch_source} cache={cache_exists} "
                        f"freshness={freshness} status={update_status} "
                        f"rows={row_count} cost={fmt_seconds(time.time() - t0)} "
                        f"backfill_success={RUN_STATE['backfill_success']}"
                    )

            except Exception as e:
                failed += 1
                RUN_STATE["failed"] = failed
                FAILED_ROWS.append({
                    "股票代码": stock_code(code),
                    "股票名称": name,
                    "失败原因": repr(e),
                })
                log(f"[ERROR] {done}/{total} code={code} name={name}: {repr(e)}")
                traceback.print_exc(limit=1)

            if done % PROGRESS_EVERY == 0 or done == total:
                elapsed = time.time() - start_ts
                progress = make_progress_row(total, elapsed)
                progress_rows.append(progress)

                pct = done / total * 100
                log(
                    f"[PROGRESS] {done}/{total} ({pct:.2f}%) | "
                    f"success={success} failed={failed} | "
                    f"full_ready={RUN_STATE['cache_full_ready']} partial={RUN_STATE['cache_partial']} "
                    f"no_cache={RUN_STATE['no_cache']} | "
                    f"inc_ok={RUN_STATE['incremental_success']} "
                    f"backfill_ok={RUN_STATE['backfill_success']}/{MAX_FULL_BACKFILL_PER_RUN} | "
                    f"elapsed={fmt_seconds(elapsed)} cache_files={count_cache_files()} | "
                    f"safe_stop={RUN_STATE['safe_stop']}"
                )

            time.sleep(0.003)

        elapsed = time.time() - start_ts
        progress_rows.append(make_progress_row(total, elapsed))

    finally:
        elapsed = time.time() - start_ts
        save_meta(meta)
        save_outputs(rows, FAILED_ROWS, elapsed, success, failed, progress_rows)
        baostock_logout_once()

        log(f"[SOURCE STATE] {SOURCE_STATE}")
        log(f"[RUN STATE] {RUN_STATE}")
        log(
            f"[DONE] success={success}, failed={failed}, "
            f"elapsed={fmt_seconds(elapsed)}, cache_files={count_cache_files()}, meta_rows={len(meta)}"
        )


if __name__ == "__main__":
    main()
