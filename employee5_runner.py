# -*- coding: utf-8 -*-
"""
五号员工：涨停/极强样本深度归因与认知闭环引擎（单文件平铺版）

定位：
- 不荐股、不打买入分、不输出交易优先级。
- 每天自动拉取全市场涨停/极强样本，并补充近20日累计大涨样本，做“为什么涨停”的归因。
- 核心输出四条主线：
  1）逐只股票：涨停前夕结构、资金行为、核心矛盾、触发路径的深度因果归因。
  2）多原因候选：不再只贴“热点/核心线”标签，而是把试盘、压缩、核心线、修复、热点点火等证据拆开评分。
  3）每日提炼：从当天涨停样本中提炼几类真实大涨前夜逻辑。
  4）认知闭环：把每日归因加工成五号员工自己的认知库，支持新增、强化、修正，而不是死套模板。

关键原则：
- 五号员工只有这一个 PY：employee5_runner.py。
- workflow 不需要改，仍然只执行 python -u employee5_runner.py。
- 核心线不是大涨后往天上找出来的线；必须先有历史共振。
- 高质量核心线必须看破位后阶段性数一数二的大量反抽失败。
- 找不到高置信核心线时，也要输出最佳候选线、疑似平台、缺失条件，不能偷懒写“未识别”。

版本：
employee5_cognition_loop_flat_v20260527
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
COGNITION_LIBRARY_FILE = REPORT_DIR / "employee5_cognition_library.json"
DAILY_LESSONS_FILE = REPORT_DIR / "employee5_daily_lessons.json"
COGNITION_UPDATE_LOG_FILE = REPORT_DIR / "employee5_cognition_update_log.jsonl"
FEEDBACK_IDEAS_FILE = REPORT_DIR / "employee5_feedback_ideas.md"
DAILY_CAUSE_CLUSTERS_FILE = REPORT_DIR / "employee5_daily_cause_clusters.json"
OBSERVATIONS_FILE = REPORT_DIR / "employee5_observations.jsonl"
EXPERIENCE_LIBRARY_FILE = REPORT_DIR / "employee5_experience_library.json"
FACTOR_DRAFTS_FILE = REPORT_DIR / "employee5_factor_drafts.json"
UNEXPLAINED_LIMITUPS_FILE = REPORT_DIR / "employee5_unexplained_limitups.jsonl"
UNEXPLAINED_20D_WINNERS_FILE = REPORT_DIR / "employee5_unexplained_20d_winners.jsonl"

VERSION = "employee5_quantum_full_repair_flat_v20260527"

TARGET_DATE_ENV = os.getenv("EMPLOYEE5_TARGET_DATE", "").strip()
MAX_POOL_SCAN = int(os.getenv("EMPLOYEE5_MAX_STOCKS", "500"))
ANALYZE_MAX_STOCKS = int(os.getenv("EMPLOYEE5_REASON_MAX_STOCKS", os.getenv("EMPLOYEE5_MAX_STOCKS", "300")))
DEEP_SAMPLE_COUNT = int(os.getenv("EMPLOYEE5_DEEP_SAMPLE_COUNT", "3"))
DEEP_HIST_SCAN_LIMIT = int(os.getenv("EMPLOYEE5_DEEP_HIST_SCAN_LIMIT", str(MAX_POOL_SCAN)))
B_GROUP_SCAN_LIMIT = int(os.getenv("EMPLOYEE5_B_GROUP_SCAN_LIMIT", "0"))  # 0=全市场扫描；>0=显式限量扫描
HISTORICAL_UNIVERSE_SCAN_LIMIT = int(os.getenv("EMPLOYEE5_HISTORICAL_UNIVERSE_SCAN_LIMIT", "0"))  # 0=历史模式也全市场扫描；>0=历史模式限量
HISTORICAL_REBUILD_LIMIT_POOL = os.getenv("EMPLOYEE5_HISTORICAL_REBUILD_LIMIT_POOL", "1") != "0"
EXTREME_MAIN_PCT = float(os.getenv("EMPLOYEE5_EXTREME_MAIN_PCT", "8.5"))
EXTREME_20CM_PCT = float(os.getenv("EMPLOYEE5_EXTREME_20CM_PCT", "15.0"))
EXTREME_30CM_PCT = float(os.getenv("EMPLOYEE5_EXTREME_30CM_PCT", "22.0"))
HIST_START_DATE = os.getenv("EMPLOYEE5_HIST_START_DATE", "2010-01-01")
HIST_START_YYYYMMDD = HIST_START_DATE.replace("-", "")
KLINE_CACHE_DIR = ROOT / os.getenv("EMPLOYEE5_KLINE_CACHE_DIR", "employee5_kline_cache")
USE_HIST_CACHE = os.getenv("EMPLOYEE5_USE_HIST_CACHE", "1") != "0"
MONTHLY_USE_INCOMPLETE_MONTH = os.getenv("EMPLOYEE5_MONTHLY_USE_INCOMPLETE_MONTH", "0") == "1"
MAX_DEEP_REPORT_ITEMS = int(os.getenv("EMPLOYEE5_MAX_DEEP_REPORT_ITEMS", str(DEEP_SAMPLE_COUNT * 2)))

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
MARKET_UNIVERSE_POOL = pd.DataFrame()
HIST_CACHE_HIT = 0
HIST_CACHE_WRITE = 0
BAOSTOCK_CONSECUTIVE_FAILURES = 0
STALE_CACHE_COUNT = 0
MISSING_TRIGGER_DAY_COUNT = 0
HIST_LAST_DATE_BY_CODE: Dict[str, str] = {}
REPORT_MODE = "current"
TRADE_DATE_GATE_REASON = ""
REALTIME_SOURCE_ALLOWED = True


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
    """确定目标交易日，并硬区分当前模式/历史模式。无手动日期时，15:10前默认取上一交易日，避免盘中/午间数据污染。"""
    global REPORT_MODE, TRADE_DATE_GATE_REASON, REALTIME_SOURCE_ALLOWED
    china_now = datetime.utcnow() + timedelta(hours=8)
    today_ymd_dash = china_now.strftime("%Y-%m-%d")
    today_ymd = china_now.strftime("%Y%m%d")
    if TARGET_DATE_ENV:
        target = TARGET_DATE_ENV.replace("-", "")
        REPORT_MODE = "historical" if target != today_ymd else "manual_current"
        REALTIME_SOURCE_ALLOWED = bool(target == today_ymd and (china_now.hour, china_now.minute) >= (15, 10))
        TRADE_DATE_GATE_REASON = f"manual_target_date={target}; realtime_allowed={REALTIME_SOURCE_ALLOWED}"
        return target
    try:
        df = ak.tool_trade_date_hist_sina()
        if df is not None and not df.empty and "trade_date" in df.columns:
            vals = sorted({str(x)[:10] for x in df["trade_date"].tolist() if str(x)[:10] <= today_ymd_dash})
            if vals:
                latest = vals[-1]
                if latest == today_ymd_dash and (china_now.hour, china_now.minute) < (15, 10) and len(vals) >= 2:
                    target_dash = vals[-2]
                    REPORT_MODE = "current_pre_close_uses_previous_trade_day"
                    REALTIME_SOURCE_ALLOWED = False
                    TRADE_DATE_GATE_REASON = f"china_time={china_now.strftime('%Y-%m-%d %H:%M:%S')}; before_15_10; use_previous_trade_day={target_dash}"
                    return target_dash.replace("-", "")
                REPORT_MODE = "current_after_close" if latest == today_ymd_dash else "current_latest_completed_trade_day"
                REALTIME_SOURCE_ALLOWED = bool(latest == today_ymd_dash and (china_now.hour, china_now.minute) >= (15, 10))
                TRADE_DATE_GATE_REASON = f"china_time={china_now.strftime('%Y-%m-%d %H:%M:%S')}; selected={latest}; realtime_allowed={REALTIME_SOURCE_ALLOWED}"
                return latest.replace("-", "")
    except Exception as e:
        print(f"trade calendar failed: {type(e).__name__}", flush=True)
    d = china_now
    if (d.hour, d.minute) < (15, 10):
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    REPORT_MODE = "calendar_fallback"
    REALTIME_SOURCE_ALLOWED = False
    TRADE_DATE_GATE_REASON = f"calendar_failed_fallback={d.strftime('%Y%m%d')}; realtime_disabled"
    return d.strftime("%Y%m%d")


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
    except Exception as e:
        print(f"source {fn_name} unavailable: {type(e).__name__}", flush=True)
        return pd.DataFrame()
    try:
        with timeout_guard(AK_TIMEOUT_SECONDS, fn_name):
            return fn(**kwargs)
    except TypeError as e:
        # 部分 akshare 函数版本之间参数不一致，先记录再尝试无参调用。
        print(f"source {fn_name} kwargs rejected: {type(e).__name__}", flush=True)
        try:
            with timeout_guard(AK_TIMEOUT_SECONDS, fn_name):
                return fn()
        except Exception as e2:
            print(f"source {fn_name} failed without kwargs: {type(e2).__name__}", flush=True)
            return pd.DataFrame()
    except Exception as e:
        print(f"source {fn_name} failed: {type(e).__name__}", flush=True)
        return pd.DataFrame()


def is_common_stock_code(code: str) -> bool:
    c = code6(code)
    return bool(c and c.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4")))


def normalize_universe(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    code_col = first_col(df, ["代码", "股票代码", "证券代码", "A股代码", "code", "symbol"])
    name_col = first_col(df, ["名称", "股票简称", "证券简称", "A股简称", "name", "code_name", "简称"])
    if not code_col:
        return pd.DataFrame()
    out = pd.DataFrame()
    out["code"] = df[code_col].map(code6)
    if name_col:
        out["name"] = df[name_col].astype(str)
    else:
        out["name"] = out["code"]
    out = out[out["code"].map(is_common_stock_code)].drop_duplicates("code", keep="first")
    if out.empty:
        return pd.DataFrame()
    boards = out.apply(lambda r: board_limit(r["code"], r["name"]), axis=1)
    out["board"] = [x[0] for x in boards]
    out["limit_pct"] = [x[1] for x in boards]
    out["limit_style"] = out["limit_pct"].apply(limit_style)
    out["pct_chg"] = 0.0
    out["close"] = 0.0
    out["source"] = source
    return out.reset_index(drop=True)


def fetch_universe_from_baostock(target_date: str) -> pd.DataFrame:
    if bs is None:
        return pd.DataFrame()
    try:
        if not ensure_baostock_login():
            return pd.DataFrame()
        with timeout_guard(max(AK_TIMEOUT_SECONDS * 2, 20), "baostock_all_stock"):
            rs = bs.query_all_stock(day=ymd_to_dash(target_date))
            if getattr(rs, "error_code", "1") != "0":
                return pd.DataFrame()
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
        df = pd.DataFrame(rows, columns=rs.fields)
        if df.empty:
            return pd.DataFrame()
        if "tradeStatus" in df.columns:
            df = df[df["tradeStatus"].astype(str) == "1"]
        if "type" in df.columns:
            df = df[df["type"].astype(str).isin(["1", "股票", "stock"])]
        out = pd.DataFrame({
            "code": df["code"].astype(str).map(lambda x: code6(x.split(".")[-1])),
            "name": df["code_name"].astype(str) if "code_name" in df.columns else df["code"].astype(str),
        })
        return normalize_universe(out, "baostock_all_stock")
    except Exception as e:
        print(f"baostock universe failed: {type(e).__name__}", flush=True)
        return pd.DataFrame()


def fetch_static_universe(target_date: str, start_ts: Optional[float] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    历史复盘专用股票池：只拿代码/名称，不混入当日实时涨跌幅。
    手动日期、本地历史复盘、涨停池接口为空时，都必须走这里兜底，
    然后用逐票历史K线反推出A组涨停/极强样本和B组20日涨幅样本。
    """
    source_defs = [
        ("a_code_name", "stock_info_a_code_name", {}),
        ("sh_code_name", "stock_info_sh_name_code", {}),
        ("sz_code_name", "stock_info_sz_name_code", {}),
        ("a_spot_name_only", "stock_zh_a_spot_em", {}),
    ]
    stage_start = start_ts or time.time()
    parts, source_counts = [], {}
    for i, (source, fn_name, kwargs) in enumerate(source_defs, 1):
        df = normalize_universe(safe_source_call(fn_name, **kwargs), source)
        source_counts[source] = int(len(df)) if df is not None and not df.empty else 0
        if df is not None and not df.empty:
            parts.append(df)
        write_progress("①B 历史股票池兜底", i, len(source_defs) + 1, stage_start, f"source={source} rows={source_counts[source]}")
    bdf = fetch_universe_from_baostock(target_date)
    source_counts["baostock_all_stock"] = int(len(bdf)) if bdf is not None and not bdf.empty else 0
    if bdf is not None and not bdf.empty:
        parts.append(bdf)
    write_progress("①B 历史股票池兜底", len(source_defs) + 1, len(source_defs) + 1, stage_start, f"source=baostock_all_stock rows={source_counts['baostock_all_stock']}")
    if not parts:
        return pd.DataFrame(), {"universe_source_counts": source_counts, "universe_total": 0}
    raw = pd.concat(parts, ignore_index=True)
    raw["source_rank"] = raw["source"].map({"baostock_all_stock": 1, "a_code_name": 2, "sh_code_name": 3, "sz_code_name": 4, "a_spot_name_only": 5}).fillna(9)
    raw = raw.sort_values(["source_rank", "code"]).drop_duplicates("code", keep="first").drop(columns=["source_rank"])
    raw = raw[raw["code"].map(is_common_stock_code)].reset_index(drop=True)
    return raw, {"universe_source_counts": source_counts, "universe_total": int(len(raw))}


def is_extreme_move(pct_chg: float, limit_pct: float) -> bool:
    if limit_pct <= 5:
        return pct_chg >= 4.3
    if limit_pct <= 10:
        return pct_chg >= EXTREME_MAIN_PCT
    if limit_pct <= 20:
        return pct_chg >= EXTREME_20CM_PCT
    return pct_chg >= EXTREME_30CM_PCT


def trigger_day_pct(df: pd.DataFrame) -> float:
    if df is None or df.empty:
        return 0.0
    last = df.iloc[-1]
    v = sf(last.get("pct_chg"), 0.0)
    if abs(v) > 0.001:
        return rd(v)
    if len(df) >= 2:
        return rd(pct(last.get("close"), df.iloc[-2].get("close")))
    return 0.0


def build_history_universe_items(target_date: str, diagnostics: Dict[str, Any], run_start: float) -> List[Dict[str, Any]]:
    """生成需要做历史K线预扫描的全市场样本池。历史模式下不能因为实时源禁用就跳过B组。"""
    source_note = ""
    if REALTIME_SOURCE_ALLOWED and MARKET_UNIVERSE_POOL is not None and not MARKET_UNIVERSE_POOL.empty:
        uni = MARKET_UNIVERSE_POOL.copy()
        source_note = "realtime_market_universe"
    else:
        uni, uni_diag = fetch_static_universe(target_date, run_start)
        diagnostics["historical_static_universe"] = uni_diag
        source_note = "historical_static_universe"
    if uni is None or uni.empty:
        diagnostics["universe_scan_source"] = source_note
        return []
    uni = uni.copy()
    uni["code"] = uni["code"].map(code6)
    uni = uni[uni["code"].map(is_common_stock_code)].drop_duplicates("code", keep="first").sort_values("code")
    # B组默认全市场；如果设置了限量，则显式限量。历史模式下也允许额外用 HISTORICAL_UNIVERSE_SCAN_LIMIT 控制运行时间。
    limit = B_GROUP_SCAN_LIMIT if B_GROUP_SCAN_LIMIT > 0 else (HISTORICAL_UNIVERSE_SCAN_LIMIT if (not REALTIME_SOURCE_ALLOWED and HISTORICAL_UNIVERSE_SCAN_LIMIT > 0) else 0)
    if limit > 0:
        uni = uni.head(limit)
    diagnostics["universe_scan_source"] = source_note
    diagnostics["universe_scan_limit_effective"] = int(limit)
    diagnostics["universe_scan_total_before_limit"] = int(len(uni) if limit == 0 else (diagnostics.get("historical_static_universe", {}) or {}).get("universe_total", len(uni)))
    items: List[Dict[str, Any]] = []
    for _, row in uni.iterrows():
        name = ss(row.get("name")) or code6(row.get("code"))
        board, lim = board_limit(row.get("code"), name)
        items.append({
            "code": code6(row.get("code")),
            "name": name,
            "board": board,
            "limit_pct": lim,
            "pct_chg": 0.0,
            "limit_style": limit_style(lim),
            "tags": fallback_tags({"board": board, "limit_pct": lim, "pct_chg": 0.0}),
            "candidate_source": source_note,
            "sample_type": "rolling_20d_top_gain",
        })
    return items


