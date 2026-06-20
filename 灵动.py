# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import sys
import time
import traceback
import warnings
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

warnings.filterwarnings(
    "ignore",
    message="Downcasting object dtype arrays on .fillna, .ffill, .bfill is deprecated.*",
    category=FutureWarning,
)
try:
    pd.set_option("future.no_silent_downcasting", True)
except Exception:
    pass

try:
    import baostock as bs
except Exception:
    bs = None

VERSION = "灵动-v26-confirm-window-touch-unsealed-event-selfcheck3"
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "artifacts"
OUTPUT_CSV = REPORT_DIR / "lingdong_latest.csv"  # 兼容旧workflow：现在等同于 selected，避免广义池污染下游
OUTPUT_ACTIVE_POOL_CSV = REPORT_DIR / "lingdong_active_pool.csv"
OUTPUT_SELECTED_CSV = REPORT_DIR / "lingdong_selected.csv"
OUTPUT_ALL_CSV = REPORT_DIR / "lingdong_all.csv"
OUTPUT_JSON = REPORT_DIR / "lingdong_latest.json"
OUTPUT_MD = REPORT_DIR / "lingdong_report.md"
SELF_CHECK_JSON = REPORT_DIR / "lingdong_self_check.json"
OUTPUT_DIAGNOSTIC_JSON = REPORT_DIR / "lingdong_diagnostics.json"

# 公共日K缓存池：对齐员工三号思路。谁先生成日K缓存，灵动就直接复用。
MAIN_CACHE_DIR = ROOT / "kline_cache"
CACHE_DIRS = [
    MAIN_CACHE_DIR,
    ROOT / "employee5_kline_cache",
    ROOT / "data" / "kline_cache",
    ROOT / "cache" / "kline_cache",
    ROOT.parent / "kline_cache",
]

MAX_STOCKS = int(os.getenv("LINGDONG_MAX_STOCKS", os.getenv("MAX_STOCKS", "0")) or "0")
PROGRESS_EVERY = int(os.getenv("LINGDONG_PROGRESS_EVERY", "200"))
CACHE_SCAN_PROGRESS_EVERY = int(os.getenv("LINGDONG_CACHE_SCAN_PROGRESS_EVERY", "800"))
REPORT_TOP_N = int(os.getenv("LINGDONG_REPORT_TOP_N", "5"))
RECENT_WINDOW_TOP_N = int(os.getenv("LINGDONG_RECENT_WINDOW_TOP_N", "3"))
REPORT_MAX_CHARS = int(os.getenv("LINGDONG_REPORT_MAX_CHARS", "3500"))
REPORT_EXCLUDE_EXTREME_HOT = os.getenv("LINGDONG_REPORT_EXCLUDE_EXTREME_HOT", "1") != "0"
REPORT_MAX_LIMITUP_100 = int(os.getenv("LINGDONG_REPORT_MAX_LIMITUP_100", "15"))
REPORT_MAX_BIG_BULL7_100 = int(os.getenv("LINGDONG_REPORT_MAX_BIG_BULL7_100", "20"))
REPORT_MAX_BIG_YANG5_100 = int(os.getenv("LINGDONG_REPORT_MAX_BIG_YANG5_100", "30"))
REPORT_MAX_BIG_YIN5_100 = int(os.getenv("LINGDONG_REPORT_MAX_BIG_YIN5_100", "16"))

LOOKBACK_DAYS = int(os.getenv("LINGDONG_LOOKBACK_DAYS", "100"))
RECENT_DAYS = int(os.getenv("LINGDONG_RECENT_DAYS", "20"))
MID_DAYS = int(os.getenv("LINGDONG_MID_DAYS", "60"))
MIN_HISTORY_DAYS = int(os.getenv("LINGDONG_MIN_HISTORY_DAYS", "120"))
MIN_CACHE_ROWS = int(os.getenv("LINGDONG_MIN_CACHE_ROWS", str(MIN_HISTORY_DAYS)))

# 默认禁止全市场逐票BaoStock扫。只允许公共缓存；需要补今天时显式打开。
ALLOW_BAOSTOCK_FALLBACK = os.getenv("LINGDONG_ALLOW_BAOSTOCK_FALLBACK", "0") == "1"
# 即使显式打开 BaoStock 补今，也限制补拉数量，避免 5000+ 逐票请求拖慢。
# 0 或负数代表不设上限；默认 500 已足够修复重点过期缓存，不允许误变成全市场慢扫。
REFRESH_LIMIT = int(os.getenv("LINGDONG_REFRESH_LIMIT", "500"))
# 关键修正：补数据只补目标交易日这一根，不从2020或最近10天重拉。
REFRESH_TARGET_DAY_ONLY = os.getenv("LINGDONG_REFRESH_TARGET_DAY_ONLY", "1") != "0"
# 缓存推断真实交易日：若最新日期覆盖达到阈值，优先用最新日期；否则回退到覆盖最多日期。
TARGET_LATEST_MIN_RATIO = float(os.getenv("LINGDONG_TARGET_LATEST_MIN_RATIO", "0.30"))
TARGET_LATEST_MIN_COUNT = int(os.getenv("LINGDONG_TARGET_LATEST_MIN_COUNT", "800"))
QFQ_ADJUSTFLAG = "2"
# 缓存复权口径未知时，默认不把BaoStock前复权单日K线直接拼进旧缓存，避免混用复权/不复权制造假缺口。
ALLOW_UNKNOWN_ADJUST_MERGE = os.getenv("LINGDONG_ALLOW_UNKNOWN_ADJUST_MERGE", "0") == "1"
ALLOW_UNPREFIXED_CACHE_IN_TELEGRAM = os.getenv("LINGDONG_ALLOW_UNPREFIXED_CACHE_IN_TELEGRAM", "0") == "1"

AMOUNT20_LOW = float(os.getenv("LINGDONG_AMOUNT20_LOW", "30000000"))
AMOUNT20_BASIC = float(os.getenv("LINGDONG_AMOUNT20_BASIC", "50000000"))
AMOUNT20_GOOD = float(os.getenv("LINGDONG_AMOUNT20_GOOD", "100000000"))

BIG_BULL7_PCT = float(os.getenv("LINGDONG_BIG_BULL7_PCT", "7.0"))
BIG_YANG5_PCT = float(os.getenv("LINGDONG_BIG_YANG5_PCT", "5.0"))
BIG_YIN5_PCT = float(os.getenv("LINGDONG_BIG_YIN5_PCT", "-5.0"))
GAP_PCT = float(os.getenv("LINGDONG_GAP_PCT", "1.0"))
DEAD_RANGE20_MAX = float(os.getenv("LINGDONG_DEAD_RANGE20_MAX", "2.5"))
DEAD_SMALL_BODY_RATIO_MIN = float(os.getenv("LINGDONG_DEAD_SMALL_BODY_RATIO_MIN", "0.55"))
BAD_BIG_YIN_EXCESS = int(os.getenv("LINGDONG_BAD_BIG_YIN_EXCESS", "2"))
BAD_VOL_LONG_BEAR_20 = int(os.getenv("LINGDONG_BAD_VOL_LONG_BEAR_20", "2"))

# 近10/20/30日活跃分：正向事件按单日互斥取最高分，负向风险可叠加扣分。
ACTIVITY_SCORE_LIMITUP = float(os.getenv("LINGDONG_SCORE_LIMITUP", "5"))
ACTIVITY_SCORE_BIG_BULL7 = float(os.getenv("LINGDONG_SCORE_BIG_BULL7", "4"))
ACTIVITY_SCORE_BIG_YANG5 = float(os.getenv("LINGDONG_SCORE_BIG_YANG5", "2.5"))
ACTIVITY_SCORE_JUMP_SEPARATION = float(os.getenv("LINGDONG_SCORE_JUMP_SEPARATION", "3"))
ACTIVITY_SCORE_SEPARATION = float(os.getenv("LINGDONG_SCORE_SEPARATION", "2.5"))
ACTIVITY_SCORE_STRONG_ENGULF = float(os.getenv("LINGDONG_SCORE_STRONG_ENGULF", "1.5"))
ACTIVITY_SCORE_GAP_UP = float(os.getenv("LINGDONG_SCORE_GAP_UP", "1"))
# 炸板/摸板失败不是天然坏，是“高活跃+高分歧”独立事件；不伪装成普通5%/7%阳。
ACTIVITY_SCORE_LIMIT_FAILED_ATTACK = float(os.getenv("LINGDONG_SCORE_LIMIT_FAILED_ATTACK", "3"))
ACTIVITY_SCORE_LIMIT_TOUCH_UNSEALED_STRONG = float(os.getenv("LINGDONG_SCORE_LIMIT_TOUCH_UNSEALED_STRONG", "3.5"))
ACTIVITY_PENALTY_BIG_YIN5 = float(os.getenv("LINGDONG_PENALTY_BIG_YIN5", "2"))
ACTIVITY_PENALTY_VOL_LONG_BEAR = float(os.getenv("LINGDONG_PENALTY_VOL_LONG_BEAR", "3"))
ACTIVITY_PENALTY_LONG_UPPER = float(os.getenv("LINGDONG_PENALTY_LONG_UPPER", "1.5"))

TARGET_KEYS = [
    "LINGDONG_TARGET_DATE",
    "SELECTION_TRADE_DATE",
    "DATA_GATE_TARGET_DATE",
    "TARGET_TRADE_DATE",
    "LAST_TRADE_DAY_OVERRIDE",
    "REQUIRED_CACHE_DATE",
]

BLOCK_NAME = (
    "指数", "B股指数", "A股指数", "综合指数", "成份指数", "上证指数", "深证成指", "深证综指", "创业板指",
    "科创板指数", "科创50", "科创100", "科创ETF", "科创板ETF",
    "基金", "ETF", "LOF", "REIT", "REITS", "货币", "理财",
    "债", "转债", "国债", "企债", "可转债", "公司债", "债券",
    "期货", "期权", "认购", "认沽", "权证", "CWB",
    "退市", "退", "摘牌",
)
BLOCK_PREFIX = ("*ST", "ST", "S*ST", "SST", "N", "C", "DR")
BLOCK_SUFFIX = ("-U", "－U", "—U", " U", "-W", "－W", "—W", " W")

GOOD_ACTIVE = "灵动充沛"
NORMAL_ACTIVE = "灵动尚可"
DEAD_ACTIVE = "死水无灵"
BAD_ACTIVE = "邪动乱流"
LOW_LIQUIDITY = "灵气枯竭"
DATA_SHORT = "样本不足"
DATA_BAD = "数据异常"

STATUS_ORDER = {
    GOOD_ACTIVE: 0,
    NORMAL_ACTIVE: 1,
    BAD_ACTIVE: 2,
    DEAD_ACTIVE: 3,
    LOW_LIQUIDITY: 4,
    DATA_SHORT: 5,
    DATA_BAD: 6,
}

@dataclass
class StockItem:
    code: str
    bs_code: str
    name: str
    cache_has_exchange_prefix: bool = True


@dataclass
class LingdongHit:
    code: str
    bs_code: str
    name: str
    status: str
    latest_trade_day: str
    amount20: float
    amount60: float
    amount_ratio_20_60: float
    limitup_count_100: int
    big_bull7_count_100: int
    big_yang5_count_100: int
    price_attack7_ex_limitup_count_100: int
    price_attack5_plain_count_100: int
    primary_jump_separation_count_100: int
    primary_separation_line_count_100: int
    primary_strong_bullish_engulf_count_100: int
    primary_big_bull7_count_100: int
    primary_big_yang5_count_100: int
    primary_gap_up_count_100: int
    primary_limit_failed_attack_count_100: int
    big_yin5_count_100: int
    limitup_count_10: int
    big_bull7_count_10: int
    big_yang5_count_10: int
    price_attack7_ex_limitup_count_10: int
    price_attack5_plain_count_10: int
    primary_jump_separation_count_10: int
    primary_separation_line_count_10: int
    primary_strong_bullish_engulf_count_10: int
    primary_big_bull7_count_10: int
    primary_big_yang5_count_10: int
    primary_gap_up_count_10: int
    primary_limit_failed_attack_count_10: int
    big_yin5_count_10: int
    gap_up_count_10: int
    jump_separation_count_10: int
    separation_line_count_10: int
    strong_bullish_engulf_count_10: int
    volume_long_bear_count_10: int
    long_upper_count_10: int
    recent_activity_score_10: float
    limitup_count_20: int
    big_bull7_count_20: int
    big_yang5_count_20: int
    price_attack7_ex_limitup_count_20: int
    price_attack5_plain_count_20: int
    primary_jump_separation_count_20: int
    primary_separation_line_count_20: int
    primary_strong_bullish_engulf_count_20: int
    primary_big_bull7_count_20: int
    primary_big_yang5_count_20: int
    primary_gap_up_count_20: int
    primary_limit_failed_attack_count_20: int
    big_yin5_count_20: int
    gap_up_count_20: int
    jump_separation_count_20: int
    separation_line_count_20: int
    strong_bullish_engulf_count_20: int
    volume_long_bear_count_20_recent: int
    long_upper_count_20: int
    recent_activity_score_20: float
    limitup_count_30: int
    big_bull7_count_30: int
    big_yang5_count_30: int
    price_attack7_ex_limitup_count_30: int
    price_attack5_plain_count_30: int
    primary_jump_separation_count_30: int
    primary_separation_line_count_30: int
    primary_strong_bullish_engulf_count_30: int
    primary_big_bull7_count_30: int
    primary_big_yang5_count_30: int
    primary_gap_up_count_30: int
    primary_limit_failed_attack_count_30: int
    big_yin5_count_30: int
    gap_up_count_30: int
    jump_separation_count_30: int
    separation_line_count_30: int
    strong_bullish_engulf_count_30: int
    volume_long_bear_count_30: int
    long_upper_count_30: int
    recent_activity_score_30: float
    gap_up_count_100: int
    gap_down_count_100: int
    range20_pct: float
    small_body_ratio_60: float
    volume_long_bear_20: int
    long_upper_count_100: int
    trend_efficiency_20: float
    attack_memory: bool
    bad_activity: bool
    dead_activity: bool
    detail: str
    amount_source: str = "raw"
    amount_missing_rate20: float = 0.0
    amount_estimated_rate20: float = 0.0
    volume_unit: str = "unknown"
    adjust_flag: str = "unknown"
    data_quality_flags: str = ""
    data_quality_action: str = "allow"
    limit_close_up_count_100: int = 0
    limit_touch_up_count_100: int = 0
    limit_failed_count_100: int = 0
    last_limitup_age: int = -1
    last_big_attack_age: int = -1
    negative_risk_decay_score: float = 0.0
    compression_seed_candidate: bool = False
    engine_role: str = "Activity Engine / 非买点"
    cache_has_exchange_prefix: bool = True
    pool_role: str = "audit_only"
    tradable_candidate: bool = False
    selected_eligible: bool = False
    not_selected_reason: str = ""
    limit_failed_count_10: int = 0
    limit_failed_count_20: int = 0
    limit_failed_count_30: int = 0
    limit_failed_unrepaired_count_100: int = 0
    limit_failed_repaired_count_100: int = 0
    limit_failed_unrepaired_count_10: int = 0
    limit_failed_unrepaired_count_20: int = 0
    limit_failed_unrepaired_count_30: int = 0
    limit_failed_repaired_count_10: int = 0
    limit_failed_repaired_count_20: int = 0
    limit_failed_repaired_count_30: int = 0
    raw_volume_long_bear_count_100: int = 0
    repaired_volume_long_bear_count_100: int = 0
    destructive_volume_long_bear_count_100: int = 0
    raw_volume_long_bear_count_10: int = 0
    repaired_volume_long_bear_count_10: int = 0
    destructive_volume_long_bear_count_10: int = 0
    raw_volume_long_bear_count_20: int = 0
    repaired_volume_long_bear_count_20: int = 0
    destructive_volume_long_bear_count_20: int = 0
    raw_volume_long_bear_count_30: int = 0
    repaired_volume_long_bear_count_30: int = 0
    destructive_volume_long_bear_count_30: int = 0
    compression_role: str = "dead_activity_exemption_only"
    ret_source_note: str = ""
    primary_limit_touch_unsealed_strong_count_100: int = 0
    primary_limit_touch_unsealed_strong_count_10: int = 0
    primary_limit_touch_unsealed_strong_count_20: int = 0
    primary_limit_touch_unsealed_strong_count_30: int = 0
    limit_failed_pending_count_100: int = 0
    limit_failed_pending_count_10: int = 0
    limit_failed_pending_count_20: int = 0
    limit_failed_pending_count_30: int = 0
    pending_volume_long_bear_count_100: int = 0
    pending_volume_long_bear_count_10: int = 0
    pending_volume_long_bear_count_20: int = 0
    pending_volume_long_bear_count_30: int = 0
    impossible_return_count_100: int = 0
    impossible_return_count_20: int = 0


@dataclass
class ScanStat:
    version: str
    target_date: str
    stock_pool_count: int
    scanned_count: int
    daily_success_count: int
    failed_count: int
    signal_count: int
    data_source: str
    cache_files: int = 0
    cache_hit_count: int = 0
    cache_bad_count: int = 0
    cache_short_count: int = 0
    stale_count: int = 0
    refreshed_count: int = 0
    refresh_failed_count: int = 0
    refresh_skipped_count: int = 0
    baostock_fallback_enabled: bool = False


def bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(str(value).replace(",", "").replace("%", ""))
    except Exception:
        return default




def finite_float(value: Any, default: float = 0.0) -> float:
    """把 NaN/inf 统一压回有限值，避免成交额缺失污染状态和排序。"""
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def safe_mean_nonzero(series: Any) -> float:
    """只对正数、有限值求均值；空样本返回0，不返回NaN。"""
    try:
        vals = pd.to_numeric(pd.Series(series), errors="coerce")
        vals = vals[(vals > 0) & vals.map(lambda x: math.isfinite(float(x)))]
        if vals.empty:
            return 0.0
        return finite_float(vals.mean(), 0.0)
    except Exception:
        return 0.0


def safe_ratio(num: Any, den: Any) -> float:
    n = finite_float(num, 0.0)
    d = finite_float(den, 0.0)
    return finite_float(n / d, 0.0) if n > 0 and d > 0 else 0.0


DIAGNOSTIC_EVENTS: List[Dict[str, Any]] = []
CACHE_EXCHANGE_PREFIX_OK: Dict[str, bool] = {}


def record_diagnostic(stage: str, code: Any = "", message: str = "", error_type: str = "", detail: Any = "") -> None:
    """关键容错不再静默吞掉：统一记录阶段、代码和错误摘要，写入诊断artifact。"""
    try:
        DIAGNOSTIC_EVENTS.append({
            "time": bj_now().strftime("%Y-%m-%d %H:%M:%S"),
            "stage": s(stage),
            "code": s(code),
            "error_type": s(error_type),
            "message": s(message)[:500],
            "detail": s(detail)[:1000],
        })
    except Exception:
        pass


def record_exception(stage: str, code: Any, exc: BaseException, detail: Any = "") -> None:
    record_diagnostic(stage, code, str(exc), type(exc).__name__, detail)


def canonical_adjust_flag(value: Any) -> str:
    raw = s(value).lower().replace(" ", "").replace("-", "_")
    if raw in {"2", "qfq", "前复权", "forward", "forward_adjusted", "fq_before"}:
        return "qfq"
    if raw in {"1", "hfq", "后复权", "backward", "backward_adjusted", "fq_after"}:
        return "hfq"
    if raw in {"3", "0", "raw", "none", "不复权", "未复权", "no", "no_adjust", "unadjusted"}:
        return "raw"
    return "unknown"


def canonical_volume_unit(value: Any) -> str:
    raw = s(value).lower().replace(" ", "")
    if raw in {"share", "shares", "股", "g"}:
        return "share"
    if raw in {"lot", "lots", "手", "shou"}:
        return "lot"
    return "unknown"


