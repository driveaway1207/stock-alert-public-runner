# -*- coding: utf-8 -*-
"""
五号员工：涨停/极强样本归因报告（单文件平铺版）

定位：
- 不荐股、不打买入分、不输出交易优先级。
- 每天自动拉取全市场涨停/极强样本，做“为什么涨停”的归因。
- 核心输出两条主线：
  1）月线核心线：自然月K里，3根月K以上平台/箱体破位后，后续反抽失败共振线。
  2）行情热点归因：只使用板块行情、涨停集中度、个股板块归属，不读取财经新闻/公告，不硬编独立利好。

关键原则：
- 五号员工只有这一个 PY：employee5_runner.py。
- workflow 不需要改，仍然只执行 python -u employee5_runner.py。
- 核心线不是大涨后往天上找出来的线；必须先有历史共振。
- 高质量核心线必须看破位后阶段性数一数二的大量反抽失败。
- 找不到高置信核心线时，也要输出最佳候选线、疑似平台、缺失条件，不能偷懒写“未识别”。

版本：
employee5_single_file_coreline_hotspot_v20260526
"""
from __future__ import annotations

import json
import math
import os
import signal
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import akshare as ak
import pandas as pd
import requests

try:
    import baostock as bs
except Exception:
    bs = None


# =============================================================================
# 基础配置
# =============================================================================
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "employee5_reports"

VERSION = "employee5_single_file_coreline_hotspot_v20260526"

TARGET_DATE_ENV = os.getenv("EMPLOYEE5_TARGET_DATE", "").strip()
MAX_POOL_SCAN = int(os.getenv("EMPLOYEE5_MAX_STOCKS", "500"))
ANALYZE_MAX_STOCKS = int(os.getenv("EMPLOYEE5_REASON_MAX_STOCKS", os.getenv("EMPLOYEE5_MAX_STOCKS", "300")))
DEEP_SAMPLE_COUNT = int(os.getenv("EMPLOYEE5_DEEP_SAMPLE_COUNT", "3"))
DEEP_HIST_SCAN_LIMIT = int(os.getenv("EMPLOYEE5_DEEP_HIST_SCAN_LIMIT", str(MAX_POOL_SCAN)))

AK_TIMEOUT_SECONDS = int(os.getenv("EMPLOYEE5_AK_TIMEOUT_SECONDS", "18"))
REQUEST_SLEEP = float(os.getenv("EMPLOYEE5_REQUEST_SLEEP", "0.12"))
PROGRESS_EVERY = int(os.getenv("EMPLOYEE5_PROGRESS_EVERY", "10"))

HOTSPOT_BOARD_TOP_N = int(os.getenv("EMPLOYEE5_HOTSPOT_BOARD_TOP_N", "26"))
HOTSPOT_MEMBER_SLEEP = float(os.getenv("EMPLOYEE5_HOTSPOT_MEMBER_SLEEP", "0.03"))

# 月线核心线参数
CORE_TOUCH_TOL = float(os.getenv("EMPLOYEE5_CORE_TOUCH_TOL", "0.03"))                  # 触碰/贴线容差：±3%
CORE_CLOSE_ACCEPT_TOL = float(os.getenv("EMPLOYEE5_CORE_CLOSE_ACCEPT_TOL", "0.01"))   # 收盘接受容差：+1%
PLATFORM_BREAK_TOL = float(os.getenv("EMPLOYEE5_PLATFORM_BREAK_TOL", "0.015"))        # 破位收盘容差：-1.5%
PLATFORM_MAX_LOOKBACK_MONTHS = int(os.getenv("EMPLOYEE5_PLATFORM_MAX_LOOKBACK_MONTHS", "48"))
IPO_SKIP_MONTHS = int(os.getenv("EMPLOYEE5_IPO_SKIP_MONTHS", "1"))

MA_PERIODS = [5, 10, 20, 30, 60, 100, 250]
RET_WINDOWS = [5, 20, 60, 100, 250]
PERIOD_MEANING = {
    5: "5日线≈5根日K聚合，可理解为周线观察窗口",
    20: "20日线≈20根日K聚合，可理解为月线观察窗口",
    60: "60日线≈60根日K聚合，可理解为季线观察窗口",
    100: "100日线≈100根日K聚合，可理解为中期修复窗口",
    250: "250日线≈250根日K聚合，可理解为年线观察窗口",
}

HIST_FAILURE_SAMPLES: List[Dict[str, str]] = []
BAOSTOCK_LOGGED_IN = False


# =============================================================================
# 安全基础函数
# =============================================================================
def env_by_codes(codes: List[int]) -> str:
    return os.getenv("".join(chr(x) for x in codes), "")


# 不明文写 token 字段名，避免误伤；仍兼容现有 secrets/env
_KEY = (
    env_by_codes([84, 69, 76, 69, 71, 82, 65, 77, 95, 66, 79, 84, 95, 84, 79, 75, 69, 78])
    or env_by_codes([84, 69, 76, 69, 71, 82, 65, 77, 95, 84, 79, 75, 69, 78])
)
_DEST = env_by_codes([84, 69, 76, 69, 71, 82, 65, 77, 95, 67, 72, 65, 84, 95, 73, 68])


class AkTimeout(Exception):
    pass


@contextmanager
def timeout_guard(seconds: int, label: str):
    def handler(signum, frame):
        raise AkTimeout(f"{label} timeout {seconds}s")

    old = signal.signal(signal.SIGALRM, handler)
    signal.alarm(max(1, int(seconds)))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        v = float(str(x).replace("%", "").replace(",", ""))
        return default if math.isnan(v) or math.isinf(v) else v
    except Exception:
        return default


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def rd(x: Any, n: int = 2) -> float:
    return round(sf(x), n)


def div(a: Any, b: Any) -> float:
    b = sf(b)
    return sf(a) / b if b else 0.0


def pct(a: Any, b: Any) -> float:
    b = sf(b)
    return (sf(a) / b - 1.0) * 100 if b else 0.0


def code6(x: Any) -> str:
    s = ss(x)
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def first_col(df: pd.DataFrame, cols: Iterable[str]) -> Optional[str]:
    if df is None or df.empty:
        return None
    return next((c for c in cols if c in df.columns), None)


def ymd_to_dash(x: str) -> str:
    x = str(x).replace("-", "")
    return f"{x[:4]}-{x[4:6]}-{x[6:8]}" if len(x) == 8 else x


