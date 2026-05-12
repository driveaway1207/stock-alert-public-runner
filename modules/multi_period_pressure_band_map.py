#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股多周期压力带地图生成器 V1

生成对象：
- 季线、月线、周线、日线：各周期本身的成交密集区/压力带
- 多周期核心重合区
- 多周期并集压力区
- 当前最重要固定突破线
- 突破买入口径

核心原则：
1. 先算单周期自己的 Volume Profile：POC / VAL / VAH / HVN。
2. 再用结构锚点校准：周期高点、最大量阳K、上影共振、假突破/放量滞涨。
3. 最后做多周期合成。
4. 当天/最近一根K线高点不自动生成核心压力带。
"""

from __future__ import annotations

import argparse
import math
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


@dataclass
class PeriodConfig:
    code: str
    cn: str
    resample_rule: Optional[str]
    max_bars: int
    bucket_pct_min: float
    bucket_pct_max: float
    base_bucket_pct: float
    value_area_ratio: float
    weight: float


PERIODS: Dict[str, PeriodConfig] = {
    "Q": PeriodConfig("Q", "季线", "QE",    40, 0.015, 0.040, 0.024, 0.70, 2.20),
    "M": PeriodConfig("M", "月线", "ME",    84, 0.010, 0.028, 0.016, 0.70, 1.75),
    "W": PeriodConfig("W", "周线", "W-FRI",156, 0.006, 0.018, 0.010, 0.70, 1.35),
    "D": PeriodConfig("D", "日线", None,   260, 0.004, 0.012, 0.006, 0.70, 1.00),
}
PERIOD_ORDER = ["Q", "M", "W", "D"]


@dataclass
class ProfileZone:
    lower: float
    upper: float
    poc: float
    vah: float
    val: float
    total_volume: float
    poc_volume_share: float
    value_area_ratio: float
    hvn_bands: List[Tuple[float, float, float]]
    lvn_bands: List[Tuple[float, float, float]]


@dataclass
class PressureCandidate:
    period: str
    lower: float
    upper: float
    line: float
    score: float
    source: str
    detail: str


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


def code6(symbol: str) -> str:
    m = re.search(r"(\d{6})", str(symbol))
    return m.group(1) if m else str(symbol)


def safe_float(x, default=np.nan) -> float:
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def fmt_band(l, u) -> str:
    if l is None or u is None or pd.isna(l) or pd.isna(u):
        return ""
    return f"{float(l):.2f}-{float(u):.2f}"


def fmt_price(x) -> str:
    if x is None or pd.isna(x):
        return ""
    return f"{float(x):.2f}"


def round4(x):
    if x is None or pd.isna(x):
        return ""
    return round(float(x), 4)


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    col_map = {
        "日期": "date", "时间": "date", "开盘": "open", "最高": "high",
        "最低": "low", "收盘": "close", "成交量": "volume", "成交额": "amount",
        "vol": "volume",
    }
    d = df.copy()
    d = d.rename(columns={c: col_map.get(c, c) for c in d.columns})
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
    d = d[d["volume"] > 0]
    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    return d[["date", "open", "high", "low", "close", "volume", "amount"]]


def load_kline_from_cache(symbol: str, cache_dir: Path) -> pd.DataFrame:
    ns = normalize_symbol(symbol)
    c = code6(ns)
    possible = [
        cache_dir / f"{c}.csv",
        cache_dir / f"{c}_daily.csv",
        cache_dir / f"{c}_daily_qfq.csv",
        cache_dir / f"{ns.replace('.', '_')}.csv",
        cache_dir / f"{ns.replace('.', '_')}_daily_qfq.csv",
    ]
    for p in possible:
        if p.exists():
            return normalize_ohlcv(pd.read_csv(p))
    raise FileNotFoundError(f"未找到缓存K线: {symbol}")


def resample_ohlcv(daily: pd.DataFrame, rule: str) -> pd.DataFrame:
    d = daily.copy().set_index("date")
    out = pd.DataFrame({
        "open": d["open"].resample(rule).first(),
        "high": d["high"].resample(rule).max(),
        "low": d["low"].resample(rule).min(),
        "close": d["close"].resample(rule).last(),
        "volume": d["volume"].resample(rule).sum(),
        "amount": d["amount"].resample(rule).sum(),
    }).dropna().reset_index()
    return out


def calc_atr(df: pd.DataFrame, n: int = 20) -> float:
    if len(df) < 5:
        return np.nan
    pc = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return safe_float(tr.rolling(n, min_periods=5).mean().iloc[-1])


def adaptive_bucket_pct(df: pd.DataFrame, pcfg: PeriodConfig) -> float:
    if df.empty:
        return pcfg.base_bucket_pct
    close = safe_float(df["close"].iloc[-1])
    atr = calc_atr(df, min(20, max(5, len(df) // 5)))
    atr_pct = atr / close if close > 0 and not pd.isna(atr) else pcfg.base_bucket_pct
    bucket = max(pcfg.base_bucket_pct, atr_pct * 0.35)
    return clamp(bucket, pcfg.bucket_pct_min, pcfg.bucket_pct_max)


def bucket_id(price: float, bucket_pct: float) -> int:
    if price <= 0:
        return 0
    return int(math.floor(math.log(price) / math.log(1 + bucket_pct)))


def bucket_bounds(bid: int, bucket_pct: float) -> Tuple[float, float]:
    lo = math.exp(bid * math.log(1 + bucket_pct))
    up = math.exp((bid + 1) * math.log(1 + bucket_pct))
    return lo, up


def allocate_bar_to_buckets(row: pd.Series, bucket_pct: float) -> Dict[int, float]:
    """
    OHLCV 近似成交量分配：
    - high-low覆盖区：55%
    - 实体区：35%
    - 收盘价所在桶：10%
    """
    low = safe_float(row["low"])
    high = safe_float(row["high"])
    open_ = safe_float(row["open"])
    close = safe_float(row["close"])
    vol = safe_float(row["volume"], 0)

    if low <= 0 or high <= 0 or vol <= 0 or high < low:
        return {}

    b0 = bucket_id(low, bucket_pct)
    b1 = bucket_id(high, bucket_pct)
    all_buckets = list(range(min(b0, b1), max(b0, b1) + 1))
    if not all_buckets:
        return {}

    out: Dict[int, float] = {}

    v1 = vol * 0.55 / len(all_buckets)
    for b in all_buckets:
        out[b] = out.get(b, 0.0) + v1

    body_low = min(open_, close)
    body_high = max(open_, close)
    bb0 = bucket_id(max(body_low, low), bucket_pct)
    bb1 = bucket_id(min(body_high, high), bucket_pct)
    body_buckets = list(range(min(bb0, bb1), max(bb0, bb1) + 1))
    if body_buckets:
        v2 = vol * 0.35 / len(body_buckets)
        for b in body_buckets:
            out[b] = out.get(b, 0.0) + v2

    cb = bucket_id(close, bucket_pct)
    out[cb] = out.get(cb, 0.0) + vol * 0.10
    return out


def profile_cluster_to_band(rows: List[pd.Series]) -> Optional[Tuple[float, float, float]]:
    if not rows:
        return None
    df = pd.DataFrame(rows)
    return safe_float(df["lower"].min()), safe_float(df["upper"].max()), safe_float(df["volume"].sum())


def cluster_profile_rows(rows: pd.DataFrame, bucket_gap: int = 2) -> List[Tuple[float, float, float]]:
    if rows is None or rows.empty:
        return []
    bands = []
    cur = []
    last_bid = None
    for _, r in rows.sort_values("bid").iterrows():
        bid = int(r["bid"])
        if last_bid is None or bid - last_bid <= bucket_gap:
            cur.append(r)
        else:
            b = profile_cluster_to_band(cur)
            if b:
                bands.append(b)
            cur = [r]
        last_bid = bid
    if cur:
        b = profile_cluster_to_band(cur)
        if b:
            bands.append(b)
    return sorted(bands, key=lambda x: x[2], reverse=True)


def build_volume_profile(df: pd.DataFrame, pcfg: PeriodConfig) -> Optional[ProfileZone]:
    if df is None or len(df) < max(12, min(30, pcfg.max_bars // 5)):
        return None

    d = df.tail(pcfg.max_bars).copy().reset_index(drop=True)
    bucket_pct = adaptive_bucket_pct(d, pcfg)
    stats: Dict[int, float] = {}
    n = len(d)

    for i, row in d.iterrows():
        # 轻微近期衰减：不能让最新K线决定全部，但也不能忽视新筹码
        recency_weight = 0.5 ** ((n - 1 - i) / max(30, n * 0.65))
        alloc = allocate_bar_to_buckets(row, bucket_pct)
        for bid, v in alloc.items():
            stats[bid] = stats.get(bid, 0.0) + v * recency_weight

    if not stats:
        return None

    rows = []
    for bid, vol in stats.items():
        lo, up = bucket_bounds(bid, bucket_pct)
        rows.append({"bid": bid, "lower": lo, "upper": up, "mid": (lo + up) / 2, "volume": vol})

    prof = pd.DataFrame(rows).sort_values("bid").reset_index(drop=True)
    total = prof["volume"].sum()
    if total <= 0:
        return None

    poc_idx = int(prof["volume"].idxmax())
    poc_row = prof.loc[poc_idx]
    poc = safe_float(poc_row["mid"])
    poc_share = safe_float(poc_row["volume"] / total)

    # Value Area：从 POC 向两侧按成交量大的方向扩展到70%
    included = {poc_idx}
    cur_vol = safe_float(poc_row["volume"])
    left = poc_idx - 1
    right = poc_idx + 1

    while cur_vol / total < pcfg.value_area_ratio and (left >= 0 or right < len(prof)):
        left_vol = safe_float(prof.loc[left, "volume"]) if left >= 0 else -1
        right_vol = safe_float(prof.loc[right, "volume"]) if right < len(prof) else -1

        if right_vol >= left_vol:
            if right < len(prof):
                included.add(right)
                cur_vol += right_vol
                right += 1
            elif left >= 0:
                included.add(left)
                cur_vol += left_vol
                left -= 1
        else:
            if left >= 0:
                included.add(left)
                cur_vol += left_vol
                left -= 1
            elif right < len(prof):
                included.add(right)
                cur_vol += right_vol
                right += 1

    inc = prof.loc[sorted(included)]
    val = safe_float(inc["lower"].min())
    vah = safe_float(inc["upper"].max())

    hvn_threshold = max(prof["volume"].quantile(0.82), prof["volume"].mean() + 0.25 * prof["volume"].std())
    hvn = prof[prof["volume"] >= hvn_threshold].copy()
    hvn_bands = cluster_profile_rows(hvn, bucket_gap=2)

    lvn_threshold = max(prof["volume"].quantile(0.20), 0)
    lvn = prof[prof["volume"] <= lvn_threshold].copy()
    lvn_bands = cluster_profile_rows(lvn, bucket_gap=2)

    return ProfileZone(
        lower=safe_float(prof["lower"].min()),
        upper=safe_float(prof["upper"].max()),
        poc=poc,
        vah=vah,
        val=val,
        total_volume=safe_float(total),
        poc_volume_share=poc_share,
        value_area_ratio=safe_float(cur_vol / total),
        hvn_bands=hvn_bands,
        lvn_bands=lvn_bands,
    )


def candle_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    d["body"] = (d["close"] - d["open"]).abs()
    d["body_ratio"] = d["body"] / rng
    d["upper_wick"] = d["high"] - d[["open", "close"]].max(axis=1)
    d["upper_wick_ratio"] = d["upper_wick"] / rng
    d["close_pos"] = (d["close"] - d["low"]) / rng
    d["vol_ma20"] = d["volume"].rolling(20, min_periods=5).mean()
    d["vol_ratio20"] = d["volume"] / d["vol_ma20"].replace(0, np.nan)
    return d


def structural_anchors(df: pd.DataFrame, current: float, period: str, pcfg: PeriodConfig) -> List[PressureCandidate]:
    if df is None or len(df) < 20:
        return []

    d = candle_features(df.tail(pcfg.max_bars)).reset_index(drop=True)
    out: List[PressureCandidate] = []

    def near_relevant(price: float) -> bool:
        return current * 0.85 <= price <= current * 1.80

    # 周期高点：作为极端锚点，不代表主密集区
    r = d.loc[d["high"].idxmax()]
    p = safe_float(r["high"])
    if near_relevant(p):
        out.append(PressureCandidate(period, p*0.995, p*1.005, p, 70*pcfg.weight, "周期最高点", f"{pcfg.cn}最高点{fmt_price(p)}"))

    # 最大量阳K高点
    bull = d[(d["close"] > d["open"]) & (d["body_ratio"].fillna(0) >= 0.25)].copy()
    if not bull.empty:
        r = bull.sort_values("volume", ascending=False).iloc[0]
        p = safe_float(r["high"])
        if near_relevant(p):
            out.append(PressureCandidate(period, p*0.99, p*1.01, p, 60*pcfg.weight, "最大量阳K高点", f"{pcfg.cn}最大量阳K高点{fmt_price(p)}"))

    # 上影线共振：至少2次相近
    wick = d[(d["upper_wick_ratio"].fillna(0) >= 0.35) & (d["close_pos"].fillna(1) <= 0.70)].copy()
    if len(wick) >= 2:
        bpct = adaptive_bucket_pct(d, pcfg) * 1.8
        wick["bid"] = wick["high"].apply(lambda x: bucket_id(float(x), bpct))
        for _, g in wick.groupby("bid"):
            if len(g) < 2:
                continue
            p = safe_float(g["high"].max())
            if near_relevant(p):
                out.append(PressureCandidate(period, p*0.99, p*1.01, p, (40 + 8*len(g))*pcfg.weight, "上影线共振", f"{pcfg.cn}上影共振{len(g)}次"))

    # 假突破/放量滞涨高点
    d["prev_high_10"] = d["high"].shift(1).rolling(10, min_periods=5).max()
    fake = d[(d["high"] > d["prev_high_10"] * 1.003) &
             (d["close"] < d["high"] - (d["high"] - d["low"]) * 0.35) &
             (d["vol_ratio20"].fillna(0) >= 1.2)].copy()
    if not fake.empty:
        r = fake.sort_values(["high", "volume"], ascending=False).iloc[0]
        p = safe_float(r["high"])
        if near_relevant(p):
            out.append(PressureCandidate(period, p*0.99, p*1.01, p, 55*pcfg.weight, "假突破/放量滞涨高点", f"{pcfg.cn}假突破高点{fmt_price(p)}"))

    return out


def merge_candidates(cands: List[PressureCandidate], pct_tol: float = 0.025) -> List[PressureCandidate]:
    if not cands:
        return []
    cands = sorted(cands, key=lambda c: c.line)
    groups: List[List[PressureCandidate]] = []
    for c in cands:
        placed = False
        for g in groups:
            mid = np.mean([x.line for x in g])
            if abs(c.line / mid - 1) <= pct_tol:
                g.append(c)
                placed = True
                break
        if not placed:
            groups.append([c])

    out = []
    for g in groups:
        lower = min(x.lower for x in g)
        upper = max(x.upper for x in g)
        line = max(x.line for x in g)  # 压力带突破线取上沿
        score = sum(x.score for x in g) / max(1, len(g)) + 8 * min(len(g), 4)
        source = "+".join(sorted(set(x.source for x in g)))
        detail = " | ".join(x.detail for x in sorted(g, key=lambda x: -x.score)[:4])
        out.append(PressureCandidate(g[0].period, lower, upper, line, score, source, detail))
    return sorted(out, key=lambda c: c.score, reverse=True)


def period_pressure_candidates(df: pd.DataFrame, current: float, period: str, pcfg: PeriodConfig, profile: Optional[ProfileZone]) -> List[PressureCandidate]:
    cands: List[PressureCandidate] = []
    if profile is not None:
        # VAH：本周期主成交密集带上沿
        if profile.vah >= current * 0.97:
            cands.append(PressureCandidate(
                period, profile.vah * 0.992, profile.vah * 1.008, profile.vah,
                80 * pcfg.weight, "VAH价值区上沿", f"{pcfg.cn}VAH {fmt_price(profile.vah)}"
            ))

        # 当前价上方/正在进入的HVN
        hvn_above = []
        for lo, up, vol in profile.hvn_bands:
            if up >= current * 0.985 and lo <= current * 1.65:
                hvn_above.append((lo, up, vol))
        hvn_above = sorted(hvn_above, key=lambda x: (max(x[0] - current, 0), -x[2]))
        for idx, (lo, up, vol) in enumerate(hvn_above[:2], start=1):
            share = vol / profile.total_volume if profile.total_volume else 0
            score = (65 + 80 * min(share, 0.25)) * pcfg.weight
            cands.append(PressureCandidate(
                period, lo, up, up, score, f"当前价上方HVN{idx}", f"{pcfg.cn}上方HVN{idx} {fmt_band(lo, up)}"
            ))

    cands.extend(structural_anchors(df, current, period, pcfg))
    return merge_candidates(cands, pct_tol=0.025)


def overlap_ratio(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lo = max(a[0], b[0])
    up = min(a[1], b[1])
    if up <= lo:
        return 0.0
    return (up - lo) / max(min(a[1]-a[0], b[1]-b[0]), 1e-9)


def cluster_multi_period_candidates(cands: List[PressureCandidate], current: float) -> List[Dict]:
    if not cands:
        return []
    cands = sorted(cands, key=lambda c: c.line)
    groups: List[List[PressureCandidate]] = []
    for c in cands:
        placed = False
        for g in groups:
            center = np.mean([x.line for x in g])
            if abs(c.line / center - 1) <= 0.028 or any(overlap_ratio((c.lower, c.upper), (x.lower, x.upper)) > 0.25 for x in g):
                g.append(c)
                placed = True
                break
        if not placed:
            groups.append([c])

    zones = []
    for g in groups:
        union_lower = min(x.lower for x in g)
        union_upper = max(x.upper for x in g)

        best_core = None
        best_count = 0
        best_score = -1
        points = sorted(set([x.lower for x in g] + [x.upper for x in g]))
        if len(points) >= 2:
            for lo, up in zip(points[:-1], points[1:]):
                mid = (lo + up) / 2
                covered = [x for x in g if x.lower <= mid <= x.upper]
                cnt = len(set(x.period for x in covered))
                sc = sum(x.score for x in covered)
                if cnt > best_count or (cnt == best_count and sc > best_score):
                    best_count = cnt
                    best_score = sc
                    best_core = (lo, up)

        if best_core is None:
            top = sorted(g, key=lambda x: -x.score)[0]
            best_core = (top.lower, top.upper)
            best_count = 1
            best_score = top.score

        periods = sorted(set(x.period for x in g), key=lambda p: PERIOD_ORDER.index(p) if p in PERIOD_ORDER else 99)
        period_weight_score = sum(PERIODS[p].weight for p in periods if p in PERIODS)
        source_count = len(set(x.source for x in g))
        dist = max(0, union_lower / current - 1) if current > 0 else 0
        dist_score = 20 if dist <= 0.08 else 12 if dist <= 0.18 else 6 if dist <= 0.35 else -5
        quality = min(100, max(0, best_score * 0.35 + period_weight_score * 12 + source_count * 4 + dist_score))

        zones.append({
            "union_lower": union_lower,
            "union_upper": union_upper,
            "core_lower": best_core[0],
            "core_upper": best_core[1],
            "break_line": union_upper,
            "periods": periods,
            "sources": sorted(set(x.source for x in g)),
            "details": " || ".join(x.detail for x in sorted(g, key=lambda x: -x.score)[:6]),
            "quality": quality,
            "period_count": len(periods),
            "core_overlap_period_count": best_count,
        })

    return sorted(zones, key=lambda z: (max(z["union_lower"] - current, 0), -z["quality"]))


def choose_current_key_zone(zones: List[Dict], current: float) -> Optional[Dict]:
    if not zones:
        return None
    above = [z for z in zones if z["union_upper"] >= current * 0.985]
    if not above:
        return sorted(zones, key=lambda z: -z["quality"])[0]
    def score(z):
        dist = max(0, z["union_lower"] / current - 1)
        return z["quality"] - dist * 120
    return sorted(above, key=score, reverse=True)[0]


def pressure_state(current: float, zone: Optional[Dict]) -> str:
    if zone is None:
        return "无有效压力带"
    lo, up = zone["union_lower"], zone["union_upper"]
    if current < lo * 0.995:
        return "尚未进入压力带"
    if lo * 0.995 <= current <= up * 1.003:
        return "处于压力带内部"
    return "已突破压力带上沿，需看回踩确认"


def buy_condition(current: float, zone: Optional[Dict]) -> str:
    if zone is None:
        return ""
    lo, up = zone["union_lower"], zone["union_upper"]
    return f"日线收盘有效站上 {fmt_price(up)}，实体大部分在压力带上方，量能健康，上影线不长；突破后不快速跌回 {fmt_price(lo)} 下方。"


def build_period_frames(daily: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    frames = {"D": daily.copy()}
    for p, cfg in PERIODS.items():
        if p == "D":
            continue
        frames[p] = resample_ohlcv(daily, cfg.resample_rule)
    return frames


def analyze_symbol(symbol: str, name: str, daily: pd.DataFrame) -> Dict:
    ns = normalize_symbol(symbol)
    if daily.empty or len(daily) < 120:
        raise ValueError("日线数据不足")

    current = safe_float(daily["close"].iloc[-1])
    frames = build_period_frames(daily)
    row: Dict = {
        "股票代码": ns,
        "股票名称": name,
        "当前价": round4(current),
        "最新日期": str(pd.to_datetime(daily["date"].iloc[-1]).date()),
    }

    all_cands: List[PressureCandidate] = []
    for p in PERIOD_ORDER:
        pcfg = PERIODS[p]
        dfp = frames[p].tail(pcfg.max_bars).copy()
        profile = build_volume_profile(dfp, pcfg)

        if profile is not None:
            row[f"{pcfg.cn}_POC公允中枢"] = round4(profile.poc)
            row[f"{pcfg.cn}_VAL价值区下沿"] = round4(profile.val)
            row[f"{pcfg.cn}_VAH价值区上沿"] = round4(profile.vah)
            row[f"{pcfg.cn}_主成交密集带"] = fmt_band(profile.val, profile.vah)
            row[f"{pcfg.cn}_POC成交占比"] = round4(profile.poc_volume_share)
        else:
            row[f"{pcfg.cn}_POC公允中枢"] = ""
            row[f"{pcfg.cn}_VAL价值区下沿"] = ""
            row[f"{pcfg.cn}_VAH价值区上沿"] = ""
            row[f"{pcfg.cn}_主成交密集带"] = ""
            row[f"{pcfg.cn}_POC成交占比"] = ""

        cands = period_pressure_candidates(dfp, current, p, pcfg, profile)
        cands = [x for x in cands if x.upper >= current * 0.985]
        cands = sorted(cands, key=lambda x: (max(x.lower - current, 0), -x.score))[:2]
        all_cands.extend(cands)

        first = cands[0] if cands else None
        second = cands[1] if len(cands) > 1 else None

        row[f"{pcfg.cn}_第一压力带"] = fmt_band(first.lower, first.upper) if first else ""
        row[f"{pcfg.cn}_第一突破线"] = round4(first.line) if first else ""
        row[f"{pcfg.cn}_第一压力来源"] = first.source if first else ""
        row[f"{pcfg.cn}_第一压力说明"] = first.detail if first else ""

        row[f"{pcfg.cn}_第二压力带"] = fmt_band(second.lower, second.upper) if second else ""
        row[f"{pcfg.cn}_第二突破线"] = round4(second.line) if second else ""
        row[f"{pcfg.cn}_第二压力来源"] = second.source if second else ""
        row[f"{pcfg.cn}_第二压力说明"] = second.detail if second else ""

    zones = cluster_multi_period_candidates(all_cands, current)
    key_zone = choose_current_key_zone(zones, current)

    if key_zone:
        row["多周期核心重合区"] = fmt_band(key_zone["core_lower"], key_zone["core_upper"])
        row["多周期并集压力区"] = fmt_band(key_zone["union_lower"], key_zone["union_upper"])
        row["当前最重要固定突破线"] = round4(key_zone["break_line"])
        row["当前压力状态"] = pressure_state(current, key_zone)
        row["压力带质量分"] = round4(key_zone["quality"])
        row["参与周期"] = "/".join(PERIODS[p].cn for p in key_zone["periods"])
        row["核心重合周期数"] = key_zone["core_overlap_period_count"]
        row["压力来源组合"] = "、".join(key_zone["sources"])
        row["压力带明细"] = key_zone["details"]
        row["突破买入口径"] = buy_condition(current, key_zone)
    else:
        row["多周期核心重合区"] = ""
        row["多周期并集压力区"] = ""
        row["当前最重要固定突破线"] = ""
        row["当前压力状态"] = "无有效压力带"
        row["压力带质量分"] = ""
        row["参与周期"] = ""
        row["核心重合周期数"] = ""
        row["压力来源组合"] = ""
        row["压力带明细"] = ""
        row["突破买入口径"] = ""

    for i, z in enumerate(zones[:3], start=1):
        row[f"复合压力带{i}"] = fmt_band(z["union_lower"], z["union_upper"])
        row[f"复合压力带{i}_核心重合区"] = fmt_band(z["core_lower"], z["core_upper"])
        row[f"复合压力带{i}_突破线"] = round4(z["break_line"])
        row[f"复合压力带{i}_周期"] = "/".join(PERIODS[p].cn for p in z["periods"])
        row[f"复合压力带{i}_质量分"] = round4(z["quality"])

    return row


def parse_symbols(symbols: Optional[str], symbols_file: Optional[str], limit: Optional[int]) -> pd.DataFrame:
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
                items.append({"symbol": normalize_symbol(parts[0]), "name": parts[1] if len(parts) > 1 else ""})

    df = pd.DataFrame(items)
    if df.empty:
        raise ValueError("请指定 --symbols 或 --symbols-file")
    df["symbol"] = df["symbol"].apply(normalize_symbol)
    df = df.drop_duplicates("symbol")
    if limit:
        df = df.head(limit)
    return df.reset_index(drop=True)


def scan(symbol_df: pd.DataFrame, cache_dir: Path, quiet: bool = False) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rows, failed = [], []
    n = len(symbol_df)
    for i, r in symbol_df.iterrows():
        symbol = r["symbol"]
        name = r.get("name", "")
        try:
            daily = load_kline_from_cache(symbol, cache_dir)
            rows.append(analyze_symbol(symbol, name, daily))
        except Exception as e:
            failed.append({"symbol": symbol, "name": name, "reason": str(e)[:1000]})
        if not quiet and ((i + 1) % 200 == 0 or i + 1 == n):
            print(f"[PROGRESS] {i+1}/{n} success={len(rows)} failed={len(failed)}")
    return pd.DataFrame(rows), pd.DataFrame(failed)


def write_outputs(df: pd.DataFrame, failed: pd.DataFrame, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    if df.empty:
        df = pd.DataFrame()
    else:
        df["_q"] = pd.to_numeric(df.get("压力带质量分", 0), errors="coerce").fillna(0)
        if "当前价" in df.columns and "当前最重要固定突破线" in df.columns:
            price = pd.to_numeric(df["当前价"], errors="coerce")
            line = pd.to_numeric(df["当前最重要固定突破线"], errors="coerce")
            df["_dist"] = ((line / price - 1) * 100).clip(lower=-999, upper=999)
        else:
            df["_dist"] = 999
        df = df.sort_values(["_q", "_dist"], ascending=[False, True]).drop(columns=["_q", "_dist"], errors="ignore")

    if failed.empty:
        failed = pd.DataFrame(columns=["symbol", "name", "reason"])

    df.to_csv(out_dir / "a_share_pressure_band_full.csv", index=False, encoding="utf-8-sig")

    simple_cols = [
        "股票代码", "股票名称", "当前价", "最新日期",
        "季线_POC公允中枢", "季线_主成交密集带", "季线_第一压力带", "季线_第一突破线",
        "月线_POC公允中枢", "月线_主成交密集带", "月线_第一压力带", "月线_第一突破线",
        "周线_POC公允中枢", "周线_主成交密集带", "周线_第一压力带", "周线_第一突破线",
        "日线_POC公允中枢", "日线_主成交密集带", "日线_第一压力带", "日线_第一突破线",
        "多周期核心重合区", "多周期并集压力区", "当前最重要固定突破线", "当前压力状态",
        "压力带质量分", "参与周期", "核心重合周期数", "压力来源组合", "突破买入口径",
    ]
    simple_cols = [c for c in simple_cols if c in df.columns]
    (df[simple_cols] if not df.empty else pd.DataFrame(columns=simple_cols)).to_csv(
        out_dir / "a_share_pressure_band_review.csv", index=False, encoding="utf-8-sig"
    )

    if not df.empty and "当前压力状态" in df.columns:
        mapping = {
            "尚未进入压力带": "a_share_pressure_not_entered.csv",
            "处于压力带内部": "a_share_pressure_inside_band.csv",
            "已突破压力带上沿，需看回踩确认": "a_share_pressure_broken.csv",
        }
        for state, fname in mapping.items():
            df[df["当前压力状态"].astype(str).eq(state)].to_csv(out_dir / fname, index=False, encoding="utf-8-sig")

    failed.to_csv(out_dir / "a_share_pressure_band_failed.csv", index=False, encoding="utf-8-sig")
    print(f"[DONE] full={len(df)} failed={len(failed)}")
    print("[OUT] a_share_pressure_band_review.csv")


def build_parser():
    p = argparse.ArgumentParser("A股多周期压力带地图生成器")
    p.add_argument("--symbols", default=None)
    p.add_argument("--symbols-file", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--cache-dir", default="kline_cache")
    p.add_argument("--out-dir", default="output")
    p.add_argument("--quiet", action="store_true")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cache_dir = Path(args.cache_dir)
    out_dir = ensure_dir(args.out_dir)
    symbols = parse_symbols(args.symbols, args.symbols_file, args.limit)
    if not args.quiet:
        print(f"[INFO] symbols={len(symbols)} cache_dir={cache_dir} out_dir={out_dir}")
        print("[INFO] 生成季/月/周/日 Volume Profile 压力带地图")
    df, failed = scan(symbols, cache_dir, quiet=args.quiet)
    write_outputs(df, failed, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
