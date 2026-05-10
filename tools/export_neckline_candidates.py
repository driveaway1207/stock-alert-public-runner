# -*- coding: utf-8 -*-
"""
A股全历史K线缓存自动补齐 + 可续跑 + 小白进度条版

核心目标：
1. 把所有A股K线缓存尽量整理成“全历史缓存”。
2. 已经全历史的股票自动跳过。
3. 只有2023年以来/明显短缓存的股票，自动拉全历史回补。
4. 没有缓存的股票，自动建立全历史缓存。
5. 每轮只跑4-5小时，快到时间自动收尾，避免 GitHub Actions 强杀。
6. 下一轮自动跳过已完成股票，继续补剩下的。
7. 全历史回补阶段优先 BaoStock，避免 EastMoney/AkShare 连续失败浪费时间。
8. 如果 BaoStock / EastMoney / AkShare 三个数据源全部熔断，立刻安全收尾，等待下一轮再继续。
"""

import os
import json
import time
import signal
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta

import requests
import pandas as pd
import baostock as bs
import akshare as ak


# =========================
# 基础配置
# =========================

OUT_DIR = "outputs"
KLINE_CACHE_DIR = "kline_cache"
STATUS_META_PATH = os.path.join(KLINE_CACHE_DIR, "_full_history_status.csv")

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(KLINE_CACHE_DIR, exist_ok=True)

FULL_HISTORY_START = "1990-01-01"
FULL_HISTORY_START_EM = "19900101"
INCREMENTAL_DAYS = 90

FULL_REBUILD = os.getenv("FULL_REBUILD", "0").strip() in ("1", "true", "True", "yes", "YES")
MIN_CACHE_FILES_REQUIRED = int(os.getenv("MIN_CACHE_FILES_REQUIRED", "3000"))

MAX_RUNTIME_MINUTES = int(os.getenv("MAX_RUNTIME_MINUTES", "270"))
SOFT_STOP_BUFFER_MINUTES = int(os.getenv("SOFT_STOP_BUFFER_MINUTES", "20"))

EASTMONEY_TIMEOUT = int(os.getenv("EASTMONEY_TIMEOUT", "20"))
AKSHARE_TIMEOUT = int(os.getenv("AKSHARE_TIMEOUT", "35"))
BAOSTOCK_TIMEOUT = int(os.getenv("BAOSTOCK_TIMEOUT", "45"))

SOURCE_FAIL_THRESHOLD = int(os.getenv("SOURCE_FAIL_THRESHOLD", "5"))
SOURCE_COOLDOWN_SECONDS = int(os.getenv("SOURCE_COOLDOWN_SECONDS", "900"))

DETAIL_FIRST_N = int(os.getenv("DETAIL_FIRST_N", "20"))
DETAIL_EVERY = int(os.getenv("DETAIL_EVERY", "20"))
PROGRESS_EVERY = int(os.getenv("PROGRESS_EVERY", "50"))

RUN_ROUND = int(os.getenv("RUN_ROUND", "1"))
MAX_AUTO_ROUNDS = int(os.getenv("MAX_AUTO_ROUNDS", "6"))

BAOSTOCK_READY = False

SOURCE_STATE = {
    "BaoStock": {"ok": 0, "fail": 0, "consecutive_fail": 0, "open_until": 0.0, "skip": 0},
    "EastMoney": {"ok": 0, "fail": 0, "consecutive_fail": 0, "open_until": 0.0, "skip": 0},
    "AkShare": {"ok": 0, "fail": 0, "consecutive_fail": 0, "open_until": 0.0, "skip": 0},
}


# =========================
# 通用工具
# =========================

class FetchTimeoutError(Exception):
    pass


@contextmanager
def time_limit(seconds, label="operation"):
    if seconds <= 0:
        yield
        return

    def signal_handler(signum, frame):
        raise FetchTimeoutError(f"{label} timeout after {seconds}s")

    old_handler = signal.signal(signal.SIGALRM, signal_handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


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


def progress_bar(done, total, width=24):
    if total <= 0:
        ratio = 0
    else:
        ratio = min(max(done / total, 0), 1)
    filled = int(round(ratio * width))
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar} {ratio * 100:6.2f}%"


def elapsed_seconds(start_ts):
    return time.time() - start_ts


