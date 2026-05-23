#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SECOND_EMPLOYEE_V12.0_LOCAL_FIRST_PROFIT_ENGINE

二号员工 V12.0：本地K线先行、外部数据预留、赚钱权限闭环。

核心原则：
1. 当前只依赖本地日线 OHLCV，可稳定运行、回测、复盘、自检。
2. 外部公告/财务/监管/板块增强数据只做契约预留，不伪造事实结论。
3. 正式池不是总分 Top，而是必须通过：数据门控、股票池、流动性、本地风险、主假设、RR、可执行性、模型权限。
4. 规则全部落为真函数、真字段、真输出；无伪代码、无猴子补丁、无隐式外部依赖。

运行：
  python second_employee_wallstreet_v12_0.py run --input daily.csv --output-dir out
复盘：
  python second_employee_wallstreet_v12_0.py review --recommendations out/second_employee_review_snapshot.csv --eod daily_with_future.csv --output out/second_employee_review_labels.csv
汇总/生成权限：
  python second_employee_wallstreet_v12_0.py summary --review out/second_employee_review_labels.csv --output-dir out
滚动回测：
  python second_employee_wallstreet_v12_0.py backtest --input daily.csv --output-dir out --backtest-start 20240101 --backtest-end 20260522
自检：
  python second_employee_wallstreet_v12_0.py selfcheck
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

MODEL_VERSION = "SECOND_EMPLOYEE_V12.0_LOCAL_FIRST_PROFIT_ENGINE"
EPS = 1e-9


# =============================================================================
# 0. Configuration / Field Registry
# =============================================================================


@dataclass(frozen=True)
class ThresholdConfig:
    min_price: float = 2.0
    min_listed_days: int = 60
    min_bars_available: int = 250
    min_effective_days60: int = 55
    min_median_amount20: float = 80_000_000.0
    min_today_amount: float = 100_000_000.0

    std_vol_prev_min: float = 1.80
    std_vol_prev_max: float = 2.50
    std_amt20_min: float = 1.50
    std_amt20_max: float = 3.00
    healthy_amt20_min: float = 1.20
    healthy_amt20_max: float = 2.80
    bullish_std_min: float = 70.0
    bullish_healthy_min: float = 60.0
    close_loc_strong: float = 0.75
    close_loc_ok: float = 0.65
    upper_shadow_healthy_max: float = 0.30
    upper_shadow_stall_min: float = 0.35
    stall_amt20_min: float = 1.80
    stall_pct_chg_max: float = 0.015

    core_line_min_score: float = 76.0
    core_line_max_fake_breaks: int = 3
    body_above_pressure_min: float = 0.60
    min_next_pressure_distance: float = 0.10
    min_next_pressure_distance_h1: float = 0.12
    platform_min_days: int = 15
    platform_max_tightness: float = 0.18
    pre_breakout_score_min: float = 68.0
    pre_breakout_distance_max: float = 0.055

    prior_breakout_quality_min: float = 74.0
    pullback_volume_contraction_min: float = 58.0
    pullback_min_days: int = 3
    pullback_max_days: int = 25
    pullback_close_distance_max: float = 0.050

    rr_strong: float = 1.80
    rr_normal: float = 2.10
    rr_weak: float = 2.60
    rr_panic: float = 999.0
    local_data_rr_penalty: float = 0.20
    external_reserved_rr_penalty: float = 0.15
    max_gap_pct_formal: float = 0.03
    buy_slippage_pct: float = 0.005
    sell_slippage_pct: float = 0.005
    commission_pct: float = 0.0003
    stamp_duty_pct: float = 0.001
    impact_cost_pct: float = 0.001

    top_limit: int = 5
    max_sector_formal: int = 2
    max_hypothesis_formal: int = 3
    h1_position_base: float = 0.18
    h2_position_base: float = 0.22
    h3_position_base: float = 0.08
    h4_position_base: float = 0.16

    low_position_pct250_max: float = 55.0
    high_position_pct250_risk: float = 83.0
    vbp_window: int = 1000
    vbp_dense_quantile: float = 0.82

    permission_min_sample: int = 30
    permission_min_win_rate: float = 0.47
    permission_min_avg_ret_t8: float = 0.012
    permission_max_stop_rate: float = 0.38


CFG = ThresholdConfig()


@dataclass(frozen=True)
class FieldSpec:
    field_name: str
    dtype: str
    producer: str
    known_at: str
    family: str
    score_effect: str
    hard_gate: str
    missing_policy: str
    status: str = "active"


FIELD_REGISTRY: Dict[str, FieldSpec] = {
    "trade_date": FieldSpec("trade_date", "str", "LocalKlineLoader", "recommendation_day", "key", "none", "BLOCK", "BLOCK"),
    "known_at": FieldSpec("known_at", "str", "DataContract", "recommendation_day_after_close", "governance", "none", "BLOCK", "BLOCK"),
    "data_scope": FieldSpec("data_scope", "str", "DataContract", "recommendation_day", "governance", "permission", "WARN", "LOCAL_KLINE_ONLY"),
    "stock_code": FieldSpec("stock_code", "str", "LocalKlineLoader", "static", "key", "none", "BLOCK", "BLOCK"),
    "stock_name": FieldSpec("stock_name", "str", "LocalKlineLoader", "static", "key", "none", "WARN", "EMPTY"),
    "open": FieldSpec("open", "float", "LocalKlineLoader", "after_close", "ohlcv", "feature_only", "BLOCK", "BLOCK"),
    "high": FieldSpec("high", "float", "LocalKlineLoader", "after_close", "ohlcv", "feature_only", "BLOCK", "BLOCK"),
    "low": FieldSpec("low", "float", "LocalKlineLoader", "after_close", "ohlcv", "feature_only", "BLOCK", "BLOCK"),
    "close": FieldSpec("close", "float", "LocalKlineLoader", "after_close", "ohlcv", "feature_only", "BLOCK", "BLOCK"),
    "volume": FieldSpec("volume", "float", "LocalKlineLoader", "after_close", "ohlcv", "feature_only", "BLOCK", "BLOCK"),
    "amount": FieldSpec("amount", "float", "LocalKlineLoader", "after_close", "ohlcv", "feature_only", "BLOCK", "BLOCK"),
    "data_gate_pass": FieldSpec("data_gate_pass", "bool", "DataGate", "recommendation_day", "gate", "hard_gate", "BLOCK", "False"),
    "universe_pass": FieldSpec("universe_pass", "bool", "UniverseGate", "recommendation_day", "universe", "hard_gate", "BLOCK", "False"),
    "liquidity_pass": FieldSpec("liquidity_pass", "bool", "LiquidityGate", "recommendation_day", "liquidity", "hard_gate", "BLOCK", "False"),
    "local_risk_action": FieldSpec("local_risk_action", "str", "LocalTechnicalRiskGate", "recommendation_day", "risk", "hard_gate", "BLOCK", "BLOCK"),
    "external_risk_status": FieldSpec("external_risk_status", "str", "ExternalRiskReservedAdapter", "reserved", "risk", "reserved_shadow", "none", "RESERVED_NOT_CONNECTED", "reserved"),
    "market_regime": FieldSpec("market_regime", "str", "MarketRegimeLocal", "after_close", "market", "permission", "WATCH", "WEAK"),
    "rr_min": FieldSpec("rr_min", "float", "MarketRegimeLocal", "after_close", "execution", "hard_gate", "BLOCK", "999"),
    "sector_heat_score": FieldSpec("sector_heat_score", "float", "SectorHeatLocal", "after_close", "sector", "context_only", "none", "0"),
    "sector_data_valid": FieldSpec("sector_data_valid", "bool", "SectorHeatLocal", "after_close", "sector", "context_only", "none", "False"),
    "core_pressure_line": FieldSpec("core_pressure_line", "float", "CoreLineEngine", "recommendation_day", "structure", "hypothesis_input", "WATCH", "NULL"),
    "pressure_band_lower": FieldSpec("pressure_band_lower", "float", "CoreLineEngine", "recommendation_day", "structure", "execution_input", "WATCH", "NULL"),
    "pressure_band_upper": FieldSpec("pressure_band_upper", "float", "CoreLineEngine", "recommendation_day", "structure", "hypothesis_input", "WATCH", "NULL"),
    "core_support_line": FieldSpec("core_support_line", "float", "CoreLineEngine", "recommendation_day", "structure", "execution_input", "WARN", "NULL"),
    "next_pressure_price": FieldSpec("next_pressure_price", "float", "StructureMap", "recommendation_day", "space", "hard_gate", "BLOCK", "NULL"),
    "next_pressure_distance": FieldSpec("next_pressure_distance", "float", "StructureMap", "recommendation_day", "space", "hard_gate", "BLOCK", "0"),
    "standard_volume_event": FieldSpec("standard_volume_event", "bool", "EventEngine", "recommendation_day", "event", "hypothesis_input", "none", "False"),
    "healthy_volume_event": FieldSpec("healthy_volume_event", "bool", "EventEngine", "recommendation_day", "event", "hypothesis_input", "none", "False"),
    "volume_stall_flag": FieldSpec("volume_stall_flag", "bool", "EventEngine", "recommendation_day", "risk", "risk_penalty", "none", "False"),
    "primary_hypothesis": FieldSpec("primary_hypothesis", "str", "HypothesisEngine", "recommendation_day", "hypothesis", "formal_gate", "BLOCK", "NONE"),
    "primary_setup_grade": FieldSpec("primary_setup_grade", "str", "HypothesisEngine", "recommendation_day", "hypothesis", "formal_gate", "BLOCK", "NONE"),
    "confirmation_state": FieldSpec("confirmation_state", "str", "ConfirmationEngine", "recommendation_day", "confirmation", "formal_gate", "BLOCK", "none"),
    "entry_price": FieldSpec("entry_price", "float", "ExecutionEngine", "recommendation_day", "execution", "formal_gate", "BLOCK", "BLOCK"),
    "defense_price": FieldSpec("defense_price", "float", "ExecutionEngine", "recommendation_day", "execution", "formal_gate", "BLOCK", "BLOCK"),
    "target_price": FieldSpec("target_price", "float", "ExecutionEngine", "recommendation_day", "execution", "formal_gate", "BLOCK", "BLOCK"),
    "rr_net": FieldSpec("rr_net", "float", "ExecutionEngine", "recommendation_day", "execution", "formal_gate", "BLOCK", "BLOCK"),
    "formal_pool_flag": FieldSpec("formal_pool_flag", "bool", "FinalJudge", "recommendation_day", "output", "output", "none", "False"),
    "watchlist_flag": FieldSpec("watchlist_flag", "bool", "FinalJudge", "recommendation_day", "output", "output", "none", "False"),
    "model_permission_allowed": FieldSpec("model_permission_allowed", "bool", "PermissionEngine", "recommendation_day", "permission", "formal_gate", "BLOCK", "False"),
    "review_status": FieldSpec("review_status", "str", "ReviewEngine", "review_day", "review", "review_only", "none", "pending"),
}


@dataclass(frozen=True)
class MarketState:
    trade_date: str
    regime: str
    score: float
    rr_min: float
    formal_cap: int
    position_cap: float
    reason: str
    allowed_entry_types: Tuple[str, ...]
    local_data_only: bool = True
    external_risk_status: str = "RESERVED_NOT_CONNECTED"


@dataclass(frozen=True)
class HypothesisResult:
    hypothesis_id: str
    eligible: bool
    setup_grade: str
    confirmation_state: str
    hypothesis_score: float
    setup_score: float
    context_score: float
    confirmation_score: float
    reason: str
    entry_type: str
    family: str


@dataclass(frozen=True)
class ExecutionPlan:
    entry_type: str
    entry_price: float
    defense_price: float
    target_price: float
    stop_distance: float
    rr_net: float
    rr_pass: bool
    executable_flag: bool
    next_day_entry_rule: str
    entry_valid_until: str
    max_allowed_gap_pct: float
    slippage_assumption_pct: float
    abandon_flags: List[str]
    position_pct: float
    defense_source: str
    target_source: str


# =============================================================================
# 1. Common helpers
# =============================================================================


def num(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        if isinstance(x, str) and not x.strip():
            return default
        return float(x)
    except Exception:
        return default


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


def boolish(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if x is None:
        return False
    return str(x).strip().lower() in {"1", "true", "yes", "y", "pass", "verified", "t"}


def parse_flags(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x if str(i)]
    try:
        if pd.isna(x):
            return []
    except Exception:
        pass
    text = str(x).strip()
    if not text:
        return []
    try:
        raw = json.loads(text)
        if isinstance(raw, list):
            return [str(i).strip() for i in raw if str(i).strip()]
    except Exception:
        pass
    try:
        raw2 = ast.literal_eval(text)
        if isinstance(raw2, list):
            return [str(i).strip() for i in raw2 if str(i).strip()]
    except Exception:
        pass
    return [i.strip() for i in text.replace("；", ";").replace(",", ";").split(";") if i.strip()]


def grade_from_score(value: float) -> str:
    if value >= 85:
        return "S"
    if value >= 72:
        return "A"
    if value >= 60:
        return "B"
    if value >= 45:
        return "C"
    return "D"


def nearest_trade_key(date_value: Any) -> str:
    return str(date_value).replace("-", "").replace("/", "").strip()


def pct_rank(s: pd.Series) -> pd.Series:
    if len(s) <= 1:
        return pd.Series([50.0] * len(s), index=s.index)
    return s.rank(pct=True).fillna(0.5) * 100.0


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    for c in out.columns:
        if out[c].apply(lambda x: isinstance(x, (list, dict, tuple))).any():
            out[c] = out[c].apply(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict, tuple)) else x)
    out.to_csv(path, index=False, encoding="utf-8-sig")


def load_json(path: Optional[str], default: Any) -> Any:
    if not path:
        return default
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


# =============================================================================
# 2. Data loading and technical features
# =============================================================================