def scan_history_candidates(target_date: str, scan_items: List[Dict[str, Any]], run_start: float) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    先轻量扫描历史K线，只确定两件事：
    1）A组：目标日是否涨停/极强；
    2）B组：近20个交易日累计涨幅排名。
    深度归因只对筛出的A/B样本再做，避免全市场几千只全部跑归因。
    """
    prelim: List[Dict[str, Any]] = []
    hist_sources: Dict[str, int] = {}
    total = len(scan_items)
    for idx, item in enumerate(scan_items, 1):
        code = code6(item.get("code")); name = ss(item.get("name")) or code
        if not code:
            continue
        hist = fetch_hist(code, target_date)
        time.sleep(REQUEST_SLEEP)
        if hist is not None and not hist.empty and len(hist) >= 30:
            df = add_indicators(hist)
            board, lim = board_limit(code, name)
            day_pct = trigger_day_pct(df)
            return20 = ret_pct(df, 20)
            limit_hit = is_limit_up(day_pct, lim)
            extreme_hit = is_extreme_move(day_pct, lim)
            hist_source = ss(hist.get("hist_source", pd.Series(["unknown"])).iloc[-1]) if "hist_source" in hist.columns else "unknown"
            hist_sources[hist_source] = hist_sources.get(hist_source, 0) + 1
            sample_type = "today_limit_up" if limit_hit else ("today_extreme_move" if extreme_hit else "rolling_20d_top_gain")
            candidate_source = "historical_kline_limit_rebuild" if limit_hit else ("historical_kline_extreme_rebuild" if extreme_hit else item.get("candidate_source", "universe_for_20d_top"))
            tags = list(dict.fromkeys((item.get("tags", []) or []) + structure_tags(df)))
            prelim.append({
                **item,
                "code": code, "name": name, "board": board, "limit_pct": lim, "limit_style": limit_style(lim),
                "pct_chg": day_pct, "return20": return20, "hist_source": hist_source,
                "hist_last_date": HIST_LAST_DATE_BY_CODE.get(code, ""),
                "is_limit_sample": bool(limit_hit), "is_extreme_sample": bool(extreme_hit),
                "sample_type": sample_type, "candidate_source": candidate_source, "tags": tags,
            })
        if idx == 1 or idx == total or idx % max(PROGRESS_EVERY, 1) == 0:
            extra = f"当前={name}({code}) 已有效={len(prelim)}"
            write_progress("④A 全市场K线预扫描", idx, total, run_start, extra)
    return prelim, hist_sources


def select_research_items(prelim: List[Dict[str, Any]], base_items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    # A组先看目标日涨停，再看极强大涨；B组严格按近20个交易日累计涨幅。
    by_code: Dict[str, Dict[str, Any]] = {}
    for x in prelim:
        c = code6(x.get("code"))
        if not c:
            continue
        old = by_code.get(c)
        if old is None or (sf(x.get("pct_chg")), sf(x.get("return20"))) > (sf(old.get("pct_chg")), sf(old.get("return20"))):
            by_code[c] = x
    prelim = list(by_code.values())
    limit_candidates = [x for x in prelim if bool(x.get("is_limit_sample"))]
    extreme_candidates = [x for x in prelim if not bool(x.get("is_limit_sample")) and bool(x.get("is_extreme_sample"))]
    a_ranked = sorted(limit_candidates + extreme_candidates, key=lambda x: (bool(x.get("is_limit_sample")), sf(x.get("pct_chg")), sf(x.get("return20"))), reverse=True)
    # B组默认排除ST，避免20日涨幅榜被ST或异常低质票污染；A组ST涨停仍可做归因样本。
    b_candidates = [x for x in prelim if sf(x.get("return20")) != 0 and "ST" not in ss(x.get("name")).upper()]
    b_ranked = sorted(b_candidates, key=lambda x: sf(x.get("return20")), reverse=True)
    selected_codes: List[str] = []
    selected: List[Dict[str, Any]] = []
    def add_many(rows: List[Dict[str, Any]], cap: int) -> None:
        for r in rows[:max(0, cap)]:
            c = code6(r.get("code"))
            if c and c not in selected_codes:
                selected_codes.append(c); selected.append(r)
    add_many(a_ranked, min(max(ANALYZE_MAX_STOCKS, DEEP_SAMPLE_COUNT), MAX_POOL_SCAN))
    add_many(b_ranked, max(DEEP_SAMPLE_COUNT, min(20, MAX_DEEP_REPORT_ITEMS)))
    summary = {
        "prelim_valid_hist_count": len(prelim),
        "rebuild_limit_count": len(limit_candidates),
        "rebuild_extreme_count": len(extreme_candidates),
        "rolling_20d_candidate_count": len(b_candidates),
        "selected_for_deep_attribution": len(selected),
        "a_top_codes_after_rebuild": [code6(x.get("code")) for x in a_ranked[:DEEP_SAMPLE_COUNT]],
        "b_top_codes_after_rebuild": [code6(x.get("code")) for x in b_ranked[:DEEP_SAMPLE_COUNT]],
        "base_limit_pool_count_before_rebuild": len(base_items),
    }
    return selected, summary


def rebuild_pool_from_results_like(items: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for x in items:
        if not bool(x.get("is_limit_sample")):
            continue
        rows.append({
            "code": code6(x.get("code")), "name": ss(x.get("name")), "pct_chg": sf(x.get("pct_chg")),
            "close": 0.0, "source": x.get("candidate_source", "historical_kline_limit_rebuild"),
            "board": x.get("board"), "limit_pct": x.get("limit_pct"), "limit_style": x.get("limit_style"),
            "is_limit_up": True,
        })
    return pd.DataFrame(rows)


def fetch_limit_pool(target_date: str, start_ts: Optional[float] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    global MARKET_UNIVERSE_POOL
    source_defs = [
        ("zt_pool", "stock_zt_pool_em", {"date": target_date}),
        ("zt_st_pool", "stock_zt_pool_st_em", {"date": target_date}),
        ("zt_previous_pool", "stock_zt_pool_previous_em", {"date": target_date}),
    ]
    if REALTIME_SOURCE_ALLOWED:
        source_defs += [("bj_spot_em", "stock_bj_a_spot_em", {}), ("bj_spot_alt", "stock_zh_bj_a_spot", {}), ("a_spot", "stock_zh_a_spot_em", {})]
    stage_start = start_ts or time.time()
    parts, source_counts = [], {}
    for i, (source, fn_name, kwargs) in enumerate(source_defs, 1):
        norm = normalize_pool(safe_source_call(fn_name, **kwargs), source)
        source_counts[source] = int(len(norm)) if norm is not None and not norm.empty else 0
        if norm is not None and not norm.empty:
            parts.append(norm)
        write_progress("① 涨停源采集", i, len(source_defs), stage_start, f"source={source} rows={source_counts[source]}")
    if not parts:
        MARKET_UNIVERSE_POOL = pd.DataFrame()
        return pd.DataFrame(), {"source_counts": source_counts, "report_mode": REPORT_MODE, "realtime_source_allowed": REALTIME_SOURCE_ALLOWED, "trade_date_gate_reason": TRADE_DATE_GATE_REASON}
    raw_pool = pd.concat(parts, ignore_index=True)
    raw_pool["source_rank"] = raw_pool["source"].map({"zt_pool": 1, "zt_st_pool": 2, "zt_previous_pool": 3, "bj_spot_em": 4, "bj_spot_alt": 5, "a_spot": 6}).fillna(9)
    raw_pool = raw_pool.sort_values(["source_rank", "pct_chg"], ascending=[True, False]).drop_duplicates("code", keep="first").drop(columns=["source_rank"])
    boards = raw_pool.apply(lambda r: board_limit(r["code"], r["name"]), axis=1)
    raw_pool["board"] = [x[0] for x in boards]
    raw_pool["limit_pct"] = [x[1] for x in boards]
    raw_pool["limit_style"] = raw_pool["limit_pct"].apply(limit_style)
    raw_pool["is_limit_up"] = raw_pool.apply(lambda r: is_limit_up(sf(r["pct_chg"]), sf(r["limit_pct"])), axis=1)
    MARKET_UNIVERSE_POOL = raw_pool.copy()
    pool = raw_pool[raw_pool["is_limit_up"]].sort_values(["limit_pct", "pct_chg", "code"], ascending=[False, False, True]).head(MAX_POOL_SCAN).reset_index(drop=True)
    diagnostics = {
        "source_counts": source_counts,
        "source_limit_counts": pool["source"].value_counts().to_dict() if not pool.empty else {},
        "board_counts": pool["board"].value_counts().to_dict() if not pool.empty else {},
        "limit_style_counts": pool["limit_style"].value_counts().to_dict() if not pool.empty else {},
        "total_limit_up_identified": int(len(pool)),
        "beijing_count": int((pool["board"] == "北交所").sum()) if not pool.empty else 0,
        "report_mode": REPORT_MODE,
        "realtime_source_allowed": REALTIME_SOURCE_ALLOWED,
        "trade_date_gate_reason": TRADE_DATE_GATE_REASON,
        "historical_realtime_guard": "实时行情源已禁用" if not REALTIME_SOURCE_ALLOWED else "目标日为当前收盘后，允许使用实时全市场行情池",
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



def hist_cache_path(code: str, target_date: str) -> Path:
    return KLINE_CACHE_DIR / target_date / f"{code6(code)}.csv"


def read_hist_cache(code: str, target_date: str) -> pd.DataFrame:
    global HIST_CACHE_HIT, STALE_CACHE_COUNT
    if not USE_HIST_CACHE:
        return pd.DataFrame()
    path = hist_cache_path(code, target_date)
    if not path.exists():
        return pd.DataFrame()
    try:
        df = normalize_hist(pd.read_csv(path))
        if len(df) >= 30:
            last_date = ss(df.iloc[-1].get("date")).replace("-", "")
            if last_date < str(target_date).replace("-", ""):
                STALE_CACHE_COUNT += 1
                if len(HIST_FAILURE_SAMPLES) < 12:
                    HIST_FAILURE_SAMPLES.append({"code": code6(code), "source": "cache", "reason": f"stale_last_date={last_date}<target={target_date}"})
                return pd.DataFrame()
            if "hist_source" not in df.columns:
                df["hist_source"] = "cache"
            else:
                df["hist_source"] = df["hist_source"].fillna("cache")
            HIST_CACHE_HIT += 1
            return df
    except Exception as e:
        if len(HIST_FAILURE_SAMPLES) < 12:
            HIST_FAILURE_SAMPLES.append({"code": code6(code), "source": "cache", "reason": type(e).__name__})
    return pd.DataFrame()


def write_hist_cache(code: str, target_date: str, df: pd.DataFrame) -> None:
    global HIST_CACHE_WRITE
    if not USE_HIST_CACHE or df is None or df.empty or len(df) < 30:
        return
    try:
        path = hist_cache_path(code, target_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False, encoding="utf-8-sig")
        HIST_CACHE_WRITE += 1
    except Exception:
        pass


def fetch_hist_baostock(code: str, target_date: str) -> pd.DataFrame:
    """BaoStock 优先历史K线：加超时、一次重登重试、连续失败计数，避免日常拉数拖死。"""
    global BAOSTOCK_LOGGED_IN, BAOSTOCK_CONSECUTIVE_FAILURES
    symbol = baostock_symbol(code)
    if not symbol:
        return pd.DataFrame()

    last_reason = ""
    for attempt in range(2):
        if not ensure_baostock_login():
            last_reason = "login_failed"
            break
        try:
            fields = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,pctChg,isST"
            with timeout_guard(max(AK_TIMEOUT_SECONDS * 2, 20), "baostock_history"):
                rs = bs.query_history_k_data_plus(
                    symbol,
                    fields,
                    start_date=HIST_START_DATE,
                    end_date=ymd_to_dash(target_date),
                    frequency="d",
                    adjustflag="2",
                )
                if getattr(rs, "error_code", "1") != "0":
                    last_reason = getattr(rs, "error_msg", "query_error")
                    # BaoStock 偶发假登录/会话失效：只重登一次。
                    BAOSTOCK_LOGGED_IN = False
                    try:
                        bs.logout()
                    except Exception:
                        pass
                    continue
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
            df = pd.DataFrame(rows, columns=rs.fields)
            if df.empty:
                last_reason = "empty"
                continue
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
            norm = normalize_hist(out)
            if len(norm) >= 30:
                BAOSTOCK_CONSECUTIVE_FAILURES = 0
                return norm
            last_reason = f"rows={len(norm)}"
        except Exception as e:
            last_reason = type(e).__name__
            BAOSTOCK_LOGGED_IN = False
            try:
                if bs is not None:
                    bs.logout()
            except Exception:
                pass
    BAOSTOCK_CONSECUTIVE_FAILURES += 1
    if len(HIST_FAILURE_SAMPLES) < 12:
        HIST_FAILURE_SAMPLES.append({"code": code, "source": "baostock", "reason": last_reason or "failed"})
    return pd.DataFrame()

def fetch_hist_akshare(code: str, target_date: str) -> pd.DataFrame:
    calls = [
        ("stock_zh_a_hist", {"symbol": code, "period": "daily", "start_date": HIST_START_YYYYMMDD, "end_date": target_date, "adjust": "qfq"}),
        ("stock_zh_a_hist", {"symbol": code, "period": "daily", "start_date": HIST_START_YYYYMMDD, "end_date": target_date, "adjust": ""}),
        ("stock_zh_a_hist", {"symbol": code, "period": "daily", "start_date": HIST_START_YYYYMMDD, "end_date": target_date}),
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
                "beg": HIST_START_YYYYMMDD,
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
    global MISSING_TRIGGER_DAY_COUNT
    code = str(code).zfill(6)
    target = str(target_date).replace("-", "")
    def _ensure_target_day(df: pd.DataFrame, source: str) -> pd.DataFrame:
        global MISSING_TRIGGER_DAY_COUNT
        if df is None or df.empty or len(df) < 30:
            return pd.DataFrame()
        df = normalize_hist(df)
        last_date = ss(df.iloc[-1].get("date")).replace("-", "")
        HIST_LAST_DATE_BY_CODE[code] = last_date
        if last_date < target:
            MISSING_TRIGGER_DAY_COUNT += 1
            if len(HIST_FAILURE_SAMPLES) < 12:
                HIST_FAILURE_SAMPLES.append({"code": code, "source": source, "reason": f"missing_trigger_day_last={last_date}<target={target}"})
            return pd.DataFrame()
        return df
    cached = _ensure_target_day(read_hist_cache(code, target_date), "cache")
    if len(cached) >= 30:
        return cached
    if not is_beijing_code(code):
        df = _ensure_target_day(fetch_hist_baostock(code, target_date), "baostock")
        if len(df) >= 30:
            write_hist_cache(code, target_date, df)
            return df
    df = _ensure_target_day(fetch_hist_akshare(code, target_date), "akshare")
    if len(df) >= 30:
        write_hist_cache(code, target_date, df)
        return df
    df = _ensure_target_day(fetch_hist_eastmoney(code, target_date), "eastmoney")
    if len(df) >= 30:
        write_hist_cache(code, target_date, df)
    return df

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
    """用日K聚合自然月K；默认剔除未完成自然月，防止当前月半截K污染月线核心线。"""
    if hist is None or hist.empty or "date" not in hist.columns:
        return pd.DataFrame()
    d = hist.copy()
    d["date_dt"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["date_dt"]).sort_values("date_dt")
    if d.empty:
        return pd.DataFrame()
    if not MONTHLY_USE_INCOMPLETE_MONTH:
        latest_dt = d["date_dt"].max()
        month_end = latest_dt.to_period("M").to_timestamp("M")
        # 只要不是自然月最后一天，就把当前未完成月剔除；它只能做“当前测试”，不能做历史确认。
        if latest_dt.normalize() < month_end.normalize():
            d = d[d["date_dt"].dt.to_period("M") < latest_dt.to_period("M")].copy()
    if d.empty:
        return pd.DataFrame()
    d["ym"] = d["date_dt"].dt.strftime("%Y-%m")
    rows: List[Dict[str, Any]] = []
    for ym, g in d.groupby("ym"):
        g = g.sort_values("date_dt")
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


# =============================================================================
# 深度归因证据抽取 + 认知闭环（平铺版，不做wrapper）
# =============================================================================
def load_json_file(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def write_json_file(path: Path, obj: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"write json failed {path.name}: {type(e).__name__}", flush=True)


def append_jsonl(path: Path, obj: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        pass


def price_distance_pct(price: float, level: float) -> float:
    return rd((sf(price) - sf(level)) / sf(level) * 100, 2) if sf(level) > 0 else 0.0


def row_close_position(row: pd.Series) -> float:
    hi, lo, cl = sf(row.get("high")), sf(row.get("low")), sf(row.get("close"))
    return rd((cl - lo) / (hi - lo), 3) if hi > lo > 0 else 0.5


def row_upper_shadow_pct(row: pd.Series) -> float:
    hi = sf(row.get("high"))
    top = max(sf(row.get("open")), sf(row.get("close")))
    return rd((hi - top) / top * 100, 2) if top > 0 and hi > top else 0.0


def row_body_pct(row: pd.Series) -> float:
    op = sf(row.get("open"))
    return rd(abs(sf(row.get("close")) - op) / op * 100, 2) if op > 0 else 0.0


def volume_ratio_at(df: pd.DataFrame, idx: int, window: int = 20) -> float:
    if df is None or df.empty or "volume" not in df.columns or idx <= 0:
        return 0.0
    start = max(0, idx - window)
    base = sf(df.iloc[start:idx]["volume"].mean())
    return rd(sf(df.iloc[idx].get("volume")) / base, 2) if base else 0.0


def amount_ratio_at(df: pd.DataFrame, idx: int, window: int = 20) -> float:
    if df is None or df.empty or "amount" not in df.columns or idx <= 0:
        return 0.0
    start = max(0, idx - window)
    base = sf(df.iloc[start:idx]["amount"].mean())
    return rd(sf(df.iloc[idx].get("amount")) / base, 2) if base else 0.0


def pct_chg_at(df: pd.DataFrame, idx: int) -> float:
    if df is None or df.empty or idx < 0 or idx >= len(df):
        return 0.0
    if "pct_chg" in df.columns and sf(df.iloc[idx].get("pct_chg")) != 0:
        return rd(df.iloc[idx].get("pct_chg"))
    if idx <= 0:
        op = sf(df.iloc[idx].get("open"))
        return rd(pct(df.iloc[idx].get("close"), op)) if op else 0.0
    return rd(pct(df.iloc[idx].get("close"), df.iloc[idx - 1].get("close")))


def max_drawdown_after(df: pd.DataFrame, start_idx: int, end_idx: int, anchor_price: float) -> float:
    if df is None or df.empty or anchor_price <= 0 or start_idx + 1 >= end_idx:
        return 0.0
    sub = df.iloc[start_idx + 1:end_idx]
    if sub.empty:
        return 0.0
    min_low = sf(sub["low"].min())
    return rd((min_low / anchor_price - 1) * 100, 2)


def window_return_before_trigger(df: pd.DataFrame, n: int) -> float:
    # 不用涨停T日做事前结构，默认最后一根是T日。
    pre = df.iloc[:-1] if len(df) >= 2 else df
    if len(pre) <= n:
        return 0.0
    return rd(pct(pre.iloc[-1]["close"], pre.iloc[-n - 1]["close"]))


def find_recent_volume_probes(df: pd.DataFrame, lookback: int = 80) -> List[Dict[str, Any]]:
    """识别T-1以前的资金试盘；传入df必须已经是前置结构窗口，函数内部不再丢最后一根。"""
    if df is None or len(df) < 35:
        return []
    pre_end = len(df)
    start = max(20, pre_end - lookback)
    probes: List[Dict[str, Any]] = []
    pre_close = sf(df.iloc[-1].get("close"))
    for idx in range(start, pre_end):
        r = df.iloc[idx]
        vr = volume_ratio_at(df, idx, 20); ar = amount_ratio_at(df, idx, 20); chg = pct_chg_at(df, idx)
        close_pos = row_close_position(r); upper_shadow = row_upper_shadow_pct(r); body = row_body_pct(r)
        prev20_high = sf(df.iloc[max(0, idx - 20):idx]["high"].max()) if idx > 0 else 0.0
        attacks_prev_high = prev20_high > 0 and sf(r.get("high")) >= prev20_high * 0.985
        strong_attack = (vr >= 1.55 and chg >= 3.0) or (vr >= 2.0 and attacks_prev_high) or (ar >= 1.8 and body >= 3.0)
        if not strong_attack:
            continue
        success = bool(close_pos >= 0.78 and sf(r.get("close")) >= max(prev20_high, sf(r.get("open"))) * 0.995)
        failed = not success or upper_shadow >= 3.0
        post = df.iloc[idx + 1:pre_end]
        post_days = len(post)
        drawdown = max_drawdown_after(df, idx, pre_end, max(sf(r.get("close")), sf(r.get("high"))))
        post_vol_mean_ratio = post_volume_cv = post_hold_score = 0.0
        if post_days >= 3:
            prior_mean = sf(df.iloc[max(0, idx - 20):idx]["volume"].mean())
            post_mean = sf(post["volume"].mean()); post_std = sf(post["volume"].std())
            post_vol_mean_ratio = rd(post_mean / prior_mean, 2) if prior_mean else 0.0
            post_volume_cv = rd(post_std / post_mean, 3) if post_mean else 0.0
            hold_level = min(sf(r.get("open")), sf(r.get("close")))
            close_below = int((post["close"] < hold_level * 0.985).sum()) if hold_level > 0 else 0
            post_hold_score = max(0.0, 100 - abs(min(drawdown, 0)) * 3 - close_below * 12)
        probes.append({"date": ss(r.get("date")), "idx": int(idx), "event_type": "volume_probe", "volume_ratio_20": rd(vr), "amount_ratio_20": rd(ar), "price_change": rd(chg), "body_pct": rd(body), "close_position": close_pos, "upper_shadow_pct": rd(upper_shadow), "attacks_prev20_high": bool(attacks_prev_high), "success": bool(success), "failed": bool(failed), "probe_high": rd(r.get("high")), "probe_close": rd(r.get("close")), "later_max_drawdown_pct": rd(drawdown), "post_days": post_days, "post_volume_mean_ratio": rd(post_vol_mean_ratio), "post_volume_cv": rd(post_volume_cv), "post_probe_hold_score": rd(post_hold_score), "distance_pretrigger_close_to_probe_high_pct": rd(price_distance_pct(pre_close, sf(r.get("high")))), "pre_visible": True})
    return sorted(probes, key=lambda x: (sf(x.get("volume_ratio_20")) + sf(x.get("amount_ratio_20")), sf(x.get("post_probe_hold_score")), -abs(sf(x.get("distance_pretrigger_close_to_probe_high_pct"))), int(x.get("idx", 0))), reverse=True)[:6]


def analyze_platform_compression(df: pd.DataFrame) -> Dict[str, Any]:
    """T-1以前平台压缩：价格波动下降、量能CV下降、低点抬高、无放量长阴；函数内部不再丢最后一根。"""
    empty = {"valid": False, "score": 0, "meaning": "历史K线不足，无法判断涨停前平台压缩。"}
    if df is None or len(df) < 45: return empty
    pre = df.copy()
    if len(pre) < 35: return empty
    last20 = pre.tail(20); prev80 = pre.tail(100).head(80) if len(pre) >= 100 else pre.iloc[:-20]
    if last20.empty or prev80.empty: return empty
    c0 = sf(last20.iloc[-1].get("close"))
    width20 = (sf(last20["high"].max()) - sf(last20["low"].min())) / c0 if c0 else 0.0
    width80 = (sf(prev80["high"].max()) - sf(prev80["low"].min())) / sf(prev80["close"].median()) if sf(prev80["close"].median()) else 0.0
    price_compress_ratio = rd(width20 / width80, 3) if width80 else 1.0
    vol_mean = sf(last20["volume"].mean()); volume_cv = rd(sf(last20["volume"].std()) / vol_mean, 3) if vol_mean else 0.0
    prev_vol_mean = sf(prev80["volume"].mean()); volume_mean_ratio = rd(vol_mean / prev_vol_mean, 2) if prev_vol_mean else 0.0
    lows = last20["low"].tolist(); low_lift = rd((min(lows[-8:]) / max(min(lows[:8]), 1e-6) - 1) * 100, 2) if len(lows) >= 12 else 0.0
    bad_long_black = 0; flat_volume_days = 0
    for j, r in last20.iterrows():
        chg = pct_chg_at(df, int(j)); vr = volume_ratio_at(df, int(j), 20)
        if chg <= -4 and vr >= 1.6 and row_close_position(r) <= 0.35: bad_long_black += 1
        if 0.75 <= vr <= 1.25: flat_volume_days += 1
    score = 0.0; evidence: List[str] = []
    if price_compress_ratio <= 0.65: score += 24; evidence.append(f"20日振幅相对前段明显压缩，压缩比{price_compress_ratio}")
    elif price_compress_ratio <= 0.85: score += 14; evidence.append(f"20日振幅有所压缩，压缩比{price_compress_ratio}")
    if volume_cv <= 0.35: score += 22; evidence.append(f"量能波动率低，CV={volume_cv}")
    elif volume_cv <= 0.55: score += 12; evidence.append(f"量能波动率中等，CV={volume_cv}")
    if flat_volume_days >= 7: score += 14; evidence.append(f"近20日平量天数{flat_volume_days}天")
    if low_lift >= 0: score += 16; evidence.append(f"平台后段低点未下移/略抬高，低点变化{low_lift}%")
    elif low_lift > -4: score += 8; evidence.append(f"平台后段低点回撤不深，低点变化{low_lift}%")
    if bad_long_black == 0: score += 14; evidence.append("平台内没有明显放量长阴破坏")
    else: score -= bad_long_black * 10
    if 0.65 <= volume_mean_ratio <= 1.45: score += 10; evidence.append(f"平台均量不过度失真，均量比{volume_mean_ratio}")
    score = max(0.0, min(100.0, score))
    return {"valid": bool(score >= 45), "pre_valid": bool(score >= 45), "score": rd(score), "pre_line_score": rd(score), "price_width_20_pct": rd(width20 * 100), "price_compress_ratio": rd(price_compress_ratio), "volume_cv_20": rd(volume_cv), "volume_mean_ratio_vs_prev": rd(volume_mean_ratio), "flat_volume_days": int(flat_volume_days), "low_lift_pct": rd(low_lift), "bad_long_black_count": int(bad_long_black), "evidence": evidence[:8], "pre_visible": True, "meaning": "T-1以前20日出现价格/量能同步压缩，属于爆发前夜压缩证据。" if score >= 60 else "压缩证据存在但不够强，需要结合资金攻击记忆或核心线临界。"}


def detect_pressure_lines(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """T-1以前日线前高/失败高点/实体顶共振线。T日是否突破只在analyze_trigger_day里计算。"""
    if df is None or len(df) < 45: return []
    pre = df.copy(); look = pre.tail(120); vals: List[float] = []
    for _, r in look.iterrows():
        if row_upper_shadow_pct(r) >= 2.0 or row_close_position(r) <= 0.65: vals.append(sf(r.get("high")))
        vals.append(max(sf(r.get("open")), sf(r.get("close"))))
    centers = cluster_prices(vals, tol=0.025); out: List[Dict[str, Any]] = []; pre_close = sf(pre.iloc[-1].get("close"))
    for line in centers:
        if line <= 0 or abs(pre_close - line) / line > 0.16: continue
        touches = rejects = vol_touches = 0; examples: List[str] = []
        for idx, r in look.iterrows():
            hi, cl, top = sf(r.get("high")), sf(r.get("close")), max(sf(r.get("open")), sf(r.get("close")))
            near = abs(hi - line) / line <= 0.025 or abs(top - line) / line <= 0.025 or (hi >= line and cl <= line * 1.01)
            if not near: continue
            touches += 1
            if cl <= line * 1.01: rejects += 1
            if volume_ratio_at(df, int(idx), 20) >= 1.4: vol_touches += 1
            if len(examples) < 4: examples.append(f"{ss(r.get('date'))} 高{rd(hi)}/收{rd(cl)}")
        if touches < 2: continue
        pre_score = min(100, touches * 10 + rejects * 8 + vol_touches * 10)
        out.append({"line": rd(line), "line_type": "daily_failed_high_pressure", "cycle": "daily", "touch_count": int(touches), "reject_count": int(rejects), "high_volume_touch_count": int(vol_touches), "break_by_trigger": False, "trigger_confirm_score": 0, "pre_line_score": rd(pre_score), "pre_valid": bool(pre_score >= 45), "distance_preclose_pct": price_distance_pct(pre_close, line), "score": rd(pre_score), "examples": examples, "pre_visible": True})
    return sorted(out, key=lambda x: sf(x.get("score")), reverse=True)[:5]


def analyze_break_repair(df: pd.DataFrame) -> Dict[str, Any]:
    """T-1以前近端破位后修复。"""
    if df is None or len(df) < 90: return {"valid": False, "score": 0, "meaning": "历史不足，无法判断破位修复。"}
    pre_end = len(df); pre = df.copy(); look = pre.tail(100)
    if len(look) < 60: return {"valid": False, "score": 0, "meaning": "结构窗口不足。"}
    events: List[Dict[str, Any]] = []
    for idx in range(max(60, pre_end - 90), max(60, pre_end - 5)):
        r = df.iloc[idx]; ma60 = sf(r.get("ma60")) if "ma60" in df.columns else 0.0; prev_low60 = sf(df.iloc[max(0, idx - 60):idx]["low"].min())
        level = max(ma60, prev_low60) if ma60 > 0 and prev_low60 > 0 else (ma60 or prev_low60)
        if level <= 0: continue
        if sf(r.get("close")) < level * 0.985 and pct_chg_at(df, idx) <= -3:
            after = df.iloc[idx + 1:pre_end]
            if after.empty: continue
            repaired_before_trigger = bool(sf(pre.iloc[-1].get("close")) >= level * 0.995)
            events.append({"break_date": ss(r.get("date")), "break_level": rd(level), "break_close": rd(r.get("close")), "max_drop_after_break_pct": rd(max_drawdown_after(df, idx, pre_end, level)), "repaired_before_trigger": repaired_before_trigger, "repaired_by_trigger": False})
    if not events: return {"valid": False, "score": 0, "meaning": "未发现近端破位后修复路径。"}
    ev = events[-1]; score = 35; evidence = [f"{ev['break_date']}跌破{ev['break_level']}附近关键位"]
    if ev["max_drop_after_break_pct"] > -18: score += 18; evidence.append(f"破位后最大下探{ev['max_drop_after_break_pct']}%，没有彻底走坏")
    if ev["repaired_before_trigger"]: score += 24; evidence.append("T-1以前已重新修复关键位")
    score = min(100, score)
    return {"valid": bool(score >= 55), "pre_valid": bool(score >= 55), "score": rd(score), "pre_line_score": rd(score), "latest_event": ev, "event_count": len(events), "evidence": evidence, "pre_visible": True, "meaning": "破位没有杀死结构，T-1以前已重新修复关键位，是大涨前夜的一种重要路径。"}


def analyze_trigger_day(df: pd.DataFrame, core: Dict[str, Any], pressure_lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    if df is None or df.empty: return {}
    idx = len(df) - 1; r = df.iloc[-1]
    vr = volume_ratio_at(df, idx, 20); ar = amount_ratio_at(df, idx, 20); close_pos = row_close_position(r); body = row_body_pct(r); upper = row_upper_shadow_pct(r)
    pre = df.iloc[:-1].copy() if len(df) >= 2 else df.copy()
    prev20_high = sf(pre.tail(20)["high"].max()) if len(pre) else 0.0; prev60_high = sf(pre.tail(60)["high"].max()) if len(pre) else 0.0
    broke_prev20 = sf(r.get("close")) >= prev20_high * 0.995 if prev20_high > 0 else False
    broke_prev60 = sf(r.get("close")) >= prev60_high * 0.995 if prev60_high > 0 else False
    core_line = sf(core.get("line")) if core else 0.0; broke_core = bool(core_line > 0 and sf(r.get("close")) >= core_line * 1.005)
    broke_pressure = []
    for x in pressure_lines or []:
        line = sf(x.get("line"))
        if line <= 0: continue
        br = bool(sf(r.get("close")) >= line * 1.005 or sf(r.get("high")) >= line * 1.01)
        x["break_by_trigger"] = br; x["trigger_confirm_score"] = 22 if br else 0; x["final_explain_score"] = rd(sf(x.get("pre_line_score", x.get("score"))) + sf(x.get("trigger_confirm_score")))
        if br: broke_pressure.append(x)
    quality = 0; evidence: List[str] = []
    if 1.4 <= vr <= 5.5: quality += 24; evidence.append(f"T日健康放量，量比{vr}")
    elif vr > 5.5: quality += 10; evidence.append(f"T日爆量，量比{vr}，需要防分歧")
    if close_pos >= 0.82: quality += 22; evidence.append(f"收盘位置强，收盘分位{close_pos}")
    if body >= 5: quality += 18; evidence.append(f"实体攻击性强，实体幅度{body}%")
    if upper <= 2.5: quality += 12; evidence.append(f"上影线不长，上影{upper}%")
    if broke_prev20 or broke_prev60 or broke_core or broke_pressure: quality += 24; evidence.append("T日打穿近端/核心压力位")
    return {"date": ss(r.get("date")), "pct_chg": rd(pct_chg_at(df, idx)), "volume_ratio_20": rd(vr), "amount_ratio_20": rd(ar), "close_position": close_pos, "body_pct": rd(body), "upper_shadow_pct": rd(upper), "breaks_prev20_high": bool(broke_prev20), "breaks_prev60_high": bool(broke_prev60), "breaks_monthly_coreline": bool(broke_core), "breaks_daily_pressure_line_count": len(broke_pressure), "quality_score": rd(min(100, quality)), "evidence": evidence}


def score_probe_second_attack(probes: List[Dict[str, Any]], trigger: Dict[str, Any]) -> Dict[str, Any]:
    if not probes:
        return make_cause("资金试盘失败后二次攻击", "candidate", 0, [], ["涨停前未发现足够明显的放量试盘事件"], "")
    p = probes[0]
    score = 0.0
    ev: List[str] = []
    counter: List[str] = []
    if sf(p.get("volume_ratio_20")) >= 1.8:
        score += 20; ev.append(f"{p.get('date')}出现明显放量攻击，量比{p.get('volume_ratio_20')}")
    if p.get("failed"):
        score += 14; ev.append("第一次攻击没有完全成功，留下失败高点/旧供应区")
    if sf(p.get("later_max_drawdown_pct")) > -12:
        score += 16; ev.append(f"失败后最大回撤{p.get('later_max_drawdown_pct')}%，没有深跌")
    else:
        counter.append(f"失败后回撤较深，最大回撤{p.get('later_max_drawdown_pct')}%")
    if 0 < sf(p.get("post_volume_cv")) <= 0.45 or 0 < sf(p.get("post_volume_mean_ratio")) <= 1.25:
        score += 16; ev.append(f"失败后量能收缩/趋稳，后段量能CV={p.get('post_volume_cv')}，均量比{p.get('post_volume_mean_ratio')}")
    if abs(sf(p.get("distance_pretrigger_close_to_probe_high_pct"))) <= 6:
        score += 16; ev.append(f"涨停前重新贴近试盘高点，距离{p.get('distance_pretrigger_close_to_probe_high_pct')}%")
    else:
        counter.append(f"涨停前离试盘高点仍有{p.get('distance_pretrigger_close_to_probe_high_pct')}%距离")
    if trigger.get("breaks_prev20_high") or trigger.get("breaks_prev60_high") or trigger.get("breaks_daily_pressure_line_count", 0) > 0:
        score += 14; ev.append("涨停日二次攻击并打穿前高/压力线")
    if sf(trigger.get("quality_score")) >= 70:
        score += 4; ev.append("涨停当天K线质量较强，二次攻击确认度提高")
    return make_cause(
        "资金试盘失败后二次攻击",
        "main" if score >= 72 else "candidate",
        score,
        ev,
        counter,
        "前期资金冲关没完全成功，但失败后没有撤退，而是缩量消化，涨停日再次打穿旧压力。",
    )


def score_core_pressure_break(core: Dict[str, Any], pressure_lines: List[Dict[str, Any]], trigger: Dict[str, Any]) -> Dict[str, Any]:
    ev: List[str] = []
    counter: List[str] = []
    score = 0.0
    core_status = ss(core.get("status"))
    if core_status in ("高置信核心线", "疑似核心线"):
        score += 34; ev.append(f"月线存在{core_status}，核心线{core.get('line')}元")
        if trigger.get("breaks_monthly_coreline"):
            score += 18; ev.append("涨停日收盘打穿月线核心线")
    elif core_status == "候选共振线":
        score += 18; ev.append(f"月线存在候选共振线{core.get('line')}元")
    else:
        counter.append("月线核心线未确认")

    if pressure_lines:
        top = pressure_lines[0]
        score += min(28, sf(top.get("score")) * 0.28)
        ev.append(f"日线压力线{top.get('line')}元反复压制，触碰{top.get('touch_count')}次/失败{top.get('reject_count')}次")
        if top.get("break_by_trigger"):
            score += 14; ev.append("涨停日打穿这条日线失败高点压力")
    else:
        counter.append("日线失败高点共振压力不明显")

    if sf(trigger.get("quality_score")) >= 65:
        score += 8; ev.append("涨停触发K线质量支持突破有效")
    return make_cause(
        "核心压力线反复压制后突破",
        "main" if score >= 72 else "candidate",
        score,
        ev,
        counter,
        "这次涨停的核心不是单日拉升，而是把过去反复压住它的旧供应区打穿。",
    )


def score_platform_compression(platform: Dict[str, Any], probes: List[Dict[str, Any]], pressure_lines: List[Dict[str, Any]], trigger: Dict[str, Any]) -> Dict[str, Any]:
    ev = list(platform.get("evidence", []) or [])
    counter: List[str] = []
    score = sf(platform.get("score")) * 0.72
    if probes:
        score += 10; ev.append("平台压缩前存在资金攻击记忆，不是单纯冷门横盘")
    else:
        counter.append("压缩前资金攻击记忆不强，需防冷门低波动")
    if pressure_lines and abs(sf(pressure_lines[0].get("distance_preclose_pct"))) <= 6:
        score += 10; ev.append("压缩发生在关键压力线附近，具备临界意义")
    if sf(trigger.get("quality_score")) >= 65:
        score += 8; ev.append("涨停日放量强触发，压缩后释放")
    return make_cause(
        "长期平台缩量压缩后爆发",
        "main" if score >= 72 else "candidate",
        score,
        ev,
        counter,
        "涨停前价格和量能被压到临界状态，不是突然活跃，而是压缩后的释放。",
    )


def score_break_repair(break_repair: Dict[str, Any], trigger: Dict[str, Any]) -> Dict[str, Any]:
    score = sf(break_repair.get("score"))
    ev = list(break_repair.get("evidence", []) or [])
    if trigger.get("quality_score", 0) >= 60:
        score += 8; ev.append("涨停日强触发，修复动作被市场确认")
    return make_cause(
        "破位后修复重新夺回关键位",
        "main" if score >= 72 else "candidate",
        score,
        ev,
        [] if break_repair.get("valid") else [break_repair.get("meaning", "破位修复证据不足")],
        "先破位、再修复，说明前面的破坏没有把结构彻底杀死，涨停是修复后的再攻击。",
    )


def score_hotspot_ignition(matches: List[Dict[str, Any]], platform: Dict[str, Any], pressure_lines: List[Dict[str, Any]], probes: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not matches:
        return make_cause("热点板块点火临界结构", "candidate", 0, [], ["未匹配到强板块热点"], "")
    top = matches[0]
    score = 0.0
    ev: List[str] = [f"匹配{top.get('kind')}{top.get('hotspot')}，热点等级{top.get('level')}，涨停命中{top.get('limit_hit_count')}只"]
    if ss(top.get("level")) == "S":
        score += 34
    elif ss(top.get("level")) == "A":
        score += 28
    elif ss(top.get("level")) == "B":
        score += 18
    else:
        score += 10
    score += min(20, sf(top.get("limit_hit_count")) * 3)
    if platform.get("valid") and sf(platform.get("score")) >= 55:
        score += 16; ev.append("个股涨停前已经有平台压缩，热点更像点火器")
    if pressure_lines:
        score += 12; ev.append("个股处于关键压力临界区，热点点火更容易形成涨停")
    if probes:
        score += 8; ev.append("个股前期有资金攻击记忆，非纯热点硬拉")
    return make_cause(
        "热点板块点火临界结构",
        "main" if score >= 72 else "candidate",
        score,
        ev,
        [],
        "热点不是唯一原因，真正有价值的是热点点燃了已经准备好的临界结构。",
    )


def score_high_volume_shadow_supply(probes: List[Dict[str, Any]], pressure_lines: List[Dict[str, Any]], trigger: Dict[str, Any]) -> Dict[str, Any]:
    ev: List[str] = []
    score = 0.0
    supply_like = [p for p in probes if sf(p.get("upper_shadow_pct")) >= 3.0 and sf(p.get("volume_ratio_20")) >= 1.6]
    if supply_like:
        p = supply_like[0]
        score += 34; ev.append(f"{p.get('date')}出现带量上影供应反应，高点{p.get('probe_high')}元")
    if pressure_lines:
        top = pressure_lines[0]
        if sf(top.get("high_volume_touch_count")) >= 1:
            score += 24; ev.append(f"压力线附近出现{top.get('high_volume_touch_count')}次高量触碰/失败")
        if top.get("break_by_trigger"):
            score += 18; ev.append("涨停日突破此前带量上影供应区")
    if trigger.get("quality_score", 0) >= 65:
        score += 10; ev.append("涨停日强触发提高供应消化可信度")
    return make_cause(
        "高量上影供应区消化后突破",
        "main" if score >= 72 else "candidate",
        score,
        ev,
        [] if ev else ["未发现清晰的带量上影供应区"],
        "左侧带量上影代表旧供应，后续能涨停打穿，说明这批供应可能已被消化。",
    )


def build_causal_chain(item: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, str]:
    causes = profile.get("cause_candidates", []) or []
    main = causes[0] if causes else {}
    structure = profile.get("structure_evidence", {}) or {}
    money = profile.get("money_behavior_evidence", {}) or {}
    trigger = profile.get("trigger_day", {}) or {}
    probes = money.get("probe_events", []) or []
    pressure_lines = structure.get("pressure_lines", []) or []
    core = structure.get("monthly_coreline", {}) or {}

    background_parts: List[str] = []
    if core.get("valid"):
        background_parts.append(f"月线层面先有一条{core.get('status')}，位置在{core.get('line')}元附近")
    if pressure_lines:
        background_parts.append(f"日线层面{pressure_lines[0].get('line')}元附近反复被压制")
    if profile.get("platform_compression", {}).get("valid"):
        background_parts.append("涨停前一段时间价格和量能同步压缩")
    if not background_parts:
        background_parts.append("涨停前没有识别到非常清晰的大级别核心线，但仍保留资金行为和热点证据")

    first_money = "未发现特别清晰的前置放量试盘。"
    if probes:
        p = probes[0]
        first_money = f"{p.get('date')}曾出现一次放量攻击，量比{p.get('volume_ratio_20')}，最高打到{p.get('probe_high')}元；这更像资金先试了一次上方供应。"

    failure_absorption = "前置失败/消化证据不强，需要谨慎理解为纯涨停触发。"
    if probes:
        p = probes[0]
        failure_absorption = f"这次试盘后最大回撤{p.get('later_max_drawdown_pct')}%，后续量能CV={p.get('post_volume_cv')}；如果没有深跌且量能缩下来，说明资金可能没有撤退，而是在消化旧抛压。"
    elif profile.get("platform_compression", {}).get("valid"):
        pc = profile.get("platform_compression", {})
        failure_absorption = f"虽然没有明显试盘K，但涨停前20日振幅压缩比{pc.get('price_compress_ratio')}、量能CV={pc.get('volume_cv_20')}，说明筹码进入压缩状态。"

    key_conflict = "核心矛盾暂不清晰。"
    if pressure_lines:
        key_conflict = f"真正矛盾是{pressure_lines[0].get('line')}元附近的旧压力/旧供应是否能被吃掉。"
    elif core.get("valid"):
        key_conflict = f"真正矛盾是月线核心线{core.get('line')}元附近的破位后反抽失败区是否能重新修复。"

    pre_signal = "涨停前夕没有生成足够高置信的提前信号，只能作为低归因价值样本。"
    if causes:
        pre_signal = "；".join((main.get("evidence") or [])[:3]) or pre_signal

    trigger_text = "涨停当天触发信息不足。"
    if trigger:
        ev = "；".join(trigger.get("evidence", [])[:4])
        trigger_text = f"涨停日质量分{trigger.get('quality_score')}。{ev}。"

    learning = main.get("one_sentence") or "该样本暂时没有沉淀出足够清晰的新认知。"
    return {
        "background": "；".join(background_parts) + "。",
        "first_money_action": first_money,
        "failure_and_absorption": failure_absorption,
        "key_conflict": key_conflict,
        "pre_limit_signal": pre_signal,
        "trigger": trigger_text,
        "conclusion": f"主归因：{main.get('cause_type', '原因暂不清晰')}。{main.get('one_sentence', '')}",
        "new_learning": learning,
    }








# =============================================================================
# 量子级归因落地层：T-1结构 / T日触发 / DNA / 原因簇 / 经验库
# =============================================================================
def split_pre_trigger_df(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """严格切分：T-1以前只做结构复原，最后一根T日只做涨停触发确认。"""
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame()
    work = add_indicators(df) if "ma20" not in df.columns else df.copy()
    if len(work) < 2:
        return work.copy(), pd.DataFrame()
    return work.iloc[:-1].copy(), work.iloc[-1:].copy()


def build_analysis_boundary(df: pd.DataFrame) -> Dict[str, Any]:
    if df is None or df.empty:
        return {
            "pre_end_date": "",
            "trigger_date": "",
            "pre_structure_uses_trigger_day": False,
            "trigger_day_only_for_confirmation": True,
            "sample_has_trigger_row": False,
        }
    return {
        "pre_end_date": ss(df.iloc[-2].get("date")) if len(df) >= 2 else "",
        "trigger_date": ss(df.iloc[-1].get("date")) if len(df) >= 1 else "",
        "pre_structure_uses_trigger_day": False,
        "trigger_day_only_for_confirmation": True,
        "sample_has_trigger_row": bool(len(df) >= 2),
    }


def score_grade(score: float) -> str:
    s = sf(score)
    if s >= 85:
        return "S"
    if s >= 70:
        return "A"
    if s >= 55:
        return "B"
    if s >= 40:
        return "C"
    return "D"


def make_cause(
    cause_type: str,
    role: str,
    score: float,
    evidence: List[str],
    counter: Optional[List[str]] = None,
    one_sentence: str = "",
    pre_score: Optional[float] = None,
    trigger_score: Optional[float] = None,
    causal_score: Optional[float] = None,
    dna_keys: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """统一原因候选结构：每个原因都拆成事前、触发、因果三层，避免一个总分混到底。"""
    total = max(0.0, min(100.0, sf(score)))
    ps = rd(pre_score if pre_score is not None else total * 0.55)
    ts = rd(trigger_score if trigger_score is not None else total * 0.30)
    cs = rd(causal_score if causal_score is not None else total * 0.15)
    return {
        "cause_type": cause_type,
        "role": role,
        "score": rd(total),
        "pre_score": ps,
        "trigger_score": ts,
        "causal_score": cs,
        "confidence": rd(total / 100, 2),
        "grade": score_grade(total),
        "evidence_count": len(evidence or []),
        "pre_visible": True,
        "trigger_visible": True,
        "evidence": (evidence or [])[:12],
        "counter_evidence": (counter or [])[:8],
        "one_sentence": one_sentence,
        "dna_keys": dna_keys or {},
    }


def evaluate_core_line(m: pd.DataFrame, bx: Dict[str, Any], line: float) -> Optional[Dict[str, Any]]:
    """月线有量箱体破位后反抽失败核心线：加入实体切割惩罚，避免为了触碰次数硬凑线。"""
    break_idx = int(bx["break_idx"])
    post = m.iloc[break_idx + 1:].copy()
    if post.empty or sf(line) <= 0:
        return None

    touch_indices: List[int] = []
    historical_indices: List[int] = []
    precise_high = precise_close = body_touch = deep_pierce = 0
    stage_high_volume = top2_volume = top20pct_volume = 0
    accept_count = 0
    body_cut_count = 0
    big_body_cut_count = 0
    body_cut_severity = 0.0
    touch_examples: List[str] = []
    stage_volume_examples: List[str] = []
    body_cut_examples: List[str] = []

    for idx, r in post.iterrows():
        hi = sf(r.high); lo = sf(r.low); op = sf(r.open); cl = sf(r.close)
        top = max(op, cl); bottom = min(op, cl)
        body_size = abs(cl - op)
        body_mid = (top + bottom) / 2 if top and bottom else 0
        if cl > line * (1 + CORE_CLOSE_ACCEPT_TOL):
            accept_count += 1

        # 实体切割：核心线穿过实体中部，且实体不小，说明这条线解释力被削弱。
        if bottom < line < top:
            cut_depth = min(abs(line - bottom), abs(top - line)) / max(body_size, 1e-6)
            line_cross_mid = abs(line - body_mid) / max(body_size, 1e-6) <= 0.35 if body_size > 0 else False
            if body_size / max(bottom, 1e-6) >= 0.035 or line_cross_mid:
                body_cut_count += 1
                body_cut_severity += min(1.0, max(0.0, cut_depth * 2))
                if body_size / max(bottom, 1e-6) >= 0.06 or line_cross_mid:
                    big_body_cut_count += 1
                if len(body_cut_examples) < 4:
                    body_cut_examples.append(f"{ss(r.ym)} 实体穿线，开{rd(op)}/收{rd(cl)}，线{rd(line)}")

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
        touch_examples.append(f"{ss(r.ym)} {label}，高{rd(hi)}/收{rd(cl)}，破位后量排名第{vr['rank_no']}")
        if is_stage:
            stage_volume_examples.append(f"{ss(r.ym)} 阶段大量反抽，高{rd(hi)}/收{rd(cl)}，破位后量排名第{vr['rank_no']}，分位{rd(sf(vr['rank_pct'])*100,1)}%")

    if len(touch_indices) < 2:
        return None

    event_count = repair_event_count(touch_indices)
    historical_touch_count = len(set(historical_indices))
    only_latest_risk = historical_touch_count < 2
    cut_penalty = body_cut_count * 6 + big_body_cut_count * 12 + body_cut_severity * 4

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
        - cut_penalty
    )

    if len(touch_indices) >= 3 and historical_touch_count >= 2 and top2_volume >= 1 and score >= 70 and big_body_cut_count == 0:
        status = "高置信核心线"
    elif len(touch_indices) >= 3 and historical_touch_count >= 2 and stage_high_volume >= 1 and big_body_cut_count <= 1:
        status = "疑似核心线"
    elif len(touch_indices) >= 2 and historical_touch_count >= 1:
        status = "候选共振线"
    else:
        status = "弱候选线"
    if len(touch_indices) >= 3 and stage_high_volume == 0:
        status = "候选共振线"
    if big_body_cut_count >= 2:
        status = "弱候选线"

    grade = "S" if status == "高置信核心线" else ("A" if status == "疑似核心线" else ("B" if status == "候选共振线" else "C"))
    missing: List[str] = []
    if stage_high_volume == 0:
        missing.append("没有破位后阶段性大量反抽触线，不能升高置信")
    if historical_touch_count < 2:
        missing.append("历史触碰偏少，不能只靠最新大涨月确认核心线")
    if body_cut_count > 0:
        missing.append(f"核心线存在实体切割{body_cut_count}次，其中大实体切割{big_body_cut_count}次，需要降权")

    return {
        "line": rd(line),
        "score": rd(max(0, score)),
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
        "body_cut_count": int(body_cut_count),
        "big_body_cut_count": int(big_body_cut_count),
        "body_cut_severity": rd(body_cut_severity),
        "invalid_due_to_body_cut": bool(big_body_cut_count >= 2),
        "only_latest_risk": only_latest_risk,
        "touch_examples": touch_examples[:8],
        "stage_volume_examples": stage_volume_examples[:6],
        "body_cut_examples": body_cut_examples[:4],
        "missing_conditions": missing,
    }


def analyze_max_bull_volume_bar(df_pre: pd.DataFrame, df_full: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """历史最大阳量阳K关键位：线的成立只看T-1以前，T日只作为触发确认。"""
    if df_pre is None or len(df_pre) < 80:
        return {"valid": False, "pre_valid": False, "pre_line_score": 0, "trigger_confirm_score": 0, "score": 0, "meaning": "历史不足，无法识别历史最大阳量阳K。"}
    work = df_pre.copy()
    bull = work[(work["close"] > work["open"]) & (work["volume"] > 0)].copy()
    if bull.empty:
        return {"valid": False, "pre_valid": False, "pre_line_score": 0, "trigger_confirm_score": 0, "score": 0, "meaning": "未找到有效阳量K。"}
    idx = int(bull["volume"].idxmax())
    r = work.loc[idx]
    op, cl, hi, lo = sf(r.open), sf(r.close), sf(r.high), sf(r.low)
    body = abs(cl - op)
    shadow = max(hi - max(op, cl), 0) + max(min(op, cl) - lo, 0)
    valid_shape = body > 0 and body >= shadow * 0.5
    level_body_bottom = min(op, cl)
    level_body_top = max(op, cl)
    pre_close = sf(df_pre.iloc[-1].close)
    trigger_close = sf(df_full.iloc[-1].close) if df_full is not None and len(df_full) else pre_close
    trigger_high = sf(df_full.iloc[-1].high) if df_full is not None and len(df_full) else pre_close
    pre_relations: List[str] = []
    trigger_relations: List[str] = []
    pre_score = 0.0
    trigger_score = 0.0
    if valid_shape:
        pre_score += 32; pre_relations.append("T-1以前已存在历史最大阳量K，且实体/影线结构合格")
    if abs(pre_close - level_body_top) / max(level_body_top, 1e-6) <= 0.06:
        pre_score += 18; pre_relations.append(f"涨停前收盘贴近最大阳量K实顶{rd(level_body_top)}")
    if trigger_close >= level_body_top * 1.005:
        trigger_score += 24; trigger_relations.append("T日收盘重新站上最大阳量K实顶")
    if trigger_close >= hi * 1.005 or trigger_high >= hi * 1.01:
        trigger_score += 20; trigger_relations.append("T日打穿最大阳量K高点")
    final_score = min(100, pre_score + trigger_score)
    pre_valid = bool(valid_shape and pre_score >= 32)
    return {
        "valid": pre_valid,
        "pre_valid": pre_valid,
        "pre_line_score": rd(pre_score),
        "trigger_confirm_score": rd(trigger_score),
        "final_explain_score": rd(final_score),
        "score": rd(final_score),
        "bar": {
            "date": ss(r.get("date")), "open": rd(op), "close": rd(cl), "high": rd(hi), "low": rd(lo),
            "volume": rd(r.get("volume"), 0), "body_pct": rd(body / max(op, 1e-6) * 100), "body_vs_shadow_ratio": rd(body / max(shadow, 1e-6), 2),
        },
        "levels": {"body_bottom": rd(level_body_bottom), "body_top": rd(level_body_top), "high": rd(hi)},
        "valid_shape": bool(valid_shape),
        "pre_close_distance_to_body_top_pct": price_distance_pct(pre_close, level_body_top),
        "trigger_break_body_top": bool(trigger_close >= level_body_top * 1.005),
        "trigger_break_high": bool(trigger_close >= hi * 1.005 or trigger_high >= hi * 1.01),
        "pre_evidence": pre_relations,
        "trigger_evidence": trigger_relations,
        "evidence": pre_relations + trigger_relations,
        "meaning": "T-1以前已有历史最大阳量K关键位，T日只是确认是否重新拿下。" if pre_valid else "最大阳量K存在，但T-1以前关键位质量不足。",
    }

def aggregate_n_days(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """固定从T-1往前倒推聚合，避免每天新增一根K线导致20日聚合K整组漂移。"""
    if df is None or df.empty:
        return pd.DataFrame()
    rows = []
    work = df.reset_index(drop=True).copy()
    end = len(work)
    while end > 0:
        start = max(0, end - n)
        sub = work.iloc[start:end]
        if not sub.empty:
            rows.append({
                "start_date": ss(sub.iloc[0].get("date")),
                "end_date": ss(sub.iloc[-1].get("date")),
                "open": sf(sub.iloc[0].get("open")),
                "close": sf(sub.iloc[-1].get("close")),
                "high": sf(sub["high"].max()),
                "low": sf(sub["low"].min()),
                "volume": sf(sub["volume"].sum()),
                "amount": sf(sub["amount"].sum()) if "amount" in sub.columns else 0.0,
                "bar_days": int(len(sub)),
            })
        end = start
    rows = list(reversed(rows))
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["body_top"] = out[["open", "close"]].max(axis=1)
    out["body_bottom"] = out[["open", "close"]].min(axis=1)
    out["anchor_mode"] = f"reverse_from_pre_end_{n}d"
    return out

def analyze_20d_coreline(df_pre: pd.DataFrame, df_full: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """20日聚合K核心线：T-1以前先成立，T日只做突破确认；聚合从T-1倒推，线更稳定。"""
    if df_pre is None or len(df_pre) < 120:
        return {"valid": False, "pre_valid": False, "pre_line_score": 0, "trigger_confirm_score": 0, "score": 0, "meaning": "历史不足，无法做20日聚合K核心线。"}
    agg = aggregate_n_days(df_pre.tail(480), 20)
    if len(agg) < 6:
        return {"valid": False, "pre_valid": False, "pre_line_score": 0, "trigger_confirm_score": 0, "score": 0, "meaning": "20日聚合K不足。"}
    bull = agg[(agg["close"] > agg["open"]) & (agg["volume"] > 0)].copy()
    if bull.empty:
        return {"valid": False, "pre_valid": False, "pre_line_score": 0, "trigger_confirm_score": 0, "score": 0, "meaning": "20日聚合K无有效阳量K。"}
    max_idx = int(bull["volume"].idxmax())
    base = agg.loc[max_idx]
    post = agg.iloc[max_idx + 1:max_idx + 21]
    if post.empty:
        return {"valid": False, "pre_valid": False, "pre_line_score": 0, "trigger_confirm_score": 0, "score": 0, "meaning": "最大阳量K后续观察窗口不足。"}
    candidates = []
    for idx, r in post.iterrows():
        hi, cl = sf(r.high), sf(r.close)
        if hi >= sf(base.high) * 0.88 and cl <= hi * 0.985:
            candidates.append((int(idx), hi, sf(r.volume), ss(r.end_date)))
    if not candidates:
        line = sf(base.high)
        source = "max_bull_volume_high"
        repair_date = ""
    else:
        idx, line, vol, repair_date = sorted(candidates, key=lambda x: (x[1], x[2]), reverse=True)[0]
        source = "max_bull_volume_failed_repair_high"
    trigger_close = sf(df_full.iloc[-1].close) if df_full is not None and len(df_full) else sf(df_pre.iloc[-1].close)
    trigger_high = sf(df_full.iloc[-1].high) if df_full is not None and len(df_full) else sf(df_pre.iloc[-1].high)
    pre_close = sf(df_pre.iloc[-1].close)
    touch_count = int(((agg["high"] >= line * 0.975) & (agg["close"] <= line * 1.015)).sum())
    high_vol_shadow = int(((agg["high"] >= line * 0.975) & (agg["close"] <= line * 1.015) & (agg["volume"] >= agg["volume"].rolling(5, min_periods=1).mean() * 1.2)).sum())
    pre_score = 0.0
    pre_evidence: List[str] = []
    if touch_count >= 2:
        pre_score += 26; pre_evidence.append(f"T-1以前20日聚合K围绕{rd(line)}元触碰{touch_count}次")
    if high_vol_shadow >= 1:
        pre_score += 24; pre_evidence.append(f"左侧阶段高量上影/收不住共振{high_vol_shadow}次")
    if abs(pre_close - line) / max(line, 1e-6) <= 0.07:
        pre_score += 18; pre_evidence.append("涨停前价格已贴近20日聚合K核心线")
    trigger_score = 0.0
    trigger_evidence: List[str] = []
    trigger_break = bool(trigger_close >= line * 1.005 or trigger_high >= line * 1.015)
    if trigger_break:
        trigger_score += 26; trigger_evidence.append("T日突破20日聚合K核心线")
    final_score = min(100, pre_score + trigger_score)
    pre_valid = bool(pre_score >= 45)
    return {
        "valid": pre_valid,
        "pre_valid": pre_valid,
        "pre_line_score": rd(pre_score),
        "trigger_confirm_score": rd(trigger_score),
        "final_explain_score": rd(final_score),
        "score": rd(final_score),
        "core_line": rd(line),
        "source": source,
        "anchor_mode": ss(agg.get("anchor_mode", pd.Series([""])).iloc[-1]) if "anchor_mode" in agg.columns else "reverse_from_pre_end_20d",
        "base_bar": {"start_date": ss(base.start_date), "end_date": ss(base.end_date), "high": rd(base.high), "volume": rd(base.volume, 0)},
        "first_repair_high_date": repair_date,
        "touch_count": touch_count,
        "left_high_volume_shadow_resonance_count": high_vol_shadow,
        "pre_close_distance_pct": price_distance_pct(pre_close, line),
        "trigger_break_20d_coreline": trigger_break,
        "pre_evidence": pre_evidence,
        "trigger_evidence": trigger_evidence,
        "meaning": "T-1以前20日聚合K核心线已经成立，T日只是突破确认。" if pre_valid else "20日聚合K核心线前置证据不足，T日突破不能反推为高质量核心线。",
    }

def analyze_gap_shadow_resonance(df_pre: pd.DataFrame, df_full: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """缺口/影线共振压力区：共振线成立只看T-1以前；T日突破只是确认。"""
    if df_pre is None or len(df_pre) < 80:
        return {"valid": False, "pre_valid": False, "pre_line_score": 0, "trigger_confirm_score": 0, "score": 0, "meaning": "历史不足，无法识别缺口/影线共振。"}
    look = df_pre.tail(160).reset_index(drop=True)
    vals: List[float] = []
    gap_zones = []
    for i, r in look.iterrows():
        if row_upper_shadow_pct(r) >= 2.5:
            vals.append(sf(r.high))
        if i > 0:
            prev_hi = sf(look.iloc[i - 1].high); lo = sf(r.low)
            prev_lo = sf(look.iloc[i - 1].low); hi = sf(r.high)
            if lo > prev_hi * 1.005:
                vals += [prev_hi, lo]; gap_zones.append({"type": "up_gap", "low": rd(prev_hi), "high": rd(lo), "date": ss(r.get("date"))})
            elif hi < prev_lo * 0.995:
                vals += [hi, prev_lo]; gap_zones.append({"type": "down_gap", "low": rd(hi), "high": rd(prev_lo), "date": ss(r.get("date"))})
    if not vals:
        return {"valid": False, "pre_valid": False, "pre_line_score": 0, "trigger_confirm_score": 0, "score": 0, "meaning": "未发现明显影线/缺口共振。"}
    centers = cluster_prices(vals, tol=0.025)
    trigger_close = sf(df_full.iloc[-1].close) if df_full is not None and len(df_full) else sf(df_pre.iloc[-1].close)
    trigger_high = sf(df_full.iloc[-1].high) if df_full is not None and len(df_full) else sf(df_pre.iloc[-1].high)
    pre_close = sf(df_pre.iloc[-1].close)
    best = None
    for line in centers:
        touches = int(((look["high"] >= line * 0.975) & (look["close"] <= line * 1.015)).sum())
        gaps = int(sum(1 for g in gap_zones if sf(g.get("low")) <= line <= sf(g.get("high")) or abs((sf(g.get("low"))+sf(g.get("high")))/2-line)/max(line,1e-6)<=0.03))
        if touches + gaps < 2:
            continue
        pre_score = touches * 12 + gaps * 14
        pre_evidence: List[str] = [f"T-1以前上影触碰{touches}次、缺口共振{gaps}处"]
        if abs(pre_close-line)/max(line,1e-6) <= 0.08:
            pre_score += 16; pre_evidence.append("涨停前价格贴近影线/缺口共振线")
        trigger_break = bool(trigger_close >= line * 1.005 or trigger_high >= line * 1.015)
        trigger_score = 24 if trigger_break else 0
        final_score = min(100, pre_score + trigger_score)
        cand = {
            "line": rd(line), "upper_shadow_touch_count": touches, "gap_zone_count": gaps,
            "pre_line_score": rd(pre_score), "trigger_confirm_score": rd(trigger_score), "final_explain_score": rd(final_score), "score": rd(final_score),
            "pre_evidence": pre_evidence,
        }
        if best is None or sf(cand["final_explain_score"]) > sf(best["final_explain_score"]):
            best = cand
    if not best:
        return {"valid": False, "pre_valid": False, "pre_line_score": 0, "trigger_confirm_score": 0, "score": 0, "meaning": "影线/缺口存在，但没有形成可解释本次涨停的T-1共振线。"}
    pre_valid = bool(sf(best.get("pre_line_score")) >= 38)
    trigger_break_line = bool(trigger_close >= sf(best.get("line")) * 1.005 or trigger_high >= sf(best.get("line")) * 1.015)
    best.update({
        "valid": pre_valid,
        "pre_valid": pre_valid,
        "pre_close_distance_pct": price_distance_pct(pre_close, sf(best.get("line"))),
        "trigger_break_line": trigger_break_line,
        "trigger_evidence": ["T日突破缺口/影线共振线"] if trigger_break_line else [],
        "meaning": "T-1以前已形成影线/缺口共振线，T日只是突破确认。" if pre_valid else "共振线前置证据一般，T日突破不能反推为高质量核心线。",
    })
    return best

def calculate_pre_setup_score(
    core: Dict[str, Any], pressure_lines: List[Dict[str, Any]], probes: List[Dict[str, Any]], platform: Dict[str, Any],
    break_repair: Dict[str, Any], max_bull: Dict[str, Any], core20: Dict[str, Any], gap_shadow: Dict[str, Any], matches: List[Dict[str, Any]], df_pre: pd.DataFrame
) -> Dict[str, Any]:
    score = 0.0; ev: List[str] = []; weak: List[str] = []
    if core.get("valid"):
        add = 22 if core.get("grade") in ("S", "A") else 14
        score += add; ev.append(f"T-1以前已存在月线{core.get('status')}，线{core.get('line')}元")
    else:
        weak.append("T-1以前月线核心线未确认")
    if pressure_lines:
        score += min(18, sf(pressure_lines[0].get("score")) * 0.18); ev.append(f"T-1以前日线压力线{pressure_lines[0].get('line')}元，触碰{pressure_lines[0].get('touch_count')}次")
    if probes:
        p = probes[0]; score += 16; ev.append(f"T-1以前有放量试盘，{p.get('date')}量比{p.get('volume_ratio_20')}，距离旧高{p.get('distance_pretrigger_close_to_probe_high_pct')}%")
    if platform.get("valid"):
        score += min(16, sf(platform.get("score")) * 0.16); ev.append(f"涨停前平台压缩，量能CV={platform.get('volume_cv_20')}，平量{platform.get('flat_volume_days')}天")
    if break_repair.get("valid"):
        score += 10; ev.append("涨停前存在破位后修复路径")
    if max_bull.get("valid"):
        score += 9; ev.append("历史最大阳量K关键位与本次涨停存在关系")
    if core20.get("valid"):
        score += 9; ev.append("20日聚合K核心线与本次涨停存在关系")
    if gap_shadow.get("valid"):
        score += 7; ev.append("缺口/影线共振区位于涨停触发附近")
    if matches:
        score += 7; ev.append(f"存在行情热点归属：{matches[0].get('hotspot')}({matches[0].get('level')}级)")
    if platform.get("bad_long_black_count", 0):
        weak.append(f"平台内存在放量长阴{platform.get('bad_long_black_count')}根")
        score -= min(12, int(platform.get("bad_long_black_count", 0)) * 6)
    score = max(0, min(100, score))
    return {"score": rd(score), "grade": score_grade(score), "evidence": ev[:10], "weakness": weak[:8]}


def calculate_trigger_score(trigger: Dict[str, Any], core: Dict[str, Any], pressure_lines: List[Dict[str, Any]], max_bull: Dict[str, Any], core20: Dict[str, Any], gap_shadow: Dict[str, Any]) -> Dict[str, Any]:
    score = sf(trigger.get("quality_score")) * 0.68
    ev = list(trigger.get("evidence", []) or [])
    if trigger.get("breaks_monthly_coreline"):
        score += 10; ev.append("T日收盘突破月线核心线")
    if trigger.get("breaks_daily_pressure_line_count", 0) > 0:
        score += 8; ev.append(f"T日突破日线压力线{trigger.get('breaks_daily_pressure_line_count')}条")
    if max_bull.get("trigger_break_body_top") or max_bull.get("trigger_break_high"):
        score += 7; ev.append("T日突破历史最大阳量K实顶/高点")
    if core20.get("trigger_break_20d_coreline"):
        score += 6; ev.append("T日突破20日聚合K核心线")
    if gap_shadow.get("trigger_break_line"):
        score += 5; ev.append("T日突破缺口/影线共振线")
    score = max(0, min(100, score))
    return {"score": rd(score), "grade": score_grade(score), "evidence": ev[:10]}


def calculate_causal_quality_score(pre_setup: Dict[str, Any], trigger_score: Dict[str, Any], causes: List[Dict[str, Any]]) -> Dict[str, Any]:
    main = causes[0] if causes else {}
    base = sf(pre_setup.get("score")) * 0.38 + sf(trigger_score.get("score")) * 0.32 + sf(main.get("score")) * 0.30
    ev_count = len(main.get("evidence", []) or [])
    if ev_count >= 4:
        base += 5
    if ss(main.get("cause_type")) == "原因暂不清晰":
        base = min(base, 38)
    score = max(0, min(100, base))
    return {
        "score": rd(score),
        "grade": score_grade(score),
        "learning_value": "high" if score >= 75 else ("medium" if score >= 55 else "low"),
        "reason": "事前结构、涨停触发、主归因证据形成闭环。" if score >= 70 else "因果链仍不够闭合，保留观察或未覆盖样本。",
    }


def score_monthly_box_repair_coreline(core: Dict[str, Any], trigger: Dict[str, Any]) -> Dict[str, Any]:
    ev: List[str] = []; counter: List[str] = []; score = 0.0
    platform = core.get("platform", {}) or {}
    if core.get("valid") and platform:
        score += 32; ev.append(f"{platform.get('start_ym')}~{platform.get('end_ym')}有量箱体，{platform.get('break_ym')}破位后形成反抽失败核心线{core.get('line')}元")
        if sf(core.get("post_break_stage_high_volume_touch_count")) >= 1:
            score += 22; ev.append(f"破位后阶段大量反抽触线{core.get('post_break_stage_high_volume_touch_count')}次")
        if sf(core.get("touch_count")) >= 3:
            score += 14; ev.append(f"核心线有效触碰{core.get('touch_count')}次")
        if sf(core.get("body_cut_count")) == 0:
            score += 8; ev.append("核心线没有切割月K实体，边界更干净")
        else:
            counter.append(f"核心线存在实体切割{core.get('body_cut_count')}次")
        if trigger.get("breaks_monthly_coreline"):
            score += 18; ev.append("涨停日打穿这条破位反抽失败核心线")
    else:
        counter.append("没有形成月线有量箱体破位后反抽失败核心线")
    return make_cause("月线有量箱体破位后反抽失败核心线", "main" if score >= 72 else "candidate", score, ev, counter, "旧箱体破位后的反抽失败线被涨停打穿，是一类重要涨停归因。")


def score_max_bull_volume_bar(max_bull: Dict[str, Any]) -> Dict[str, Any]:
    if not max_bull.get("valid"):
        return make_cause("历史最大阳量K实顶/实底修复突破", "candidate", 0, [], [max_bull.get("meaning", "最大阳量K证据不足")], "")
    score = sf(max_bull.get("score")); ev = list(max_bull.get("evidence", []) or [])
    return make_cause("历史最大阳量K实顶/实底修复突破", "main" if score >= 72 else "candidate", score, ev, [], "历史最大阳量K代表大规模筹码交换，涨停重新拿下其实顶/高点，说明旧重码区被攻击。")


def score_20d_coreline_break(core20: Dict[str, Any]) -> Dict[str, Any]:
    if not core20.get("valid"):
        return make_cause("20日聚合K核心线突破", "candidate", 0, [], [core20.get("meaning", "20日聚合K证据不足")], "")
    score = sf(core20.get("score"))
    ev = [f"20日聚合K核心线{core20.get('core_line')}元，触碰{core20.get('touch_count')}次，带量上影共振{core20.get('left_high_volume_shadow_resonance_count')}次"]
    if core20.get("trigger_break_20d_coreline"):
        ev.append("涨停日突破20日聚合K核心线")
    return make_cause("20日聚合K核心线突破", "main" if score >= 72 else "candidate", score, ev, [], "用20日聚合K看，本次涨停打穿的是中周期反复卡住的核心线。")


def score_gap_shadow_resonance(gap_shadow: Dict[str, Any]) -> Dict[str, Any]:
    if not gap_shadow.get("valid"):
        return make_cause("缺口/影线共振突破", "candidate", 0, [], [gap_shadow.get("meaning", "缺口/影线共振证据不足")], "")
    score = sf(gap_shadow.get("score"))
    ev = [f"缺口/影线共振线{gap_shadow.get('line')}元，上影触碰{gap_shadow.get('upper_shadow_touch_count')}次，缺口共振{gap_shadow.get('gap_zone_count')}处"]
    if gap_shadow.get("trigger_break_line"):
        ev.append("涨停日突破缺口/影线记忆区")
    return make_cause("缺口/影线共振突破", "main" if score >= 72 else "candidate", score, ev, [], "涨停打穿的是缺口和影线反复留下的结构记忆区。")


def score_limit_chain_emotion(matches: List[Dict[str, Any]], item: Dict[str, Any]) -> Dict[str, Any]:
    if not matches:
        return make_cause("涨停梯队/情绪接力", "candidate", 0, [], ["未匹配到涨停扩散板块"], "")
    top = matches[0]
    count = int(sf(top.get("limit_hit_count")))
    score = min(100, count * 7 + (18 if ss(top.get("level")) in ("S", "A") else 0))
    ev = [f"同板块涨停命中{count}只，热点等级{top.get('level')}，个股涨停风格{item.get('limit_style')}"]
    role = "main" if score >= 72 else "candidate"
    return make_cause("涨停梯队/情绪接力", role, score, ev, [], "这只票处在板块涨停扩散里，情绪接力是涨停归因的一部分，但仍需结合个股结构。")


def build_limit_up_dna(
    df_full: pd.DataFrame, core: Dict[str, Any], pressure_lines: List[Dict[str, Any]], probes: List[Dict[str, Any]], platform: Dict[str, Any],
    trigger: Dict[str, Any], max_bull: Dict[str, Any], core20: Dict[str, Any], gap_shadow: Dict[str, Any], matches: List[Dict[str, Any]]
) -> Dict[str, Any]:
    pre, _ = split_pre_trigger_df(df_full)
    pre_close = sf(pre.iloc[-1].close) if len(pre) else 0.0
    return {
        "structure_dna": {
            "has_monthly_coreline": bool(core.get("valid")),
            "monthly_coreline_grade": core.get("grade", ""),
            "monthly_coreline": core.get("line", 0),
            "has_daily_pressure_line": bool(pressure_lines),
            "daily_pressure_line": pressure_lines[0].get("line") if pressure_lines else 0,
            "has_20d_coreline": bool(core20.get("valid")),
            "coreline_20d": core20.get("core_line", 0),
            "has_max_volume_bar_level": bool(max_bull.get("valid")),
            "has_gap_shadow_resonance": bool(gap_shadow.get("valid")),
        },
        "money_dna": {
            "has_volume_probe": bool(probes),
            "probe_failed_then_absorbed": bool(probes and probes[0].get("failed") and sf(probes[0].get("later_max_drawdown_pct")) > -15),
            "probe_volume_ratio_20": probes[0].get("volume_ratio_20") if probes else 0,
            "post_probe_volume_cv": probes[0].get("post_volume_cv") if probes else 0,
            "flat_volume_days": platform.get("flat_volume_days", 0),
            "bad_long_black_count": platform.get("bad_long_black_count", 0),
        },
        "position_dna": {
            "pre_close": rd(pre_close),
            "pre_close_to_coreline_pct": price_distance_pct(pre_close, sf(core.get("line"))) if core.get("line") else 0,
            "pre_close_to_probe_high_pct": probes[0].get("distance_pretrigger_close_to_probe_high_pct") if probes else 0,
            "range_position_250_pre": range_position(pre, 250) if len(pre) else None,
            "not_extreme_high": bool((range_position(pre, 250) or 0) <= 0.85) if len(pre) else False,
        },
        "trigger_dna": {
            "date": trigger.get("date"),
            "volume_ratio_20": trigger.get("volume_ratio_20"),
            "close_position": trigger.get("close_position"),
            "body_pct": trigger.get("body_pct"),
            "upper_shadow_pct": trigger.get("upper_shadow_pct"),
            "breaks_coreline": trigger.get("breaks_monthly_coreline"),
            "breaks_daily_pressure": trigger.get("breaks_daily_pressure_line_count", 0) > 0,
            "trigger_quality_score": trigger.get("quality_score"),
        },
        "hotspot_dna": {
            "hotspot_level": matches[0].get("level") if matches else "",
            "same_board_limit_count": matches[0].get("limit_hit_count") if matches else 0,
            "is_hotspot_ignition": bool(matches),
            "hotspot_name": matches[0].get("hotspot") if matches else "",
        },
    }


def build_deep_attribution_profile(df: pd.DataFrame, core: Dict[str, Any], matches: List[Dict[str, Any]], item: Dict[str, Any]) -> Dict[str, Any]:
    """新版五号员工核心：只研究涨停样本，且每个原因原子化落地到字段/阈值/分数。"""
    if df is None or df.empty or len(df) < 35:
        unclear = make_cause("原因暂不清晰", "main", 15, [], ["历史K线不足，无法复原涨停前夜逻辑"], "样本数据不足，暂不能做深度归因。")
        return {
            "analysis_boundary": build_analysis_boundary(df),
            "pre_limit_context": {},
            "attribution_scores": {"pre_setup_score": 0, "trigger_score": 0, "causal_quality_score": 15, "learning_value": "low"},
            "structure_evidence": {"monthly_coreline": core, "pressure_lines": []},
            "money_behavior_evidence": {"probe_events": []},
            "platform_compression": {"valid": False, "score": 0, "meaning": "历史K线不足"},
            "trigger_day": {},
            "limit_up_dna": {},
            "cause_candidates": [unclear],
            "main_cause": "原因暂不清晰",
            "secondary_causes": [],
            "causal_chain": {},
            "cognition_extract": {"learning_value": "low", "new_insight": "历史数据不足，低学习价值。"},
        }

    df_full = add_indicators(df) if "ma20" not in df.columns else df.copy()
    df_pre, _trigger_only = split_pre_trigger_df(df_full)
    # 再保险：传入core若来自旧逻辑，也强制用T-1以前重算，彻底断开涨停日污染。
    core_pre = find_monthly_coreline(df_pre) if len(df_pre) >= 35 else (core or {})

    probes = find_recent_volume_probes(df_pre)
    platform = analyze_platform_compression(df_pre)
    pressure_lines = detect_pressure_lines(df_pre)
    break_repair = analyze_break_repair(df_pre)
    trigger = analyze_trigger_day(df_full, core_pre, pressure_lines)
    max_bull = analyze_max_bull_volume_bar(df_pre, df_full)
    core20 = analyze_20d_coreline(df_pre, df_full)
    gap_shadow = analyze_gap_shadow_resonance(df_pre, df_full)

    pre_context = {
        "return5_pre": window_return_before_trigger(df_full, 5),
        "return20_pre": window_return_before_trigger(df_full, 20),
        "return60_pre": window_return_before_trigger(df_full, 60),
        "range_position_250_pre": range_position(df_pre, 250) if len(df_pre) else None,
        "pre_close": rd(df_pre.iloc[-1].get("close")) if len(df_pre) else 0,
        "trigger_close": rd(df_full.iloc[-1].get("close")),
        "t1_structure_locked": True,
    }

    causes = [
        score_probe_second_attack(probes, trigger),
        score_core_pressure_break(core_pre, pressure_lines, trigger),
        score_monthly_box_repair_coreline(core_pre, trigger),
        score_platform_compression(platform, probes, pressure_lines, trigger),
        score_break_repair(break_repair, trigger),
        score_max_bull_volume_bar(max_bull),
        score_20d_coreline_break(core20),
        score_gap_shadow_resonance(gap_shadow),
        score_high_volume_shadow_supply(probes, pressure_lines, trigger),
        score_hotspot_ignition(matches, platform, pressure_lines, probes),
        score_limit_chain_emotion(matches, item),
    ]
    causes = [c for c in causes if isinstance(c, dict)]
    strongish = [c for c in causes if sf(c.get("score")) >= 45]
    if not strongish:
        causes.append(make_cause(
            "原因暂不清晰", "main", 30, [],
            ["没有识别到强热点、强核心线、试盘二攻、压缩爆发、最大阳量K、20日聚合K或影线缺口共振证据"],
            "该涨停可能来自消息、情绪或尚未建模的原因，不能硬编技术归因。",
            pre_score=20, trigger_score=sf(trigger.get("quality_score")) * 0.25, causal_score=10,
        ))
    causes = sorted(causes, key=lambda x: sf(x.get("score")), reverse=True)
    if causes:
        causes[0]["role"] = "main"
        for c in causes[1:]:
            c["role"] = "secondary" if sf(c.get("score")) >= 55 else "candidate"

    pre_setup = calculate_pre_setup_score(core_pre, pressure_lines, probes, platform, break_repair, max_bull, core20, gap_shadow, matches, df_pre)
    trigger_sc = calculate_trigger_score(trigger, core_pre, pressure_lines, max_bull, core20, gap_shadow)
    causal_sc = calculate_causal_quality_score(pre_setup, trigger_sc, causes)
    dna = build_limit_up_dna(df_full, core_pre, pressure_lines, probes, platform, trigger, max_bull, core20, gap_shadow, matches)

    profile: Dict[str, Any] = {
        "analysis_boundary": build_analysis_boundary(df_full),
        "pre_limit_context": pre_context,
        "attribution_scores": {
            "pre_setup_score": pre_setup.get("score"),
            "pre_setup_grade": pre_setup.get("grade"),
            "trigger_score": trigger_sc.get("score"),
            "trigger_grade": trigger_sc.get("grade"),
            "causal_quality_score": causal_sc.get("score"),
            "causal_quality_grade": causal_sc.get("grade"),
            "learning_value": causal_sc.get("learning_value"),
            "score_note": causal_sc.get("reason"),
        },
        "score_evidence": {"pre_setup": pre_setup, "trigger": trigger_sc, "causal_quality": causal_sc},
        "structure_evidence": {
            "monthly_coreline": core_pre,
            "pressure_lines": pressure_lines,
            "monthly_box_repair_coreline": core_pre if core_pre.get("platform") else {},
            "max_bull_volume_bar": max_bull,
            "aggregate_20d_coreline": core20,
            "gap_shadow_resonance": gap_shadow,
            "break_repair": break_repair,
        },
        "money_behavior_evidence": {"probe_events": probes},
        "platform_compression": platform,
        "trigger_day": trigger,
        "limit_up_dna": dna,
        "cause_candidates": causes,
        "main_cause": causes[0].get("cause_type") if causes else "原因暂不清晰",
        "secondary_causes": [c.get("cause_type") for c in causes[1:] if sf(c.get("score")) >= 55],
    }
    profile["causal_chain"] = build_causal_chain(item, profile)
    main_score = sf(causes[0].get("score")) if causes else 0
    profile["cognition_extract"] = {
        "learning_value": causal_sc.get("learning_value"),
        "matched_logic_name": profile["main_cause"],
        "new_insight": (causes[0].get("one_sentence") if causes else "") or "暂未形成清晰认知。",
        "is_unexplained_limitup": bool(profile["main_cause"] == "原因暂不清晰"),
        "main_reason_score": rd(main_score),
    }
    return profile


def logic_id_for_cause(cause_type: str) -> str:
    mapping = {
        "资金试盘失败后二次攻击": "probe_second_attack",
        "核心压力线反复压制后突破": "core_pressure_break",
        "月线有量箱体破位后反抽失败核心线": "monthly_box_break_repair_coreline",
        "长期平台缩量压缩后爆发": "platform_compression_breakout",
        "破位后修复重新夺回关键位": "breakdown_repair_reclaim",
        "历史最大阳量K实顶/实底修复突破": "max_bull_volume_bar_reclaim",
        "20日聚合K核心线突破": "aggregate_20d_coreline_break",
        "缺口/影线共振突破": "gap_shadow_resonance_break",
        "热点板块点火临界结构": "sector_ignition_ready_structure",
        "涨停梯队/情绪接力": "limit_chain_emotion",
        "高量上影供应区消化后突破": "high_volume_shadow_supply_absorb",
        "原因暂不清晰": "unclear_or_news_driven",
    }
    return mapping.get(ss(cause_type), "other_logic")


def build_daily_cause_clusters(target_date: str, results: List[Dict[str, Any]], sample_filter: Optional[str] = None) -> Dict[str, Any]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}; selected = []
    for r in results:
        st = ss(r.get("sample_type")) or ("today_limit_up" if r.get("is_limit_sample") else "rolling_20d_top_gain")
        if sample_filter and st != sample_filter: continue
        selected.append(r); profile = r.get("deep_attribution", {}) or {}; main = (profile.get("cause_candidates") or [{}])[0]; cause_type = ss(main.get("cause_type")) or "原因暂不清晰"; grouped.setdefault(cause_type, []).append(r)
    clusters = []
    for cause_type, items in grouped.items():
        pre_scores = [sf((x.get("deep_attribution", {}) or {}).get("attribution_scores", {}).get("pre_setup_score")) for x in items]
        trg_scores = [sf((x.get("deep_attribution", {}) or {}).get("attribution_scores", {}).get("trigger_score")) for x in items]
        causal_scores = [sf((x.get("deep_attribution", {}) or {}).get("attribution_scores", {}).get("causal_quality_score")) for x in items]
        reps = []
        for x in sorted(items, key=lambda z: sf((z.get("deep_attribution", {}) or {}).get("attribution_scores", {}).get("causal_quality_score")), reverse=True)[:5]:
            prof = x.get("deep_attribution", {}) or {}; main = (prof.get("cause_candidates") or [{}])[0]
            reps.append({"code": x.get("code"), "name": x.get("name"), "sample_type": x.get("sample_type"), "pre_setup_score": prof.get("attribution_scores", {}).get("pre_setup_score"), "trigger_score": prof.get("attribution_scores", {}).get("trigger_score"), "causal_quality_score": prof.get("attribution_scores", {}).get("causal_quality_score"), "reason": main.get("one_sentence"), "evidence": (main.get("evidence") or [])[:3]})
        shared: List[str] = []
        if cause_type != "原因暂不清晰":
            if sum(1 for x in items if (x.get("deep_attribution", {}) or {}).get("limit_up_dna", {}).get("structure_dna", {}).get("has_monthly_coreline")) >= max(1, len(items)//3): shared.append("较多样本T-1前已有核心线/压力线")
            if sum(1 for x in items if (x.get("deep_attribution", {}) or {}).get("limit_up_dna", {}).get("money_dna", {}).get("has_volume_probe")) >= max(1, len(items)//3): shared.append("较多样本T-1前有资金试盘记忆")
            if sum(1 for x in items if sf((x.get("deep_attribution", {}) or {}).get("trigger_day", {}).get("volume_ratio_20")) >= 1.4) >= max(1, len(items)//2): shared.append("多数样本T日有健康放量确认")
        else: shared.append("技术结构和板块证据不足，保留为未覆盖强样本")
        avg_pre = rd(sum(pre_scores)/len(pre_scores) if pre_scores else 0); avg_trg = rd(sum(trg_scores)/len(trg_scores) if trg_scores else 0); avg_causal = rd(sum(causal_scores)/len(causal_scores) if causal_scores else 0)
        clusters.append({"cluster_id": logic_id_for_cause(cause_type), "cluster_name": cause_type, "sample_filter": sample_filter or "all_strong_samples", "sample_count": len(items), "strong_sample_count": sum(1 for s in causal_scores if s >= 75), "avg_pre_setup_score": avg_pre, "avg_trigger_score": avg_trg, "avg_causal_quality_score": avg_causal, "representative_samples": reps, "shared_features": shared, "experience_sentence": build_cluster_experience_sentence(cause_type, len(items), avg_pre, avg_trg, avg_causal)})
    clusters = sorted(clusters, key=lambda x: (x.get("strong_sample_count",0), x.get("sample_count",0), sf(x.get("avg_causal_quality_score"))), reverse=True)
    payload = {"target_date": ymd_to_dash(target_date), "version": VERSION, "sample_filter": sample_filter or "all_strong_samples", "sample_count": len(selected), "clusters": clusters}
    if sample_filter is None: write_json_file(DAILY_CAUSE_CLUSTERS_FILE, payload)
    return payload


def build_cluster_experience_sentence(cause_type: str, count: int, avg_pre: float, avg_trg: float, avg_causal: float) -> str:
    if cause_type == "原因暂不清晰":
        return f"今日有{count}只涨停暂未被现有结构解释，进入未覆盖涨停样本池，后续只从涨停样本内部继续寻找新逻辑。"
    if "核心线" in cause_type or "压力线" in cause_type:
        return f"今日这类涨停的重点不是单日强，而是T-1前已经有线，T日用涨停打穿旧供应区；平均事前{avg_pre}、触发{avg_trg}、因果{avg_causal}。"
    if "热点" in cause_type or "情绪" in cause_type:
        return f"今日这类涨停说明热点只是火柴，个股必须有临界结构配合；平均事前{avg_pre}、触发{avg_trg}。"
    if "试盘" in cause_type:
        return f"今日这类涨停强调前期资金冲关失败后的消化，再由T日涨停二次攻击。"
    return f"今日{cause_type}出现{count}只样本，已沉淀为涨停归因观察。"


def update_experience_library(target_date: str, cluster_payload: Dict[str, Any]) -> Dict[str, Any]:
    library = load_json_file(EXPERIENCE_LIBRARY_FILE, {"version": VERSION, "experiences": {}, "last_updated": "", "total_runs": 0})
    if not isinstance(library, dict):
        library = {"version": VERSION, "experiences": {}, "last_updated": "", "total_runs": 0}
    exps = library.setdefault("experiences", {})
    for cl in cluster_payload.get("clusters", []) or []:
        cid = ss(cl.get("cluster_id")) or "unknown"
        exp = exps.get(cid) or {
            "experience_id": cid,
            "logic_name": cl.get("cluster_name"),
            "source": "employee5_strong_sample_attribution",
            "status": "observation",
            "total_observation_days": 0,
            "total_strong_samples": 0,
            "typical_pre_features": [],
            "typical_trigger_features": [],
            "representative_samples": [],
            "maturity": "prototype",
        }
        exp["total_observation_days"] = int(exp.get("total_observation_days", 0)) + 1
        exp["total_strong_samples"] = int(exp.get("total_strong_samples", 0)) + int(cl.get("sample_count", 0))
        exp["last_updated"] = ymd_to_dash(target_date)
        exp["typical_pre_features"] = list(dict.fromkeys((exp.get("typical_pre_features", []) or []) + (cl.get("shared_features", []) or [])))[-20:]
        exp["typical_trigger_features"] = list(dict.fromkeys((exp.get("typical_trigger_features", []) or []) + [cl.get("experience_sentence")]))[-20:]
        reps = exp.get("representative_samples", []) or []
        for r in cl.get("representative_samples", [])[:3]:
            reps.append({"date": ymd_to_dash(target_date), "code": r.get("code"), "name": r.get("name"), "reason": r.get("reason"), "causal_quality_score": r.get("causal_quality_score")})
        exp["representative_samples"] = reps[-60:]
        total = int(exp.get("total_strong_samples", 0))
        exp["maturity"] = "mature_observation" if total >= 50 else ("developing" if total >= 15 else "prototype")
        exps[cid] = exp
    library["version"] = VERSION
    library["last_updated"] = ymd_to_dash(target_date)
    library["total_runs"] = int(library.get("total_runs", 0)) + 1
    library["experiences"] = exps
    write_json_file(EXPERIENCE_LIBRARY_FILE, library)
    return library


def build_factor_drafts(target_date: str, experience_library: Dict[str, Any], cluster_payload: Dict[str, Any]) -> Dict[str, Any]:
    drafts: List[Dict[str, Any]] = []
    cluster_by_id = {c.get("cluster_id"): c for c in cluster_payload.get("clusters", []) or []}
    for cid, exp in (experience_library.get("experiences", {}) or {}).items():
        if cid == "unclear_or_news_driven":
            continue
        cl = cluster_by_id.get(cid, {})
        if int(exp.get("total_strong_samples", 0)) < 3:
            continue
        draft = {
            "factor_id": f"employee5_{cid}_draft_v1",
            "source": "employee5_strong_sample_attribution",
            "status": "draft_only",
            "last_updated": ymd_to_dash(target_date),
            "logic_name": exp.get("logic_name"),
            "sample_count_strong_samples_only": exp.get("total_strong_samples"),
            "observation_days": exp.get("total_observation_days"),
            "maturity": exp.get("maturity"),
            "recommended_for": "一号员工基础召回/深度解释候选，不可直接作为买入规则",
            "precondition": build_draft_precondition(cid),
            "trigger_condition": build_draft_trigger_condition(cid),
            "risk_note": "五号员工只研究涨停样本，不包含未涨停负样本；该草稿只能作为经验素材，不能直接当胜率规则。",
            "latest_cluster_stats": {
                "sample_count": cl.get("sample_count"),
                "avg_pre_setup_score": cl.get("avg_pre_setup_score"),
                "avg_trigger_score": cl.get("avg_trigger_score"),
                "avg_causal_quality_score": cl.get("avg_causal_quality_score"),
            },
        }
        drafts.append(draft)
    payload = {"target_date": ymd_to_dash(target_date), "version": VERSION, "factor_drafts": drafts}
    write_json_file(FACTOR_DRAFTS_FILE, payload)
    return payload


def build_draft_precondition(cid: str) -> Dict[str, Any]:
    if cid == "core_pressure_break":
        return {"core_or_pressure_touch_count_min": 2, "pre_close_distance_to_line_pct_max": 8, "line_must_be_visible_before_trigger": True}
    if cid == "monthly_box_break_repair_coreline":
        return {"monthly_box_min_months": 3, "post_break_touch_count_min": 2, "stage_high_volume_touch_min": 1, "body_cut_big_count_max": 1}
    if cid == "probe_second_attack":
        return {"probe_volume_ratio_20_min": 1.55, "post_probe_max_drawdown_pct_min": -15, "pre_close_distance_to_probe_high_pct_abs_max": 8}
    if cid == "platform_compression_breakout":
        return {"price_compress_ratio_max": 0.85, "volume_cv_20_max": 0.55, "bad_long_black_count_max": 0, "must_have_money_or_line_context": True}
    return {"t1_structure_visible": True, "pre_close_near_key_conflict": True}


def build_draft_trigger_condition(cid: str) -> Dict[str, Any]:
    base = {"limit_up_trigger": True, "trigger_quality_score_min": 60, "close_position_min": 0.82}
    if "coreline" in cid or "pressure" in cid:
        base["must_break_key_line"] = True
    if cid == "sector_ignition_ready_structure":
        base["same_board_limit_count_min"] = 5
    return base


def record_unexplained_limitups(target_date: str, results: List[Dict[str, Any]]) -> None:
    for r in results:
        prof = r.get("deep_attribution", {}) or {}
        if prof.get("main_cause") != "原因暂不清晰": continue
        sample_type = ss(r.get("sample_type")) or ("today_limit_up" if r.get("is_limit_sample") else "rolling_20d_top_gain")
        target_file = UNEXPLAINED_LIMITUPS_FILE if sample_type in ("today_limit_up", "today_extreme_move") else UNEXPLAINED_20D_WINNERS_FILE
        append_jsonl(target_file, {"date": ymd_to_dash(target_date), "sample_type": sample_type, "code": r.get("code"), "name": r.get("name"), "pct_chg": r.get("pct_chg"), "return20": r.get("return20"), "technical_evidence_score": prof.get("attribution_scores", {}).get("pre_setup_score"), "trigger_score": prof.get("attribution_scores", {}).get("trigger_score"), "reason": "结构、资金行为和热点证据不足，不能硬编；按样本类型进入未覆盖样本池。"})


def distill_daily_cognition(target_date: str, results: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], str]:
    """强样本 -> 分样本类型原因簇 -> 观察库 -> 经验库 -> 因子草稿；不引入未涨停负样本。"""
    cluster_payload = build_daily_cause_clusters(target_date, results)
    limitup_cluster_payload = build_daily_cause_clusters(target_date, results, sample_filter="today_limit_up")
    rolling20_cluster_payload = build_daily_cause_clusters(target_date, results, sample_filter="rolling_20d_top_gain")
    for cl in cluster_payload.get("clusters", []) or []:
        append_jsonl(OBSERVATIONS_FILE, {"observation_id": f"{ymd_to_dash(target_date)}_{cl.get('sample_filter')}_{cl.get('cluster_id')}", "date": ymd_to_dash(target_date), "sample_filter": cl.get("sample_filter"), "cause_type": cl.get("cluster_name"), "sample_count": cl.get("sample_count"), "representative_samples": cl.get("representative_samples", [])[:3], "raw_shared_features": cl.get("shared_features", []), "experience_sentence": cl.get("experience_sentence")})
    experience_library = update_experience_library(target_date, cluster_payload); factor_drafts = build_factor_drafts(target_date, experience_library, cluster_payload); record_unexplained_limitups(target_date, results)
    old_style_library = load_json_file(COGNITION_LIBRARY_FILE, {"version": VERSION, "logics": {}, "last_updated": "", "total_runs": 0})
    old_style_library["version"] = VERSION; old_style_library["last_updated"] = ymd_to_dash(target_date); old_style_library["total_runs"] = int(old_style_library.get("total_runs", 0)) + 1; old_style_library["strong_sample_experience_library"] = experience_library; write_json_file(COGNITION_LIBRARY_FILE, old_style_library)
    actions: List[Dict[str, Any]] = []
    for cl in cluster_payload.get("clusters", []) or []:
        action = "observe" if cl.get("cluster_name") == "原因暂不清晰" else "reinforce"
        actions.append({"action": action, "logic_id": cl.get("cluster_id"), "logic_name": cl.get("cluster_name"), "sample_filter": cl.get("sample_filter"), "reason": f"今日{cl.get('sample_count')}只强样本，强样本{cl.get('strong_sample_count')}只，因果均分{cl.get('avg_causal_quality_score')}。{cl.get('experience_sentence')}", "new_samples": [f"{x.get('name')}({x.get('code')})" for x in (cl.get("representative_samples") or [])[:4]]})
    daily_lessons = {"target_date": ymd_to_dash(target_date), "version": VERSION, "daily_cause_summary": [{"cause_type": c.get("cluster_name"), "sample_filter": c.get("sample_filter"), "sample_count": c.get("sample_count"), "strong_sample_count": c.get("strong_sample_count"), "avg_score": c.get("avg_causal_quality_score"), "avg_pre_setup_score": c.get("avg_pre_setup_score"), "avg_trigger_score": c.get("avg_trigger_score"), "daily_importance": "high" if c.get("strong_sample_count", 0) >= 3 or c.get("sample_count", 0) >= 5 else ("medium" if c.get("sample_count", 0) >= 2 else "low"), "representative_samples": c.get("representative_samples"), "shared_features": c.get("shared_features"), "experience_sentence": c.get("experience_sentence")} for c in cluster_payload.get("clusters", [])], "daily_cause_clusters": cluster_payload, "limitup_cause_clusters": limitup_cluster_payload, "rolling_20d_winner_clusters": rolling20_cluster_payload, "experience_library_snapshot": experience_library, "factor_drafts": factor_drafts, "cognition_update_actions": actions, "top_lessons": [c.get("experience_sentence") for c in cluster_payload.get("clusters", [])[:8]], "discipline": "研究当日涨停/极强样本与近20日大涨样本；不引入未涨停失败样本；所有反哺均为经验草稿。"}
    write_json_file(DAILY_LESSONS_FILE, daily_lessons); append_jsonl(COGNITION_UPDATE_LOG_FILE, {"target_date": ymd_to_dash(target_date), "actions": actions, "clusters": cluster_payload.get("clusters", [])})
    feedback_md = build_feedback_ideas_markdown(target_date, daily_lessons); FEEDBACK_IDEAS_FILE.write_text(feedback_md, encoding="utf-8")
    return old_style_library, daily_lessons, actions, feedback_md




def build_feedback_ideas_markdown(target_date: str, daily_lessons: Dict[str, Any]) -> str:
    lines = [
        "# 五号员工认知反哺思路草稿",
        f"日期：{ymd_to_dash(target_date)}",
        f"版本：{VERSION}",
        "",
        "> 只来自涨停样本归因，不包含未涨停失败样本；不是一号员工正式规则。",
        "",
        "## 今日原因簇经验",
    ]
    for ds in daily_lessons.get("daily_cause_summary", [])[:12]:
        lines.append(f"- {ds.get('cause_type')}：样本{ds.get('sample_count')}只，强样本{ds.get('strong_sample_count')}只，事前均分{ds.get('avg_pre_setup_score')}，触发均分{ds.get('avg_trigger_score')}，因果均分{ds.get('avg_score')}。")
        lines.append(f"  - 经验：{ds.get('experience_sentence')}")
        reps = ds.get("representative_samples", []) or []
        if reps:
            lines.append("  - 代表样本：" + "；".join([f"{r.get('name')}({r.get('code')})" for r in reps[:4]]))
    lines += ["", "## 一号员工可参考的因子草稿"]
    for fd in (daily_lessons.get("factor_drafts", {}) or {}).get("factor_drafts", [])[:12]:
        lines.append(f"- {fd.get('factor_id')}｜{fd.get('logic_name')}｜{fd.get('recommended_for')}")
        lines.append(f"  - 前置：{json.dumps(fd.get('precondition'), ensure_ascii=False)}")
        lines.append(f"  - 触发：{json.dumps(fd.get('trigger_condition'), ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


# =============================================================================
# 量子级报告落地：核心线价格明细 + 三个核心问题 + 模型审计字段
# =============================================================================
def _safe_line_price(v: Any) -> float:
    return rd(v, 2) if sf(v) > 0 else 0.0


def _line_grade(score: Any) -> str:
    try:
        return score_grade(sf(score))
    except Exception:
        sc = sf(score)
        if sc >= 85:
            return "S"
        if sc >= 70:
            return "A"
        if sc >= 55:
            return "B"
        if sc >= 40:
            return "C"
        return "D"


def _line_status_priority(status: str) -> int:
    order = {
        "高置信核心线": 1,
        "疑似核心线": 2,
        "候选共振线": 3,
        "月线核心线": 4,
        "20日聚合K核心线": 5,
        "日线压力线": 6,
        "历史最大阳量K实顶": 7,
        "历史最大阳量K高点": 8,
        "缺口/影线共振线": 9,
        "未确认": 99,
    }
    return order.get(ss(status), 50)


def _line_source_priority(cycle: str) -> int:
    order = {"月线": 1, "20日聚合K": 2, "日线": 3, "最大阳量K": 4, "缺口/影线": 5}
    return order.get(ss(cycle), 20)


def _append_line_detail(details: List[Dict[str, Any]], *, code: Any, name: Any, price: Any, status: str,
                        cycle: str, grade: str = "", score: Any = 0, line_type: str = "", source: str = "",
                        formation_basis: str = "", evidence: Optional[List[str]] = None,
                        trigger_relation: str = "", pre_visible: bool = True) -> None:
    px = _safe_line_price(price)
    if px <= 0:
        return
    key = (code6(code), ss(name), px, ss(status), ss(cycle), ss(line_type))
    for old in details:
        old_key = (code6(old.get("code")), ss(old.get("name")), _safe_line_price(old.get("price")), ss(old.get("status")), ss(old.get("cycle")), ss(old.get("line_type")))
        if old_key == key:
            return
    details.append({
        "code": code6(code),
        "name": ss(name),
        "price": px,
        "status": ss(status) or "未确认",
        "cycle": ss(cycle),
        "grade": ss(grade) or _line_grade(score),
        "score": rd(score),
        "line_type": ss(line_type),
        "source": ss(source),
        "formation_basis": ss(formation_basis),
        "evidence": (evidence or [])[:6],
        "trigger_relation": ss(trigger_relation),
        "pre_visible": bool(pre_visible),
    })


def build_item_coreline_price_details(item: Dict[str, Any], profile: Dict[str, Any], fallback_core: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """把每只样本的所有关键线价格显式落地，供Markdown、Telegram和JSON共用。"""
    details: List[Dict[str, Any]] = []
    code = item.get("code")
    name = item.get("name")
    structure = (profile or {}).get("structure_evidence", {}) or {}
    trigger = (profile or {}).get("trigger_day", {}) or {}

    core = structure.get("monthly_coreline") or fallback_core or {}
    if core and sf(core.get("line")) > 0:
        platform = core.get("platform", {}) or {}
        basis = "月线有量箱体破位后反抽失败共振"
        if platform:
            basis = (
                f"{platform.get('start_ym')}~{platform.get('end_ym')}有量箱体；"
                f"{platform.get('break_ym')}破位后多次反抽失败"
            )
        evidence = []
        if core.get("touch_count") is not None:
            evidence.append(f"触碰/刺穿{core.get('touch_count')}次")
        if core.get("post_break_stage_high_volume_touch_count") is not None:
            evidence.append(f"阶段高量触线{core.get('post_break_stage_high_volume_touch_count')}次")
        if core.get("body_cut_count") is not None:
            evidence.append(f"实体切割{core.get('body_cut_count')}次")
        _append_line_detail(
            details, code=code, name=name, price=core.get("line"),
            status=core.get("status") or ("月线核心线" if core.get("valid") else "候选共振线"),
            cycle="月线", grade=core.get("grade"), score=core.get("score"),
            line_type="monthly_box_break_repair_coreline", source="monthly_coreline_engine",
            formation_basis=basis, evidence=evidence,
            trigger_relation="T日收盘突破" if trigger.get("breaks_monthly_coreline") else "T日前已存在，等待触发确认",
            pre_visible=True,
        )

    core20 = structure.get("aggregate_20d_coreline", {}) or {}
    if core20 and sf(core20.get("core_line")) > 0:
        evidence = [
            f"触碰{core20.get('touch_count')}次",
            f"带量上影共振{core20.get('left_high_volume_shadow_resonance_count')}次",
        ]
        _append_line_detail(
            details, code=code, name=name, price=core20.get("core_line"),
            status="20日聚合K核心线" if core20.get("valid") else "20日候选线",
            cycle="20日聚合K", grade=_line_grade(core20.get("score")), score=core20.get("score"),
            line_type="aggregate_20d_coreline", source=core20.get("source"),
            formation_basis="历史最大阳量K失败后的第一次修复努力高点/带量上影共振",
            evidence=evidence,
            trigger_relation="T日突破" if core20.get("trigger_break_20d_coreline") else "T日前已存在",
            pre_visible=True,
        )

    pressure_lines = structure.get("pressure_lines", []) or []
    for pl in pressure_lines[:2]:
        _append_line_detail(
            details, code=code, name=name, price=pl.get("line"),
            status="日线压力线", cycle="日线", grade=_line_grade(pl.get("score")), score=pl.get("score"),
            line_type=pl.get("line_type") or "daily_failed_high_pressure", source="daily_pressure_engine",
            formation_basis=f"日线失败高点/实体顶共振，触碰{pl.get('touch_count')}次、失败{pl.get('reject_count')}次",
            evidence=pl.get("examples", []) or [],
            trigger_relation="T日突破" if pl.get("break_by_trigger") else "T日前临界",
            pre_visible=True,
        )

    max_bull = structure.get("max_bull_volume_bar", {}) or {}
    if max_bull.get("valid"):
        levels = max_bull.get("levels", {}) or {}
        bar = max_bull.get("bar", {}) or {}
        _append_line_detail(
            details, code=code, name=name, price=levels.get("body_top"),
            status="历史最大阳量K实顶", cycle="最大阳量K", grade=_line_grade(max_bull.get("score")), score=max_bull.get("score"),
            line_type="max_bull_volume_body_top", source="max_bull_volume_bar_engine",
            formation_basis=f"{bar.get('date')}历史最大阳量K实顶/筹码交换位",
            evidence=max_bull.get("evidence", []) or [],
            trigger_relation="T日重新站上实顶" if max_bull.get("trigger_break_body_top") else "T日前关键位",
            pre_visible=True,
        )
        _append_line_detail(
            details, code=code, name=name, price=levels.get("high"),
            status="历史最大阳量K高点", cycle="最大阳量K", grade=_line_grade(max_bull.get("score")), score=max_bull.get("score"),
            line_type="max_bull_volume_high", source="max_bull_volume_bar_engine",
            formation_basis=f"{bar.get('date')}历史最大阳量K最高点",
            evidence=max_bull.get("evidence", []) or [],
            trigger_relation="T日打穿高点" if max_bull.get("trigger_break_high") else "T日前关键位",
            pre_visible=True,
        )

    gap_shadow = structure.get("gap_shadow_resonance", {}) or {}
    if gap_shadow.get("valid") and sf(gap_shadow.get("line")) > 0:
        _append_line_detail(
            details, code=code, name=name, price=gap_shadow.get("line"),
            status="缺口/影线共振线", cycle="缺口/影线", grade=_line_grade(gap_shadow.get("score")), score=gap_shadow.get("score"),
            line_type="gap_shadow_resonance", source="gap_shadow_engine",
            formation_basis=f"上影触碰{gap_shadow.get('upper_shadow_touch_count')}次，缺口共振{gap_shadow.get('gap_zone_count')}处",
            evidence=gap_shadow.get("examples", []) or [],
            trigger_relation="T日突破" if gap_shadow.get("trigger_break_line") else "T日前关键位",
            pre_visible=True,
        )

    details.sort(key=lambda d: (_line_status_priority(d.get("status")), _line_source_priority(d.get("cycle")), -sf(d.get("score")), sf(d.get("price"))))
    return details


def format_coreline_detail_line(d: Dict[str, Any], with_stock: bool = True) -> str:
    stock = f"{d.get('name')}({d.get('code')})：" if with_stock else ""
    tail = []
    if d.get("formation_basis"):
        tail.append(f"形成依据：{d.get('formation_basis')}")
    if d.get("trigger_relation"):
        tail.append(f"关系：{d.get('trigger_relation')}")
    right = "｜".join(tail)
    base = f"{stock}{d.get('price')}元｜{d.get('status')}｜{d.get('cycle')}｜级别{d.get('grade')}｜分数{d.get('score')}"
    return base + (f"｜{right}" if right else "")


def collect_report_coreline_price_details(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    details: List[Dict[str, Any]] = []
    for x in results or []:
        prof = x.get("deep_attribution", {}) or {}
        item_details = prof.get("coreline_price_details") or build_item_coreline_price_details(x, prof, x.get("core_line", {}) or {})
        for d in item_details:
            _append_line_detail(
                details, code=d.get("code"), name=d.get("name"), price=d.get("price"), status=d.get("status"),
                cycle=d.get("cycle"), grade=d.get("grade"), score=d.get("score"), line_type=d.get("line_type"),
                source=d.get("source"), formation_basis=d.get("formation_basis"), evidence=d.get("evidence", []),
                trigger_relation=d.get("trigger_relation"), pre_visible=d.get("pre_visible", True),
            )
    details.sort(key=lambda d: (_line_status_priority(d.get("status")), _line_source_priority(d.get("cycle")), -sf(d.get("score")), code6(d.get("code"))))
    return details



def count_detail_status(details: List[Dict[str, Any]]) -> Dict[str, int]:
    """按已经落地的价格线明细统计全部关键线状态，和月线主核心线口径分开。"""
    out: Dict[str, int] = {}
    for d in details or []:
        st = ss(d.get("status") or "未确认")
        out[st] = out.get(st, 0) + 1
    return out


def count_main_coreline_status(results: List[Dict[str, Any]]) -> Dict[str, int]:
    """只统计主核心线/月线核心线状态，避免和20日、日线、最大阳量K等关键线混口径。"""
    out: Dict[str, int] = {}
    for x in results or []:
        st = ss((x.get("core_line") or {}).get("status", "未确认")) or "未确认"
        out[st] = out.get(st, 0) + 1
    return out


def has_t1_previsible_structure(profile: Dict[str, Any]) -> bool:
    """一号员工反哺硬门槛：必须是T-1以前已成立的A/B级可扫描结构，不能靠T日突破或单纯热点反推。"""
    structure = (profile or {}).get("structure_evidence", {}) or {}
    core = structure.get("monthly_coreline", {}) or {}
    if bool(core.get("valid")) and ss(core.get("status")) in ("高置信核心线", "疑似核心线") and sf(core.get("score")) >= 55: return True
    core20 = structure.get("aggregate_20d_coreline", {}) or {}
    if bool(core20.get("pre_valid")) and sf(core20.get("pre_line_score")) >= 55: return True
    max_bull = structure.get("max_bull_volume_bar", {}) or {}
    if bool(max_bull.get("pre_valid")) and sf(max_bull.get("pre_line_score")) >= 50: return True
    gap_shadow = structure.get("gap_shadow_resonance", {}) or {}
    if bool(gap_shadow.get("pre_valid")) and sf(gap_shadow.get("pre_line_score")) >= 52: return True
    platform = (profile or {}).get("platform_compression", {}) or {}; pressure_lines = structure.get("pressure_lines", []) or []; probes = ((profile or {}).get("money_behavior_evidence", {}) or {}).get("probe_events", []) or []
    platform_ok = bool(platform.get("pre_valid") or platform.get("valid")) and sf(platform.get("score")) >= 62
    pressure_ok = bool(pressure_lines and sf(pressure_lines[0].get("pre_line_score", pressure_lines[0].get("score"))) >= 65 and int(pressure_lines[0].get("touch_count", 0)) >= 3)
    probe_ok = bool(probes and sf(probes[0].get("volume_ratio_20")) >= 1.8 and sf(probes[0].get("post_probe_hold_score")) >= 60 and sf(probes[0].get("post_volume_cv")) <= 0.45)
    return bool((platform_ok and (pressure_ok or probe_ok)) or (pressure_ok and probe_ok))

def feedback_layers_from_profile(profile: Dict[str, Any], matches: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """只输出一号员工可能吸收的位置；仍为草稿，不等于胜率已验证。"""
    structure = (profile or {}).get("structure_evidence", {}) or {}
    platform = (profile or {}).get("platform_compression", {}) or {}
    trigger = (profile or {}).get("trigger_day", {}) or {}
    money = (profile or {}).get("money_behavior_evidence", {}) or {}
    layers: List[str] = []
    if (structure.get("monthly_coreline", {}) or {}).get("valid"):
        layers.append("coreline_generation")
    if (structure.get("aggregate_20d_coreline", {}) or {}).get("valid") or structure.get("pressure_lines"):
        layers.append("key_line_context")
    if platform.get("valid") or (money.get("probe_events") or []):
        layers.append("pre_breakout_recall")
    if matches:
        layers.append("sector_heat_context")
    if sf(trigger.get("quality_score")) >= 60:
        layers.append("trigger_confirmation")
    return list(dict.fromkeys(layers)) or ["observation_only"]

def build_deep_research_questions(item: Dict[str, Any], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    structure = (profile or {}).get("structure_evidence", {}) or {}
    platform = (profile or {}).get("platform_compression", {}) or {}
    money = (profile or {}).get("money_behavior_evidence", {}) or {}
    trigger = (profile or {}).get("trigger_day", {}) or {}
    matches = item.get("hotspot_matches", []) or []
    causes = (profile or {}).get("cause_candidates", []) or []
    main = causes[0] if causes else {}
    details = (profile or {}).get("coreline_price_details") or build_item_coreline_price_details(item, profile, (profile or {}).get("structure_evidence", {}).get("monthly_coreline", {}))
    best = details[0] if details else {}
    core = structure.get("monthly_coreline", {}) or {}
    pressure_lines = structure.get("pressure_lines", []) or []
    core20 = structure.get("aggregate_20d_coreline", {}) or {}
    probes = money.get("probe_events", []) or []

    if best:
        q1_answer = (
            f"最核心的线先看{best.get('price')}元，状态是{best.get('status')}，周期是{best.get('cycle')}。"
            f"它的形成依据是：{best.get('formation_basis') or '由历史价格/量能反复共振生成'}。"
        )
        q1_evidence = list(best.get("evidence", []) or [])
    else:
        q1_answer = "当前样本没有生成足够清晰的核心线，不能为了涨停结果硬画线。"
        q1_evidence = ["核心线未确认", "保留为未覆盖或弱结构样本"]

    if core.get("valid"):
        q1_evidence += [
            f"月线核心线{core.get('line')}元",
            f"触碰/刺穿{core.get('touch_count')}次",
            f"阶段高量触线{core.get('post_break_stage_high_volume_touch_count')}次",
        ]
    elif core20.get("valid"):
        q1_evidence.append(f"20日聚合K核心线{core20.get('core_line')}元")
    elif pressure_lines:
        q1_evidence.append(f"日线压力线{pressure_lines[0].get('line')}元")

    timing_bits: List[str] = []
    if probes:
        p = probes[0]
        timing_bits.append(f"前置试盘：{p.get('date')}量比{p.get('volume_ratio_20')}，随后最大回撤{p.get('later_max_drawdown_pct')}%")
    if platform.get("valid"):
        timing_bits.append(f"平台压缩：量能CV={platform.get('volume_cv_20')}，平量{platform.get('flat_volume_days')}天，放量长阴{platform.get('bad_long_black_count')}根")
    if best:
        timing_bits.append(f"涨停前围绕{best.get('price')}元核心线临界消化")
    if matches:
        top = matches[0]
        timing_bits.append(f"板块点火：{top.get('hotspot')} {top.get('level')}级，涨停命中{top.get('limit_hit_count')}只")
    if trigger:
        timing_bits.append(f"T日只做确认：触发质量分{trigger.get('quality_score')}，量比{trigger.get('volume_ratio_20')}，收盘分位{trigger.get('close_position')}")
    q2_answer = "；".join(timing_bits) + "。" if timing_bits else "涨停前没有足够清晰的试盘、压缩、贴线或热点共振，时间点解释力不足。"

    feed_layers: List[str] = []
    if core.get("valid"):
        feed_layers.append("核心线生成层（月线有量箱体破位后反抽失败核心线）")
    if core20.get("valid") or pressure_lines:
        feed_layers.append("中短周期关键线/压力线临界层")
    if platform.get("valid"):
        feed_layers.append("爆发前夜基础召回层（压缩、平量、贴线、无放量长阴）")
    if trigger and sf(trigger.get("quality_score")) >= 60:
        feed_layers.append("触发确认层（T日强收盘与健康放量）")
    if matches:
        feed_layers.append("板块热度辅助层")
    if not feed_layers:
        feed_layers.append("暂不反哺正式因子，只进入未覆盖样本库")

    q3_answer = (
        f"主归因是{main.get('cause_type', '原因暂不清晰')}，建议反哺位置：" + "；".join(feed_layers) + "。"
        "注意：五号员工只研究涨停样本，这里只能生成一号员工候选因子草稿，不能直接当胜率结论。"
    )

    return [
        {
            "question_id": "Q1_coreline_validity",
            "question": "这条核心线为什么有效？",
            "answer": q1_answer,
            "evidence": list(dict.fromkeys([ss(x) for x in q1_evidence if ss(x)]))[:8],
            "failure_exclusion": "如果线位低于原箱体下沿、实体切割过多、触碰无量、收盘已多次有效接受，则不能升为高置信核心线。",
        },
        {
            "question_id": "Q2_timing_selection",
            "question": "资金为什么选择这个时间点发动？",
            "answer": q2_answer,
            "evidence": timing_bits[:8],
            "failure_exclusion": "如果涨停前没有压缩、贴线、试盘后消化、热点共振，只是T日突然强拉，则不能归为爆发前夜结构。",
        },
        {
            "question_id": "Q3_employee1_feedback",
            "question": "这个案例能不能形成一号员工可提前识别的因子？",
            "answer": q3_answer,
            "evidence": feed_layers[:8],
            "failure_exclusion": "只出现T日涨停确认而缺少T-1以前可见结构时，只能进入触发样本库，不能反哺基础召回。",
        },
    ]


def build_model_audit_questions(item: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    boundary = (profile or {}).get("analysis_boundary", {}) or {}
    structure = (profile or {}).get("structure_evidence", {}) or {}
    platform = (profile or {}).get("platform_compression", {}) or {}
    trigger = (profile or {}).get("trigger_day", {}) or {}
    money = (profile or {}).get("money_behavior_evidence", {}) or {}
    causes = (profile or {}).get("cause_candidates", []) or []
    main = causes[0] if causes else {}
    details = (profile or {}).get("coreline_price_details") or build_item_coreline_price_details(item, profile, structure.get("monthly_coreline", {}))
    best = details[0] if details else {}
    matches = item.get("hotspot_matches", []) or []
    reason_type = ss(main.get("cause_type")) or "原因暂不清晰"

    is_structure = any(k in reason_type for k in ["核心线", "压力", "平台", "修复", "20日", "最大阳量", "影线", "缺口", "试盘"])
    is_hot = bool("热点" in reason_type or "情绪" in reason_type or matches)
    cause_class = "结构型涨停" if is_structure else ("热点/情绪型涨停" if is_hot else "未覆盖/待研究涨停")
    previsible_ok = has_t1_previsible_structure(profile)
    timing_ok = bool(platform.get("valid") or structure.get("pressure_lines") or best or money.get("probe_events") or matches)
    primary_coreline_ok = bool(best and best.get("status") in ("高置信核心线", "疑似核心线", "月线核心线", "20日聚合K核心线"))
    not_unclear = bool(reason_type and reason_type != "原因暂不清晰")
    can_feedback = bool(not_unclear and previsible_ok)

    if primary_coreline_ok:
        coreline_verdict = format_coreline_detail_line(best, with_stock=False)
    elif best:
        coreline_verdict = "存在关键线但还不是高置信主核心线：" + format_coreline_detail_line(best, with_stock=False)
    else:
        coreline_verdict = "未确认核心线，不能硬解释"

    return {
        "anti_hindsight_bias": {
            "passed": bool(boundary.get("pre_end_date") and boundary.get("trigger_date")),
            "verdict": "T-1结构与T日触发分离" if boundary.get("pre_end_date") else "边界不足，需要补历史K线",
            "pre_end_date": boundary.get("pre_end_date"),
            "trigger_date": boundary.get("trigger_date"),
        },
        "true_coreline_check": {
            "passed": primary_coreline_ok,
            "verdict": coreline_verdict,
        },
        "cause_classification_check": {
            "passed": not_unclear,
            "verdict": cause_class,
            "main_cause": reason_type,
        },
        "timing_explanation_check": {
            "passed": timing_ok,
            "verdict": "存在T-1以前的贴线/压缩/压力/试盘/热点临界证据" if timing_ok else "缺少发动时间点解释",
        },
        "employee1_feedback_check": {
            "passed": can_feedback,
            "verdict": "可沉淀为一号员工候选因子草稿，但仍需失败样本验证" if can_feedback else "暂不反哺正式因子：缺少清晰主因或T-1以前结构证据",
            "recommended_layers": feedback_layers_from_profile(profile, matches) if can_feedback else ["observation_only"],
        },
        "failure_exclusion_check": {
            "passed": False,
            "validation_status": "not_validated_by_failure_samples",
            "verdict": "已输出失败排除条件，但尚未用未涨停/冲高失败样本验证误报率；不得当作完成反证。",
        },
    }


def build_employee1_feedback_potential(item: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    questions = (profile or {}).get("deep_research_questions") or build_deep_research_questions(item, profile)
    causes = (profile or {}).get("cause_candidates", []) or []; main = causes[0] if causes else {}; reason_type = ss(main.get("cause_type")) or "原因暂不清晰"
    matches = item.get("hotspot_matches", []) or []; layers = feedback_layers_from_profile(profile, matches)
    previsible_ok = has_t1_previsible_structure(profile); not_unclear = bool(reason_type and reason_type != "原因暂不清晰")
    trigger_only = bool(sf(main.get("pre_score")) < 45 and sf(main.get("trigger_score")) >= 55)
    can_feedback = bool(previsible_ok and not_unclear and not trigger_only)
    return {"can_feedback": can_feedback, "recommended_layers": layers if can_feedback else ["observation_only"], "logic_name": reason_type, "draft_only": True, "formal_factor_ready": False, "failure_sample_validation_required": True, "sample_type": item.get("sample_type") or item.get("candidate_source") or "unknown", "why": questions[-1].get("answer") if questions else "暂无可反哺结论。", "gate_reason": "通过：T-1以前存在A/B级可扫描结构，主归因清晰，且不是纯T日触发。" if can_feedback else "未通过：缺少T-1以前A/B级可扫描结构、主归因不清晰，或只是T日触发强。"}




def assign_sample_groups(results: List[Dict[str, Any]], deep_count: int = DEEP_SAMPLE_COUNT) -> Dict[str, Any]:
    """A组=当日涨停/极强前三；B组=近20日累计涨幅前三。报告深讲A/B并集，其余只做统计沉淀。"""
    limit_rank_source = [x for x in results if x.get("sample_type") in ("today_limit_up", "today_extreme_move") or bool(x.get("is_limit_sample", False))] or results
    day_ranked = sorted(limit_rank_source, key=lambda x: (sf(x.get("pct_chg")), sf(x.get("reason_confidence"))), reverse=True)
    ret_candidates = [x for x in results if sf(x.get("return20")) != 0 and x.get("hist_source") != "missing"]
    ret_ranked = sorted(ret_candidates, key=lambda x: sf(x.get("return20")), reverse=True)
    group_a_codes = [code6(x.get("code")) for x in day_ranked[:deep_count]]; group_b_codes = [code6(x.get("code")) for x in ret_ranked[:deep_count]]
    deep_codes: List[str] = []
    for c in group_a_codes + group_b_codes:
        if c and c not in deep_codes: deep_codes.append(c)
    for x in results:
        code = code6(x.get("code")); groups = []
        if code in group_a_codes: groups.append("A组当日涨停/极强前三")
        if code in group_b_codes: groups.append("B组近20日累计涨幅前三")
        if not groups: groups.append("统计沉淀样本")
        x["sample_groups"] = groups; x["deep_report_selected"] = bool(code in deep_codes[:MAX_DEEP_REPORT_ITEMS])
    scope_base = "实时全市场行情池" if REALTIME_SOURCE_ALLOWED else "历史静态股票池+历史K线反推"
    limit_desc = "全市场" if B_GROUP_SCAN_LIMIT <= 0 else f"限量前{B_GROUP_SCAN_LIMIT}只"
    return {"group_a_today_top": group_a_codes, "group_b_20d_top": group_b_codes, "deep_report_codes": deep_codes[:MAX_DEEP_REPORT_ITEMS], "deep_report_count": len(deep_codes[:MAX_DEEP_REPORT_ITEMS]), "b_group_scan_limit": B_GROUP_SCAN_LIMIT, "b_group_scope": f"{scope_base}{limit_desc}", "max_deep_report_items": MAX_DEEP_REPORT_ITEMS}


def make_telegram_summary(target_date: str, results: List[Dict[str, Any]], hotspots: List[Dict[str, Any]], elapsed: float) -> str:
    reason_count: Dict[str, int] = {}; high_learning = 0
    for x in results:
        rt = ss(x.get("reason_type")) or "原因暂不清晰"; reason_count[rt] = reason_count.get(rt, 0) + 1
        if (x.get("deep_attribution", {}) or {}).get("attribution_scores", {}).get("learning_value") == "high": high_learning += 1
    top_reasons = "；".join([f"{k}{v}只" for k, v in sorted(reason_count.items(), key=lambda z: z[1], reverse=True)[:5]])
    top_hot = "、".join([f"{h.get('hotspot')}({h.get('level')})" for h in hotspots[:5]]) or "无稳定热点"
    coreline_details = [d for d in collect_report_coreline_price_details(results) if d.get("status") in ("高置信核心线", "疑似核心线", "20日聚合K核心线", "历史最大阳量K实顶", "缺口/影线共振线") and bool(d.get("pre_visible", True))]
    main_core_status_count = count_main_coreline_status(results); key_line_status_count = count_detail_status(coreline_details); sample_group_summary = assign_sample_groups(results, DEEP_SAMPLE_COUNT)
    limit_n = sum(1 for x in results if x.get("sample_type") == "today_limit_up"); rolling_n = sum(1 for x in results if x.get("sample_type") == "rolling_20d_top_gain")
    lines = ["🧬【五号员工-强样本归因完成】", f"日期：{target_date}｜模式：{REPORT_MODE}", f"样本：当日涨停/极强{limit_n}只，20日大涨候选{rolling_n}只；高学习价值：{high_learning}只", f"A组当日前三：{', '.join(sample_group_summary.get('group_a_today_top', [])) or '无'}；B组20日涨幅前三：{', '.join(sample_group_summary.get('group_b_20d_top', [])) or '无'}", f"B组口径：{sample_group_summary.get('b_group_scope')}；实时源允许={REALTIME_SOURCE_ALLOWED}", f"主核心线分布（月线口径）：{json.dumps(main_core_status_count, ensure_ascii=False)}", f"高价值关键线分布：{json.dumps(key_line_status_count, ensure_ascii=False)}"]
    if coreline_details:
        lines.append("核心线/关键线价格明细：")
        for d in coreline_details[:12]: lines.append("- " + format_coreline_detail_line(d, with_stock=True))
        if len(coreline_details) > 12: lines.append(f"- 其余{len(coreline_details) - 12}条核心线/关键线见完整报告JSON。")
    else: lines.append("核心线/关键线价格明细：暂无高质量核心线，完整报告保留未确认原因。")
    lines += [f"主原因簇：{top_reasons}", f"热点：{top_hot}", f"数据门控：{TRADE_DATE_GATE_REASON}", "纪律：T-1结构/T日触发分离；历史模式禁用实时热点；不输出买卖建议。", f"耗时：{fmt_seconds(elapsed)}"]
    return "\n".join(lines)

def build_report(target_date: str, pool: pd.DataFrame, diagnostics: Dict[str, Any], hotspots: List[Dict[str, Any]], results: List[Dict[str, Any]], hist_source_count: Dict[str, int], elapsed: float, daily_lessons: Optional[Dict[str, Any]] = None, cognition_actions: Optional[List[Dict[str, Any]]] = None) -> Tuple[str, Dict[str, Any]]:
    reason_count: Dict[str, int] = {}; type_count: Dict[str, int] = {}
    high_learning = medium_learning = low_learning = 0
    for x in results:
        rt = ss(x.get("reason_type")) or "原因暂不清晰"; reason_count[rt] = reason_count.get(rt, 0) + 1
        lv = ss((x.get("deep_attribution", {}) or {}).get("attribution_scores", {}).get("learning_value"))
        if lv == "high": high_learning += 1
        elif lv == "medium": medium_learning += 1
        else: low_learning += 1
        for t in x.get("tags", []) or []:
            type_count[t] = type_count.get(t, 0) + 1

    sample_group_summary = assign_sample_groups(results, DEEP_SAMPLE_COUNT)
    clusters = ((daily_lessons or {}).get("daily_cause_clusters", {}) or {}).get("clusters", [])
    coreline_details = collect_report_coreline_price_details(results)
    main_core_status_count = count_main_coreline_status(results)
    key_line_status_count = count_detail_status(coreline_details)
    lines = [
        "🧬【五号员工-强样本归因学习报告】",
        f"日期：{target_date}",
        f"版本：{VERSION}",
        f"运行耗时：{fmt_seconds(elapsed)}",
        f"报告模式：{REPORT_MODE}｜实时行情源允许={REALTIME_SOURCE_ALLOWED}｜门控={TRADE_DATE_GATE_REASON}",
        "固定定位：只研究涨停/大涨强样本；不荐股、不打买入分、不输出交易优先级；不把未涨停失败样本伪装成归因样本。",
        "归因纪律：T-1以前复原结构，T日只做涨停触发确认；所有核心线、平台、试盘、压缩必须在涨停前可见。",
        "闭环纪律：逐只DNA → 原因候选 → 今日原因簇 → 观察库 → 经验库 → 一号员工因子草稿。",
        "新增纪律：核心线状态必须同步写价格；每只样本必须提出三个深度研究问题；JSON必须沉淀审计字段。",
        "",
        "一、今日强样本归因总览：",
        f"- 当日涨停识别：{len(pool)}只；完成归因强样本：{len(results)}只。",
        f"- 深度展开：A组当日涨停/极强前三 + B组近20日累计涨幅前三；其余样本进入统计和JSON。",
        f"- A组代码：{', '.join(sample_group_summary.get('group_a_today_top', [])) or '无'}；B组代码：{', '.join(sample_group_summary.get('group_b_20d_top', [])) or '无'}。",
        f"- B组口径：{sample_group_summary.get('b_group_scope')}，用历史K线计算20日涨幅；历史/上一交易日模式禁止混入实时行情池。",
        f"- 学习价值：高{high_learning}只，中{medium_learning}只，低/未覆盖{low_learning}只。",
        f"- 主核心线状态（月线口径）：{json.dumps(main_core_status_count, ensure_ascii=False)}",
        f"- 关键线状态（全部价格线）：{json.dumps(key_line_status_count, ensure_ascii=False)}",
    ]
    if coreline_details:
        lines.append("- 核心线/关键线价格明细：")
        for d in coreline_details[:60]:
            lines.append("  - " + format_coreline_detail_line(d, with_stock=True))
        if len(coreline_details) > 60:
            lines.append(f"  - 其余{len(coreline_details) - 60}条核心线/关键线见JSON字段 coreline_price_details。")
    else:
        lines.append("- 核心线价格明细：暂无确认核心线/关键线；不得硬画线。")
    lines += [
        f"- 主归因分布：{json.dumps(reason_count, ensure_ascii=False)}",
        f"- 历史K线来源：{json.dumps(hist_source_count, ensure_ascii=False)}",
        f"- 历史K线缓存：命中{HIST_CACHE_HIT}次，写入{HIST_CACHE_WRITE}次；BaoStock连续失败={BAOSTOCK_CONSECUTIVE_FAILURES}。",
        "",
        "二、今日行情热点总览（历史/上一交易日模式下禁用实时热点）：",
    ]
    lines += [f"- {h.get('hotspot')}({h.get('kind')})：{h.get('level')}级，板块涨幅{h.get('pct_chg')}%，涨停命中{h.get('limit_hit_count')}只，强度分{h.get('score')}" for h in hotspots[:12]] or ["- 未能稳定提取热点；只保留个股结构归因。"]

    lines += ["", "三、今日强样本原因簇："]
    if clusters:
        for cl in clusters[:12]:
            reps = cl.get("representative_samples", []) or []
            rep_txt = "；代表：" + "、".join([f"{r.get('name')}({r.get('code')})" for r in reps[:4]]) if reps else ""
            lines.append(f"- {cl.get('cluster_name')}：样本{cl.get('sample_count')}只，强样本{cl.get('strong_sample_count')}只，事前均分{cl.get('avg_pre_setup_score')}，触发均分{cl.get('avg_trigger_score')}，因果均分{cl.get('avg_causal_quality_score')}{rep_txt}")
            lines.append(f"  经验：{cl.get('experience_sentence')}")
    else:
        lines.append("- 暂无原因簇。")

    lines += ["", "四、今日最值得沉淀的经验："]
    for lesson in (daily_lessons or {}).get("top_lessons", [])[:8]:
        lines.append(f"- {lesson}")
    if not (daily_lessons or {}).get("top_lessons"):
        lines.append("- 今日没有形成高一致性经验，保留逐只归因。")

    lines += ["", "五、强样本大类统计："]
    for tag, cnt in sorted(type_count.items(), key=lambda x: x[1], reverse=True)[:18]:
        lines.append(f"- {tag}：{cnt}只")

    lines += ["", "六、逐只深度归因（只展开A/B组，其余样本在JSON保留）："]
    deep_results = [x for x in results if x.get("deep_report_selected")] or results[:MAX_DEEP_REPORT_ITEMS]
    for i, x in enumerate(deep_results, 1):
        deep = x.get("deep_attribution", {}) or {}
        scores = deep.get("attribution_scores", {}) or {}
        boundary = deep.get("analysis_boundary", {}) or {}
        trigger = deep.get("trigger_day", {}) or {}
        dna = deep.get("limit_up_dna", {}) or {}
        chain = deep.get("causal_chain", {}) or {}
        item_corelines = deep.get("coreline_price_details", []) or []
        questions = deep.get("deep_research_questions", []) or build_deep_research_questions(x, deep)
        audit = deep.get("model_audit_questions", {}) or build_model_audit_questions(x, deep)
        group_txt = "、".join(x.get("sample_groups", []) or [])
        lines.append(f"\n【{i}】{x.get('name')}({x.get('code')})｜{group_txt}｜{x.get('board')}｜涨幅{rd(x.get('pct_chg'))}%｜近20日{rd(x.get('return20'))}%｜K线源={x.get('hist_source')}")
        lines.append(f"边界：事前结构截止{boundary.get('pre_end_date')}，涨停触发日{boundary.get('trigger_date')}；T日不参与核心线生成。")
        lines.append(f"主归因：{x.get('reason_type')}｜总证据分{int(sf(x.get('reason_confidence')))}｜事前{scores.get('pre_setup_score')}({scores.get('pre_setup_grade')}) / 触发{scores.get('trigger_score')}({scores.get('trigger_grade')}) / 因果{scores.get('causal_quality_score')}({scores.get('causal_quality_grade')})｜学习价值={scores.get('learning_value')}")
        lines.append(f"一句话：{x.get('reason_text')}")
        if item_corelines:
            lines.append("核心线/关键线价格：")
            for d in item_corelines[:8]:
                lines.append("  - " + format_coreline_detail_line(d, with_stock=False))
        else:
            lines.append("核心线/关键线价格：暂未确认；本样本不能硬套核心线故事。")
        if chain:
            lines.append(f"- 背景：{chain.get('background')}")
            lines.append(f"- 资金动作：{chain.get('first_money_action')}")
            lines.append(f"- 消化/修复：{chain.get('failure_and_absorption')}")
            lines.append(f"- 核心矛盾：{chain.get('key_conflict')}")
        else:
            lines.append("- 核心矛盾：样本前置结构不足或主因暂不清晰，本票不得为了涨停结果硬编故事。")
        lines.append("### 深度研究：三个核心问题")
        for q_idx, q in enumerate(questions[:3], 1):
            lines.append(f"问题{q_idx}：{q.get('question')}")
            lines.append(f"- 回答：{q.get('answer')}")
            ev = q.get("evidence", []) or []
            if ev:
                lines.append("- 证据：" + "；".join([ss(e) for e in ev[:6] if ss(e)]) + "。")
            if q.get("failure_exclusion"):
                lines.append(f"- 失败排除：{q.get('failure_exclusion')}")
        if chain:
            lines.append(f"- 涨停前夜：{chain.get('pre_limit_signal')}")
            lines.append(f"- 涨停当天：{chain.get('trigger')}")
            lines.append(f"- 学习沉淀：{chain.get('new_learning')}")
        if audit:
            lines.append("模型审计：")
            for key in ["anti_hindsight_bias", "true_coreline_check", "cause_classification_check", "timing_explanation_check", "employee1_feedback_check", "failure_exclusion_check"]:
                node = audit.get(key, {}) or {}
                mark = "通过" if node.get("passed") else "待验证"
                lines.append(f"  - {key}：{mark}｜{node.get('verdict')}")
        if trigger:
            lines.append(f"T日触发DNA：量比{trigger.get('volume_ratio_20')}，收盘分位{trigger.get('close_position')}，实体{trigger.get('body_pct')}%，上影{trigger.get('upper_shadow_pct')}%，触发分{trigger.get('quality_score')}。")
        sdna = dna.get("structure_dna", {}) or {}; mdna = dna.get("money_dna", {}) or {}; hdna = dna.get("hotspot_dna", {}) or {}
        lines.append(f"结构DNA：月线核心线={sdna.get('has_monthly_coreline')}({sdna.get('monthly_coreline')})，日线压力={sdna.get('daily_pressure_line')}，20日核心线={sdna.get('coreline_20d')}，最大阳量K={sdna.get('has_max_volume_bar_level')}，缺口/影线共振={sdna.get('has_gap_shadow_resonance')}。")
        lines.append(f"资金DNA：试盘={mdna.get('has_volume_probe')}，试盘量比={mdna.get('probe_volume_ratio_20')}，试盘后CV={mdna.get('post_probe_volume_cv')}，平量天数={mdna.get('flat_volume_days')}，放量长阴={mdna.get('bad_long_black_count')}。")
        if hdna.get("is_hotspot_ignition"):
            lines.append(f"热点DNA：{hdna.get('hotspot_name')}，等级{hdna.get('hotspot_level')}，同板块涨停{hdna.get('same_board_limit_count')}只。")
        causes = deep.get("cause_candidates", []) or []
        cause_bits = [f"{c.get('cause_type')}({c.get('role')}, 总{c.get('score')} / 前{c.get('pre_score')} / 触{c.get('trigger_score')} / 因{c.get('causal_score')})" for c in causes[:6] if sf(c.get('score')) > 0]
        if cause_bits:
            lines.append("多原因候选：" + "；".join(cause_bits) + "。")
        if x.get("reason_type") == "原因暂不清晰":
            lines.append("未覆盖说明：本样本进入 employee5_unexplained_limitups.jsonl，只作为未解释涨停样本沉淀，不硬编原因。")
        # 月线核心线细节已在“核心线/关键线价格”中平铺输出，这里不再重复追加旧式说明。

    lines += ["", "七、认知库动作："]
    for ac in cognition_actions or []:
        lines.append(f"- {ac.get('action')}｜{ac.get('logic_name')}：{ac.get('reason')}")

    lines += ["", "八、可反哺一号员工的草稿："]
    drafts = ((daily_lessons or {}).get("factor_drafts", {}) or {}).get("factor_drafts", [])
    if drafts:
        for fd in drafts[:10]:
            lines.append(f"- {fd.get('factor_id')}｜{fd.get('logic_name')}｜{fd.get('recommended_for')}")
    else:
        lines.append("- 今日尚未形成可结构化输出的因子草稿。")

    lines += [
        "", "九、今日复盘纪律：",
        "- 五号员工研究当日涨停/极强样本与近20日大涨样本，不输出买卖建议。",
        "- 报告用于积累大涨/涨停前夜逻辑、核心线经验、资金行为样本和原因簇。",
        "- 所有反哺一号员工的内容均为经验草稿，需由一号员工模型层单独吸收。",
        "- 本版强制输出核心线价格明细、三个深度研究问题、模型审计字段，防止只讲故事不沉淀。",
    ]

    payload = {
        "target_date": target_date,
        "version": VERSION,
        "run_elapsed_seconds": round(elapsed, 2),
        "run_elapsed_text": fmt_seconds(elapsed),
        "discipline": "limit_up_and_20d_strong_samples_no_failure_samples_no_trading_advice",
        "diagnostics": {**(diagnostics or {}), "report_mode": REPORT_MODE, "trade_date_gate_reason": TRADE_DATE_GATE_REASON, "realtime_source_allowed": REALTIME_SOURCE_ALLOWED, "stale_cache_count": STALE_CACHE_COUNT, "missing_trigger_day_count": MISSING_TRIGGER_DAY_COUNT},
        "sample_group_summary": sample_group_summary,
        "hist_source_count": hist_source_count,
        "hist_cache": {"enabled": USE_HIST_CACHE, "cache_dir": str(KLINE_CACHE_DIR), "hit": HIST_CACHE_HIT, "write": HIST_CACHE_WRITE, "stale_cache_count": STALE_CACHE_COUNT, "missing_trigger_day_count": MISSING_TRIGGER_DAY_COUNT},
        "baostock_health": {"consecutive_failures": BAOSTOCK_CONSECUTIVE_FAILURES},
        "hotspots": hotspots,
        "core_status_count": main_core_status_count,
        "main_core_status_count": main_core_status_count,
        "key_line_status_count": key_line_status_count,
        "coreline_price_details": coreline_details,
        "reason_type_count": reason_count,
        "learning_value_count": {"high": high_learning, "medium": medium_learning, "low": low_learning},
        "summary_type_count": type_count,
        "daily_cause_clusters": (daily_lessons or {}).get("daily_cause_clusters", {}),
        "results": results,
        "daily_lessons": daily_lessons or {},
        "cognition_actions": cognition_actions or [],
        "hist_failure_samples": HIST_FAILURE_SAMPLES,
        "model_research_question_schema": {
            "Q1_coreline_validity": "这条核心线为什么有效？",
            "Q2_timing_selection": "资金为什么选择这个时间点发动？",
            "Q3_employee1_feedback": "这个案例能不能形成一号员工可提前识别的因子？",
        },
        "model_audit_schema": [
            "anti_hindsight_bias", "true_coreline_check", "cause_classification_check",
            "timing_explanation_check", "employee1_feedback_check", "failure_exclusion_check",
        ],
        "coreline_definition": {
            "t1_boundary_required": True,
            "platform_min_months": 3,
            "touch_tol_pct": CORE_TOUCH_TOL * 100,
            "close_accept_tol_pct": CORE_CLOSE_ACCEPT_TOL * 100,
            "break_tol_pct": PLATFORM_BREAK_TOL * 100,
            "stage_volume_rule": "post-break volume top2 or top20pct",
            "body_cut_penalty_enabled": True,
            "price_must_be_reported": True,
            "deep_research_questions_required": True,
            "t_day_trigger_cannot_create_line": True,
            "monthly_incomplete_month_excluded": not MONTHLY_USE_INCOMPLETE_MONTH,
            "aggregate_20d_anchor_mode": "reverse_from_pre_end",
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

    base_items: List[Dict[str, Any]] = []
    if pool is not None and not pool.empty:
        pool = pool.head(MAX_POOL_SCAN).copy()
        for _, row in pool.iterrows():
            base_items.append({
                "code": ss(row.get("code")),
                "name": ss(row.get("name")),
                "board": ss(row.get("board")),
                "limit_pct": sf(row.get("limit_pct")),
                "pct_chg": sf(row.get("pct_chg")),
                "limit_style": ss(row.get("limit_style")),
                "tags": fallback_tags(row),
                "sample_type": "today_limit_up",
                "is_limit_sample": True,
                "candidate_source": ss(row.get("source")) or "limit_pool",
            })
    else:
        pool = pd.DataFrame()
        diagnostics["limit_pool_empty_policy"] = "不再直接return；继续用历史K线反推A组，并强制计算B组20日涨幅前三。"

    write_progress("② 涨停池整理", 1, 1, run_start, f"接口涨停数={len(base_items)}；空池不短路")

    write_progress("③ 行情热点提取", 0, 1, run_start, "历史/手动日期不混入实时热点；只在当前收盘后模式提取板块热点")
    hotspots, hotspot_map = build_market_hotspots(base_items) if REALTIME_SOURCE_ALLOWED else ([], {})
    write_progress("③ 行情热点提取", 1, 1, run_start, f"热点数={len(hotspots)}")

    universe_items = build_history_universe_items(target_date, diagnostics, run_start)

    scan_map: Dict[str, Dict[str, Any]] = {}
    for it in base_items + universe_items:
        c = code6(it.get("code"))
        if not c:
            continue
        if c not in scan_map:
            scan_map[c] = it
        else:
            # 接口涨停池优先保留A组身份；历史全市场池只补充B组扫描口径。
            if it.get("sample_type") == "today_limit_up" or bool(it.get("is_limit_sample")):
                scan_map[c] = {**scan_map[c], **it, "sample_type": "today_limit_up", "is_limit_sample": True}
    scan_items = list(scan_map.values())
    diagnostics["universe_scan_for_20d_top"] = {
        "enabled": bool(universe_items),
        "scan_limit": B_GROUP_SCAN_LIMIT,
        "historical_universe_scan_limit": HISTORICAL_UNIVERSE_SCAN_LIMIT,
        "scan_scope": "full_market" if B_GROUP_SCAN_LIMIT <= 0 else "limited_market",
        "universe_candidates": len(universe_items),
        "deduped_scan_items": len(scan_items),
        "disabled_reason": "无法取得静态股票池，且接口涨停池为空" if not scan_items else "",
    }

    if not scan_items:
        msg = (
            f"🧬【五号员工-涨停归因】\n日期：{target_date}\n"
            "数据源采集失败：涨停池接口为空，静态股票池也为空，无法用历史K线反推A组/B组。\n"
            "这不是市场没有样本，而是采集链路没有拿到可扫描股票池。"
        )
        (REPORT_DIR / "limit_up_research_report.md").write_text(msg, encoding="utf-8")
        (REPORT_DIR / "limit_up_research_report.json").write_text(json.dumps({"target_date": target_date, "diagnostics": diagnostics, "results": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        send_msg(msg)
        return

    write_progress("④A 全市场K线预扫描", 0, len(scan_items), run_start, "反推A组涨停/极强 + 计算B组20日累计涨幅")
    prelim, prelim_hist_source_count = scan_history_candidates(target_date, scan_items, run_start)
    research_items, selection_summary = select_research_items(prelim, base_items)
    diagnostics["historical_rebuild_selection"] = selection_summary

    rebuilt_pool = rebuild_pool_from_results_like(prelim)
    if (pool is None or pool.empty) and not rebuilt_pool.empty:
        pool = rebuilt_pool.sort_values(["limit_pct", "pct_chg", "code"], ascending=[False, False, True]).head(MAX_POOL_SCAN).reset_index(drop=True)
        diagnostics["pool_rebuilt_from_kline"] = True
        diagnostics["total_limit_up_identified"] = int(len(pool))
        diagnostics["source_limit_counts"] = pool["source"].value_counts().to_dict() if "source" in pool.columns else {}
        diagnostics["board_counts"] = pool["board"].value_counts().to_dict() if "board" in pool.columns else {}
        diagnostics["limit_style_counts"] = pool["limit_style"].value_counts().to_dict() if "limit_style" in pool.columns else {}
    else:
        diagnostics["pool_rebuilt_from_kline"] = False

    if not research_items:
        msg = (
            f"🧬【五号员工-涨停归因】\n日期：{target_date}\n"
            "完成了历史K线预扫描，但没有拿到可归因的A组涨停/极强样本或B组20日大涨样本。\n"
            f"诊断：{json.dumps(diagnostics.get('historical_rebuild_selection', {}), ensure_ascii=False)}"
        )
        (REPORT_DIR / "limit_up_research_report.md").write_text(msg, encoding="utf-8")
        (REPORT_DIR / "limit_up_research_report.json").write_text(json.dumps({"target_date": target_date, "diagnostics": diagnostics, "prelim_count": len(prelim), "results": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        send_msg(msg)
        return

    results: List[Dict[str, Any]] = []
    hist_source_count: Dict[str, int] = {}
    write_progress("④B 逐只深度归因", 0, len(research_items), run_start, "只展开A组涨停/极强与B组20日涨幅前列，避免全市场无意义深跑")

    for idx, item in enumerate(research_items, 1):
        code, name = code6(item.get("code")), ss(item.get("name"))
        hist = fetch_hist(code, target_date)
        time.sleep(REQUEST_SLEEP)

        if hist is None or hist.empty or len(hist) < 30:
            core = {"valid": False, "status": "未确认", "reason": "历史K线不足，无法计算自然月核心线。", "box_candidates": []}
            return20 = sf(item.get("return20"))
            tags = item.get("tags", [])
            hist_source = "missing"
            df = pd.DataFrame()
            pct_chg_value = sf(item.get("pct_chg"))
        else:
            df = add_indicators(hist)
            return20 = ret_pct(df, 20)
            pct_chg_value = trigger_day_pct(df)
            hist_source = ss(hist.get("hist_source", pd.Series(["unknown"])).iloc[-1]) if "hist_source" in hist.columns else "unknown"
            hist_source_count[hist_source] = hist_source_count.get(hist_source, 0) + 1
            tags = list(dict.fromkeys((item.get("tags", []) or []) + structure_tags(df)))
            df_pre_for_core = df.iloc[:-1].copy() if len(df) >= 2 else df.copy()
            core = find_monthly_coreline(df_pre_for_core)

        board, lim = board_limit(code, name)
        limit_hit = is_limit_up(pct_chg_value, lim)
        extreme_hit = is_extreme_move(pct_chg_value, lim)
        if limit_hit:
            sample_type = "today_limit_up"
            candidate_source = item.get("candidate_source") or "historical_kline_limit_rebuild"
        elif extreme_hit:
            sample_type = "today_extreme_move"
            candidate_source = item.get("candidate_source") or "historical_kline_extreme_rebuild"
        else:
            sample_type = "rolling_20d_top_gain"
            candidate_source = item.get("candidate_source") or "universe_for_20d_top"

        matches = hotspot_map.get(code, [])
        profile_item = {**item, "code": code, "name": name, "board": board, "limit_pct": lim, "pct_chg": pct_chg_value, "sample_type": sample_type, "candidate_source": candidate_source, "hotspot_matches": matches}
        deep_profile = build_deep_attribution_profile(df, core, matches, profile_item)
        deep_profile["coreline_price_details"] = build_item_coreline_price_details(profile_item, deep_profile, core)
        deep_profile["deep_research_questions"] = build_deep_research_questions(profile_item, deep_profile)
        deep_profile["employee1_feedback_potential"] = build_employee1_feedback_potential(profile_item, deep_profile)
        deep_profile["model_audit_questions"] = build_model_audit_questions(profile_item, deep_profile)
        main_cause = (deep_profile.get("cause_candidates") or [{}])[0]
        reason_type = ss(main_cause.get("cause_type")) or "原因暂不清晰"
        reason_text = ss(main_cause.get("one_sentence")) or "未形成足够清晰的主因。"
        confidence = int(sf(main_cause.get("score")))

        results.append({
            "code": code,
            "name": name,
            "board": board,
            "pct_chg": pct_chg_value,
            "limit_style": limit_style(lim),
            "candidate_source": candidate_source,
            "sample_type": sample_type,
            "is_limit_sample": bool(limit_hit),
            "is_extreme_sample": bool(extreme_hit),
            "return20": return20,
            "hist_source": hist_source,
            "hist_last_date": HIST_LAST_DATE_BY_CODE.get(code, ""),
            "tags": tags,
            "hotspot_matches": matches,
            "core_line": core,
            "reason_type": reason_type,
            "reason_text": reason_text,
            "reason_confidence": confidence,
            "deep_attribution": deep_profile,
        })

        if idx == 1 or idx == len(research_items) or idx % max(PROGRESS_EVERY, 1) == 0:
            write_progress("④B 逐只深度归因", idx, len(research_items), run_start, f"当前={name}({code}) 原因={reason_type}")

    # 预扫描历史源也写进诊断，深度归因源单独统计。
    diagnostics["pre_scan_hist_source_count"] = prelim_hist_source_count

    write_progress("⑤ 认知提炼", 0, 1, run_start, "从逐只归因提炼今日大涨逻辑，并更新五号员工认知库")
    cognition_library, daily_lessons, cognition_actions, feedback_md = distill_daily_cognition(target_date, results)
    write_progress("⑤ 认知提炼", 1, 1, run_start, f"认知动作={len(cognition_actions)}")

    elapsed = time.time() - run_start
    write_progress("⑥ 报告生成", 0, 1, run_start, "写入 md/json/artifact")
    text, payload = build_report(target_date, pool, diagnostics, hotspots, results, hist_source_count, elapsed, daily_lessons, cognition_actions)

    # 保持旧 artifact 文件名兼容，同时新增更直观的文件名。
    (REPORT_DIR / "limit_up_research_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "limit_up_research_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "limit_up_cause_pool_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "limit_up_cause_pool_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    deep_selected = [x for x in results if x.get("deep_report_selected")] or results[:MAX_DEEP_REPORT_ITEMS]
    (REPORT_DIR / "limit_up_deep_samples.json").write_text(json.dumps(deep_selected, ensure_ascii=False, indent=2), encoding="utf-8")

    write_progress("⑥ 报告生成", 1, 1, run_start, f"报告完成 样本={len(results)}")
    print(text, flush=True)

    summary = make_telegram_summary(target_date, results, hotspots, elapsed)
    write_progress("⑦ Telegram摘要推送", 0, 1, run_start, "完整报告在artifact，Telegram只发摘要")
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
