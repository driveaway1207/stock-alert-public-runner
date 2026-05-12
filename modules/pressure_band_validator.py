#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
pressure_band_validator.py

独立压力带测试模块：
- 只用于验证“压力带识别是否精准”
- 不直接给买卖建议
- 支持缓存 K 线
- 支持固定股票池 --symbols
- 支持自定义股票池 --symbols-file
- 支持全市场 --all，但 AkShare 股票列表偶尔会失败，所以优先建议先用 --symbols 测试
- 已修复新版 pandas 的 M/Q 频率问题：月线用 ME，季线用 QE
"""

from __future__ import annotations

import argparse
import math
import os
import re
import time
import warnings
from dataclasses import dataclass, asdict
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
    daily_bars: int = 520
    weekly_bars: int = 180
    monthly_bars: int = 120
    quarterly_bars: int = 60

    base_bucket_pct: float = 0.008
    min_bucket_pct: float = 0.004
    max_bucket_pct: float = 0.018
    atr_bucket_multiplier: float = 0.35

    profile_quantile: float = 0.80
    profile_min_cover_bars: int = 3
    recency_half_life: int = 120

    anchor_width_pct: float = 0.012
    same_period_merge_pct: float = 0.025
    cross_period_merge_pct: float = 0.035

    max_above_current_pct: float = 0.40
    max_below_current_pct: float = 0.08

    approach_pct: float = 0.035
    broke_buffer_pct: float = 0.003

    strong_close_pos: float = 0.80
    min_body_above_final_ratio: float = 0.50

    healthy_vol_ratio_min: float = 1.20
    healthy_vol_ratio_max: float = 3.50

    standard_double_vol_min: float = 1.80
    standard_double_vol_max: float = 2.50

    min_composite_quality: float = 30.0
    top_composites_per_symbol: int = 3
    only_interesting: bool = True


@dataclass
class Band:
    period: str
    lower: float
    upper: float
    source: str
    quality: float
    detail: str = ""


@dataclass
class CompositeBand:
    lower: float
    upper: float
    core_lower: float
    core_upper: float
    final_upper: float
    periods: List[str]
    sources: List[str]
    quality: float
    bands: List[Band]


PERIOD_ORDER = {"D": 1, "W": 2, "M": 3, "Q": 4}


# ============================================================
# 2. 基础工具
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


def pct(a: float, b: float) -> float:
    if b == 0 or pd.isna(a) or pd.isna(b):
        return np.nan
    return (a / b - 1) * 100


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


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

    return d


def calc_atr_pct(df: pd.DataFrame, n: int = 20) -> float:
    if len(df) < 5:
        return 0.02

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

    atr = tr.rolling(n, min_periods=5).mean().iloc[-1]
    close = d["close"].iloc[-1]

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


def fetch_daily_akshare(
    symbol: str,
    start_date: str,
    end_date: str,
    retries: int = 3,
    sleep_sec: float = 0.8,
) -> pd.DataFrame:
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

    raise RuntimeError(
        f"source=akshare stage=fetch_kline symbol={symbol} retry={retries} err={last_err}"
    )


def load_daily(
    symbol: str,
    cache_dir: Path,
    start_date: str,
    end_date: str,
    refresh: bool = False,
) -> pd.DataFrame:
    ns = normalize_symbol(symbol)
    ensure_dir(cache_dir)

    possible_names = [
        f"{ns.replace('.', '_')}_daily_qfq.csv",
        f"{ns.replace('.', '_')}.csv",
        f"{ak_symbol(ns)}.csv",
        f"{ak_symbol(ns)}_daily.csv",
        f"{ak_symbol(ns)}_daily_qfq.csv",
    ]

    if not refresh:
        for name in possible_names:
            cache_file = cache_dir / name
            if cache_file.exists():
                try:
                    d = pd.read_csv(cache_file)
                    d = normalize_ohlcv(d)
                    if not d.empty:
                        return d
                except Exception:
                    pass

    d = fetch_daily_akshare(ns, start_date=start_date, end_date=end_date)

    if not d.empty:
        save_file = cache_dir / f"{ns.replace('.', '_')}_daily_qfq.csv"
        d.to_csv(save_file, index=False, encoding="utf-8-sig")

    return d


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    d = df.copy()
    d = d.set_index("date")

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

    # pandas 新版不再推荐 M/Q，用 ME/QE
    m = resample_ohlcv(daily, "ME").tail(cfg.monthly_bars)
    q = resample_ohlcv(daily, "QE").tail(cfg.quarterly_bars)

    return {"D": d, "W": w, "M": m, "Q": q}


# ============================================================
# 4. 单周期压力带识别
# ============================================================

def volume_profile_bands(
    df: pd.DataFrame,
    period: str,
    current_price: float,
    cfg: Config,
) -> List[Band]:
    if len(df) < 30:
        return []

    d = candle_features(df).reset_index(drop=True)
    bucket_pct = adaptive_bucket_pct(d, cfg)

    bucket_stats: Dict[int, Dict[str, float]] = {}
    n = len(d)

    for idx, row in d.iterrows():
        low = float(row["low"])
        high = float(row["high"])

        if low <= 0 or high <= 0 or high < low:
            continue

        b0 = log_bucket_id(low, bucket_pct)
        b1 = log_bucket_id(high, bucket_pct)

        if b1 < b0:
            b0, b1 = b1, b0

        bucket_ids = list(range(b0, b1 + 1))
        if not bucket_ids:
            continue

        age = n - 1 - idx
        recency_w = 0.5 ** (age / max(1, cfg.recency_half_life))

        vol = float(row["volume"])
        amount = float(row.get("amount", row["close"] * row["volume"]))

        per_vol = vol / len(bucket_ids)
        per_amount = amount / len(bucket_ids)

        close_bid = log_bucket_id(float(row["close"]), bucket_pct)
        body_low = min(float(row["open"]), float(row["close"]))
        body_high = max(float(row["open"]), float(row["close"]))
        body_b0 = log_bucket_id(body_low, bucket_pct)
        body_b1 = log_bucket_id(body_high, bucket_pct)

        for bid in bucket_ids:
            st = bucket_stats.setdefault(
                bid,
                {
                    "volume": 0.0,
                    "amount": 0.0,
                    "w_volume": 0.0,
                    "cover_bars": 0.0,
                    "close_count": 0.0,
                    "body_count": 0.0,
                },
            )

            st["volume"] += per_vol
            st["amount"] += per_amount
            st["w_volume"] += per_vol * recency_w
            st["cover_bars"] += 1

            if bid == close_bid:
                st["close_count"] += 1

            if body_b0 <= bid <= body_b1:
                st["body_count"] += 1

    if not bucket_stats:
        return []

    rows = []
    for bid, st in bucket_stats.items():
        lower, upper = bucket_bounds(bid, bucket_pct)
        rows.append(
            {
                "bid": bid,
                "lower": lower,
                "upper": upper,
                "mid": (lower + upper) / 2,
                **st,
            }
        )

    p = pd.DataFrame(rows).sort_values("bid")

    p = p[
        (p["upper"] >= current_price * (1 - cfg.max_below_current_pct))
        & (p["lower"] <= current_price * (1 + cfg.max_above_current_pct))
    ].copy()

    if p.empty:
        return []

    threshold = max(
        p["w_volume"].quantile(cfg.profile_quantile),
        p["w_volume"].mean() + 0.20 * p["w_volume"].std(),
    )

    hvn = p[
        (p["w_volume"] >= threshold)
        & (p["cover_bars"] >= cfg.profile_min_cover_bars)
    ].copy()

    if hvn.empty:
        return []

    hvn = hvn.sort_values("bid")

    clusters = []
    cur = []
    last_bid = None

    for _, r in hvn.iterrows():
        bid = int(r["bid"])

        if last_bid is None or bid - last_bid <= 2:
            cur.append(r)
        else:
            if cur:
                clusters.append(pd.DataFrame(cur))
            cur = [r]

        last_bid = bid

    if cur:
        clusters.append(pd.DataFrame(cur))

    bands = []
    total_wv = max(1.0, p["w_volume"].sum())
    max_wv = max(1.0, p["w_volume"].max())

    for c in clusters:
        lower = float(c["lower"].min())
        upper = float(c["upper"].max())

        if upper < current_price * (1 - cfg.max_below_current_pct):
            continue

        cluster_wv = float(c["w_volume"].sum())
        volume_share = cluster_wv / total_wv
        peak_ratio = float(c["w_volume"].max() / max_wv)
        cover = float(c["cover_bars"].sum())
        close_count = float(c["close_count"].sum())
        body_count = float(c["body_count"].sum())

        width = (upper - lower) / max((upper + lower) / 2, 1e-9)

        quality = (
            22
            + 35 * volume_share
            + 18 * peak_ratio
            + 0.20 * cover
            + 0.35 * close_count
            + 0.25 * body_count
        )

        if width > 0.12:
            quality -= 8
        elif 0.018 <= width <= 0.09:
            quality += 5

        quality = clamp(float(quality), 10, 85)

        detail = (
            f"VolumeProfile高成交密集区; bucket={bucket_pct:.3%}; "
            f"share={volume_share:.2%}; cover={cover:.0f}; width={width:.2%}"
        )

        bands.append(
            Band(
                period=period,
                lower=lower,
                upper=upper,
                source="volume_profile_hvn",
                quality=quality,
                detail=detail,
            )
        )

    return bands


def make_anchor_band(
    period: str,
    price: float,
    source: str,
    quality: float,
    cfg: Config,
    detail: str,
) -> Band:
    w = cfg.anchor_width_pct
    return Band(
        period=period,
        lower=float(price * (1 - w)),
        upper=float(price * (1 + w)),
        source=source,
        quality=float(quality),
        detail=detail,
    )


def max_bull_volume_bands(
    df: pd.DataFrame,
    period: str,
    current_price: float,
    cfg: Config,
) -> List[Band]:
    if len(df) < 20:
        return []

    d = candle_features(df).copy()

    d["valid"] = (
        (d["close"] > d["open"])
        & (d["body_ratio"].fillna(0) >= 0.30)
        & (d["upper_wick_ratio"].fillna(0) <= 0.60)
    )

    cand = d[d["valid"]].sort_values("volume", ascending=False).head(3)

    bands = []

    for _, r in cand.iterrows():
        high = float(r["high"])

        if high < current_price * (1 - cfg.max_below_current_pct):
            continue

        if high > current_price * (1 + cfg.max_above_current_pct):
            continue

        vol_rank = float((d["volume"] <= r["volume"]).mean())
        body_ratio = float(r.get("body_ratio", 0) or 0)
        close_pos = float(r.get("close_pos", 0) or 0)

        quality = 28 + 30 * vol_rank + 18 * body_ratio + 10 * close_pos
        quality = clamp(quality, 20, 90)

        detail = (
            f"最大量阳K高点; date={r['date'].date()}; "
            f"high={high:.3f}; vol_rank={vol_rank:.2%}; "
            f"body_ratio={body_ratio:.2f}; close_pos={close_pos:.2f}"
        )

        bands.append(
            make_anchor_band(
                period,
                high,
                "max_bull_volume_high",
                quality,
                cfg,
                detail,
            )
        )

    return bands


def swing_high_bands(
    df: pd.DataFrame,
    period: str,
    current_price: float,
    cfg: Config,
) -> List[Band]:
    if len(df) < 30:
        return []

    d = candle_features(df).copy().reset_index(drop=True)
    highs = d["high"]

    is_swing = highs == highs.rolling(7, center=True, min_periods=3).max()
    cand = d[is_swing.fillna(False)].copy()

    if cand.empty:
        cand = d.nlargest(8, "high").copy()

    cand = cand.sort_values(["high", "volume"], ascending=False).head(8)

    bands = []

    for _, r in cand.iterrows():
        high = float(r["high"])

        if high < current_price * (1 - cfg.max_below_current_pct):
            continue

        if high > current_price * (1 + cfg.max_above_current_pct):
            continue

        high_percentile = float((d["high"] <= high).mean())
        vol_ratio = float(r.get("vol_ratio20", 1) if not pd.isna(r.get("vol_ratio20", np.nan)) else 1)
        upper_wick = float(r.get("upper_wick_ratio", 0) or 0)
        close_pos = float(r.get("close_pos", 0) or 0)

        quality = (
            20
            + 20 * high_percentile
            + 16 * min(vol_ratio, 3) / 3
            + 18 * upper_wick
            + 8 * (1 - close_pos)
        )

        quality = clamp(quality, 15, 78)

        detail = (
            f"阶段/局部高点; date={r['date'].date()}; "
            f"high={high:.3f}; high_percentile={high_percentile:.2%}; "
            f"vol_ratio20={vol_ratio:.2f}"
        )

        bands.append(
            make_anchor_band(
                period,
                high,
                "swing_high",
                quality,
                cfg,
                detail,
            )
        )

    return bands


def upper_wick_resonance_bands(
    df: pd.DataFrame,
    period: str,
    current_price: float,
    cfg: Config,
) -> List[Band]:
    if len(df) < 40:
        return []

    d = candle_features(df).copy().reset_index(drop=True)

    cand = d[
        (d["upper_wick_ratio"].fillna(0) >= 0.42)
        & (d["close_pos"].fillna(1) <= 0.62)
        & (d["vol_ratio20"].fillna(1) >= 1.15)
    ].copy()

    if cand.empty:
        return []

    bucket_pct = adaptive_bucket_pct(d, cfg) * 1.5
    cand["bid"] = cand["high"].apply(lambda x: log_bucket_id(float(x), bucket_pct))

    bands = []

    for bid, g in cand.groupby("bid"):
        if len(g) < 2:
            continue

        lower, upper = bucket_bounds(int(bid), bucket_pct)

        if upper < current_price * (1 - cfg.max_below_current_pct):
            continue

        if lower > current_price * (1 + cfg.max_above_current_pct):
            continue

        touch = len(g)
        avg_wick = float(g["upper_wick_ratio"].mean())
        avg_vol_ratio = float(
            g["vol_ratio20"].replace([np.inf, -np.inf], np.nan).fillna(1).mean()
        )
        avg_reject = float(
            (g["high"] / g["close"] - 1)
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0)
            .mean()
        )

        quality = (
            24
            + 9 * min(touch, 5)
            + 22 * avg_wick
            + 8 * min(avg_vol_ratio, 3)
            + 120 * min(avg_reject, 0.08)
        )

        quality = clamp(quality, 20, 90)

        detail = (
            f"上影线共振; touch={touch}; avg_wick={avg_wick:.2f}; "
            f"avg_vol_ratio20={avg_vol_ratio:.2f}; avg_reject={avg_reject:.2%}"
        )

        bands.append(
            Band(
                period=period,
                lower=float(lower),
                upper=float(upper),
                source="upper_wick_resonance",
                quality=quality,
                detail=detail,
            )
        )

    return bands


def gap_pressure_bands(
    df: pd.DataFrame,
    period: str,
    current_price: float,
    cfg: Config,
) -> List[Band]:
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

    gaps = gaps.sort_values("date", ascending=False).head(5)

    bands = []

    for _, r in gaps.iterrows():
        lower = float(r["gap_lower"])
        upper = float(r["gap_upper"])

        if upper < current_price * (1 - cfg.max_below_current_pct):
            continue

        if lower > current_price * (1 + cfg.max_above_current_pct):
            continue

        gap_pct = float(r["gap_pct"])
        quality = 22 + 500 * min(gap_pct, 0.08)
        quality = clamp(quality, 15, 70)

        detail = (
            f"向下跳空缺口压力区; date={r['date'].date()}; "
            f"gap={lower:.3f}-{upper:.3f}; gap_pct={gap_pct:.2%}"
        )

        bands.append(
            Band(
                period=period,
                lower=lower,
                upper=upper,
                source="down_gap_pressure",
                quality=quality,
                detail=detail,
            )
        )

    return bands


def false_break_memory_bands(
    df: pd.DataFrame,
    period: str,
    current_price: float,
    cfg: Config,
) -> List[Band]:
    if len(df) < 50:
        return []

    d = candle_features(df).tail(180).copy().reset_index(drop=True)

    d["rolling_high_prev"] = d["high"].shift(1).rolling(20, min_periods=8).max()

    cand = d[
        (d["high"] > d["rolling_high_prev"] * 1.003)
        & (d["close"] < d["rolling_high_prev"] * 1.003)
        & (d["upper_wick_ratio"].fillna(0) >= 0.32)
        & (d["close_pos"].fillna(1) <= 0.65)
    ].copy()

    if cand.empty:
        return []

    bucket_pct = adaptive_bucket_pct(d, cfg) * 1.5
    cand["bid"] = cand["high"].apply(lambda x: log_bucket_id(float(x), bucket_pct))

    bands = []

    for bid, g in cand.groupby("bid"):
        lower, upper = bucket_bounds(int(bid), bucket_pct)

        if upper < current_price * (1 - cfg.max_below_current_pct):
            continue

        if lower > current_price * (1 + cfg.max_above_current_pct):
            continue

        touch = len(g)
        best_high = float(g["high"].max())
        avg_wick = float(g["upper_wick_ratio"].mean())
        avg_vol_ratio = float(
            g["vol_ratio20"].replace([np.inf, -np.inf], np.nan).fillna(1).mean()
        )

        quality = 28 + 10 * min(touch, 4) + 18 * avg_wick + 7 * min(avg_vol_ratio, 3)
        quality = clamp(quality, 20, 88)

        detail = (
            f"假突破记忆/Liquidity Sweep; touch={touch}; "
            f"best_high={best_high:.3f}; avg_wick={avg_wick:.2f}; "
            f"avg_vol_ratio20={avg_vol_ratio:.2f}"
        )

        bands.append(
            Band(
                period=period,
                lower=float(lower),
                upper=float(upper),
                source="false_break_memory",
                quality=quality,
                detail=detail,
            )
        )

    return bands


def overlap_or_near(a: Band, b: Band, merge_pct: float) -> bool:
    if max(a.lower, b.lower) <= min(a.upper, b.upper):
        return True

    gap = max(a.lower, b.lower) - min(a.upper, b.upper)
    mid = (a.lower + a.upper + b.lower + b.upper) / 4

    return gap / max(mid, 1e-9) <= merge_pct


def merge_bands_same_period(bands: List[Band], cfg: Config) -> List[Band]:
    if not bands:
        return []

    bands = sorted(bands, key=lambda x: (x.lower, x.upper))
    groups: List[List[Band]] = []

    for b in bands:
        placed = False

        for g in groups:
            if any(overlap_or_near(b, gb, cfg.same_period_merge_pct) for gb in g):
                g.append(b)
                placed = True
                break

        if not placed:
            groups.append([b])

    merged = []

    for g in groups:
        lower = min(x.lower for x in g)
        upper = max(x.upper for x in g)
        sources = sorted(set(x.source for x in g))
        quality = sum(x.quality for x in g)
        quality = clamp(quality * (0.72 + 0.08 * min(len(sources), 4)), 10, 100)

        detail = " | ".join([x.detail for x in sorted(g, key=lambda z: -z.quality)[:4]])

        merged.append(
            Band(
                period=g[0].period,
                lower=float(lower),
                upper=float(upper),
                source="+".join(sources),
                quality=float(quality),
                detail=detail,
            )
        )

    return sorted(merged, key=lambda x: x.quality, reverse=True)


def generate_period_bands(
    df: pd.DataFrame,
    period: str,
    current_price: float,
    cfg: Config,
) -> List[Band]:
    if df is None or df.empty or len(df) < 20:
        return []

    methods = [
        volume_profile_bands,
        max_bull_volume_bands,
        swing_high_bands,
        upper_wick_resonance_bands,
        gap_pressure_bands,
        false_break_memory_bands,
    ]

    bands: List[Band] = []

    for fn in methods:
        try:
            bands.extend(fn(df, period, current_price, cfg))
        except Exception:
            continue

    bands = [b for b in bands if b.upper > b.lower and b.upper > 0]
    return merge_bands_same_period(bands, cfg)


# ============================================================
# 5. 多周期复合压力带
# ============================================================

def intervals_core_overlap(bands: List[Band]) -> Tuple[float, float]:
    if not bands:
        return np.nan, np.nan

    inter_l = max(b.lower for b in bands)
    inter_u = min(b.upper for b in bands)

    if inter_l <= inter_u:
        return float(inter_l), float(inter_u)

    points = sorted(set([b.lower for b in bands] + [b.upper for b in bands]))

    best = None

    for i in range(len(points) - 1):
        l, u = points[i], points[i + 1]

        if u <= l:
            continue

        active = [b for b in bands if b.lower <= l and b.upper >= u]

        if not active:
            continue

        period_count = len(set(b.period for b in active))
        q = sum(b.quality for b in active)
        score = period_count * 1000 + q

        if best is None or score > best[0]:
            best = (score, l, u)

    if best is None:
        b = max(bands, key=lambda x: x.quality)
        return b.lower, b.upper

    return float(best[1]), float(best[2])


def merge_cross_period_bands(
    period_bands: Dict[str, List[Band]],
    current_price: float,
    cfg: Config,
) -> List[CompositeBand]:
    all_bands = []

    for p, bs in period_bands.items():
        filtered = []

        for b in bs:
            if b.upper < current_price * (1 - cfg.max_below_current_pct):
                continue

            if b.lower > current_price * (1 + cfg.max_above_current_pct):
                continue

            filtered.append(b)

        all_bands.extend(sorted(filtered, key=lambda x: x.quality, reverse=True)[:8])

    if not all_bands:
        return []

    groups: List[List[Band]] = []

    for b in sorted(all_bands, key=lambda x: x.lower):
        placed = False

        for g in groups:
            if any(overlap_or_near(b, gb, cfg.cross_period_merge_pct) for gb in g):
                g.append(b)
                placed = True
                break

        if not placed:
            groups.append([b])

    composites = []

    for g in groups:
        lower = min(b.lower for b in g)
        upper = max(b.upper for b in g)
        core_l, core_u = intervals_core_overlap(g)

        periods = sorted(set(b.period for b in g), key=lambda x: PERIOD_ORDER.get(x, 99))
        sources = sorted(set(s for b in g for s in b.source.split("+")))

        period_bonus = {1: 0, 2: 14, 3: 28, 4: 42}.get(len(periods), 0)

        big_period_bonus = 0
        if "M" in periods:
            big_period_bonus += 8
        if "Q" in periods:
            big_period_bonus += 10

        source_bonus = min(len(sources), 8) * 2.2

        raw_q = sum(b.quality for b in g) * 0.38 + period_bonus + big_period_bonus + source_bonus

        width_pct = (upper - lower) / max((upper + lower) / 2, 1e-9)

        if width_pct > 0.18:
            raw_q -= 18
        elif width_pct > 0.12:
            raw_q -= 8
        elif 0.018 <= width_pct <= 0.09:
            raw_q += 5

        if lower <= current_price <= upper:
            raw_q += 8
        elif current_price < lower:
            dist = lower / current_price - 1
            if dist <= cfg.approach_pct:
                raw_q += 6
            elif dist > 0.18:
                raw_q -= 8
        else:
            if current_price / upper - 1 <= 0.08:
                raw_q += 4
            else:
                raw_q -= 10

        quality = clamp(float(raw_q), 0, 100)

        composites.append(
            CompositeBand(
                lower=float(lower),
                upper=float(upper),
                core_lower=float(core_l),
                core_upper=float(core_u),
                final_upper=float(upper),
                periods=periods,
                sources=sources,
                quality=quality,
                bands=sorted(g, key=lambda x: (PERIOD_ORDER.get(x.period, 99), -x.quality)),
            )
        )

    composites = sorted(
        composites,
        key=lambda x: (x.quality, len(x.periods)),
        reverse=True,
    )

    return composites


# ============================================================
# 6. 状态判断
# ============================================================

def latest_breakout_metrics(
    daily: pd.DataFrame,
    comp: CompositeBand,
    cfg: Config,
) -> Dict:
    d = candle_features(daily).copy()

    if d.empty:
        return {}

    r = d.iloc[-1]

    final_upper = comp.final_upper
    core_upper = comp.core_upper

    open_ = float(r["open"])
    close = float(r["close"])
    high = float(r["high"])
    low = float(r["low"])

    body_low = min(open_, close)
    body_high = max(open_, close)
    body_len = max(body_high - body_low, 1e-9)

    body_above_final = max(0.0, body_high - max(body_low, final_upper)) / body_len
    body_above_core = max(0.0, body_high - max(body_low, core_upper)) / body_len

    close_pos = float(r.get("close_pos", np.nan))
    vol_ratio_prev = float(r.get("vol_ratio_prev", np.nan))
    vol_ratio20 = float(r.get("vol_ratio20", np.nan))

    vol_ref = max(
        vol_ratio_prev if not pd.isna(vol_ratio_prev) else 0,
        vol_ratio20 if not pd.isna(vol_ratio20) else 0,
    )

    is_standard_double = cfg.standard_double_vol_min <= vol_ratio_prev <= cfg.standard_double_vol_max
    is_healthy_volume = cfg.healthy_vol_ratio_min <= vol_ref <= cfg.healthy_vol_ratio_max

    broke_core_close = close > core_upper * (1 + cfg.broke_buffer_pct)
    broke_final_close = close > final_upper * (1 + cfg.broke_buffer_pct)

    intraday_swept_final = (
        high > final_upper * (1 + cfg.broke_buffer_pct)
        and close <= final_upper * (1 + cfg.broke_buffer_pct)
    )

    strong_break_final = (
        broke_final_close
        and body_above_final >= cfg.min_body_above_final_ratio
        and close_pos >= cfg.strong_close_pos
        and (is_standard_double or is_healthy_volume or vol_ratio20 >= cfg.healthy_vol_ratio_min)
    )

    return {
        "latest_date": str(pd.to_datetime(r["date"]).date()),
        "latest_open": open_,
        "latest_high": high,
        "latest_low": low,
        "latest_close": close,
        "latest_volume": float(r["volume"]),
        "latest_close_pos": close_pos,
        "latest_body_above_core_ratio": body_above_core,
        "latest_body_above_final_ratio": body_above_final,
        "latest_vol_ratio_prev": vol_ratio_prev,
        "latest_vol_ratio20": vol_ratio20,
        "is_standard_double_volume": bool(is_standard_double),
        "is_healthy_volume": bool(is_healthy_volume),
        "broke_core_close": bool(broke_core_close),
        "broke_final_close": bool(broke_final_close),
        "intraday_swept_final_failed": bool(intraday_swept_final),
        "strong_break_final": bool(strong_break_final),
    }


def pressure_digestion_metrics(
    daily: pd.DataFrame,
    comp: CompositeBand,
    cfg: Config,
) -> Dict:
    d = candle_features(daily).copy().tail(30)

    if d.empty:
        return {}

    lower = comp.lower
    upper = comp.final_upper

    inside = d[
        (d["close"] >= lower * 0.995)
        & (d["close"] <= upper * 1.005)
    ].copy()

    last_n = d.tail(12)

    in_last = last_n[
        (last_n["close"] >= lower * 0.995)
        & (last_n["close"] <= upper * 1.005)
    ]

    low_break = (last_n["close"] < lower * (1 - 0.035)).any()

    small_body_ratio = float((last_n["body_ratio"].fillna(1) <= 0.45).mean())

    rising_lows = False
    if len(last_n) >= 6:
        lows = last_n["low"].values
        rising_lows = bool(np.nanmedian(lows[-3:]) >= np.nanmedian(lows[:3]) * 0.995)

    vol_cv = np.nan
    if len(last_n) >= 5 and last_n["volume"].mean() > 0:
        vol_cv = float(last_n["volume"].std() / last_n["volume"].mean())

    vol_stable = bool(not pd.isna(vol_cv) and vol_cv <= 0.45)

    no_heavy_bear = bool(
        (
            (last_n["is_bear"])
            & (last_n["vol_ratio20"].fillna(0) >= 1.8)
            & (last_n["body_ratio"].fillna(0) >= 0.55)
        ).sum() == 0
    )

    score = 0

    if len(in_last) >= 5:
        score += 25
    if not low_break:
        score += 20
    if small_body_ratio >= 0.55:
        score += 15
    if rising_lows:
        score += 15
    if vol_stable:
        score += 15
    if no_heavy_bear:
        score += 10

    return {
        "digestion_bars_30": int(len(inside)),
        "digestion_bars_12": int(len(in_last)),
        "digestion_low_break": bool(low_break),
        "digestion_small_body_ratio_12": small_body_ratio,
        "digestion_rising_lows": bool(rising_lows),
        "digestion_volume_cv_12": vol_cv,
        "digestion_volume_stable": bool(vol_stable),
        "digestion_no_heavy_bear": bool(no_heavy_bear),
        "digestion_score": float(clamp(score, 0, 100)),
    }


def classify_state(
    current_price: float,
    comp: CompositeBand,
    daily: pd.DataFrame,
    cfg: Config,
) -> Tuple[str, str, Dict]:
    lower = comp.lower
    core_u = comp.core_upper
    final_u = comp.final_upper

    metrics = latest_breakout_metrics(daily, comp, cfg)
    digestion = pressure_digestion_metrics(daily, comp, cfg)
    m = {**metrics, **digestion}

    if metrics.get("intraday_swept_final_failed"):
        return "假突破失败/冲高回落", "D", m

    if current_price > final_u * (1 + cfg.broke_buffer_pct):
        if metrics.get("strong_break_final"):
            return "一根日K打穿最终压力上沿", "S_TEST", m

        if (
            metrics.get("latest_body_above_final_ratio", 0) >= 0.25
            and metrics.get("latest_close_pos", 0) >= 0.70
        ):
            return "突破最终压力上沿但质量待确认", "A_TEST", m

        return "站上最终压力上沿但突破质量一般", "B_PLUS", m

    if current_price > core_u * (1 + cfg.broke_buffer_pct):
        return "突破核心重叠压力带但未打穿最终上沿", "A_OBSERVE", m

    if lower <= current_price <= final_u:
        if digestion.get("digestion_score", 0) >= 70:
            return "压力带内消化较充分", "A_DIGEST", m

        return "进入复合压力带内部", "B", m

    if current_price < lower:
        dist_to_lower = lower / current_price - 1

        if dist_to_lower <= cfg.approach_pct:
            return "靠近复合压力带下沿", "C_PLUS", m

        return "压力带在上方但距离较远", "C", m

    return "状态待人工复核", "REVIEW", m


# ============================================================
# 7. 单股票分析
# ============================================================

def analyze_symbol(
    symbol: str,
    name: str,
    daily: pd.DataFrame,
    cfg: Config,
) -> List[Dict]:
    ns = normalize_symbol(symbol)

    if daily is None or daily.empty or len(daily) < 120:
        return []

    current_price = float(daily["close"].iloc[-1])
    frames = build_period_frames(daily, cfg)

    period_bands: Dict[str, List[Band]] = {}

    for p, f in frames.items():
        period_bands[p] = generate_period_bands(f, p, current_price, cfg)

    composites = merge_cross_period_bands(period_bands, current_price, cfg)

    rows = []

    for rank, comp in enumerate(composites[: cfg.top_composites_per_symbol], start=1):
        if comp.quality < cfg.min_composite_quality:
            continue

        state, verify_grade, metrics = classify_state(current_price, comp, frames["D"], cfg)

        if cfg.only_interesting:
            interesting_states = [
                "靠近复合压力带下沿",
                "进入复合压力带内部",
                "压力带内消化较充分",
                "突破核心重叠压力带但未打穿最终上沿",
                "突破最终压力上沿但质量待确认",
                "一根日K打穿最终压力上沿",
                "假突破失败/冲高回落",
                "站上最终压力上沿但突破质量一般",
            ]

            if state not in interesting_states:
                continue

        source_summary = ";".join(comp.sources)
        period_summary = "/".join(comp.periods)

        component_brief = []
        for b in comp.bands[:8]:
            component_brief.append(
                f"{b.period}:{b.lower:.2f}-{b.upper:.2f}:{b.source}:q{b.quality:.0f}"
            )

        row = {
            "symbol": ns,
            "name": name,
            "current_price": round(current_price, 4),
            "composite_rank": rank,
            "core_lower": round(comp.core_lower, 4),
            "core_upper": round(comp.core_upper, 4),
            "union_lower": round(comp.lower, 4),
            "final_upper": round(comp.final_upper, 4),
            "dist_to_union_lower_pct": round(pct(current_price, comp.lower), 2),
            "dist_to_final_upper_pct": round(pct(current_price, comp.final_upper), 2),
            "current_state": state,
            "verify_grade": verify_grade,
            "composite_quality": round(comp.quality, 2),
            "period_count": len(comp.periods),
            "periods": period_summary,
            "sources": source_summary,
            "has_volume_profile": any("volume_profile" in s for s in comp.sources),
            "has_max_bull_volume_high": any("max_bull_volume" in s for s in comp.sources),
            "has_upper_wick_resonance": any("upper_wick" in s for s in comp.sources),
            "has_gap_pressure": any("gap" in s for s in comp.sources),
            "has_false_break_memory": any("false_break" in s for s in comp.sources),
            "component_brief": " | ".join(component_brief),
        }

        row.update(
            {
                k: round(v, 4) if isinstance(v, float) and not pd.isna(v) else v
                for k, v in metrics.items()
            }
        )

        rows.append(row)

    return rows


# ============================================================
# 8. 股票池
# ============================================================

def get_a_stock_list() -> pd.DataFrame:
    if ak is None:
        raise RuntimeError("未安装 akshare，请改用 --symbols-file 输入自定义股票池。")

    try:
        df = ak.stock_info_a_code_name()
    except Exception as e:
        raise RuntimeError(
            f"无法通过 AkShare 获取A股股票列表，请改用 --symbols-file 输入自定义股票池。err={e}"
        )

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


def parse_symbols_arg(
    symbols: Optional[str],
    symbols_file: Optional[str],
    use_all: bool,
    limit: Optional[int],
) -> pd.DataFrame:
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

    if limit:
        df = df.head(limit)

    return df.reset_index(drop=True)


# ============================================================
# 9. 批量扫描
# ============================================================

def scan_symbols(
    symbol_df: pd.DataFrame,
    cfg: Config,
    cache_dir: Path,
    start_date: str,
    end_date: str,
    refresh: bool = False,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    failed = []

    iterator = symbol_df.iterrows()

    if tqdm is not None:
        iterator = tqdm(symbol_df.iterrows(), total=len(symbol_df), desc="pressure-band-scan")

    for _, item in iterator:
        symbol = item["symbol"]
        name = item.get("name", "")

        try:
            daily = load_daily(
                symbol,
                cache_dir=cache_dir,
                start_date=start_date,
                end_date=end_date,
                refresh=refresh,
            )

            if daily.empty or len(daily) < 120:
                failed.append(
                    {
                        "symbol": symbol,
                        "name": name,
                        "reason": "empty_or_too_few_bars",
                    }
                )
                continue

            result = analyze_symbol(symbol, name, daily, cfg)
            rows.extend(result)

        except Exception as e:
            failed.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "reason": str(e)[:800],
                }
            )
            continue

    out = pd.DataFrame(rows)
    fail = pd.DataFrame(failed)

    if not out.empty:
        grade_order = {
            "S_TEST": 1,
            "A_TEST": 2,
            "A_DIGEST": 3,
            "A_OBSERVE": 4,
            "B_PLUS": 5,
            "B": 6,
            "C_PLUS": 7,
            "D": 8,
            "C": 9,
            "REVIEW": 10,
        }

        out["grade_order"] = out["verify_grade"].map(grade_order).fillna(99)
        out = out.sort_values(
            ["grade_order", "composite_quality", "period_count"],
            ascending=[True, False, False],
        )

    return out, fail


# ============================================================
# 10. 命令行入口
# ============================================================

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="独立压力带测试模块：验证多周期复合压力带识别是否准确。")

    p.add_argument("--symbols", type=str, default=None, help="股票代码，逗号分隔，例如 SZ.000001,SH.600519")
    p.add_argument("--symbols-file", type=str, default=None, help="股票池文件，每行一个代码，可附名称")
    p.add_argument("--all", action="store_true", help="扫描全A股票池，自动过滤ST/退市名称")
    p.add_argument("--limit", type=int, default=None, help="限制扫描数量，用于测试")

    p.add_argument("--cache-dir", type=str, default="data/cache/kline", help="K线缓存目录")
    p.add_argument("--out", type=str, default="output/pressure_band_candidates.csv", help="候选输出CSV")
    p.add_argument("--failed-out", type=str, default="output/pressure_band_failed.csv", help="失败清单CSV")

    p.add_argument("--start-date", type=str, default="20160101", help="开始日期，格式YYYYMMDD")
    p.add_argument("--end-date", type=str, default=pd.Timestamp.today().strftime("%Y%m%d"), help="结束日期，格式YYYYMMDD")
    p.add_argument("--refresh", action="store_true", help="强制刷新缓存")

    p.add_argument("--base-bucket-pct", type=float, default=None, help="基础价格桶百分比，例如0.008")
    p.add_argument("--min-quality", type=float, default=None, help="最小复合压力带质量分")
    p.add_argument("--include-boring", action="store_true", help="输出普通压力带，不只输出靠近/进入/突破状态")

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    cfg = Config()

    if args.base_bucket_pct is not None:
        cfg.base_bucket_pct = float(args.base_bucket_pct)

    if args.min_quality is not None:
        cfg.min_composite_quality = float(args.min_quality)

    if args.include_boring:
        cfg.only_interesting = False

    cache_dir = ensure_dir(args.cache_dir)
    ensure_dir(Path(args.out).parent)
    ensure_dir(Path(args.failed_out).parent)

    symbol_df = parse_symbols_arg(args.symbols, args.symbols_file, args.all, args.limit)

    print(f"[INFO] symbols={len(symbol_df)} start={args.start_date} end={args.end_date} cache={cache_dir}")
    print(f"[INFO] config={asdict(cfg)}")

    out, failed = scan_symbols(
        symbol_df=symbol_df,
        cfg=cfg,
        cache_dir=cache_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        refresh=args.refresh,
    )

    if not out.empty:
        out.to_csv(args.out, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(args.out, index=False, encoding="utf-8-sig")

    if not failed.empty:
        failed.to_csv(args.failed_out, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=["symbol", "name", "reason"]).to_csv(
            args.failed_out,
            index=False,
            encoding="utf-8-sig",
        )

    print(f"[DONE] candidates={len(out)} -> {args.out}")
    print(f"[DONE] failed={len(failed)} -> {args.failed_out}")

    if not out.empty:
        show_cols = [
            "symbol",
            "name",
            "current_price",
            "union_lower",
            "core_upper",
            "final_upper",
            "dist_to_union_lower_pct",
            "dist_to_final_upper_pct",
            "current_state",
            "verify_grade",
            "composite_quality",
            "periods",
            "sources",
        ]

        print(out[show_cols].head(20).to_string(index=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
