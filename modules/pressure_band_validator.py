#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pressure_band_validator.py  V4
固定核心压力带 + 高级日线突破/突破后站稳/回踩确认 可实操筛选

核心修正：
1. 不只看最近1-3天“刚突破”，也识别最近10天突破后仍站稳、回踩确认的机会。
2. 同时评估核心线1、核心线2、当前最重要线，避免只盯最高压力导致全市场全进等待。
3. 正式可实操、准可实操、等待突破、假突破失败分开输出。
4. --quiet 下不刷屏，适配当前 yml。
"""

from __future__ import annotations
import argparse, math, re, time, warnings
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


@dataclass
class Config:
    daily_bars: int = 620
    weekly_bars: int = 260
    monthly_bars: int = 180
    quarterly_bars: int = 90
    yearly_bars: int = 35
    anchor_band_width_pct: float = 0.010
    cluster_pct: float = 0.028
    min_cluster_score: float = 30.0
    max_above_current_pct: float = 0.60
    max_below_current_pct: float = 0.30
    broke_buffer_pct: float = 0.003
    strong_close_pos: float = 0.78
    good_close_pos: float = 0.66
    min_body_above_s: float = 0.55
    min_body_above_a: float = 0.32
    max_upper_wick_s: float = 0.35
    max_upper_wick_a: float = 0.50
    standard_double_min: float = 1.80
    standard_double_max: float = 2.50
    healthy_vol_min: float = 1.10
    healthy_vol_max: float = 4.50
    recent_break_days: int = 3
    valid_break_window: int = 10
    min_rr_actionable: float = 1.50
    min_rr_near: float = 1.15
    prefer_rr: float = 2.00
    high_position_pct: float = 0.82
    quiet: bool = False
    debug: bool = False


PERIOD_NAME = {"D": "日线", "W": "周线", "M": "月线", "Q": "季线", "Y": "年线"}
PERIOD_ORDER = {"D": 1, "W": 2, "M": 3, "Q": 4, "Y": 5}
PERIOD_WEIGHT = {"D": 1.0, "W": 1.35, "M": 1.75, "Q": 2.10, "Y": 2.50}
SOURCE_CN = {
    "period_high": "周期最高点",
    "swing_high": "阶段高点/前高",
    "max_bull_volume_high": "最大量阳K高点",
    "upper_wick_resonance": "上影线共振压力",
    "false_break_memory": "假突破记忆压力",
    "down_gap_pressure": "向下跳空缺口压力",
    "large_body_supply_high": "放量供应K高点",
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


@dataclass
class CoreBand:
    lower: float
    upper: float
    core_line: float
    score: float
    periods: List[str]
    sources: List[str]
    anchors: List[Anchor] = field(default_factory=list)
    status: str = ""
    purpose: str = ""


def ensure_dir(p: str | Path) -> Path:
    p = Path(p)
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


def calc_atr(df: pd.DataFrame, n: int = 20) -> float:
    if len(df) < 5:
        return np.nan
    pc = df["close"].shift(1)
    tr = pd.concat([(df["high"] - df["low"]), (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return safe_float(tr.rolling(n, min_periods=5).mean().iloc[-1])


def adaptive_bucket_pct(df: pd.DataFrame) -> float:
    atr = calc_atr(df)
    close = safe_float(df["close"].iloc[-1]) if not df.empty else np.nan
    atr_pct = atr / close if close and close > 0 and not pd.isna(atr) else 0.02
    return clamp(max(0.008, atr_pct * 0.35), 0.004, 0.018)


def log_bucket_id(price: float, bucket_pct: float) -> int:
    if price <= 0:
        return 0
    return int(math.floor(math.log(price) / math.log(1 + bucket_pct)))


def bucket_bounds(bid: int, bucket_pct: float) -> Tuple[float, float]:
    return math.exp(bid * math.log(1 + bucket_pct)), math.exp((bid + 1) * math.log(1 + bucket_pct))


AK_COL_MAP = {
    "日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
    "成交量": "volume", "成交额": "amount"
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
    d = d[d["volume"] > 0].reset_index(drop=True)
    return d[["date", "open", "high", "low", "close", "volume", "amount"]]


def fetch_daily_akshare(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    if ak is None:
        raise RuntimeError("未安装 akshare")
    c = code6(symbol)
    last_err = None
    for i in range(3):
        try:
            raw = ak.stock_zh_a_hist(symbol=c, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            return normalize_ohlcv(raw)
        except Exception as e:
            last_err = e
            time.sleep(0.8 * (i + 1))
    raise RuntimeError(f"akshare fetch failed {symbol}: {last_err}")


def load_daily(symbol: str, cache_dir: Path, start_date: str, end_date: str, refresh: bool, cfg: Config) -> pd.DataFrame:
    ns = normalize_symbol(symbol)
    c = code6(ns)
    names = [
        f"{c}.csv", f"{c}_daily.csv", f"{c}_daily_qfq.csv",
        f"{ns.replace('.', '_')}_daily_qfq.csv", f"{ns.replace('.', '_')}.csv",
    ]
    if not refresh:
        for name in names:
            p = cache_dir / name
            if p.exists():
                try:
                    d = normalize_ohlcv(pd.read_csv(p))
                    if not d.empty:
                        return d
                except Exception:
                    pass
    d = fetch_daily_akshare(ns, start_date, end_date)
    if not d.empty:
        d.to_csv(cache_dir / f"{c}.csv", index=False, encoding="utf-8-sig")
    return d


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    d = df.copy().set_index("date")
    out = pd.DataFrame({
        "open": d["open"].resample(rule).first(),
        "high": d["high"].resample(rule).max(),
        "low": d["low"].resample(rule).min(),
        "close": d["close"].resample(rule).last(),
        "volume": d["volume"].resample(rule).sum(),
        "amount": d["amount"].resample(rule).sum(),
    }).dropna().reset_index()
    return out


def build_frames(daily: pd.DataFrame, cfg: Config) -> Dict[str, pd.DataFrame]:
    return {
        "D": daily.tail(cfg.daily_bars).copy(),
        "W": resample_ohlcv(daily, "W-FRI").tail(cfg.weekly_bars),
        "M": resample_ohlcv(daily, "ME").tail(cfg.monthly_bars),
        "Q": resample_ohlcv(daily, "QE").tail(cfg.quarterly_bars),
        "Y": resample_ohlcv(daily, "YE").tail(cfg.yearly_bars),
    }


def make_anchor(period, price, source, score, cfg, date="", detail="") -> Anchor:
    price = float(price)
    w = cfg.anchor_band_width_pct
    return Anchor(period=period, price=price, lower=price*(1-w), upper=price*(1+w),
                  source=source, score=float(score), date=date, detail=detail)


def in_range(price: float, current: float, cfg: Config) -> bool:
    if pd.isna(price) or price <= 0 or current <= 0:
        return False
    return current * (1 - cfg.max_below_current_pct) <= price <= current * (1 + cfg.max_above_current_pct)


def extract_period_high(df, period, current, cfg):
    if len(df) < 10:
        return []
    d = candle_features(df)
    r = d.loc[d["high"].idxmax()]
    high = safe_float(r["high"])
    if not in_range(high, current, cfg):
        return []
    score = (35 + 10 * PERIOD_WEIGHT.get(period, 1)) * PERIOD_WEIGHT.get(period, 1)
    return [make_anchor(period, high, "period_high", score, cfg, str(pd.to_datetime(r["date"]).date()), "周期最高点")]


def extract_swing_high(df, period, current, cfg):
    if len(df) < 30:
        return []
    d = candle_features(df).reset_index(drop=True)
    is_swing = d["high"] == d["high"].rolling(7, center=True, min_periods=3).max()
    cand = d[is_swing.fillna(False)].copy()
    if cand.empty:
        cand = d.nlargest(8, "high")
    cand = cand.sort_values(["high", "volume"], ascending=False).head(10)
    res = []
    for _, r in cand.iterrows():
        high = safe_float(r["high"])
        if not in_range(high, current, cfg):
            continue
        high_pct = float((d["high"] <= high).mean())
        vol = safe_float(r.get("vol_ratio20", 1), 1)
        wick = safe_float(r.get("upper_wick_ratio", 0), 0)
        score = (18 + 20*high_pct + 7*min(vol,3) + 10*wick) * PERIOD_WEIGHT.get(period,1)
        res.append(make_anchor(period, high, "swing_high", score, cfg, str(pd.to_datetime(r["date"]).date()), "阶段高点/前高"))
    return res


def extract_max_bull_volume(df, period, current, cfg):
    if len(df) < 20:
        return []
    d = candle_features(df).copy()
    valid = d[(d["close"] > d["open"]) & (d["body_ratio"].fillna(0) >= 0.28)]
    cand = valid.sort_values("volume", ascending=False).head(3)
    res = []
    for _, r in cand.iterrows():
        high = safe_float(r["high"])
        if not in_range(high, current, cfg):
            continue
        vol_rank = float((d["volume"] <= r["volume"]).mean())
        body = safe_float(r.get("body_ratio", 0), 0)
        score = (25 + 28*vol_rank + 14*body) * PERIOD_WEIGHT.get(period,1)
        res.append(make_anchor(period, high, "max_bull_volume_high", score, cfg, str(pd.to_datetime(r["date"]).date()), "最大量阳K高点"))
    return res


def extract_upper_wick_resonance(df, period, current, cfg):
    if len(df) < 40:
        return []
    d = candle_features(df).reset_index(drop=True)
    cand = d[(d["upper_wick_ratio"].fillna(0) >= 0.38) & (d["close_pos"].fillna(1) <= 0.66)].copy()
    if cand.empty:
        return []
    bucket = adaptive_bucket_pct(d) * 1.5
    cand["bid"] = cand["high"].apply(lambda x: log_bucket_id(float(x), bucket))
    res = []
    for bid, g in cand.groupby("bid"):
        if len(g) < 2:
            continue
        high = safe_float(g["high"].max())
        if not in_range(high, current, cfg):
            continue
        score = (26 + 9*min(len(g),5) + 18*safe_float(g["upper_wick_ratio"].mean(),0)) * PERIOD_WEIGHT.get(period,1)
        res.append(make_anchor(period, high, "upper_wick_resonance", score, cfg, str(pd.to_datetime(g["date"].iloc[-1]).date()), f"上影线共振{len(g)}次"))
    return res


def extract_false_break(df, period, current, cfg):
    if len(df) < 50:
        return []
    d = candle_features(df).tail(260).reset_index(drop=True)
    d["rh"] = d["high"].shift(1).rolling(20, min_periods=8).max()
    cand = d[(d["high"] > d["rh"]*1.003) & (d["close"] < d["rh"]*1.003) &
             (d["upper_wick_ratio"].fillna(0) >= 0.30)].copy()
    res = []
    for _, r in cand.sort_values("high", ascending=False).head(6).iterrows():
        high = safe_float(r["high"])
        if not in_range(high, current, cfg):
            continue
        score = (32 + 12*min(safe_float(r.get("vol_ratio20",1),1),3) + 16*safe_float(r.get("upper_wick_ratio",0),0)) * PERIOD_WEIGHT.get(period,1)
        res.append(make_anchor(period, high, "false_break_memory", score, cfg, str(pd.to_datetime(r["date"]).date()), "假突破失败高点"))
    return res


def extract_gap_pressure(df, period, current, cfg):
    if len(df) < 20:
        return []
    d = df.copy().reset_index(drop=True)
    d["prev_low"] = d["low"].shift(1)
    gaps = d[d["high"] < d["prev_low"] * 0.995].copy()
    res = []
    for _, r in gaps.sort_values("date", ascending=False).head(5).iterrows():
        up = safe_float(r["prev_low"])
        if not in_range(up, current, cfg):
            continue
        score = 20 * PERIOD_WEIGHT.get(period,1)
        res.append(make_anchor(period, up, "down_gap_pressure", score, cfg, str(pd.to_datetime(r["date"]).date()), "向下跳空缺口上沿"))
    return res


def extract_supply_bar(df, period, current, cfg):
    if len(df) < 40:
        return []
    d = candle_features(df)
    cand = d[(d["vol_ratio20"].fillna(0) >= 1.6) &
             ((d["upper_wick_ratio"].fillna(0) >= 0.28) | (d["close_pos"].fillna(1) <= 0.62))].copy()
    res = []
    for _, r in cand.sort_values(["volume", "high"], ascending=False).head(6).iterrows():
        high = safe_float(r["high"])
        if not in_range(high, current, cfg):
            continue
        score = (20 + 10*min(safe_float(r.get("vol_ratio20",1),1),3)) * PERIOD_WEIGHT.get(period,1)
        res.append(make_anchor(period, high, "large_body_supply_high", score, cfg, str(pd.to_datetime(r["date"]).date()), "放量供应K高点"))
    return res


def extract_volume_profile(df, period, current, cfg):
    if len(df) < 40:
        return []
    d = candle_features(df).reset_index(drop=True)
    bucket = adaptive_bucket_pct(d)
    stats = {}
    n = len(d)
    for i, r in d.iterrows():
        low, high = safe_float(r["low"]), safe_float(r["high"])
        if low <= 0 or high <= 0:
            continue
        b0, b1 = log_bucket_id(low, bucket), log_bucket_id(high, bucket)
        bids = list(range(min(b0,b1), max(b0,b1)+1))
        if not bids:
            continue
        w = 0.5 ** ((n-1-i)/120)
        vol = safe_float(r["volume"],0) / len(bids)
        for bid in bids:
            s = stats.setdefault(bid, {"wv":0.0, "cover":0})
            s["wv"] += vol*w
            s["cover"] += 1
    if not stats:
        return []
    rows = []
    for bid, s in stats.items():
        lo, up = bucket_bounds(bid, bucket)
        rows.append({"bid": bid, "lower": lo, "upper": up, **s})
    p = pd.DataFrame(rows)
    p = p[(p["upper"] >= current*(1-cfg.max_below_current_pct)) & (p["lower"] <= current*(1+cfg.max_above_current_pct))]
    if p.empty:
        return []
    th = max(p["wv"].quantile(0.84), p["wv"].mean() + 0.25*p["wv"].std())
    hvn = p[(p["wv"] >= th) & (p["cover"] >= 3)].sort_values("bid")
    if hvn.empty:
        return []
    clusters, cur, last = [], [], None
    for _, rr in hvn.iterrows():
        bid = int(rr["bid"])
        if last is None or bid-last <= 2:
            cur.append(rr)
        else:
            clusters.append(pd.DataFrame(cur)); cur=[rr]
        last = bid
    if cur: clusters.append(pd.DataFrame(cur))
    total = max(1.0, p["wv"].sum())
    res = []
    for c in clusters[:5]:
        up = safe_float(c["upper"].max())
        if not in_range(up, current, cfg):
            continue
        share = safe_float(c["wv"].sum()/total,0)
        score = (14 + 45*share) * PERIOD_WEIGHT.get(period,1)
        res.append(make_anchor(period, up, "volume_profile_hvn", score, cfg, "", "成交密集区上沿"))
    return res


def extract_anchors(frames: Dict[str, pd.DataFrame], current: float, cfg: Config) -> List[Anchor]:
    funcs = [
        extract_period_high, extract_swing_high, extract_max_bull_volume,
        extract_upper_wick_resonance, extract_false_break, extract_gap_pressure,
        extract_supply_bar, extract_volume_profile
    ]
    anchors = []
    for period, df in frames.items():
        for fn in funcs:
            try:
                anchors.extend(fn(df, period, current, cfg))
            except Exception:
                continue
    return [a for a in anchors if a.score >= 12 and a.price > 0]


def anchors_near(a: Anchor, b: Anchor, cfg: Config) -> bool:
    mid = (a.price + b.price)/2
    return mid > 0 and abs(a.price-b.price)/mid <= cfg.cluster_pct


def band_status(current: float, b: CoreBand, cfg: Config) -> Tuple[str, str]:
    line = b.core_line
    if current > line*(1+cfg.broke_buffer_pct):
        if current <= line*1.10:
            return "已突破，压力转支撑观察", "可看回踩不破与承接"
        return "已明显突破，作为下方支撑参考", "不再作为当前上方压力"
    if abs(current/line - 1) <= 0.05:
        return "正在接近核心上沿", "等待日线高级K线突破"
    return "尚未突破，当前上方核心压力", "等待高级突破"


def cluster_anchors(anchors: List[Anchor], current: float, cfg: Config) -> List[CoreBand]:
    groups: List[List[Anchor]] = []
    for a in sorted(anchors, key=lambda x: x.price):
        placed = False
        for g in groups:
            if any(anchors_near(a, x, cfg) for x in g):
                g.append(a); placed=True; break
        if not placed:
            groups.append([a])
    out = []
    for g in groups:
        lower = min(x.lower for x in g)
        upper = max(x.upper for x in g)
        core = max(x.price for x in g)
        periods = sorted(set(x.period for x in g), key=lambda p: PERIOD_ORDER.get(p,99))
        sources = sorted(set(x.source for x in g))
        big = sum(PERIOD_WEIGHT.get(p,1) for p in periods)
        score = sum(x.score for x in g)*0.42 + big*8 + min(len(sources),7)*3
        dist = abs(core/current - 1) if current > 0 else 0
        if dist <= 0.06: score += 16
        elif dist <= 0.12: score += 9
        elif dist <= 0.25: score += 2
        else: score -= 10
        width = (upper-lower)/max((upper+lower)/2, 1e-9)
        if width > 0.12: score -= 14
        elif width <= 0.05: score += 5
        score = clamp(score,0,100)
        if score < cfg.min_cluster_score:
            continue
        b = CoreBand(lower=float(lower), upper=float(upper), core_line=float(core),
                     score=float(score), periods=periods, sources=sources,
                     anchors=sorted(g, key=lambda x: -x.score))
        b.status, b.purpose = band_status(current, b, cfg)
        out.append(b)
    return sorted(out, key=lambda c: (c.score, len(c.periods)), reverse=True)


def select_core_bands(bands: List[CoreBand], current: float) -> Tuple[Optional[CoreBand], Optional[CoreBand], Optional[CoreBand]]:
    if not bands:
        return None, None, None
    core1 = sorted(bands, key=lambda c: (abs(c.core_line/current-1), -c.score, -len(c.periods)))[0]
    above = [c for c in bands if c.core_line >= current*0.995]
    if above:
        core2 = sorted(above, key=lambda c: (sum(PERIOD_WEIGHT.get(p,1) for p in c.periods)*10 + c.score - abs(c.core_line/current-1)*15), reverse=True)[0]
        if abs(core2.core_line/core1.core_line-1) <= 0.015:
            alt = [c for c in above if abs(c.core_line/core1.core_line-1) > 0.015]
            if alt:
                core2 = sorted(alt, key=lambda c: c.score, reverse=True)[0]
    else:
        core2 = None
    unbroken = [c for c in bands if c.core_line > current*(1+0.003)]
    most = sorted(unbroken, key=lambda c: (abs(c.core_line/current-1), -c.score))[0] if unbroken else core1
    return core1, core2, most


def evaluate_line_signal(daily: pd.DataFrame, line: float, cfg: Config) -> Dict:
    d = candle_features(daily).copy().reset_index(drop=True)
    if d.empty or line <= 0:
        return {}
    lookback = d.tail(cfg.valid_break_window).copy()
    best = None
    for idx, r in lookback.iterrows():
        open_, close, high, low = map(safe_float, [r["open"], r["close"], r["high"], r["low"]])
        body_low, body_high = min(open_, close), max(open_, close)
        body_len = max(body_high-body_low, 1e-9)
        close_above = close > line*(1+cfg.broke_buffer_pct)
        high_above = high > line*(1+cfg.broke_buffer_pct)
        body_above = max(0.0, body_high - max(body_low, line)) / body_len
        close_pos = safe_float(r.get("close_pos"), np.nan)
        wick = safe_float(r.get("upper_wick_ratio"), np.nan)
        vrp = safe_float(r.get("vol_ratio_prev"), np.nan)
        vr20 = safe_float(r.get("vol_ratio20"), np.nan)
        vol_ref = max(vrp if not pd.isna(vrp) else 0, vr20 if not pd.isna(vr20) else 0)
        standard = cfg.standard_double_min <= vrp <= cfg.standard_double_max
        healthy = cfg.healthy_vol_min <= vol_ref <= cfg.healthy_vol_max
        failed_intraday = high_above and not close_above
        score = 0
        if close_above: score += 24
        if body_above >= cfg.min_body_above_s: score += 22
        elif body_above >= cfg.min_body_above_a: score += 14
        if close_pos >= cfg.strong_close_pos: score += 18
        elif close_pos >= cfg.good_close_pos: score += 10
        if wick <= cfg.max_upper_wick_s: score += 12
        elif wick <= cfg.max_upper_wick_a: score += 6
        if standard: score += 14
        elif healthy: score += 9
        elif vol_ref >= 0.85 and close_above and close_pos >= cfg.good_close_pos:
            score += 5
        if failed_intraday: score -= 35
        if vol_ref > 5 and close_pos < 0.70: score -= 18
        score = clamp(score,0,100)
        if score >= 78:
            grade = "S级高级突破"
        elif score >= 62:
            grade = "A级有效突破"
        elif score >= 50 and close_above:
            grade = "B+级准有效突破"
        elif failed_intraday:
            grade = "D级冲高回落/假突破"
        else:
            grade = "未形成有效高级突破"
        rec = {
            "突破日期": str(pd.to_datetime(r["date"]).date()),
            "突破线": line,
            "收盘站上": "是" if close_above else "否",
            "盘中突破但失败": "是" if failed_intraday else "否",
            "实体站上线比例": body_above,
            "收盘位置": close_pos,
            "上影线比例": wick,
            "昨比量": vrp,
            "20日量比": vr20,
            "标准倍量": "是" if standard else "否",
            "健康放量": "是" if healthy else "否",
            "突破评分": score,
            "突破等级": grade,
            "突破日序号": idx,
            "突破日收盘": close,
            "突破日最低": low,
            "突破日最高": high,
            "突破日开盘": open_,
        }
        if best is None or rec["突破评分"] > best["突破评分"]:
            best = rec
    if best is None:
        return {}
    latest = d.iloc[-1]
    after = d.loc[best["突破日序号"]:]
    body_mid = (best["突破日开盘"]+best["突破日收盘"])/2
    close_hold_line = bool((after["close"] >= line*0.995).all())
    close_hold_mid = bool((after["close"] >= body_mid*0.995).all())
    latest_close = safe_float(latest["close"])
    pulled_back_near_line = bool((after["low"] <= line*1.025).any())
    reclaim_hold = latest_close >= line*1.003 and close_hold_line
    pullback_confirmed = pulled_back_near_line and reclaim_hold
    best["近10日守核心线"] = "是" if close_hold_line else "否"
    best["近10日守突破K实体中位"] = "是" if close_hold_mid else "否"
    best["突破后回踩确认"] = "是" if pullback_confirmed else "否"
    best["最新收盘"] = latest_close
    best["距突破天数"] = int(len(d) - 1 - best["突破日序号"])
    if best["收盘站上"] == "是" and best["距突破天数"] <= cfg.recent_break_days:
        best["触发类型"] = "最近3日高级突破"
    elif best["收盘站上"] == "是" and pullback_confirmed:
        best["触发类型"] = "突破后回踩确认"
    elif best["收盘站上"] == "是" and close_hold_line:
        best["触发类型"] = "突破后站稳"
    elif best["盘中突破但失败"] == "是":
        best["触发类型"] = "假突破失败"
    else:
        best["触发类型"] = "等待突破"
    return best


def estimate_space(daily: pd.DataFrame, current: float, line: float, bands: List[CoreBand], cfg: Config) -> Dict:
    d = candle_features(daily).copy().reset_index(drop=True)
    atr = calc_atr(d)
    if pd.isna(atr) or atr <= 0:
        atr = current*0.035
    above_lines = sorted({b.core_line for b in bands if b.core_line > max(current, line)*1.01})
    next_pressure = above_lines[0] if above_lines else np.nan
    recent_low = safe_float(d.tail(120)["low"].min(), current*0.85)
    box_h = max(line - recent_low, atr*2)
    t1 = max(line + atr, line + 0.382*box_h)
    t2 = max(line + 2*atr, line + 0.618*box_h)
    t3 = max(line + 3*atr, line + 1.000*box_h)
    if not pd.isna(next_pressure):
        t1 = min(t1, next_pressure)
    last = d.iloc[-1]
    body_mid = (safe_float(last["open"]) + safe_float(last["close"])) / 2
    stop = min(line*0.985, body_mid, safe_float(last["low"])*0.995)
    risk = max(current-stop, current*0.01)
    rr = max(t1-current,0)/risk if risk > 0 else np.nan
    high252 = safe_float(d.tail(252)["high"].max(), np.nan)
    low252 = safe_float(d.tail(252)["low"].min(), np.nan)
    pos = (current-low252)/max(high252-low252,1e-9) if not pd.isna(high252) else np.nan
    if rr >= cfg.prefer_rr:
        space_grade = "空间较好"
    elif rr >= cfg.min_rr_actionable:
        space_grade = "空间合格"
    elif rr >= cfg.min_rr_near:
        space_grade = "空间接近合格"
    else:
        space_grade = "空间不足"
    note = "阶段高位，突破后必须看承接和RR" if not pd.isna(pos) and pos >= cfg.high_position_pct else ""
    return {
        "下一层压力": next_pressure, "第一目标": t1, "第二目标": t2, "强势目标": t3,
        "交易防守位": stop, "第一目标收益风险比": rr, "空间等级": space_grade,
        "近一年位置": pos, "高位提示": note
    }


def periods_cn(b: Optional[CoreBand]) -> str:
    return "" if not b else "/".join(PERIOD_NAME.get(p,p) for p in b.periods)


def sources_cn(b: Optional[CoreBand]) -> str:
    return "" if not b else "、".join(SOURCE_CN.get(s,s) for s in b.sources)


def detail_cn(b: Optional[CoreBand]) -> str:
    if not b: return ""
    return "；".join(f"{PERIOD_NAME.get(a.period,a.period)} {SOURCE_CN.get(a.source,a.source)} {fmt_price(a.price)} {a.date}" for a in b.anchors[:5])


def classify_trade(sig: Dict, space: Dict, band: Optional[CoreBand], cfg: Config) -> Tuple[str, str, str, str]:
    if not band or not sig:
        return "等待高级突破", "未形成有效突破", "等待日线高级K线有效突破核心确认线", "waiting"
    grade = str(sig.get("突破等级",""))
    trigger = str(sig.get("触发类型",""))
    close_above = sig.get("收盘站上") == "是"
    failed = sig.get("盘中突破但失败") == "是"
    rr = safe_float(space.get("第一目标收益风险比"), np.nan)
    hold_line = sig.get("近10日守核心线") == "是"
    pullback = sig.get("突破后回踩确认") == "是"
    if failed or "D级" in grade:
        return "假突破/失败", "冲高回落或收盘未站稳", "放弃；除非后续重新高级突破失败高点", "failed"
    high_quality = grade in ["S级高级突破","A级有效突破"]
    near_quality = grade in ["S级高级突破","A级有效突破","B+级准有效突破"]
    if close_above and high_quality and rr >= cfg.min_rr_actionable:
        if trigger in ["最近3日高级突破", "突破后站稳", "突破后回踩确认"]:
            if hold_line or pullback:
                return "正式可实操", f"{trigger}，{grade}，RR合格", "次日守住确认线/突破K实体中位则继续有效", "actionable"
            return "准可实操", f"{grade}但承接仍需确认", "次日必须守住核心确认线", "near"
    if close_above and near_quality and rr >= cfg.min_rr_near:
        if trigger in ["最近3日高级突破", "突破后站稳", "突破后回踩确认"]:
            return "准可实操", f"{trigger}，{grade}，等待进一步承接/RR确认", "列入次日确认；守住核心线再升级", "near"
    if close_above and "B+" in grade:
        return "准可实操", "已突破但质量略弱", "等待次日量价承接确认", "near"
    return "等待高级突破", "尚未满足高级突破+RR条件", "等待日线高级K线有效突破", "waiting"


def analyze_symbol(symbol: str, name: str, daily: pd.DataFrame, cfg: Config) -> Optional[Dict]:
    if daily.empty or len(daily) < 120:
        return None
    ns = normalize_symbol(symbol)
    d = normalize_ohlcv(daily)
    current = safe_float(d["close"].iloc[-1])
    frames = build_frames(d, cfg)
    anchors = extract_anchors(frames, current, cfg)
    bands = cluster_anchors(anchors, current, cfg)
    bands = bands[:8]
    core1, core2, most = select_core_bands(bands, current)
    if not most:
        return None
    candidates = []
    for tag, b in [("核心线1", core1), ("核心线2", core2), ("当前最重要线", most)]:
        if not b: continue
        sig = evaluate_line_signal(frames["D"], b.core_line, cfg)
        sp = estimate_space(frames["D"], current, b.core_line, bands, cfg)
        status, reason, action, bucket = classify_trade(sig, sp, b, cfg)
        candidates.append((bucket, status, reason, action, tag, b, sig, sp))
    prio = {"actionable":1, "near":2, "waiting":3, "failed":4}
    candidates = sorted(candidates, key=lambda x: (prio.get(x[0],9), -safe_float(x[7].get("第一目标收益风险比"),0), -safe_float(x[6].get("突破评分"),0)))
    bucket, status, reason, action, tag, b, sig, sp = candidates[0]
    comp_lower = min((x.lower for x in bands), default=np.nan)
    comp_upper = max((x.upper for x in bands), default=np.nan)
    return {
        "股票代码": ns,
        "股票名称": name,
        "当前价": round(current,4),
        "固定核心压力带1": fmt_band(core1.lower, core1.upper) if core1 else "",
        "核心上沿1": round(core1.core_line,4) if core1 else "",
        "核心上沿1状态": core1.status if core1 else "",
        "核心上沿1周期": periods_cn(core1),
        "核心上沿1来源": sources_cn(core1),
        "固定核心压力带2": fmt_band(core2.lower, core2.upper) if core2 else "",
        "核心上沿2": round(core2.core_line,4) if core2 else "",
        "核心上沿2状态": core2.status if core2 else "",
        "核心上沿2周期": periods_cn(core2),
        "核心上沿2来源": sources_cn(core2),
        "本次触发线类型": tag,
        "本次判断核心压力带": fmt_band(b.lower, b.upper),
        "本次核心突破确认线": round(b.core_line,4),
        "本次核心压力状态": b.status,
        "本次核心压力周期": periods_cn(b),
        "本次核心压力来源": sources_cn(b),
        "核心锚点明细": detail_cn(b),
        "辅助复合压力带": fmt_band(comp_lower, comp_upper),
        "压力带数量": len(bands),
        "触发类型": sig.get("触发类型",""),
        "突破日期": sig.get("突破日期",""),
        "距突破天数": sig.get("距突破天数",""),
        "突破K等级": sig.get("突破等级",""),
        "突破K评分": round(safe_float(sig.get("突破评分")),2) if sig else "",
        "是否日线收盘站上确认线": sig.get("收盘站上",""),
        "是否盘中突破但失败": sig.get("盘中突破但失败",""),
        "实体站上线比例": round(safe_float(sig.get("实体站上线比例")),4) if sig else "",
        "收盘位置": round(safe_float(sig.get("收盘位置")),4) if sig else "",
        "上影线比例": round(safe_float(sig.get("上影线比例")),4) if sig else "",
        "昨比量": round(safe_float(sig.get("昨比量")),4) if sig else "",
        "20日量比": round(safe_float(sig.get("20日量比")),4) if sig else "",
        "标准倍量": sig.get("标准倍量",""),
        "健康放量": sig.get("健康放量",""),
        "近10日守核心线": sig.get("近10日守核心线",""),
        "近10日守突破K实体中位": sig.get("近10日守突破K实体中位",""),
        "突破后回踩确认": sig.get("突破后回踩确认",""),
        "下一层压力": round(safe_float(sp.get("下一层压力")),4) if not pd.isna(safe_float(sp.get("下一层压力"))) else "",
        "第一目标": round(safe_float(sp.get("第一目标")),4),
        "第二目标": round(safe_float(sp.get("第二目标")),4),
        "强势目标": round(safe_float(sp.get("强势目标")),4),
        "交易防守位": round(safe_float(sp.get("交易防守位")),4),
        "第一目标收益风险比": round(safe_float(sp.get("第一目标收益风险比")),4),
        "空间等级": sp.get("空间等级",""),
        "高位提示": sp.get("高位提示",""),
        "候选分类": status,
        "分类原因": reason,
        "下一步动作": action,
        "放弃条件": "冲高回落、收盘跌回核心确认线、放量长上影、近10日跌破突破K实体中位或核心线上沿",
        "_bucket": bucket,
    }


def get_a_stock_list() -> pd.DataFrame:
    if ak is None:
        raise RuntimeError("未安装 akshare")
    df = ak.stock_info_a_code_name()
    cmap = {}
    for c in df.columns:
        if c in ["code","代码","证券代码"]: cmap[c]="code"
        if c in ["name","名称","证券简称"]: cmap[c]="name"
    df = df.rename(columns=cmap)
    if "code" not in df.columns: df["code"] = df.iloc[:,0].astype(str)
    if "name" not in df.columns: df["name"] = ""
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["symbol"] = df["code"].apply(normalize_symbol)
    df = df[~df["name"].astype(str).str.contains("ST|退", case=False, na=False)]
    return df[["symbol","name"]].drop_duplicates("symbol")


def parse_symbols(symbols, symbols_file, use_all, limit) -> pd.DataFrame:
    items = []
    if symbols:
        for s in re.split(r"[,，\s]+", symbols.strip()):
            if s: items.append({"symbol": normalize_symbol(s), "name": ""})
    if symbols_file:
        with open(symbols_file, "r", encoding="utf-8") as f:
            for line in f:
                line=line.strip()
                if not line or line.startswith("#"): continue
                parts = re.split(r"[,，\s]+", line)
                items.append({"symbol": normalize_symbol(parts[0]), "name": parts[1] if len(parts)>1 else ""})
    if use_all:
        items.extend(get_a_stock_list().to_dict("records"))
    df = pd.DataFrame(items)
    if df.empty:
        raise ValueError("请指定 --symbols、--symbols-file 或 --all")
    df["symbol"] = df["symbol"].apply(normalize_symbol)
    df = df.drop_duplicates("symbol")
    if limit:
        df = df.head(limit)
    return df.reset_index(drop=True)


def scan(symbol_df: pd.DataFrame, cfg: Config, cache_dir: Path, start_date: str, end_date: str, refresh: bool) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rows, failed = [], []
    total = len(symbol_df)
    for i, item in symbol_df.iterrows():
        symbol, name = item["symbol"], item.get("name","")
        try:
            daily = load_daily(symbol, cache_dir, start_date, end_date, refresh, cfg)
            row = analyze_symbol(symbol, name, daily, cfg)
            if row:
                rows.append(row)
        except Exception as e:
            failed.append({"symbol": symbol, "name": name, "reason": str(e)[:1000]})
        if not cfg.quiet and ((i+1) % 200 == 0 or i+1 == total):
            print(f"[PROGRESS] {i+1}/{total} rows={len(rows)} failed={len(failed)}")
    return pd.DataFrame(rows), pd.DataFrame(failed)


def write_outputs(df: pd.DataFrame, failed: pd.DataFrame, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    if df.empty:
        df = pd.DataFrame()
    if failed.empty:
        failed = pd.DataFrame(columns=["symbol","name","reason"])
    if not df.empty:
        bucket_order = {"正式可实操":1, "准可实操":2, "等待高级突破":3, "假突破/失败":4}
        df["_sort1"] = df["候选分类"].map(bucket_order).fillna(9)
        df["_rr"] = pd.to_numeric(df["第一目标收益风险比"], errors="coerce").fillna(0)
        df["_score"] = pd.to_numeric(df["突破K评分"], errors="coerce").fillna(0)
        df = df.sort_values(["_sort1","_rr","_score"], ascending=[True,False,False]).drop(columns=["_sort1","_rr","_score"], errors="ignore")
    allv = df.drop(columns=["_bucket"], errors="ignore")
    if not df.empty and "_bucket" in df.columns:
        actionable = df[df["_bucket"].eq("actionable")].drop(columns=["_bucket"], errors="ignore")
        near = df[df["_bucket"].eq("near")].drop(columns=["_bucket"], errors="ignore")
        waiting = df[df["_bucket"].eq("waiting")].drop(columns=["_bucket"], errors="ignore")
        failed_break = df[df["_bucket"].eq("failed")].drop(columns=["_bucket"], errors="ignore")
    else:
        actionable = near = waiting = failed_break = pd.DataFrame()
    simple_cols = [
        "股票代码","股票名称","当前价","候选分类","本次触发线类型",
        "本次判断核心压力带","本次核心突破确认线","本次核心压力状态",
        "触发类型","突破日期","距突破天数","突破K等级",
        "是否日线收盘站上确认线","近10日守核心线","近10日守突破K实体中位","突破后回踩确认",
        "第一目标","第二目标","强势目标","交易防守位","第一目标收益风险比",
        "空间等级","分类原因","下一步动作","放弃条件"
    ]
    simple_cols = [c for c in simple_cols if c in allv.columns]
    actionable.to_csv(out_dir/"pressure_band_actionable.csv", index=False, encoding="utf-8-sig")
    near.to_csv(out_dir/"pressure_band_near_actionable.csv", index=False, encoding="utf-8-sig")
    waiting.to_csv(out_dir/"pressure_band_waiting_breakout.csv", index=False, encoding="utf-8-sig")
    failed_break.to_csv(out_dir/"pressure_band_failed_breakout.csv", index=False, encoding="utf-8-sig")
    allv.to_csv(out_dir/"pressure_band_all_validation.csv", index=False, encoding="utf-8-sig")
    failed.to_csv(out_dir/"pressure_band_failed.csv", index=False, encoding="utf-8-sig")
    (actionable[simple_cols] if not actionable.empty else pd.DataFrame(columns=simple_cols)).to_csv(out_dir/"pressure_band_actionable_精简版.csv", index=False, encoding="utf-8-sig")
    (near[simple_cols] if not near.empty else pd.DataFrame(columns=simple_cols)).to_csv(out_dir/"pressure_band_near_actionable_精简版.csv", index=False, encoding="utf-8-sig")
    print(f"[SUMMARY] actionable={len(actionable)} near={len(near)} waiting={len(waiting)} failed_break={len(failed_break)} all={len(allv)} failed={len(failed)}")


def build_parser():
    p = argparse.ArgumentParser("核心压力带高级突破可实操筛选 V4")
    p.add_argument("--symbols", default=None)
    p.add_argument("--symbols-file", default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--cache-dir", default="kline_cache")
    p.add_argument("--out-dir", default="output")
    p.add_argument("--start-date", default="20160101")
    p.add_argument("--end-date", default=pd.Timestamp.today().strftime("%Y%m%d"))
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--min-rr", type=float, default=1.5)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--debug", action="store_true")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config()
    cfg.min_rr_actionable = float(args.min_rr)
    cfg.quiet = bool(args.quiet)
    cfg.debug = bool(args.debug)
    cache_dir = ensure_dir(args.cache_dir)
    out_dir = ensure_dir(args.out_dir)
    symbols = parse_symbols(args.symbols, args.symbols_file, args.all, args.limit)
    if not cfg.quiet:
        print(f"[INFO] symbols={len(symbols)} cache_dir={cache_dir} out_dir={out_dir}")
        print("[INFO] V4: 最近10日突破有效性 + 突破后站稳/回踩确认 + 核心线1/2同步评估 + 低刷屏")
    df, failed = scan(symbols, cfg, cache_dir, args.start_date, args.end_date, args.refresh)
    write_outputs(df, failed, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