def infer_adjust_flag(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "unknown"
    for col in ["adjust_flag", "adjustflag", "复权", "复权类型", "adjust"]:
        if col in df.columns:
            vals = [canonical_adjust_flag(x) for x in df[col].dropna().tolist()]
            vals = [x for x in vals if x != "unknown"]
            if vals:
                # 同一缓存存在混合复权口径时，按unknown处理，禁止无脑拼接。
                return vals[-1] if len(set(vals)) == 1 else "unknown"
    return "unknown"


def infer_volume_unit(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "unknown"
    for col in ["volume_unit", "成交量单位"]:
        if col in df.columns:
            vals = [canonical_volume_unit(x) for x in df[col].dropna().tolist()]
            vals = [x for x in vals if x != "unknown"]
            if vals:
                return vals[-1] if len(set(vals)) == 1 else "unknown"
    try:
        tmp = df.copy()
        for col in ["close", "volume", "amount"]:
            if col not in tmp.columns:
                return "unknown"
            tmp[col] = pd.to_numeric(tmp[col], errors="coerce")
        base = tmp[(tmp["close"] > 0) & (tmp["volume"] > 0) & (tmp["amount"] > 0)].copy()
        if len(base) < 5:
            return "unknown"
        ratio = (base["amount"] / (base["close"] * base["volume"])).replace([math.inf, -math.inf], pd.NA).dropna()
        if ratio.empty:
            return "unknown"
        med = float(ratio.median())
        if 0.2 <= med <= 5.0:
            return "share"
        if 20.0 <= med <= 500.0:
            return "lot"
    except Exception as exc:
        record_exception("infer_volume_unit", "", exc)
    return "unknown"


def volume_unit_multiplier(unit: str) -> float:
    return 100.0 if canonical_volume_unit(unit) == "lot" else 1.0


def amount_source_profile(df: pd.DataFrame) -> str:
    if df is None or df.empty or "amount" not in df.columns:
        return "missing"
    try:
        amt = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
        raw_rate = float((amt > 0).mean()) if len(amt) else 0.0
        if raw_rate >= 0.95:
            return "raw"
        if raw_rate <= 0.05 and "amount_effective" in df.columns and safe_mean_nonzero(df["amount_effective"]) > 0:
            return "estimated"
        if raw_rate <= 0.05:
            return "missing"
        return "mixed"
    except Exception:
        return "unknown"


def data_quality_profile(df: pd.DataFrame) -> Dict[str, Any]:
    unit = infer_volume_unit(df)
    adj = infer_adjust_flag(df)
    src = amount_source_profile(df)
    flags: List[str] = []
    if adj == "unknown":
        flags.append("adjust_unknown")
    if unit == "unknown":
        flags.append("volume_unit_unknown")
    if src in {"missing", "unknown"}:
        flags.append("amount_missing")
    elif src == "estimated":
        flags.append("amount_estimated")
    elif src == "mixed":
        flags.append("amount_mixed")
    return {"adjust_flag": adj, "volume_unit": unit, "amount_source": src, "flags": flags}


def can_merge_fresh_adjusted_bar(existing: pd.DataFrame, fresh: pd.DataFrame) -> Tuple[bool, str]:
    old_adj = infer_adjust_flag(existing)
    new_adj = infer_adjust_flag(fresh)
    if new_adj == "unknown":
        return False, "fresh_adjust_unknown"
    if old_adj == "unknown" and not ALLOW_UNKNOWN_ADJUST_MERGE:
        return False, f"existing_adjust_unknown_vs_{new_adj}"
    if old_adj != "unknown" and old_adj != new_adj:
        return False, f"adjust_mismatch_{old_adj}_vs_{new_adj}"
    return True, "ok"


def code6(value: Any) -> str:
    raw = value.stem if isinstance(value, Path) else s(value)
    match = re.search(r"(\d{6})", raw)
    return match.group(1) if match else ""


def valid_code(code: str) -> bool:
    c = code6(code)
    return c.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4"))


def norm_date(value: Any) -> str:
    raw = (
        s(value)
        .replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
        .replace("_", "-")
    )
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def prev_workday(day: datetime) -> datetime:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def explicit_target_date() -> str:
    for key in TARGET_KEYS:
        value = os.getenv(key)
        if value:
            parsed = norm_date(value)
            if parsed:
                return parsed
    return ""


def target_date() -> str:
    explicit = explicit_target_date()
    if explicit:
        return explicit

    now = bj_now()
    if now.weekday() >= 5 or now.hour < 20 or (now.hour == 20 and now.minute < 35):
        now = prev_workday(now - timedelta(days=1))
    return now.strftime("%Y-%m-%d")


TARGET_DASH = target_date()


def bs_code_of(code: str) -> str:
    raw = s(code).lower()
    m = re.search(r"(?i)(sh|sz|bj)[\._-]?(\d{6})", raw)
    if m:
        return f"{m.group(1).lower()}.{m.group(2)}"
    c = code6(code)
    if c.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh." + c
    if c.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz." + c
    if c.startswith(("8", "4", "920")):
        return "bj." + c
    return ""


def cache_identity(value: Any) -> str:
    """缓存去重键必须保留交易所前缀，避免 sh.000001 指数占掉 sz.000001 股票。"""
    raw = value.stem if isinstance(value, Path) else s(value)
    explicit = bs_code_of(raw)
    if explicit:
        return explicit
    c = code6(raw)
    return bs_code_of(c) if c else ""


def exchange_stock_ok(bs_code: str, code: str) -> bool:
    raw = s(bs_code).lower()
    c = code6(code or bs_code)
    if not c:
        return False

    if raw.startswith("sh."):
        return c.startswith(("600", "601", "603", "605", "688", "689"))
    if raw.startswith("sz."):
        return c.startswith(("000", "001", "002", "003", "300", "301"))
    if raw.startswith("bj."):
        return c.startswith(("8", "4", "920"))

    return bool(bs_code_of(c))


def name_stock_ok(name: str) -> bool:
    raw = s(name).replace(" ", "").replace("　", "")
    upper = raw.upper()
    if not upper or upper in {"名称待补", "NAN", "NONE", "NULL", "-"}:
        return False
    if any(upper.startswith(prefix) for prefix in BLOCK_PREFIX):
        return False
    if any(token in upper for token in BLOCK_SUFFIX):
        return False
    # 科创/创新药等带 -U/-W 后缀的特殊表决权/未盈利标的不进Telegram。
    if upper.endswith(("-U", "－U", "—U", "U", "-W", "－W", "—W", "W")) and any(ch in raw for ch in ["-", "－", "—"]):
        return False
    return not any(word.upper() in upper for word in BLOCK_NAME)


def common_stock_ok(bs_code: str, code: str, name: str) -> bool:
    return exchange_stock_ok(bs_code, code) and name_stock_ok(name)


def ensure_report_dir() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def bs_rows(rs: Any) -> List[List[str]]:
    rows: List[List[str]] = []
    while rs is not None and getattr(rs, "error_code", "0") == "0" and rs.next():
        rows.append(rs.get_row_data())
    return rows


def normalize_hist(df: pd.DataFrame, default_code: str = "") -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    mp = {
        "日期": "date", "交易日期": "date", "date": "date", "time": "date",
        "代码": "code", "股票代码": "code", "证券代码": "code", "code": "code", "symbol": "code",
        "名称": "name", "股票名称": "name", "股票简称": "name", "证券简称": "name", "name": "name", "code_name": "name",
        "开盘": "open", "开盘价": "open", "open": "open",
        "最高": "high", "最高价": "high", "high": "high",
        "最低": "low", "最低价": "low", "low": "low",
        "收盘": "close", "收盘价": "close", "close": "close",
        "成交量": "volume", "volume": "volume", "vol": "volume",
        "成交额": "amount", "amount": "amount",
        "涨跌幅": "pct_chg", "涨幅": "pct_chg", "pct_chg": "pct_chg", "pctChg": "pct_chg",
        "换手率": "turnover", "turn": "turnover", "turnover": "turnover",
        "adjustflag": "adjust_flag", "adjust_flag": "adjust_flag", "复权": "adjust_flag", "复权类型": "adjust_flag", "adjust": "adjust_flag",
        "成交量单位": "volume_unit", "volume_unit": "volume_unit",
        "成交额来源": "amount_source", "amount_source": "amount_source",
    }
    d = df.rename(columns={c: mp.get(str(c), mp.get(str(c).lower(), c)) for c in df.columns}).copy()

    if not {"date", "open", "high", "low", "close"}.issubset(d.columns):
        return pd.DataFrame()

    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]:
        if col in d.columns:
            d[col] = pd.to_numeric(d[col].map(f), errors="coerce").fillna(0.0).astype(float)

    if "volume" not in d.columns:
        d["volume"] = 0.0
    if "amount" not in d.columns:
        d["amount"] = 0.0
    if "pct_chg" not in d.columns:
        d["pct_chg"] = 0.0
    if "turnover" not in d.columns:
        d["turnover"] = 0.0
    if "code" not in d.columns:
        d["code"] = default_code
    if "name" not in d.columns:
        d["name"] = ""
    if "adjust_flag" not in d.columns:
        d["adjust_flag"] = ""
    if "volume_unit" not in d.columns:
        d["volume_unit"] = ""
    if "amount_source" not in d.columns:
        d["amount_source"] = ""

    d["date"] = d["date"].map(norm_date)
    d["code"] = d["code"].map(lambda x: code6(x) or code6(default_code))
    d.loc[d["code"].astype(str).str.len() == 0, "code"] = code6(default_code)
    d["name"] = d["name"].map(s)
    d["adjust_flag"] = d["adjust_flag"].map(canonical_adjust_flag)
    d["volume_unit"] = d["volume_unit"].map(canonical_volume_unit)
    d["amount_source"] = d["amount_source"].map(s)

    d = d[
        (d["date"] != "")
        & (d["open"] > 0)
        & (d["high"] > 0)
        & (d["low"] > 0)
        & (d["close"] > 0)
    ].copy()

    # 不在归一化阶段按 TARGET_DASH 截断。
    # 先完整读取公共缓存，再推断真实目标交易日；评价阶段再按目标日截断。
    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)

    if d.empty:
        return pd.DataFrame()

    if float(d["pct_chg"].abs().sum()) == 0:
        prev = d["close"].shift(1)
        d["pct_chg"] = (d["close"] / prev.replace(0, pd.NA) - 1.0) * 100.0
        d["pct_chg"] = pd.to_numeric(d["pct_chg"], errors="coerce").fillna(0.0).astype(float)

    base_cols = ["date", "code", "name", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover", "adjust_flag", "volume_unit", "amount_source"]
    return d[[c for c in base_cols if c in d.columns]].reset_index(drop=True)


def read_cache_file(path: Path) -> pd.DataFrame:
    code = code6(path)
    try:
        return normalize_hist(pd.read_csv(path), code)
    except Exception as exc:
        record_exception("read_cache_csv", code or path.name, exc, path)
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            rows = obj.get("rows") or obj.get("data") or obj.get("klines") or obj.get("records") or []
            meta = obj.get("meta") or obj.get("metadata") or {}
            df = pd.DataFrame(rows)
            if isinstance(meta, dict):
                if "adjust_flag" not in df.columns and (meta.get("adjust_flag") or meta.get("adjustflag")):
                    df["adjust_flag"] = meta.get("adjust_flag") or meta.get("adjustflag")
                if "volume_unit" not in df.columns and meta.get("volume_unit"):
                    df["volume_unit"] = meta.get("volume_unit")
            return normalize_hist(df, code)
        except Exception as exc2:
            record_exception("read_cache_json", code or path.name, exc2, path)
            return pd.DataFrame()


def iter_cache_files() -> List[Path]:
    """扫描公共日K缓存。

    同一 bs_code 可能在多个缓存目录里同时存在，不能再“先到先得”；
    先按交易所级身份过滤普通A股，再按文件mtime选择最新缓存，避免旧缓存占位。
    """
    seen: Dict[str, Path] = {}
    for directory in CACHE_DIRS:
        if not directory.exists():
            continue
        for p in sorted(directory.glob("*")):
            if p.suffix.lower() not in {".csv", ".json"}:
                continue
            key = cache_identity(p)
            code = code6(key)
            if not (key and valid_code(code) and exchange_stock_ok(key, code)):
                continue
            prefixed = cache_key_has_exchange_prefix(p)
            if not prefixed:
                record_diagnostic("cache_unprefixed_downgraded", code, "无交易所前缀缓存允许进入扫描审计，但默认不得进入Telegram精选", detail=p.name)
            old = seen.get(key)
            if old is None:
                seen[key] = p
                CACHE_EXCHANGE_PREFIX_OK[key] = prefixed
                continue
            old_prefixed = CACHE_EXCHANGE_PREFIX_OK.get(key, cache_key_has_exchange_prefix(old))
            # 同一key冲突时，优先保留带交易所前缀的缓存；只有同级别才按mtime选择。
            if old_prefixed and not prefixed:
                continue
            if prefixed and not old_prefixed:
                seen[key] = p
                CACHE_EXCHANGE_PREFIX_OK[key] = prefixed
                continue
            try:
                if p.stat().st_mtime > old.stat().st_mtime:
                    seen[key] = p
                    CACHE_EXCHANGE_PREFIX_OK[key] = prefixed
            except Exception as exc:
                record_exception("cache_file_mtime", key, exc, f"new={p} old={old}")
    files = list(seen.values())
    if MAX_STOCKS > 0:
        files = files[:MAX_STOCKS]
    return files


def valid_stock_display_name(code: Any, name: Any) -> bool:
    c = code6(code)
    n = s(name)
    if not n:
        return False
    low = n.lower()
    bad = {"nan", "none", "null", "名称待补", "名称缺失", "--", "-"}
    if n in bad or low in bad:
        return False
    digits = code6(n)
    if c and digits == c and re.sub(r"\D", "", n) in {c, "0" + c, "1" + c}:
        return False
    if c and n in {c, f"sh.{c}", f"sz.{c}", f"bj.{c}", f"SH.{c}", f"SZ.{c}", f"BJ.{c}"}:
        return False
    return True


def stock_display_name(code: Any, name: Any) -> str:
    c = code6(code)
    n = s(name)
    return n if valid_stock_display_name(c, n) else "名称待补"


def add_name_mapping(name_map: Dict[str, str], code: Any, name: Any) -> None:
    """名称映射必须防止6位代码串名。

    sh/sz/bj.xxxxxx 交易所级名称可以保留作审计；
    6位 fallback 只能写入确认是普通股票的名称，指数/ETF/债券/ST/退市名称绝不能污染 000001 这类兜底键。
    """
    n = s(name)
    bs_key = bs_code_of(code)
    c = code6(code)
    if not valid_stock_display_name(c, n):
        return
    if bs_key and valid_code(c) and bs_key not in name_map:
        name_map[bs_key] = n
    if valid_code(c) and c not in name_map and common_stock_ok(bs_key, c, n):
        name_map[c] = n


def scan_name_frame(name_map: Dict[str, str], df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return

    code_cols = ["原始代码", "代码", "股票代码", "证券代码", "code", "symbol", "bs_code"]
    name_cols = ["股票中文名称", "名称", "股票名称", "股票简称", "证券简称", "简称", "name", "code_name"]
    usable_code_cols = [c for c in code_cols if c in df.columns]
    usable_name_cols = [c for c in name_cols if c in df.columns]
    if not usable_code_cols or not usable_name_cols:
        return

    for code_col in usable_code_cols:
        for name_col in usable_name_cols:
            pair = df[[code_col, name_col]].dropna(how="all")
            if pair.empty:
                continue
            for _, r in pair.iterrows():
                add_name_mapping(name_map, r.get(code_col, ""), r.get(name_col, ""))


def scan_name_json(name_map: Dict[str, str], path: Path) -> None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        record_exception("scan_name_json", path.name if isinstance(path, Path) else path, exc)
        return

    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                add_name_mapping(name_map, k, v)
            elif isinstance(v, dict):
                code = v.get("代码") or v.get("股票代码") or v.get("原始代码") or v.get("code") or v.get("symbol") or k
                name = v.get("股票中文名称") or v.get("名称") or v.get("股票名称") or v.get("股票简称") or v.get("证券简称") or v.get("name") or v.get("code_name")
                add_name_mapping(name_map, code, name)
            elif isinstance(v, list) and v:
                add_name_mapping(name_map, k, v[0])
    elif isinstance(obj, list):
        scan_name_frame(name_map, pd.DataFrame(obj))


def load_name_map() -> Dict[str, str]:
    """从员工三号同口径的公共文件中补股票名称，避免K线缓存只有代码导致报告名称待补。"""
    name_map: Dict[str, str] = {}
    explicit = s(os.getenv("MODEL_UNIVERSE_FILE"))
    paths: List[Path] = []
    if explicit:
        paths.append(Path(explicit))

    search_dirs: List[Path] = [
        ROOT / "outputs",
        ROOT,
        ROOT.parent / "outputs",
        ROOT / "data",
        ROOT / "cache",
        MAIN_CACHE_DIR,
        REPORT_DIR,
    ]
    search_dirs.extend(CACHE_DIRS)

    seen_dirs: List[Path] = []
    for directory in search_dirs:
        if directory and directory not in seen_dirs:
            seen_dirs.append(directory)

    for base in seen_dirs:
        if not base.exists():
            continue
        paths.extend(sorted(base.glob("model_usable_universe_*.csv"), key=lambda x: x.stat().st_mtime, reverse=True))
        status = base / "_full_history_status.csv"
        if status.exists():
            paths.append(status)
        for pth in sorted(base.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)[:60]:
            low = pth.name.lower()
            if any(key in low for key in ["universe", "stock", "usable", "name", "股票", "status", "mapping"]):
                paths.append(pth)
        for pth in sorted(base.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
            low = pth.name.lower()
            if any(key in low for key in ["universe", "stock", "usable", "name", "股票", "status", "map", "mapping"]):
                paths.append(pth)

    uniq: List[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key not in seen:
            seen.add(key)
            uniq.append(path)

    for path in uniq:
        if not path.exists():
            continue
        suffix = path.suffix.lower()
        if suffix == ".csv":
            try:
                scan_name_frame(name_map, pd.read_csv(path, dtype=str))
            except Exception as exc:
                record_exception("load_name_csv", path.name, exc)
                continue
        elif suffix == ".json":
            scan_name_json(name_map, path)

    if os.getenv("LINGDONG_NAME_BAOSTOCK_FALLBACK", "0") == "1" and bs is not None:
        try:
            lg = bs.login()
            if getattr(lg, "error_code", "") == "0":
                rs = bs.query_all_stock(TARGET_DASH or bj_now().strftime("%Y-%m-%d"))
                df = rs.get_data()
                if df is not None and not df.empty:
                    scan_name_frame(name_map, df)
        except Exception as exc:
            record_exception("name_baostock_fallback", "query_all_stock", exc)
        finally:
            try:
                bs.logout()
            except Exception:
                pass

    return {k: v for k, v in name_map.items() if valid_code(code6(k)) and valid_stock_display_name(code6(k), v)}


def save_cache(code: str, df: pd.DataFrame) -> bool:
    d = normalize_hist(df, code)
    if d.empty or len(d) < MIN_CACHE_ROWS:
        return False
    MAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    d.loc[d["code"].astype(str).str.len() == 0, "code"] = code
    cols = ["date", "code", "name", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover", "adjust_flag", "volume_unit", "amount_source"]
    out = MAIN_CACHE_DIR / f"{code}.csv"
    tmp = out.with_suffix(".csv.tmp")
    d[[c for c in cols if c in d.columns]].to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, out)
    return True


def load_public_cache() -> Tuple[Dict[str, pd.DataFrame], Dict[str, str], Dict[str, int]]:
    files = iter_cache_files()
    hist: Dict[str, pd.DataFrame] = {}
    names: Dict[str, str] = load_name_map()
    stat = {"cache_files": len(files), "cache_hit": 0, "bad": 0, "short": 0}
    start = time.time()

    for idx, path in enumerate(files, 1):
        key = cache_identity(path)
        code = code6(key)
        df = read_cache_file(path)
        if df.empty:
            stat["bad"] += 1
        elif len(df) < MIN_CACHE_ROWS:
            stat["short"] += 1
        else:
            if code and "code" in df.columns:
                df.loc[df["code"].astype(str).str.len() == 0, "code"] = code
            if code and "name" in df.columns:
                names_found = [s(x) for x in df["name"].tolist() if valid_stock_display_name(code, x)]
                if names_found:
                    names[key] = names_found[-1]
                    if common_stock_ok(key, code, names_found[-1]):
                        names.setdefault(code, names_found[-1])
            hist[key] = df
            stat["cache_hit"] += 1

        if idx == 1 or idx % max(1, CACHE_SCAN_PROGRESS_EVERY) == 0 or idx == len(files):
            elapsed = max(time.time() - start, 0.001)
            print(
                f"灵动公共日K缓存读取 {idx}/{len(files)}"
                f"｜命中{stat['cache_hit']}"
                f"｜坏{stat['bad']}"
                f"｜短{stat['short']}"
                f"｜速度{idx / elapsed:.2f}个/秒"
                f"｜当前{key}",
                flush=True,
            )
    return hist, names, stat


def latest_common_cache_trade_day(hist: Dict[str, pd.DataFrame]) -> str:
    """从公共缓存推断真实最近交易日。

    规则：
    1）丢弃异常未来日期，缓存推断目标日不能晚于北京时间今天；
    2）若最新有效日期覆盖达到阈值，优先用最新日期，避免半更新缓存被“昨日覆盖最多”拖慢；
    3）否则回退到覆盖股票最多的日期，避免节假日/错误工作日误判。
    """
    counts: Dict[str, int] = {}
    total = 0
    today = bj_now().strftime("%Y-%m-%d")
    for df in hist.values():
        try:
            d = normalize_hist(df)
            if d.empty:
                continue
            total += 1
            day = s(d.iloc[-1].get("date"))
            if day and day <= today:
                counts[day] = counts.get(day, 0) + 1
        except Exception as exc:
            record_exception("latest_common_cache_trade_day", "cache_df", exc)
            continue
    if not counts:
        return ""
    latest_day = max(counts)
    latest_count = counts.get(latest_day, 0)
    ratio = latest_count / max(total, 1)
    if latest_count >= max(1, int(TARGET_LATEST_MIN_COUNT)) or ratio >= max(0.0, float(TARGET_LATEST_MIN_RATIO)):
        return latest_day
    return sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[0][0]

def resolve_target_date_after_cache(hist: Dict[str, pd.DataFrame]) -> str:
    explicit = explicit_target_date()
    if explicit:
        return explicit
    inferred = latest_common_cache_trade_day(hist)
    if inferred:
        return inferred
    return target_date()


def fetch_target_day_only(code: str, existing: pd.DataFrame, target: str) -> Tuple[pd.DataFrame, bool]:
    """只补目标交易日这一根日K。绝不从2020或全历史重拉。"""
    if bs is None or not target:
        return existing, False

    fields = "date,code,open,high,low,close,volume,amount,pctChg,turn,tradestatus"
    rs = bs.query_history_k_data_plus(
        bs_code_of(code),
        fields,
        start_date=target,
        end_date=target,
        frequency="d",
        adjustflag=QFQ_ADJUSTFLAG,
    )
    rows = bs_rows(rs)
    if not rows:
        return existing, False

    fresh = pd.DataFrame(rows, columns=fields.split(","))
    if "tradestatus" in fresh.columns:
        fresh = fresh[fresh["tradestatus"].map(s).isin({"", "1"})].copy()
    fresh = fresh.rename(columns={"pctChg": "pct_chg", "turn": "turnover"})
    fresh["adjust_flag"] = "qfq"
    fresh["volume_unit"] = "share"
    fresh["amount_source"] = "raw"
    fresh = normalize_hist(fresh, code)
    if fresh.empty:
        return existing, False

    existing_norm = normalize_hist(existing, code)
    ok_merge, reason = can_merge_fresh_adjusted_bar(existing_norm, fresh)
    if not ok_merge:
        record_diagnostic("refresh_adjust_guard", code, "skip target-day merge because adjustment profile is unsafe", "adjust_profile", reason)
        return existing, False

    merged = normalize_hist(pd.concat([existing_norm, fresh], ignore_index=True), code)
    if "adjust_flag" in merged.columns and infer_adjust_flag(merged) == "unknown":
        merged["adjust_flag"] = fresh["adjust_flag"].iloc[-1]
    if "volume_unit" in merged.columns and infer_volume_unit(merged) == "unknown":
        merged["volume_unit"] = fresh["volume_unit"].iloc[-1]
    return merged, bool(not merged.empty and s(merged.iloc[-1].get("date")) == target)


def limit_name_is_st(name: Any) -> bool:
    raw = s(name).replace(" ", "").replace("　", "").upper()
    return bool(raw.startswith(("*ST", "ST", "S*ST", "SST")))


def limit_name_is_special_new(name: Any) -> bool:
    raw = s(name).replace(" ", "").replace("　", "").upper()
    return bool(raw.startswith(("N", "C", "DR")))


def get_limit_touch_threshold(code: str, name: Any = "") -> float:
    """盘中摸板近似阈值。ST按5%制度近似；新股/特殊前缀只做数据风险标记，不伪造真实限幅。"""
    if limit_name_is_st(name):
        return 4.80
    c = code6(code)
    if c.startswith(("300", "301", "688", "689")):
        return 19.3
    if c.startswith(("8", "4", "920")):
        return 29.0
    return 9.3


def get_limit_close_threshold(code: str, name: Any = "") -> float:
    """收盘封住近似阈值，必须严于摸板阈值。ST按5%制度近似。"""
    if limit_name_is_st(name):
        return 4.85
    c = code6(code)
    if c.startswith(("300", "301", "688", "689")):
        return 19.50
    if c.startswith(("8", "4", "920")):
        return 29.50
    return 9.75


def get_limit_threshold(code: str) -> float:
    # 兼容旧调用：默认返回摸板近似阈值；收盘封板必须调用 get_limit_close_threshold。
    return get_limit_touch_threshold(code)


def get_limit_ratio(code: str, name: Any = "") -> float:
    if limit_name_is_st(name):
        return 5.0
    c = code6(code)
    if c.startswith(("300", "301", "688", "689")):
        return 20.0
    if c.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


def round_tick_half_up(value: Any, tick: str = "0.01") -> float:
    try:
        if value is None or pd.isna(value):
            return float("nan")
        q = Decimal(str(tick))
        return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        return float("nan")


def calc_limit_up_price_series(prev_close: pd.Series, code: str, name: Any = "") -> pd.Series:
    """按昨收近似计算理论涨停价，使用 ROUND_HALF_UP，避免银行家舍入造成0.01边界误差。"""
    ratio = 1.0 + get_limit_ratio(code, name) / 100.0
    vals = pd.to_numeric(prev_close, errors="coerce") * ratio
    return vals.map(round_tick_half_up).astype(float)

def normalize_return_pct_columns(d: pd.DataFrame) -> pd.DataFrame:
    """校验 pct_chg 口径，防止 0.10 表示10%却被当成0.10%。"""
    raw = pd.to_numeric(d.get("pct_chg", 0.0), errors="coerce").fillna(0.0).astype(float)
    prev = d["prev_close"].replace(0, pd.NA)
    price_ret = pd.to_numeric((d["close"] / prev - 1.0) * 100.0, errors="coerce")
    price_ret = price_ret.fillna(raw).astype(float)
    scaled_raw = raw * 100.0
    raw_abs = raw.abs()
    price_abs = price_ret.abs()
    raw_diff = (raw - price_ret).abs()
    scaled_diff = (scaled_raw - price_ret).abs()
    # 若原字段像小数比例口径（0.10=10%），且乘100后明显更贴近价格涨幅，则自动缩放。
    scaled_mask = (raw_abs <= 0.80) & (price_abs >= 3.0) & (scaled_diff <= 0.80) & (scaled_diff < raw_diff)
    # 若原字段与价格计算差异过大，以价格计算为准。
    mismatch_mask = (~scaled_mask) & (price_abs >= 1.0) & (raw_diff > pd.concat([pd.Series(1.0, index=d.index), price_abs * 0.35], axis=1).max(axis=1))
    ret = raw.copy()
    ret.loc[scaled_mask] = scaled_raw.loc[scaled_mask]
    ret.loc[mismatch_mask] = price_ret.loc[mismatch_mask]
    d["ret_pct_raw"] = raw
    d["ret_pct_price_calc"] = price_ret
    d["ret_source"] = "raw_pct"
    d.loc[scaled_mask, "ret_source"] = "pct_scaled"
    d.loc[mismatch_mask, "ret_source"] = "price_calc"
    d["ret_pct"] = pd.to_numeric(ret, errors="coerce").fillna(0.0).astype(float)
    return d


def add_lingdong_indicators(df: pd.DataFrame, code: str) -> pd.DataFrame:
    d = normalize_hist(df, code).copy().reset_index(drop=True)
    if d.empty:
        return d

    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if col not in d.columns:
            d[col] = 0.0
        d[col] = pd.to_numeric(d[col], errors="coerce").fillna(0.0).astype(float)

    # 成交额兜底：公共缓存偶尔只有volume没有amount。
    # v21加入成交量单位识别：volume若为“手”，估算成交额必须乘100；单位未知则保守按股处理并打质量标记。
    if "adjust_flag" not in d.columns:
        d["adjust_flag"] = "unknown"
    if "volume_unit" not in d.columns:
        d["volume_unit"] = "unknown"
    if "amount_source" not in d.columns:
        d["amount_source"] = ""
    inferred_unit = infer_volume_unit(d)
    if inferred_unit != "unknown":
        d["volume_unit"] = inferred_unit
    d["amount_raw_missing"] = (d["amount"] <= 0) | ~d["amount"].map(lambda x: math.isfinite(float(x)))
    multiplier = volume_unit_multiplier(inferred_unit)
    estimated_amount = d["close"].clip(lower=0.0) * d["volume"].clip(lower=0.0) * multiplier
    d["amount_effective"] = d["amount"].where(d["amount"] > 0, estimated_amount)
    d["amount_effective"] = pd.to_numeric(d["amount_effective"], errors="coerce").fillna(0.0).astype(float)
    d["amount_estimated"] = d["amount_raw_missing"] & (d["amount_effective"] > 0)
    d["amount_source_effective"] = "raw"
    d.loc[d["amount_estimated"], "amount_source_effective"] = "estimated"
    d.loc[d["amount_effective"] <= 0, "amount_source_effective"] = "missing"

    d["prev_close"] = d["close"].shift(1)
    d.loc[d["prev_close"] <= 0, "prev_close"] = pd.NA
    d = normalize_return_pct_columns(d)

    denom = d["prev_close"].replace(0, pd.NA)
    d["range_pct"] = pd.to_numeric((d["high"] - d["low"]) / denom * 100.0, errors="coerce").fillna(0.0).astype(float)
    d["body_pct_prev"] = pd.to_numeric((d["close"] - d["open"]) / denom * 100.0, errors="coerce").fillna(0.0).astype(float)
    d["abs_body_pct"] = d["body_pct_prev"].abs()
    rng = (d["high"] - d["low"]).replace(0, pd.NA)
    d["close_pos"] = pd.to_numeric((d["close"] - d["low"]) / rng, errors="coerce").fillna(0.5).astype(float)
    d["body_ratio"] = pd.to_numeric((d["close"] - d["open"]).abs() / rng, errors="coerce").fillna(0.0).astype(float)
    d["upper_shadow_ratio"] = pd.to_numeric((d["high"] - d[["open", "close"]].max(axis=1)) / rng, errors="coerce").fillna(0.0).astype(float)
    d["lower_shadow_ratio"] = pd.to_numeric((d[["open", "close"]].min(axis=1) - d["low"]) / rng, errors="coerce").fillna(0.0).astype(float)
    # 极窄幅/一字K线不能默认 close_pos=0.5；否则一字涨停会被误判为摸板失败。
    zero_range = (d["high"] - d["low"]).abs() <= (d["close"].abs().clip(lower=1e-9) * 1e-6)
    d.loc[zero_range & (d["ret_pct"] > 0), "close_pos"] = 1.0
    d.loc[zero_range & (d["ret_pct"] < 0), "close_pos"] = 0.0
    d.loc[zero_range, "upper_shadow_ratio"] = 0.0
    d.loc[zero_range, "lower_shadow_ratio"] = 0.0
    d["is_yang"] = d["close"] > d["open"]
    d["is_yin"] = d["close"] < d["open"]
    d["vol_ma20"] = d["volume"].rolling(20, min_periods=5).mean()
    d["amount_ma20"] = d["amount_effective"].rolling(20, min_periods=5).mean()
    d["ma5"] = d["close"].rolling(5, min_periods=3).mean()
    d["ma10"] = d["close"].rolling(10, min_periods=5).mean()
    d["ma20"] = d["close"].rolling(20, min_periods=8).mean()
    d["ma60"] = d["close"].rolling(60, min_periods=20).mean()
    bbi_parts = [
        d["close"].rolling(3, min_periods=2).mean(),
        d["close"].rolling(6, min_periods=3).mean(),
        d["close"].rolling(12, min_periods=6).mean(),
        d["close"].rolling(24, min_periods=10).mean(),
    ]
    d["bbi"] = pd.concat(bbi_parts, axis=1).mean(axis=1)
    d["near_bbi"] = (pd.to_numeric((d["close"] - d["bbi"]).abs() / d["close"].replace(0, pd.NA), errors="coerce").fillna(9.99) <= 0.045)
    high100 = d["high"].rolling(100, min_periods=20).max()
    low100 = d["low"].rolling(100, min_periods=20).min()
    span100 = (high100 - low100).replace(0, pd.NA)
    d["pos_100"] = pd.to_numeric((d["close"] - low100) / span100, errors="coerce").fillna(0.5).clip(0.0, 1.0)
    high60 = d["high"].rolling(60, min_periods=15).max()
    d["drawdown_60_pct"] = pd.to_numeric((d["close"] / high60.replace(0, pd.NA) - 1.0) * 100.0, errors="coerce").fillna(0.0)
    d["near_pressure_60"] = pd.to_numeric(d["close"] / high60.replace(0, pd.NA), errors="coerce").fillna(0.0) >= 0.96
    name_for_limit = ""
    if "name" in d.columns:
        valid_names = [s(x) for x in d["name"].tolist() if s(x)]
        name_for_limit = valid_names[-1] if valid_names else ""
    d["limit_touch_threshold"] = get_limit_touch_threshold(code, name_for_limit)
    d["limit_close_threshold"] = get_limit_close_threshold(code, name_for_limit)
    d["limit_ratio_pct"] = get_limit_ratio(code, name_for_limit)
    d["limit_special_new_stock_uncertain"] = bool(limit_name_is_special_new(name_for_limit))
    # 对普通A股，超出制度限幅太多多半来自除权/复权口径错误；这类事件不参与股性事件识别。
    d["impossible_return_flag"] = (
        ~d["limit_special_new_stock_uncertain"]
        & (d["prev_close"].fillna(0) > 0)
        & (d["ret_pct"].abs() > (d["limit_ratio_pct"] + 2.0))
    ).fillna(False).astype(bool)
    d["valid_return_event"] = (~d["impossible_return_flag"]).fillna(True).astype(bool)
    d["limit_threshold"] = d["limit_touch_threshold"]  # 兼容旧字段；不能用于收盘封板。
    d["theoretical_limit_up_price"] = calc_limit_up_price_series(d["prev_close"], code, name_for_limit)
    touch_ret = pd.to_numeric((d["high"] / d["prev_close"].replace(0, pd.NA) - 1.0) * 100.0, errors="coerce").fillna(0.0)
    close_near_limit_price = d["close"] >= d["theoretical_limit_up_price"].fillna(10**18) * 0.998
    high_near_limit_price = d["high"] >= d["theoretical_limit_up_price"].fillna(10**18) * 0.998
    d["limit_touch_up"] = (high_near_limit_price | (touch_ret >= d["limit_touch_threshold"])) & d["valid_return_event"]
    # 日K近似涨停质量：摸板可以宽，封板必须严；优先用理论涨停价，避免9.4%强阳误判涨停。
    # 极窄幅/一字涨停优先识别为封住，不能被 close_pos=0.5 误打成炸板。
    d["limit_one_word_like"] = close_near_limit_price & (d["range_pct"] <= 0.03) & d["valid_return_event"]
    d["limit_close_up"] = (
        close_near_limit_price
        & d["valid_return_event"]
        & (d["ret_pct"] >= (d["limit_close_threshold"] - 0.35))
        & (
            d["limit_one_word_like"]
            | ((d["close_pos"] >= 0.72) & (d["upper_shadow_ratio"] <= 0.30))
        )
    )
    d["limit_sealed_like"] = d["limit_close_up"] & (
        d["limit_one_word_like"] | (d["range_pct"] <= 0.05) | (d["upper_shadow_ratio"] <= 0.18) | (d["close_pos"] >= 0.88)
    )
    d["limit_failed"] = (
        d["limit_touch_up"]
        & ~d["limit_close_up"]
        & ((d["close_pos"] <= 0.68) | (d["upper_shadow_ratio"] >= 0.32))
        & ~d["limit_one_word_like"]
    )
    fail_mid = (d["open"] + d["close"]) / 2.0
    fail_bottom = d[["open", "close"]].min(axis=1)
    future3_close_limit = pd.concat([d["close"].shift(-1), d["close"].shift(-2), d["close"].shift(-3)], axis=1)
    future3_count_limit = future3_close_limit.notna().sum(axis=1)
    future3_max_close_limit = future3_close_limit.max(axis=1)
    future3_min_close_limit = future3_close_limit.min(axis=1)
    d["limit_failed_repaired_3d"] = (
        d["limit_failed"]
        & (future3_max_close_limit >= pd.concat([fail_mid, d["close"] * 0.995], axis=1).max(axis=1))
        & (future3_min_close_limit >= fail_bottom * 0.985)
    ).fillna(False).astype(bool)
    d["limit_failed_pending_3d"] = (
        d["limit_failed"]
        & ~d["limit_failed_repaired_3d"]
        & (future3_count_limit < 3)
    ).fillna(False).astype(bool)
    d["limit_failed_unrepaired"] = (
        d["limit_failed"]
        & ~d["limit_failed_repaired_3d"]
        & ~d["limit_failed_pending_3d"]
    ).fillna(False).astype(bool)
    # 兼容旧字段：limit_up 只代表收盘涨停/类封住，不再把摸板失败算作涨停。
    d["limit_up"] = d["limit_close_up"].fillna(False).astype(bool)
    d["big_bull7"] = (
        d["valid_return_event"]
        & (d["ret_pct"] >= BIG_BULL7_PCT)
        & d["is_yang"]
        & (d["close_pos"] >= 0.60)
        & (d["upper_shadow_ratio"] <= 0.45)
        & ~d["limit_failed"]
    )
    d["big_yang5"] = (
        d["valid_return_event"]
        & (d["ret_pct"] >= BIG_YANG5_PCT)
        & d["is_yang"]
        & (d["close_pos"] >= 0.55)
    )
    # 价格攻击层级在K线级别互斥，不再用统计后相减。
    # 涨停是一档；非涨停7%强阳是一档；普通5%阳线是一档。
    d["price_attack7_ex_limitup"] = d["big_bull7"] & ~d["limit_up"]
    d["price_attack5_plain"] = d["big_yang5"] & ~d["limit_up"] & ~d["price_attack7_ex_limitup"]
    # “5%阴线”必须是真阴线；大幅低开后收阳属于“大跌假阳”，不混入5%阴线口径。
    d["big_yin5"] = (
        d["valid_return_event"]
        & d["is_yin"]
        & (
            (d["ret_pct"] <= BIG_YIN5_PCT)
            | (d["body_pct_prev"] <= BIG_YIN5_PCT)
        )
    )
    d["gap_up"] = (d["open"] >= d["high"].shift(1) * (1.0 + GAP_PCT / 100.0)) & d["valid_return_event"]
    d["gap_down"] = (d["open"] <= d["low"].shift(1) * (1.0 - GAP_PCT / 100.0)) & d["valid_return_event"]

    # 大阴线后反转结构：统一以前一日跌幅超过3%的大阴线为基准。
    d["prev_open"] = d["open"].shift(1)
    d["prev_high"] = d["high"].shift(1)
    d["prev_ret_pct"] = d["ret_pct"].shift(1)
    d["prev_body_pct"] = d["body_pct_prev"].shift(1)
    d["prev_close_pos"] = d["close_pos"].shift(1)
    prev_big_yin = (
        (d["close"].shift(1) < d["open"].shift(1))
        & ((d["prev_ret_pct"] <= -3.0) | (d["prev_body_pct"] <= -3.0))
        & (d["prev_body_pct"] <= -1.5)
        & (d["prev_close_pos"] <= 0.55)
    )
    prev_open_denom = d["prev_open"].replace(0, pd.NA)
    open_near_prev_open = pd.to_numeric((d["open"] - d["prev_open"]).abs() / prev_open_denom, errors="coerce").fillna(9.99) <= 0.012

    # 第二日质量过滤：防止长上影、收盘弱、小实体假修复被统计成正向反转结构。
    reversal_body_quality = (d["body_ratio"] >= 0.45) | (d["body_pct_prev"] >= 2.0)
    reversal_quality = (
        (d["close_pos"] >= 0.60)
        & (d["upper_shadow_ratio"] <= 0.45)
        & reversal_body_quality
    )
    # 跳空分手线以“跳空越过昨日大阴供应区并收盘站住”为核心。
    # 一字板/小实体强承接不强行要求普通阳实体，但必须非长上影、收盘不弱。
    jump_quality = (
        ((d["close_pos"] >= 0.60) | (d["range_pct"] <= 0.01))
        & (d["upper_shadow_ratio"] <= 0.45)
        & ((d["close"] >= d["open"] * 0.998) | d["limit_up"])
    )
    # 事件上下文：两根K事件只是Event，不等于可加分Context。
    # 高位近压力且长上影/收盘效率不足时，保留原始事件用于审计，但不进入primary活跃分。
    trend_weak_context = (d["close"] < d["ma20"].fillna(d["close"])) & (d["ma20"].fillna(d["close"]) <= d["ma60"].fillna(d["ma20"].fillna(d["close"])) * 1.01)
    weak_reversal_close = ((d["upper_shadow_ratio"] >= 0.36) | (d["close_pos"] < 0.66) | d["limit_failed"].fillna(False).astype(bool))
    d["reversal_high_trap_context"] = (
        ~d["limit_up"]
        & (d["range_pct"] > 0.05)
        & weak_reversal_close
        & (
            ((d["pos_100"] >= 0.88) & d["near_pressure_60"])
            | ((d["pos_100"] >= 0.82) & d["limit_failed"].fillna(False).astype(bool))
            | ((d["drawdown_60_pct"] <= -10.0) & trend_weak_context & ~d["near_bbi"].fillna(False).astype(bool))
        )
    )
    d["reversal_context_ok"] = (~d["reversal_high_trap_context"]).fillna(True).astype(bool)
    jump_separation = (
        prev_big_yin
        & (d["open"] > d["prev_high"])
        & (d["close"] > d["prev_high"])
        & jump_quality
    )
    separation = (
        prev_big_yin
        & d["is_yang"]
        & open_near_prev_open
        & (d["close"] >= d["prev_open"] * 0.995)
        & (d["close_pos"] >= 0.70)
        & (d["upper_shadow_ratio"] <= 0.45)
        & reversal_body_quality
    )
    strong_engulf = (
        prev_big_yin
        & d["is_yang"]
        & (d["close"] > d["prev_high"])
        & reversal_quality
    )

    # 三类结构互斥：跳空分手线 > 普通分手线 > 强阳包阴。
    d["jump_separation_line"] = jump_separation.fillna(False).astype(bool)
    d["separation_line"] = (separation & ~d["jump_separation_line"]).fillna(False).astype(bool)
    d["strong_bullish_engulf"] = (strong_engulf & ~d["jump_separation_line"] & ~d["separation_line"]).fillna(False).astype(bool)

    # 报告归因与活跃分分开：涨停不重复加分，但不能盖掉“大阴后跳空分手线”等结构事件。
    # 结构事件三者内部互斥；价格攻击/缺口仍避开已出现的涨停或结构事件。
    # v23：所有正向活跃加分统一走 primary_*；高位诱多/炸板弱收盘不只压反转，也压普通5%/7%阳和缺口。
    positive_weak_close = (
        (d["upper_shadow_ratio"] >= 0.36)
        | (d["close_pos"] < 0.66)
        | d["limit_failed"].fillna(False).astype(bool)
    )
    d["positive_high_trap_context"] = (
        ~d["limit_up"].fillna(False).astype(bool)
        & (d["range_pct"] > 0.05)
        & positive_weak_close
        & (
            ((d["pos_100"] >= 0.86) & d["near_pressure_60"])
            | ((d["pos_100"] >= 0.80) & d["limit_failed"].fillna(False).astype(bool))
            | ((d["drawdown_60_pct"] <= -10.0) & trend_weak_context & ~d["near_bbi"].fillna(False).astype(bool))
        )
    ).fillna(False).astype(bool)
    # 复权未知属于股票级/排序级降权，不在日K层面硬砍所有跳空事件；否则老缓存会把真实事件全部压没。
    positive_context_ok = (~d["positive_high_trap_context"]).fillna(True).astype(bool)
    gap_event_ok = positive_context_ok.fillna(False).astype(bool)

    # 炸板/摸板失败是“高活跃 + 高分歧”独立事件：
    # 不能伪装成普通5%/7%阳，也不能简单当坏事。
    # 低中位、收盘仍不弱、或三日内修复的炸板保留活跃分；高位诱多/弱收盘只做风险审计。
    d["primary_limit_failed_attack"] = (
        d["limit_failed"].fillna(False).astype(bool)
        & ((d["close_pos"] >= 0.50) | (d["ret_pct"] >= 3.0) | d["limit_failed_repaired_3d"].fillna(False).astype(bool))
    ).fillna(False).astype(bool)
    # 摸板未封但收盘仍强：不是普通7%阳，也不是失败炸板；单独记为“高活跃+分歧未完全坏”。
    d["limit_touch_unsealed_strong"] = (
        d["limit_touch_up"].fillna(False).astype(bool)
        & ~d["limit_close_up"].fillna(False).astype(bool)
        & ~d["limit_failed"].fillna(False).astype(bool)
        & d["valid_return_event"].fillna(True).astype(bool)
        & (d["ret_pct"] >= BIG_BULL7_PCT)
        & (d["close_pos"] >= 0.68)
        & (d["upper_shadow_ratio"] <= 0.32)
    ).fillna(False).astype(bool)
    d["primary_limit_touch_unsealed_strong"] = (
        d["limit_touch_unsealed_strong"]
        & positive_context_ok.fillna(True).astype(bool)
    ).fillna(False).astype(bool)

    d["primary_jump_separation"] = (d["jump_separation_line"].fillna(False).astype(bool) & gap_event_ok)
    d["primary_separation_line"] = (d["separation_line"].fillna(False).astype(bool) & positive_context_ok)
    d["primary_strong_bullish_engulf"] = (d["strong_bullish_engulf"].fillna(False).astype(bool) & positive_context_ok)
    used = (
        d["limit_up"].fillna(False).astype(bool)
        | d["primary_limit_failed_attack"]
        | d["primary_limit_touch_unsealed_strong"]
        | d["primary_jump_separation"]
        | d["primary_separation_line"]
        | d["primary_strong_bullish_engulf"]
    )
    d["primary_big_bull7"] = (d["price_attack7_ex_limitup"] & positive_context_ok & ~used).fillna(False).astype(bool)
    used = used | d["primary_big_bull7"]
    d["primary_big_yang5"] = (d["price_attack5_plain"] & positive_context_ok & ~used).fillna(False).astype(bool)
    used = used | d["primary_big_yang5"]
    d["primary_gap_up"] = (d["gap_up"] & gap_event_ok & ~used).fillna(False).astype(bool)

    vol_ref = d["vol_ma20"].fillna(float(d["volume"].median()) if len(d) else 0.0)
    d["volume_long_bear"] = (
        d["is_yin"]
        & (d["abs_body_pct"] >= 4.0)
        & (d["close_pos"] <= 0.35)
        & (d["volume"] >= vol_ref * 1.20)
    )
    bear_mid = (d["open"] + d["close"]) / 2.0
    bear_top = d[["open", "close"]].max(axis=1)
    future3_close_bear = pd.concat([d["close"].shift(-1), d["close"].shift(-2), d["close"].shift(-3)], axis=1)
    future3_count_bear = future3_close_bear.notna().sum(axis=1)
    future3_max_close = future3_close_bear.max(axis=1)
    future3_min_close = future3_close_bear.min(axis=1)
    d["volume_long_bear_repaired_3d"] = (
        d["volume_long_bear"]
        & (future3_max_close >= bear_mid)
        & (future3_min_close >= d["low"] * 0.985)
    ).fillna(False).astype(bool)
    d["volume_long_bear_pending_3d"] = (
        d["volume_long_bear"]
        & ~d["volume_long_bear_repaired_3d"]
        & (future3_count_bear < 3)
    ).fillna(False).astype(bool)
    d["volume_long_bear_destructive"] = (
        d["volume_long_bear"]
        & ~d["volume_long_bear_repaired_3d"]
        & ~d["volume_long_bear_pending_3d"]
        & (d["close"] < bear_top)
    ).fillna(False).astype(bool)
    d["long_upper_reversal"] = (
        pd.to_numeric(d["high"] / d["prev_close"].replace(0, pd.NA) - 1.0, errors="coerce").fillna(0.0) >= 0.05
    ) & (d["close_pos"] <= 0.45) & (d["upper_shadow_ratio"] >= 0.45)
    d["small_body_narrow"] = (d["abs_body_pct"] <= 1.2) & (d["range_pct"] <= 3.0)
    return d


def trend_efficiency(d: pd.DataFrame, days: int = 20) -> float:
    if d is None or len(d) < days + 1:
        return 0.0
    seg = d.tail(days + 1).copy().reset_index(drop=True)
    start = f(seg.iloc[0].get("close"))
    end = f(seg.iloc[-1].get("close"))
    if start <= 0 or end <= 0:
        return 0.0
    # 只奖励向上的趋势效率，顺畅下跌不再加分。
    net = max(end / start - 1.0, 0.0)
    rets = pd.to_numeric(seg["close"].pct_change(), errors="coerce").abs().dropna()
    path = float(rets.sum()) if len(rets) else 0.0
    if path <= 0:
        return 0.0
    return round(max(0.0, min(1.0, net / path)), 3)




def _event_age(d: pd.DataFrame, col: str, days: int = 100) -> int:
    if d is None or d.empty or col not in d.columns:
        return -1
    w = d.tail(max(1, int(days))).copy().reset_index(drop=True)
    flags = w[col].fillna(False).astype(bool).tolist()
    for age, flag in enumerate(reversed(flags)):
        if flag:
            return int(age)
    return -1


def decayed_negative_risk_score(d: pd.DataFrame, days: int = 100) -> float:
    """负向风险也必须时间衰减；远端旧伤只能当背景，不能长期压死已修复的股票。"""
    if d is None or d.empty:
        return 0.0
    w = d.tail(max(1, int(days))).copy().reset_index(drop=True)
    n = len(w)
    if n == 0:
        return 0.0
    age = pd.Series(range(n - 1, -1, -1), index=w.index)
    weights = pd.Series(0.10, index=w.index)
    weights[age < 60] = 0.22
    weights[age < 30] = 0.45
    weights[age < 20] = 0.70
    weights[age < 10] = 1.00

    def bcol(col: str) -> pd.Series:
        if col not in w.columns:
            return pd.Series(False, index=w.index)
        return w[col].fillna(False).astype(bool)

    risk_score = (
        bcol("big_yin5").astype(float) * ACTIVITY_PENALTY_BIG_YIN5
        + bcol("volume_long_bear_destructive").astype(float) * ACTIVITY_PENALTY_VOL_LONG_BEAR
        + bcol("long_upper_reversal").astype(float) * ACTIVITY_PENALTY_LONG_UPPER
        + bcol("limit_failed_unrepaired").astype(float) * 2.0
        + bcol("limit_failed_repaired_3d").astype(float) * 0.5
    )
    return round(finite_float((risk_score * weights).sum(), 0.0), 3)


def compression_seed_candidate(d: pd.DataFrame) -> bool:
    """区分真死水与爆发前夜压缩：灵动不活跃不能直接等同垃圾。"""
    if d is None or len(d) < 80:
        return False
    w20 = d.tail(20).copy()
    w60 = d.tail(60).copy()
    amount_col = "amount_effective" if "amount_effective" in d.columns else "amount"
    range20 = finite_float(w20["range_pct"].median(), 0.0) if "range_pct" in w20 else 0.0
    small_ratio = finite_float(w60["small_body_narrow"].mean(), 0.0) if "small_body_narrow" in w60 else 0.0
    amount20 = safe_mean_nonzero(w20[amount_col])
    amount60 = safe_mean_nonzero(w60[amount_col])
    amount_lift = safe_ratio(amount20, amount60)
    near_bbi_ratio = finite_float(w20.get("near_bbi", pd.Series(False, index=w20.index)).astype(bool).mean(), 0.0)
    low_slope_ok = False
    try:
        lows = pd.to_numeric(w60["low"], errors="coerce").dropna()
        if len(lows) >= 40:
            low_slope_ok = float(lows.tail(20).median()) >= float(lows.head(20).median()) * 0.985
    except Exception:
        low_slope_ok = False
    neg_ok = int(w20.get("big_yin5", pd.Series(False, index=w20.index)).sum()) <= 1 and int(w20.get("volume_long_bear_destructive", pd.Series(False, index=w20.index)).sum()) == 0
    return bool(
        range20 >= 1.40
        and range20 <= DEAD_RANGE20_MAX * 1.25
        and small_ratio >= 0.45
        and amount_lift >= 0.85
        and near_bbi_ratio >= 0.35
        and low_slope_ok
        and neg_ok
    )


def data_quality_decision(flags: List[str], amount_estimated_rate20: float, volume_unit: str, amount_source: str, adjust_flag: str) -> Tuple[str, float, List[str]]:
    """数据质量必须参与决策：allow / warn / downgrade / block。"""
    fs = set(flags or [])
    reasons: List[str] = []
    penalty = 0.0
    action = "allow"
    if "amount_unusable" in fs:
        action = "block"; reasons.append("amount_unusable")
    if amount_source in {"missing", "unknown"}:
        action = "block"; reasons.append("amount_source_missing")
    if amount_estimated_rate20 >= 0.80 and canonical_volume_unit(volume_unit) == "unknown":
        action = "block"; reasons.append("amount_estimated_with_unknown_volume_unit")
    elif amount_estimated_rate20 >= 0.50:
        penalty += 4.0; action = "downgrade" if action == "allow" else action; reasons.append("amount_estimated_high")
    if adjust_flag == "unknown":
        penalty += 1.5; action = "warn" if action == "allow" else action; reasons.append("adjust_unknown")
    if canonical_volume_unit(volume_unit) == "unknown" and amount_estimated_rate20 > 0:
        penalty += 2.0; action = "downgrade" if action in {"allow", "warn"} else action; reasons.append("volume_unit_unknown")
    if "cache_unprefixed" in fs:
        penalty += 2.5; action = "downgrade" if action in {"allow", "warn"} else action; reasons.append("cache_unprefixed")
    if "impossible_return_recent" in fs:
        penalty += 8.0; action = "block"; reasons.append("impossible_return_recent")
    elif "impossible_return" in fs:
        penalty += 4.0; action = "downgrade" if action in {"allow", "warn"} else action; reasons.append("impossible_return")
    if "special_new_limit_uncertain" in fs:
        penalty += 3.0; action = "downgrade" if action in {"allow", "warn"} else action; reasons.append("special_new_limit_uncertain")
    return action, round(penalty, 3), reasons


def cache_key_has_exchange_prefix(value: Any) -> bool:
    raw = value.stem if isinstance(value, Path) else s(value)
    return bool(re.search(r"(?i)^(sh|sz|bj)[\._-]?\d{6}$", raw.strip()))

def decayed_activity_memory_score(d: pd.DataFrame, days: int = 100) -> float:
    """历史攻击记忆必须时间衰减：近期攻击最值钱，远端攻击只保留背景权重。"""
    if d is None or d.empty:
        return 0.0
    w = d.tail(max(1, int(days))).copy().reset_index(drop=True)
    n = len(w)
    if n == 0:
        return 0.0
    age = pd.Series(range(n - 1, -1, -1), index=w.index)
    weights = pd.Series(0.15, index=w.index)
    weights[age < 60] = 0.30
    weights[age < 30] = 0.55
    weights[age < 20] = 0.75
    weights[age < 10] = 1.00

    def bcol(col: str) -> pd.Series:
        if col not in w.columns:
            return pd.Series(False, index=w.index)
        return w[col].fillna(False).astype(bool)

    # 与近端活跃分保持一致：同一交易日正向事件只取最高分，避免涨停+跳分重复抬高历史记忆。
    pos_scores = pd.DataFrame(index=w.index)
    pos_scores["limitup"] = bcol("limit_up").astype(float) * ACTIVITY_SCORE_LIMITUP
    pos_scores["big_bull7"] = bcol("primary_big_bull7").astype(float) * ACTIVITY_SCORE_BIG_BULL7
    pos_scores["big_yang5"] = bcol("primary_big_yang5").astype(float) * ACTIVITY_SCORE_BIG_YANG5
    pos_scores["jump_separation"] = bcol("primary_jump_separation").astype(float) * ACTIVITY_SCORE_JUMP_SEPARATION
    pos_scores["separation_line"] = bcol("primary_separation_line").astype(float) * ACTIVITY_SCORE_SEPARATION
    pos_scores["strong_bullish_engulf"] = bcol("primary_strong_bullish_engulf").astype(float) * ACTIVITY_SCORE_STRONG_ENGULF
    pos_scores["gap_up"] = bcol("primary_gap_up").astype(float) * ACTIVITY_SCORE_GAP_UP
    pos_scores["limit_failed_attack"] = bcol("primary_limit_failed_attack").astype(float) * ACTIVITY_SCORE_LIMIT_FAILED_ATTACK
    pos_scores["limit_touch_unsealed_strong"] = bcol("primary_limit_touch_unsealed_strong").astype(float) * ACTIVITY_SCORE_LIMIT_TOUCH_UNSEALED_STRONG
    event_score = pos_scores.max(axis=1) if not pos_scores.empty else pd.Series(0.0, index=w.index)
    return round(finite_float((event_score * weights).sum(), 0.0), 3)


def cooled_rebuild_after_extreme_hot(hit: LingdongHit) -> bool:
    """极端妖动不是一刀切：历史妖动后若已冷却、波动收缩、近端风险低，可保留为重新启动观察。"""
    last_attack_age = int(getattr(hit, "last_big_attack_age", -1) or -1)
    last_limit_age = int(getattr(hit, "last_limitup_age", -1) or -1)
    cooling_ok = (last_attack_age >= 10 or last_attack_age < 0) and (last_limit_age >= 10 or last_limit_age < 0)
    return bool(
        cooling_ok
        and float(hit.range20_pct or 0.0) <= 4.8
        and float(hit.small_body_ratio_60 or 0.0) >= 0.38
        and int(hit.big_yin5_count_20 or 0) <= 1
        and int(hit.long_upper_count_20 or 0) <= 1
        and int(hit.volume_long_bear_count_20_recent or 0) == 0
        and float(hit.recent_activity_score_20 or 0.0) > 0
        and float(hit.trend_efficiency_20 or 0.0) >= 0.20
    )


def lingdong_activity_rank_score(hit: LingdongHit) -> float:
    limitup, price7, price5 = price_attack_counts(hit, 100)
    return (
        min(limitup, 6) * 5.0
        + min(price7, 8) * 3.2
        + min(price5, 12) * 1.4
        + min(hit.primary_jump_separation_count_100, 3) * 1.2
        + min(hit.primary_separation_line_count_100, 3) * 0.8
        + min(hit.primary_strong_bullish_engulf_count_100, 3) * 0.5
        + min(hit.primary_gap_up_count_100, 6) * 0.6
        + min(getattr(hit, "primary_limit_failed_attack_count_100", 0), 6) * 2.3
        + min(getattr(hit, "primary_limit_touch_unsealed_strong_count_100", 0), 6) * 2.6
    )


def trade_quality_proxy_score(hit: LingdongHit) -> float:
    """灵动不是买点模型，但排序不能只看活跃；用已有字段拆出轻量交易质量代理。"""
    recent20 = max(float(hit.recent_activity_score_20 or 0.0), 0.0)
    recent30 = max(float(hit.recent_activity_score_30 or 0.0), 0.0)
    liquidity = min(max(finite_float(hit.amount20, 0.0) / 1e8, 0.0), 8.0)
    amount_lift = min(max(finite_float(hit.amount_ratio_20_60, 0.0), 0.0), 3.0)
    up_efficiency = max(finite_float(hit.trend_efficiency_20, 0.0), 0.0)
    moderate_volatility = 1.0 if 2.0 <= finite_float(hit.range20_pct, 0.0) <= 8.0 else 0.0
    risk_drag = min(float(getattr(hit, "negative_risk_decay_score", 0.0) or 0.0), 18.0) * 0.55
    risk_drag += min(int(getattr(hit, "limit_failed_unrepaired_count_100", getattr(hit, "limit_failed_count_100", 0)) or 0), 6) * 1.2
    risk_drag += min(int(getattr(hit, "limit_failed_repaired_count_100", 0) or 0), 6) * 0.25
    if getattr(hit, "data_quality_action", "allow") == "downgrade":
        risk_drag += 4.0
    elif getattr(hit, "data_quality_action", "allow") == "warn":
        risk_drag += 1.5
    elif getattr(hit, "data_quality_action", "allow") == "block":
        risk_drag += 99.0
    return (
        min(recent20, 20.0) * 0.35
        + min(recent30, 30.0) * 0.15
        + liquidity * 1.5
        + amount_lift * 2.0
        + up_efficiency * 5.0
        + moderate_volatility * 1.0
        - risk_drag
    )

def window_activity_counts(d: pd.DataFrame, days: int) -> Dict[str, Any]:
    w = d.tail(max(1, int(days))).copy() if d is not None and not d.empty else pd.DataFrame()
    if w.empty:
        return {
            "limitup": 0, "limit_close_up": 0, "limit_touch_up": 0, "limit_failed": 0, "limit_failed_unrepaired": 0, "limit_failed_repaired": 0, "big_bull7": 0, "big_yang5": 0, "price_attack7_ex_limitup": 0, "price_attack5_plain": 0, "big_yin5": 0,
            "gap_up": 0, "gap_down": 0, "long_upper": 0, "vol_long_bear": 0,
            "jump_separation": 0, "separation_line": 0, "strong_bullish_engulf": 0,
            "primary_jump_separation": 0, "primary_separation_line": 0, "primary_strong_bullish_engulf": 0,
            "primary_big_bull7": 0, "primary_big_yang5": 0, "primary_gap_up": 0, "primary_limit_failed_attack": 0, "primary_limit_touch_unsealed_strong": 0,
            "raw_vol_long_bear": 0, "repaired_vol_long_bear": 0, "destructive_vol_long_bear": 0, "pending_vol_long_bear": 0,
            "limit_failed_pending": 0, "impossible_return": 0,
            "score": 0.0,
        }

    def cnt(col: str) -> int:
        return int(w[col].sum()) if col in w.columns else 0

    limitup = cnt("limit_up")
    limit_close_up = cnt("limit_close_up")
    limit_touch_up = cnt("limit_touch_up")
    limit_failed = cnt("limit_failed")
    limit_failed_unrepaired = cnt("limit_failed_unrepaired") if "limit_failed_unrepaired" in w.columns else limit_failed
    limit_failed_repaired = cnt("limit_failed_repaired_3d")
    limit_failed_pending = cnt("limit_failed_pending_3d")
    big_bull7 = cnt("big_bull7")
    big_yang5 = cnt("big_yang5")
    price7_ex_limitup = cnt("price_attack7_ex_limitup")
    price5_plain = cnt("price_attack5_plain")
    big_yin5 = cnt("big_yin5")
    gap_up = cnt("gap_up")
    gap_down = cnt("gap_down")
    long_upper = cnt("long_upper_reversal")
    vol_long_bear = cnt("volume_long_bear_destructive")
    jump_sep = cnt("jump_separation_line")
    sep = cnt("separation_line")
    strong_engulf = cnt("strong_bullish_engulf")
    primary_jump_sep = cnt("primary_jump_separation")
    primary_sep = cnt("primary_separation_line")
    primary_strong_engulf = cnt("primary_strong_bullish_engulf")
    primary_bull7 = cnt("primary_big_bull7")
    primary_yang5 = cnt("primary_big_yang5")
    primary_gap_up = cnt("primary_gap_up")
    primary_limit_failed_attack = cnt("primary_limit_failed_attack")
    primary_limit_touch_unsealed_strong = cnt("primary_limit_touch_unsealed_strong")
    raw_vol_long_bear = cnt("volume_long_bear")
    repaired_vol_long_bear = cnt("volume_long_bear_repaired_3d")
    destructive_vol_long_bear = cnt("volume_long_bear_destructive")
    pending_vol_long_bear = cnt("volume_long_bear_pending_3d")
    impossible_return = cnt("impossible_return_flag")

    # 近期活跃榜只衡量近端股性/攻击强度，不直接等同买点。
    # 正向事件单日互斥取最高分，避免涨停同时叠加7%阳/5%阳/缺口导致妖股霸榜。
    pos_scores = pd.DataFrame(index=w.index)
    pos_scores["limitup"] = w.get("limit_up", False).astype(float) * ACTIVITY_SCORE_LIMITUP
    pos_scores["big_bull7"] = w.get("primary_big_bull7", False).astype(float) * ACTIVITY_SCORE_BIG_BULL7
    pos_scores["big_yang5"] = w.get("primary_big_yang5", False).astype(float) * ACTIVITY_SCORE_BIG_YANG5
    pos_scores["jump_separation"] = w.get("primary_jump_separation", False).astype(float) * ACTIVITY_SCORE_JUMP_SEPARATION
    pos_scores["separation_line"] = w.get("primary_separation_line", False).astype(float) * ACTIVITY_SCORE_SEPARATION
    pos_scores["strong_bullish_engulf"] = w.get("primary_strong_bullish_engulf", False).astype(float) * ACTIVITY_SCORE_STRONG_ENGULF
    pos_scores["gap_up"] = w.get("primary_gap_up", False).astype(float) * ACTIVITY_SCORE_GAP_UP
    pos_scores["limit_failed_attack"] = w.get("primary_limit_failed_attack", False).astype(float) * ACTIVITY_SCORE_LIMIT_FAILED_ATTACK
    pos_scores["limit_touch_unsealed_strong"] = w.get("primary_limit_touch_unsealed_strong", False).astype(float) * ACTIVITY_SCORE_LIMIT_TOUCH_UNSEALED_STRONG
    positive_score = float(pos_scores.max(axis=1).sum()) if not pos_scores.empty else 0.0

    negative_score = (
        big_yin5 * ACTIVITY_PENALTY_BIG_YIN5
        + vol_long_bear * ACTIVITY_PENALTY_VOL_LONG_BEAR
        + long_upper * ACTIVITY_PENALTY_LONG_UPPER
        + limit_failed_unrepaired * 2.0
        + limit_failed_pending * 0.75
        + limit_failed_repaired * 0.25
    )
    score = positive_score - negative_score

    return {
        "limitup": limitup,
        "limit_close_up": limit_close_up,
        "limit_touch_up": limit_touch_up,
        "limit_failed": limit_failed,
        "limit_failed_unrepaired": limit_failed_unrepaired,
        "limit_failed_repaired": limit_failed_repaired,
        "limit_failed_pending": limit_failed_pending,
        "big_bull7": big_bull7,
        "big_yang5": big_yang5,
        "price_attack7_ex_limitup": price7_ex_limitup,
        "price_attack5_plain": price5_plain,
        "big_yin5": big_yin5,
        "gap_up": gap_up,
        "gap_down": gap_down,
        "long_upper": long_upper,
        "vol_long_bear": vol_long_bear,
        "jump_separation": jump_sep,
        "separation_line": sep,
        "strong_bullish_engulf": strong_engulf,
        "primary_jump_separation": primary_jump_sep,
        "primary_separation_line": primary_sep,
        "primary_strong_bullish_engulf": primary_strong_engulf,
        "primary_big_bull7": primary_bull7,
        "primary_big_yang5": primary_yang5,
        "primary_gap_up": primary_gap_up,
        "primary_limit_failed_attack": primary_limit_failed_attack,
        "primary_limit_touch_unsealed_strong": primary_limit_touch_unsealed_strong,
        "raw_vol_long_bear": raw_vol_long_bear,
        "repaired_vol_long_bear": repaired_vol_long_bear,
        "destructive_vol_long_bear": destructive_vol_long_bear,
        "pending_vol_long_bear": pending_vol_long_bear,
        "impossible_return": impossible_return,
        "score": round(float(score), 3),
    }


def evaluate_lingdong(df: pd.DataFrame, item: StockItem, target: str = TARGET_DASH) -> LingdongHit:
    d0 = normalize_hist(df, item.code)
    if target and not d0.empty:
        d0 = d0[d0["date"] <= target].copy().reset_index(drop=True)
    if d0.empty or len(d0) < MIN_HISTORY_DAYS:
        return LingdongHit(
            code=item.code, bs_code=item.bs_code, name=item.name, status=DATA_SHORT,
            latest_trade_day=s(d0.iloc[-1].get("date")) if not d0.empty else "",
            amount20=0.0, amount60=0.0, amount_ratio_20_60=0.0,
            limitup_count_100=0, big_bull7_count_100=0, big_yang5_count_100=0, price_attack7_ex_limitup_count_100=0, price_attack5_plain_count_100=0,
            primary_jump_separation_count_100=0, primary_separation_line_count_100=0, primary_strong_bullish_engulf_count_100=0,
            primary_big_bull7_count_100=0, primary_big_yang5_count_100=0, primary_gap_up_count_100=0, primary_limit_failed_attack_count_100=0, big_yin5_count_100=0,
            limitup_count_10=0, big_bull7_count_10=0, big_yang5_count_10=0, price_attack7_ex_limitup_count_10=0, price_attack5_plain_count_10=0,
            primary_jump_separation_count_10=0, primary_separation_line_count_10=0, primary_strong_bullish_engulf_count_10=0,
            primary_big_bull7_count_10=0, primary_big_yang5_count_10=0, primary_gap_up_count_10=0, primary_limit_failed_attack_count_10=0, big_yin5_count_10=0, gap_up_count_10=0, jump_separation_count_10=0, separation_line_count_10=0, strong_bullish_engulf_count_10=0, volume_long_bear_count_10=0, long_upper_count_10=0, recent_activity_score_10=0.0,
            limitup_count_20=0, big_bull7_count_20=0, big_yang5_count_20=0, price_attack7_ex_limitup_count_20=0, price_attack5_plain_count_20=0,
            primary_jump_separation_count_20=0, primary_separation_line_count_20=0, primary_strong_bullish_engulf_count_20=0,
            primary_big_bull7_count_20=0, primary_big_yang5_count_20=0, primary_gap_up_count_20=0, primary_limit_failed_attack_count_20=0, big_yin5_count_20=0, gap_up_count_20=0, jump_separation_count_20=0, separation_line_count_20=0, strong_bullish_engulf_count_20=0, volume_long_bear_count_20_recent=0, long_upper_count_20=0, recent_activity_score_20=0.0,
            limitup_count_30=0, big_bull7_count_30=0, big_yang5_count_30=0, price_attack7_ex_limitup_count_30=0, price_attack5_plain_count_30=0,
            primary_jump_separation_count_30=0, primary_separation_line_count_30=0, primary_strong_bullish_engulf_count_30=0,
            primary_big_bull7_count_30=0, primary_big_yang5_count_30=0, primary_gap_up_count_30=0, primary_limit_failed_attack_count_30=0, big_yin5_count_30=0, gap_up_count_30=0, jump_separation_count_30=0, separation_line_count_30=0, strong_bullish_engulf_count_30=0, volume_long_bear_count_30=0, long_upper_count_30=0, recent_activity_score_30=0.0,
            gap_up_count_100=0, gap_down_count_100=0, range20_pct=0.0, small_body_ratio_60=0.0,
            volume_long_bear_20=0, long_upper_count_100=0, trend_efficiency_20=0.0,
            attack_memory=False, bad_activity=False, dead_activity=False, detail="日K样本不足",
        )

    d = add_lingdong_indicators(d0, item.code)
    if d.empty or len(d) < MIN_HISTORY_DAYS:
        return LingdongHit(
            code=item.code, bs_code=item.bs_code, name=item.name, status=DATA_SHORT,
            latest_trade_day=s(d.iloc[-1].get("date")) if not d.empty else "",
            amount20=0.0, amount60=0.0, amount_ratio_20_60=0.0,
            limitup_count_100=0, big_bull7_count_100=0, big_yang5_count_100=0, price_attack7_ex_limitup_count_100=0, price_attack5_plain_count_100=0,
            primary_jump_separation_count_100=0, primary_separation_line_count_100=0, primary_strong_bullish_engulf_count_100=0,
            primary_big_bull7_count_100=0, primary_big_yang5_count_100=0, primary_gap_up_count_100=0, primary_limit_failed_attack_count_100=0, big_yin5_count_100=0,
            limitup_count_10=0, big_bull7_count_10=0, big_yang5_count_10=0, price_attack7_ex_limitup_count_10=0, price_attack5_plain_count_10=0,
            primary_jump_separation_count_10=0, primary_separation_line_count_10=0, primary_strong_bullish_engulf_count_10=0,
            primary_big_bull7_count_10=0, primary_big_yang5_count_10=0, primary_gap_up_count_10=0, primary_limit_failed_attack_count_10=0, big_yin5_count_10=0, gap_up_count_10=0, jump_separation_count_10=0, separation_line_count_10=0, strong_bullish_engulf_count_10=0, volume_long_bear_count_10=0, long_upper_count_10=0, recent_activity_score_10=0.0,
            limitup_count_20=0, big_bull7_count_20=0, big_yang5_count_20=0, price_attack7_ex_limitup_count_20=0, price_attack5_plain_count_20=0,
            primary_jump_separation_count_20=0, primary_separation_line_count_20=0, primary_strong_bullish_engulf_count_20=0,
            primary_big_bull7_count_20=0, primary_big_yang5_count_20=0, primary_gap_up_count_20=0, primary_limit_failed_attack_count_20=0, big_yin5_count_20=0, gap_up_count_20=0, jump_separation_count_20=0, separation_line_count_20=0, strong_bullish_engulf_count_20=0, volume_long_bear_count_20_recent=0, long_upper_count_20=0, recent_activity_score_20=0.0,
            limitup_count_30=0, big_bull7_count_30=0, big_yang5_count_30=0, price_attack7_ex_limitup_count_30=0, price_attack5_plain_count_30=0,
            primary_jump_separation_count_30=0, primary_separation_line_count_30=0, primary_strong_bullish_engulf_count_30=0,
            primary_big_bull7_count_30=0, primary_big_yang5_count_30=0, primary_gap_up_count_30=0, primary_limit_failed_attack_count_30=0, big_yin5_count_30=0, gap_up_count_30=0, jump_separation_count_30=0, separation_line_count_30=0, strong_bullish_engulf_count_30=0, volume_long_bear_count_30=0, long_upper_count_30=0, recent_activity_score_30=0.0,
            gap_up_count_100=0, gap_down_count_100=0, range20_pct=0.0, small_body_ratio_60=0.0,
            volume_long_bear_20=0, long_upper_count_100=0, trend_efficiency_20=0.0,
            attack_memory=False, bad_activity=False, dead_activity=False, detail="日K有效样本不足",
        )

    w100 = d.tail(LOOKBACK_DAYS).copy()
    w60 = d.tail(MID_DAYS).copy()
    w20 = d.tail(RECENT_DAYS).copy()
    a10 = window_activity_counts(d, 10)
    a20 = window_activity_counts(d, 20)
    a30 = window_activity_counts(d, 30)
    a100 = window_activity_counts(d, LOOKBACK_DAYS)

    amount_col = "amount_effective" if "amount_effective" in d.columns else "amount"
    amount20 = safe_mean_nonzero(w20[amount_col]) if len(w20) else 0.0
    amount60 = safe_mean_nonzero(w60[amount_col]) if len(w60) else 0.0
    amount_ratio = safe_ratio(amount20, amount60)
    amount_missing_rate20 = float(w20["amount_raw_missing"].mean()) if len(w20) and "amount_raw_missing" in w20.columns else 0.0
    amount_estimated_rate20 = float(w20["amount_estimated"].mean()) if len(w20) and "amount_estimated" in w20.columns else 0.0
    profile = data_quality_profile(d)
    amount_source = amount_source_profile(w20)
    volume_unit = profile.get("volume_unit", "unknown")
    adjust_flag = profile.get("adjust_flag", "unknown")
    data_quality_flags = list(profile.get("flags", []))
    if not getattr(item, "cache_has_exchange_prefix", True):
        data_quality_flags.append("cache_unprefixed")
    if amount_missing_rate20 >= 0.95 and amount20 <= 0:
        data_quality_flags.append("amount_unusable")
    if amount_estimated_rate20 >= 0.95:
        data_quality_flags.append("amount_estimated_20d")
    impossible_return_100_pre = int(d.tail(LOOKBACK_DAYS).get("impossible_return_flag", pd.Series(False, index=d.tail(LOOKBACK_DAYS).index)).sum()) if not d.empty else 0
    impossible_return_20_pre = int(d.tail(RECENT_DAYS).get("impossible_return_flag", pd.Series(False, index=d.tail(RECENT_DAYS).index)).sum()) if not d.empty else 0
    special_uncertain_pre = bool(d.tail(LOOKBACK_DAYS).get("limit_special_new_stock_uncertain", pd.Series(False, index=d.tail(LOOKBACK_DAYS).index)).astype(bool).any()) if not d.empty else False
    if impossible_return_100_pre > 0:
        data_quality_flags.append("impossible_return")
    if impossible_return_20_pre > 0:
        data_quality_flags.append("impossible_return_recent")
    if special_uncertain_pre:
        data_quality_flags.append("special_new_limit_uncertain")
    data_quality_action, data_quality_penalty, data_quality_reasons = data_quality_decision(
        data_quality_flags, amount_estimated_rate20, volume_unit, amount_source, adjust_flag
    )
    if data_quality_reasons:
        data_quality_flags.extend([f"quality_{x}" for x in data_quality_reasons])

    limitups = int(a100["limitup"])
    big_bull7 = int(a100["big_bull7"])
    big_yang5 = int(a100["big_yang5"])
    price7_ex_limitup_100 = int(a100["price_attack7_ex_limitup"])  # raw审计字段
    price5_plain_100 = int(a100["price_attack5_plain"])  # raw审计字段
    primary_price7_100 = int(a100["primary_big_bull7"])
    primary_price5_100 = int(a100["primary_big_yang5"])
    big_yin5 = int(a100["big_yin5"])
    gap_up = int(a100["gap_up"])
    gap_down = int(a100["gap_down"])
    range20 = float(w20["range_pct"].median()) if len(w20) else 0.0
    small_body_ratio = float(w60["small_body_narrow"].mean()) if len(w60) else 0.0
    vol_long_bear20 = int(w20["volume_long_bear_destructive"].sum()) if "volume_long_bear_destructive" in w20.columns else int(w20["volume_long_bear"].sum())
    long_upper100 = int(w100["long_upper_reversal"].sum())
    eff20 = trend_efficiency(d, RECENT_DAYS)
    limit_close_up_100 = int(a100.get("limit_close_up", limitups))
    limit_touch_up_100 = int(a100.get("limit_touch_up", limitups))
    limit_failed_100 = int(a100.get("limit_failed", 0))
    limit_failed_unrepaired_100 = int(a100.get("limit_failed_unrepaired", limit_failed_100))
    limit_failed_repaired_100 = int(a100.get("limit_failed_repaired", 0))
    last_limitup_age = _event_age(d, "limit_up", LOOKBACK_DAYS)
    last_big_attack_age = min([x for x in [
        _event_age(d, "limit_up", LOOKBACK_DAYS),
        _event_age(d, "primary_big_bull7", LOOKBACK_DAYS),
        _event_age(d, "primary_big_yang5", LOOKBACK_DAYS),
        _event_age(d, "primary_jump_separation", LOOKBACK_DAYS),
        _event_age(d, "primary_separation_line", LOOKBACK_DAYS),
        _event_age(d, "primary_strong_bullish_engulf", LOOKBACK_DAYS),
        _event_age(d, "primary_gap_up", LOOKBACK_DAYS),
        _event_age(d, "primary_limit_failed_attack", LOOKBACK_DAYS),
        _event_age(d, "primary_limit_touch_unsealed_strong", LOOKBACK_DAYS),
    ] if x >= 0], default=-1)
    negative_risk_decay = decayed_negative_risk_score(d, LOOKBACK_DAYS)
    compression_seed = compression_seed_candidate(d)

    primary_reversal_100 = int(a100["primary_jump_separation"]) + int(a100["primary_separation_line"]) + int(a100["primary_strong_bullish_engulf"])
    primary_gap_up_100 = int(a100["primary_gap_up"])
    primary_limit_failed_attack_100 = int(a100.get("primary_limit_failed_attack", 0))
    primary_limit_touch_unsealed_strong_100 = int(a100.get("primary_limit_touch_unsealed_strong", 0))
    limit_failed_pending_100 = int(a100.get("limit_failed_pending", 0))
    impossible_return_100 = int(a100.get("impossible_return", 0))
    impossible_return_20 = int(a20.get("impossible_return", 0))
    exclusive_attack_100 = (
        limitups + primary_price7_100 + primary_price5_100 + primary_reversal_100
        + primary_gap_up_100 + primary_limit_failed_attack_100 + primary_limit_touch_unsealed_strong_100
    )

    # 状态分类统一使用K线级互斥后的事件层；历史攻击记忆必须时间衰减。
    activity_memory_score = decayed_activity_memory_score(d, LOOKBACK_DAYS)
    attack_memory = bool(
        activity_memory_score >= 4.5
        or (limitups >= 1 and float(a30.get("score", 0.0) or 0.0) > 0)
        or (primary_price7_100 >= 2 and activity_memory_score >= 3.5)
        or (primary_price5_100 >= 4 and activity_memory_score >= 3.5)
        or (primary_reversal_100 >= 1 and activity_memory_score >= 2.5)
        or (primary_gap_up_100 >= 2 and activity_memory_score >= 2.5)
        or (primary_limit_failed_attack_100 >= 1 and activity_memory_score >= 2.5)
        or (primary_limit_touch_unsealed_strong_100 >= 1 and activity_memory_score >= 2.5)
    )
    recent_active_core = bool(
        float(a20.get("score", 0.0) or 0.0) > 0
        or float(a30.get("score", 0.0) or 0.0) > 3
        or int(a10.get("limitup", 0) or 0) > 0
        or int(a10.get("primary_jump_separation", 0) or 0) > 0
        or int(a10.get("primary_separation_line", 0) or 0) > 0
        or int(a10.get("primary_strong_bullish_engulf", 0) or 0) > 0
        or int(a10.get("primary_limit_failed_attack", 0) or 0) > 0
        or int(a10.get("primary_limit_touch_unsealed_strong", 0) or 0) > 0
    )
    high_attack_high_yin = bool(exclusive_attack_100 >= 8 and big_yin5 >= 6 and negative_risk_decay >= 4.0 and (big_yin5 / max(exclusive_attack_100, 1)) >= 0.65)
    recent_negative_cluster = bool(
        int(a30.get("big_yin5", 0) or 0)
        + int(a30.get("vol_long_bear", 0) or 0)
        + int(a30.get("long_upper", 0) or 0)
        + int(a30.get("limit_failed_unrepaired", 0) or 0) >= 5
    )
    bad_activity = bool(
        (negative_risk_decay >= 8.0 and big_yin5 > exclusive_attack_100 + BAD_BIG_YIN_EXCESS)
        or high_attack_high_yin
        or vol_long_bear20 >= BAD_VOL_LONG_BEAR_20
        or (long_upper100 >= 6 and exclusive_attack_100 <= big_yin5 and negative_risk_decay >= 8.0)
        or recent_negative_cluster
    )
    dead_activity = bool(
        limitups == 0
        and primary_price7_100 < 2
        and primary_price5_100 < 3
        and primary_reversal_100 == 0
        and primary_gap_up_100 < 2
        and range20 < DEAD_RANGE20_MAX
        and small_body_ratio >= DEAD_SMALL_BODY_RATIO_MIN
        and not compression_seed
    )

    reasons: List[str] = []
    # 数据口径硬拦截优先于低流动性；否则坏数据会被误归因为“灵气枯竭”。
    if data_quality_action == "block":
        status = DATA_BAD
        reasons.append("数据质量硬拦截：" + ",".join(data_quality_reasons))
    elif amount20 < AMOUNT20_LOW:
        status = LOW_LIQUIDITY
        reasons.append(f"20日均成交额{amount20/1e8:.2f}亿低于底线")
    elif bad_activity:
        status = BAD_ACTIVE
        if big_yin5 > exclusive_attack_100 + BAD_BIG_YIN_EXCESS:
            reasons.append(f"100日大阴{big_yin5}次明显多于互斥攻击{exclusive_attack_100}次")
        if high_attack_high_yin:
            reasons.append(f"高攻击高大阴并存：互斥攻击{exclusive_attack_100}次/5%阴{big_yin5}次")
        if vol_long_bear20 >= BAD_VOL_LONG_BEAR_20:
            reasons.append(f"20日放量长阴{vol_long_bear20}次")
        if long_upper100 >= 6 and exclusive_attack_100 <= big_yin5:
            reasons.append(f"100日长上影冲高回落{long_upper100}次")
        if recent_negative_cluster:
            reasons.append("近30日负向波动簇偏多")
    elif attack_memory and recent_active_core and amount20 >= AMOUNT20_BASIC:
        status = GOOD_ACTIVE
        reasons.append("具备攻击记忆且近端仍有活性")
    elif attack_memory and amount20 >= AMOUNT20_BASIC:
        status = NORMAL_ACTIVE
        reasons.append("历史攻击记忆存在，但近30日活性不足")
    elif dead_activity:
        status = DEAD_ACTIVE
        reasons.append("缺少攻击记忆且近期振幅/实体偏窄")
    else:
        status = NORMAL_ACTIVE
        reasons.append("普通可交易活性")

    if attack_memory:
        reasons.append(f"衰减攻击记忆{activity_memory_score:.1f}｜封板{limitups}次/摸板{limit_touch_up_100}次/炸板{limit_failed_100}次/主非停7%阳{primary_price7_100}次/主普通5%阳{primary_price5_100}次/主缺口{primary_gap_up_100}次/炸板攻击{primary_limit_failed_attack_100}次/摸板强收未封{primary_limit_touch_unsealed_strong_100}次")
    if compression_seed:
        reasons.append("窄幅压缩但具备蓄势特征，不按真死水处理")
    if limit_failed_pending_100 > 0:
        reasons.append(f"炸板等待确认{limit_failed_pending_100}次")
    if impossible_return_100 > 0:
        reasons.append(f"异常涨跌幅/疑似复权断层{impossible_return_100}次")
    if negative_risk_decay > 0:
        reasons.append(f"衰减负向风险{negative_risk_decay:.1f}")
    if data_quality_action != "allow":
        reasons.append(f"数据质量动作{data_quality_action}｜惩罚{data_quality_penalty:.1f}")
    if amount_estimated_rate20 > 0:
        reasons.append(f"20日成交额估算占比{amount_estimated_rate20:.0%}｜量单位{volume_unit}")
    elif amount_missing_rate20 > 0:
        reasons.append(f"20日成交额缺失占比{amount_missing_rate20:.0%}")
    if adjust_flag == "unknown":
        reasons.append("复权口径未知")
    if volume_unit == "unknown" and amount_estimated_rate20 > 0:
        reasons.append("成交量单位未知，成交额估算保守")
    if amount_ratio > 0:
        reasons.append(f"20/60日成交额比{amount_ratio:.2f}")
    reasons.append(f"20日中位振幅{range20:.2f}%")
    reasons.append(f"60日小实体窄振幅比例{small_body_ratio:.0%}")
    reasons.append(f"上涨效率20日{eff20:.2f}")
    latest_day = s(d.iloc[-1].get("date"))
    if target and latest_day != target:
        reasons.append(f"缓存未覆盖目标日，当前按{latest_day}股性参考")

    return LingdongHit(
        code=item.code,
        bs_code=item.bs_code,
        name=item.name,
        status=status,
        latest_trade_day=latest_day,
        amount20=round(amount20, 2),
        amount60=round(amount60, 2),
        amount_ratio_20_60=round(amount_ratio, 3),
        limitup_count_100=limitups,
        big_bull7_count_100=big_bull7,
        big_yang5_count_100=big_yang5,
        price_attack7_ex_limitup_count_100=price7_ex_limitup_100,
        price_attack5_plain_count_100=price5_plain_100,
        primary_jump_separation_count_100=int(a100["primary_jump_separation"]),
        primary_separation_line_count_100=int(a100["primary_separation_line"]),
        primary_strong_bullish_engulf_count_100=int(a100["primary_strong_bullish_engulf"]),
        primary_big_bull7_count_100=int(a100["primary_big_bull7"]),
        primary_big_yang5_count_100=int(a100["primary_big_yang5"]),
        primary_gap_up_count_100=int(a100["primary_gap_up"]),
        primary_limit_failed_attack_count_100=primary_limit_failed_attack_100,
        primary_limit_touch_unsealed_strong_count_100=primary_limit_touch_unsealed_strong_100,
        big_yin5_count_100=big_yin5,
        limitup_count_10=int(a10["limitup"]),
        big_bull7_count_10=int(a10["big_bull7"]),
        big_yang5_count_10=int(a10["big_yang5"]),
        price_attack7_ex_limitup_count_10=int(a10["price_attack7_ex_limitup"]),
        price_attack5_plain_count_10=int(a10["price_attack5_plain"]),
        primary_jump_separation_count_10=int(a10["primary_jump_separation"]),
        primary_separation_line_count_10=int(a10["primary_separation_line"]),
        primary_strong_bullish_engulf_count_10=int(a10["primary_strong_bullish_engulf"]),
        primary_big_bull7_count_10=int(a10["primary_big_bull7"]),
        primary_big_yang5_count_10=int(a10["primary_big_yang5"]),
        primary_gap_up_count_10=int(a10["primary_gap_up"]),
        primary_limit_failed_attack_count_10=int(a10.get("primary_limit_failed_attack", 0)),
        primary_limit_touch_unsealed_strong_count_10=int(a10.get("primary_limit_touch_unsealed_strong", 0)),
        big_yin5_count_10=int(a10["big_yin5"]),
        gap_up_count_10=int(a10["gap_up"]),
        jump_separation_count_10=int(a10["jump_separation"]),
        separation_line_count_10=int(a10["separation_line"]),
        strong_bullish_engulf_count_10=int(a10["strong_bullish_engulf"]),
        volume_long_bear_count_10=int(a10["vol_long_bear"]),
        long_upper_count_10=int(a10["long_upper"]),
        recent_activity_score_10=round(float(a10["score"]), 3),
        limitup_count_20=int(a20["limitup"]),
        big_bull7_count_20=int(a20["big_bull7"]),
        big_yang5_count_20=int(a20["big_yang5"]),
        price_attack7_ex_limitup_count_20=int(a20["price_attack7_ex_limitup"]),
        price_attack5_plain_count_20=int(a20["price_attack5_plain"]),
        primary_jump_separation_count_20=int(a20["primary_jump_separation"]),
        primary_separation_line_count_20=int(a20["primary_separation_line"]),
        primary_strong_bullish_engulf_count_20=int(a20["primary_strong_bullish_engulf"]),
        primary_big_bull7_count_20=int(a20["primary_big_bull7"]),
        primary_big_yang5_count_20=int(a20["primary_big_yang5"]),
        primary_gap_up_count_20=int(a20["primary_gap_up"]),
        primary_limit_failed_attack_count_20=int(a20.get("primary_limit_failed_attack", 0)),
        primary_limit_touch_unsealed_strong_count_20=int(a20.get("primary_limit_touch_unsealed_strong", 0)),
        big_yin5_count_20=int(a20["big_yin5"]),
        gap_up_count_20=int(a20["gap_up"]),
        jump_separation_count_20=int(a20["jump_separation"]),
        separation_line_count_20=int(a20["separation_line"]),
        strong_bullish_engulf_count_20=int(a20["strong_bullish_engulf"]),
        volume_long_bear_count_20_recent=int(a20["vol_long_bear"]),
        long_upper_count_20=int(a20["long_upper"]),
        recent_activity_score_20=round(float(a20["score"]), 3),
        limitup_count_30=int(a30["limitup"]),
        big_bull7_count_30=int(a30["big_bull7"]),
        big_yang5_count_30=int(a30["big_yang5"]),
        price_attack7_ex_limitup_count_30=int(a30["price_attack7_ex_limitup"]),
        price_attack5_plain_count_30=int(a30["price_attack5_plain"]),
        primary_jump_separation_count_30=int(a30["primary_jump_separation"]),
        primary_separation_line_count_30=int(a30["primary_separation_line"]),
        primary_strong_bullish_engulf_count_30=int(a30["primary_strong_bullish_engulf"]),
        primary_big_bull7_count_30=int(a30["primary_big_bull7"]),
        primary_big_yang5_count_30=int(a30["primary_big_yang5"]),
        primary_gap_up_count_30=int(a30["primary_gap_up"]),
        primary_limit_failed_attack_count_30=int(a30.get("primary_limit_failed_attack", 0)),
        primary_limit_touch_unsealed_strong_count_30=int(a30.get("primary_limit_touch_unsealed_strong", 0)),
        big_yin5_count_30=int(a30["big_yin5"]),
        gap_up_count_30=int(a30["gap_up"]),
        jump_separation_count_30=int(a30["jump_separation"]),
        separation_line_count_30=int(a30["separation_line"]),
        strong_bullish_engulf_count_30=int(a30["strong_bullish_engulf"]),
        volume_long_bear_count_30=int(a30["vol_long_bear"]),
        long_upper_count_30=int(a30["long_upper"]),
        recent_activity_score_30=round(float(a30["score"]), 3),
        gap_up_count_100=gap_up,
        gap_down_count_100=gap_down,
        range20_pct=round(range20, 3),
        small_body_ratio_60=round(small_body_ratio, 3),
        volume_long_bear_20=vol_long_bear20,
        long_upper_count_100=long_upper100,
        trend_efficiency_20=eff20,
        attack_memory=attack_memory,
        bad_activity=bad_activity,
        dead_activity=dead_activity,
        detail="；".join(reasons),
        amount_source=amount_source,
        amount_missing_rate20=round(float(amount_missing_rate20), 3),
        amount_estimated_rate20=round(float(amount_estimated_rate20), 3),
        volume_unit=volume_unit,
        adjust_flag=adjust_flag,
        data_quality_flags="|".join(sorted(set(data_quality_flags))),
        data_quality_action=data_quality_action,
        limit_close_up_count_100=limit_close_up_100,
        limit_touch_up_count_100=limit_touch_up_100,
        limit_failed_count_100=limit_failed_100,
        last_limitup_age=last_limitup_age,
        last_big_attack_age=last_big_attack_age,
        negative_risk_decay_score=round(float(negative_risk_decay), 3),
        compression_seed_candidate=bool(compression_seed),
        cache_has_exchange_prefix=bool(getattr(item, "cache_has_exchange_prefix", True)),
        limit_failed_count_10=int(a10.get("limit_failed", 0)),
        limit_failed_count_20=int(a20.get("limit_failed", 0)),
        limit_failed_count_30=int(a30.get("limit_failed", 0)),
        limit_failed_unrepaired_count_100=limit_failed_unrepaired_100,
        limit_failed_repaired_count_100=limit_failed_repaired_100,
        limit_failed_pending_count_100=limit_failed_pending_100,
        limit_failed_unrepaired_count_10=int(a10.get("limit_failed_unrepaired", a10.get("limit_failed", 0))),
        limit_failed_unrepaired_count_20=int(a20.get("limit_failed_unrepaired", a20.get("limit_failed", 0))),
        limit_failed_unrepaired_count_30=int(a30.get("limit_failed_unrepaired", a30.get("limit_failed", 0))),
        limit_failed_repaired_count_10=int(a10.get("limit_failed_repaired", 0)),
        limit_failed_repaired_count_20=int(a20.get("limit_failed_repaired", 0)),
        limit_failed_repaired_count_30=int(a30.get("limit_failed_repaired", 0)),
        raw_volume_long_bear_count_100=int(a100.get("raw_vol_long_bear", 0)),
        repaired_volume_long_bear_count_100=int(a100.get("repaired_vol_long_bear", 0)),
        destructive_volume_long_bear_count_100=int(a100.get("destructive_vol_long_bear", 0)),
        raw_volume_long_bear_count_10=int(a10.get("raw_vol_long_bear", 0)),
        repaired_volume_long_bear_count_10=int(a10.get("repaired_vol_long_bear", 0)),
        destructive_volume_long_bear_count_10=int(a10.get("destructive_vol_long_bear", 0)),
        raw_volume_long_bear_count_20=int(a20.get("raw_vol_long_bear", 0)),
        repaired_volume_long_bear_count_20=int(a20.get("repaired_vol_long_bear", 0)),
        destructive_volume_long_bear_count_20=int(a20.get("destructive_vol_long_bear", 0)),
        raw_volume_long_bear_count_30=int(a30.get("raw_vol_long_bear", 0)),
        repaired_volume_long_bear_count_30=int(a30.get("repaired_vol_long_bear", 0)),
        destructive_volume_long_bear_count_30=int(a30.get("destructive_vol_long_bear", 0)),
        compression_role="dead_activity_exemption_only",
        ret_source_note="|".join(sorted(set([x for x in d.get("ret_source", pd.Series([], dtype=str)).tail(20).astype(str).tolist() if x != "raw_pct"]))),
    )


def is_report_signal(hit: LingdongHit) -> bool:
    # Telegram正式报告只推真正有攻击记忆的“灵动充沛”。
    # “灵动尚可”只保留在JSON统计/全量结果里，避免消息过长和普通票刷屏。
    return hit.status == GOOD_ACTIVE


def sort_hits(hits: List[LingdongHit]) -> List[LingdongHit]:
    """全量CSV/active_pool排序也必须使用互斥事件，不再沿用原始重叠字段。"""
    return sorted(
        hits,
        key=lambda x: (
            STATUS_ORDER.get(x.status, 99),
            -report_rank_key(x)[0],
            -max(float(x.recent_activity_score_20 or 0.0), float(x.recent_activity_score_30 or 0.0)),
            -x.amount_ratio_20_60,
            -x.amount20,
            x.bs_code,
        ),
    )


def _telegram_safe_text(lines: List[str], max_chars: int = REPORT_MAX_CHARS) -> str:
    limit = max(1200, min(int(max_chars), 3900))
    out: List[str] = []
    total = 0
    for line in lines:
        item = s(line)
        add_len = len(item) + (1 if out else 0)
        if out and total + add_len > limit:
            out.append("……消息已截断，完整明细见 CSV/JSON artifact")
            break
        out.append(item)
        total += add_len
    return "\n".join(out).strip()



def is_extreme_hot_for_report(hit: LingdongHit) -> bool:
    """报告Top5排除极端妖动残留。

    使用K线级互斥事件，不再用原始 big_bull7/big_yang5 重叠字段，避免一根涨停被重复计算后误杀。
    """
    if not REPORT_EXCLUDE_EXTREME_HOT:
        return False
    exclusive_attack = (
        int(hit.limitup_count_100 or 0)
        + int(hit.primary_big_bull7_count_100 or 0)
        + int(hit.primary_big_yang5_count_100 or 0)
        + int(hit.primary_jump_separation_count_100 or 0)
        + int(hit.primary_separation_line_count_100 or 0)
        + int(hit.primary_strong_bullish_engulf_count_100 or 0)
        + int(hit.primary_gap_up_count_100 or 0)
        + int(getattr(hit, "primary_limit_failed_attack_count_100", 0) or 0)
        + int(getattr(hit, "primary_limit_touch_unsealed_strong_count_100", 0) or 0)
    )
    reversal_total = (
        int(hit.primary_jump_separation_count_100 or 0)
        + int(hit.primary_separation_line_count_100 or 0)
        + int(hit.primary_strong_bullish_engulf_count_100 or 0)
    )
    extreme_flag = bool(
        int(hit.limitup_count_100 or 0) >= REPORT_MAX_LIMITUP_100
        or exclusive_attack >= (REPORT_MAX_BIG_BULL7_100 + 8)
        or int(hit.primary_big_yang5_count_100 or 0) >= REPORT_MAX_BIG_YANG5_100
        or int(hit.big_yin5_count_100 or 0) >= REPORT_MAX_BIG_YIN5_100
        or (exclusive_attack >= 18 and int(hit.big_yin5_count_100 or 0) >= 10)
        or (reversal_total >= 8 and int(hit.long_upper_count_100 or 0) >= 8)
    )
    if not extreme_flag:
        return False
    if cooled_rebuild_after_extreme_hot(hit):
        return False
    return True

def price_attack_counts(hit: LingdongHit, days: int) -> Tuple[int, int, int]:
    """返回可加分的primary价格攻击层级：涨停、主非涨停7%阳、主普通5%阳。raw字段只用于审计。"""
    suffix = str(days)
    limitup = int(getattr(hit, f"limitup_count_{suffix}", 0) or 0)
    bull7_ex_limit = int(getattr(hit, f"primary_big_bull7_count_{suffix}", 0) or 0)
    yang5_plain = int(getattr(hit, f"primary_big_yang5_count_{suffix}", 0) or 0)
    return limitup, bull7_ex_limit, yang5_plain


def is_target_fresh(hit: LingdongHit) -> bool:
    return bool((not TARGET_DASH) or s(hit.latest_trade_day) == TARGET_DASH)


def telegram_name_ok(hit: LingdongHit) -> bool:
    # 名称缺失的缓存票保留在CSV/JSON，但不进Telegram，避免ST/异常标的漏过滤。
    return valid_stock_display_name(hit.code, hit.name)


def telegram_stock_ok(hit: LingdongHit) -> bool:
    # Telegram只展示确认是普通A股且名称无ST/退/ETF/指数/债券/特殊前后缀的标的。
    return common_stock_ok(hit.bs_code, hit.code, hit.name)


def data_quality_eligible(hit: LingdongHit) -> bool:
    return getattr(hit, "data_quality_action", "allow") != "block"


def cache_prefix_eligible(hit: LingdongHit) -> bool:
    return bool(getattr(hit, "cache_has_exchange_prefix", True) or ALLOW_UNPREFIXED_CACHE_IN_TELEGRAM)


def telegram_eligible(hit: LingdongHit) -> bool:
    return is_target_fresh(hit) and telegram_name_ok(hit) and telegram_stock_ok(hit) and data_quality_eligible(hit) and cache_prefix_eligible(hit)


def primary_event_counts(hit: LingdongHit, days: int) -> Tuple[int, int, int, int, int, int, int, int, int]:
    suffix = str(days)
    return (
        int(getattr(hit, f"limitup_count_{suffix}", 0) or 0),
        int(getattr(hit, f"primary_jump_separation_count_{suffix}", 0) or 0),
        int(getattr(hit, f"primary_separation_line_count_{suffix}", 0) or 0),
        int(getattr(hit, f"primary_strong_bullish_engulf_count_{suffix}", 0) or 0),
        int(getattr(hit, f"primary_big_bull7_count_{suffix}", 0) or 0),
        int(getattr(hit, f"primary_big_yang5_count_{suffix}", 0) or 0),
        int(getattr(hit, f"primary_gap_up_count_{suffix}", 0) or 0),
        int(getattr(hit, f"primary_limit_failed_attack_count_{suffix}", 0) or 0),
        int(getattr(hit, f"primary_limit_touch_unsealed_strong_count_{suffix}", 0) or 0),
    )



def primary_event_note(hit: LingdongHit, days: int) -> str:
    _, jump, sep, engulf, bull7, yang5, gap, fail_attack, touch_unsealed = primary_event_counts(hit, days)
    parts: List[str] = []
    if jump:
        parts.append(f"主跳分{jump}")
    if sep:
        parts.append(f"主分手{sep}")
    if engulf:
        parts.append(f"主强包{engulf}")
    if bull7:
        parts.append(f"主7%阳{bull7}")
    if yang5:
        parts.append(f"主5%阳{yang5}")
    if gap:
        parts.append(f"主缺口{gap}")
    if fail_attack:
        parts.append(f"炸板攻击{fail_attack}")
    return "｜" + "｜".join(parts) if parts else ""

def has_recent_report_activity(hit: LingdongHit) -> bool:
    """灵动精选必须近期仍有活性，避免100日历史活跃但最近熄火的票进入Telegram精选。"""
    score20 = float(hit.recent_activity_score_20 or 0.0)
    score30 = float(hit.recent_activity_score_30 or 0.0)
    hot10 = (
        int(hit.limitup_count_10 or 0)
        + int(hit.primary_jump_separation_count_10 or 0)
        + int(hit.primary_separation_line_count_10 or 0)
        + int(hit.primary_strong_bullish_engulf_count_10 or 0)
        + int(hit.primary_big_bull7_count_10 or 0)
        + int(hit.primary_big_yang5_count_10 or 0)
        + int(hit.primary_gap_up_count_10 or 0)
        + int(getattr(hit, "primary_limit_failed_attack_count_10", 0) or 0)
    )
    return bool(score20 > 0 or score30 > 3 or hot10 > 0)


def report_risk_note(hit: LingdongHit, days: Optional[int] = None) -> str:
    tags: List[str] = []
    if hit.status == BAD_ACTIVE:
        tags.append("邪动")
    if days is None:
        if hit.big_yin5_count_100 >= REPORT_MAX_BIG_YIN5_100:
            tags.append(f"100日5%阴{hit.big_yin5_count_100}")
        if hit.long_upper_count_100 >= 6:
            tags.append(f"长上影{hit.long_upper_count_100}")
        if hit.volume_long_bear_20 >= BAD_VOL_LONG_BEAR_20:
            tags.append(f"20日长阴{hit.volume_long_bear_20}")
        unrepaired_failed_100 = int(getattr(hit, "limit_failed_unrepaired_count_100", getattr(hit, "limit_failed_count_100", 0)) or 0)
        if unrepaired_failed_100 >= 2:
            tags.append(f"未修复炸板{unrepaired_failed_100}")
    else:
        yin = int(getattr(hit, f"big_yin5_count_{days}", 0) or 0)
        long_upper = int(getattr(hit, f"long_upper_count_{days}", 0) or 0)
        vol_bear = int(getattr(hit, 'volume_long_bear_count_20_recent' if days == 20 else f'volume_long_bear_count_{days}', 0) or 0)
        if yin:
            tags.append(f"5%阴{yin}")
        if vol_bear:
            tags.append(f"长阴{vol_bear}")
        failed = int(getattr(hit, f"limit_failed_unrepaired_count_{days}", getattr(hit, f"limit_failed_count_{days}", 0)) or 0)
        if long_upper:
            tags.append(f"长上影{long_upper}")
        if failed:
            tags.append(f"炸板{failed}")
    return "｜风险" + ",".join(tags) if tags else ""


def enrich_export_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """导出层只补兼容字段，不再用统计后相减覆盖K线级互斥结果。"""
    for row in rows:
        row.setdefault("data_quality_action", "allow")
        row.setdefault("limit_close_up_count_100", row.get("limitup_count_100", 0))
        row.setdefault("limit_touch_up_count_100", row.get("limitup_count_100", 0))
        row.setdefault("limit_failed_count_100", 0)
        row.setdefault("limit_failed_count_10", 0)
        row.setdefault("limit_failed_count_20", 0)
        row.setdefault("limit_failed_count_30", 0)
        row.setdefault("primary_limit_failed_attack_count_100", 0)
        row.setdefault("primary_limit_failed_attack_count_10", 0)
        row.setdefault("primary_limit_failed_attack_count_20", 0)
        row.setdefault("primary_limit_failed_attack_count_30", 0)
        for extra_key in (
            "primary_limit_touch_unsealed_strong_count_100", "primary_limit_touch_unsealed_strong_count_10",
            "primary_limit_touch_unsealed_strong_count_20", "primary_limit_touch_unsealed_strong_count_30",
            "limit_failed_pending_count_100", "limit_failed_pending_count_10", "limit_failed_pending_count_20", "limit_failed_pending_count_30",
            "pending_volume_long_bear_count_100", "pending_volume_long_bear_count_10", "pending_volume_long_bear_count_20", "pending_volume_long_bear_count_30",
            "impossible_return_count_100", "impossible_return_count_20",
        ):
            row.setdefault(extra_key, 0)
        row.setdefault("cache_has_exchange_prefix", True)
        row.setdefault("pool_role", "audit_only")
        row.setdefault("tradable_candidate", False)
        row.setdefault("selected_eligible", False)
        row.setdefault("not_selected_reason", "")
        row.setdefault("last_limitup_age", -1)
        row.setdefault("last_big_attack_age", -1)
        row.setdefault("negative_risk_decay_score", 0.0)
        row.setdefault("compression_seed_candidate", False)
        row.setdefault("engine_role", "Activity Engine / 非买点")
        for days in (100, 10, 20, 30):
            suffix = str(days)
            row.setdefault(f"price_attack7_ex_limitup_count_{suffix}", 0)
            row.setdefault(f"price_attack5_plain_count_{suffix}", 0)
            for key in (
                "primary_jump_separation", "primary_separation_line", "primary_strong_bullish_engulf",
                "primary_big_bull7", "primary_big_yang5", "primary_gap_up",
            ):
                row.setdefault(f"{key}_count_{suffix}", 0)
    return rows


def report_rank_key(hit: LingdongHit) -> Tuple[float, float, float, float, float, str]:
    # 排序拆成两层：股性活跃分 + 轻量交易质量代理。灵动仍是股性雷达，不伪装成正式买点模型。
    activity_score = lingdong_activity_rank_score(hit)
    quality_score = trade_quality_proxy_score(hit)
    recent20 = max(finite_float(hit.recent_activity_score_20, 0.0), 0.0)
    amount_lift = min(max(finite_float(hit.amount_ratio_20_60, 0.0), 0.0), 3.0)
    liquidity = min(max(finite_float(hit.amount20, 0.0) / 1e8, 0.0), 8.0)
    score = activity_score + quality_score
    return (score, activity_score, quality_score, recent20, amount_lift + liquidity, hit.code)


def select_report_hits(hits: List[LingdongHit]) -> List[LingdongHit]:
    good = [x for x in hits if x.status == GOOD_ACTIVE]
    if not good:
        return []
    # 精选榜必须近期仍在动；非目标日缓存/名称缺失/极端妖动只保留在CSV/JSON。
    cleaned = [x for x in good if telegram_eligible(x) and (not is_extreme_hot_for_report(x)) and has_recent_report_activity(x)]
    return sorted(cleaned, key=report_rank_key, reverse=True)


def recent_window_rank_key(hit: LingdongHit, days: int) -> Tuple[float, float, float, float, float, str]:
    # 近端榜是热度雷达：先看真实活跃强度，再看净活跃质量与风险标签。
    # 不能把 clean_quality 放第一，否则会把真正热票压成“温和活跃榜”。
    score = float(getattr(hit, f"recent_activity_score_{days}", 0.0) or 0.0)
    yin = float(getattr(hit, f"big_yin5_count_{days}", 0) or 0)
    vol_bear = float(getattr(hit, f"volume_long_bear_count_{days if days != 20 else '20_recent'}", 0) or 0)
    long_upper = float(getattr(hit, f"long_upper_count_{days}", 0) or 0)
    failed = float(getattr(hit, f"limit_failed_unrepaired_count_{days}", getattr(hit, f"limit_failed_count_{days}", 0)) or 0)
    risk_penalty = yin * ACTIVITY_PENALTY_BIG_YIN5 + vol_bear * ACTIVITY_PENALTY_VOL_LONG_BEAR + long_upper * ACTIVITY_PENALTY_LONG_UPPER + failed * 2.5
    net_quality = score - risk_penalty * 0.35
    risk_flag = 1.0 if hit.status == BAD_ACTIVE else 0.0
    liquidity = min(max(hit.amount20 / 1e8, 0.0), 10.0)
    lift = min(max(hit.amount_ratio_20_60, 0.0), 3.0)
    return (score, net_quality, -risk_flag, liquidity, lift, -yin, hit.code)

def select_recent_window_hits(hits: List[LingdongHit], days: int, top_n: int = RECENT_WINDOW_TOP_N) -> List[LingdongHit]:
    out: List[LingdongHit] = []
    for x in hits:
        score = float(getattr(x, f"recent_activity_score_{days}", 0.0) or 0.0)
        # 近10/20/30榜允许邪动乱流上榜，但必须在报告行里标风险；死水、低流动性、样本不足不进热度榜。
        if x.status not in {GOOD_ACTIVE, NORMAL_ACTIVE, BAD_ACTIVE}:
            continue
        if not telegram_eligible(x):
            continue
        if score <= 0:
            continue
        if x.amount20 < AMOUNT20_BASIC:
            continue
        out.append(x)
    return sorted(out, key=lambda z: recent_window_rank_key(z, days), reverse=True)[:max(1, int(top_n))]


def recent_window_line(hit: LingdongHit, days: int, idx: int) -> str:
    limitup, price7, price5 = price_attack_counts(hit, days)
    return (
        f"{idx}. {hit.code} {hit.name}"
        f"｜{hit.status}"
        f"｜涨停{limitup}"
        f"｜非停7%阳{price7}"
        f"｜普通5%阳{price5}"
        f"｜主跳分{getattr(hit, f'primary_jump_separation_count_{days}', 0)}"
        f"｜主分手{getattr(hit, f'primary_separation_line_count_{days}', 0)}"
        f"｜主强包{getattr(hit, f'primary_strong_bullish_engulf_count_{days}', 0)}"
        f"｜炸板攻击{getattr(hit, f'primary_limit_failed_attack_count_{days}', 0)}"
        f"｜摸强未封{getattr(hit, f'primary_limit_touch_unsealed_strong_count_{days}', 0)}"
        f"｜待确认炸板{getattr(hit, f'limit_failed_pending_count_{days}', 0)}"
        f"｜未修复炸板{getattr(hit, f'limit_failed_unrepaired_count_{days}', getattr(hit, f'limit_failed_count_{days}', 0))}"
        f"｜修复炸板{getattr(hit, f'limit_failed_repaired_count_{days}', 0)}"
        f"｜分{float(getattr(hit, f'recent_activity_score_{days}', 0.0) or 0.0):.1f}"
        f"｜20日额{hit.amount20/1e8:.2f}亿"
        f"{report_risk_note(hit, days)}"
    )


def build_report_text(
    hits: List[LingdongHit],
    all_results: Optional[List[LingdongHit]] = None,
    stat: Optional[ScanStat] = None,
) -> str:
    # Telegram有4096字符限制。主报告只做“可读摘要 + 灵动精选 + 近端活跃雷达”，
    # 全字段明细继续放在 CSV/JSON，避免 send telegram result 报 message is too long。
    rows = all_results if all_results is not None else hits
    counts = status_counts(rows) if rows else {}
    good_hits_all = [x for x in rows if x.status == GOOD_ACTIVE]
    good_hits = select_report_hits(rows)

    if not good_hits and all_results is None and stat is None:
        return "无符合灵动充沛条件股票"

    lines: List[str] = []
    lines.append("【灵动｜日K股性活跃度｜非买点】")
    if stat is not None:
        stale_note = f"｜过期{stat.stale_count}" if stat.stale_count else ""
        refresh_note = f"｜补今{stat.refreshed_count}" if stat.refreshed_count else ""
        skipped = getattr(stat, "refresh_skipped_count", 0)
        skip_note = f"｜限补跳过{skipped}" if skipped else ""
        lines.append(
            f"目标日{stat.target_date}｜缓存命中{stat.cache_hit_count}/{stat.cache_files}"
            f"｜扫描{stat.daily_success_count}{stale_note}{refresh_note}{skip_note}"
        )

    lines.append(
        f"灵动充沛{counts.get(GOOD_ACTIVE, 0)}｜灵动尚可{counts.get(NORMAL_ACTIVE, 0)}｜"
        f"邪动乱流{counts.get(BAD_ACTIVE, 0)}｜死水无灵{counts.get(DEAD_ACTIVE, 0)}｜"
        f"灵气枯竭{counts.get(LOW_LIQUIDITY, 0)}｜样本不足{counts.get(DATA_SHORT, 0)}｜数据异常{counts.get(DATA_BAD, 0)}"
    )

    if not good_hits:
        lines.append(f"无符合灵动精选条件股票；广义灵动充沛{len(good_hits_all)}只详见 CSV/JSON")
    else:
        top_n = max(1, int(REPORT_TOP_N))
        top = good_hits[:top_n]
        report_label = "灵动股性精选（非买点）" if REPORT_EXCLUDE_EXTREME_HOT else "灵动充沛（非买点）"
        lines.append(f"【{report_label} Top {len(top)}/{len(good_hits)}｜广义灵动充沛{len(good_hits_all)}】")
        for i, x in enumerate(top, 1):
            limitup, price7, price5 = price_attack_counts(x, 100)
            lines.append(
                f"{i}. {x.code} {x.name}"
                f"｜封板{limitup}"
                f"｜摸板{getattr(x, 'limit_touch_up_count_100', limitup)}"
                f"｜待确认炸板{getattr(x, 'limit_failed_pending_count_100', 0)}"
                f"｜未修复炸板{getattr(x, 'limit_failed_unrepaired_count_100', getattr(x, 'limit_failed_count_100', 0))}"
                f"｜摸强未封{getattr(x, 'primary_limit_touch_unsealed_strong_count_100', 0)}"
                f"｜非停7%阳{price7}"
                f"｜普通5%阳{price5}"
                f"{primary_event_note(x, 100)}"
                f"｜近20分{x.recent_activity_score_20:.1f}"
                f"｜近30分{x.recent_activity_score_30:.1f}"
                f"｜5%阴{x.big_yin5_count_100}"
                f"｜20日额{x.amount20/1e8:.2f}亿"
                f"{report_risk_note(x)}"
            )

    for days in (10, 20, 30):
        recent_top = select_recent_window_hits(rows, days, RECENT_WINDOW_TOP_N)
        if recent_top:
            lines.append(f"【近{days}日活跃 Top {len(recent_top)}｜热度雷达，非买点】")
            for j, item in enumerate(recent_top, 1):
                lines.append(recent_window_line(item, days, j))

    if good_hits and len(good_hits) > min(len(good_hits), max(1, int(REPORT_TOP_N))):
        lines.append(f"其余{len(good_hits) - max(1, int(REPORT_TOP_N))}只灵动股性精选详见 CSV/JSON artifact")
    if REPORT_EXCLUDE_EXTREME_HOT and len(good_hits_all) > len(good_hits):
        lines.append(f"另有{len(good_hits_all) - len(good_hits)}只极端妖动/近期不足的广义灵动票仅保留在CSV/JSON")

    return _telegram_safe_text(lines)

def status_counts(results: List[LingdongHit]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for x in results:
        out[x.status] = out.get(x.status, 0) + 1
    return out


def not_selected_reason(hit: LingdongHit) -> str:
    reasons: List[str] = []
    if hit.status != GOOD_ACTIVE:
        reasons.append(f"status={hit.status}")
    if not is_target_fresh(hit):
        reasons.append("stale_cache")
    if not telegram_name_ok(hit):
        reasons.append("name_unconfirmed")
    if not telegram_stock_ok(hit):
        reasons.append("not_common_stock")
    if not data_quality_eligible(hit):
        reasons.append("data_quality_block")
    if not cache_prefix_eligible(hit):
        reasons.append("cache_unprefixed")
    if is_extreme_hot_for_report(hit):
        reasons.append("extreme_hot")
    if not has_recent_report_activity(hit):
        reasons.append("recent_activity_not_enough")
    return "|".join(reasons) if reasons else ""


def annotate_export_rows(rows: List[Dict[str, Any]], role: str, selected_codes: Optional[set] = None) -> List[Dict[str, Any]]:
    selected_codes = selected_codes or set()
    for row in rows:
        key = s(row.get("bs_code") or row.get("code"))
        row["pool_role"] = role
        row["tradable_candidate"] = False
        row["selected_eligible"] = key in selected_codes or role == "selected_activity_radar"
        if role == "broad_activity_pool" and not row.get("selected_eligible"):
            row.setdefault("not_selected_reason", "not_selected_broad_activity_only")
        elif role == "selected_activity_radar":
            row["not_selected_reason"] = ""
        row.setdefault("not_selected_reason", "")
    return rows


def write_outputs(all_results: List[LingdongHit], stat: ScanStat, failures: List[Dict[str, str]]) -> None:
    ensure_report_dir()
    active_pool_hits = [x for x in all_results if is_report_signal(x)]
    selected_hits = select_report_hits(all_results)
    selected_codes = {s(x.bs_code or x.code) for x in selected_hits}
    all_rows = enrich_export_rows([asdict(x) for x in all_results])
    signal_rows = enrich_export_rows([asdict(x) for x in active_pool_hits])
    selected_rows = enrich_export_rows([asdict(x) for x in selected_hits])
    # v23：active_pool是广义股性池，不是买点池；selected也是Activity Radar，不等于可交易候选。
    for row, hit in zip(all_rows, all_results):
        row["pool_role"] = "all_status_audit"
        row["tradable_candidate"] = False
        row["selected_eligible"] = s(row.get("bs_code") or row.get("code")) in selected_codes
        row["not_selected_reason"] = "" if row["selected_eligible"] else not_selected_reason(hit)
    for row, hit in zip(signal_rows, active_pool_hits):
        row["pool_role"] = "broad_activity_pool"
        row["tradable_candidate"] = False
        row["selected_eligible"] = s(row.get("bs_code") or row.get("code")) in selected_codes
        row["not_selected_reason"] = "" if row["selected_eligible"] else not_selected_reason(hit)
    for row in selected_rows:
        row["pool_role"] = "selected_activity_radar"
        row["tradable_candidate"] = False
        row["selected_eligible"] = True
        row["not_selected_reason"] = ""

    columns = [
        "code", "bs_code", "name", "status", "latest_trade_day",
        "amount20", "amount60", "amount_ratio_20_60", "amount_source", "amount_missing_rate20", "amount_estimated_rate20", "volume_unit", "adjust_flag", "data_quality_flags", "data_quality_action",
        "limit_close_up_count_100", "limit_touch_up_count_100", "limit_failed_count_100", "limit_failed_count_10", "limit_failed_count_20", "limit_failed_count_30", "limit_failed_unrepaired_count_100", "limit_failed_repaired_count_100", "limit_failed_pending_count_100", "limit_failed_unrepaired_count_10", "limit_failed_unrepaired_count_20", "limit_failed_unrepaired_count_30", "limit_failed_repaired_count_10", "limit_failed_repaired_count_20", "limit_failed_repaired_count_30", "limit_failed_pending_count_10", "limit_failed_pending_count_20", "limit_failed_pending_count_30", "raw_volume_long_bear_count_100", "repaired_volume_long_bear_count_100", "destructive_volume_long_bear_count_100", "pending_volume_long_bear_count_100", "raw_volume_long_bear_count_10", "repaired_volume_long_bear_count_10", "destructive_volume_long_bear_count_10", "pending_volume_long_bear_count_10", "raw_volume_long_bear_count_20", "repaired_volume_long_bear_count_20", "destructive_volume_long_bear_count_20", "pending_volume_long_bear_count_20", "raw_volume_long_bear_count_30", "repaired_volume_long_bear_count_30", "destructive_volume_long_bear_count_30", "pending_volume_long_bear_count_30", "impossible_return_count_100", "impossible_return_count_20", "last_limitup_age", "last_big_attack_age", "negative_risk_decay_score", "compression_seed_candidate", "compression_role", "engine_role", "cache_has_exchange_prefix", "pool_role", "tradable_candidate", "selected_eligible", "not_selected_reason",
        "limitup_count_100", "big_bull7_count_100", "big_yang5_count_100", "price_attack7_ex_limitup_count_100", "price_attack5_plain_count_100", "primary_jump_separation_count_100", "primary_separation_line_count_100", "primary_strong_bullish_engulf_count_100", "primary_big_bull7_count_100", "primary_big_yang5_count_100", "primary_gap_up_count_100", "primary_limit_failed_attack_count_100", "primary_limit_touch_unsealed_strong_count_100", "big_yin5_count_100",
        "limitup_count_10", "big_bull7_count_10", "big_yang5_count_10", "price_attack7_ex_limitup_count_10", "price_attack5_plain_count_10", "primary_jump_separation_count_10", "primary_separation_line_count_10", "primary_strong_bullish_engulf_count_10", "primary_big_bull7_count_10", "primary_big_yang5_count_10", "primary_gap_up_count_10", "primary_limit_failed_attack_count_10", "primary_limit_touch_unsealed_strong_count_10", "big_yin5_count_10", "gap_up_count_10", "jump_separation_count_10", "separation_line_count_10", "strong_bullish_engulf_count_10", "volume_long_bear_count_10", "long_upper_count_10", "recent_activity_score_10",
        "limitup_count_20", "big_bull7_count_20", "big_yang5_count_20", "price_attack7_ex_limitup_count_20", "price_attack5_plain_count_20", "primary_jump_separation_count_20", "primary_separation_line_count_20", "primary_strong_bullish_engulf_count_20", "primary_big_bull7_count_20", "primary_big_yang5_count_20", "primary_gap_up_count_20", "primary_limit_failed_attack_count_20", "primary_limit_touch_unsealed_strong_count_20", "big_yin5_count_20", "gap_up_count_20", "jump_separation_count_20", "separation_line_count_20", "strong_bullish_engulf_count_20", "volume_long_bear_count_20_recent", "long_upper_count_20", "recent_activity_score_20",
        "limitup_count_30", "big_bull7_count_30", "big_yang5_count_30", "price_attack7_ex_limitup_count_30", "price_attack5_plain_count_30", "primary_jump_separation_count_30", "primary_separation_line_count_30", "primary_strong_bullish_engulf_count_30", "primary_big_bull7_count_30", "primary_big_yang5_count_30", "primary_gap_up_count_30", "primary_limit_failed_attack_count_30", "primary_limit_touch_unsealed_strong_count_30", "big_yin5_count_30", "gap_up_count_30", "jump_separation_count_30", "separation_line_count_30", "strong_bullish_engulf_count_30", "volume_long_bear_count_30", "long_upper_count_30", "recent_activity_score_30",
        "gap_up_count_100", "gap_down_count_100", "range20_pct", "small_body_ratio_60",
        "volume_long_bear_20", "long_upper_count_100", "trend_efficiency_20",
        "attack_memory", "bad_activity", "dead_activity", "detail",
    ]
    # 兼容旧workflow：lingdong_latest.csv 输出精选池，避免广义active_pool污染下游。
    pd.DataFrame(selected_rows, columns=columns).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(signal_rows, columns=columns).to_csv(OUTPUT_ACTIVE_POOL_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(selected_rows, columns=columns).to_csv(OUTPUT_SELECTED_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(all_rows, columns=columns).to_csv(OUTPUT_ALL_CSV, index=False, encoding="utf-8-sig")

    payload = {
        "summary": asdict(stat),
        "selected": selected_rows,
        "active_pool": signal_rows,
        "signals": signal_rows,
        "all_status_count": status_counts(all_results),
        "all_results": all_rows,
        "failures": failures,
        "diagnostics": DIAGNOSTIC_EVENTS[-1000:],
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_DIAGNOSTIC_JSON.write_text(json.dumps(DIAGNOSTIC_EVENTS[-3000:], ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(build_report_text([x for x in all_results if is_report_signal(x)], all_results, stat).rstrip() + "\n", encoding="utf-8")


def refresh_priority_codes(
    items: List[Tuple[str, pd.DataFrame]],
    names: Dict[str, str],
    target: str,
    limit: int,
) -> Optional[set]:
    """fallback显式打开时，优先补有交易价值的过期缓存，不再按缓存文件顺序盲补前500只。"""
    if limit <= 0:
        return None
    ranked: List[Tuple[float, str]] = []
    for code, cached in items:
        try:
            daily = normalize_hist(cached, code)
            if daily.empty or s(daily.iloc[-1].get("date")) == target:
                continue
            display_code = code6(code)
            name = stock_display_name(display_code, names.get(code, names.get(display_code, "")))
            if name == "名称待补" and "name" in daily.columns:
                vals = [s(x) for x in daily["name"].tolist() if valid_stock_display_name(display_code, x)]
                if vals:
                    name = vals[-1]
            item = StockItem(code=display_code, bs_code=bs_code_of(code), name=name, cache_has_exchange_prefix=CACHE_EXCHANGE_PREFIX_OK.get(code, True))
            hit = evaluate_lingdong(daily, item, target)
            if not telegram_name_ok(hit) or not telegram_stock_ok(hit):
                continue
            if hit.amount20 < AMOUNT20_BASIC:
                continue
            activity = max(float(hit.recent_activity_score_20 or 0.0), float(hit.recent_activity_score_30 or 0.0))
            if hit.status not in {GOOD_ACTIVE, NORMAL_ACTIVE, BAD_ACTIVE} and activity <= 0:
                continue
            status_bonus = {GOOD_ACTIVE: 30.0, NORMAL_ACTIVE: 16.0, BAD_ACTIVE: 8.0}.get(hit.status, 0.0)
            score = status_bonus + activity + min(hit.amount20 / 1e8, 10.0) + min(hit.amount_ratio_20_60, 3.0)
            ranked.append((score, code))
        except Exception as exc:
            record_exception("refresh_priority_codes", code, exc)
            continue
    ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return {code for _, code in ranked[:max(1, int(limit))]}


def run_scan(limit: int = 0) -> int:
    DIAGNOSTIC_EVENTS.clear()
    CACHE_EXCHANGE_PREFIX_OK.clear()
    ensure_report_dir()

    hist, names, cache_stat = load_public_cache()
    if limit and limit > 0:
        hist = dict(list(hist.items())[:limit])

    if not hist:
        raise RuntimeError("公共日K缓存为空，灵动禁止全市场慢拉；请先运行任一日K员工生成kline_cache")

    # 目标交易日优先用环境变量；否则从公共缓存推断最近共同交易日，避免节假日被普通工作日规则误判。
    global TARGET_DASH
    TARGET_DASH = resolve_target_date_after_cache(hist)

    refresh_enabled = bool(ALLOW_BAOSTOCK_FALLBACK and bs is not None)
    logged_in = False
    if refresh_enabled:
        login_result = bs.login()
        logged_in = getattr(login_result, "error_code", "") == "0"
        if not logged_in:
            print(
                f"baostock登录失败，跳过目标日补拉: {getattr(login_result, 'error_code', '')} {getattr(login_result, 'error_msg', '')}",
                flush=True,
            )
    try:
        results: List[LingdongHit] = []
        failures: List[Dict[str, str]] = []
        daily_success = 0
        stale_count = 0
        refreshed_count = 0
        refresh_failed_count = 0
        refresh_skipped_count = 0
        refresh_attempted_count = 0
        start = time.time()
        items = list(hist.items())
        refresh_allow_set = refresh_priority_codes(items, names, TARGET_DASH, REFRESH_LIMIT) if refresh_enabled and logged_in else None

        for idx, (cache_key, cached) in enumerate(items, 1):
            try:
                display_code = code6(cache_key)
                daily = normalize_hist(cached, display_code)
                if daily.empty:
                    failures.append({"code": cache_key, "name": names.get(cache_key, names.get(display_code, "名称待补")), "error": "缓存日K为空"})
                    continue

                latest = s(daily.iloc[-1].get("date"))
                if TARGET_DASH and latest != TARGET_DASH:
                    stale_count += 1
                    # 只补目标日这一根。补不到就继续用缓存旧日作为股性参考，绝不全市场全历史慢拉。
                    if refresh_enabled and logged_in:
                        priority_ok = refresh_allow_set is None or cache_key in refresh_allow_set
                        can_refresh = priority_ok and (REFRESH_LIMIT <= 0 or refresh_attempted_count < REFRESH_LIMIT)
                        if not can_refresh:
                            refresh_skipped_count += 1
                        else:
                            refresh_attempted_count += 1
                            try:
                                merged, ok = fetch_target_day_only(cache_key, daily, TARGET_DASH)
                                if ok:
                                    daily = merged
                                    latest = s(daily.iloc[-1].get("date"))
                                    save_cache(cache_key, daily)
                                    refreshed_count += 1
                                else:
                                    refresh_failed_count += 1
                            except Exception as exc:
                                refresh_failed_count += 1
                                record_exception("fetch_target_day_only", cache_key, exc)

                name = stock_display_name(display_code, names.get(cache_key, names.get(display_code, "")))
                if name == "名称待补" and "name" in daily.columns:
                    vals = [s(x) for x in daily["name"].tolist() if valid_stock_display_name(display_code, x)]
                    if vals:
                        name = vals[-1]

                item = StockItem(code=display_code, bs_code=cache_key, name=name, cache_has_exchange_prefix=CACHE_EXCHANGE_PREFIX_OK.get(cache_key, True))
                daily_success += 1
                hit = evaluate_lingdong(daily, item, TARGET_DASH)
                results.append(hit)

            except Exception as exc:
                record_exception("run_scan_stock", cache_key, exc)
                failures.append({"stage": "run_scan_stock", "code": cache_key, "name": names.get(cache_key, names.get(code6(cache_key), "名称待补")), "error_type": type(exc).__name__, "error": str(exc)[:180]})

            if idx == 1 or idx % max(1, PROGRESS_EVERY) == 0 or idx == len(items):
                elapsed = max(time.time() - start, 0.001)
                print(
                    f"灵动日K缓存扫描 {idx}/{len(items)}"
                    f"｜日K成功{daily_success}"
                    f"｜信号{sum(1 for x in results if is_report_signal(x))}"
                    f"｜过期{stale_count}"
                    f"｜补今{refreshed_count}"
                    f"｜限补跳过{refresh_skipped_count}"
                    f"｜补失败{refresh_failed_count}"
                    f"｜失败{len(failures)}"
                    f"｜速度{idx / elapsed:.2f}只/秒"
                    f"｜当前{cache_key} {names.get(cache_key, names.get(code6(cache_key), '名称待补'))}",
                    flush=True,
                )

        if daily_success == 0:
            raise RuntimeError("公共缓存可用日K数量为0，不能伪装成无符合灵动股票")

        results = sort_hits(results)
        signal_count = sum(1 for x in results if is_report_signal(x))

        stat = ScanStat(
            version=VERSION,
            target_date=TARGET_DASH,
            stock_pool_count=len(items),
            scanned_count=len(items),
            daily_success_count=daily_success,
            failed_count=len(failures),
            signal_count=signal_count,
            data_source="public_kline_cache_first_target_day_only_refresh_d_qfq_activity_label",
            cache_files=int(cache_stat.get("cache_files", 0)),
            cache_hit_count=int(cache_stat.get("cache_hit", 0)),
            cache_bad_count=int(cache_stat.get("bad", 0)),
            cache_short_count=int(cache_stat.get("short", 0)),
            stale_count=stale_count,
            refreshed_count=refreshed_count,
            refresh_failed_count=refresh_failed_count,
            refresh_skipped_count=refresh_skipped_count,
            baostock_fallback_enabled=bool(refresh_enabled),
        )

        write_outputs(results, stat, failures)
        print(json.dumps(asdict(stat), ensure_ascii=False, indent=2), flush=True)
        print(json.dumps(status_counts(results), ensure_ascii=False, indent=2), flush=True)
        return 0
    finally:
        if logged_in:
            try:
                bs.logout()
            except Exception:
                pass


def synthetic_daily(rows: List[Tuple[Any, ...]], code: str = "000001") -> pd.DataFrame:
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if len(row) == 5:
            d, o, h, l, c = row
            volume = 1000000.0
            amount = c * volume
            pct = 0.0
        elif len(row) == 7:
            d, o, h, l, c, volume, amount = row
            pct = 0.0
        elif len(row) == 8:
            d, o, h, l, c, volume, amount, pct = row
        else:
            raise ValueError("synthetic row must have 5, 7 or 8 fields")
        normalized.append({"date": d, "code": code, "open": o, "high": h, "low": l, "close": c, "volume": volume, "amount": amount, "pct_chg": pct, "turnover": 0.0})
    df = pd.DataFrame(normalized)
    df["date"] = df["date"].map(norm_date)
    return normalize_hist(df, code)


def base_daily_rows(days: int = 130, start: str = "2025-01-01", price: float = 10.0, amount: float = 80000000.0) -> List[Tuple[Any, ...]]:
    base = pd.Timestamp(start)
    rows: List[Tuple[Any, ...]] = []
    cur = price
    trade_i = 0
    calendar_i = 0
    while trade_i < days:
        day = base + pd.Timedelta(days=calendar_i)
        calendar_i += 1
        if day.weekday() >= 5:
            continue
        open_ = cur * 0.998
        close = cur * 1.002
        high = max(open_, close) * 1.008
        low = min(open_, close) * 0.992
        volume = amount / max(close, 1e-9)
        rows.append((day.strftime("%Y-%m-%d"), round(open_, 3), round(high, 3), round(low, 3), round(close, 3), volume, amount))
        cur = close
        trade_i += 1
    return rows


def make_good_active_rows() -> pd.DataFrame:
    rows = base_daily_rows(amount=120000000.0)
    for idx, pct in [(80, 8.2), (105, 7.5), (115, 5.8), (122, 5.2)]:
        d, o, h, l, c, v, a = rows[idx]
        new_o = c
        new_c = c * (1 + pct / 100.0)
        rows[idx] = (d, round(new_o, 3), round(new_c * 1.01, 3), round(new_o * 0.995, 3), round(new_c, 3), v * 2.0, a * 2.0, pct)
    return synthetic_daily(rows)


def make_low_liquidity_rows() -> pd.DataFrame:
    return synthetic_daily(base_daily_rows(amount=12000000.0))


def make_dead_rows() -> pd.DataFrame:
    rows = base_daily_rows(amount=70000000.0)
    new_rows = []
    for d, o, h, l, c, v, a in rows:
        mid = c
        new_rows.append((d, mid * 0.999, mid * 1.006, mid * 0.994, mid * 1.001, v, a))
    return synthetic_daily(new_rows)


def make_bad_active_rows() -> pd.DataFrame:
    rows = base_daily_rows(amount=100000000.0)
    for idx in [111, 114, 118, 122, 126]:
        d, o, h, l, c, v, a = rows[idx]
        prev = c
        new_o = prev * 1.01
        new_c = prev * 0.94
        rows[idx] = (d, round(new_o, 3), round(new_o * 1.005, 3), round(new_c * 0.990, 3), round(new_c, 3), v * 2.4, a * 2.4, -6.0)
    return synthetic_daily(rows)


def make_normal_rows() -> pd.DataFrame:
    rows = base_daily_rows(amount=90000000.0)
    new_rows = []
    for idx, (d, o, h, l, c, v, a) in enumerate(rows):
        if idx % 4 == 0:
            new_rows.append((d, round(c * 0.995, 3), round(c * 1.028, 3), round(c * 0.982, 3), round(c * 1.006, 3), v, a))
        else:
            new_rows.append((d, round(c * 0.998, 3), round(c * 1.022, 3), round(c * 0.980, 3), round(c * 1.002, 3), v, a))
    return synthetic_daily(new_rows)


def self_check_once() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        items.append({"name": name, "ok": bool(ok), "detail": detail})

    src = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    add("source::public_cache_pool", "load_public_cache" in function_names and "MAIN_CACHE_DIR" in src, "必须优先读取公共日K缓存池")
    add("source::no_full_market_slow_sweep", ("query_" + "stock_pool") not in function_names, "生产扫描不得再先拿BaoStock全市场股票池逐票慢拉")
    add("source::target_day_only_refresh", "fetch_target_day_only" in function_names and 'start_date=target' in src, "补数据只能补目标交易日这一根")
    add("source::fallback_default_off", 'LINGDONG_ALLOW_BAOSTOCK_FALLBACK", "0"' in src, "BaoStock fallback默认关闭")
    add("source::daily_frequency", 'frequency="d"' in src, "如启用补拉，必须使用BaoStock日K frequency=d")
    add("source::qfq_adjustflag", 'adjustflag=QFQ_ADJUSTFLAG' in src and 'QFQ_ADJUSTFLAG = "2"' in src, "如启用补拉，必须使用前复权日K adjustflag=2")
    add("source::workflow_scan", "run_scan" in function_names and "write_outputs" in function_names, "必须保留扫描与三件套输出链路")
    add("source::activity_evaluator", "evaluate_lingdong" in function_names, "必须存在灵动评价函数")
    add("source::name_map_loader", "load_name_map" in function_names and "scan_name_frame" in function_names, "必须借用公共名称映射，避免名称待补刷屏")
    add("output::selected_top5", "select_report_hits" in function_names and "REPORT_TOP_N" in src, "Telegram只推精选TopN，不让极端妖动长期霸榜")
    add("output::recent_window_top3", "select_recent_window_hits" in function_names and "RECENT_WINDOW_TOP_N" in src, "Telegram必须输出近10/20/30日活跃Top3")
    add("rule::mutual_positive_activity_score", "pos_scores.max(axis=1).sum" in src, "近端活跃分必须按单日正向事件互斥取最高分")
    add("rule::big_yin_reversal_events", "jump_separation_line" in src and "strong_bullish_engulf" in src and 'd["prev_ret_pct"] <= -3.0' in src, "强阳包阴/分手线/跳空分手线必须以前一日3%以上大阴为基准")
    add("rule::updated_negative_weights", "ACTIVITY_PENALTY_VOL_LONG_BEAR" in src and '"3"' in src and "ACTIVITY_PENALTY_LONG_UPPER" in src and '"1.5"' in src, "放量长阴-3，长上影冲高回落-1.5")
    add("rule::nonoverlap_report_counts", "price_attack_counts" in function_names and "非停7%阳" in src and "普通5%阳" in src, "报告必须展示互斥价格攻击层级")
    add("rule::selected_requires_recent", "has_recent_report_activity" in function_names and "score20 > 0 or score30 > 3" in src, "灵动精选必须近期仍有活性")
    add("rule::bad_active_recent_radar", "BAD_ACTIVE" in src and "热度雷达，非买点" in src, "近10/20/30活跃榜允许邪动乱流但标风险")
    add("source::refresh_limit", "REFRESH_LIMIT" in src and "LINGDONG_REFRESH_LIMIT" in src, "fallback显式打开时也有补拉上限")
    add("source::refresh_priority", "refresh_priority_codes" in function_names, "fallback补今日K线必须按候选价值优先，而不是缓存顺序盲补")
    add("source::cache_inferred_target", "resolve_target_date_after_cache" in function_names and "latest_common_cache_trade_day" in function_names and "TARGET_LATEST_MIN_RATIO" in src, "目标交易日必须用最新日期覆盖阈值+最大覆盖兜底，避免半更新缓存被昨日拖慢")
    add("source::cache_identity_preserves_exchange", "cache_identity" in function_names and "bs_code: str" in src and "sh.000001" in src and "sz.000001" in src, "缓存去重键必须保留交易所前缀，避免指数/股票6位代码冲突")
    add("source::normalize_before_target_inference", "不在归一化阶段按 TARGET_DASH 截断" in src, "公共缓存读取阶段不得按初始目标日截断，必须先完整读缓存再推断目标交易日")
    add("output::clear_csv_contract", "OUTPUT_ACTIVE_POOL_CSV" in src and "OUTPUT_SELECTED_CSV" in src and "OUTPUT_ALL_CSV" in src, "必须输出 all/active_pool/selected 三份清晰复盘文件，并保留 latest 兼容旧workflow")
    add("rule::status_uses_exclusive_events", "exclusive_attack_100" in src and "price7_ex_limitup_100" in src and "price5_plain_100" in src, "状态归类必须统一使用互斥事件层，不再混用原始重叠次数")
    add("rule::separation_close_recovers_prev_open", 'd["close"] >= d["prev_open"] * 0.995' in src, "普通分手线必须收盘重新站回前一日开盘区域附近")
    add("stock_pool::telegram_stock_filter_connected", "telegram_stock_ok" in function_names and "common_stock_ok" in src, "ST/ETF/指数/转债等非普通股过滤必须接入Telegram候选")

    add("stock_pool::exclude_sh_000003_index", not common_stock_ok("sh.000003", "000003", "上证B股指数"), "必须剔除 sh.000003 上证B股指数")
    add("stock_pool::exclude_sh_000001_index", not common_stock_ok("sh.000001", "000001", "上证指数"), "必须剔除 sh.000001 上证指数")
    add("stock_pool::allow_sz_000001_stock", common_stock_ok("sz.000001", "000001", "平安银行"), "必须保留 sz.000001 普通股票")
    add("stock_pool::allow_sh_600000_stock", common_stock_ok("sh.600000", "600000", "浦发银行"), "必须保留 sh.600000 普通股票")
    add("stock_pool::allow_kechuang_name_stock", common_stock_ok("sz.300730", "300730", "科创信息"), "名称含科创的普通股票不能被误杀，不能用单独‘科创’做过滤词")
    add("stock_pool::exclude_st_name", not common_stock_ok("sz.000001", "000001", "*ST测试"), "Telegram必须剔除ST/*ST标的")
    add("stock_pool::exclude_etf_name", not common_stock_ok("sh.510300", "510300", "沪深300ETF"), "Telegram必须剔除ETF/基金类标的")
    add("stock_pool::exclude_special_prefix_n", not common_stock_ok("sz.001234", "001234", "N新股"), "Telegram必须剔除N/C/DR等特殊前缀标的")

    item = StockItem("000001", "sz.000001", "测试股")
    good = evaluate_lingdong(make_good_active_rows(), item)
    add("rule::good_active_hit", good.status == GOOD_ACTIVE, f"状态={good.status}，详情={good.detail}")
    add("rule::good_active_has_attack_memory", good.attack_memory, f"7%阳={good.big_bull7_count_100}，5%阳={good.big_yang5_count_100}")

    low = evaluate_lingdong(make_low_liquidity_rows(), item)
    add("rule::low_liquidity", low.status == LOW_LIQUIDITY, f"状态={low.status}，20日额={low.amount20}")

    dead = evaluate_lingdong(make_dead_rows(), item)
    add("rule::dead_activity", dead.status == DEAD_ACTIVE, f"状态={dead.status}，振幅={dead.range20_pct}，小实体={dead.small_body_ratio_60}")

    bad = evaluate_lingdong(make_bad_active_rows(), item)
    add("rule::bad_active", bad.status == BAD_ACTIVE, f"状态={bad.status}，大阴={bad.big_yin5_count_100}，大阳={bad.big_yang5_count_100}，长阴={bad.volume_long_bear_20}")

    normal = evaluate_lingdong(make_normal_rows(), item)
    add("rule::normal_active", normal.status == NORMAL_ACTIVE, f"状态={normal.status}，详情={normal.detail}")

    unordered = [bad, normal, good, low, dead]
    sorted_status = [x.status for x in sort_hits(unordered)]
    add("output::sort_hits_status_priority", sorted_status[0] == GOOD_ACTIVE and sorted_status[-1] in {DATA_SHORT, LOW_LIQUIDITY}, f"排序={sorted_status}")

    good_for_report = replace(good, latest_trade_day=TARGET_DASH, name="平安银行")
    report_text = build_report_text([good_for_report], [good_for_report], None).strip()
    add("output::report_contains_status", GOOD_ACTIVE in report_text and "000001" in report_text, f"报告内容={report_text!r}")
    add("output::report_contains_recent_windows", "近10日活跃" in report_text and "近20日活跃" in report_text and "近30日活跃" in report_text, f"报告内容={report_text!r}")

    many_hits = [good] * 200
    limited_text = build_report_text(many_hits, many_hits, None)
    add("output::telegram_safe_length", len(limited_text) <= REPORT_MAX_CHARS + 80, f"报告长度={len(limited_text)}")

    empty_text = build_report_text([]).strip()
    add("output::empty_text", empty_text == "无符合灵动充沛条件股票", f"空文案={empty_text!r}")

    # 边界案例自检：覆盖这次8项逻辑优化，不能只做结构存在性检查。
    limit_rows = base_daily_rows(amount=120000000.0)
    d0, o0, h0, l0, c0, v0, a0 = limit_rows[-1]
    limit_rows[-1] = (d0, round(c0 * 1.01, 3), round(c0 * 1.105, 3), round(c0 * 1.005, 3), round(c0 * 1.10, 3), v0 * 2, a0 * 2, 10.0)
    limit_ind = add_lingdong_indicators(synthetic_daily(limit_rows), "000001")
    limit_a1 = window_activity_counts(limit_ind, 1)
    add("edge::limitup_not_counted_as_5pct_again", limit_a1["limitup"] == 1 and limit_a1["price_attack7_ex_limitup"] == 0 and limit_a1["price_attack5_plain"] == 0 and abs(float(limit_a1["score"]) - ACTIVITY_SCORE_LIMITUP) < 1e-6, f"a1={limit_a1}")

    rev_rows = base_daily_rows(amount=120000000.0)
    d1, o1, h1, l1, c1, v1, a1 = rev_rows[-2]
    prev_open = c1 * 1.02
    prev_close = prev_open * 0.955
    rev_rows[-2] = (d1, round(prev_open, 3), round(prev_open * 1.01, 3), round(prev_close * 0.99, 3), round(prev_close, 3), v1 * 1.5, a1 * 1.5, -4.5)
    d2, o2, h2, l2, c2, v2, a2 = rev_rows[-1]
    jump_open = prev_open * 1.015
    jump_close = jump_open * 1.025
    rev_rows[-1] = (d2, round(jump_open, 3), round(jump_close * 1.005, 3), round(jump_open * 0.995, 3), round(jump_close, 3), v2 * 1.8, a2 * 1.8, 6.0)
    rev_ind = add_lingdong_indicators(synthetic_daily(rev_rows), "000001")
    rev_a1 = window_activity_counts(rev_ind, 1)
    add("edge::jump_separation_primary_event", rev_a1["jump_separation"] == 1 and rev_a1["primary_jump_separation"] == 1, f"a1={rev_a1}")

    small_sep = synthetic_daily([
        ("2026-01-01", 10.0, 10.2, 9.5, 9.6, 1000000, 100000000, -4.0),
        ("2026-01-02", 10.01, 10.05, 9.90, 10.02, 1000000, 100000000, 4.375),
    ])
    small_ind = add_lingdong_indicators(small_sep, "000001")
    small_a1 = window_activity_counts(small_ind, 1)
    add("edge::small_body_separation_excluded", small_a1["separation_line"] == 0 and small_a1["strong_bullish_engulf"] == 0, f"a1={small_a1}")

    inferred = latest_common_cache_trade_day({"000001": make_good_active_rows(), "600000": make_normal_rows()})
    add("edge::cache_target_inference", bool(inferred), f"推断交易日={inferred}")

    stale_good = replace(good_for_report, latest_trade_day="2000-01-01")
    add("edge::stale_not_telegram_eligible", not telegram_eligible(stale_good), f"latest={stale_good.latest_trade_day}, target={TARGET_DASH}")
    st_good = replace(good_for_report, latest_trade_day=TARGET_DASH, name="*ST测试")
    add("edge::st_not_telegram_eligible", not telegram_eligible(st_good), f"name={st_good.name}")

    selected_rows_preview = select_report_hits([good_for_report, st_good, stale_good])
    add("edge::selected_excludes_stale_and_st", len(selected_rows_preview) == 1 and selected_rows_preview[0].name == "平安银行", f"selected={[x.name for x in selected_rows_preview]}")

    add("edge::cache_identity_no_sh_sz_collision", cache_identity("sh.000001.csv") == "sh.000001" and cache_identity("sz.000001.csv") == "sz.000001", f"sh={cache_identity('sh.000001.csv')} sz={cache_identity('sz.000001.csv')}")

    future_df = normalize_hist(pd.DataFrame([
        {"date": "2099-01-01", "code": "000001", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100, "amount": 1000, "pct_chg": 1.0}
    ]), "000001")
    add("edge::normalize_keeps_future_for_cache_inference", not future_df.empty and s(future_df.iloc[-1].get("date")) == "2099-01-01", f"normalize结果={future_df.to_dict('records') if not future_df.empty else []}")

    jump_limit_rows = base_daily_rows(amount=120000000.0)
    d1, o1, h1, l1, c1, v1, a1 = jump_limit_rows[-2]
    prev_open2 = c1 * 1.02
    prev_close2 = prev_open2 * 0.955
    jump_limit_rows[-2] = (d1, round(prev_open2, 3), round(prev_open2 * 1.01, 3), round(prev_close2 * 0.99, 3), round(prev_close2, 3), v1 * 1.5, a1 * 1.5, -4.5)
    d2, o2, h2, l2, c2, v2, a2 = jump_limit_rows[-1]
    jump_open2 = prev_open2 * 1.015
    jump_close2 = prev_close2 * 1.10
    jump_limit_rows[-1] = (d2, round(jump_open2, 3), round(jump_close2 * 1.005, 3), round(jump_open2 * 0.995, 3), round(jump_close2, 3), v2 * 2.0, a2 * 2.0, 10.0)
    jump_limit_ind = add_lingdong_indicators(synthetic_daily(jump_limit_rows), "000001")
    jump_limit_a1 = window_activity_counts(jump_limit_ind, 1)
    add("edge::limitup_does_not_hide_jump_separation_structure", jump_limit_a1["limitup"] == 1 and jump_limit_a1["jump_separation"] == 1 and jump_limit_a1["primary_jump_separation"] == 1 and abs(float(jump_limit_a1["score"]) - ACTIVITY_SCORE_LIMITUP) < 1e-6, f"a1={jump_limit_a1}")

    chaos_rows = base_daily_rows(days=150, amount=150000000.0)
    for i in range(55, 75):
        d0, o0, h0, l0, c0, v0, a0 = chaos_rows[i]
        prev0 = chaos_rows[i - 1][4]
        o0 = prev0 * 1.01
        c0 = prev0 * 1.06
        h0 = c0 * 1.01
        l0 = o0 * 0.995
        chaos_rows[i] = (d0, round(o0, 3), round(h0, 3), round(l0, 3), round(c0, 3), v0 * 2, a0 * 2, 5.8)
    for i in range(75, 93):
        d0, o0, h0, l0, c0, v0, a0 = chaos_rows[i]
        prev0 = chaos_rows[i - 1][4]
        o0 = prev0 * 0.99
        c0 = prev0 * 0.93
        h0 = o0 * 1.005
        l0 = c0 * 0.99
        chaos_rows[i] = (d0, round(o0, 3), round(h0, 3), round(l0, 3), round(c0, 3), v0 * 2, a0 * 2, -6.2)
    chaos = evaluate_lingdong(synthetic_daily(chaos_rows), item)
    add("edge::high_attack_high_yin_is_bad_activity", chaos.status == BAD_ACTIVE and "高攻击高大阴" in chaos.detail, f"状态={chaos.status}，详情={chaos.detail}")


    # v18新增边界：名称映射/Telegram必须全链路保留bs_code，不能用6位代码串名。
    nm: Dict[str, str] = {}
    add_name_mapping(nm, "sh.000001", "上证指数")
    add_name_mapping(nm, "sz.000001", "平安银行")
    add("edge::name_map_preserves_exchange_identity", nm.get("sh.000001") == "上证指数" and nm.get("sz.000001") == "平安银行", f"name_map={nm}")
    fake_index_hit = replace(good_for_report, bs_code="sh.000001", code="000001", name="平安银行")
    add("edge::telegram_uses_hit_bs_code_not_code6_guess", not telegram_stock_ok(fake_index_hit), f"bs_code={fake_index_hit.bs_code}, code={fake_index_hit.code}, name={fake_index_hit.name}")

    # v18新增边界：最新日期覆盖达到阈值时，不能被昨日最大覆盖拖慢。
    old_ratio = TARGET_LATEST_MIN_RATIO
    old_count = TARGET_LATEST_MIN_COUNT
    try:
        globals()["TARGET_LATEST_MIN_RATIO"] = 0.30
        globals()["TARGET_LATEST_MIN_COUNT"] = 800
        today_df = make_good_active_rows()
        # 人工追加一个更晚日期，使其成为最新日；2/4=50% 覆盖，应优先最新日期。
        more_rows = base_daily_rows(amount=120000000.0)
        d_last, o_last, h_last, l_last, c_last, v_last, a_last = more_rows[-1]
        today_df2 = normalize_hist(pd.concat([today_df, pd.DataFrame([{
            "date": "2026-06-18", "code": "000001", "open": 10, "high": 11, "low": 9.8, "close": 10.5, "volume": 1000000, "amount": 100000000, "pct_chg": 1.0
        }])], ignore_index=True), "000001")
        today_df3 = normalize_hist(pd.concat([make_normal_rows(), pd.DataFrame([{
            "date": "2026-06-18", "code": "600000", "open": 10, "high": 11, "low": 9.8, "close": 10.5, "volume": 1000000, "amount": 100000000, "pct_chg": 1.0
        }])], ignore_index=True), "600000")
        inferred_latest = latest_common_cache_trade_day({"sz.000001": today_df2, "sh.600000": today_df3, "sz.000002": make_good_active_rows(), "sh.600001": make_normal_rows()})
        add("edge::target_inference_uses_latest_when_coverage_enough", inferred_latest == "2026-06-18", f"推断={inferred_latest}")
    finally:
        globals()["TARGET_LATEST_MIN_RATIO"] = old_ratio
        globals()["TARGET_LATEST_MIN_COUNT"] = old_count

    # v18新增边界：一字/小实体跳空分手线应作为结构被识别，但不重复加分。
    one_word_rows = base_daily_rows(amount=120000000.0)
    d1, o1, h1, l1, c1, v1, a1 = one_word_rows[-2]
    p_open = c1 * 1.02
    p_close = p_open * 0.955
    one_word_rows[-2] = (d1, round(p_open, 3), round(p_open * 1.01, 3), round(p_close * 0.99, 3), round(p_close, 3), v1 * 1.5, a1 * 1.5, -4.5)
    d2, o2, h2, l2, c2, v2, a2 = one_word_rows[-1]
    flat = p_open * 1.02
    one_word_rows[-1] = (d2, round(flat, 3), round(flat, 3), round(flat, 3), round(flat, 3), v2 * 2, a2 * 2, 10.0)
    one_word_ind = add_lingdong_indicators(synthetic_daily(one_word_rows), "000001")
    one_word_a1 = window_activity_counts(one_word_ind, 1)
    add("edge::jump_separation_allows_one_word_strong_hold", one_word_a1["jump_separation"] == 1 and one_word_a1["primary_jump_separation"] == 1, f"a1={one_word_a1}")

    add("rule::extreme_filter_uses_exclusive_events", "price_attack7_ex_limitup_count_100" in src and "exclusive_attack" in src and "原始 big_bull7/big_yang5" in src, "极端妖动过滤必须使用互斥事件，不再用原始重叠次数")
    add("rule::recent_radar_sorts_by_heat_first", "return (score, net_quality" in src, "近10/20/30热度榜必须先按活跃分排序，再按净质量/风险修正")


    # v19深度边界：真实扫描路径不能再出现未定义 code 变量。
    old_load_public_cache = globals().get("load_public_cache")
    old_write_outputs = globals().get("write_outputs")
    old_target_latest_ratio = TARGET_LATEST_MIN_RATIO
    old_target_latest_count = TARGET_LATEST_MIN_COUNT
    old_target_dash = TARGET_DASH
    try:
        globals()["TARGET_LATEST_MIN_RATIO"] = 0.0
        globals()["TARGET_LATEST_MIN_COUNT"] = 1
        def fake_load_public_cache():
            df = make_good_active_rows()
            latest = s(df.iloc[-1].get("date"))
            return {"sz.000001": df}, {"sz.000001": "平安银行", "000001": "平安银行"}, {"cache_files": 1, "cache_hit": 1, "bad": 0, "short": 0}
        def fake_write_outputs(results, stat, failures):
            add("edge::run_scan_real_cache_path_produces_hit", len(results) == 1 and results[0].bs_code == "sz.000001" and failures == [], f"results={len(results)} failures={failures} target={stat.target_date}")
        globals()["load_public_cache"] = fake_load_public_cache
        globals()["write_outputs"] = fake_write_outputs
        rc = run_scan(0)
        add("edge::run_scan_real_cache_path_no_nameerror", rc == 0, f"run_scan返回={rc}")
    except Exception as exc:
        add("edge::run_scan_real_cache_path_no_nameerror", False, f"异常={type(exc).__name__}: {exc}")
    finally:
        globals()["load_public_cache"] = old_load_public_cache
        globals()["write_outputs"] = old_write_outputs
        globals()["TARGET_LATEST_MIN_RATIO"] = old_target_latest_ratio
        globals()["TARGET_LATEST_MIN_COUNT"] = old_target_latest_count
        globals()["TARGET_DASH"] = old_target_dash

    # v19深度边界：指数/ETF/ST名称不得污染6位fallback名称。
    nm2: Dict[str, str] = {}
    add_name_mapping(nm2, "sh.000001", "上证指数")
    add_name_mapping(nm2, "sh.510300", "沪深300ETF")
    add_name_mapping(nm2, "sz.000001", "平安银行")
    add("edge::six_digit_fallback_only_common_stock", nm2.get("000001") == "平安银行" and nm2.get("sh.000001") == "上证指数" and "510300" not in nm2, f"name_map={nm2}")

    # v19深度边界：缓存文件同key去重必须选择mtime更新的文件，且sh.000001指数缓存不应进入普通股缓存列表。
    import tempfile
    old_cache_dirs = list(CACHE_DIRS)
    old_max_stocks = MAX_STOCKS
    try:
        with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
            d1 = Path(td1); d2 = Path(td2)
            old_file = d1 / "sz.000001.csv"
            new_file = d2 / "sz.000001.csv"
            idx_file = d2 / "sh.000001.csv"
            old_file.write_text("date,code,open,high,low,close,volume,amount,pct_chg\n2026-01-01,000001,10,11,9,10.5,1,1,1\n", encoding="utf-8")
            new_file.write_text("date,code,open,high,low,close,volume,amount,pct_chg\n2026-01-02,000001,10,11,9,10.5,1,1,1\n", encoding="utf-8")
            idx_file.write_text("date,code,open,high,low,close,volume,amount,pct_chg\n2026-01-02,000001,10,11,9,10.5,1,1,1\n", encoding="utf-8")
            os.utime(old_file, (1000, 1000))
            os.utime(new_file, (2000, 2000))
            os.utime(idx_file, (3000, 3000))
            CACHE_DIRS[:] = [d1, d2]
            globals()["MAX_STOCKS"] = 0
            found = iter_cache_files()
            found_names = [x.name for x in found]
            add("edge::cache_dedup_uses_newest_mtime_and_excludes_index", found_names == ["sz.000001.csv"], f"found={found_names}")
    finally:
        CACHE_DIRS[:] = old_cache_dirs
        globals()["MAX_STOCKS"] = old_max_stocks

    # v19深度边界：5%阴线必须是真阴线，假阳大跌不能混入。
    fake_yang_drop = synthetic_daily([
        ("2026-01-01", 10.0, 10.2, 9.8, 10.0, 1000000, 100000000, 0.0),
        ("2026-01-02", 9.2, 9.6, 9.0, 9.5, 1000000, 100000000, -5.0),
    ])
    fake_yang_ind = add_lingdong_indicators(fake_yang_drop, "000001")
    fake_yang_a1 = window_activity_counts(fake_yang_ind, 1)
    add("edge::fake_yang_big_drop_not_big_yin5", fake_yang_a1["big_yin5"] == 0, f"a1={fake_yang_a1}")

    # v19深度边界：大阴线基准必须有阴实体质量，小实体大跌日不能触发分手/强包。
    weak_prev = synthetic_daily([
        ("2026-01-01", 10.0, 10.8, 9.1, 9.9, 1000000, 100000000, -3.5),
        ("2026-01-02", 10.95, 11.2, 10.9, 11.1, 1000000, 100000000, 12.0),
    ])
    weak_prev_ind = add_lingdong_indicators(weak_prev, "000001")
    weak_prev_a1 = window_activity_counts(weak_prev_ind, 1)
    add("edge::weak_body_big_drop_not_reversal_base", weak_prev_a1["jump_separation"] == 0 and weak_prev_a1["strong_bullish_engulf"] == 0, f"a1={weak_prev_a1}")

    # v19深度边界：目标日推断必须忽略异常未来日期。
    future_only = normalize_hist(pd.DataFrame([{"date": "2026-06-18", "code": "000001", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100, "amount": 1000, "pct_chg": 1.0}]), "000001")
    today_like = make_good_active_rows()
    inferred_no_future = latest_common_cache_trade_day({"sz.000001": today_like, "sz.000002": future_only})
    add("edge::target_inference_ignores_future_dates", inferred_no_future != "2099-12-31" and bool(inferred_no_future), f"推断={inferred_no_future}")

    add("rule::sort_hits_uses_report_rank_key", "report_rank_key(x)[0]" in src or "report_rank_key(x)" in src, "sort_hits必须统一使用互斥事件综合排序，不再用原始big_bull7/big_yang5排序")

    # v20九项逻辑优化真实边界：不能只靠源码字符串，要覆盖实际输出和误判路径。
    add("rule::rank_split_activity_and_trade_quality", "lingdong_activity_rank_score" in function_names and "trade_quality_proxy_score" in function_names, "排序必须拆成股性活跃分与轻量交易质量代理，不能只按活跃追高")
    add("output::latest_csv_selected_contract_source", "OUTPUT_CSV 输出精选池" in src or "selected_rows, columns=columns).to_csv(OUTPUT_CSV" in src, "lingdong_latest.csv必须等同selected，不能再等同广义active_pool")

    priority = refresh_priority_codes(
        [("sz.000001", make_good_active_rows())],
        {"sz.000001": "平安银行", "000001": "平安银行"},
        "2099-01-01",
        10,
    )
    add("edge::refresh_priority_real_path_returns_candidate", priority == {"sz.000001"}, f"priority={priority}")

    amount_zero_rows = []
    for row in base_daily_rows(amount=100000000.0):
        d0, o0, h0, l0, c0, v0, a0 = row
        amount_zero_rows.append((d0, o0, h0, l0, c0, max(v0, 5000000.0), 0.0))
    amount_zero_hit = evaluate_lingdong(synthetic_daily(amount_zero_rows), item)
    add(
        "edge::amount_missing_estimated_no_nan",
        math.isfinite(amount_zero_hit.amount20) and amount_zero_hit.amount20 > 0 and "成交额估算" in amount_zero_hit.detail,
        f"amount20={amount_zero_hit.amount20}, detail={amount_zero_hit.detail}",
    )

    amount_none_rows = []
    for row in base_daily_rows(amount=100000000.0):
        d0, o0, h0, l0, c0, v0, a0 = row
        amount_none_rows.append((d0, o0, h0, l0, c0, 0.0, 0.0))
    amount_none_hit = evaluate_lingdong(synthetic_daily(amount_none_rows), item)
    add("edge::amount_and_volume_missing_data_block_priority", amount_none_hit.status == DATA_BAD and amount_none_hit.amount20 == 0.0, f"状态={amount_none_hit.status}, amount20={amount_none_hit.amount20}")

    trap_rows = base_daily_rows(amount=120000000.0)
    d1, o1, h1, l1, c1, v1, a1 = trap_rows[-2]
    p_open = c1 * 1.02
    p_close = p_open * 0.955
    p_high = p_open * 1.01
    trap_rows[-2] = (d1, round(p_open, 3), round(p_high, 3), round(p_close * 0.99, 3), round(p_close, 3), v1 * 1.6, a1 * 1.6, -4.5)
    d2, o2, h2, l2, c2, v2, a2 = trap_rows[-1]
    t_open = p_high * 1.01
    t_low = t_open * 0.995
    t_close = t_open * 1.01
    t_high = t_low + (t_close - t_low) / 0.65
    trap_rows[-1] = (d2, round(t_open, 3), round(t_high, 3), round(t_low, 3), round(t_close, 3), v2 * 1.8, a2 * 1.8, 6.0)
    trap_ind = add_lingdong_indicators(synthetic_daily(trap_rows), "000001")
    trap_a1 = window_activity_counts(trap_ind, 1)
    add("edge::high_position_trap_reversal_raw_not_primary", trap_a1["jump_separation"] == 1 and trap_a1["primary_jump_separation"] == 0 and trap_a1["score"] < ACTIVITY_SCORE_JUMP_SEPARATION, f"a1={trap_a1}")

    repair_rows = base_daily_rows(amount=120000000.0)
    i = -5
    d0, o0, h0, l0, c0, v0, a0 = repair_rows[i]
    r_open = c0 * 1.02
    r_close = c0 * 0.94
    repair_rows[i] = (d0, round(r_open, 3), round(r_open * 1.005, 3), round(r_close * 0.99, 3), round(r_close, 3), v0 * 2.2, a0 * 2.2, -6.0)
    d1, o1, h1, l1, c1, v1, a1 = repair_rows[i + 1]
    r_mid = (r_open + r_close) / 2.0
    repair_rows[i + 1] = (d1, round(r_mid * 0.995, 3), round(r_mid * 1.03, 3), round(r_mid * 0.99, 3), round(r_mid * 1.015, 3), v1, a1, 3.0)
    repair_ind = add_lingdong_indicators(synthetic_daily(repair_rows), "000001")
    repair_bar = repair_ind.iloc[len(repair_ind) + i]
    add("edge::repaired_long_bear_not_destructive", bool(repair_bar.get("volume_long_bear")) and bool(repair_bar.get("volume_long_bear_repaired_3d")) and not bool(repair_bar.get("volume_long_bear_destructive")), f"bar={repair_bar[['volume_long_bear','volume_long_bear_repaired_3d','volume_long_bear_destructive']].to_dict()}")

    old_attack_rows = base_daily_rows(amount=120000000.0)
    j = 35
    d0, o0, h0, l0, c0, v0, a0 = old_attack_rows[j]
    old_attack_rows[j] = (d0, round(c0 * 1.01, 3), round(c0 * 1.11, 3), round(c0 * 1.005, 3), round(c0 * 1.10, 3), v0 * 2.0, a0 * 2.0, 10.0)
    old_attack_hit = evaluate_lingdong(synthetic_daily(old_attack_rows), item)
    add("edge::old_attack_memory_decay_not_good", old_attack_hit.status != GOOD_ACTIVE and not old_attack_hit.attack_memory, f"状态={old_attack_hit.status}, detail={old_attack_hit.detail}")

    cooled_hot = replace(
        good_for_report,
        limitup_count_100=20,
        price_attack7_ex_limitup_count_100=4,
        price_attack5_plain_count_100=8,
        big_yin5_count_100=2,
        big_yin5_count_20=0,
        long_upper_count_20=0,
        volume_long_bear_count_20_recent=0,
        range20_pct=3.2,
        small_body_ratio_60=0.55,
        trend_efficiency_20=0.35,
        recent_activity_score_20=5.0,
        last_big_attack_age=18,
        last_limitup_age=18,
    )
    add("edge::cooled_extreme_hot_not_forced_excluded", not is_extreme_hot_for_report(cooled_hot), f"extreme={is_extreme_hot_for_report(cooled_hot)}")

    old_paths = (OUTPUT_CSV, OUTPUT_ACTIVE_POOL_CSV, OUTPUT_SELECTED_CSV, OUTPUT_ALL_CSV, OUTPUT_JSON, OUTPUT_MD, SELF_CHECK_JSON, OUTPUT_DIAGNOSTIC_JSON)
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            globals()["OUTPUT_CSV"] = base / "lingdong_latest.csv"
            globals()["OUTPUT_ACTIVE_POOL_CSV"] = base / "lingdong_active_pool.csv"
            globals()["OUTPUT_SELECTED_CSV"] = base / "lingdong_selected.csv"
            globals()["OUTPUT_ALL_CSV"] = base / "lingdong_all.csv"
            globals()["OUTPUT_JSON"] = base / "lingdong_latest.json"
            globals()["OUTPUT_MD"] = base / "lingdong_report.md"
            globals()["SELF_CHECK_JSON"] = base / "lingdong_self_check.json"
            globals()["OUTPUT_DIAGNOSTIC_JSON"] = base / "lingdong_diagnostics.json"
            stat0 = ScanStat(VERSION, TARGET_DASH, 2, 2, 2, 0, 1, "self_check")
            current_good = replace(good_for_report, latest_trade_day=TARGET_DASH, name="平安银行")
            current_st = replace(current_good, name="*ST测试")
            current_stale = replace(current_good, latest_trade_day="2000-01-01")
            write_outputs([current_good, current_st, current_stale], stat0, [])
            latest_df = pd.read_csv(globals()["OUTPUT_CSV"])
            selected_df = pd.read_csv(globals()["OUTPUT_SELECTED_CSV"])
            active_df = pd.read_csv(globals()["OUTPUT_ACTIVE_POOL_CSV"])
            add("edge::latest_csv_equals_selected_not_active_pool", len(latest_df) == len(selected_df) == 1 and len(active_df) >= len(selected_df), f"latest={len(latest_df)} selected={len(selected_df)} active={len(active_df)}")
            add("edge::active_pool_declared_non_tradable", "pool_role" in active_df.columns and "tradable_candidate" in active_df.columns and set(active_df["tradable_candidate"].astype(str).str.lower()).issubset({"false", "0"}), f"cols={list(active_df.columns)} tradable={active_df.get('tradable_candidate').tolist() if 'tradable_candidate' in active_df.columns else []}")
    finally:
        (globals()["OUTPUT_CSV"], globals()["OUTPUT_ACTIVE_POOL_CSV"], globals()["OUTPUT_SELECTED_CSV"], globals()["OUTPUT_ALL_CSV"], globals()["OUTPUT_JSON"], globals()["OUTPUT_MD"], globals()["SELF_CHECK_JSON"], globals()["OUTPUT_DIAGNOSTIC_JSON"]) = old_paths

    # v23可用数据范围内的剩余逻辑硬化：正向统一primary、炸板扣分、无前缀缓存降级扫描。
    add("rule::positive_score_uses_primary_events", 'w.get("primary_big_bull7"' in src and 'w.get("primary_big_yang5"' in src and 'w.get("primary_gap_up"' in src, "近端活跃分必须只用primary正向事件，raw event只保留审计")
    add("edge::limit_failed_penalizes_recent_score", weak_prev_a1["limit_failed"] == 1 and weak_prev_a1["primary_limit_failed_attack"] == 1 and weak_prev_a1["score"] > 0.0, f"a1={weak_prev_a1}")

    old_cache_dirs_v23 = list(CACHE_DIRS)
    old_max_stocks_v23 = MAX_STOCKS
    old_prefix_map = dict(CACHE_EXCHANGE_PREFIX_OK)
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dtmp = Path(td)
            f_un = dtmp / "000001.csv"
            f_un.write_text("date,code,name,open,high,low,close,volume,amount,pct_chg\n2026-01-01,000001,平安银行,10,11,9,10.5,1000000,10500000,1\n", encoding="utf-8")
            CACHE_DIRS[:] = [dtmp]
            globals()["MAX_STOCKS"] = 0
            CACHE_EXCHANGE_PREFIX_OK.clear()
            found_un = iter_cache_files()
            un_key = cache_identity(f_un)
            add("edge::unprefixed_cache_scanned_but_marked", len(found_un) == 1 and found_un[0].name == "000001.csv" and CACHE_EXCHANGE_PREFIX_OK.get(un_key) is False, f"found={[x.name for x in found_un]} map={CACHE_EXCHANGE_PREFIX_OK}")
    finally:
        CACHE_DIRS[:] = old_cache_dirs_v23
        globals()["MAX_STOCKS"] = old_max_stocks_v23
        CACHE_EXCHANGE_PREFIX_OK.clear(); CACHE_EXCHANGE_PREFIX_OK.update(old_prefix_map)

    unprefixed_hit = replace(good_for_report, cache_has_exchange_prefix=False, latest_trade_day=TARGET_DASH, name="平安银行")
    add("edge::unprefixed_cache_not_telegram_eligible_by_default", not telegram_eligible(unprefixed_hit) and "cache_unprefixed" in not_selected_reason(unprefixed_hit), f"eligible={telegram_eligible(unprefixed_hit)} reason={not_selected_reason(unprefixed_hit)}")

    # v21数据口径与诊断硬化：复权口径、成交量单位、异常日志必须进入真实边界自检。
    add("rule::data_profile_helpers_exist", "infer_adjust_flag" in function_names and "infer_volume_unit" in function_names and "can_merge_fresh_adjusted_bar" in function_names, "必须存在复权口径/成交量单位/拼接安全判断函数")
    add("rule::diagnostic_artifact_exists", "OUTPUT_DIAGNOSTIC_JSON" in src and "DIAGNOSTIC_EVENTS" in src and "record_exception" in src, "关键容错必须写入诊断artifact，不能静默吞掉")

    qfq_existing = normalize_hist(pd.DataFrame([
        {"date": "2026-01-01", "code": "000001", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000000, "amount": 10500000, "pct_chg": 1, "adjust_flag": "qfq", "volume_unit": "share"}
    ]), "000001")
    qfq_fresh = normalize_hist(pd.DataFrame([
        {"date": "2026-01-02", "code": "000001", "open": 10.5, "high": 11, "low": 10.2, "close": 10.8, "volume": 1000000, "amount": 10800000, "pct_chg": 2, "adjust_flag": "qfq", "volume_unit": "share"}
    ]), "000001")
    raw_unknown = normalize_hist(pd.DataFrame([
        {"date": "2026-01-01", "code": "000001", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000000, "amount": 10500000, "pct_chg": 1}
    ]), "000001")
    add("edge::qfq_adjust_profile_allows_merge", can_merge_fresh_adjusted_bar(qfq_existing, qfq_fresh)[0], f"qfq_merge={can_merge_fresh_adjusted_bar(qfq_existing, qfq_fresh)}")
    add("edge::unknown_adjust_blocks_qfq_merge_by_default", not can_merge_fresh_adjusted_bar(raw_unknown, qfq_fresh)[0], f"unknown_merge={can_merge_fresh_adjusted_bar(raw_unknown, qfq_fresh)}")

    lot_df = normalize_hist(pd.DataFrame([
        {"date": f"2026-01-{i+1:02d}", "code": "000001", "open": 10, "high": 11, "low": 9, "close": 10+i*0.01, "volume": 10000, "amount": (10+i*0.01)*10000*100, "pct_chg": 0.1} for i in range(10)
    ]), "000001")
    share_df = normalize_hist(pd.DataFrame([
        {"date": f"2026-02-{i+1:02d}", "code": "000001", "open": 10, "high": 11, "low": 9, "close": 10+i*0.01, "volume": 1000000, "amount": (10+i*0.01)*1000000, "pct_chg": 0.1} for i in range(10)
    ]), "000001")
    add("edge::infer_volume_unit_lot_from_amount_ratio", infer_volume_unit(lot_df) == "lot", f"unit={infer_volume_unit(lot_df)}")
    add("edge::infer_volume_unit_share_from_amount_ratio", infer_volume_unit(share_df) == "share", f"unit={infer_volume_unit(share_df)}")

    amount_lot_missing = []
    for row in base_daily_rows(amount=100000000.0):
        d0, o0, h0, l0, c0, v0, a0 = row
        amount_lot_missing.append({"date": d0, "code": "000001", "open": o0, "high": h0, "low": l0, "close": c0, "volume": max(v0 / 100.0, 10000.0), "amount": 0.0, "pct_chg": 0.0, "volume_unit": "lot"})
    lot_hit = evaluate_lingdong(normalize_hist(pd.DataFrame(amount_lot_missing), "000001"), item)
    add("edge::amount_estimation_respects_lot_unit", lot_hit.amount20 > 50000000 and lot_hit.volume_unit == "lot" and lot_hit.amount_source == "estimated", f"amount20={lot_hit.amount20}, unit={lot_hit.volume_unit}, source={lot_hit.amount_source}, detail={lot_hit.detail}")

    before_diag = len(DIAGNOSTIC_EVENTS)
    record_diagnostic("self_check_diag", "000001", "诊断记录测试", "test", "ok")
    add("edge::diagnostic_event_records_stage_code", len(DIAGNOSTIC_EVENTS) == before_diag + 1 and DIAGNOSTIC_EVENTS[-1].get("stage") == "self_check_diag" and DIAGNOSTIC_EVENTS[-1].get("code") == "000001", f"diag={DIAGNOSTIC_EVENTS[-1] if DIAGNOSTIC_EVENTS else {}}")

    # v24新增边界：一字/极窄幅涨停不能被误判成炸板；主板9.4%强阳不能按收盘涨停处理。
    one_limit_rows = base_daily_rows(amount=120000000.0)
    d0, o0, h0, l0, c0, v0, a0 = one_limit_rows[-1]
    flat_limit = round(c0 * 1.10, 3)
    one_limit_rows[-1] = (d0, flat_limit, flat_limit, flat_limit, flat_limit, v0 * 2, a0 * 2, 10.0)
    one_limit_ind = add_lingdong_indicators(synthetic_daily(one_limit_rows), "000001")
    one_limit_a1 = window_activity_counts(one_limit_ind, 1)
    add("edge::one_word_limitup_not_failed", one_limit_a1["limitup"] == 1 and one_limit_a1["limit_failed"] == 0 and one_limit_a1["score"] >= ACTIVITY_SCORE_LIMITUP, f"a1={one_limit_a1}")

    strong94_rows = base_daily_rows(amount=120000000.0)
    d0, o0, h0, l0, c0, v0, a0 = strong94_rows[-1]
    new_c = round(c0 * 1.094, 3)
    strong94_rows[-1] = (d0, round(c0 * 1.01, 3), round(new_c * 1.002, 3), round(c0 * 1.005, 3), new_c, v0 * 2, a0 * 2, 9.4)
    strong94_ind = add_lingdong_indicators(synthetic_daily(strong94_rows), "000001")
    strong94_a1 = window_activity_counts(strong94_ind, 1)
    add("edge::mainboard_94pct_not_close_limitup", strong94_a1["limitup"] == 0 and strong94_a1["limit_touch_up"] >= 1, f"a1={strong94_a1}")

    # v24新增边界：衰减攻击记忆也必须单日互斥，不能涨停+跳分重复记忆。
    overlap = one_limit_ind.copy()
    overlap.loc[overlap.index[-1], "primary_jump_separation"] = True
    overlap_score = decayed_activity_memory_score(overlap.tail(1), 1)
    add("edge::decayed_memory_single_day_mutual_exclusive", abs(overlap_score - ACTIVITY_SCORE_LIMITUP) < 1e-6, f"score={overlap_score}")

    # v24新增边界：炸板三日修复后降扣，未修复炸板才重罚。
    fail_repair_rows = base_daily_rows(days=125, amount=120000000.0)
    i = -4
    d0, o0, h0, l0, c0, v0, a0 = fail_repair_rows[i]
    prev = fail_repair_rows[i - 1][4]
    fail_repair_rows[i] = (d0, round(prev * 1.02, 3), round(prev * 1.095, 3), round(prev * 1.01, 3), round(prev * 1.04, 3), v0 * 2, a0 * 2, 4.0)
    d1, o1, h1, l1, c1, v1, a1 = fail_repair_rows[i + 1]
    fail_repair_rows[i + 1] = (d1, round(prev * 1.035, 3), round(prev * 1.075, 3), round(prev * 1.03, 3), round(prev * 1.065, 3), v1 * 1.5, a1 * 1.5, 2.0)
    fail_repair_ind = add_lingdong_indicators(synthetic_daily(fail_repair_rows), "000001")
    repaired_count = int(fail_repair_ind["limit_failed_repaired_3d"].sum())
    unrepaired_count = int(fail_repair_ind["limit_failed_unrepaired"].sum())
    add("edge::limit_failed_repaired_3d_reduces_unrepaired_count", repaired_count >= 1 and unrepaired_count == 0, f"repaired={repaired_count} unrepaired={unrepaired_count}")

    # v24新增边界：数据硬拦截优先于低流动性归因。
    blocked_hit = evaluate_lingdong(make_low_liquidity_rows().assign(amount=0.0, volume=0.0), item)
    add("edge::data_quality_block_priority_before_low_liquidity", blocked_hit.status == DATA_BAD and "数据质量硬拦截" in blocked_hit.detail, f"status={blocked_hit.status} detail={blocked_hit.detail}")


    # v25新增边界：pct_chg小数比例口径必须自动缩放，不能把真涨停当0.1%小涨。
    pct_scale = synthetic_daily([
        ("2026-01-01", 10.0, 10.1, 9.9, 10.0, 1000000, 100000000, 0.0),
        ("2026-01-02", 10.9, 11.0, 10.8, 11.0, 1000000, 100000000, 0.10),
    ])
    pct_scale_ind = add_lingdong_indicators(pct_scale, "000001")
    pct_scale_a1 = window_activity_counts(pct_scale_ind, 1)
    add("edge::pct_chg_decimal_ratio_scaled_to_percent", pct_scale_a1["limitup"] == 1 and s(pct_scale_ind.iloc[-1].get("ret_source")) in {"pct_scaled", "price_calc"}, f"a1={pct_scale_a1}, ret_source={pct_scale_ind.iloc[-1].get('ret_source')}, ret={pct_scale_ind.iloc[-1].get('ret_pct')}")

    # v25新增边界：炸板是独立活跃分歧事件，不伪装普通5%阳，也不一棍子打死。
    fail_attack_rows = base_daily_rows(amount=120000000.0)
    d0, o0, h0, l0, c0, v0, a0 = fail_attack_rows[-1]
    prev = fail_attack_rows[-2][4]
    fail_attack_rows[-1] = (d0, round(prev * 1.03, 3), round(prev * 1.10, 3), round(prev * 1.02, 3), round(prev * 1.07, 3), v0 * 2.0, a0 * 2.0, 7.0)
    fail_attack_ind = add_lingdong_indicators(synthetic_daily(fail_attack_rows), "000001")
    fail_attack_a1 = window_activity_counts(fail_attack_ind, 1)
    add("edge::limit_failed_is_independent_positive_divergence_event", fail_attack_a1["limit_failed"] == 1 and fail_attack_a1["primary_limit_failed_attack"] == 1 and fail_attack_a1["primary_big_yang5"] == 0 and fail_attack_a1["score"] > 0, f"a1={fail_attack_a1}")

    # v25新增边界：数据异常与样本不足分开统计。
    add("edge::data_bad_status_distinct_from_data_short", DATA_BAD != DATA_SHORT and STATUS_ORDER.get(DATA_BAD, 99) > STATUS_ORDER.get(DATA_SHORT, 0), f"DATA_BAD={DATA_BAD}, DATA_SHORT={DATA_SHORT}")


    # v26新增边界：最新1-3日确认窗口未走完时，炸板/放量长阴只能pending，不能提前判未修复/破坏。
    pending_fail_rows = base_daily_rows(amount=120000000.0)
    d0, o0, h0, l0, c0, v0, a0 = pending_fail_rows[-1]
    prev = pending_fail_rows[-2][4]
    pending_fail_rows[-1] = (d0, round(prev * 1.03, 3), round(prev * 1.10, 3), round(prev * 1.02, 3), round(prev * 1.07, 3), v0 * 2.0, a0 * 2.0, 7.0)
    pending_fail_ind = add_lingdong_indicators(synthetic_daily(pending_fail_rows), "000001")
    pending_fail_a1 = window_activity_counts(pending_fail_ind, 1)
    add("edge::recent_limit_failed_pending_not_unrepaired", pending_fail_a1["limit_failed"] == 1 and pending_fail_a1["limit_failed_pending"] == 1 and pending_fail_a1["limit_failed_unrepaired"] == 0, f"a1={pending_fail_a1}")

    pending_bear_rows = base_daily_rows(amount=120000000.0)
    d0, o0, h0, l0, c0, v0, a0 = pending_bear_rows[-1]
    prev = pending_bear_rows[-2][4]
    pending_bear_rows[-1] = (d0, round(prev * 1.01, 3), round(prev * 1.015, 3), round(prev * 0.93, 3), round(prev * 0.94, 3), v0 * 2.2, a0 * 2.2, -6.0)
    pending_bear_ind = add_lingdong_indicators(synthetic_daily(pending_bear_rows), "000001")
    add("edge::recent_volume_long_bear_pending_not_destructive", bool(pending_bear_ind.iloc[-1].get("volume_long_bear_pending_3d")) and not bool(pending_bear_ind.iloc[-1].get("volume_long_bear_destructive")), f"last={pending_bear_ind.iloc[-1][['volume_long_bear','volume_long_bear_pending_3d','volume_long_bear_destructive']].to_dict()}")

    # v26新增边界：摸板强收盘未封，不能混成普通7%强阳或炸板，应单独做活跃分歧事件。
    touch_strong_rows = base_daily_rows(amount=120000000.0)
    d0, o0, h0, l0, c0, v0, a0 = touch_strong_rows[-1]
    prev = touch_strong_rows[-2][4]
    touch_strong_rows[-1] = (d0, round(prev * 1.035, 3), round(prev * 1.10, 3), round(prev * 1.03, 3), round(prev * 1.086, 3), v0 * 2.0, a0 * 2.0, 8.6)
    touch_strong_ind = add_lingdong_indicators(synthetic_daily(touch_strong_rows), "000001")
    touch_strong_a1 = window_activity_counts(touch_strong_ind, 1)
    add("edge::touch_limit_unsealed_strong_is_independent_event", touch_strong_a1["limit_touch_up"] == 1 and touch_strong_a1["limit_failed"] == 0 and touch_strong_a1["primary_limit_touch_unsealed_strong"] == 1 and touch_strong_a1["primary_big_bull7"] == 0, f"a1={touch_strong_a1}")

    # v26新增边界：异常涨跌幅/疑似除权断层不能制造假活跃事件。
    impossible_base = base_daily_rows(amount=120000000.0)
    d0, o0, h0, l0, c0, v0, a0 = impossible_base[-1]
    prev = impossible_base[-2][4]
    impossible_base[-1] = (d0, round(prev * 1.40, 3), round(prev * 1.42, 3), round(prev * 1.38, 3), round(prev * 1.40, 3), v0 * 2.0, a0 * 2.0, 40.0)
    impossible_rows = synthetic_daily(impossible_base)
    impossible_ind = add_lingdong_indicators(impossible_rows, "000001")
    impossible_a1 = window_activity_counts(impossible_ind, 1)
    impossible_hit = evaluate_lingdong(impossible_rows, item)
    add("edge::impossible_return_blocks_fake_activity", impossible_a1["impossible_return"] == 1 and impossible_a1["limitup"] == 0 and impossible_a1["score"] <= 0.0 and impossible_hit.status == DATA_BAD, f"a1={impossible_a1}, status={impossible_hit.status}, detail={impossible_hit.detail}")

    # v26新增边界：ST股票按5%限幅近似，5%涨停应能识别；同时Telegram仍过滤ST。
    st_rows = synthetic_daily([
        ("2026-01-01", 10.0, 10.1, 9.9, 10.0, 1000000, 100000000, 0.0),
        ("2026-01-02", 10.45, 10.50, 10.40, 10.50, 1000000, 105000000, 5.0),
    ])
    st_rows["name"] = "ST测试"
    st_ind = add_lingdong_indicators(st_rows, "000001")
    st_a1 = window_activity_counts(st_ind, 1)
    add("edge::st_limit_ratio_5pct_detected_but_filtered", st_a1["limitup"] == 1 and not common_stock_ok("sz.000001", "000001", "ST测试"), f"a1={st_a1}")

    # v26新增边界：涨停价用ROUND_HALF_UP，不使用银行家舍入。
    add("edge::limit_price_round_half_up", round_tick_half_up(10.005) == 10.01 and round_tick_half_up(10.004) == 10.00, f"10.005->{round_tick_half_up(10.005)} 10.004->{round_tick_half_up(10.004)}")

    return items


def run_self_check(rounds: int) -> None:
    ensure_report_dir()
    all_rounds: List[Dict[str, Any]] = []

    for round_id in range(1, max(1, rounds) + 1):
        items = self_check_once()
        ok = all(x["ok"] for x in items)
        all_rounds.append({"round": round_id, "ok": ok, "items": items})

        print(f"灵动自检第{round_id}遍：{'通过' if ok else '失败'}", flush=True)
        for item in items:
            print(f"  [{'OK' if item['ok'] else 'FAIL'}] {item['name']}｜{item['detail']}", flush=True)

        if not ok:
            SELF_CHECK_JSON.write_text(json.dumps(all_rounds, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(f"灵动自检第{round_id}遍失败")

    SELF_CHECK_JSON.write_text(json.dumps(all_rounds, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="灵动日K活性扫描器")
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-rounds", type=int, default=3)
    parser.add_argument("--limit", type=int, default=MAX_STOCKS)
    args = parser.parse_args()

    try:
        if args.self_test:
            run_self_check(args.self_test_rounds)
            return 0
        if args.scan:
            return run_scan(args.limit)
        parser.print_help()
        return 0
    except Exception as exc:
        print(f"灵动运行失败: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
