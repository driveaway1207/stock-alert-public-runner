#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pressure_band_validator.py

固定核心压力带 + 日线高级突破可实操筛选模块

目标：
1）不是找“有压力带的股票”；
2）而是找“当前最核心固定压力带被日线高级K线有效突破后，具备实操价值的股票”；
3）压力带核心上沿必须来自固定历史锚点，不允许每天随动态成交区漂移；
4）全量扫描后自动输出：
   - pressure_band_actionable.csv         可实操候选，只看这个
   - pressure_band_waiting_breakout.csv   核心压力清晰但尚未高级突破
   - pressure_band_failed_breakout.csv    冲高失败/假突破风险
   - pressure_band_all_validation.csv     全量验证数据
   - pressure_band_failed.csv             读取/计算失败清单

推荐工作流：
python modules/pressure_band_validator.py \
  --symbols-file output/symbols_from_cache.txt \
  --cache-dir kline_cache \
  --out-dir output

依赖：
pandas numpy akshare tqdm
"""

from __future__ import annotations

import argparse
import math
import re
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

try:
    import akshare as ak
except Exception:
    ak = None

try:
    from tqdm import tqdm
except Exception:
    tqdm = None


# ============================================================
# 1. 配置
# ============================================================

@dataclass
class Config:
    # 多周期窗口
    daily_bars: int = 620
    weekly_bars: int = 260
    monthly_bars: int = 180
    quarterly_bars: int = 90
    yearly_bars: int = 35

    # 固定锚点与压力带聚类
    anchor_band_width_pct: float = 0.010      # 单锚点附近允许 1% 带宽
    cross_cluster_pct: float = 0.025          # 锚点聚类距离 2.5%
    min_anchor_score: float = 14.0
    min_cluster_score: float = 38.0
    max_above_current_pct: float = 0.55       # 当前价上方 55% 内仍考虑
    max_below_current_pct: float = 0.20       # 当前价下方 20% 内可作为已突破核心区

    # Volume Profile 辅助，不允许单独主导核心线
    base_bucket_pct: float = 0.008
    min_bucket_pct: float = 0.004
    max_bucket_pct: float = 0.018
    atr_bucket_multiplier: float = 0.35

    # 高级突破K
    broke_buffer_pct: float = 0.003
    advanced_close_pos_s: float = 0.82
    advanced_close_pos_a: float = 0.74
    min_body_above_line_s: float = 0.60
    min_body_above_line_a: float = 0.45
    max_upper_wick_s: float = 0.28
    max_upper_wick_a: float = 0.38
    healthy_vol_ratio_min: float = 1.15
    healthy_vol_ratio_max: float = 4.20
    standard_double_vol_min: float = 1.80
    standard_double_vol_max: float = 2.50

    # 可交易过滤
    min_rr_actionable: float = 1.50
    min_rr_preferred: float = 2.00
    high_position_pct: float = 0.80
    high_position_min_rr: float = 2.00
    max_bias20_preferred: float = 0.16
    max_bias60_preferred: float = 0.28
    max_rsi_preferred: float = 82.0
    max_recent20_gain_preferred: float = 0.35

    # 等待突破候选
    waiting_near_pct: float = 0.06

    # 输出
    top_n_preview: int = 300
    print_top: int = 30


PERIOD_NAME = {"D": "日线", "W": "周线", "M": "月线", "Q": "季线", "Y": "年线"}
PERIOD_ORDER = {"D": 1, "W": 2, "M": 3, "Q": 4, "Y": 5}
PERIOD_WEIGHT = {"D": 1.00, "W": 1.35, "M": 1.80, "Q": 2.20, "Y": 2.65}

SOURCE_CN = {
    "period_high": "周期最高点",
    "swing_high": "阶段高点/前高",
    "recent_platform_high": "近端平台上沿",
    "max_bull_volume_high": "最大量阳K高点",
    "upper_wick_resonance": "上影线共振压力",
    "false_break_memory": "假突破记忆压力",
    "down_gap_pressure": "向下跳空缺口压力",
    "large_supply_k_high": "放量供应K高点",
    "volume_profile_hvn": "成交密集区上沿",
}


@dataclass
class Anchor:
    period: str
    price: float
    source: str
    score: float
    date: str = ""
    detail: str = ""
    lower: float = 0.0
    upper: float = 0.0

    def __post_init__(self):
        if self.lower == 0.0:
            self.lower = self.price
        if self.upper == 0.0:
            self.upper = self.price


@dataclass
class PressureCluster:
    lower: float
    upper: float
    core_line: float
    score: float
    periods: List[str]
    sources: List[str]
    anchors: List[Anchor] = field(default_factory=list)
    status: str = ""
    role: str = ""


# ============================================================
# 2. 通用工具
# ============================================================

def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def normalize_symbol(symbol: str) -> str:
    s = str(symbol).strip().upper().replace(" ", "")
    if not s:
        return s
    m = re.match(r"^(SZ|SH|BJ)\.(\d{6})$", s)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    m = re.match(r"^(\d{6})\.(SZ|SH|BJ)$", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    if re.match(r"^\d{6}$", s):
        if s.startswith(("6", "9")):
            return f"SH.{s}"
        if s.startswith(("8", "4")):
            return f"BJ.{s}"
        return f"SZ.{s}"
    return s


def ak_symbol(symbol: str) -> str:
    s = normalize_symbol(symbol)
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else s


def market_prefix_code(code: str) -> str:
    code = str(code).zfill(6)
    return normalize_symbol(code)


def pct(a: float, b: float) -> float:
    if b == 0 or pd.isna(a) or pd.isna(b):
        return np.nan
    return (a / b - 1.0) * 100.0


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def safe_float(x, default=np.nan) -> float:
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def fmt_price(x) -> str:
    if x is None or pd.isna(x):
        return ""
    return f"{float(x):.2f}"


def fmt_band(l, u) -> str:
    if l is None or u is None or pd.isna(l) or pd.isna(u):
        return ""
    return f"{float(l):.2f}-{float(u):.2f}"


def candle_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    d["body"] = (d["close"] - d["open"]).abs()
    d["body_ratio"] = d["body"] / rng
    d["body_pct"] = d["body"] / d["close"].replace(0, np.nan)
    d["upper_wick"] = d["high"] - d[["open", "close"]].max(axis=1)
    d["lower_wick"] = d[["open", "close"]].min(axis=1) - d["low"]
    d["upper_wick_ratio"] = d["upper_wick"] / rng
    d["lower_wick_ratio"] = d["lower_wick"] / rng
    d["close_pos"] = (d["close"] - d["low"]) / rng
    d["is_bull"] = d["close"] > d["open"]
    d["is_bear"] = d["close"] < d["open"]
    d["vol_ma20"] = d["volume"].rolling(20, min_periods=5).mean()
    d["vol_ratio20"] = d["volume"] / d["vol_ma20"].replace(0, np.nan)
    d["prev_volume"] = d["volume"].shift(1)
    d["vol_ratio_prev"] = d["volume"] / d["prev_volume"].replace(0, np.nan)

    delta = d["close"].diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)
    rs = up.rolling(14, min_periods=5).mean() / down.rolling(14, min_periods=5).mean().replace(0, np.nan)
    d["rsi14"] = 100 - 100 / (1 + rs)

    ma20 = d["close"].rolling(20, min_periods=5).mean()
    ma60 = d["close"].rolling(60, min_periods=20).mean()
    d["ma20"] = ma20
    d["ma60"] = ma60
    d["bias20"] = d["close"] / ma20.replace(0, np.nan) - 1
    d["bias60"] = d["close"] / ma60.replace(0, np.nan) - 1
    d["gain20"] = d["close"] / d["close"].shift(20).replace(0, np.nan) - 1
    return d


def calc_atr(df: pd.DataFrame, n: int = 20) -> float:
    if len(df) < 5:
        return np.nan
    d = df.copy()
    prev_close = d["close"].shift(1)
    tr = pd.concat(
        [
            d["high"] - d["low"],
            (d["high"] - prev_close).abs(),
            (d["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return safe_float(tr.rolling(n, min_periods=5).mean().iloc[-1])


def calc_atr_pct(df: pd.DataFrame, n: int = 20) -> float:
    atr = calc_atr(df, n)
    close = safe_float(df["close"].iloc[-1]) if not df.empty else np.nan
    if close <= 0 or pd.isna(atr):
        return 0.02
    return float(atr / close)


def adaptive_bucket_pct(df: pd.DataFrame, cfg: Config) -> float:
    atr_pct = calc_atr_pct(df)
    bucket = max(cfg.base_bucket_pct, atr_pct * cfg.atr_bucket_multiplier)
    return clamp(bucket, cfg.min_bucket_pct, cfg.max_bucket_pct)


def log_bucket_id(price: float, bucket_pct: float) -> int:
    if price <= 0:
        return 0
    return int(math.floor(math.log(price) / math.log(1 + bucket_pct)))


def bucket_bounds(bucket_id: int, bucket_pct: float) -> Tuple[float, float]:
    lower = math.exp(bucket_id * math.log(1 + bucket_pct))
    upper = math.exp((bucket_id + 1) * math.log(1 + bucket_pct))
    return lower, upper


# ============================================================
# 3. 数据读取
# ============================================================

AK_COL_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
}


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy()
    d = d.rename(columns={c: AK_COL_MAP.get(c, c) for c in d.columns})
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in d.columns]
    if missing:
        raise ValueError(f"K线字段缺失: {missing}; 当前字段={list(d.columns)}")
    d["date"] = pd.to_datetime(d["date"])
    for c in ["open", "high", "low", "close", "volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    if "amount" not in d.columns:
        d["amount"] = d["close"] * d["volume"]
    else:
        d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
    d = d.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    d = d.sort_values("date").drop_duplicates("date")
    d = d[d["volume"] > 0]
    d = d.reset_index(drop=True)
    return d[["date", "open", "high", "low", "close", "volume", "amount"]]


def fetch_daily_akshare(symbol: str, start_date: str, end_date: str, retries: int = 3, sleep_sec: float = 0.8) -> pd.DataFrame:
    if ak is None:
        raise RuntimeError("未安装 akshare，请先 pip install akshare")
    code = ak_symbol(symbol)
    last_err = None
    for i in range(retries):
        try:
            raw = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
            )
            return normalize_ohlcv(raw)
        except Exception as e:
            last_err = e
            time.sleep(sleep_sec * (i + 1))
    raise RuntimeError(f"source=akshare stage=fetch_kline symbol={symbol} retry={retries} err={last_err}")


def load_daily(symbol: str, cache_dir: Path, start_date: str, end_date: str, refresh: bool = False) -> pd.DataFrame:
    ns = normalize_symbol(symbol)
    code = ak_symbol(ns)
    ensure_dir(cache_dir)
    possible_names = [
        f"{code}.csv",
        f"{code}_daily.csv",
        f"{code}_daily_qfq.csv",
        f"{ns.replace('.', '_')}_daily_qfq.csv",
        f"{ns.replace('.', '_')}.csv",
        f"{ns}_daily_qfq.csv",
    ]

    if not refresh:
        for name in possible_names:
            cache_file = cache_dir / name
            if cache_file.exists():
                try:
                    d = pd.read_csv(cache_file)
                    d = normalize_ohlcv(d)
                    if not d.empty:
                        print(f"[CACHE_HIT] {ns} <- {cache_file}")
                        return d
                except Exception as e:
                    print(f"[CACHE_BAD] {ns} {cache_file} err={e}")

    print(f"[CACHE_MISS] {ns}; fetch from akshare")
    d = fetch_daily_akshare(ns, start_date=start_date, end_date=end_date)
    if not d.empty:
        save_file = cache_dir / f"{code}.csv"
        d.to_csv(save_file, index=False, encoding="utf-8-sig")
    return d


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    d = df.copy().set_index("date")
    out = pd.DataFrame(
        {
            "open": d["open"].resample(rule).first(),
            "high": d["high"].resample(rule).max(),
            "low": d["low"].resample(rule).min(),
            "close": d["close"].resample(rule).last(),
            "volume": d["volume"].resample(rule).sum(),
            "amount": d["amount"].resample(rule).sum(),
        }
    ).dropna().reset_index()
    return out


def build_period_frames(daily: pd.DataFrame, cfg: Config) -> Dict[str, pd.DataFrame]:
    d = daily.tail(cfg.daily_bars).copy()
    w = resample_ohlcv(daily, "W-FRI").tail(cfg.weekly_bars)
    m = resample_ohlcv(daily, "ME").tail(cfg.monthly_bars)
    q = resample_ohlcv(daily, "QE").tail(cfg.quarterly_bars)
    y = resample_ohlcv(daily, "YE").tail(cfg.yearly_bars)
    return {"D": d, "W": w, "M": m, "Q": q, "Y": y}


# ============================================================
# 4. 固定锚点提取
# ============================================================

def make_anchor(period: str, price: float, source: str, score: float, cfg: Config, date: str = "", detail: str = "") -> Anchor:
    price = float(price)
    w = cfg.anchor_band_width_pct
    return Anchor(
        period=period,
        price=price,
        lower=price * (1 - w),
        upper=price * (1 + w),
        source=source,
        score=float(score),
        date=date,
        detail=detail,
    )


def in_current_zone(price: float, current_price: float, cfg: Config) -> bool:
    return current_price * (1 - cfg.max_below_current_pct) <= price <= current_price * (1 + cfg.max_above_current_pct)


def extract_period_high_anchor(df: pd.DataFrame, period: str, current_price: float, cfg: Config) -> List[Anchor]:
    if len(df) < 8:
        return []
    d = candle_features(df)
    idx = d["high"].idxmax()
    r = d.loc[idx]
    high = safe_float(r["high"])
    if high <= 0 or not in_current_zone(high, current_price, cfg):
        return []
    vol_ratio = safe_float(r.get("vol_ratio20", 1), 1)
    wick = safe_float(r.get("upper_wick_ratio", 0), 0)
    score = (30 + 8 * min(vol_ratio, 3) + 8 * wick) * PERIOD_WEIGHT.get(period, 1.0)
    return [make_anchor(period, high, "period_high", score, cfg, str(pd.to_datetime(r["date"]).date()), f"{PERIOD_NAME[period]}窗口最高点")]


def extract_swing_high_anchors(df: pd.DataFrame, period: str, current_price: float, cfg: Config) -> List[Anchor]:
    if len(df) < 30:
        return []
    d = candle_features(df).reset_index(drop=True)
    highs = d["high"]
    is_swing = highs == highs.rolling(7, center=True, min_periods=3).max()
    cand = d[is_swing.fillna(False)].copy()
    if cand.empty:
        cand = d.nlargest(10, "high").copy()
    cand = cand.sort_values(["high", "volume"], ascending=False).head(12)
    anchors = []
    for _, r in cand.iterrows():
        high = safe_float(r["high"])
        if high <= 0 or not in_current_zone(high, current_price, cfg):
            continue
        high_pct = float((d["high"] <= high).mean())
        vol_ratio = safe_float(r.get("vol_ratio20", 1), 1)
        upper_wick = safe_float(r.get("upper_wick_ratio", 0), 0)
        close_pos = safe_float(r.get("close_pos", 0.5), 0.5)
        score = (16 + 18 * high_pct + 7 * min(vol_ratio, 3) + 9 * upper_wick + 5 * (1 - close_pos)) * PERIOD_WEIGHT.get(period, 1.0)
        anchors.append(make_anchor(period, high, "swing_high", score, cfg, str(pd.to_datetime(r["date"]).date()), f"阶段高点/前高，高点分位{high_pct:.0%}"))
    return anchors


def extract_recent_platform_high_anchors(df: pd.DataFrame, period: str, current_price: float, cfg: Config) -> List[Anchor]:
    """近端平台上沿：波动收敛、近N根的上边界，作为短线交易确认线。"""
    if period not in ["D", "W"] or len(df) < 60:
        return []
    d = candle_features(df).reset_index(drop=True)
    anchors = []
    for n in [20, 40, 60]:
        seg = d.tail(n)
        if len(seg) < n * 0.8:
            continue
        high = safe_float(seg["high"].max())
        low = safe_float(seg["low"].min())
        if high <= 0 or not in_current_zone(high, current_price, cfg):
            continue
        width = high / max(low, 1e-9) - 1
        # 平台不能太宽；太宽不是明确上沿
        if width > 0.28:
            continue
        touch = int((seg["high"] >= high * 0.985).sum())
        small_body = float((seg["body_ratio"].fillna(1) <= 0.55).mean())
        vol_cv = safe_float(seg["volume"].std() / max(seg["volume"].mean(), 1), 1)
        score = (18 + 5 * min(touch, 5) + 10 * small_body + 8 * max(0, 1 - vol_cv)) * PERIOD_WEIGHT.get(period, 1.0)
        anchors.append(make_anchor(period, high, "recent_platform_high", score, cfg, str(pd.to_datetime(seg["date"].iloc[-1]).date()), f"近{n}根平台上沿，触碰{touch}次"))
    return anchors


def extract_max_bull_volume_anchors(df: pd.DataFrame, period: str, current_price: float, cfg: Config) -> List[Anchor]:
    if len(df) < 20:
        return []
    d = candle_features(df).copy()
    d["valid"] = (
        (d["close"] > d["open"]) &
        (d["body_ratio"].fillna(0) >= 0.28) &
        (d["upper_wick_ratio"].fillna(0) <= 0.60)
    )
    cand = d[d["valid"]].sort_values("volume", ascending=False).head(4)
    anchors = []
    for _, r in cand.iterrows():
        high = safe_float(r["high"])
        if high <= 0 or not in_current_zone(high, current_price, cfg):
            continue
        vol_rank = float((d["volume"] <= r["volume"]).mean())
        body_ratio = safe_float(r.get("body_ratio", 0), 0)
        close_pos = safe_float(r.get("close_pos", 0), 0)
        score = (22 + 26 * vol_rank + 12 * body_ratio + 7 * close_pos) * PERIOD_WEIGHT.get(period, 1.0)
        anchors.append(make_anchor(period, high, "max_bull_volume_high", score, cfg, str(pd.to_datetime(r["date"]).date()), f"最大量阳K高点，量能分位{vol_rank:.0%}"))
    return anchors


def extract_upper_wick_resonance_anchors(df: pd.DataFrame, period: str, current_price: float, cfg: Config) -> List[Anchor]:
    if len(df) < 40:
        return []
    d = candle_features(df).copy().reset_index(drop=True)
    cand = d[
        (d["upper_wick_ratio"].fillna(0) >= 0.38) &
        (d["close_pos"].fillna(1) <= 0.66) &
        (d["vol_ratio20"].fillna(1) >= 1.10)
    ].copy()
    if cand.empty:
        return []
    bucket_pct = adaptive_bucket_pct(d, cfg) * 1.5
    cand["bid"] = cand["high"].apply(lambda x: log_bucket_id(float(x), bucket_pct))
    anchors = []
    for bid, g in cand.groupby("bid"):
        if len(g) < 2:
            continue
        lower, upper = bucket_bounds(int(bid), bucket_pct)
        high = safe_float(g["high"].max())
        if high <= 0 or not in_current_zone(high, current_price, cfg):
            continue
        touch = len(g)
        avg_wick = safe_float(g["upper_wick_ratio"].mean(), 0)
        avg_vol_ratio = safe_float(g["vol_ratio20"].replace([np.inf, -np.inf], np.nan).fillna(1).mean(), 1)
        score = (24 + 8 * min(touch, 5) + 18 * avg_wick + 6 * min(avg_vol_ratio, 3)) * PERIOD_WEIGHT.get(period, 1.0)
        anchors.append(make_anchor(period, high, "upper_wick_resonance", score, cfg, str(pd.to_datetime(g["date"].iloc[-1]).date()), f"上影线共振{touch}次"))
    return anchors


def extract_false_break_anchors(df: pd.DataFrame, period: str, current_price: float, cfg: Config) -> List[Anchor]:
    if len(df) < 50:
        return []
    d = candle_features(df).tail(260).copy().reset_index(drop=True)
    d["rolling_high_prev"] = d["high"].shift(1).rolling(20, min_periods=8).max()
    cand = d[
        (d["high"] > d["rolling_high_prev"] * 1.003) &
        (d["close"] < d["rolling_high_prev"] * 1.003) &
        (d["upper_wick_ratio"].fillna(0) >= 0.30) &
        (d["close_pos"].fillna(1) <= 0.68)
    ].copy()
    if cand.empty:
        return []
    anchors = []
    for _, r in cand.sort_values("high", ascending=False).head(8).iterrows():
        high = safe_float(r["high"])
        if high <= 0 or not in_current_zone(high, current_price, cfg):
            continue
        vol_ratio = safe_float(r.get("vol_ratio20", 1), 1)
        wick = safe_float(r.get("upper_wick_ratio", 0), 0)
        score = (30 + 8 * min(vol_ratio, 3) + 16 * wick) * PERIOD_WEIGHT.get(period, 1.0)
        anchors.append(make_anchor(period, high, "false_break_memory", score, cfg, str(pd.to_datetime(r["date"]).date()), "历史假突破/扫单失败高点"))
    return anchors


def extract_gap_pressure_anchors(df: pd.DataFrame, period: str, current_price: float, cfg: Config) -> List[Anchor]:
    if len(df) < 20:
        return []
    d = df.copy().reset_index(drop=True)
    d["prev_low"] = d["low"].shift(1)
    gaps = d[d["high"] < d["prev_low"] * 0.995].copy()
    if gaps.empty:
        return []
    gaps["gap_lower"] = gaps["high"]
    gaps["gap_upper"] = gaps["prev_low"]
    gaps["gap_pct"] = gaps["gap_upper"] / gaps["gap_lower"] - 1
    gaps = gaps.sort_values("date", ascending=False).head(6)
    anchors = []
    for _, r in gaps.iterrows():
        upper = safe_float(r["gap_upper"])
        lower = safe_float(r["gap_lower"])
        if upper <= 0 or not in_current_zone(upper, current_price, cfg):
            continue
        gap_pct = safe_float(r["gap_pct"], 0)
        score = (18 + 320 * min(gap_pct, 0.08)) * PERIOD_WEIGHT.get(period, 1.0)
        anchors.append(make_anchor(period, upper, "down_gap_pressure", score, cfg, str(pd.to_datetime(r["date"]).date()), f"向下跳空缺口上沿 {lower:.2f}-{upper:.2f}"))
    return anchors


def extract_large_supply_k_anchors(df: pd.DataFrame, period: str, current_price: float, cfg: Config) -> List[Anchor]:
    if len(df) < 40:
        return []
    d = candle_features(df).copy()
    cand = d[
        (d["vol_ratio20"].fillna(0) >= 1.6) &
        (
            (d["upper_wick_ratio"].fillna(0) >= 0.28) |
            (d["close_pos"].fillna(1) <= 0.62)
        )
    ].copy()
    if cand.empty:
        return []
    cand = cand.sort_values(["volume", "high"], ascending=False).head(8)
    anchors = []
    for _, r in cand.iterrows():
        high = safe_float(r["high"])
        if high <= 0 or not in_current_zone(high, current_price, cfg):
            continue
        vol_ratio = safe_float(r.get("vol_ratio20", 1), 1)
        close_pos = safe_float(r.get("close_pos", 0.5), 0.5)
        score = (18 + 9 * min(vol_ratio, 3) + 7 * (1 - close_pos)) * PERIOD_WEIGHT.get(period, 1.0)
        anchors.append(make_anchor(period, high, "large_supply_k_high", score, cfg, str(pd.to_datetime(r["date"]).date()), f"放量供应K高点，20日量比{vol_ratio:.2f}"))
    return anchors


def extract_volume_profile_anchors(df: pd.DataFrame, period: str, current_price: float, cfg: Config) -> List[Anchor]:
    """成交密集区只作辅助锚点，不允许单独主导核心压力线。"""
    if len(df) < 45:
        return []
    d = candle_features(df).reset_index(drop=True)
    bucket_pct = adaptive_bucket_pct(d, cfg)
    bucket_stats: Dict[int, Dict[str, float]] = {}
    n = len(d)
    for idx, row in d.iterrows():
        low = safe_float(row["low"])
        high = safe_float(row["high"])
        if low <= 0 or high <= 0 or high < low:
            continue
        b0 = log_bucket_id(low, bucket_pct)
        b1 = log_bucket_id(high, bucket_pct)
        bucket_ids = list(range(min(b0, b1), max(b0, b1) + 1))
        if not bucket_ids:
            continue
        age = n - 1 - idx
        recency_w = 0.5 ** (age / 120)
        per_vol = safe_float(row["volume"], 0) / len(bucket_ids)
        for bid in bucket_ids:
            st = bucket_stats.setdefault(bid, {"w_volume": 0.0, "cover": 0.0})
            st["w_volume"] += per_vol * recency_w
            st["cover"] += 1
    if not bucket_stats:
        return []
    rows = []
    for bid, st in bucket_stats.items():
        lower, upper = bucket_bounds(bid, bucket_pct)
        rows.append({"bid": bid, "lower": lower, "upper": upper, **st})
    p = pd.DataFrame(rows).sort_values("bid")
    p = p[(p["upper"] >= current_price * (1 - cfg.max_below_current_pct)) & (p["lower"] <= current_price * (1 + cfg.max_above_current_pct))].copy()
    if p.empty:
        return []
    threshold = max(p["w_volume"].quantile(0.82), p["w_volume"].mean() + 0.25 * p["w_volume"].std())
    hvn = p[(p["w_volume"] >= threshold) & (p["cover"] >= 3)].copy()
    if hvn.empty:
        return []
    hvn = hvn.sort_values("bid")
    clusters = []
    cur = []
    last = None
    for _, r in hvn.iterrows():
        bid = int(r["bid"])
        if last is None or bid - last <= 2:
            cur.append(r)
        else:
            if cur:
                clusters.append(pd.DataFrame(cur))
            cur = [r]
        last = bid
    if cur:
        clusters.append(pd.DataFrame(cur))

    total_w = max(1.0, p["w_volume"].sum())
    anchors = []
    for c in clusters[:8]:
        upper = safe_float(c["upper"].max())
        lower = safe_float(c["lower"].min())
        if upper <= 0 or not in_current_zone(upper, current_price, cfg):
            continue
        share = safe_float(c["w_volume"].sum() / total_w, 0)
        cover = safe_float(c["cover"].sum(), 0)
        score = (12 + 42 * share + 0.15 * cover) * PERIOD_WEIGHT.get(period, 1.0)
        anchors.append(make_anchor(period, upper, "volume_profile_hvn", score, cfg, "", f"成交密集区上沿，成交占比{share:.1%}"))
    return anchors


def extract_anchors_for_period(df: pd.DataFrame, period: str, current_price: float, cfg: Config) -> List[Anchor]:
    if df is None or df.empty or len(df) < 8:
        return []
    funcs = [
        extract_period_high_anchor,
        extract_swing_high_anchors,
        extract_recent_platform_high_anchors,
        extract_max_bull_volume_anchors,
        extract_upper_wick_resonance_anchors,
        extract_false_break_anchors,
        extract_gap_pressure_anchors,
        extract_large_supply_k_anchors,
        extract_volume_profile_anchors,
    ]
    anchors: List[Anchor] = []
    for fn in funcs:
        try:
            anchors.extend(fn(df, period, current_price, cfg))
        except Exception as e:
            print(f"[WARN] anchor extract failed period={period} fn={fn.__name__} err={e}")
    return [a for a in anchors if a.price > 0 and a.score >= cfg.min_anchor_score]


# ============================================================
# 5. 固定锚点聚类与核心线选择
# ============================================================

def anchors_near(a: Anchor, b: Anchor, cfg: Config) -> bool:
    mid = (a.price + b.price) / 2
    if mid <= 0:
        return False
    return abs(a.price - b.price) / mid <= cfg.cross_cluster_pct


def cluster_fixed_anchors(anchors: List[Anchor], current_price: float, cfg: Config) -> List[PressureCluster]:
    if not anchors:
        return []
    anchors = sorted(anchors, key=lambda a: a.price)
    groups: List[List[Anchor]] = []
    for a in anchors:
        placed = False
        for g in groups:
            if any(anchors_near(a, ga, cfg) for ga in g):
                g.append(a)
                placed = True
                break
        if not placed:
            groups.append([a])

    clusters: List[PressureCluster] = []
    for g in groups:
        prices = [x.price for x in g]
        lower = min(x.lower for x in g)
        upper = max(x.upper for x in g)
        core_line = max(prices)  # 核心确认线必须来自真实锚点最高价
        periods = sorted(set(x.period for x in g), key=lambda p: PERIOD_ORDER.get(p, 99))
        sources = sorted(set(x.source for x in g))

        anchor_score = sum(x.score for x in g) * 0.38
        period_bonus = sum(PERIOD_WEIGHT.get(p, 1.0) for p in periods) * 8.5
        source_bonus = min(len(sources), 7) * 2.8

        dist = abs(core_line / current_price - 1) if current_price > 0 else 0
        if dist <= 0.04:
            distance_bonus = 18
        elif dist <= 0.08:
            distance_bonus = 14
        elif dist <= 0.15:
            distance_bonus = 7
        elif dist <= 0.30:
            distance_bonus = 1
        else:
            distance_bonus = -10

        if core_line < current_price * (1 - cfg.max_below_current_pct):
            distance_bonus -= 20

        width_pct = (upper - lower) / max((upper + lower) / 2, 1e-9)
        width_adjust = 0
        if width_pct <= 0.04:
            width_adjust += 6
        elif width_pct > 0.12:
            width_adjust -= 12

        score = clamp(anchor_score + period_bonus + source_bonus + distance_bonus + width_adjust, 0, 100)
        if score < cfg.min_cluster_score:
            continue

        cluster = PressureCluster(
            lower=float(lower),
            upper=float(upper),
            core_line=float(core_line),
            score=float(score),
            periods=periods,
            sources=sources,
            anchors=sorted(g, key=lambda x: (-x.score, PERIOD_ORDER.get(x.period, 99))),
        )
        cluster.status, cluster.role = cluster_status(current_price, cluster, cfg)
        clusters.append(cluster)

    return sorted(clusters, key=lambda c: (c.score, len(c.periods)), reverse=True)


def cluster_status(current_price: float, c: PressureCluster, cfg: Config) -> Tuple[str, str]:
    line = c.core_line
    if current_price > line * (1 + cfg.broke_buffer_pct):
        if current_price <= line * 1.08:
            return "已突破，近端压力转支撑观察", "回踩不破可作为承接确认；不再作为当前主要压力"
        return "已明显突破，转为下方结构支撑", "只用于回踩/防守观察，不作为当前上方压力"
    if abs(current_price / line - 1) <= cfg.waiting_near_pct:
        return "正在接近核心上沿", "等待日线高级K线有效突破"
    if current_price < line:
        return "尚未突破，当前上方核心压力", "若被日线高级K线放量实体突破，才具备交易触发意义"
    return "状态待复核", "需要人工看图确认"


def select_core_lines(clusters: List[PressureCluster], current_price: float, cfg: Config) -> Tuple[Optional[PressureCluster], Optional[PressureCluster], Optional[PressureCluster]]:
    if not clusters:
        return None, None, None

    # 核心线1：近端固定核心线，离现价最近且质量合格
    near_sorted = sorted(
        clusters,
        key=lambda c: (abs(c.core_line / current_price - 1), -c.score, -len(c.periods))
    )
    core1 = near_sorted[0] if near_sorted else None

    # 核心线2：上方更大周期/最终确认线
    above = [c for c in clusters if c.core_line >= current_price * (1 - cfg.broke_buffer_pct)]
    def big_key(c: PressureCluster):
        big_period_score = sum(PERIOD_WEIGHT.get(p, 1.0) for p in c.periods)
        dist = c.core_line / current_price - 1
        # 0-20% 上方优先；太远实操性下降
        dist_penalty = abs(dist - 0.08) * 18 if dist >= 0 else 20
        return big_period_score * 12 + c.score - dist_penalty
    core2 = sorted(above, key=big_key, reverse=True)[0] if above else None

    if core1 and core2 and abs(core1.core_line / core2.core_line - 1) <= 0.015:
        alt = [c for c in above if abs(c.core_line / core1.core_line - 1) > 0.015]
        if alt:
            core2 = sorted(alt, key=big_key, reverse=True)[0]

    # 当前最重要突破确认线：优先未突破且最近的高质量固定核心线
    unbroken = [c for c in clusters if c.core_line > current_price * (1 + cfg.broke_buffer_pct)]
    if unbroken:
        most = sorted(unbroken, key=lambda c: (abs(c.core_line / current_price - 1), -c.score, -len(c.periods)))[0]
    else:
        # 若已经全部突破，选择最近刚突破的核心线，用于回踩确认
        most = core1
    return core1, core2, most


# ============================================================
# 6. 高级突破、空间、风险收益比
# ============================================================

def latest_break_quality(daily: pd.DataFrame, line: float, cfg: Config) -> Dict:
    d = candle_features(daily).copy()
    if d.empty or line <= 0:
        return {}
    r = d.iloc[-1]
    prev = d.iloc[-2] if len(d) >= 2 else r

    open_ = safe_float(r["open"])
    close = safe_float(r["close"])
    high = safe_float(r["high"])
    low = safe_float(r["low"])
    prev_close = safe_float(prev["close"])

    body_low = min(open_, close)
    body_high = max(open_, close)
    body_len = max(body_high - body_low, 1e-9)

    close_above = close > line * (1 + cfg.broke_buffer_pct)
    prev_above = prev_close > line * (1 + cfg.broke_buffer_pct)
    high_above = high > line * (1 + cfg.broke_buffer_pct)
    recent_new_break = close_above and not prev_above

    body_above_ratio = max(0.0, body_high - max(body_low, line)) / body_len
    close_pos = safe_float(r.get("close_pos"), np.nan)
    upper_wick_ratio = safe_float(r.get("upper_wick_ratio"), np.nan)
    vol_ratio_prev = safe_float(r.get("vol_ratio_prev"), np.nan)
    vol_ratio20 = safe_float(r.get("vol_ratio20"), np.nan)
    vol_ref = max(vol_ratio_prev if not pd.isna(vol_ratio_prev) else 0, vol_ratio20 if not pd.isna(vol_ratio20) else 0)

    is_standard_double = cfg.standard_double_vol_min <= vol_ratio_prev <= cfg.standard_double_vol_max
    is_healthy_volume = cfg.healthy_vol_ratio_min <= vol_ref <= cfg.healthy_vol_ratio_max
    intraday_failed = high_above and not close_above

    score = 0
    if close_above:
        score += 22
    if recent_new_break:
        score += 12
    if body_above_ratio >= cfg.min_body_above_line_s:
        score += 22
    elif body_above_ratio >= cfg.min_body_above_line_a:
        score += 14
    if close_pos >= cfg.advanced_close_pos_s:
        score += 18
    elif close_pos >= cfg.advanced_close_pos_a:
        score += 12
    if upper_wick_ratio <= cfg.max_upper_wick_s:
        score += 14
    elif upper_wick_ratio <= cfg.max_upper_wick_a:
        score += 8
    if is_standard_double:
        score += 16
    elif is_healthy_volume:
        score += 11
    if intraday_failed:
        score -= 40
    if vol_ref > 5 and close_pos < 0.70:
        score -= 18
    if close_above and upper_wick_ratio > 0.50:
        score -= 12

    score = clamp(score, 0, 100)
    if intraday_failed:
        grade = "D级冲高回落/假突破风险"
    elif score >= 82:
        grade = "S级高级突破"
    elif score >= 66:
        grade = "A级有效突破"
    elif score >= 48:
        grade = "B级普通突破/待确认"
    else:
        grade = "未形成有效高级突破"

    return {
        "最新日期": str(pd.to_datetime(r["date"]).date()),
        "最新开盘": open_,
        "最新最高": high,
        "最新最低": low,
        "最新收盘": close,
        "前收盘": prev_close,
        "日线收盘站上确认线": "是" if close_above else "否",
        "是否最新突破": "是" if recent_new_break else "否",
        "盘中突破但收盘失败": "是" if intraday_failed else "否",
        "实体站上线比例": body_above_ratio,
        "收盘位置": close_pos,
        "上影线比例": upper_wick_ratio,
        "昨比量": vol_ratio_prev,
        "20日量比": vol_ratio20,
        "是否标准倍量": "是" if is_standard_double else "否",
        "是否健康放量": "是" if is_healthy_volume else "否",
        "突破K评分": score,
        "突破K等级": grade,
    }


def estimate_space_rr(daily: pd.DataFrame, current_price: float, line: float, clusters: List[PressureCluster], cfg: Config) -> Dict:
    if daily.empty or current_price <= 0 or line <= 0:
        return {}
    d = candle_features(daily)
    atr = calc_atr(d)
    if pd.isna(atr) or atr <= 0:
        atr = current_price * 0.035

    above_lines = sorted({c.core_line for c in clusters if c.core_line > max(current_price, line) * 1.01})
    next_pressure = above_lines[0] if above_lines else np.nan

    recent_low_120 = safe_float(d.tail(120)["low"].min(), current_price * 0.85)
    box_height = max(line - recent_low_120, atr * 2)

    target1 = max(line + atr, line + 0.382 * box_height)
    target2 = max(line + 2 * atr, line + 0.618 * box_height)
    target3 = max(line + 3 * atr, line + 1.000 * box_height)

    if not pd.isna(next_pressure):
        target1 = min(target1, next_pressure)

    latest = d.iloc[-1]
    body_mid = (safe_float(latest["open"]) + safe_float(latest["close"])) / 2
    line_buffer_stop = line * 0.985
    latest_low_stop = safe_float(latest["low"]) * 0.995

    if current_price > line:
        stop = min(body_mid, line_buffer_stop, latest_low_stop)
    else:
        stop = line_buffer_stop

    risk = max(current_price - stop, current_price * 0.01)
    reward1 = max(target1 - current_price, 0)
    rr1 = reward1 / risk if risk > 0 else np.nan

    high_252 = safe_float(d.tail(252)["high"].max(), np.nan)
    low_252 = safe_float(d.tail(252)["low"].min(), np.nan)
    pos_252 = (current_price - low_252) / max(high_252 - low_252, 1e-9) if not pd.isna(high_252) and not pd.isna(low_252) else np.nan

    r = d.iloc[-1]
    bias20 = safe_float(r.get("bias20"), np.nan)
    bias60 = safe_float(r.get("bias60"), np.nan)
    rsi14 = safe_float(r.get("rsi14"), np.nan)
    gain20 = safe_float(r.get("gain20"), np.nan)

    high_note = ""
    if not pd.isna(pos_252) and pos_252 >= cfg.high_position_pct:
        high_note = "近一年高位，突破后必须更重视空间、承接和风险收益比"

    overheat_flags = []
    if not pd.isna(bias20) and bias20 > cfg.max_bias20_preferred:
        overheat_flags.append(f"20日乖离偏高{bias20:.1%}")
    if not pd.isna(bias60) and bias60 > cfg.max_bias60_preferred:
        overheat_flags.append(f"60日乖离偏高{bias60:.1%}")
    if not pd.isna(rsi14) and rsi14 > cfg.max_rsi_preferred:
        overheat_flags.append(f"RSI偏热{rsi14:.1f}")
    if not pd.isna(gain20) and gain20 > cfg.max_recent20_gain_preferred:
        overheat_flags.append(f"20日涨幅偏大{gain20:.1%}")

    if rr1 >= cfg.min_rr_preferred:
        space_grade = "空间较好"
    elif rr1 >= cfg.min_rr_actionable:
        space_grade = "空间一般但可观察"
    else:
        space_grade = "空间不足/不适合追"

    return {
        "ATR": atr,
        "下一层压力": next_pressure,
        "第一目标": target1,
        "第二目标": target2,
        "强势目标": target3,
        "交易防守位": stop,
        "单股风险": risk,
        "第一目标收益风险比": rr1,
        "空间等级": space_grade,
        "近一年位置": pos_252,
        "高位提示": high_note,
        "过热提示": "；".join(overheat_flags),
        "20日乖离": bias20,
        "60日乖离": bias60,
        "RSI14": rsi14,
        "20日涨幅": gain20,
    }


def trade_decision(break_info: Dict, space: Dict, most: Optional[PressureCluster], current_price: float, cfg: Config) -> Tuple[str, str, str, str]:
    if most is None:
        return "否", "无核心压力带", "没有识别到足够稳定的固定核心压力带", "无"

    grade = str(break_info.get("突破K等级", ""))
    close_above = break_info.get("日线收盘站上确认线", "") == "是"
    fail = break_info.get("盘中突破但收盘失败", "") == "是"
    rr = safe_float(space.get("第一目标收益风险比"), np.nan)
    pos = safe_float(space.get("近一年位置"), np.nan)
    overheat = str(space.get("过热提示", ""))

    if fail:
        return "否", "假突破/冲高回落", "盘中突破但收盘未站稳，不能追", "收盘重新站上确认线且上影线收敛前放弃"

    if not close_above:
        if most.core_line > current_price and abs(most.core_line / current_price - 1) <= cfg.waiting_near_pct:
            return "等待突破", "核心压力清晰但尚未突破", f"等待日线高级K有效突破 {fmt_price(most.core_line)}", "若冲高回落或放量长上影则放弃"
        return "否", "尚未突破核心压力带", "不是可实操突破候选", "无"

    if "S级" not in grade and "A级" not in grade:
        return "观察", "已站上但不是高级突破", "需要次日/三日承接或回踩不破后再确认", "跌回核心确认线下方放弃"

    if pd.isna(rr) or rr < cfg.min_rr_actionable:
        return "否", "收益风险比不足", "虽然突破，但第一目标空间不足以覆盖风险", "不追，除非回踩后防守位变近"

    if not pd.isna(pos) and pos >= cfg.high_position_pct:
        if rr < cfg.high_position_min_rr:
            return "谨慎观察", "高位突破但空间要求未完全满足", "历史/阶段高位必须看次日或三日承接", "跌回确认线或突破K实体中位放弃"
        if overheat:
            return "谨慎观察", "高位突破且存在过热提示", "可观察，但不适合无脑追；等承接确认", "放量长上影、跌回确认线、次日低开低走放弃"

    if "S级" in grade and rr >= cfg.min_rr_preferred:
        return "是", "S级高级突破核心压力带", "可进入重点可实操候选，次日不跌回确认线则继续有效", "收盘跌回确认线或放量长上影放弃"

    if "A级" in grade:
        if rr >= cfg.min_rr_preferred:
            return "是", "A级有效突破且风险收益比合格", "可进入可实操候选，优先看次日承接", "跌回确认线或突破K实体中位失守放弃"
        return "谨慎观察", "A级突破但空间一般", "可小仓/观察，最好等回踩不破再确认", "跌回确认线放弃"

    return "观察", "突破质量待确认", "需要次日承接确认", "跌回确认线放弃"


# ============================================================
# 7. 输出辅助
# ============================================================

def period_summary(clusters: List[PressureCluster], period: str) -> str:
    vals = []
    for c in clusters:
        if period in c.periods:
            vals.append(f"{fmt_band(c.lower, c.upper)} 上沿{fmt_price(c.core_line)}")
    return "；".join(vals[:3])


def sources_cn(c: Optional[PressureCluster]) -> str:
    if c is None:
        return ""
    return "、".join(SOURCE_CN.get(s, s) for s in c.sources)


def periods_cn(c: Optional[PressureCluster]) -> str:
    if c is None:
        return ""
    return "/".join(PERIOD_NAME.get(p, p) for p in c.periods)


def anchors_detail(c: Optional[PressureCluster]) -> str:
    if c is None:
        return ""
    parts = []
    for a in c.anchors[:6]:
        parts.append(f"{PERIOD_NAME.get(a.period,a.period)} {SOURCE_CN.get(a.source,a.source)} {fmt_price(a.price)} {a.date}")
    return "；".join(parts)


# ============================================================
# 8. 单股分析
# ============================================================

def analyze_symbol(symbol: str, name: str, daily: pd.DataFrame, cfg: Config) -> Dict:
    ns = normalize_symbol(symbol)
    if daily is None or daily.empty or len(daily) < 120:
        raise ValueError("empty_or_too_few_bars")

    daily = normalize_ohlcv(daily)
    current_price = safe_float(daily["close"].iloc[-1])
    frames = build_period_frames(daily, cfg)

    all_anchors: List[Anchor] = []
    for period, frame in frames.items():
        all_anchors.extend(extract_anchors_for_period(frame, period, current_price, cfg))

    clusters = cluster_fixed_anchors(all_anchors, current_price, cfg)
    clusters = clusters[:10]

    core1, core2, most = select_core_lines(clusters, current_price, cfg)
    line = most.core_line if most else np.nan

    break_info = latest_break_quality(frames["D"], line, cfg) if most else {}
    space = estimate_space_rr(frames["D"], current_price, line, clusters, cfg) if most else {}
    actionable, reason, next_action, abandon = trade_decision(break_info, space, most, current_price, cfg)

    if clusters:
        composite_lower = min(c.lower for c in clusters)
        composite_upper = max(c.upper for c in clusters)
    else:
        composite_lower = np.nan
        composite_upper = np.nan

    most_note = ""
    if most:
        if current_price > most.core_line * (1 + cfg.broke_buffer_pct):
            most_note = "当前价已站上该固定核心线，重点看是否站稳/回踩不破；若站稳，该线转为支撑观察。"
        else:
            most_note = "当前价尚未有效突破该固定核心线；只有日线高级K实体放量站上，才具备交易触发意义。"
    else:
        most_note = "未识别到足够稳定的固定核心压力带。"

    row = {
        "股票代码": ns,
        "股票名称": name,
        "当前价": round(current_price, 4),

        "固定核心压力带1": fmt_band(core1.lower, core1.upper) if core1 else "",
        "核心上沿1": round(core1.core_line, 4) if core1 else "",
        "核心上沿1状态": core1.status if core1 else "",
        "核心上沿1周期": periods_cn(core1),
        "核心上沿1来源": sources_cn(core1),
        "核心上沿1说明": core1.role if core1 else "",

        "固定核心压力带2": fmt_band(core2.lower, core2.upper) if core2 else "",
        "核心上沿2": round(core2.core_line, 4) if core2 else "",
        "核心上沿2状态": core2.status if core2 else "",
        "核心上沿2周期": periods_cn(core2),
        "核心上沿2来源": sources_cn(core2),
        "核心上沿2说明": core2.role if core2 else "",

        "当前最重要压力带": fmt_band(most.lower, most.upper) if most else "",
        "当前最重要突破确认线": round(most.core_line, 4) if most else "",
        "当前最重要压力状态": most.status if most else "",
        "当前最重要压力周期": periods_cn(most),
        "当前最重要压力来源": sources_cn(most),
        "当前最重要压力解释": most_note,
        "核心锚点明细": anchors_detail(most),

        "日线压力带": period_summary(clusters, "D"),
        "周线压力带": period_summary(clusters, "W"),
        "月线压力带": period_summary(clusters, "M"),
        "季线压力带": period_summary(clusters, "Q"),
        "年线压力带": period_summary(clusters, "Y"),
        "辅助复合压力带": fmt_band(composite_lower, composite_upper),
        "识别到的固定压力带数量": len(clusters),

        "最新日期": break_info.get("最新日期", ""),
        "日线收盘站上确认线": break_info.get("日线收盘站上确认线", ""),
        "是否最新突破": break_info.get("是否最新突破", ""),
        "盘中突破但收盘失败": break_info.get("盘中突破但收盘失败", ""),
        "突破K等级": break_info.get("突破K等级", ""),
        "突破K评分": round(safe_float(break_info.get("突破K评分")), 2) if break_info else "",
        "实体站上线比例": round(safe_float(break_info.get("实体站上线比例")), 4) if break_info else "",
        "收盘位置": round(safe_float(break_info.get("收盘位置")), 4) if break_info else "",
        "上影线比例": round(safe_float(break_info.get("上影线比例")), 4) if break_info else "",
        "昨比量": round(safe_float(break_info.get("昨比量")), 4) if break_info else "",
        "20日量比": round(safe_float(break_info.get("20日量比")), 4) if break_info else "",
        "是否标准倍量": break_info.get("是否标准倍量", ""),
        "是否健康放量": break_info.get("是否健康放量", ""),

        "下一层压力": round(safe_float(space.get("下一层压力")), 4) if space and not pd.isna(safe_float(space.get("下一层压力"))) else "",
        "第一目标": round(safe_float(space.get("第一目标")), 4) if space else "",
        "第二目标": round(safe_float(space.get("第二目标")), 4) if space else "",
        "强势目标": round(safe_float(space.get("强势目标")), 4) if space else "",
        "交易防守位": round(safe_float(space.get("交易防守位")), 4) if space else "",
        "第一目标收益风险比": round(safe_float(space.get("第一目标收益风险比")), 4) if space else "",
        "空间等级": space.get("空间等级", "") if space else "",
        "近一年位置": round(safe_float(space.get("近一年位置")), 4) if space else "",
        "高位提示": space.get("高位提示", "") if space else "",
        "过热提示": space.get("过热提示", "") if space else "",

        "是否可实操": actionable,
        "可实操原因": reason,
        "下一步动作": next_action,
        "放弃条件": abandon,
    }
    return row


# ============================================================
# 9. 股票池
# ============================================================

def get_a_stock_list() -> pd.DataFrame:
    if ak is None:
        raise RuntimeError("未安装 akshare，请改用 --symbols-file 输入自定义股票池。")
    try:
        df = ak.stock_info_a_code_name()
    except Exception as e:
        raise RuntimeError(f"无法通过 AkShare 获取A股股票列表，请改用 --symbols-file 输入自定义股票池。err={e}")

    col_map = {}
    for c in df.columns:
        if c in ["code", "代码", "证券代码"]:
            col_map[c] = "code"
        if c in ["name", "名称", "证券简称"]:
            col_map[c] = "name"
    df = df.rename(columns=col_map)
    if "code" not in df.columns:
        df["code"] = df.iloc[:, 0].astype(str)
    if "name" not in df.columns:
        df["name"] = ""
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["symbol"] = df["code"].apply(normalize_symbol)
    df["name"] = df["name"].astype(str)
    df = df[~df["name"].str.contains("ST|退", case=False, na=False)]
    return df[["symbol", "name"]].drop_duplicates("symbol")


def parse_symbols_arg(symbols: Optional[str], symbols_file: Optional[str], use_all: bool, limit: Optional[int]) -> pd.DataFrame:
    items = []
    if symbols:
        for s in re.split(r"[,，\s]+", symbols.strip()):
            if s:
                items.append({"symbol": normalize_symbol(s), "name": ""})

    if symbols_file:
        with open(symbols_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = re.split(r"[,，\s]+", line)
                sym = normalize_symbol(parts[0])
                nm = parts[1] if len(parts) > 1 else ""
                items.append({"symbol": sym, "name": nm})

    if use_all:
        df_all = get_a_stock_list()
        items.extend(df_all.to_dict("records"))

    df = pd.DataFrame(items)
    if df.empty:
        raise ValueError("请通过 --symbols、--symbols-file 或 --all 指定股票池。")
    df["symbol"] = df["symbol"].apply(normalize_symbol)
    df = df.drop_duplicates("symbol")
    if limit and limit > 0:
        df = df.head(limit)
    return df.reset_index(drop=True)


# ============================================================
# 10. 批量扫描与输出分层
# ============================================================

def scan_symbols(symbol_df: pd.DataFrame, cfg: Config, cache_dir: Path, start_date: str, end_date: str, refresh: bool = False) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    failed = []
    iterator = symbol_df.iterrows()
    if tqdm is not None:
        iterator = tqdm(symbol_df.iterrows(), total=len(symbol_df), desc="actionable-pressure-breakout-scan")

    for _, item in iterator:
        symbol = item["symbol"]
        name = item.get("name", "")
        try:
            daily = load_daily(symbol, cache_dir=cache_dir, start_date=start_date, end_date=end_date, refresh=refresh)
            if daily.empty or len(daily) < 120:
                failed.append({"symbol": symbol, "name": name, "reason": "empty_or_too_few_bars"})
                continue
            row = analyze_symbol(symbol, name, daily, cfg)
            rows.append(row)
        except Exception as e:
            failed.append({"symbol": symbol, "name": name, "reason": str(e)[:1200]})
    return pd.DataFrame(rows), pd.DataFrame(failed)


def sort_results(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    action_order = {"是": 1, "谨慎观察": 2, "观察": 3, "等待突破": 4, "否": 9}
    def grade_order(x):
        s = str(x)
        if "S级" in s:
            return 1
        if "A级" in s:
            return 2
        if "B级" in s:
            return 3
        if "D级" in s:
            return 8
        return 5

    df = df.copy()
    df["_实操排序"] = df["是否可实操"].map(action_order).fillna(9)
    df["_突破排序"] = df["突破K等级"].apply(grade_order)
    df["_RR"] = pd.to_numeric(df["第一目标收益风险比"], errors="coerce").fillna(0)
    df["_突破分"] = pd.to_numeric(df["突破K评分"], errors="coerce").fillna(0)
    price = pd.to_numeric(df["当前价"], errors="coerce")
    line = pd.to_numeric(df["当前最重要突破确认线"], errors="coerce")
    df["_距离确认线"] = ((price / line - 1) * 100).abs()

    df = df.sort_values(
        ["_实操排序", "_突破排序", "_RR", "_突破分", "_距离确认线"],
        ascending=[True, True, False, False, True],
    )
    return df.drop(columns=["_实操排序", "_突破排序", "_RR", "_突破分", "_距离确认线"], errors="ignore")


def split_outputs(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    if df.empty:
        return {
            "actionable": df.copy(),
            "waiting": df.copy(),
            "failed_breakout": df.copy(),
            "validation": df.copy(),
        }

    actionable = df[
        df["是否可实操"].astype(str).isin(["是", "谨慎观察"]) &
        df["突破K等级"].astype(str).str.contains("S级|A级", regex=True, na=False) &
        (df["日线收盘站上确认线"].astype(str) == "是") &
        (df["盘中突破但收盘失败"].astype(str) == "否") &
        (pd.to_numeric(df["第一目标收益风险比"], errors="coerce").fillna(0) >= 1.5)
    ].copy()

    waiting = df[
        (df["是否可实操"].astype(str) == "等待突破") |
        (
            df["当前最重要压力状态"].astype(str).str.contains("尚未突破|接近", regex=True, na=False) &
            (df["日线收盘站上确认线"].astype(str) != "是")
        )
    ].copy()

    failed = df[
        (df["盘中突破但收盘失败"].astype(str) == "是") |
        df["突破K等级"].astype(str).str.contains("D级|假突破|冲高回落", regex=True, na=False)
    ].copy()

    return {
        "actionable": sort_results(actionable),
        "waiting": sort_results(waiting),
        "failed_breakout": sort_results(failed),
        "validation": sort_results(df),
    }


def save_outputs(df: pd.DataFrame, failed: pd.DataFrame, out_dir: Path) -> None:
    ensure_dir(out_dir)
    outputs = split_outputs(sort_results(df))

    # 完整表
    outputs["validation"].to_csv(out_dir / "pressure_band_all_validation.csv", index=False, encoding="utf-8-sig")
    outputs["actionable"].to_csv(out_dir / "pressure_band_actionable.csv", index=False, encoding="utf-8-sig")
    outputs["waiting"].to_csv(out_dir / "pressure_band_waiting_breakout.csv", index=False, encoding="utf-8-sig")
    outputs["failed_breakout"].to_csv(out_dir / "pressure_band_failed_breakout.csv", index=False, encoding="utf-8-sig")

    if not failed.empty:
        failed.to_csv(out_dir / "pressure_band_failed.csv", index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=["symbol", "name", "reason"]).to_csv(out_dir / "pressure_band_failed.csv", index=False, encoding="utf-8-sig")

    # 给手机看的精简可实操表
    simple_cols = [
        "股票代码", "股票名称", "当前价",
        "当前最重要压力带", "当前最重要突破确认线", "当前最重要压力状态", "当前最重要压力来源",
        "突破K等级", "日线收盘站上确认线", "实体站上线比例", "收盘位置", "上影线比例",
        "昨比量", "20日量比", "第一目标", "第二目标", "交易防守位", "第一目标收益风险比",
        "是否可实操", "可实操原因", "下一步动作", "放弃条件",
    ]
    simple_cols = [c for c in simple_cols if c in outputs["actionable"].columns]
    outputs["actionable"][simple_cols].to_csv(out_dir / "pressure_band_actionable_精简版.csv", index=False, encoding="utf-8-sig")


# ============================================================
# 11. 命令行入口
# ============================================================

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="核心压力带被日线高级K突破后的可实操股票筛选。")
    p.add_argument("--symbols", type=str, default=None, help="股票代码，逗号分隔，例如 SZ.000001,SH.600519")
    p.add_argument("--symbols-file", type=str, default=None, help="股票池文件，每行一个代码，可附名称")
    p.add_argument("--all", action="store_true", help="通过 AkShare 获取全A股票池")
    p.add_argument("--limit", type=int, default=0, help="限制扫描数量；0=不限制")
    p.add_argument("--cache-dir", type=str, default="kline_cache", help="K线缓存目录，建议 kline_cache")
    p.add_argument("--out-dir", type=str, default="output", help="输出目录")
    p.add_argument("--start-date", type=str, default="20160101", help="开始日期，格式YYYYMMDD")
    p.add_argument("--end-date", type=str, default=pd.Timestamp.today().strftime("%Y%m%d"), help="结束日期，格式YYYYMMDD")
    p.add_argument("--refresh", action="store_true", help="强制刷新缓存")
    p.add_argument("--min-rr", type=float, default=None, help="可实操最低收益风险比")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    cfg = Config()
    if args.min_rr is not None:
        cfg.min_rr_actionable = float(args.min_rr)

    cache_dir = ensure_dir(args.cache_dir)
    out_dir = ensure_dir(args.out_dir)

    symbol_df = parse_symbols_arg(args.symbols, args.symbols_file, args.all, args.limit)
    print(f"[INFO] symbols={len(symbol_df)} cache_dir={cache_dir}")
    print("[INFO] 目标：只筛选“核心固定压力带被日线高级K有效突破后”的可实操股票。")

    df, failed = scan_symbols(
        symbol_df=symbol_df,
        cfg=cfg,
        cache_dir=cache_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        refresh=args.refresh,
    )
    save_outputs(df, failed, out_dir)

    print("[DONE] 输出文件：")
    for fn in [
        "pressure_band_actionable.csv",
        "pressure_band_actionable_精简版.csv",
        "pressure_band_waiting_breakout.csv",
        "pressure_band_failed_breakout.csv",
        "pressure_band_all_validation.csv",
        "pressure_band_failed.csv",
    ]:
        fp = out_dir / fn
        rows = 0
        if fp.exists() and fp.stat().st_size > 0:
            try:
                rows = max(0, len(pd.read_csv(fp)) )
            except Exception:
                rows = -1
        print(f"  {fp} rows={rows}")

    actionable_path = out_dir / "pressure_band_actionable_精简版.csv"
    if actionable_path.exists() and actionable_path.stat().st_size > 0:
        ac = pd.read_csv(actionable_path)
        if not ac.empty:
            print("[PREVIEW] 可实操候选前30：")
            print(ac.head(cfg.print_top).to_string(index=False))
        else:
            print("[PREVIEW] 当前没有满足严格标准的可实操突破候选。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