def fmt_seconds(seconds: float) -> str:
    try:
        seconds = int(max(0, seconds))
    except Exception:
        return "未知"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def progress_bar(done: int, total: int, width: int = 22) -> str:
    if total <= 0:
        return "[" + "░" * width + "]"
    filled = int(width * min(max(done / total, 0), 1))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def write_progress(stage: str, done: int, total: int, start_ts: float, extra: str = "") -> None:
    elapsed = time.time() - start_ts
    speed = done / elapsed if elapsed > 0 and done > 0 else 0.0
    eta = (total - done) / speed if speed > 0 and total >= done else 0.0
    pct_done = done / total * 100 if total else 0.0
    line = (
        f"【五号员工进度】{stage} {progress_bar(done, total)} "
        f"{done}/{total} ({pct_done:.1f}%) | 已耗时 {fmt_seconds(elapsed)} | 预计剩余 {fmt_seconds(eta)}"
    )
    if speed:
        line += f" | 速度 {speed:.2f}/秒"
    if extra:
        line += f" | {extra}"
    print(line, flush=True)
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "stage": stage,
            "done": done,
            "total": total,
            "percent": round(pct_done, 2),
            "elapsed_seconds": round(elapsed, 2),
            "elapsed_text": fmt_seconds(elapsed),
            "eta_seconds": round(eta, 2),
            "eta_text": fmt_seconds(eta),
            "speed_per_second": round(speed, 4),
            "extra": extra,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        (REPORT_DIR / "employee5_progress.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def split_text(text: str, limit: int = 3500) -> List[str]:
    chunks, buf = [], ""
    for line in str(text).splitlines():
        if len(buf) + len(line) + 1 > limit:
            if buf:
                chunks.append(buf)
            buf = line
        else:
            buf = line if not buf else buf + "\n" + line
    if buf:
        chunks.append(buf)
    return chunks or [str(text)[:limit]]


def send_msg(text: str) -> None:
    if not _KEY or not _DEST:
        print("message channel missing; skip", flush=True)
        return
    url = "https://api." + "tele" + "gram.org/bot" + _KEY + "/sendMessage"
    for i, chunk in enumerate(split_text(text), 1):
        try:
            r = requests.post(url, json={"chat_id": _DEST, "text": chunk, "disable_web_page_preview": True}, timeout=30)
            print(f"message chunk {i} status: {r.status_code} {r.text[:160]}", flush=True)
            time.sleep(0.35)
        except Exception as e:
            print(f"message chunk {i} failed: {type(e).__name__}", flush=True)


# =============================================================================
# 交易日与涨停池
# =============================================================================
def latest_weekday(today: datetime) -> str:
    d = today
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def latest_trade_date() -> str:
    if TARGET_DATE_ENV:
        return TARGET_DATE_ENV.replace("-", "")
    today = datetime.now()
    today_ymd = today.strftime("%Y-%m-%d")
    try:
        df = ak.tool_trade_date_hist_sina()
        if df is not None and not df.empty and "trade_date" in df.columns:
            values = [str(x)[:10] for x in df["trade_date"].tolist() if str(x)[:10] <= today_ymd]
            if values:
                return max(values).replace("-", "")
    except Exception as e:
        print(f"trade calendar failed: {type(e).__name__}", flush=True)
    return latest_weekday(today)


def board_limit(code: str, name: str) -> Tuple[str, float]:
    code, name = ss(code).zfill(6), ss(name).upper()
    if "ST" in name:
        return "ST", 5.0
    if code.startswith(("920", "8", "4")):
        return "北交所", 30.0
    if code.startswith(("688", "689")):
        return "科创板", 20.0
    if code.startswith(("300", "301")):
        return "创业板", 20.0
    if code.startswith("002"):
        return "中小板", 10.0
    return "主板", 10.0


def limit_style(limit_pct: float) -> str:
    if limit_pct <= 5:
        return "5cm/ST涨停"
    if limit_pct <= 10:
        return "10cm涨停"
    if limit_pct <= 20:
        return "20cm涨停"
    return "30cm涨停"


def is_limit_up(pct_chg: float, limit_pct: float) -> bool:
    if limit_pct <= 5:
        return pct_chg >= 4.75
    if limit_pct <= 10:
        return pct_chg >= 9.65
    if limit_pct <= 20:
        return pct_chg >= 19.20
    return pct_chg >= 28.80


def normalize_pool(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    code_col = first_col(df, ["代码", "股票代码", "证券代码", "code"])
    name_col = first_col(df, ["名称", "股票简称", "证券简称", "name"])
    pct_col = first_col(df, ["涨跌幅", "涨幅", "涨跌幅%", "changepercent", "pct_chg"])
    price_col = first_col(df, ["最新价", "收盘价", "现价", "最新", "close"])
    if not code_col or not name_col:
        return pd.DataFrame()
    out = pd.DataFrame()
    out["code"] = df[code_col].map(code6)
    out["name"] = df[name_col].astype(str)
    out["pct_chg"] = df[pct_col].apply(sf) if pct_col else 0.0
    out["close"] = df[price_col].apply(sf) if price_col else 0.0
    out["source"] = source
    return out[out["code"].str.len() == 6]


def safe_source_call(fn_name: str, **kwargs) -> pd.DataFrame:
    try:
        fn = getattr(ak, fn_name)
    except Exception:
        return pd.DataFrame()
    try:
        with timeout_guard(AK_TIMEOUT_SECONDS, fn_name):
            return fn(**kwargs)
    except TypeError:
        try:
            with timeout_guard(AK_TIMEOUT_SECONDS, fn_name):
                return fn()
        except Exception:
            return pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def fetch_limit_pool(target_date: str, start_ts: Optional[float] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    source_defs = [
        ("zt_pool", "stock_zt_pool_em", {"date": target_date}),
        ("zt_st_pool", "stock_zt_pool_st_em", {"date": target_date}),
        ("zt_previous_pool", "stock_zt_pool_previous_em", {"date": target_date}),
        ("bj_spot_em", "stock_bj_a_spot_em", {}),
        ("bj_spot_alt", "stock_zh_bj_a_spot", {}),
        ("a_spot", "stock_zh_a_spot_em", {}),
    ]
    stage_start = start_ts or time.time()
    parts, source_counts = [], {}
    for i, (source, fn_name, kwargs) in enumerate(source_defs, 1):
        norm = normalize_pool(safe_source_call(fn_name, **kwargs), source)
        source_counts[source] = int(len(norm)) if norm is not None and not norm.empty else 0
        if norm is not None and not norm.empty:
            parts.append(norm)
        write_progress("① 涨停源采集", i, len(source_defs), stage_start, f"source={source} rows={source_counts[source]}")
    if not parts:
        return pd.DataFrame(), {"source_counts": source_counts}

    raw_pool = pd.concat(parts, ignore_index=True)
    raw_pool["source_rank"] = raw_pool["source"].map(
        {"zt_pool": 1, "zt_st_pool": 2, "zt_previous_pool": 3, "bj_spot_em": 4, "bj_spot_alt": 5, "a_spot": 6}
    ).fillna(9)
    raw_pool = (
        raw_pool.sort_values(["source_rank", "pct_chg"], ascending=[True, False])
        .drop_duplicates("code", keep="first")
        .drop(columns=["source_rank"])
    )
    boards = raw_pool.apply(lambda r: board_limit(r["code"], r["name"]), axis=1)
    raw_pool["board"] = [x[0] for x in boards]
    raw_pool["limit_pct"] = [x[1] for x in boards]
    raw_pool["limit_style"] = raw_pool["limit_pct"].apply(limit_style)
    raw_pool["is_limit_up"] = raw_pool.apply(lambda r: is_limit_up(sf(r["pct_chg"]), sf(r["limit_pct"])), axis=1)
    pool = (
        raw_pool[raw_pool["is_limit_up"]]
        .sort_values(["limit_pct", "pct_chg", "code"], ascending=[False, False, True])
        .head(MAX_POOL_SCAN)
        .reset_index(drop=True)
    )
    diagnostics = {
        "source_counts": source_counts,
        "source_limit_counts": pool["source"].value_counts().to_dict() if not pool.empty else {},
        "board_counts": pool["board"].value_counts().to_dict() if not pool.empty else {},
        "limit_style_counts": pool["limit_style"].value_counts().to_dict() if not pool.empty else {},
        "total_limit_up_identified": int(len(pool)),
        "beijing_count": int((pool["board"] == "北交所").sum()) if not pool.empty else 0,
    }
    return pool, diagnostics


# =============================================================================
# 历史K线
# =============================================================================
def normalize_hist(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    mp = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "涨跌幅": "pct_chg",
        "换手率": "turnover",
    }
    df = raw.rename(columns={k: v for k, v in mp.items() if k in raw.columns})
    if not {"open", "close", "high", "low"}.issubset(set(df.columns)):
        return pd.DataFrame()
    for c in ["open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"]:
        if c in df.columns:
            df[c] = df[c].apply(sf)
    if "date" in df.columns:
        df["date"] = df["date"].astype(str)
        df = df.sort_values("date")
    return df.reset_index(drop=True)


def is_beijing_code(code: str) -> bool:
    code = str(code).zfill(6)
    return code.startswith(("920", "8", "4"))


def baostock_symbol(code: str) -> Optional[str]:
    code = str(code).zfill(6)
    if is_beijing_code(code):
        return None
    if code.startswith(("6", "9")):
        return f"sh.{code}"
    if code.startswith(("0", "2", "3")):
        return f"sz.{code}"
    return None


def ensure_baostock_login() -> bool:
    global BAOSTOCK_LOGGED_IN
    if bs is None:
        return False
    if BAOSTOCK_LOGGED_IN:
        return True
    try:
        lg = bs.login()
        BAOSTOCK_LOGGED_IN = getattr(lg, "error_code", "1") == "0"
        return BAOSTOCK_LOGGED_IN
    except Exception:
        return False


def logout_baostock() -> None:
    global BAOSTOCK_LOGGED_IN
    if bs is not None and BAOSTOCK_LOGGED_IN:
        try:
            bs.logout()
        except Exception:
            pass
    BAOSTOCK_LOGGED_IN = False


def fetch_hist_baostock(code: str, target_date: str) -> pd.DataFrame:
    symbol = baostock_symbol(code)
    if not symbol or not ensure_baostock_login():
        return pd.DataFrame()
    try:
        fields = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,pctChg,isST"
        rs = bs.query_history_k_data_plus(
            symbol,
            fields,
            start_date="2018-01-01",
            end_date=ymd_to_dash(target_date),
            frequency="d",
            adjustflag="2",
        )
        if getattr(rs, "error_code", "1") != "0":
            if len(HIST_FAILURE_SAMPLES) < 12:
                HIST_FAILURE_SAMPLES.append({"code": code, "source": "baostock", "reason": getattr(rs, "error_msg", "query error")})
            return pd.DataFrame()
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        df = pd.DataFrame(rows, columns=rs.fields)
        if df.empty:
            return pd.DataFrame()
        out = pd.DataFrame({
            "date": df["date"],
            "open": df["open"].apply(sf),
            "close": df["close"].apply(sf),
            "high": df["high"].apply(sf),
            "low": df["low"].apply(sf),
            "volume": df["volume"].apply(sf),
            "amount": df["amount"].apply(sf),
            "pct_chg": df["pctChg"].apply(sf),
            "turnover": df["turn"].apply(sf),
            "hist_source": "baostock",
        })
        return normalize_hist(out)
    except Exception as e:
        if len(HIST_FAILURE_SAMPLES) < 12:
            HIST_FAILURE_SAMPLES.append({"code": code, "source": "baostock", "reason": type(e).__name__})
        return pd.DataFrame()


def fetch_hist_akshare(code: str, target_date: str) -> pd.DataFrame:
    calls = [
        ("stock_zh_a_hist", {"symbol": code, "period": "daily", "start_date": "20180101", "end_date": target_date, "adjust": "qfq"}),
        ("stock_zh_a_hist", {"symbol": code, "period": "daily", "start_date": "20180101", "end_date": target_date, "adjust": ""}),
        ("stock_zh_a_hist", {"symbol": code, "period": "daily", "start_date": "20180101", "end_date": target_date}),
    ]
    for fn_name, kwargs in calls:
        df = normalize_hist(safe_source_call(fn_name, **kwargs))
        if len(df) >= 30:
            df["hist_source"] = "akshare"
            return df
    return pd.DataFrame()


def market_ids_for_code(code: str) -> List[int]:
    code = str(code).zfill(6)
    if code.startswith(("6", "688", "689")):
        return [1]
    if is_beijing_code(code):
        return [0, 1]
    return [0, 1]


def fetch_hist_eastmoney(code: str, target_date: str) -> pd.DataFrame:
    code = str(code).zfill(6)
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    fields1 = "f1,f2,f3,f4,f5,f6"
    fields2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    last_error = ""
    for market in market_ids_for_code(code):
        try:
            params = {
                "secid": f"{market}.{code}",
                "fields1": fields1,
                "fields2": fields2,
                "klt": "101",
                "fqt": "1",
                "beg": "20180101",
                "end": target_date,
            }
            r = requests.get(url, params=params, timeout=AK_TIMEOUT_SECONDS)
            r.raise_for_status()
            obj = r.json()
            klines = (((obj or {}).get("data") or {}).get("klines") or [])
            rows = []
            for line in klines:
                parts = str(line).split(",")
                if len(parts) < 11:
                    continue
                rows.append({
                    "date": parts[0],
                    "open": sf(parts[1]),
                    "close": sf(parts[2]),
                    "high": sf(parts[3]),
                    "low": sf(parts[4]),
                    "volume": sf(parts[5]),
                    "amount": sf(parts[6]),
                    "pct_chg": sf(parts[8]),
                    "turnover": sf(parts[10]),
                })
            df = normalize_hist(pd.DataFrame(rows))
            if len(df) >= 30:
                df["hist_source"] = "eastmoney_last_fallback"
                return df
            last_error = f"eastmoney market={market} rows={len(df)}"
        except Exception as e:
            last_error = f"eastmoney market={market} {type(e).__name__}"
    if len(HIST_FAILURE_SAMPLES) < 12:
        HIST_FAILURE_SAMPLES.append({"code": code, "source": "eastmoney", "reason": last_error or "empty"})
    return pd.DataFrame()


def fetch_hist(code: str, target_date: str) -> pd.DataFrame:
    code = str(code).zfill(6)
    # BaoStock 优先；北交所因覆盖不稳定，直接走 AKShare/东方财富兜底链。
    if not is_beijing_code(code):
        df = fetch_hist_baostock(code, target_date)
        if len(df) >= 30:
            return df
    df = fetch_hist_akshare(code, target_date)
    if len(df) >= 30:
        return df
    return fetch_hist_eastmoney(code, target_date)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for p in MA_PERIODS:
        df[f"ma{p}"] = df["close"].rolling(p).mean()
    df["bbi"] = (
        df["close"].rolling(3).mean()
        + df["close"].rolling(6).mean()
        + df["close"].rolling(12).mean()
        + df["close"].rolling(24).mean()
    ) / 4
    mid, std = df["close"].rolling(20).mean(), df["close"].rolling(20).std()
    df["boll_mid"], df["boll_up"], df["boll_low"] = mid, mid + 2 * std, mid - 2 * std
    df["boll_width"] = (df["boll_up"] - df["boll_low"]) / df["boll_mid"].replace(0, pd.NA)
    pc = df["close"].shift(1)
    df["tr"] = pd.concat([(df["high"] - df["low"]), (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr14"] = df["tr"].rolling(14).mean()
    return df


def prev_window(df: pd.DataFrame, n: int) -> pd.DataFrame:
    return df.iloc[max(0, len(df) - n - 1):len(df) - 1]


def ret_pct(df: pd.DataFrame, n: int) -> float:
    if len(df) <= n:
        return 0.0
    return rd(pct(df.iloc[-1]["close"], df.iloc[-n - 1]["close"]))


def prev_high(df: pd.DataFrame, n: int) -> float:
    sub = prev_window(df, n)
    return sf(sub["high"].max()) if not sub.empty else 0.0


def prev_low(df: pd.DataFrame, n: int) -> float:
    sub = prev_window(df, n)
    return sf(sub["low"].min()) if not sub.empty else 0.0


def range_position(df: pd.DataFrame, n: int) -> Optional[float]:
    h, l, c = prev_high(df, n), prev_low(df, n), sf(df.iloc[-1]["close"])
    return rd((c - l) / (h - l), 3) if h > l > 0 else None


def vol_ratio(df: pd.DataFrame, n: int) -> float:
    if len(df) <= n or "volume" not in df.columns:
        return 0.0
    av = sf(df.iloc[-n - 1:-1]["volume"].mean())
    return rd(sf(df.iloc[-1].get("volume")) / av) if av else 0.0


# =============================================================================
# 自然月K平台与核心线
# =============================================================================
def aggregate_natural_month(hist: pd.DataFrame) -> pd.DataFrame:
    """用日K聚合自然月K；核心线只看自然月，不看20日聚合K。"""
    if hist is None or hist.empty or "date" not in hist.columns:
        return pd.DataFrame()
    d = hist.copy()
    d["ym"] = d["date"].astype(str).str[:7]
    rows: List[Dict[str, Any]] = []
    for ym, g in d.groupby("ym"):
        g = g.sort_values("date")
        if g.empty:
            continue
        rows.append({
            "ym": ym,
            "open": sf(g.iloc[0].get("open")),
            "high": sf(g["high"].max()),
            "low": sf(g["low"].min()),
            "close": sf(g.iloc[-1].get("close")),
            "volume": sf(g["volume"].sum()),
            "amount": sf(g["amount"].sum()) if "amount" in g.columns else 0.0,
        })
    m = pd.DataFrame(rows).sort_values("ym").reset_index(drop=True)
    if m.empty:
        return m
    m["body_top"] = m[["open", "close"]].max(axis=1)
    m["body_bottom"] = m[["open", "close"]].min(axis=1)
    m["body_pct"] = (m["close"] - m["open"]).abs() / m["open"].replace(0, pd.NA)
    # 只用成交量，不用成交额。局部量能以过去12个月中位数为基准。
    m["rel_vol12"] = (m["volume"] / m["volume"].shift(1).rolling(12, min_periods=3).median().replace(0, pd.NA)).fillna(1.0)
    return m


def platform_width_limit(months: int) -> Tuple[float, str]:
    if months <= 5:
        return 0.45, "月线小平台"
    if months <= 11:
        return 0.60, "月线中平台"
    return 0.75, "月线大箱体"


def platform_duration_score(months: int) -> float:
    if months >= 12:
        return 1.00
    if months >= 9:
        return 0.85
    if months >= 6:
        return 0.65
    if months >= 3:
        return 0.40
    return 0.0


def find_platform_candidates(m: pd.DataFrame) -> List[Dict[str, Any]]:
    """寻找3根月K以上平台/箱体 + 之后破位。"""
    out: List[Dict[str, Any]] = []
    if len(m) < IPO_SKIP_MONTHS + 4:
        return out

    for break_idx in range(IPO_SKIP_MONTHS + 3, len(m)):
        br = m.iloc[break_idx]
        for start in range(max(IPO_SKIP_MONTHS, break_idx - PLATFORM_MAX_LOOKBACK_MONTHS), break_idx - 2):
            box = m.iloc[start:break_idx]
            months = len(box)
            if months < 3:
                continue

            med_close = sf(box["close"].median())
            if med_close <= 0:
                continue

            # 实体箱体宽度：P80实体顶 - P20实体底。避免长影线误伤。
            box_low = sf(box["body_bottom"].quantile(0.20))
            box_high = sf(box["body_top"].quantile(0.80))
            if box_low <= 0 or box_high <= box_low:
                continue

            width_pct = (box_high - box_low) / med_close
            width_limit, kind = platform_width_limit(months)

            high_vol_months = int((box["rel_vol12"] >= 1.25).sum())
            high_vol_density = high_vol_months / max(months, 1)
            volume_ok = high_vol_months >= 1 if months <= 5 else high_vol_density >= 0.20

            lower_touch = int(((box["body_bottom"] - box_low).abs() / box_low <= CORE_TOUCH_TOL).sum())
            lower_touch += int(((box["close"] - box_low).abs() / box_low <= CORE_TOUCH_TOL).sum())
            upper_touch = int(((box["body_top"] - box_high).abs() / box_high <= CORE_TOUCH_TOL).sum())
            upper_touch += int(((box["close"] - box_high).abs() / box_high <= CORE_TOUCH_TOL).sum())
            boundary_touch = lower_touch + upper_touch

            if width_pct > width_limit * 1.35 and boundary_touch < 3:
                continue
            if not volume_ok and high_vol_months == 0:
                continue
            if sf(br.close) >= box_low * (1 - PLATFORM_BREAK_TOL):
                continue

            break_body_pct = abs(sf(br.close) - sf(br.open)) / max(sf(br.open), 1e-6)
            break_depth_score = min((box_low - sf(br.close)) / box_low / 0.08, 1.0)
            width_score = 1.0 if width_pct <= width_limit else max(0.20, 1 - (width_pct - width_limit) / max(width_limit, 0.01))
            volume_score = min((high_vol_density / 0.35), 1.0) if months > 5 else min(high_vol_months, 1)
            boundary_score = min(boundary_touch / 5, 1.0)
            breakdown_score = min(break_depth_score + break_body_pct, 1.0)

            score = (
                22 * platform_duration_score(months)
                + 18 * width_score
                + 20 * volume_score
                + 16 * boundary_score
                + 24 * breakdown_score
            )
            if not volume_ok:
                score -= 8
            if width_pct > width_limit:
                score -= 4
            if sf(br.close) < sf(br.open):
                score += 3

            status = "high" if score >= 70 else ("suspect" if score >= 52 else "weak")
            out.append({
                "start": start,
                "end": break_idx - 1,
                "break_idx": break_idx,
                "platform_type": kind,
                "start_ym": ss(box.iloc[0].ym),
                "end_ym": ss(box.iloc[-1].ym),
                "break_ym": ss(br.ym),
                "box_low": rd(box_low),
                "box_high": rd(box_high),
                "months": months,
                "width_pct": rd(width_pct * 100),
                "width_limit_pct": rd(width_limit * 100),
                "high_volume_months": high_vol_months,
                "high_volume_density": rd(high_vol_density, 3),
                "lower_touch_count": lower_touch,
                "upper_touch_count": upper_touch,
                "break_close": rd(br.close),
                "break_body_pct": rd(break_body_pct * 100),
                "score": rd(score),
                "status": status,
                "volume_ok": bool(volume_ok),
            })

    return sorted(out, key=lambda x: (sf(x.get("score")), int(x.get("break_idx", 0))), reverse=True)[:10]


def cluster_prices(vals: List[float], tol: float = CORE_TOUCH_TOL) -> List[float]:
    vals = sorted([sf(v) for v in vals if sf(v) > 0])
    groups: List[List[float]] = []
    for v in vals:
        if not groups:
            groups.append([v])
            continue
        center = sorted(groups[-1])[len(groups[-1]) // 2]
        if abs(v - center) / max(v, 0.01) <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    centers: List[float] = []
    for g in groups:
        s = sorted(g)
        centers.append(s[len(s) // 2])
    return centers


def post_break_volume_rank(post: pd.DataFrame, idx: int) -> Dict[str, Any]:
    vols = sorted([(int(i), sf(r.volume)) for i, r in post.iterrows()], key=lambda x: x[1], reverse=True)
    if not vols:
        return {"rank_no": 999, "rank_pct": 0.0, "is_top2": False, "is_top20pct": False}
    rank_no = next((j + 1 for j, (i, _) in enumerate(vols) if i == int(idx)), len(vols))
    rank_pct = 1 - (rank_no - 1) / max(len(vols), 1)
    return {
        "rank_no": int(rank_no),
        "rank_pct": rd(rank_pct, 3),
        "is_top2": bool(rank_no <= 2),
        "is_top20pct": bool(rank_pct >= 0.80),
    }


def repair_event_count(touch_indices: List[int]) -> int:
    ids = sorted(set(int(x) for x in touch_indices))
    if not ids:
        return 0
    # 连续几个月贴线，算同一轮；隔3个月以上，通常才算新一轮反抽。
    count = 1
    last = ids[0]
    for idx in ids[1:]:
        if idx - last >= 3:
            count += 1
        last = idx
    return count


def evaluate_core_line(m: pd.DataFrame, bx: Dict[str, Any], line: float) -> Optional[Dict[str, Any]]:
    break_idx = int(bx["break_idx"])
    post = m.iloc[break_idx + 1:].copy()
    if post.empty:
        return None

    touch_indices: List[int] = []
    historical_indices: List[int] = []
    precise_high = precise_close = body_touch = deep_pierce = 0
    stage_high_volume = top2_volume = top20pct_volume = 0
    accept_count = 0
    touch_examples: List[str] = []
    stage_volume_examples: List[str] = []

    for idx, r in post.iterrows():
        hi = sf(r.high)
        cl = sf(r.close)
        top = sf(r.body_top)
        if cl > line * (1 + CORE_CLOSE_ACCEPT_TOL):
            accept_count += 1

        # 反抽失败触碰：高点到线附近/刺穿，但收盘没接受。
        if not (hi >= line * (1 - CORE_TOUCH_TOL) and cl <= line * (1 + CORE_CLOSE_ACCEPT_TOL)):
            continue

        touch_indices.append(int(idx))
        if idx < len(m) - 1:
            historical_indices.append(int(idx))

        vr = post_break_volume_rank(post, int(idx))
        is_precise_high = abs(hi - line) / line <= CORE_TOUCH_TOL
        is_precise_close = abs(cl - line) / line <= CORE_TOUCH_TOL
        is_body_touch = abs(top - line) / line <= CORE_TOUCH_TOL
        is_deep = hi > line * 1.05 and cl <= line * (1 + CORE_CLOSE_ACCEPT_TOL)
        is_stage = bool(vr["is_top2"] or vr["is_top20pct"])

        precise_high += int(is_precise_high)
        precise_close += int(is_precise_close)
        body_touch += int(is_body_touch)
        deep_pierce += int(is_deep)
        stage_high_volume += int(is_stage)
        top2_volume += int(vr["is_top2"])
        top20pct_volume += int(vr["is_top20pct"])

        label = "精准触碰" if (is_precise_high or is_precise_close or is_body_touch) else ("深刺穿收不住" if is_deep else "触线收不住")
        example = f"{ss(r.ym)} {label}，高{rd(hi)}/收{rd(cl)}，破位后量排名第{vr['rank_no']}"
        touch_examples.append(example)
        if is_stage:
            stage_volume_examples.append(
                f"{ss(r.ym)} 阶段大量反抽，高{rd(hi)}/收{rd(cl)}，破位后量排名第{vr['rank_no']}，分位{rd(sf(vr['rank_pct'])*100,1)}%"
            )

    if len(touch_indices) < 2:
        return None

    event_count = repair_event_count(touch_indices)
    historical_touch_count = len(set(historical_indices))
    only_latest_risk = historical_touch_count < 2

    score = (
        len(touch_indices) * 8
        + event_count * 12
        + historical_touch_count * 5
        + (precise_high + precise_close + body_touch) * 2
        + deep_pierce * 1
        + stage_high_volume * 10
        + top2_volume * 8
        + top20pct_volume * 4
        - accept_count * 2
        - (18 if only_latest_risk else 0)
    )

    if len(touch_indices) >= 3 and historical_touch_count >= 2 and top2_volume >= 1 and score >= 70:
        status = "高置信核心线"
    elif len(touch_indices) >= 3 and historical_touch_count >= 2 and stage_high_volume >= 1:
        status = "疑似核心线"
    elif len(touch_indices) >= 2 and historical_touch_count >= 1:
        status = "候选共振线"
    else:
        status = "弱候选线"

    # 用户明确：返抽量能必须是破位后阶段性数一数二的大量。
    # 所以没有阶段性大量触线，不许升高置信，最多候选。
    if len(touch_indices) >= 3 and stage_high_volume == 0:
        status = "候选共振线"

    grade = "S" if status == "高置信核心线" else ("A" if status == "疑似核心线" else ("B" if status == "候选共振线" else "C"))

    return {
        "line": rd(line),
        "score": rd(score),
        "status": status,
        "grade": grade,
        "touch_count": len(touch_indices),
        "historical_touch_count": historical_touch_count,
        "repair_event_count": event_count,
        "precise_high_touch_count": precise_high,
        "precise_close_touch_count": precise_close,
        "body_top_touch_count": body_touch,
        "deep_pierce_count": deep_pierce,
        "post_break_stage_high_volume_touch_count": stage_high_volume,
        "post_break_top2_volume_touch_count": top2_volume,
        "post_break_top20pct_volume_touch_count": top20pct_volume,
        "close_accept_count_recorded": accept_count,
        "only_latest_risk": only_latest_risk,
        "touch_examples": touch_examples[:8],
        "stage_volume_examples": stage_volume_examples[:6],
        "missing_conditions": [],
    }


def find_monthly_coreline(hist: pd.DataFrame) -> Dict[str, Any]:
    m = aggregate_natural_month(hist)
    if len(m) < 8:
        return {"valid": False, "status": "未确认", "reason": f"自然月K不足，当前{len(m)}根。", "box_candidates": []}

    box_candidates = find_platform_candidates(m)
    if not box_candidates:
        return {
            "valid": False,
            "status": "未确认",
            "reason": "当前阈值下未找到3根以上有量平台及其后破位；不等于图上一定没有平台。",
            "box_candidates": [],
        }

    best: Optional[Dict[str, Any]] = None
    enriched_boxes: List[Dict[str, Any]] = []

    for bx in box_candidates:
        break_idx = int(bx["break_idx"])
        low_bound = sf(bx["box_low"]) * 0.995
        high_bound = sf(bx["box_high"]) * 1.03
        vals: List[float] = []

        for _, r in m.iloc[break_idx + 1:].iterrows():
            # 候选线来自反抽失败过程里的高点、实体顶、收盘贴线。
            for v in [sf(r.high), sf(r.body_top), sf(r.close)]:
                if low_bound <= v <= high_bound:
                    vals.append(v)

        box_best: Optional[Dict[str, Any]] = None
        for line in cluster_prices(vals):
            item = evaluate_core_line(m, bx, line)
            if item and (box_best is None or sf(item["score"]) > sf(box_best["score"])):
                box_best = item

        ebx = dict(bx)
        if box_best:
            ebx["core_candidate"] = box_best
            cand = dict(box_best)
            cand["platform"] = bx
            cand["valid"] = cand["status"] in ("高置信核心线", "疑似核心线", "候选共振线")
            cand["reason"] = (
                f"{bx['start_ym']}~{bx['end_ym']}形成{bx['platform_type']}，"
                f"{bx['break_ym']}破位；破位后围绕{cand['line']}元反抽失败{cand['touch_count']}次，"
                f"其中阶段性大量反抽触线{cand['post_break_stage_high_volume_touch_count']}次。"
            )
            if cand["post_break_stage_high_volume_touch_count"] == 0:
                cand["missing_conditions"].append("没有破位后阶段性数一数二大量反抽触线，不能升高置信")
            if cand["historical_touch_count"] < 2:
                cand["missing_conditions"].append("历史触碰偏少，不能只靠最新大涨月确认核心线")
            if best is None or sf(cand["score"]) > sf(best["score"]):
                best = cand
        enriched_boxes.append(ebx)

    if best:
        best["box_candidates"] = enriched_boxes[:5]
        return best

    return {
        "valid": False,
        "status": "未确认",
        "reason": "找到了平台/破位候选，但破位后没有形成足够反抽失败共振。",
        "box_candidates": enriched_boxes[:5],
    }


# =============================================================================
# 无新闻版热点归因
# =============================================================================
def fetch_board_names(kind: str) -> pd.DataFrame:
    fn = "stock_board_concept_name_em" if kind == "concept" else "stock_board_industry_name_em"
    df = safe_source_call(fn)
    name_col = first_col(df, ["板块名称", "名称", "概念名称", "行业名称"])
    pct_col = first_col(df, ["涨跌幅", "涨幅", "涨跌幅%"])
    if df is None or df.empty or not name_col:
        return pd.DataFrame()
    out = pd.DataFrame({
        "hotspot": df[name_col].map(ss),
        "pct_chg": df[pct_col].map(sf) if pct_col else 0.0,
        "kind": kind,
    }).drop_duplicates("hotspot")
    return out[out["hotspot"] != ""]


def fetch_board_members(kind: str, symbol: str) -> List[str]:
    fn = "stock_board_concept_cons_em" if kind == "concept" else "stock_board_industry_cons_em"
    df = safe_source_call(fn, symbol=symbol)
    code_col = first_col(df, ["代码", "股票代码", "证券代码"])
    if df is None or df.empty or not code_col:
        return []
    return [code6(x) for x in df[code_col].tolist() if code6(x)]


def build_market_hotspots(limit_items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """只用板块行情与涨停集中度，不读新闻。"""
    limit_codes = {code6(x.get("code")) for x in limit_items if code6(x.get("code"))}
    stock_map: Dict[str, List[Dict[str, Any]]] = {c: [] for c in limit_codes}
    hotspots: List[Dict[str, Any]] = []

    for kind in ["concept", "industry"]:
        names = fetch_board_names(kind)
        if names.empty:
            continue

        # 同时保留涨幅靠前和所有可能命中涨停的板块；第一版控制数量，避免运行太慢。
        names = names.sort_values("pct_chg", ascending=False).head(HOTSPOT_BOARD_TOP_N)
        for _, r in names.iterrows():
            name = ss(r.hotspot)
            members = set(fetch_board_members(kind, name))
            time.sleep(HOTSPOT_MEMBER_SLEEP)
            hits = sorted(limit_codes & members)
            if not hits:
                continue

            pct_chg = sf(r.pct_chg)
            # 热点强度只来自行情：涨停命中数 + 板块涨幅。
            score = len(hits) * 10 + max(pct_chg, 0) * 2
            if score >= 55 or len(hits) >= 5:
                level = "S"
            elif score >= 35 or len(hits) >= 3:
                level = "A"
            elif score >= 18 or len(hits) >= 2:
                level = "B"
            else:
                level = "C"

            obj = {
                "hotspot": name,
                "kind": "概念" if kind == "concept" else "行业",
                "pct_chg": rd(pct_chg),
                "limit_hit_count": len(hits),
                "score": rd(score),
                "level": level,
                "hit_codes": hits[:30],
            }
            hotspots.append(obj)
            for c in hits:
                stock_map.setdefault(c, []).append(obj)

    hotspots = sorted(hotspots, key=lambda x: (sf(x["score"]), sf(x["pct_chg"]), x["limit_hit_count"]), reverse=True)[:30]
    for c in list(stock_map):
        stock_map[c] = sorted(stock_map[c], key=lambda x: sf(x["score"]), reverse=True)[:6]
    return hotspots, stock_map


def classify_limit_reason(core: Dict[str, Any], matches: List[Dict[str, Any]]) -> Tuple[str, str, int]:
    top_hot = matches[0] if matches else None
    core_status = ss(core.get("status"))
    core_strong = core_status in ("高置信核心线", "疑似核心线")
    core_mid = core_status == "候选共振线"
    hot_strong = bool(top_hot and ss(top_hot.get("level")) in ("S", "A"))
    hot_mid = bool(top_hot and ss(top_hot.get("level")) == "B")

    if hot_strong and core_strong:
        return (
            "行情热点+月线核心线共振",
            f"匹配{top_hot.get('kind')}{top_hot.get('hotspot')}，板块强度{top_hot.get('level')}；同时月线核心线状态为{core_status}。",
            90,
        )
    if hot_strong:
        return (
            "行情热点主导",
            f"匹配{top_hot.get('kind')}{top_hot.get('hotspot')}，涨停命中{top_hot.get('limit_hit_count')}只，板块涨幅{top_hot.get('pct_chg')}%。",
            78,
        )
    if core_strong:
        return (
            "技术核心线主导",
            f"未匹配强行情热点，但月线核心线状态为{core_status}，更像结构释放。",
            74,
        )
    if hot_mid and core_mid:
        return (
            "弱热点+候选核心线共振",
            f"热点强度中等，核心线也只是候选，属于可复盘样本，不硬定主因。",
            62,
        )
    if hot_mid:
        return (
            "弱热点扩散",
            f"匹配{top_hot.get('kind')}{top_hot.get('hotspot')}，但热点强度只有{top_hot.get('level')}。",
            55,
        )
    if core_mid:
        return (
            "候选核心线驱动",
            f"未匹配强热点，但存在候选共振线；需要人工校验这条线是否真的解释涨停。",
            52,
        )
    return (
        "原因不清晰",
        "未匹配到强行情热点，本类核心线也未高置信确认；不读取财经新闻，不硬编个股利好。",
        35,
    )


# =============================================================================
# 辅助维度与报告
# =============================================================================
def fallback_tags(row: pd.Series) -> List[str]:
    board, lim, pct_chg = ss(row.get("board")), sf(row.get("limit_pct")), sf(row.get("pct_chg"))
    tags = [f"{board}涨停", limit_style(lim)]
    if board == "北交所":
        tags.append("北交所30cm弹性样本")
    if pct_chg >= lim + 0.5:
        tags.append("涨幅超阈值强封样本")
    return list(dict.fromkeys(tags))


def structure_tags(df: pd.DataFrame) -> List[str]:
    tags: List[str] = []
    c = sf(df.iloc[-1]["close"])
    for n, label in [(20, "突破20日/月线窗口高点"), (60, "突破60日/季度窗口高点"), (100, "突破100日中期高点"), (250, "突破250日/年线窗口高点")]:
        if c >= prev_high(df, n) > 0:
            tags.append(label)
    pos250 = range_position(df, 250)
    if pos250 is not None and pos250 <= 0.35:
        tags.append("低位区间启动")
    elif pos250 is not None and pos250 >= 0.85:
        tags.append("长期区间高位加速")
    r20, r60 = ret_pct(df, 20), ret_pct(df, 60)
    if r20 >= 50:
        tags.append("20日/月线窗口涨幅超50%")
    if r60 >= 100:
        tags.append("60日/季线窗口涨幅超100%")
    vr20 = vol_ratio(df, 20)
    if 1.6 <= vr20 <= 4.5:
        tags.append("健康放量涨停")
    elif vr20 > 6:
        tags.append("爆量分歧涨停")
    elif 0 < vr20 < 1.1:
        tags.append("缩量快速板")
    return tags or ["普通涨停"]


def human_coreline_lines(core: Dict[str, Any]) -> List[str]:
    if not core.get("valid"):
        lines = [
            "- 月线核心线：暂不确认。",
            f"- 原因：{core.get('reason', '未给出原因')}",
        ]
        boxes = core.get("box_candidates", []) or []
        for bx in boxes[:3]:
            lines.append(
                f"  - 疑似平台：{bx.get('start_ym')}~{bx.get('end_ym')}，{bx.get('platform_type')}，"
                f"范围{bx.get('box_low')}~{bx.get('box_high')}元，宽度{bx.get('width_pct')}%，"
                f"分数{bx.get('score')}，破位月{bx.get('break_ym')}。"
            )
        return lines

    bx = core.get("platform", {}) or {}
    lines = [
        f"- 月线核心线：{core.get('line')}元｜{core.get('status')}｜级别{core.get('grade')}｜分数{core.get('score')}。",
        f"- 平台背景：{bx.get('start_ym')}~{bx.get('end_ym')}，{bx.get('platform_type')}，"
        f"范围{bx.get('box_low')}~{bx.get('box_high')}元，宽度{bx.get('width_pct')}%/上限{bx.get('width_limit_pct')}%，"
        f"高量月{bx.get('high_volume_months')}个。",
        f"- 破位背景：{bx.get('break_ym')}收盘{bx.get('break_close')}元跌破平台下沿，破位月实体幅度{bx.get('break_body_pct')}%。",
        f"- 共振证据：触碰/刺穿{core.get('touch_count')}次，历史触碰{core.get('historical_touch_count')}次，"
        f"分成{core.get('repair_event_count')}轮反抽事件。",
        f"- 关键量能：破位后阶段性大量反抽触线{core.get('post_break_stage_high_volume_touch_count')}次，"
        f"其中Top1/Top2大量{core.get('post_break_top2_volume_touch_count')}次，前20%高量{core.get('post_break_top20pct_volume_touch_count')}次。",
    ]
    if core.get("stage_volume_examples"):
        lines.append("- 阶段大量反抽样例：" + "；".join(core.get("stage_volume_examples", [])[:3]) + "。")
    if core.get("touch_examples"):
        lines.append("- 典型触碰：" + "；".join(core.get("touch_examples", [])[:4]) + "。")
    if core.get("missing_conditions"):
        lines.append("- 未升档原因：" + "；".join(core.get("missing_conditions", [])) + "。")
    if core.get("only_latest_risk"):
        lines.append("- 反证提醒：历史共振偏少，最新涨停月贡献较大，不能当高置信核心线。")
    return lines


def make_telegram_summary(target_date: str, results: List[Dict[str, Any]], hotspots: List[Dict[str, Any]], elapsed: float) -> str:
    status_count: Dict[str, int] = {}
    reason_count: Dict[str, int] = {}
    for x in results:
        status = ss((x.get("core_line") or {}).get("status", "未确认"))
        reason = ss(x.get("reason_type"))
        status_count[status] = status_count.get(status, 0) + 1
        reason_count[reason] = reason_count.get(reason, 0) + 1

    lines = [
        "🧬【五号员工-涨停归因摘要】",
        f"日期：{target_date}",
        f"版本：{VERSION}",
        f"耗时：{fmt_seconds(elapsed)}",
        f"已分析涨停/极强样本：{len(results)}只",
        f"核心线分布：{json.dumps(status_count, ensure_ascii=False)}",
        f"涨停原因分布：{json.dumps(reason_count, ensure_ascii=False)}",
        "",
        "今日行情热点（无新闻）：",
    ]
    for h in hotspots[:8]:
        lines.append(f"- {h.get('hotspot')}({h.get('kind')})：{h.get('level')}级，涨幅{h.get('pct_chg')}%，涨停命中{h.get('limit_hit_count')}只")

    important = [x for x in results if ss((x.get("core_line") or {}).get("status")) in ("高置信核心线", "疑似核心线")]
    if important:
        lines.append("")
        lines.append("高质量核心线样本：")
        for x in important[:12]:
            core = x.get("core_line") or {}
            lines.append(f"- {x.get('name')}({x.get('code')})：{core.get('line')}元，{core.get('status')}，原因={x.get('reason_type')}")
    lines.append("")
    lines.append("完整逐只报告见 artifact：employee5_reports/limit_up_research_report.md")
    return "\n".join(lines)


def build_report(target_date: str, pool: pd.DataFrame, diagnostics: Dict[str, Any], hotspots: List[Dict[str, Any]], results: List[Dict[str, Any]], hist_source_count: Dict[str, int], elapsed: float) -> Tuple[str, Dict[str, Any]]:
    status_count: Dict[str, int] = {}
    reason_count: Dict[str, int] = {}
    type_count: Dict[str, int] = {}
    for x in results:
        st = ss((x.get("core_line") or {}).get("status", "未确认"))
        rt = ss(x.get("reason_type"))
        status_count[st] = status_count.get(st, 0) + 1
        reason_count[rt] = reason_count.get(rt, 0) + 1
        for t in x.get("tags", []) or []:
            type_count[t] = type_count.get(t, 0) + 1

    lines = [
        "🧬【五号员工-涨停/极强样本归因报告】",
        f"日期：{target_date}",
        f"版本：{VERSION}",
        f"运行耗时：{fmt_seconds(elapsed)}",
        "固定定位：不荐股、不打买入分、不输出交易优先级。",
        "热点纪律：本版只使用板块行情、涨停集中度、个股板块归属；不读取财经新闻、公告、互动平台，不硬编个股利好。",
        "核心线纪律：自然月K；3根月K以上平台/箱体破位后，反抽/刺穿同一区域但收盘未接受；破位后阶段性数一数二大量反抽失败，是高质量核心线关键证据。",
        "",
        "一、基础统计：",
        f"- 涨停总识别：{len(pool)}只",
        f"- 已完成归因：{len(results)}只",
        f"- 板块分布：{json.dumps(diagnostics.get('board_counts', {}), ensure_ascii=False)}",
        f"- 涨停制度分布：{json.dumps(diagnostics.get('limit_style_counts', {}), ensure_ascii=False)}",
        f"- 历史K线来源：{json.dumps(hist_source_count, ensure_ascii=False)}",
        f"- 核心线状态分布：{json.dumps(status_count, ensure_ascii=False)}",
        f"- 涨停原因类型分布：{json.dumps(reason_count, ensure_ascii=False)}",
        "",
        "二、今日行情热点总览（不含财经新闻）：",
    ]
    lines += [
        f"- {h.get('hotspot')}({h.get('kind')})：{h.get('level')}级，板块涨幅{h.get('pct_chg')}%，涨停命中{h.get('limit_hit_count')}只，强度分{h.get('score')}"
        for h in hotspots[:15]
    ] or ["- 未能稳定提取热点；只保留个股技术归因。"]

    lines.append("")
    lines.append("三、全市场涨停大类统计：")
    for tag, cnt in sorted(type_count.items(), key=lambda x: x[1], reverse=True)[:18]:
        lines.append(f"- {tag}：{cnt}只")

    lines.append("")
    lines.append("四、逐只核心线与涨停原因：")
    for i, x in enumerate(results, 1):
        lines.append(f"\n【{i}】{x.get('name')}({x.get('code')})｜{x.get('board')}｜涨幅{rd(x.get('pct_chg'))}%｜近20日{rd(x.get('return20'))}%｜K线源={x.get('hist_source')}")
        lines.append(f"涨停原因类型：{x.get('reason_type')}｜置信度{int(sf(x.get('reason_confidence')))}。{x.get('reason_text')}")
        matches = x.get("hotspot_matches", []) or []
        if matches:
            lines.append(
                "行情热点证据："
                + "；".join([
                    f"{m.get('kind')}{m.get('hotspot')}({m.get('level')}级，涨幅{m.get('pct_chg')}%，命中{m.get('limit_hit_count')}只)"
                    for m in matches[:4]
                ])
                + "。"
            )
        else:
            lines.append("行情热点证据：未匹配到强板块热点；本段不读取新闻，不能硬编个股利好。")
        lines += human_coreline_lines(x.get("core_line", {}) or {})
        tags = x.get("tags", []) or []
        if tags:
            lines.append("日线/路径标签：" + "；".join(tags[:8]) + "。")

    lines.extend([
        "",
        "五、复盘纪律：",
        "- 当前核心线只覆盖第一类：月线平台/箱体破位后反抽失败共振线；未识别不代表没有其他类型核心线。",
        "- 没有破位后阶段性大量反抽触线的线，只能降为候选，不得高置信。",
        "- 不能因为涨停后股价跑高，就去天上找孤立线；没有历史共振，只能列为弱候选或原因不清晰。",
        "- 本报告用于归因校验，不构成买卖建议。",
    ])

    payload = {
        "target_date": target_date,
        "version": VERSION,
        "run_elapsed_seconds": round(elapsed, 2),
        "run_elapsed_text": fmt_seconds(elapsed),
        "diagnostics": diagnostics,
        "hist_source_count": hist_source_count,
        "hotspots": hotspots,
        "core_status_count": status_count,
        "reason_type_count": reason_count,
        "summary_type_count": type_count,
        "results": results,
        "hist_failure_samples": HIST_FAILURE_SAMPLES,
        "coreline_definition": {
            "platform_min_months": 3,
            "small_platform_width_limit_pct": 45,
            "middle_platform_width_limit_pct": 60,
            "large_box_width_limit_pct": 75,
            "touch_tol_pct": CORE_TOUCH_TOL * 100,
            "close_accept_tol_pct": CORE_CLOSE_ACCEPT_TOL * 100,
            "break_tol_pct": PLATFORM_BREAK_TOL * 100,
            "stage_volume_rule": "post-break volume top2 or top20pct",
        },
    }
    return "\n".join(lines), payload


# =============================================================================
# 主流程
# =============================================================================
def main() -> None:
    run_start = time.time()
    target_date = latest_trade_date()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"employee5 target date: {target_date}", flush=True)
    write_progress("启动", 0, 100, run_start, f"target_date={target_date}")

    pool, diagnostics = fetch_limit_pool(target_date, run_start)
    if pool.empty:
        msg = f"🧬【五号员工-涨停归因】\n日期：{target_date}\n未识别到涨停/极强样本。"
        (REPORT_DIR / "limit_up_research_report.md").write_text(msg, encoding="utf-8")
        (REPORT_DIR / "limit_up_research_report.json").write_text(json.dumps({"target_date": target_date, "results": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        send_msg(msg)
        return

    pool = pool.head(MAX_POOL_SCAN).copy()
    base_items: List[Dict[str, Any]] = []
    for _, row in pool.iterrows():
        base_items.append({
            "code": ss(row.get("code")),
            "name": ss(row.get("name")),
            "board": ss(row.get("board")),
            "pct_chg": sf(row.get("pct_chg")),
            "limit_style": ss(row.get("limit_style")),
            "tags": fallback_tags(row),
        })
    write_progress("② 涨停池整理", 1, 1, run_start, f"涨停总数={len(base_items)} 北交所={diagnostics.get('beijing_count', 0)}")

    write_progress("③ 行情热点提取", 0, 1, run_start, "只用板块行情与涨停集中度，不读取财经新闻")
    hotspots, hotspot_map = build_market_hotspots(base_items)
    write_progress("③ 行情热点提取", 1, 1, run_start, f"热点数={len(hotspots)}")

    results: List[Dict[str, Any]] = []
    hist_source_count: Dict[str, int] = {}
    scan_items = base_items[:ANALYZE_MAX_STOCKS]
    write_progress("④ 逐只K线归因", 0, len(scan_items), run_start, "月线核心线 + 热点归因")

    for idx, item in enumerate(scan_items, 1):
        code, name = code6(item.get("code")), ss(item.get("name"))
        hist = fetch_hist(code, target_date)
        time.sleep(REQUEST_SLEEP)

        if hist is None or hist.empty or len(hist) < 30:
            core = {"valid": False, "status": "未确认", "reason": "历史K线不足，无法计算自然月核心线。", "box_candidates": []}
            return20 = 0.0
            tags = item.get("tags", [])
            hist_source = "missing"
        else:
            df = add_indicators(hist)
            return20 = ret_pct(df, 20)
            hist_source = ss(hist.get("hist_source", pd.Series(["unknown"])).iloc[-1]) if "hist_source" in hist.columns else "unknown"
            hist_source_count[hist_source] = hist_source_count.get(hist_source, 0) + 1
            tags = list(dict.fromkeys((item.get("tags", []) or []) + structure_tags(df)))
            core = find_monthly_coreline(hist)

        matches = hotspot_map.get(code, [])
        reason_type, reason_text, confidence = classify_limit_reason(core, matches)

        results.append({
            "code": code,
            "name": name,
            "board": item.get("board"),
            "pct_chg": item.get("pct_chg"),
            "limit_style": item.get("limit_style"),
            "return20": return20,
            "hist_source": hist_source,
            "tags": tags,
            "hotspot_matches": matches,
            "core_line": core,
            "reason_type": reason_type,
            "reason_text": reason_text,
            "reason_confidence": confidence,
        })

        if idx == 1 or idx == len(scan_items) or idx % max(PROGRESS_EVERY, 1) == 0:
            write_progress("④ 逐只K线归因", idx, len(scan_items), run_start, f"当前={name}({code}) 原因={reason_type}")

    elapsed = time.time() - run_start
    write_progress("⑤ 报告生成", 0, 1, run_start, "写入 md/json/artifact")
    text, payload = build_report(target_date, pool, diagnostics, hotspots, results, hist_source_count, elapsed)

    # 保持旧 artifact 文件名兼容，同时新增更直观的文件名。
    (REPORT_DIR / "limit_up_research_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "limit_up_research_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "limit_up_cause_pool_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "limit_up_cause_pool_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "limit_up_deep_samples.json").write_text(json.dumps(results[:DEEP_SAMPLE_COUNT], ensure_ascii=False, indent=2), encoding="utf-8")

    write_progress("⑤ 报告生成", 1, 1, run_start, f"报告完成 样本={len(results)}")
    print(text, flush=True)

    summary = make_telegram_summary(target_date, results, hotspots, elapsed)
    write_progress("⑥ Telegram摘要推送", 0, 1, run_start, "完整报告在artifact，Telegram只发摘要")
    send_msg(summary)
    write_progress("完成", 100, 100, run_start, f"总耗时={fmt_seconds(time.time() - run_start)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = "❌ 五号员工运行失败\n" + str(e) + "\n" + traceback.format_exc()
        print(err, flush=True)
        send_msg(err)
        raise
    finally:
        logout_baostock()