def should_soft_stop(start_ts):
    budget = MAX_RUNTIME_MINUTES * 60
    buffer_sec = SOFT_STOP_BUFFER_MINUTES * 60
    return elapsed_seconds(start_ts) >= max(60, budget - buffer_sec)


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
            if f.lower().endswith(".csv") and not f.startswith("_")
        ])
    except Exception:
        return 0


def source_available(source):
    state = SOURCE_STATE[source]
    now = time.time()
    if now < state["open_until"]:
        state["skip"] += 1
        return False
    return True


def all_sources_in_cooldown():
    """
    三个数据源全部进入熔断冷却时，本轮继续扫股票没有意义。
    直接安全收尾，让下一轮再继续。
    """
    now = time.time()
    return all(now < SOURCE_STATE[s]["open_until"] for s in SOURCE_STATE)


def source_cooldown_text():
    now = time.time()
    parts = []
    for source, state in SOURCE_STATE.items():
        remain = int(max(0, state["open_until"] - now))
        parts.append(
            f"{source}: ok={state['ok']} fail={state['fail']} "
            f"consecutive_fail={state['consecutive_fail']} "
            f"cooldown_left={fmt_seconds(remain)}"
        )
    return " | ".join(parts)


def record_source_success(source):
    state = SOURCE_STATE[source]
    state["ok"] += 1
    state["consecutive_fail"] = 0
    state["open_until"] = 0.0


def record_source_failure(source, err):
    state = SOURCE_STATE[source]
    state["fail"] += 1
    state["consecutive_fail"] += 1

    if state["consecutive_fail"] >= SOURCE_FAIL_THRESHOLD:
        state["open_until"] = time.time() + SOURCE_COOLDOWN_SECONDS
        log(
            f"[数据源熔断] source={source} 连续失败={state['consecutive_fail']} "
            f"暂停={fmt_seconds(SOURCE_COOLDOWN_SECONDS)} err={repr(err)}"
        )


# =========================
# 状态文件
# =========================

def load_status_meta():
    if not os.path.exists(STATUS_META_PATH):
        return {}

    try:
        df = pd.read_csv(STATUS_META_PATH, dtype={"code": str})
        if df.empty:
            return {}
        df["code"] = df["code"].astype(str).str.zfill(6)
        return {str(r["code"]).zfill(6): dict(r) for _, r in df.iterrows()}
    except Exception as e:
        log(f"[WARN] read status meta failed: {repr(e)}")
        return {}


def save_status_meta(meta):
    try:
        rows = list(meta.values())
        if not rows:
            return

        df = pd.DataFrame(rows)
        if "code" in df.columns:
            df["code"] = df["code"].astype(str).str.zfill(6)
            df = df.sort_values("code")

        df.to_csv(STATUS_META_PATH, index=False, encoding="utf-8-sig")
        log(f"[META] saved {STATUS_META_PATH}, rows={len(df)}")
    except Exception as e:
        log(f"[WARN] save status meta failed: {repr(e)}")