def load_local_kline(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    df = pd.read_parquet(p) if p.suffix.lower() in {".parquet", ".pq"} else pd.read_csv(p, dtype={"trade_date": str, "stock_code": str})
    required = ["trade_date", "stock_code", "stock_name", "open", "high", "low", "close", "volume", "amount"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required input columns: {missing}")
    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str).map(nearest_trade_key)
    df["stock_code"] = df["stock_code"].astype(str)
    df["stock_name"] = df["stock_name"].fillna("").astype(str)
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    defaults: Dict[str, Any] = {
        "sector_code": "UNKNOWN",
        "is_st": False,
        "is_suspended": False,
        "listed_days": 9999,
        "adj_factor": 1.0,
    }
    for k, v in defaults.items():
        if k not in df.columns:
            df[k] = v
    df["sector_code"] = df["sector_code"].fillna("UNKNOWN").astype(str)
    df["listed_days"] = pd.to_numeric(df["listed_days"], errors="coerce").fillna(9999)
    df["is_st"] = df["is_st"].apply(boolish)
    df["is_suspended"] = df["is_suspended"].apply(boolish)
    df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce").fillna(1.0)
    df = df.drop_duplicates(["stock_code", "trade_date"], keep="last").sort_values(["stock_code", "trade_date"]).reset_index(drop=True)
    df["known_at"] = df["trade_date"] + "_after_close"
    df["data_scope"] = "LOCAL_KLINE_ONLY"
    df["external_risk_status"] = "RESERVED_NOT_CONNECTED"
    df["external_sector_status"] = "RESERVED_OR_LOCAL_SECTOR_ONLY"
    return df


def add_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()
    group = data.groupby("stock_code", group_keys=False)
    data["prev_close"] = group["close"].shift(1)
    data["prev_volume"] = group["volume"].shift(1)
    data["pct_chg"] = data["close"] / data["prev_close"].replace(0, pd.NA) - 1.0

    for n in [3, 5, 6, 10, 12, 20, 24, 60, 120, 250]:
        data[f"ma{n}"] = group["close"].transform(lambda s, n=n: s.rolling(n, min_periods=max(2, n // 3)).mean())
    data["bbi"] = (data["ma3"] + data["ma6"] + data["ma12"] + data["ma24"]) / 4.0
    data["amount_median20"] = group["amount"].transform(lambda s: s.rolling(20, min_periods=5).median())
    data["amount_median60"] = group["amount"].transform(lambda s: s.rolling(60, min_periods=20).median())
    data["amount_median120"] = group["amount"].transform(lambda s: s.rolling(120, min_periods=30).median())
    data["amount_mean20"] = group["amount"].transform(lambda s: s.rolling(20, min_periods=5).mean())
    data["amount_std20"] = group["amount"].transform(lambda s: s.rolling(20, min_periods=8).std())
    data["amount_cv20"] = data["amount_std20"] / data["amount_mean20"].replace(0, pd.NA)
    data["volume_ratio_prev"] = data["volume"] / data["prev_volume"].replace(0, pd.NA)
    data["amount_ratio20"] = data["amount"] / data["amount_median20"].replace(0, pd.NA)
    data["turnover_persistence"] = data["amount_median20"] / data["amount_median120"].replace(0, pd.NA)

    rng = (data["high"] - data["low"]).replace(0, pd.NA)
    data["body_low"] = data[["open", "close"]].min(axis=1)
    data["body_high"] = data[["open", "close"]].max(axis=1)
    data["real_body_pct"] = ((data["close"] - data["open"]).abs() / rng).fillna(0).clip(0, 1)
    data["entity_pct_vs_prev_close"] = ((data["close"] - data["open"]).abs() / data["prev_close"].replace(0, pd.NA)).fillna(0)
    data["close_location"] = ((data["close"] - data["low"]) / rng).fillna(0.5).clip(0, 1)
    data["upper_shadow_pct"] = ((data["high"] - data["body_high"]) / rng).fillna(0).clip(0, 1)
    data["lower_shadow_pct"] = ((data["body_low"] - data["low"]) / rng).fillna(0).clip(0, 1)
    up_vs_prev = data["close"] > data["prev_close"].fillna(data["open"])
    data["bullish_quality"] = (
        35.0 * data["close_location"]
        + 25.0 * data["real_body_pct"]
        + 20.0 * (1.0 - data["upper_shadow_pct"])
        + 10.0 * (data["close"] >= data["open"]).astype(float)
        + 10.0 * up_vs_prev.astype(float)
    ).clip(0, 100)

    data["tr0"] = (data["high"] - data["low"]).abs()
    data["tr1"] = (data["high"] - data["prev_close"]).abs()
    data["tr2"] = (data["low"] - data["prev_close"]).abs()
    data["true_range"] = data[["tr0", "tr1", "tr2"]].max(axis=1)
    data["atr20"] = group["true_range"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    data["high250"] = group["high"].transform(lambda s: s.rolling(250, min_periods=60).max())
    data["low250"] = group["low"].transform(lambda s: s.rolling(250, min_periods=60).min())
    width250 = (data["high250"] - data["low250"]).replace(0, pd.NA)
    data["position_pct250"] = ((data["close"] - data["low250"]) / width250 * 100.0).fillna(50).clip(0, 100)

    data["range_pct"] = ((data["high"] - data["low"]) / data["close"].replace(0, pd.NA)).fillna(0)
    data["range_median20"] = group["range_pct"].transform(lambda s: s.rolling(20, min_periods=8).median())
    data["range_median60"] = group["range_pct"].transform(lambda s: s.rolling(60, min_periods=20).median())
    data["amount_cv60"] = group["amount"].transform(lambda s: (s.rolling(60, min_periods=20).std() / s.rolling(60, min_periods=20).mean()).replace([float("inf"), -float("inf")], pd.NA))
    cv_part = (1.0 - data["amount_cv20"] / data["amount_cv60"].replace(0, pd.NA)).fillna(0).clip(-1, 1)
    range_part = (1.0 - data["range_median20"] / data["range_median60"].replace(0, pd.NA)).fillna(0).clip(-1, 1)
    data["volatility_compression_score"] = (50.0 + 25.0 * cv_part + 25.0 * range_part).clip(0, 100)

    def _higher_low_quality(x: pd.Series) -> int:
        s = pd.Series(x).dropna()
        if len(s) < 8:
            return 0
        y = s.tail(20).reset_index(drop=True)
        trough_idx = []
        for i in range(1, len(y) - 1):
            if y.iloc[i] <= y.iloc[i - 1] and y.iloc[i] <= y.iloc[i + 1]:
                trough_idx.append(i)
        troughs = y.iloc[trough_idx].tail(3).tolist()
        trough_up = len(troughs) >= 2 and troughs[-1] >= troughs[0] * 0.985
        slope = float(pd.Series(y).rolling(3, min_periods=1).mean().diff().tail(8).mean()) if len(y) else 0.0
        q_now = float(y.tail(5).quantile(0.25))
        q_prev = float(y.head(max(5, len(y) // 2)).quantile(0.25))
        quantile_up = q_now >= q_prev * 0.985
        return int(trough_up) + int(slope > 0) + int(quantile_up)

    data["higher_low_count20"] = group["low"].transform(lambda s: s.rolling(20, min_periods=8).apply(_higher_low_quality, raw=False)).fillna(0).astype(int)
    data["bars_available"] = group["close"].cumcount() + 1
    data["effective_trade"] = ((data["volume"] > 0) & (data["amount"] > 0)).astype(int)
    data["effective_days60"] = group["effective_trade"].transform(lambda s: s.rolling(60, min_periods=1).sum()).astype(int)
    return data.drop(columns=["tr0", "tr1", "tr2"], errors="ignore")


# =============================================================================
# 3. Gates: Data / Universe / Liquidity / Local Risk
# =============================================================================


def data_gate(row: pd.Series) -> Tuple[bool, List[str], float]:
    flags: List[str] = []
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if pd.isna(row.get(c)):
            flags.append(f"MISSING_{c.upper()}")
    o, h, l, c, v, a = (num(row.get(x)) for x in ["open", "high", "low", "close", "volume", "amount"])
    if l <= 0:
        flags.append("LOW_NON_POSITIVE")
    if h < max(o, c):
        flags.append("HIGH_BELOW_BODY")
    if l > min(o, c):
        flags.append("LOW_ABOVE_BODY")
    if v < 0 or a < 0:
        flags.append("NEGATIVE_VOLUME_OR_AMOUNT")
    if boolish(row.get("is_suspended", False)):
        flags.append("SUSPENDED")
    if int(num(row.get("bars_available"), 0)) < CFG.min_bars_available:
        flags.append("BARS_AVAILABLE_LT_250")
    if int(num(row.get("effective_days60"), 0)) < CFG.min_effective_days60:
        flags.append("EFFECTIVE_TRADE_DAYS60_LOW")
    score = clamp(100.0 - 18.0 * len(flags))
    return len(flags) == 0, flags, score


def universe_gate(row: pd.Series) -> Tuple[bool, List[str]]:
    flags: List[str] = []
    name = str(row.get("stock_name", "")).upper()
    if boolish(row.get("is_st", False)) or name.startswith("ST") or name.startswith("*ST"):
        flags.append("ST_OR_SPECIAL_TREATMENT_LOCAL_NAME")
    if num(row.get("close")) < CFG.min_price:
        flags.append("PRICE_TOO_LOW")
    if int(num(row.get("listed_days"), 9999)) < CFG.min_listed_days:
        flags.append("LISTED_DAYS_TOO_SHORT")
    return len(flags) == 0, flags


def liquidity_gate(row: pd.Series) -> Tuple[bool, float, List[str]]:
    flags: List[str] = []
    med20, today, persist = num(row.get("amount_median20")), num(row.get("amount")), num(row.get("turnover_persistence"))
    if med20 < CFG.min_median_amount20:
        flags.append("MEDIAN_AMOUNT20_LOW")
    if today < CFG.min_today_amount:
        flags.append("TODAY_AMOUNT_LOW")
    score = clamp(
        45.0 * min(med20 / max(CFG.min_median_amount20, EPS), 1.8) / 1.8
        + 35.0 * min(today / max(CFG.min_today_amount, EPS), 1.8) / 1.8
        + 20.0 * min(max(persist, 0.0), 1.5) / 1.5
    )
    return len(flags) == 0, score, flags


def local_technical_risk_gate(row: pd.Series) -> Tuple[str, float, List[str]]:
    flags: List[str] = []
    score = 100.0
    position = num(row.get("position_pct250"), 50.0)
    amount_ratio20 = num(row.get("amount_ratio20"), 1.0)
    pct_chg = num(row.get("pct_chg"), 0.0)
    close_location = num(row.get("close_location"), 0.5)
    upper_shadow = num(row.get("upper_shadow_pct"), 0.0)
    volume_stall_like = amount_ratio20 >= CFG.stall_amt20_min and (
        pct_chg <= CFG.stall_pct_chg_max or close_location <= 0.55 or upper_shadow >= CFG.upper_shadow_stall_min
    )
    if position >= CFG.high_position_pct250_risk and volume_stall_like:
        flags.append("HIGH_POSITION_VOLUME_STALL")
        score -= 35.0
    if upper_shadow >= 0.45 and amount_ratio20 >= 1.5:
        flags.append("LONG_UPPER_SHADOW_WITH_VOLUME")
        score -= 22.0
    if pct_chg <= -0.06 and amount_ratio20 >= 1.4 and close_location <= 0.35:
        flags.append("DESTRUCTIVE_BEAR_BAR")
        score -= 35.0
    if int(num(row.get("fake_break_count"), 0)) > CFG.core_line_max_fake_breaks:
        flags.append("TOO_MANY_FAKE_BREAKS")
        score -= 24.0
    if num(row.get("next_pressure_distance"), 0.0) < 0.06:
        flags.append("NEXT_PRESSURE_TOO_CLOSE")
        score -= 18.0
    score = clamp(score)
    if any(x in flags for x in ["HIGH_POSITION_VOLUME_STALL", "DESTRUCTIVE_BEAR_BAR"]) and score < 65:
        return "BLOCK", score, flags
    if score < 72:
        return "WATCH", score, flags
    return "PASS", score, flags


def apply_basic_gates(latest: pd.DataFrame) -> pd.DataFrame:
    out = latest.copy()
    records: List[Dict[str, Any]] = []
    for idx, row in out.iterrows():
        dg, df, ds = data_gate(row)
        ug, uf = universe_gate(row)
        lg, ls, lf = liquidity_gate(row)
        records.append({
            "_idx": idx,
            "data_gate_pass": dg,
            "data_error_flags": df,
            "local_data_quality_score": ds,
            "universe_pass": ug,
            "universe_flags": uf,
            "liquidity_pass": lg,
            "liquidity_score": ls,
            "liquidity_flags": lf,
        })
    rec = pd.DataFrame(records).set_index("_idx")
    for c in rec.columns:
        out[c] = rec[c]
    return out


# =============================================================================
# 4. Market / Sector Context
# =============================================================================


def calc_market_state(latest: pd.DataFrame) -> MarketState:
    trade_date = str(latest["trade_date"].iloc[0]) if not latest.empty else ""
    if latest.empty:
        return MarketState(trade_date, "PANIC", 0.0, CFG.rr_panic, 0, 0.0, "empty latest", tuple())
    above20 = float((latest["close"] > latest["ma20"]).mean() * 100.0)
    above60 = float((latest["close"] > latest["ma60"]).mean() * 100.0)
    advance = float((latest["pct_chg"] > 0).mean() * 100.0)
    big_down = float((latest["pct_chg"] <= -0.07).mean() * 100.0)
    limit_up = int((latest["pct_chg"] >= 0.095).sum())
    limit_down = int((latest["pct_chg"] <= -0.095).sum())
    broke = ((latest["high"] >= latest["prev_close"] * 1.095) & (latest["close"] < latest["prev_close"] * 1.07)).fillna(False)
    broken_rate = float(broke.sum() / max(limit_up + broke.sum(), 1))
    amount_total, median20_total = num(latest["amount"].sum()), num(latest["amount_median20"].sum())
    turnover = clamp(amount_total / max(median20_total, EPS) * 50.0)
    breadth = clamp(0.35 * above20 + 0.25 * above60 + 0.20 * advance + 0.20 * (100.0 - min(big_down * 5.0, 100.0)))
    limit_structure = clamp(50.0 + min(25.0, 0.55 * limit_up) - min(28.0, 1.10 * limit_down) - 35.0 * broken_rate)
    risk_appetite = clamp(50.0 + min(22.0, 0.50 * limit_up) - min(28.0, 1.25 * limit_down) - 25.0 * broken_rate)
    trend = clamp(0.5 * above20 + 0.5 * above60)
    score = clamp(0.22 * trend + 0.23 * breadth + 0.15 * turnover + 0.20 * limit_structure + 0.20 * risk_appetite)
    reason = (
        f"LOCAL_KLINE_ONLY;above20={above20:.1f};above60={above60:.1f};advance={advance:.1f};"
        f"big_down={big_down:.1f};limit_up≈{limit_up};limit_down≈{limit_down};broken≈{broken_rate:.1%};turnover={turnover:.1f}"
    )
    rr_addon = CFG.local_data_rr_penalty + CFG.external_reserved_rr_penalty
    if score >= 75 and risk_appetite >= 65:
        return MarketState(trade_date, "STRONG", score, CFG.rr_strong + rr_addon, 5, 1.00, reason, ("breakout_confirm", "retest_buy", "midline_retest_buy", "pre_breakout_watch"))
    if score >= 55 and risk_appetite >= 45:
        return MarketState(trade_date, "NORMAL", score, CFG.rr_normal + rr_addon, 4, 0.70, reason, ("retest_buy", "midline_retest_buy", "breakout_confirm"))
    if score >= 35:
        return MarketState(trade_date, "WEAK", score, CFG.rr_weak + rr_addon, 2, 0.40, reason, ("retest_buy", "midline_retest_buy"))
    return MarketState(trade_date, "PANIC", score, CFG.rr_panic, 0, 0.0, reason, tuple())


def add_sector_context(latest: pd.DataFrame) -> pd.DataFrame:
    out = latest.copy()
    if "sector_code" not in out.columns:
        out["sector_code"] = "UNKNOWN"
    out["sector_code"] = out["sector_code"].fillna("UNKNOWN").astype(str)
    grouped = out.groupby("sector_code", dropna=False).agg(
        ret=("pct_chg", "mean"),
        amount=("amount", "sum"),
        up_ratio=("pct_chg", lambda s: float((s > 0).mean())),
        strong_ratio=("pct_chg", lambda s: float((s >= 0.07).mean())),
        weak_ratio=("pct_chg", lambda s: float((s <= -0.04).mean())),
        count=("stock_code", "count"),
        leader_return=("pct_chg", "max"),
    ).reset_index()
    grouped["sector_data_valid"] = (grouped["sector_code"] != "UNKNOWN") & (grouped["count"] >= 5)
    grouped["sector_heat_score"] = (
        0.32 * pct_rank(grouped["ret"])
        + 0.20 * pct_rank(grouped["amount"])
        + 0.20 * grouped["up_ratio"] * 100.0
        + 0.18 * grouped["strong_ratio"] * 100.0
        - 0.10 * grouped["weak_ratio"] * 100.0
    ).clip(0, 100).fillna(0)
    grouped.loc[~grouped["sector_data_valid"], "sector_heat_score"] = 0.0
    grouped["sector_lifecycle"] = grouped["sector_heat_score"].apply(lambda x: "CLIMAX" if x >= 88 else "MAIN" if x >= 72 else "START" if x >= 58 else "FADE" if x < 38 else "COLD")
    grouped.loc[~grouped["sector_data_valid"], "sector_lifecycle"] = "UNKNOWN"
    grouped["sector_support_action"] = grouped["sector_lifecycle"].map({
        "START": "BOOST",
        "MAIN": "BOOST",
        "CLIMAX": "DOWNGRADE_BACK_ROW",
        "FADE": "BLOCK_BREAKOUT",
        "COLD": "NEUTRAL",
        "UNKNOWN": "NEUTRAL",
    }).fillna("NEUTRAL")
    out = out.merge(grouped[["sector_code", "sector_heat_score", "sector_lifecycle", "sector_support_action", "sector_data_valid", "leader_return"]], on="sector_code", how="left")
    out["sector_rank_pct"] = out.groupby("sector_code")["pct_chg"].rank(pct=True).fillna(0.0) * 100.0
    out.loc[~out["sector_data_valid"].fillna(False), "sector_rank_pct"] = 0.0
    out["sector_role"] = "LAGGARD"
    out.loc[out["sector_rank_pct"] >= 85, "sector_role"] = "LEADER"
    out.loc[(out["sector_rank_pct"] >= 60) & (out["sector_rank_pct"] < 85), "sector_role"] = "MIDCORE"
    out.loc[(out["sector_rank_pct"] >= 35) & (out["sector_rank_pct"] < 60), "sector_role"] = "FOLLOWER"
    out.loc[~out["sector_data_valid"].fillna(False), "sector_role"] = "UNKNOWN"
    out["sector_leader_return"] = out["leader_return"].fillna(0.0)
    out = out.drop(columns=["leader_return"], errors="ignore")
    return out


# =============================================================================
# 5. Structure / Core Line / VBP
# =============================================================================


def resample_bars(hist: pd.DataFrame, freq: str) -> pd.DataFrame:
    if hist.empty:
        return pd.DataFrame()
    h = hist.copy()
    h["_dt"] = pd.to_datetime(h["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
    h = h.dropna(subset=["_dt"]).set_index("_dt").sort_index()
    if h.empty:
        return pd.DataFrame()
    agg = h.resample(freq).agg({
        "trade_date": "last",
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "amount": "sum",
        "body_high": "max",
        "body_low": "min",
    }).dropna(subset=["open", "high", "low", "close"])
    return agg.reset_index(drop=True)


def weighted_cluster(items: List[Dict[str, Any]], reference: float, bucket_pct: float = 0.012) -> List[Dict[str, Any]]:
    buckets: Dict[int, List[Dict[str, Any]]] = {}
    for item in items:
        p = num(item.get("price"))
        if p <= 0 or reference <= 0:
            continue
        key = int(round((p / reference - 1.0) / bucket_pct))
        buckets.setdefault(key, []).append(item)
    out: List[Dict[str, Any]] = []
    for values in buckets.values():
        weights = [max(num(v.get("weight"), 1.0), 0.1) for v in values]
        prices = [num(v.get("price")) for v in values]
        total = sum(weights) or 1.0
        price = sum(p * w for p, w in zip(prices, weights)) / total
        levels = sorted(set(str(v.get("level", "daily")) for v in values))
        kinds = sorted(set(str(v.get("kind", "")) for v in values))
        out.append({"price": price, "weight": total, "count": len(values), "levels": levels, "kinds": kinds})
    return sorted(out, key=lambda x: (x["weight"], x["count"]), reverse=True)


def period_anchor_candidates(hist: pd.DataFrame, freq: str, level: str) -> List[Dict[str, Any]]:
    bars = resample_bars(hist, freq)
    if bars.empty:
        return []
    amount_rank = bars["amount"].rank(pct=True).fillna(0.0) * 100.0
    out: List[Dict[str, Any]] = []
    heavy = bars[(amount_rank >= 80.0) & (bars["close"] >= bars["open"])].tail(30).copy()
    for idx, r in heavy.iterrows():
        rng = max(num(r.high) - num(r.low), EPS)
        body = abs(num(r.close) - num(r.open))
        if body < rng * 0.25:
            continue
        ar = num(amount_rank.loc[idx])
        out.append({"price": num(r.high), "kind": f"{level}_heavy_bull_high", "level": level, "weight": 18.0 + ar * 0.20})
        out.append({"price": num(max(r.open, r.close)), "kind": f"{level}_heavy_bull_body_top", "level": level, "weight": 16.0 + ar * 0.18})
        out.append({"price": num(min(r.open, r.close)), "kind": f"{level}_heavy_bull_body_bottom", "level": level, "weight": 12.0 + ar * 0.12})
    for _, r in bars.tail(36).iterrows():
        out.append({"price": num(r.high), "kind": f"{level}_swing_high", "level": level, "weight": 7.0})
        out.append({"price": num(max(r.open, r.close)), "kind": f"{level}_body_top", "level": level, "weight": 6.0})
        out.append({"price": num(min(r.open, r.close)), "kind": f"{level}_body_bottom", "level": level, "weight": 5.0})
    return out


def build_vbp_zones(hist: pd.DataFrame, close: float, atr: float) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if hist.empty or close <= EPS:
        return [], []
    h = hist.tail(CFG.vbp_window).copy()
    bin_width = max(close * 0.006, atr * 0.50, EPS)
    typical = (h["high"] + h["low"] + h["close"]) / 3.0
    bucket = (typical / bin_width).round() * bin_width
    grouped = h.assign(_bucket=bucket).groupby("_bucket", as_index=False)["amount"].sum()
    if grouped.empty:
        return [], []
    threshold = grouped["amount"].quantile(CFG.vbp_dense_quantile) if len(grouped) > 8 else grouped["amount"].median()
    heavy = grouped[grouped["amount"] >= threshold].copy()
    max_amt = max(num(grouped["amount"].max()), EPS)
    pressure, support = [], []
    for _, r in heavy.iterrows():
        p = num(r["_bucket"])
        w = 10.0 + 18.0 * num(r["amount"]) / max_amt
        item = {"price": p, "kind": "vbp_dense_amount_zone", "level": "vbp", "weight": w}
        if p >= close * 0.985:
            pressure.append(item)
        else:
            support.append(item)
    return pressure, support


def score_price_resonance(hist: pd.DataFrame, price: float, atr: float) -> Dict[str, float]:
    if hist.empty or price <= EPS:
        return {"body": 0.0, "wick": 0.0, "gap": 0.0, "fake": 0.0}
    h = hist.tail(520).copy()
    tol = max(price * 0.012, atr * 0.50)
    body = ((h["body_low"] - tol <= price) & (h["body_high"] + tol >= price)).sum()
    wick = ((h["low"] - tol <= price) & (h["high"] + tol >= price)).sum()
    prev_close = h["close"].shift(1)
    up_gap = ((h["low"] > prev_close * 1.003) & (price >= prev_close) & (price <= h["low"])).sum()
    down_gap = ((h["high"] < prev_close * 0.997) & (price <= prev_close) & (price >= h["high"])).sum()
    sweep = (h["high"] > price * 1.005) & (h["close"] <= price) & (h["amount_ratio20"] >= 1.20) & (h["upper_shadow_pct"] >= 0.25)
    fake = 0
    for idx in list(h.index[sweep]):
        loc = h.index.get_loc(idx)
        after = h.iloc[loc + 1: loc + 4]
        if after.empty or not (after["close"] > price * 1.01).any():
            fake += 1
    return {"body": float(body), "wick": float(wick), "gap": float(up_gap + down_gap), "fake": float(fake)}


def detect_platform(hist: pd.DataFrame, close: float) -> Dict[str, float]:
    if hist.empty or close <= EPS:
        return {"platform_score": 0.0, "platform_upper": 0.0, "platform_lower": 0.0}
    best_score, best_upper, best_lower = 0.0, 0.0, 0.0
    for w in [20, 30, 45, 60, 80]:
        sub = hist.tail(min(w, len(hist))).copy()
        if len(sub) < CFG.platform_min_days:
            continue
        upper, lower = num(sub["high"].quantile(0.92)), num(sub["low"].quantile(0.08))
        mid = (upper + lower) / 2.0
        tightness = (upper - lower) / max(mid, EPS)
        tight_score = max(0.0, 100.0 - tightness / max(CFG.platform_max_tightness, EPS) * 50.0)
        duration_score = clamp(len(sub) / 80.0 * 100.0)
        destructive = ((sub["close"] < sub["open"]) & (sub["amount_ratio20"] >= 1.45) & (sub["close_location"] <= 0.35)).sum()
        no_destroy_score = clamp(100.0 - destructive * 25.0)
        low_slope = num(sub["low"].tail(5).mean()) / max(num(sub["low"].head(5).mean()), EPS) - 1.0
        higher_low_score = clamp(50.0 + low_slope / 0.06 * 50.0)
        amount_cv = num(sub["amount"].std() / max(sub["amount"].mean(), EPS), 1.0)
        volume_stability = clamp(100.0 - amount_cv / 1.0 * 50.0)
        score = clamp(0.25 * duration_score + 0.25 * tight_score + 0.20 * no_destroy_score + 0.15 * higher_low_score + 0.15 * volume_stability)
        if score > best_score:
            best_score, best_upper, best_lower = score, upper, lower
    return {"platform_score": best_score, "platform_upper": best_upper, "platform_lower": best_lower}


def detect_notch(hist: pd.DataFrame, close: float) -> Dict[str, float]:
    if hist.empty or len(hist) < 60 or close <= EPS:
        return {"notch_score": 0.0, "notch_upper": 0.0}
    h = hist.tail(180).copy()
    highs = h["high"].rolling(5, center=True, min_periods=3).max()
    swing_high_idx = h.index[(h["high"] >= highs) & (h["high"] >= h["high"].rolling(20, min_periods=5).quantile(0.85))]
    score, upper = 0.0, 0.0
    for hi_idx in swing_high_idx[-8:]:
        left_pos = h.index.get_loc(hi_idx)
        after = h.iloc[left_pos + 1:]
        if len(after) < 20:
            continue
        low_after = after["low"].min()
        low_pos = after["low"].idxmin()
        right = h.loc[low_pos:].tail(60)
        depth = num(h.loc[hi_idx, "high"]) / max(num(low_after), EPS) - 1.0
        repair = close / max(num(h.loc[hi_idx, "high"]), EPS)
        active = num(right["amount"].median()) / max(num(h.iloc[:left_pos + 1]["amount"].median()), EPS)
        cur_score = clamp(40.0 * min(depth / 0.18, 1.0) + 35.0 * min(max(repair - 0.85, 0.0) / 0.18, 1.0) + 25.0 * min(active, 1.6) / 1.6)
        if cur_score > score:
            score, upper = cur_score, num(h.loc[hi_idx, "high"])
    return {"notch_score": score, "notch_upper": upper}


def default_prior_breakout_state() -> Dict[str, Any]:
    return {
        "prior_valid_breakout_exists": False,
        "prior_breakout_quality": 0.0,
        "prior_breakout_date": "",
        "prior_breakout_body_low": 0.0,
        "prior_breakout_body_mid": 0.0,
        "pullback_days": 999,
        "pullback_volume_contraction_score": 0.0,
        "pullback_destructive_bear_count": 99,
        "pullback_close_below_body_mid_count": 99,
        "pullback_min_close_distance_to_support": 1.0,
        "pullback_shrink_volume_days": 0,
        "reclaim_bar_quality": 0.0,
        "reclaim_volume_quality": 0.0,
    }


def derive_prior_breakout_state(hist: pd.DataFrame, pressure_upper: float, current_date: str) -> Dict[str, Any]:
    before = hist[hist["trade_date"].astype(str) < str(current_date)].tail(80).copy()
    if before.empty or pressure_upper <= EPS:
        return default_prior_breakout_state()
    body_range = (before["body_high"] - before["body_low"]).replace(0, pd.NA)
    body_above = ((before["body_high"] - pressure_upper).clip(lower=0) / body_range).fillna(0).clip(0, 1)
    local_stall = (before["amount_ratio20"] >= CFG.stall_amt20_min) & ((before["pct_chg"] <= CFG.stall_pct_chg_max) | (before["close_location"] <= 0.55) | (before["upper_shadow_pct"] >= CFG.upper_shadow_stall_min))
    quality = (35.0 * body_above + 25.0 * before["close_location"].fillna(0.5) + 20.0 * before["bullish_quality"].fillna(0) / 100.0 + 20.0 * before["amount_ratio20"].fillna(0).clip(0, 3.0) / 3.0 - 25.0 * local_stall.astype(float)).clip(0, 100)
    valid = before[(before["close"] > pressure_upper * 1.005) & (body_above >= 0.45) & (before["close_location"] >= 0.68) & (before["amount_ratio20"].between(1.15, 3.30)) & (~local_stall) & (quality >= CFG.prior_breakout_quality_min)].copy()
    if valid.empty:
        return default_prior_breakout_state()
    valid["breakout_quality"] = quality.loc[valid.index]
    breakout = valid.iloc[-1]
    idx = before.index.get_loc(breakout.name)
    after = before.iloc[idx + 1:]
    days_since = len(after) + 1
    body_low = min(num(breakout.open), num(breakout.close))
    body_mid = (body_low + max(num(breakout.open), num(breakout.close))) / 2.0
    if after.empty:
        contraction, destructive, close_below_mid, min_close_dist, shrink_days = 0.0, 0, 99, 1.0, 0
    else:
        break_amount = max(num(breakout.amount), EPS)
        contraction = clamp((1.0 - num(after["amount"].median(), break_amount) / break_amount) / 0.45 * 100.0)
        destructive = int(((after["close"] < pressure_upper * 0.97) & (after["amount_ratio20"] >= 1.25) & (after["close"] < after["open"])).sum())
        close_below_mid = int((after["close"] < body_mid).sum())
        min_close_dist = float((after["close"] / max(pressure_upper, EPS) - 1.0).abs().min())
        shrink_days = int(((after["amount"] <= break_amount * 0.78) | (after["amount_ratio20"] <= 1.10)).sum())
    current = hist[hist["trade_date"].astype(str) == str(current_date)].tail(1)
    if current.empty:
        reclaim_bar_quality, reclaim_volume_quality = 0.0, 0.0
    else:
        cur = current.iloc[0]
        reclaim_bar_quality = clamp(0.45 * num(cur.bullish_quality) + 0.30 * num(cur.close_location) * 100.0 + 0.25 * (1.0 - num(cur.upper_shadow_pct)) * 100.0)
        reclaim_volume_quality = clamp(50.0 + (num(cur.amount_ratio20, 1.0) - 1.0) * 32.0)
    prior_valid = CFG.pullback_min_days <= days_since <= CFG.pullback_max_days and destructive == 0 and contraction >= CFG.pullback_volume_contraction_min and close_below_mid <= 1 and min_close_dist <= CFG.pullback_close_distance_max
    return {
        "prior_valid_breakout_exists": bool(prior_valid),
        "prior_breakout_quality": float(num(valid.iloc[-1].breakout_quality)),
        "prior_breakout_date": str(breakout.trade_date),
        "prior_breakout_body_low": float(body_low),
        "prior_breakout_body_mid": float(body_mid),
        "pullback_days": int(days_since),
        "pullback_volume_contraction_score": float(contraction),
        "pullback_destructive_bear_count": int(destructive),
        "pullback_close_below_body_mid_count": int(close_below_mid),
        "pullback_min_close_distance_to_support": float(min_close_dist),
        "pullback_shrink_volume_days": int(shrink_days),
        "reclaim_bar_quality": float(reclaim_bar_quality),
        "reclaim_volume_quality": float(reclaim_volume_quality),
    }


def build_structure_features(full: pd.DataFrame, latest: pd.DataFrame, registry_path: Optional[str] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    prior = pd.DataFrame()
    if registry_path and Path(registry_path).exists():
        try:
            prior = pd.read_csv(registry_path, dtype={"stock_code": str})
        except Exception:
            prior = pd.DataFrame()
    enriched, registry_rows = [], []

    def find_prior(code: str) -> Optional[pd.Series]:
        if prior.empty or "stock_code" not in prior.columns:
            return None
        status_col = prior["status"].astype(str) if "status" in prior.columns else pd.Series(["active"] * len(prior), index=prior.index)
        p = prior[(prior["stock_code"].astype(str) == str(code)) & (status_col.isin(["active", "frozen"]))]
        if p.empty:
            return None
        return p.sort_values("last_valid_date").iloc[-1] if "last_valid_date" in p.columns else p.iloc[-1]

    for code, hist in full.groupby("stock_code"):
        hist = hist.sort_values("trade_date").copy()
        cur_df = latest[latest["stock_code"].astype(str) == str(code)]
        if cur_df.empty:
            continue
        row = cur_df.iloc[0].copy()
        close = num(row.close)
        atr = max(num(row.get("atr20")), close * 0.015)
        recent = hist.tail(2520).copy()
        if recent.empty or close <= EPS:
            continue
        pressure_items, support_items = [], []
        for w in [40, 80, 120, 180, 250, 520]:
            sub = recent.tail(min(w, len(recent))).copy()
            if len(sub) >= 20:
                pressure_items.extend([
                    {"price": num(sub["high"].max()), "kind": f"d{w}_high", "level": "daily", "weight": 7.0},
                    {"price": num(sub["close"].max()), "kind": f"d{w}_close_high", "level": "daily", "weight": 8.0},
                    {"price": num(sub["body_high"].max()), "kind": f"d{w}_body_top", "level": "daily", "weight": 8.0},
                ])
                support_items.extend([
                    {"price": num(sub["body_low"].tail(30).min()), "kind": f"d{w}_body_low", "level": "daily", "weight": 6.0},
                    {"price": num(sub["low"].tail(30).min()), "kind": f"d{w}_low", "level": "daily", "weight": 5.0},
                ])
        heavy = recent[(recent["amount"] >= recent["amount"].quantile(0.80)) & (recent["close"] >= recent["open"])].tail(40)
        for _, r in heavy.iterrows():
            pressure_items.append({"price": num(r.high), "kind": "daily_heavy_bull_high", "level": "daily", "weight": 12.0})
            pressure_items.append({"price": num(r.body_high), "kind": "daily_heavy_bull_body_top", "level": "daily", "weight": 12.0})
            support_items.append({"price": num(r.body_low), "kind": "daily_heavy_bull_body_bottom", "level": "daily", "weight": 10.0})
        for freq, level in [("W", "weekly"), ("ME", "monthly"), ("QE", "quarterly"), ("YE", "yearly")]:
            items = period_anchor_candidates(recent, freq, level)
            pressure_items.extend([x for x in items if "high" in x["kind"] or "top" in x["kind"] or "swing_high" in x["kind"]])
            support_items.extend([x for x in items if "bottom" in x["kind"]])
        vbp_pressure, vbp_support = build_vbp_zones(recent, close, atr)
        pressure_items.extend(vbp_pressure)
        support_items.extend(vbp_support)
        pressure_items = [x for x in pressure_items if close * 0.88 <= num(x.get("price")) <= close * 1.65]
        support_items = [x for x in support_items if close * 0.60 <= num(x.get("price")) <= close * 1.08]
        clusters = weighted_cluster(pressure_items, close)
        if clusters:
            best = clusters[0]
            candidate_pressure, reaction_count, levels, weight = num(best["price"]), int(best["count"]), set(best["levels"]), num(best["weight"])
        else:
            candidate_pressure, reaction_count, levels, weight = max(num(row.get("ma60")), close * 1.08), 1, {"fallback"}, 5.0
        inherited, replace_reason, active_pressure, first_detected = False, "fresh_local_multiframe_cluster", candidate_pressure, str(recent["trade_date"].min())
        prior_zone = find_prior(str(code))
        inherited_fake = 0
        if prior_zone is not None:
            prior_anchor = num(prior_zone.get("core_pressure_line", prior_zone.get("anchor_price", 0.0)))
            prior_upper = num(prior_zone.get("pressure_band_upper", prior_zone.get("upper", prior_anchor)))
            drift = abs(candidate_pressure / max(prior_anchor, EPS) - 1.0)
            drift_allowed = max(0.03, 1.5 * atr / max(close, EPS))
            inherited_fake = int(num(prior_zone.get("fake_break_count"), 0))
            if prior_anchor > EPS and drift <= drift_allowed:
                active_pressure, inherited, replace_reason = prior_anchor, True, "inherited_prior_core_line_within_drift_band"
            elif prior_anchor > EPS and not (close > max(prior_upper, candidate_pressure) * 1.03 and num(row.close_location, 0.5) >= 0.75):
                active_pressure, inherited, replace_reason = prior_anchor, True, "kept_prior_core_line_no_confirmed_replacement"
        resonance = score_price_resonance(recent, active_pressure, atr)
        fake_break_count = max(inherited_fake, int(resonance["fake"]))
        multi_credit = (10.0 if "monthly" in levels else 0.0) + (12.0 if "quarterly" in levels else 0.0) + (14.0 if "yearly" in levels else 0.0) + (10.0 if "vbp" in levels else 0.0)
        body_score, wick_score, gap_score = clamp(resonance["body"] * 2.2, 0, 18), clamp(resonance["wick"] * 1.2, 0, 16), clamp(resonance["gap"] * 4.0, 0, 10)
        reaction_score = clamp(reaction_count * 5.0 + weight * 0.12, 0, 25)
        next_pressures = sorted([num(x["price"]) for x in pressure_items if num(x["price"]) > active_pressure * 1.025])
        next_pressure = next_pressures[0] if next_pressures else max(active_pressure * 1.10, close * 1.12)
        next_pressure_distance = max(0.0, next_pressure / max(close, EPS) - 1.0)
        space_score = clamp(next_pressure_distance / 0.18 * 18.0, 0, 18)
        core_line_score = clamp(24.0 + reaction_score + body_score + wick_score + gap_score + multi_credit + space_score + (6.0 if inherited else 0.0) - min(20.0, fake_break_count * 3.5))
        major_anchor_count = int(sum(1 for x in levels if x in {"monthly", "quarterly", "yearly"}))
        has_vbp = "vbp" in levels
        if major_anchor_count >= 1 and has_vbp and core_line_score >= 82:
            core_line_tier = "major_vbp_core_line"
        elif core_line_score >= 78 and (major_anchor_count >= 1 or has_vbp or reaction_count >= 4):
            core_line_tier = "high_confidence_core_line"
        elif inherited and core_line_score >= 76 and fake_break_count <= 2:
            core_line_tier = "persisted_core_line"
        else:
            core_line_tier = "near_pressure_line"
        support_clusters = weighted_cluster(support_items, close)
        support_line = num(support_clusters[0]["price"]) if support_clusters else min(num(row.body_low), active_pressure * 0.985)
        platform, notch = detect_platform(recent, close), detect_notch(recent, close)
        band_half = max(atr * 0.5, active_pressure * 0.012)
        prior_state = derive_prior_breakout_state(hist, active_pressure + band_half, str(row.trade_date))

        row["platform_score"], row["platform_upper"], row["platform_lower"] = platform["platform_score"], platform["platform_upper"], platform["platform_lower"]
        row["notch_score"], row["notch_upper"] = notch["notch_score"], notch["notch_upper"]
        row["core_pressure_line"] = active_pressure
        row["pressure_band_lower"] = active_pressure - band_half
        row["pressure_band_upper"] = active_pressure + band_half
        row["core_support_line"] = support_line
        row["support_band_lower"] = support_line - max(atr * 0.5, support_line * 0.012)
        row["support_band_upper"] = support_line + max(atr * 0.5, support_line * 0.012)
        row["core_line_score"], row["core_line_tier"], row["fake_break_count"] = core_line_score, core_line_tier, fake_break_count
        row["vbp_confluence"], row["body_resonance_count"], row["wick_resonance_count"], row["gap_resonance_count"] = has_vbp, int(resonance["body"]), int(resonance["wick"]), int(resonance["gap"])
        row["next_pressure_price"], row["next_pressure_distance"] = next_pressure, next_pressure_distance
        row["core_line_reason"] = f"score={core_line_score:.1f};tier={core_line_tier};major={major_anchor_count};vbp={has_vbp};body={int(resonance['body'])};wick={int(resonance['wick'])};gap={int(resonance['gap'])};fake={fake_break_count};space={next_pressure_distance:.1%};{replace_reason}"
        for k, v in prior_state.items():
            row[k] = v
        registry_rows.append({
            "trade_date": row["trade_date"],
            "stock_code": code,
            "stock_name": row["stock_name"],
            "core_pressure_line": active_pressure,
            "pressure_band_lower": row["pressure_band_lower"],
            "pressure_band_upper": row["pressure_band_upper"],
            "core_support_line": support_line,
            "core_line_score": core_line_score,
            "core_line_tier": core_line_tier,
            "fake_break_count": fake_break_count,
            "status": "frozen" if inherited else "active",
            "last_valid_date": row["trade_date"],
            "first_detected_date": first_detected,
            "replace_reason": replace_reason,
            "core_line_reason": row["core_line_reason"],
        })
        enriched.append(row)
    return pd.DataFrame(enriched), pd.DataFrame(registry_rows)


# =============================================================================
# 6. Events / Path-dependent confirmations
# =============================================================================


def add_daily_events(latest: pd.DataFrame) -> pd.DataFrame:
    data = latest.copy()
    data["volume_stall_flag"] = (data["amount_ratio20"] >= CFG.stall_amt20_min) & ((data["pct_chg"] <= CFG.stall_pct_chg_max) | (data["close_location"] <= 0.55) | (data["upper_shadow_pct"] >= CFG.upper_shadow_stall_min))
    data["standard_volume_event"] = data["volume_ratio_prev"].between(CFG.std_vol_prev_min, CFG.std_vol_prev_max) & data["amount_ratio20"].between(CFG.std_amt20_min, CFG.std_amt20_max) & (data["bullish_quality"] >= CFG.bullish_std_min) & (~data["volume_stall_flag"])
    data["healthy_volume_event"] = data["amount_ratio20"].between(CFG.healthy_amt20_min, CFG.healthy_amt20_max) & (data["close_location"] >= CFG.close_loc_ok) & (data["upper_shadow_pct"] <= CFG.upper_shadow_healthy_max) & (data["bullish_quality"] >= CFG.bullish_healthy_min) & (~data["volume_stall_flag"])
    body_range = (data["body_high"] - data["body_low"]).replace(0, pd.NA)
    data["body_above_pressure"] = ((data["body_high"] - data["pressure_band_upper"]).clip(lower=0) / body_range).fillna(0).clip(0, 1)
    data["core_pressure_break_event"] = (data["close"] > data["pressure_band_upper"] * 1.005) & (data["body_above_pressure"] >= CFG.body_above_pressure_min) & (data["close_location"] >= CFG.close_loc_strong) & (data["standard_volume_event"] | data["healthy_volume_event"]) & (~data["volume_stall_flag"])

    tol = data["atr20"].fillna(data["close"] * 0.015).clip(lower=data["close"] * 0.008)
    data["retest_event"] = (
        (data["prior_valid_breakout_exists"].astype(bool))
        & (data["low"] <= data["pressure_band_upper"] + 1.2 * tol)
        & (data["close"] >= data["pressure_band_upper"] * 0.992)
        & (data["pullback_days"].between(CFG.pullback_min_days, CFG.pullback_max_days))
        & (data["pullback_destructive_bear_count"] == 0)
        & (data["pullback_volume_contraction_score"] >= CFG.pullback_volume_contraction_min)
        & (data["reclaim_bar_quality"] >= 65)
        & (data["reclaim_volume_quality"] >= 52)
        & (~data["volume_stall_flag"])
    )

    midline = data[["ma20", "bbi"]].mean(axis=1)
    data["midline_retest_event"] = (data["low"] <= midline * 1.018) & (data["close"] >= midline * 0.995) & (data["close_location"] >= 0.58) & (data["amount_ratio20"] <= 1.65) & (data["bullish_quality"] >= 55) & (~data["volume_stall_flag"]) & (data["platform_score"] >= 55)
    data["pre_breakout_night_score"] = (
        0.28 * data["volatility_compression_score"].fillna(0)
        + 0.22 * data["platform_score"].fillna(0)
        + 0.12 * data["notch_score"].fillna(0)
        + 0.13 * (data["higher_low_count20"].fillna(0).clip(0, 3) / 3.0 * 100.0)
        + 0.10 * (100.0 - (data["amount_cv20"].fillna(1.0).clip(0, 1.5) / 1.5 * 100.0))
        + 0.15 * (100.0 - (abs(data["close"] / data["core_pressure_line"].replace(0, pd.NA) - 1.0).fillna(1.0).clip(0, 0.12) / 0.12 * 100.0))
    ).clip(0, 100)
    data["pre_breakout_distance_ok"] = abs(data["close"] / data["core_pressure_line"].replace(0, pd.NA) - 1.0).fillna(9) <= CFG.pre_breakout_distance_max
    data["pre_breakout_night_event"] = (data["pre_breakout_night_score"] >= CFG.pre_breakout_score_min) & data["pre_breakout_distance_ok"] & (data["next_pressure_distance"] >= CFG.min_next_pressure_distance) & (~data["volume_stall_flag"]) & (data["position_pct250"] <= 75)
    return data


def add_path_dependent_events(full: pd.DataFrame, latest: pd.DataFrame) -> pd.DataFrame:
    out = latest.copy()
    for c in ["flat_volume_acceptance_event", "weak_break_midline_confirm_event"]:
        if c not in out.columns:
            out[c] = False
    updates: Dict[int, Dict[str, Any]] = {}
    for code, hist in full.groupby("stock_code"):
        hist = hist.sort_values("trade_date").copy()
        cur_df = out[out["stock_code"].astype(str) == str(code)]
        if cur_df.empty or len(hist) < 30:
            continue
        cur = cur_df.iloc[0]
        cur_idx = hist.index[hist["trade_date"].astype(str) == str(cur.trade_date)]
        if len(cur_idx) == 0:
            continue
        pos = hist.index.get_loc(cur_idx[-1])
        before = hist.iloc[max(0, pos - 25):pos].copy()
        idx_out = int(cur_df.index[0])

        flat_accept = False
        prior = before.tail(6)
        if len(prior) >= 2:
            b1, b2 = prior.iloc[-2], prior.iloc[-1]
            b1_event = num(b1.volume_ratio_prev) >= 1.5 and num(b1.amount_ratio20) >= 1.3 and num(b1.bullish_quality) >= 60
            vol_diff = abs(num(b2.volume) / max(num(b1.volume), EPS) - 1.0)
            b2_not_bad = not (num(b2.close) < num(b2.open) and num(b2.close_location) <= 0.35)
            flat_accept = bool(b1_event and vol_diff <= 0.08 and b2_not_bad and num(cur.close) >= num(b2.body_low))

        weak_break_midline_confirm = False
        recent = before.tail(12)
        if len(recent) >= 5 and num(cur.midline_retest_event) == 1:
            pressure = num(cur.core_pressure_line)
            weak_break = recent[(recent["high"] > pressure * 1.002) & (recent["close"] >= pressure * 0.985) & (recent["body_above_pressure"].fillna(0) < 0.60)] if "body_above_pressure" in recent.columns else pd.DataFrame()
            weak_break_midline_confirm = bool(not weak_break.empty and num(cur.close_location) >= 0.58 and num(cur.amount_ratio20) <= 1.65)

        updates[idx_out] = {
            "flat_volume_acceptance_event": flat_accept,
            "weak_break_midline_confirm_event": weak_break_midline_confirm,
        }
    for idx, vals in updates.items():
        for k, v in vals.items():
            out.loc[idx, k] = v
    return out


# =============================================================================
# 7. Model permission
# =============================================================================


def default_permission() -> Dict[str, Any]:
    return {
        "model_version": MODEL_VERSION,
        "permission_mode": "LOCAL_BOOTSTRAP",
        "note": "No external risk/source data connected. H1/H2/H4 may enter formal only with stricter RR. H3 remains shadow/watch by default until reviewed.",
        "hypotheses": {
            "H1_CORE_PRESSURE_BREAK": {"allowed": True, "position_scale": 0.85, "reason": "bootstrap_allowed_with_hard_gates"},
            "H2_BREAKOUT_RETEST_BUY": {"allowed": True, "position_scale": 0.90, "reason": "bootstrap_allowed_with_hard_gates"},
            "H3_PRE_BREAKOUT_NIGHT": {"allowed": False, "position_scale": 0.35, "reason": "shadow_until_backtest_permission"},
            "H4_WEAK_BREAK_MIDLINE_RETEST": {"allowed": True, "position_scale": 0.75, "reason": "bootstrap_allowed_with_hard_gates"},
        },
    }


def load_permission(path: Optional[str]) -> Dict[str, Any]:
    data = load_json(path, default_permission())
    base = default_permission()
    if not isinstance(data, dict):
        return base
    if "hypotheses" not in data or not isinstance(data["hypotheses"], dict):
        data["hypotheses"] = base["hypotheses"]
    for hid, v in base["hypotheses"].items():
        data["hypotheses"].setdefault(hid, v)
    return data


def permission_for(permission: Dict[str, Any], hypothesis_id: str) -> Tuple[bool, float, str]:
    item = permission.get("hypotheses", {}).get(hypothesis_id, {})
    return boolish(item.get("allowed", False)), num(item.get("position_scale", 0.0), 0.0), str(item.get("reason", "permission_missing"))


# =============================================================================
# 8. Hypotheses and execution
# =============================================================================


def score_space_row(row: pd.Series) -> float:
    return clamp(num(row.get("next_pressure_distance")) / 0.15 * 100.0)


def base_context(row: pd.Series, market: MarketState) -> float:
    sector_bonus = 8.0 if boolish(row.get("sector_data_valid")) and str(row.get("sector_support_action")) == "BOOST" and str(row.get("sector_role")) in {"LEADER", "MIDCORE", "FOLLOWER"} else 0.0
    sector_penalty = 10.0 if boolish(row.get("sector_data_valid")) and str(row.get("sector_support_action")) == "BLOCK_BREAKOUT" else 0.0
    low_bonus = 8.0 if num(row.get("position_pct250"), 50) <= CFG.low_position_pct250_max else 0.0
    high_penalty = 12.0 if num(row.get("position_pct250"), 50) >= CFG.high_position_pct250_risk else 0.0
    external_reserved_penalty = 4.0
    return clamp(
        0.30 * num(row.get("core_line_score"))
        + 0.18 * num(row.get("platform_score"))
        + 0.10 * num(row.get("notch_score"))
        + 0.18 * market.score
        + 0.08 * num(row.get("sector_heat_score"))
        + 0.16 * score_space_row(row)
        + sector_bonus
        + low_bonus
        - sector_penalty
        - high_penalty
        - external_reserved_penalty
    )


def make_h(hid: str, eligible: bool, grade: str, state: str, score: float, setup: float, context: float, confirm: float, reason: str, entry_type: str, family: str) -> HypothesisResult:
    return HypothesisResult(hid, bool(eligible), grade, state if eligible else "none", clamp(score), clamp(setup), clamp(context), clamp(confirm), reason, entry_type if eligible else "observe_only", family)


def evaluate_hypotheses(row: pd.Series, market: MarketState) -> List[HypothesisResult]:
    context = base_context(row, market)
    no_stall = not boolish(row.get("volume_stall_flag"))
    line_ok = num(row.get("core_line_score")) >= CFG.core_line_min_score and str(row.get("core_line_tier")) in {"major_vbp_core_line", "high_confidence_core_line", "persisted_core_line"}
    space_ok = num(row.get("next_pressure_distance")) >= CFG.min_next_pressure_distance

    h1_ok = boolish(row.get("core_pressure_break_event")) and line_ok and num(row.get("next_pressure_distance")) >= CFG.min_next_pressure_distance_h1 and no_stall and int(num(row.get("fake_break_count"))) <= CFG.core_line_max_fake_breaks
    h1_setup = clamp(30.0 * float(boolish(row.get("core_pressure_break_event"))) + 18.0 * num(row.get("bullish_quality")) / 100 + 16.0 * num(row.get("body_above_pressure")) + 14.0 * float(line_ok) + 12.0 * float(space_ok) + 10.0 * (1.0 - min(num(row.get("upper_shadow_pct")), 0.6) / 0.6))
    h1_confirm = clamp(45.0 * float(boolish(row.get("core_pressure_break_event"))) + 25.0 * num(row.get("close_location")) + 15.0 * float(boolish(row.get("standard_volume_event"))) + 15.0 * float(boolish(row.get("healthy_volume_event"))))
    h1_score = clamp(0.40 * h1_setup + 0.35 * context + 0.25 * h1_confirm)
    h1 = make_h("H1_CORE_PRESSURE_BREAK", h1_ok, grade_from_score(h1_score), "breakout_confirmed" if h1_ok else "none", h1_score, h1_setup, context, h1_confirm, f"核心压力带突破;line_ok={line_ok};space={num(row.get('next_pressure_distance')):.1%};body_above={num(row.get('body_above_pressure')):.2f}", "breakout_confirm", "breakout")

    h2_ok = boolish(row.get("retest_event")) and line_ok and space_ok and no_stall
    h2_setup = clamp(22.0 * float(boolish(row.get("prior_valid_breakout_exists"))) + 18.0 * num(row.get("prior_breakout_quality")) / 100 + 18.0 * num(row.get("pullback_volume_contraction_score")) / 100 + 14.0 * (1.0 - min(num(row.get("pullback_close_below_body_mid_count"), 99), 3) / 3) + 14.0 * num(row.get("reclaim_bar_quality")) / 100 + 14.0 * float(line_ok))
    h2_confirm = clamp(50.0 * float(boolish(row.get("retest_event"))) + 20.0 * num(row.get("reclaim_bar_quality")) / 100 + 15.0 * num(row.get("reclaim_volume_quality")) / 100 + 15.0 * (1.0 - min(num(row.get("pullback_min_close_distance_to_support"), 1.0), 0.08) / 0.08))
    h2_score = clamp(0.40 * h2_setup + 0.35 * context + 0.25 * h2_confirm)
    h2 = make_h("H2_BREAKOUT_RETEST_BUY", h2_ok, grade_from_score(h2_score), "valid_retest" if h2_ok else "none", h2_score, h2_setup, context, h2_confirm, f"突破后回踩确认;days={int(num(row.get('pullback_days'),999))};contraction={num(row.get('pullback_volume_contraction_score')):.1f}", "retest_buy", "retest")

    h3_ok = boolish(row.get("pre_breakout_night_event")) and line_ok and space_ok and no_stall
    h3_setup = clamp(0.50 * num(row.get("pre_breakout_night_score")) + 0.20 * num(row.get("volatility_compression_score")) + 0.15 * num(row.get("platform_score")) + 0.15 * score_space_row(row))
    h3_confirm = clamp(40.0 * float(boolish(row.get("pre_breakout_night_event"))) + 20.0 * (1.0 - min(abs(num(row.get("close")) / max(num(row.get("core_pressure_line")), EPS) - 1.0), 0.08) / 0.08) + 20.0 * num(row.get("higher_low_count20"), 0) / 3.0 + 20.0 * float(no_stall))
    h3_score = clamp(0.42 * h3_setup + 0.38 * context + 0.20 * h3_confirm)
    h3 = make_h("H3_PRE_BREAKOUT_NIGHT", h3_ok, grade_from_score(h3_score), "watch_trigger_ready" if h3_ok else "none", h3_score, h3_setup, context, h3_confirm, f"爆发前夜临界;pre_score={num(row.get('pre_breakout_night_score')):.1f};distance_ok={boolish(row.get('pre_breakout_distance_ok'))}", "pre_breakout_watch", "pre_breakout")

    h4_ok = boolish(row.get("weak_break_midline_confirm_event")) and boolish(row.get("midline_retest_event")) and space_ok and no_stall
    h4_setup = clamp(30.0 * float(boolish(row.get("weak_break_midline_confirm_event"))) + 22.0 * float(boolish(row.get("midline_retest_event"))) + 18.0 * num(row.get("platform_score")) / 100 + 15.0 * num(row.get("bullish_quality")) / 100 + 15.0 * score_space_row(row) / 100)
    h4_confirm = clamp(45.0 * float(boolish(row.get("midline_retest_event"))) + 25.0 * num(row.get("close_location")) + 15.0 * (1.0 - min(num(row.get("amount_ratio20"), 1.0), 2.0) / 2.0) + 15.0 * float(num(row.get("higher_low_count20"), 0) >= 1))
    h4_score = clamp(0.40 * h4_setup + 0.35 * context + 0.25 * h4_confirm)
    h4 = make_h("H4_WEAK_BREAK_MIDLINE_RETEST", h4_ok, grade_from_score(h4_score), "midline_retest_confirmed" if h4_ok else "none", h4_score, h4_setup, context, h4_confirm, "弱突破后回踩中轨/BBI承接确认", "midline_retest_buy", "retest")

    return [h1, h2, h3, h4]


def build_execution_plan(row: pd.Series, h: HypothesisResult, market: MarketState, permission_scale: float = 1.0) -> ExecutionPlan:
    close = num(row.get("close"))
    atr = max(num(row.get("atr20")), close * 0.015)
    abandon_flags: List[str] = []
    if not h.eligible or h.entry_type == "observe_only" or close <= EPS:
        return ExecutionPlan("observe_only", 0.0, 0.0, 0.0, 0.0, 0.0, False, False, "观察，不触发交易计划", "", CFG.max_gap_pct_formal, CFG.buy_slippage_pct, ["NO_ELIGIBLE_HYPOTHESIS"], 0.0, "none", "none")

    if h.hypothesis_id == "H2_BREAKOUT_RETEST_BUY":
        entry = close
        defense = min(num(row.get("pressure_band_lower")), num(row.get("prior_breakout_body_low"), num(row.get("pressure_band_lower")))) - max(atr * 0.20, close * 0.006)
        defense_source = "converted_pressure_retest_or_prior_body_low_buffer"
    elif h.hypothesis_id == "H4_WEAK_BREAK_MIDLINE_RETEST":
        mid = num(row.get("bbi"), close)
        entry = close
        defense = min(mid, num(row.get("ma20"), mid), num(row.get("low"))) - max(atr * 0.25, close * 0.008)
        defense_source = "bbi_ma20_midline_retest_buffer"
    elif h.hypothesis_id == "H3_PRE_BREAKOUT_NIGHT":
        entry = close
        defense = min(num(row.get("platform_lower"), close * 0.95), num(row.get("core_support_line"), close * 0.95), close - atr * 1.2)
        defense_source = "platform_or_core_support_prebreakout"
    else:
        entry = close
        defense = min(num(row.get("pressure_band_lower")), num(row.get("body_low")), close - atr * 1.0) - max(atr * 0.15, close * 0.006)
        defense_source = "breakout_band_lower_body_low_buffer"

    next_pressure = num(row.get("next_pressure_price"), close * 1.12)
    target = max(next_pressure, entry + 2.2 * max(entry - defense, atr))
    target_source = "next_pressure_or_min_rr_target"
    stop_distance = max(entry - defense, 0.0)
    if stop_distance <= close * 0.006:
        defense = entry - max(atr * 0.8, close * 0.018)
        stop_distance = entry - defense
        defense_source += "+min_stop_distance_adjustment"
    round_trip_cost = CFG.buy_slippage_pct + CFG.sell_slippage_pct + CFG.commission_pct * 2 + CFG.stamp_duty_pct + CFG.impact_cost_pct * 2
    gross_reward = max(target - entry, 0.0)
    gross_risk = max(entry - defense, EPS)
    rr_net = max(0.0, (gross_reward / max(entry, EPS) - round_trip_cost) / max(gross_risk / max(entry, EPS) + round_trip_cost, EPS))
    rr_pass = rr_net >= market.rr_min
    if not rr_pass:
        abandon_flags.append(f"RR_NOT_PASS:{rr_net:.2f}<{market.rr_min:.2f}")
    if h.entry_type not in market.allowed_entry_types:
        abandon_flags.append(f"MARKET_NOT_ALLOW_ENTRY:{h.entry_type}")
    if num(row.get("next_pressure_distance"), 0.0) < CFG.min_next_pressure_distance:
        abandon_flags.append("NEXT_PRESSURE_SPACE_INSUFFICIENT")
    if str(row.get("local_risk_action", "")) == "BLOCK":
        abandon_flags.append("LOCAL_RISK_BLOCK")
    executable = len(abandon_flags) == 0 and entry > 0 and defense > 0 and target > entry and defense < entry
    if h.entry_type == "breakout_confirm":
        rule = f"次日不高开超过{CFG.max_gap_pct_formal:.1%}；盘中不跌回压力带上沿下方；放量滞涨或长上影放弃"
    elif h.entry_type == "retest_buy":
        rule = f"次日允许贴近回踩位低吸；跌破防守价或收盘跌回压力带下方放弃"
    elif h.entry_type == "midline_retest_buy":
        rule = "次日守住BBI/MA20承接区；若放量长阴跌破中轨则放弃"
    else:
        rule = "观察触发线；突破核心线上沿且RR仍合格才允许升级"
    pos_base = {
        "H1_CORE_PRESSURE_BREAK": CFG.h1_position_base,
        "H2_BREAKOUT_RETEST_BUY": CFG.h2_position_base,
        "H3_PRE_BREAKOUT_NIGHT": CFG.h3_position_base,
        "H4_WEAK_BREAK_MIDLINE_RETEST": CFG.h4_position_base,
    }.get(h.hypothesis_id, 0.0)
    if market.regime == "WEAK":
        pos_base *= 0.60
    if market.regime == "PANIC":
        pos_base = 0.0
    if str(row.get("external_risk_status")) == "RESERVED_NOT_CONNECTED":
        pos_base *= 0.85
    position_pct = min(pos_base * max(permission_scale, 0.0), market.position_cap)
    if not executable:
        position_pct = 0.0
    return ExecutionPlan(h.entry_type, float(entry), float(defense), float(target), float(stop_distance), float(rr_net), bool(rr_pass), bool(executable), rule, str(row.get("trade_date")), CFG.max_gap_pct_formal, CFG.buy_slippage_pct, abandon_flags, float(position_pct), defense_source, target_source)


def choose_primary_candidate(row: pd.Series, market: MarketState, permission: Dict[str, Any]) -> Tuple[HypothesisResult, ExecutionPlan, bool, str, float]:
    candidates: List[Tuple[HypothesisResult, ExecutionPlan, float, bool, str, float]] = []
    for h in evaluate_hypotheses(row, market):
        allowed, scale, reason = permission_for(permission, h.hypothesis_id)
        plan = build_execution_plan(row, h, market, scale)
        formal_capable = h.eligible and h.setup_grade in {"S", "A"} and allowed and plan.rr_pass and plan.executable_flag and plan.entry_type in market.allowed_entry_types
        watch_capable = h.eligible and plan.entry_type != "observe_only" and (h.setup_grade in {"B", "A", "S"})
        if formal_capable or watch_capable:
            rr_score = clamp(plan.rr_net / max(market.rr_min, 1.0) * 100.0) if plan.rr_net > 0 else 0.0
            score = 0.62 * h.hypothesis_score + 0.25 * rr_score + 0.13 * num(row.get("liquidity_score"))
            if not formal_capable:
                score = min(score, 68.0)
            candidates.append((h, plan, score, allowed, reason, scale))
    if not candidates:
        h = HypothesisResult("NONE", False, "NONE", "none", 0.0, 0.0, 0.0, 0.0, "没有可执行且RR合格的主假设", "observe_only", "none")
        return h, build_execution_plan(row, h, market, 0.0), False, "no_candidate", 0.0
    h, plan, _, allowed, reason, scale = max(candidates, key=lambda x: x[2])
    return h, plan, allowed, reason, scale


# =============================================================================
# 9. Decision pipeline
# =============================================================================


def build_decisions(full: pd.DataFrame, trade_date: Optional[str] = None, output_dir: Optional[Path] = None, permission_path: Optional[str] = None, registry_path: Optional[str] = None) -> Tuple[pd.DataFrame, pd.DataFrame, MarketState]:
    data = add_technical_features(full)
    if trade_date is None:
        trade_date = str(data["trade_date"].max())
    else:
        trade_date = nearest_trade_key(trade_date)
    latest = data[data["trade_date"].astype(str) == str(trade_date)].copy()
    if latest.empty:
        raise ValueError(f"No bars for trade_date={trade_date}")
    latest = apply_basic_gates(latest)
    market = calc_market_state(latest)
    latest = add_sector_context(latest)
    latest, registry = build_structure_features(data[data["trade_date"].astype(str) <= str(trade_date)].copy(), latest, registry_path=registry_path)
    latest = add_daily_events(latest)
    latest = add_path_dependent_events(data[data["trade_date"].astype(str) <= str(trade_date)].copy(), latest)

    # local technical risk must run after structure features, because fake_break_count and next_pressure_distance are needed.
    risk_records = []
    for idx, row in latest.iterrows():
        action, score, flags = local_technical_risk_gate(row)
        risk_records.append({"_idx": idx, "local_risk_action": action, "technical_risk_score": score, "technical_risk_flags": flags})
    risk_df = pd.DataFrame(risk_records).set_index("_idx") if risk_records else pd.DataFrame()
    for c in risk_df.columns:
        latest[c] = risk_df[c]

    permission = load_permission(permission_path)
    rows: List[Dict[str, Any]] = []
    for _, row in latest.iterrows():
        block: List[str] = []
        if not boolish(row.get("data_gate_pass")):
            block.append("DATA_GATE_FAIL")
        if not boolish(row.get("universe_pass")):
            block.append("UNIVERSE_FAIL")
        if not boolish(row.get("liquidity_pass")):
            block.append("LIQUIDITY_FAIL")
        if str(row.get("local_risk_action")) == "BLOCK":
            block.append("LOCAL_TECHNICAL_RISK_BLOCK")
        h, plan, perm_allowed, perm_reason, perm_scale = choose_primary_candidate(row, market, permission)
        if h.hypothesis_id == "NONE":
            block.append("NO_ACTIVE_MAIN_HYPOTHESIS")
        if not perm_allowed and h.hypothesis_id != "NONE":
            block.append("MODEL_PERMISSION_NOT_ALLOWED")
        if not plan.rr_pass and h.hypothesis_id != "NONE":
            block.append("RR_FAIL")
        if not plan.executable_flag and h.hypothesis_id != "NONE":
            block.append("NOT_EXECUTABLE")
        if h.entry_type not in market.allowed_entry_types and h.hypothesis_id != "NONE":
            block.append("MARKET_PERMISSION_FAIL")
        formal_candidate = len(block) == 0 and h.setup_grade in {"S", "A"}
        watch_candidate = h.hypothesis_id != "NONE" and not formal_candidate and h.setup_grade in {"A", "B", "S"}
        rr_score = clamp(plan.rr_net / max(market.rr_min, 1.0) * 100.0) if plan.rr_net > 0 else 0.0
        final_score = clamp(0.50 * h.hypothesis_score + 0.20 * rr_score + 0.12 * num(row.get("liquidity_score")) + 0.10 * market.score + 0.08 * num(row.get("sector_heat_score")))
        rows.append({
            "trade_date": row.get("trade_date"),
            "known_at": row.get("known_at"),
            "data_scope": row.get("data_scope", "LOCAL_KLINE_ONLY"),
            "external_risk_status": row.get("external_risk_status", "RESERVED_NOT_CONNECTED"),
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "model_version": MODEL_VERSION,
            "market_regime": market.regime,
            "market_score": market.score,
            "rr_min": market.rr_min,
            "market_reason": market.reason,
            "sector_code": row.get("sector_code", "UNKNOWN"),
            "sector_data_valid": boolish(row.get("sector_data_valid")),
            "sector_lifecycle": row.get("sector_lifecycle", "UNKNOWN"),
            "sector_support_action": row.get("sector_support_action", "NEUTRAL"),
            "sector_role": row.get("sector_role", "UNKNOWN"),
            "sector_heat_score": num(row.get("sector_heat_score")),
            "sector_rank_pct": num(row.get("sector_rank_pct")),
            "data_gate_pass": boolish(row.get("data_gate_pass")),
            "data_error_flags": row.get("data_error_flags", []),
            "local_data_quality_score": num(row.get("local_data_quality_score")),
            "universe_pass": boolish(row.get("universe_pass")),
            "universe_flags": row.get("universe_flags", []),
            "liquidity_pass": boolish(row.get("liquidity_pass")),
            "liquidity_score": num(row.get("liquidity_score")),
            "liquidity_flags": row.get("liquidity_flags", []),
            "local_risk_action": row.get("local_risk_action", "BLOCK"),
            "technical_risk_score": num(row.get("technical_risk_score")),
            "technical_risk_flags": row.get("technical_risk_flags", []),
            "primary_hypothesis": h.hypothesis_id,
            "primary_setup_grade": h.setup_grade,
            "confirmation_state": h.confirmation_state,
            "hypothesis_score": h.hypothesis_score,
            "setup_score": h.setup_score,
            "context_score": h.context_score,
            "confirmation_score": h.confirmation_score,
            "rank_reason": h.reason,
            "core_pressure_line": num(row.get("core_pressure_line")),
            "pressure_band_lower": num(row.get("pressure_band_lower")),
            "pressure_band_upper": num(row.get("pressure_band_upper")),
            "core_support_line": num(row.get("core_support_line")),
            "core_line_score": num(row.get("core_line_score")),
            "core_line_tier": row.get("core_line_tier", ""),
            "core_line_reason": row.get("core_line_reason", ""),
            "platform_score": num(row.get("platform_score")),
            "notch_score": num(row.get("notch_score")),
            "fake_break_count": int(num(row.get("fake_break_count"))),
            "vbp_confluence": boolish(row.get("vbp_confluence")),
            "body_resonance_count": int(num(row.get("body_resonance_count"))),
            "wick_resonance_count": int(num(row.get("wick_resonance_count"))),
            "gap_resonance_count": int(num(row.get("gap_resonance_count"))),
            "next_pressure_price": num(row.get("next_pressure_price")),
            "next_pressure_distance": num(row.get("next_pressure_distance")),
            "pre_breakout_night_score": num(row.get("pre_breakout_night_score")),
            "standard_volume_event": boolish(row.get("standard_volume_event")),
            "healthy_volume_event": boolish(row.get("healthy_volume_event")),
            "volume_stall_flag": boolish(row.get("volume_stall_flag")),
            "entry_type": plan.entry_type,
            "entry_price": plan.entry_price,
            "defense_price": plan.defense_price,
            "target_price": plan.target_price,
            "stop_distance": plan.stop_distance,
            "rr_net": plan.rr_net,
            "rr_pass": plan.rr_pass,
            "executable_flag": plan.executable_flag,
            "defense_source": plan.defense_source,
            "target_source": plan.target_source,
            "next_day_entry_rule": plan.next_day_entry_rule,
            "entry_valid_until": plan.entry_valid_until,
            "max_allowed_gap_pct": plan.max_allowed_gap_pct,
            "slippage_assumption_pct": plan.slippage_assumption_pct,
            "abandon_flags": plan.abandon_flags,
            "position_pct": plan.position_pct,
            "model_permission_allowed": perm_allowed,
            "model_permission_reason": perm_reason,
            "model_permission_position_scale": perm_scale,
            "family_cap_applied": False,
            "final_score": final_score,
            "formal_pool_flag": formal_candidate,
            "watchlist_flag": watch_candidate,
            "formal_tier": "" if not formal_candidate else ("S" if final_score >= 85 else "A"),
            "block_reason": block,
        })
    decisions = pd.DataFrame(rows)

    if not decisions.empty:
        decisions = decisions.sort_values(["formal_pool_flag", "final_score"], ascending=[False, False]).reset_index(drop=True)
        # Portfolio caps: top limit, sector cap, hypothesis cap, market cap.
        sector_counts: Dict[str, int] = {}
        hyp_counts: Dict[str, int] = {}
        formal_kept = 0
        for i, r in decisions.iterrows():
            if not boolish(r.get("formal_pool_flag")):
                continue
            if formal_kept >= min(CFG.top_limit, market.formal_cap):
                decisions.at[i, "formal_pool_flag"] = False
                decisions.at[i, "watchlist_flag"] = True
                br = parse_flags(r.get("block_reason")) + ["TOP_OR_MARKET_CAP_EXCEEDED"]
                decisions.at[i, "block_reason"] = br
                continue
            sec = str(r.get("sector_code", "UNKNOWN"))
            hyp = str(r.get("primary_hypothesis", "NONE"))
            if sec != "UNKNOWN" and sector_counts.get(sec, 0) >= CFG.max_sector_formal:
                decisions.at[i, "formal_pool_flag"] = False
                decisions.at[i, "watchlist_flag"] = True
                decisions.at[i, "family_cap_applied"] = True
                decisions.at[i, "block_reason"] = parse_flags(r.get("block_reason")) + ["SECTOR_CAP_APPLIED"]
                continue
            if hyp_counts.get(hyp, 0) >= CFG.max_hypothesis_formal:
                decisions.at[i, "formal_pool_flag"] = False
                decisions.at[i, "watchlist_flag"] = True
                decisions.at[i, "family_cap_applied"] = True
                decisions.at[i, "block_reason"] = parse_flags(r.get("block_reason")) + ["HYPOTHESIS_CAP_APPLIED"]
                continue
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
            hyp_counts[hyp] = hyp_counts.get(hyp, 0) + 1
            formal_kept += 1
    return decisions, registry, market


# =============================================================================
# 10. Output reports and registries
# =============================================================================


def export_field_registry(output_dir: Path) -> None:
    rows = [asdict(v) for v in FIELD_REGISTRY.values()]
    write_csv(pd.DataFrame(rows), output_dir / "second_employee_field_registry.csv")


def save_review_snapshot(decisions: pd.DataFrame, output_dir: Path) -> None:
    cols = [
        "trade_date", "known_at", "data_scope", "external_risk_status", "stock_code", "stock_name", "model_version",
        "market_regime", "market_score", "rr_min", "sector_code", "sector_data_valid", "sector_lifecycle", "sector_support_action", "sector_role", "sector_heat_score", "sector_rank_pct",
        "data_gate_pass", "universe_pass", "liquidity_pass", "local_risk_action", "technical_risk_flags",
        "primary_hypothesis", "primary_setup_grade", "confirmation_state", "formal_tier",
        "entry_type", "entry_price", "defense_price", "target_price", "stop_distance", "defense_source", "target_source", "rr_net", "position_pct",
        "core_pressure_line", "pressure_band_lower", "pressure_band_upper", "core_support_line", "core_line_score", "core_line_tier", "core_line_reason",
        "platform_score", "notch_score", "fake_break_count", "vbp_confluence", "body_resonance_count", "wick_resonance_count", "gap_resonance_count", "next_pressure_price", "next_pressure_distance",
        "pre_breakout_night_score", "standard_volume_event", "healthy_volume_event", "volume_stall_flag",
        "hypothesis_score", "setup_score", "context_score", "confirmation_score", "final_score",
        "model_permission_allowed", "model_permission_reason", "model_permission_position_scale",
        "formal_pool_flag", "watchlist_flag", "block_reason", "rank_reason",
        "next_day_entry_rule", "entry_valid_until", "max_allowed_gap_pct", "slippage_assumption_pct", "abandon_flags",
    ]
    existing = [c for c in cols if c in decisions.columns]
    write_csv(decisions[existing], output_dir / "second_employee_review_snapshot.csv")


def write_report(decisions: pd.DataFrame, market: MarketState, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "second_employee_report.md"
    lines: List[str] = []
    lines.append(f"# 二号员工 V12.0 盘后报告 - {market.trade_date}\n\n")
    lines.append(f"模型：`{MODEL_VERSION}`\n\n")
    lines.append(f"市场权限：**{market.regime}**，score={market.score:.1f}，rr_min={market.rr_min:.2f}，formal_cap={market.formal_cap}，position_cap={market.position_cap:.0%}\n\n")
    lines.append(f"市场理由：{market.reason}\n\n")
    lines.append("> 当前版本为 LOCAL_KLINE_ONLY：外部公告、监管、财务、诉讼、质押等风险源尚未接入；报告中的风险结论仅代表本地技术风险，不构成外部风险安全背书。正式池因此自动提高 RR 门槛并收紧仓位。\n\n")
    formal = decisions[decisions["formal_pool_flag"].apply(boolish)].copy() if not decisions.empty else pd.DataFrame()
    lines.append("## 正式买入池\n\n")
    if formal.empty:
        lines.append("今日无正式买入池。\n\n")
    for _, r in formal.iterrows():
        lines.append(f"### {r.stock_name}（{r.stock_code}）\n")
        lines.append(f"- 正式层级：{r.formal_tier}；主假设：{r.primary_hypothesis} / {r.primary_setup_grade} / {r.confirmation_state}\n")
        lines.append(f"- 核心线：{float(num(r.core_pressure_line)):.2f}；压力带：{float(num(r.pressure_band_lower)):.2f}~{float(num(r.pressure_band_upper)):.2f}；tier={r.core_line_tier}\n")
        lines.append(f"- 结构质量：core={float(num(r.core_line_score)):.1f}，platform={float(num(r.platform_score)):.1f}，notch={float(num(r.notch_score)):.1f}，VBP={boolish(r.vbp_confluence)}，fake={int(num(r.fake_break_count))}\n")
        lines.append(f"- 空间：next_pressure={float(num(r.next_pressure_price)):.2f}，distance={float(num(r.next_pressure_distance)):.1%}\n")
        lines.append(f"- 板块：sector={r.sector_code}，valid={boolish(r.sector_data_valid)}，lifecycle={r.sector_lifecycle}，role={r.sector_role}，heat={float(num(r.sector_heat_score)):.1f}\n")
        lines.append(f"- 入场：{r.entry_type} @ {float(num(r.entry_price)):.2f}；防守：{float(num(r.defense_price)):.2f}（{r.defense_source}）；目标：{float(num(r.target_price)):.2f}（{r.target_source}）；净RR：{float(num(r.rr_net)):.2f}\n")
        lines.append(f"- 仓位建议：{float(num(r.position_pct)):.2%}；权限：{boolish(r.model_permission_allowed)}（{r.model_permission_reason}）\n")
        lines.append(f"- 次日约束：{r.next_day_entry_rule}\n")
        lines.append(f"- 选择理由：{r.rank_reason}\n")
        lines.append("- 放弃条件：跌破防守价；突破后放量滞涨；次日高开超过约束后回落；板块退潮；市场转 PANIC。\n\n")

    lines.append("## 观察池前十\n\n")
    watch = decisions[(~decisions["formal_pool_flag"].apply(boolish)) & (decisions["watchlist_flag"].apply(boolish))].head(10) if not decisions.empty else pd.DataFrame()
    if watch.empty:
        lines.append("无。\n\n")
    for _, r in watch.iterrows():
        lines.append(f"### {r.stock_name}（{r.stock_code}）\n")
        lines.append(f"- 主假设：{r.primary_hypothesis} / {r.primary_setup_grade} / final={float(num(r.final_score)):.1f}\n")
        lines.append(f"- 未进正式池：{';'.join(parse_flags(r.block_reason))}\n")
        lines.append(f"- 核心线：{float(num(r.core_pressure_line)):.2f}，tier={r.core_line_tier}，reason={r.core_line_reason}\n\n")
    path.write_text("".join(lines), encoding="utf-8")


def write_outputs(decisions: pd.DataFrame, registry: pd.DataFrame, market: MarketState, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(decisions, output_dir / "second_employee_decisions.csv")
    write_csv(decisions[decisions["formal_pool_flag"].apply(boolish)].head(CFG.top_limit), output_dir / "second_employee_recommendations.csv")
    if not registry.empty:
        write_csv(registry, output_dir / "second_employee_core_zone_registry.csv")
    save_review_snapshot(decisions, output_dir)
    export_field_registry(output_dir)
    write_report(decisions, market, output_dir)


# =============================================================================
# 11. Review / Summary / Permission generation
# =============================================================================


def first_hit_index(values: pd.Series, threshold: float, direction: str) -> Optional[int]:
    for i, v in enumerate(values.tolist(), start=1):
        val = num(v)
        if direction == "le" and val <= threshold:
            return i
        if direction == "ge" and val >= threshold:
            return i
    return None


def classify_path(sub: pd.DataFrame, entry: float, defense: float, target: float) -> Tuple[str, str]:
    if sub.empty:
        return "insufficient_data", "insufficient_data"
    stop_day = first_hit_index(sub["low"], defense, "le")
    target_day = first_hit_index(sub["high"], target, "ge")
    close_ret = num(sub.iloc[-1].close) / max(entry, EPS) - 1.0
    max_ret = num(sub.high.max()) / max(entry, EPS) - 1.0
    min_ret = num(sub.low.min()) / max(entry, EPS) - 1.0
    if stop_day is not None and (target_day is None or stop_day <= target_day):
        return "quick_fail" if stop_day <= 3 else "failed_after_hold", "stop_hit_before_target"
    if target_day is not None and (stop_day is None or target_day < stop_day):
        return "target_reached", "target_hit_before_stop"
    if min_ret <= -0.05 and close_ret > 0:
        return "shakeout_then_up", "deep_pullback_recovered"
    if max_ret < 0.03 and close_ret <= 0:
        return "no_follow", "no_upside_follow_through"
    if close_ret > 0:
        return "slow_trend", "positive_hold"
    return "weak_path", "unclassified_weak"


def simulate_actual_entry(rec_row: pd.Series, future: pd.DataFrame) -> Dict[str, Any]:
    entry_plan = num(rec_row.get("entry_price"))
    entry_type = str(rec_row.get("entry_type", "observe_only"))
    max_gap = num(rec_row.get("max_allowed_gap_pct", CFG.max_gap_pct_formal), CFG.max_gap_pct_formal)
    if future.empty or entry_plan <= EPS or entry_type == "observe_only":
        return {"entry_triggered": False, "gap_rejected": False, "limit_up_unbuyable": False, "entry_price_actual": math.nan, "entry_reject_reason": "NO_NEXT_DAY_OR_OBSERVE_ONLY"}
    nxt = future.iloc[0]
    gap = num(nxt.open) / max(entry_plan, EPS) - 1.0
    one_price_like = abs(num(nxt.high) - num(nxt.low)) <= max(0.01, num(nxt.close) * 0.001)
    limit_up_unbuyable = bool(gap >= 0.095 and one_price_like)
    if gap > max_gap:
        return {"entry_triggered": False, "gap_rejected": True, "limit_up_unbuyable": limit_up_unbuyable, "entry_price_actual": math.nan, "entry_reject_reason": "NEXT_DAY_GAP_OVER_LIMIT"}
    if limit_up_unbuyable:
        return {"entry_triggered": False, "gap_rejected": False, "limit_up_unbuyable": True, "entry_price_actual": math.nan, "entry_reject_reason": "LIMIT_UP_UNBUYABLE"}
    actual = max(num(nxt.open), entry_plan) * (1 + CFG.buy_slippage_pct)
    return {"entry_triggered": True, "gap_rejected": False, "limit_up_unbuyable": False, "entry_price_actual": actual, "entry_reject_reason": ""}


def review_recommendations(recommendations: str, eod: str, output: str) -> pd.DataFrame:
    rec = pd.read_csv(recommendations, dtype={"trade_date": str, "stock_code": str})
    data = load_local_kline(eod)
    labels: List[Dict[str, Any]] = []
    windows = [1, 3, 5, 8, 13, 20]
    for _, r in rec.iterrows():
        code = str(r.stock_code)
        date = str(r.trade_date)
        hist = data[(data["stock_code"].astype(str) == code) & (data["trade_date"].astype(str) > date)].sort_values("trade_date").copy()
        entry_sim = simulate_actual_entry(r, hist)
        entry = num(entry_sim.get("entry_price_actual"), num(r.get("entry_price")))
        defense = num(r.get("defense_price"))
        target = num(r.get("target_price"))
        row = {
            "trade_date": date,
            "known_at": r.get("known_at", ""),
            "stock_code": code,
            "stock_name": r.get("stock_name", ""),
            "model_version": r.get("model_version", MODEL_VERSION),
            "primary_hypothesis": r.get("primary_hypothesis", ""),
            "primary_setup_grade": r.get("primary_setup_grade", ""),
            "formal_pool_flag": boolish(r.get("formal_pool_flag")),
            "entry_plan": num(r.get("entry_price")),
            "entry_triggered": entry_sim["entry_triggered"],
            "entry_price_actual": entry,
            "entry_reject_reason": entry_sim["entry_reject_reason"],
            "defense_price": defense,
            "target_price": target,
            "review_status": "done" if len(hist) >= 1 else "insufficient_data",
        }
        for w in windows:
            sub = hist.head(w)
            if sub.empty or not entry_sim["entry_triggered"]:
                row[f"ret_t{w}"] = math.nan
                row[f"mfe_t{w}"] = math.nan
                row[f"mae_t{w}"] = math.nan
                row[f"hit_stop_t{w}"] = False
                row[f"hit_target_t{w}"] = False
                row[f"path_tag_t{w}"] = "not_entered" if not entry_sim["entry_triggered"] else "insufficient_data"
                continue
            row[f"ret_t{w}"] = num(sub.iloc[-1].close) / max(entry, EPS) - 1.0
            row[f"mfe_t{w}"] = num(sub.high.max()) / max(entry, EPS) - 1.0
            row[f"mae_t{w}"] = num(sub.low.min()) / max(entry, EPS) - 1.0
            row[f"hit_stop_t{w}"] = bool((sub.low <= defense).any()) if defense > 0 else False
            row[f"hit_target_t{w}"] = bool((sub.high >= target).any()) if target > 0 else False
            path_tag, failure_reason = classify_path(sub, entry, defense, target)
            row[f"path_tag_t{w}"] = path_tag
            if w == 8:
                row["main_failure_reason"] = failure_reason if path_tag in {"quick_fail", "failed_after_hold", "no_follow", "weak_path"} else "none"
        labels.append(row)
    out = pd.DataFrame(labels)
    write_csv(out, Path(output))
    return out


def summarize_review(review_csv: str, output_dir: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    df = pd.read_csv(review_csv, dtype={"trade_date": str, "stock_code": str})
    if df.empty:
        summary = pd.DataFrame()
        permission = default_permission()
    else:
        rows = []
        for hyp, g in df.groupby("primary_hypothesis"):
            entered = g[g["entry_triggered"].apply(boolish)] if "entry_triggered" in g.columns else g
            sample = len(entered)
            avg_ret_t8 = float(pd.to_numeric(entered.get("ret_t8", pd.Series(dtype=float)), errors="coerce").mean()) if sample else math.nan
            win_rate_t8 = float((pd.to_numeric(entered.get("ret_t8", pd.Series(dtype=float)), errors="coerce") > 0).mean()) if sample else math.nan
            stop_rate_t8 = float(entered.get("hit_stop_t8", pd.Series(dtype=bool)).apply(boolish).mean()) if sample else math.nan
            target_rate_t8 = float(entered.get("hit_target_t8", pd.Series(dtype=bool)).apply(boolish).mean()) if sample else math.nan
            avg_mfe_t8 = float(pd.to_numeric(entered.get("mfe_t8", pd.Series(dtype=float)), errors="coerce").mean()) if sample else math.nan
            avg_mae_t8 = float(pd.to_numeric(entered.get("mae_t8", pd.Series(dtype=float)), errors="coerce").mean()) if sample else math.nan
            allowed = bool(sample >= CFG.permission_min_sample and win_rate_t8 >= CFG.permission_min_win_rate and avg_ret_t8 >= CFG.permission_min_avg_ret_t8 and stop_rate_t8 <= CFG.permission_max_stop_rate)
            rows.append({
                "primary_hypothesis": hyp,
                "sample_entered": sample,
                "avg_ret_t8": avg_ret_t8,
                "win_rate_t8": win_rate_t8,
                "stop_rate_t8": stop_rate_t8,
                "target_rate_t8": target_rate_t8,
                "avg_mfe_t8": avg_mfe_t8,
                "avg_mae_t8": avg_mae_t8,
                "permission_allowed": allowed,
                "permission_reason": f"sample={sample};win={win_rate_t8:.2%};avg_ret_t8={avg_ret_t8:.2%};stop={stop_rate_t8:.2%}" if sample else "no_entered_sample",
            })
        summary = pd.DataFrame(rows).sort_values("avg_ret_t8", ascending=False)
        permission = default_permission()
        permission["permission_mode"] = "REVIEW_DERIVED"
        permission["source_review_csv"] = str(review_csv)
        for _, r in summary.iterrows():
            hyp = str(r.primary_hypothesis)
            if hyp and hyp != "NONE":
                allowed = boolish(r.permission_allowed)
                scale = 1.0 if allowed else 0.0
                if hyp == "H3_PRE_BREAKOUT_NIGHT" and allowed:
                    scale = 0.45
                permission["hypotheses"][hyp] = {"allowed": allowed, "position_scale": scale, "reason": str(r.permission_reason)}
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(summary, out_dir / "second_employee_hypothesis_backtest_summary.csv")
    (out_dir / "second_employee_model_permission.json").write_text(json.dumps(permission, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary, permission


# =============================================================================
# 12. CLI operations
# =============================================================================


def run_command(args: argparse.Namespace) -> None:
    full = load_local_kline(args.input)
    output_dir = Path(args.output_dir)
    registry_path = args.registry if args.registry else str(output_dir / "second_employee_core_zone_registry.csv")
    permission_path = args.permission if args.permission else str(output_dir / "second_employee_model_permission.json")
    decisions, registry, market = build_decisions(full, trade_date=args.trade_date, output_dir=output_dir, permission_path=permission_path, registry_path=registry_path)
    write_outputs(decisions, registry, market, output_dir)
    print(json.dumps({"ok": True, "model_version": MODEL_VERSION, "trade_date": market.trade_date, "rows": int(len(decisions)), "formal": int(decisions["formal_pool_flag"].apply(boolish).sum())}, ensure_ascii=False))


def backtest_command(args: argparse.Namespace) -> None:
    full = load_local_kline(args.input)
    dates = sorted(full["trade_date"].astype(str).unique())
    start = nearest_trade_key(args.backtest_start) if args.backtest_start else dates[0]
    end = nearest_trade_key(args.backtest_end) if args.backtest_end else dates[-1]
    dates = [d for d in dates if start <= d <= end]
    output_dir = Path(args.output_dir)
    bt_dir = output_dir / "backtest_daily"
    bt_dir.mkdir(parents=True, exist_ok=True)
    snapshots: List[pd.DataFrame] = []
    registry_path = str(output_dir / "second_employee_core_zone_registry.csv")
    permission_path = args.permission if args.permission else None
    for d in dates:
        hist = full[full["trade_date"].astype(str) <= d].copy()
        if hist.groupby("stock_code").size().max() < CFG.min_bars_available:
            continue
        try:
            decisions, registry, market = build_decisions(hist, trade_date=d, permission_path=permission_path, registry_path=registry_path)
            snap = decisions[decisions["formal_pool_flag"].apply(boolish)].copy()
            if not snap.empty:
                snapshots.append(snap)
            if not registry.empty:
                write_csv(registry, Path(registry_path))
        except Exception as exc:
            print(f"WARN backtest date {d} failed: {exc}", file=sys.stderr)
            continue
    if snapshots:
        all_rec = pd.concat(snapshots, ignore_index=True)
    else:
        all_rec = pd.DataFrame()
    write_csv(all_rec, output_dir / "second_employee_backtest_recommendations.csv")
    if not all_rec.empty:
        review_path = output_dir / "second_employee_backtest_review_labels.csv"
        tmp_rec = output_dir / "second_employee_backtest_review_snapshot.csv"
        write_csv(all_rec, tmp_rec)
        review_recommendations(str(tmp_rec), args.input, str(review_path))
        summarize_review(str(review_path), str(output_dir))
    print(json.dumps({"ok": True, "dates": len(dates), "recommendations": int(len(all_rec))}, ensure_ascii=False))


def selfcheck_command(_: argparse.Namespace) -> None:
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="second_employee_v12_0_"))
    rows: List[Dict[str, Any]] = []
    dates = pd.bdate_range("2023-01-02", periods=360).strftime("%Y%m%d").tolist()
    for sidx in range(12):
        code = f"000{sidx+1:03d}.SZ"
        name = f"测试{sidx+1}"
        base = 8.0 + sidx * 0.6
        price = base
        for i, d in enumerate(dates):
            trend = 1 + 0.0006 * i + 0.015 * math.sin(i / 22.0 + sidx)
            shock = 0.0
            if sidx in {0, 1, 2} and i in {300, 301, 302}:
                shock = 0.04 + 0.01 * sidx
            close = base * trend * (1 + shock)
            open_ = price * (1 + 0.002 * math.sin(i / 5.0))
            high = max(open_, close) * (1.01 + 0.004 * math.sin(i / 3.0))
            low = min(open_, close) * (0.99 - 0.003 * math.cos(i / 4.0))
            vol = 1_000_000 * (1 + 0.15 * math.sin(i / 9.0) + 0.05 * sidx)
            if shock > 0:
                vol *= 2.05
            amount = vol * close
            rows.append({"trade_date": d, "stock_code": code, "stock_name": name, "open": open_, "high": high, "low": low, "close": close, "volume": vol, "amount": amount, "sector_code": "测试板块" if sidx < 6 else "其他板块", "is_st": False, "is_suspended": False, "listed_days": 1000})
            price = close
    sample = pd.DataFrame(rows)
    input_path = tmp / "sample.csv"
    sample.to_csv(input_path, index=False, encoding="utf-8-sig")
    out_dir = tmp / "out"
    decisions, registry, market = build_decisions(load_local_kline(str(input_path)), output_dir=out_dir)
    write_outputs(decisions, registry, market, out_dir)
    assert (out_dir / "second_employee_decisions.csv").exists()
    assert (out_dir / "second_employee_report.md").exists()
    assert (out_dir / "second_employee_review_snapshot.csv").exists()
    assert (out_dir / "second_employee_field_registry.csv").exists()
    rec_path = out_dir / "second_employee_review_snapshot.csv"
    review_path = out_dir / "second_employee_review_labels.csv"
    review_recommendations(str(rec_path), str(input_path), str(review_path))
    summarize_review(str(review_path), str(out_dir))
    print(json.dumps({"ok": True, "tmp_dir": str(tmp), "decisions": int(len(decisions)), "formal": int(decisions["formal_pool_flag"].apply(boolish).sum()), "market": market.regime}, ensure_ascii=False))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Second Employee V12.0 Local-first profit engine")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--input", required=True)
    p_run.add_argument("--output-dir", required=True)
    p_run.add_argument("--trade-date", default=None)
    p_run.add_argument("--permission", default=None)
    p_run.add_argument("--registry", default=None)
    p_run.set_defaults(func=run_command)

    p_review = sub.add_parser("review")
    p_review.add_argument("--recommendations", required=True)
    p_review.add_argument("--eod", required=True)
    p_review.add_argument("--output", required=True)
    p_review.set_defaults(func=lambda a: print(json.dumps({"ok": True, "rows": int(len(review_recommendations(a.recommendations, a.eod, a.output)))}, ensure_ascii=False)))

    p_summary = sub.add_parser("summary")
    p_summary.add_argument("--review", required=True)
    p_summary.add_argument("--output-dir", required=True)
    p_summary.set_defaults(func=lambda a: print(json.dumps({"ok": True, "summary_rows": int(len(summarize_review(a.review, a.output_dir)[0]))}, ensure_ascii=False)))

    p_bt = sub.add_parser("backtest")
    p_bt.add_argument("--input", required=True)
    p_bt.add_argument("--output-dir", required=True)
    p_bt.add_argument("--backtest-start", default=None)
    p_bt.add_argument("--backtest-end", default=None)
    p_bt.add_argument("--permission", default=None)
    p_bt.set_defaults(func=backtest_command)

    p_self = sub.add_parser("selfcheck")
    p_self.set_defaults(func=selfcheck_command)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