def update_meta(meta, code, name, status, reason, rows=0, first_date="", last_date="", source="", note=""):
    code = str(code).zfill(6)
    meta[code] = {
        "code": code,
        "name": name,
        "status": status,
        "reason": reason,
        "rows": int(rows or 0),
        "first_date": first_date,
        "last_date": last_date,
        "source": source,
        "note": note,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# =========================
# K线标准化、读写
# =========================

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

    return normalize_daily_columns(pd.concat([old_df, new_df], ignore_index=True))


def df_first_date(df):
    if df is None or df.empty:
        return ""
    return pd.to_datetime(df["date"].min()).strftime("%Y-%m-%d")


def df_last_date(df):
    if df is None or df.empty:
        return ""
    return pd.to_datetime(df["date"].max()).strftime("%Y-%m-%d")


def cache_is_fresh(df):
    if df is None or df.empty:
        return False

    last_date = pd.to_datetime(df["date"].max()).date()
    now_date = datetime.now().date()
    return (now_date - last_date).days <= 5


def likely_needs_backfill(df, meta_row=None):
    """
    判断缓存是否需要全历史回补。
    重点识别：2023开始、800根左右的短缓存。
    """
    if df is None or df.empty:
        return True, "无缓存或缓存不可读"

    if meta_row and str(meta_row.get("status", "")) == "full_history_confirmed":
        return False, "meta已确认全历史"

    rows = len(df)
    first_str = df_first_date(df)

    if first_str >= "2020-01-01" and rows < 1600:
        return True, f"明显短缓存 first={first_str} rows={rows}"

    if first_str >= "2015-01-01" and rows < 2500:
        return True, f"疑似非全量 first={first_str} rows={rows}"

    if rows < 500:
        return True, f"K线根数过少 rows={rows}"

    return False, f"缓存长度看起来足够 first={first_str} rows={rows}"


# =========================
# 股票列表
# =========================

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


# =========================
# 数据源
# =========================

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


def fetch_from_source(code, source, mode, start_str, em_start):
    if not source_available(source):
        log(f"[数据源跳过] code={code} source={source} reason=熔断冷却中")
        return None

    try:
        t0 = time.time()

        if source == "BaoStock":
            log(f"[拉取开始] code={code} source=BaoStock mode={mode} start={start_str}")
            df = fetch_daily_from_baostock(code, start_str)

        elif source == "EastMoney":
            log(f"[拉取开始] code={code} source=EastMoney mode={mode} start={em_start}")
            df = fetch_daily_from_eastmoney(code, em_start)

        elif source == "AkShare":
            log(f"[拉取开始] code={code} source=AkShare mode={mode}")
            df = fetch_daily_from_akshare(code)

        else:
            raise ValueError(f"unknown source: {source}")

        cost = fmt_seconds(time.time() - t0)

        if df is not None and not df.empty:
            record_source_success(source)
            log(f"[拉取成功] code={code} source={source} rows={len(df)} cost={cost}")
            return df

        record_source_failure(source, "empty dataframe")
        log(f"[拉取为空] code={code} source={source} cost={cost}")
        return None

    except Exception as e:
        record_source_failure(source, e)
        log(f"[拉取失败] code={code} source={source} err={repr(e)}")
        return None


def fetch_full_history(code, old_df=None):
    """
    全历史回补阶段：
    优先 BaoStock，因为当前 GitHub 环境下 BaoStock 成功率最高；
    BaoStock 失败后再尝试 EastMoney / AkShare。
    """
    old_first = df_first_date(old_df)
    old_rows = 0 if old_df is None else len(old_df)

    start_str = FULL_HISTORY_START
    em_start = FULL_HISTORY_START_EM
    mode = "full_backfill"

    best_df = None
    best_source = "全部失败"

    for source in ["BaoStock", "EastMoney", "AkShare"]:
        df = fetch_from_source(code, source, mode, start_str, em_start)
        if df is None or df.empty:
            continue

        new_first = df_first_date(df)
        new_rows = len(df)

        improved = False

        if old_df is None or old_df.empty:
            improved = True
        else:
            if new_first and old_first and new_first < old_first:
                improved = True
            if new_rows > old_rows + 100:
                improved = True

        if improved:
            return df, source, True

        if best_df is None or len(df) > len(best_df):
            best_df = df
            best_source = source

    if best_df is not None:
        return best_df, best_source, False

    return None, "全部失败", False


def fetch_incremental(code, cache_df):
    """
    增量更新阶段：
    先试 EastMoney / AkShare，失败再 BaoStock。
    """
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


# =========================
# 体检
# =========================

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


def inspect_kline(df, code, name, cache_exists, completeness, update_status, fetch_source, action):
    item = {
        "股票代码": stock_code(code),
        "股票名称": name,
        "市场": "沪市" if str(code).startswith("6") else "深市",
        "是否ST": "是" if "ST" in str(name).upper() else "否",
        "是否N/C新股": "是" if str(name).startswith(("N", "C")) else "否",

        "缓存是否存在": cache_exists,
        "缓存完整性": completeness,
        "本轮动作": action,
        "缓存更新状态": update_status,
        "本次使用数据源": fetch_source,

        "K线起始日期": "",
        "K线最新日期": "",
        "距离今天自然日": "",
        "K线总根数": 0,
        "最大自然日断档": "",

        "开高低收缺失数": 0,
        "价格为0数量": 0,
        "high_low错误数": 0,
        "high小于开收数量": 0,
        "low大于开收数量": 0,
        "价格异常日期示例": "",

        "成交量缺失数": 0,
        "成交量为0数量": 0,
        "成交量缺失日期示例": "",
        "零成交/疑似停牌日期示例": "",
        "成交额缺失数": 0,
        "成交额缺失日期示例": "",

        "数据体检结论": "不通过",
        "机器备注": "K线数据不可用",
    }

    df = normalize_daily_columns(df)

    if df is None or df.empty:
        return item

    n = len(df)
    first_date = pd.to_datetime(df["date"].min())
    last_date = pd.to_datetime(df["date"].max())

    item["K线起始日期"] = first_date.strftime("%Y-%m-%d")
    item["K线最新日期"] = last_date.strftime("%Y-%m-%d")
    item["距离今天自然日"] = latest_gap_days(df)
    item["K线总根数"] = n
    item["最大自然日断档"] = max_calendar_gap_days(df)

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

    item["成交量缺失数"] = int(volume_missing.sum())
    item["成交量为0数量"] = int(volume_zero.sum())
    item["成交量缺失日期示例"] = first_bad_date(df, volume_missing)
    item["零成交/疑似停牌日期示例"] = first_bad_date(df, volume_zero)
    item["成交额缺失数"] = int(amount_missing.sum())
    item["成交额缺失日期示例"] = first_bad_date(df, amount_missing)

    hard_errors = []
    warnings = []

    if price_error.any():
        hard_errors.append("存在价格硬错误")
    if volume_missing.any() or amount_missing.any():
        warnings.append("存在成交量/成交额缺失记录")
    if volume_zero.any():
        warnings.append("存在零成交/疑似停牌记录")
    if item["距离今天自然日"] != "" and item["距离今天自然日"] > 10:
        warnings.append("最新K距离今天超过10个自然日，可能停牌或数据未更新")
    if completeness != "全历史":
        warnings.append("缓存尚未确认全历史")

    if hard_errors:
        item["数据体检结论"] = "不通过"
        item["机器备注"] = "；".join(hard_errors + warnings)
    elif warnings:
        item["数据体检结论"] = "通过但限制使用"
        item["机器备注"] = "；".join(warnings)
    else:
        item["数据体检结论"] = "通过"
        item["机器备注"] = "数据层可用"

    return item


# =========================
# 单票处理
# =========================

def process_one_stock(code, name, meta):
    code = str(code).zfill(6)

    old_df = read_cache(code)
    cache_exists = old_df is not None and not old_df.empty
    meta_row = meta.get(code)

    needs_backfill, reason = likely_needs_backfill(old_df, meta_row)

    # 已确认全历史 + 新鲜：直接跳过
    if cache_exists and not needs_backfill and cache_is_fresh(old_df):
        update_meta(
            meta,
            code,
            name,
            "full_history_confirmed",
            "缓存已确认全历史且新鲜",
            len(old_df),
            df_first_date(old_df),
            df_last_date(old_df),
            "缓存",
            "无需联网",
        )
        return old_df, "有", "全历史", "无需更新", "缓存", "跳过-已全量"

    # 已确认全历史但偏旧：增量更新
    if cache_exists and not needs_backfill and not cache_is_fresh(old_df):
        new_df, source = fetch_incremental(code, old_df)
        merged = merge_kline(old_df, new_df)

        if merged is not None and not merged.empty:
            save_cache(code, merged)
            update_meta(
                meta,
                code,
                name,
                "full_history_confirmed",
                "全历史缓存增量更新",
                len(merged),
                df_first_date(merged),
                df_last_date(merged),
                source,
                "",
            )
            return merged, "有", "全历史", f"已增量更新:{source}", source, "增量更新"

        update_meta(
            meta,
            code,
            name,
            "full_history_confirmed",
            "增量更新失败但保留旧缓存",
            len(old_df),
            df_first_date(old_df),
            df_last_date(old_df),
            "缓存",
            "",
        )
        return old_df, "有", "全历史", "增量失败保留旧缓存", "缓存", "增量失败"

    # 无缓存时，如果缓存恢复数量太少且不允许从0重建，保护跳过该票
    if not cache_exists and not FULL_REBUILD and count_cache_files() < MIN_CACHE_FILES_REQUIRED:
        return None, "无", "待建立", "保护跳过:缓存恢复过少且FULL_REBUILD=0", "未联网", "跳过-保护"

    # 无缓存或明显非全量：全历史回补/建立
    full_df, source, improved = fetch_full_history(code, old_df)

    if full_df is not None and not full_df.empty:
        merged = merge_kline(old_df, full_df)

        if merged is not None and not merged.empty:
            save_cache(code, merged)

            if improved:
                note = "全历史回补有改善"
            else:
                note = "数据源未提供更早历史，按当前可得数据确认"

            update_meta(
                meta,
                code,
                name,
                "full_history_confirmed",
                note,
                len(merged),
                df_first_date(merged),
                df_last_date(merged),
                source,
                reason,
            )

            action = "回补成功" if cache_exists else "首次建立成功"
            status = f"已全历史回补:{source}" if cache_exists else f"首次建立全历史:{source}"
            return merged, "有" if cache_exists else "无", "全历史", status, source, action

    # 回补失败：有旧缓存就保留旧缓存
    if cache_exists:
        update_meta(
            meta,
            code,
            name,
            "backfill_failed",
            "全历史回补失败但保留旧缓存",
            len(old_df),
            df_first_date(old_df),
            df_last_date(old_df),
            "缓存",
            reason,
        )
        return old_df, "有", "非全量", "回补失败保留旧缓存", "缓存", "回补失败"

    update_meta(
        meta,
        code,
        name,
        "backfill_failed",
        "无缓存且建立失败",
        0,
        "",
        "",
        "全部失败",
        reason,
    )
    return None, "无", "不可用", "建立失败", "全部失败", "建立失败"


# =========================
# 输出
# =========================

def save_csv(path, rows):
    if rows:
        pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame([{"诊断": "无数据"}]).to_csv(path, index=False, encoding="utf-8-sig")


def build_need_rows(stocks, meta):
    rows = []
    for _, r in stocks.iterrows():
        code = str(r["code"]).zfill(6)
        name = str(r["name"])
        m = meta.get(code, {})
        if str(m.get("status", "")) != "full_history_confirmed":
            rows.append({
                "股票代码": stock_code(code),
                "股票名称": name,
                "当前状态": m.get("status", "未处理"),
                "原因": m.get("reason", ""),
                "K线起始日期": m.get("first_date", ""),
                "K线最新日期": m.get("last_date", ""),
                "K线总根数": m.get("rows", ""),
                "数据源": m.get("source", ""),
                "备注": m.get("note", ""),
            })
    return rows


def save_outputs(rows, done_rows, need_rows, failed_rows, summary):
    today = today_str()

    health_path = os.path.join(OUT_DIR, f"data_cache_health_full_{today}.csv")
    done_path = os.path.join(OUT_DIR, f"full_history_backfill_done_{today}.csv")
    need_path = os.path.join(OUT_DIR, f"need_full_history_backfill_{today}.csv")
    failed_path = os.path.join(OUT_DIR, f"data_cache_failed_{today}.csv")
    summary_path = os.path.join(OUT_DIR, f"backfill_summary_{today}.csv")
    state_path = os.path.join(OUT_DIR, "backfill_state.json")

    save_csv(health_path, rows)
    save_csv(done_path, done_rows)
    save_csv(need_path, need_rows)
    save_csv(failed_path, failed_rows)
    save_csv(summary_path, [summary])

    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    log(f"[输出] 体检表: {health_path}")
    log(f"[输出] 本轮回补成功: {done_path}")
    log(f"[输出] 待回补清单: {need_path}")
    log(f"[输出] 失败清单: {failed_path}")
    log(f"[输出] 总结: {summary_path}")
    log(f"[输出] 自动续跑状态: {state_path}")


# =========================
# 主流程
# =========================

def main():
    start_ts = time.time()
    meta = load_status_meta()

    rows = []
    done_rows = []
    failed_rows = []

    processed = 0
    skipped_full = 0
    backfill_success = 0
    built_success = 0
    backfill_failed = 0
    stopped_by_time = False
    stopped_by_circuit = False

    try:
        existing_cache_count = count_cache_files()

        log("")
        log("========== A股全历史缓存自动补齐任务 ==========")
        log(f"[轮次] 当前第 {RUN_ROUND} 轮 / 最多自动 {MAX_AUTO_ROUNDS} 轮")
        log(f"[配置] 每轮最多运行 {MAX_RUNTIME_MINUTES} 分钟，提前 {SOFT_STOP_BUFFER_MINUTES} 分钟收尾")
        log(f"[配置] FULL_REBUILD={FULL_REBUILD}, MIN_CACHE_FILES_REQUIRED={MIN_CACHE_FILES_REQUIRED}")
        log(f"[缓存] 当前缓存CSV数量={existing_cache_count}")
        log("[策略] 全历史回补优先 BaoStock；增量更新优先 EastMoney/AkShare")
        log("[保护] 三个数据源全部熔断时，本轮立刻安全收尾")
        log("==============================================")
        log("")

        if existing_cache_count < MIN_CACHE_FILES_REQUIRED and not FULL_REBUILD:
            log(
                f"[保护停止] 恢复出的缓存数量过少 existing={existing_cache_count}, "
                f"required={MIN_CACHE_FILES_REQUIRED}。为避免从0误重建，本轮停止。"
            )

            summary = {
                "run_round": RUN_ROUND,
                "max_auto_rounds": MAX_AUTO_ROUNDS,
                "cache_files": existing_cache_count,
                "stock_total": 0,
                "processed_this_run": 0,
                "skipped_full_this_run": 0,
                "backfill_success_this_run": 0,
                "built_success_this_run": 0,
                "backfill_failed_this_run": 0,
                "full_history_count": 0,
                "need_backfill_count": 0,
                "stopped_by_time": False,
                "stopped_by_circuit": False,
                "should_continue": False,
                "continue_reason": "cache_restore_too_small",
                "elapsed": fmt_seconds(elapsed_seconds(start_ts)),
            }

            save_outputs(rows, done_rows, [], failed_rows, summary)
            return

        stocks = fetch_stock_list()
        total = len(stocks)

        log(f"[股票池] total={total}")
        log(f"[整体进度] {progress_bar(0, total)} 0/{total}")
        log("")

        for idx, row in stocks.iterrows():
            if should_soft_stop(start_ts):
                stopped_by_time = True
                log("")
                log("[时间保护] 快到本轮时间上限，开始安全收尾。已补好的缓存会保存，下轮自动继续。")
                log("")
                break

            if all_sources_in_cooldown():
                stopped_by_circuit = True
                log("")
                log("[熔断保护] BaoStock / EastMoney / AkShare 三个数据源全部进入冷却。")
                log("[熔断保护] 本轮继续扫描已经没有意义，开始安全收尾，等待下一轮再继续。")
                log(f"[熔断状态] {source_cooldown_text()}")
                log("")
                break

            processed += 1
            done_index = idx + 1
            code = str(row["code"]).zfill(6)
            name = str(row["name"])

            should_detail = (
                processed <= DETAIL_FIRST_N
                or processed % DETAIL_EVERY == 0
                or done_index == total
            )

            if should_detail:
                log(
                    f"[开始] {done_index}/{total} {code} {name} | "
                    f"整体 {progress_bar(done_index - 1, total)} | "
                    f"耗时={fmt_seconds(elapsed_seconds(start_ts))}"
                )

            try:
                t0 = time.time()
                df, cache_exists, completeness, status, source, action = process_one_stock(code, name, meta)
                item = inspect_kline(df, code, name, cache_exists, completeness, status, source, action)
                rows.append(item)

                row_count = 0 if df is None else len(df)
                first = "" if df is None or df.empty else df_first_date(df)
                last = "" if df is None or df.empty else df_last_date(df)

                if completeness == "全历史" and action.startswith("跳过"):
                    skipped_full += 1
                elif action == "回补成功":
                    backfill_success += 1
                    done_rows.append({
                        "股票代码": stock_code(code),
                        "股票名称": name,
                        "动作": action,
                        "数据源": source,
                        "K线起始日期": first,
                        "K线最新日期": last,
                        "K线总根数": row_count,
                    })
                elif action == "首次建立成功":
                    built_success += 1
                    done_rows.append({
                        "股票代码": stock_code(code),
                        "股票名称": name,
                        "动作": action,
                        "数据源": source,
                        "K线起始日期": first,
                        "K线最新日期": last,
                        "K线总根数": row_count,
                    })
                elif "失败" in action:
                    backfill_failed += 1
                    failed_rows.append({
                        "股票代码": stock_code(code),
                        "股票名称": name,
                        "动作": action,
                        "状态": status,
                        "数据源": source,
                    })

                if should_detail:
                    log(
                        f"[完成] {done_index}/{total} {code} {name} | "
                        f"动作={action} 完整性={completeness} source={source} "
                        f"rows={row_count} first={first} last={last} "
                        f"cost={fmt_seconds(time.time() - t0)}"
                    )

            except Exception as e:
                backfill_failed += 1
                failed_rows.append({
                    "股票代码": stock_code(code),
                    "股票名称": name,
                    "动作": "异常失败",
                    "状态": repr(e),
                    "数据源": "未知",
                })
                log(f"[异常] {done_index}/{total} {code} {name}: {repr(e)}")
                traceback.print_exc(limit=1)

            if processed % PROGRESS_EVERY == 0 or done_index == total:
                elapsed = elapsed_seconds(start_ts)
                avg = elapsed / max(processed, 1)
                remain_scan = avg * (total - done_index)

                log("")
                log("---------- 本轮进度条 ----------")
                log(f"整体扫描: {progress_bar(done_index, total)} {done_index}/{total}")
                log(f"本轮时间: {progress_bar(elapsed, MAX_RUNTIME_MINUTES * 60)} {fmt_seconds(elapsed)} / {MAX_RUNTIME_MINUTES}m")
                log(f"已确认跳过全历史: {skipped_full}")
                log(f"本轮回补成功: {backfill_success}")
                log(f"本轮首次建立: {built_success}")
                log(f"本轮失败: {backfill_failed}")
                log(f"当前缓存文件数: {count_cache_files()}")
                log(f"按当前速度估算剩余扫描时间: {fmt_seconds(remain_scan)}")
                log("--------------------------------")
                log("")

            time.sleep(0.003)

        save_status_meta(meta)

        full_history_count = 0
        for m in meta.values():
            if str(m.get("status", "")) == "full_history_confirmed":
                full_history_count += 1

        need_rows = build_need_rows(stocks, meta)
        need_backfill_count = len(need_rows)

        should_continue = need_backfill_count > 0 and RUN_ROUND < MAX_AUTO_ROUNDS

        if need_backfill_count <= 0:
            continue_reason = "全部完成"
            should_continue = False
        elif RUN_ROUND >= MAX_AUTO_ROUNDS:
            continue_reason = "达到最大自动轮数"
            should_continue = False
        elif stopped_by_circuit and should_continue:
            continue_reason = "三个数据源全部熔断，本轮提前收尾，10分钟后自动下一轮"
        elif stopped_by_time and should_continue:
            continue_reason = "达到本轮时间保护，本轮提前收尾，10分钟后自动下一轮"
        elif should_continue:
            continue_reason = "仍有待回补，10分钟后自动下一轮"
        else:
            continue_reason = "保护停止"

        summary = {
            "run_round": RUN_ROUND,
            "max_auto_rounds": MAX_AUTO_ROUNDS,
            "cache_files": count_cache_files(),
            "stock_total": total,
            "processed_this_run": processed,
            "skipped_full_this_run": skipped_full,
            "backfill_success_this_run": backfill_success,
            "built_success_this_run": built_success,
            "backfill_failed_this_run": backfill_failed,
            "full_history_count": full_history_count,
            "need_backfill_count": need_backfill_count,
            "stopped_by_time": stopped_by_time,
            "stopped_by_circuit": stopped_by_circuit,
            "should_continue": should_continue,
            "continue_reason": continue_reason,
            "elapsed": fmt_seconds(elapsed_seconds(start_ts)),
            "source_BaoStock_ok": SOURCE_STATE["BaoStock"]["ok"],
            "source_BaoStock_fail": SOURCE_STATE["BaoStock"]["fail"],
            "source_EastMoney_ok": SOURCE_STATE["EastMoney"]["ok"],
            "source_EastMoney_fail": SOURCE_STATE["EastMoney"]["fail"],
            "source_AkShare_ok": SOURCE_STATE["AkShare"]["ok"],
            "source_AkShare_fail": SOURCE_STATE["AkShare"]["fail"],
        }

        log("")
        log("========== 本轮总结 ==========")
        log(f"缓存文件数: {summary['cache_files']}")
        log(f"全历史确认数: {summary['full_history_count']}")
        log(f"仍需回补数: {summary['need_backfill_count']}")
        log(f"本轮回补成功: {summary['backfill_success_this_run']}")
        log(f"本轮首次建立: {summary['built_success_this_run']}")
        log(f"本轮失败: {summary['backfill_failed_this_run']}")
        log(f"是否因数据源熔断提前收尾: {summary['stopped_by_circuit']}")
        log(f"是否需要自动下一轮: {summary['should_continue']}")
        log(f"原因: {summary['continue_reason']}")
        log("==============================")
        log("")

        save_outputs(rows, done_rows, need_rows, failed_rows, summary)

    finally:
        save_status_meta(meta)
        baostock_logout_once()
        log(f"[DONE] 本轮结束，总耗时={fmt_seconds(elapsed_seconds(start_ts))}, cache_files={count_cache_files()}")


if __name__ == "__main__":
    main()
