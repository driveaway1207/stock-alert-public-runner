# -*- coding: utf-8 -*-
"""藏锋指标核心｜Hidden Edge Index

识别爆发前夜：价格压缩、量能稳定、结构完整、多周期收敛、临近触发。
本文件只计算指标，不读取/修改 PAT、workflow、缓存或 Telegram。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import math
import numpy as np
import pandas as pd

INDICATOR_NAME = "藏锋"
INDICATOR_VERSION = "1.0.1"


@dataclass(frozen=True)
class ZangfengConfig:
    min_rows: int = 60
    short_window: int = 10
    mid_window: int = 20
    long_window: int = 30
    price_weight: float = 25.0
    volume_weight: float = 20.0
    structure_weight: float = 25.0
    cycle_weight: float = 20.0
    trigger_weight: float = 10.0
    pressure_near_pct: float = 0.05
    trigger_near_pct: float = 0.035
    defense_min_pct: float = 0.025
    defense_max_pct: float = 0.13
    overheat_20d_pct: float = 0.28


def _f(x: Any, default: float = float("nan")) -> float:
    try:
        v = float(str(x).replace("%", "").replace(",", ""))
    except Exception:
        return default
    return v if math.isfinite(v) else default


def _clamp(x: Any, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, _f(x, lo)))


def _div(a: Any, b: Any, default: float = 0.0) -> float:
    a, b = _f(a), _f(b)
    if not math.isfinite(a) or not math.isfinite(b) or abs(b) < 1e-12:
        return default
    v = a / b
    return v if math.isfinite(v) else default


def _low_score(v: Any, good: float, bad: float, pts: float) -> float:
    v = _f(v)
    if not math.isfinite(v):
        return 0.0
    if v <= good:
        return pts
    if v >= bad:
        return 0.0
    return pts * (bad - v) / (bad - good)


def _high_score(v: Any, good: float, bad: float, pts: float) -> float:
    v = _f(v)
    if not math.isfinite(v):
        return 0.0
    if v >= good:
        return pts
    if v <= bad:
        return 0.0
    return pts * (v - bad) / (good - bad)


def _band_score(v: Any, low: float, high: float, pts: float, soft: float) -> float:
    v = _f(v)
    if not math.isfinite(v):
        return 0.0
    if low <= v <= high:
        return pts
    soft = max(soft, 1e-12)
    miss = low - v if v < low else v - high
    return max(0.0, pts * (1.0 - miss / soft))


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df必须是pandas.DataFrame")
    src = df.copy()
    src.columns = [str(c).strip() for c in src.columns]
    lower = {c.lower(): c for c in src.columns}
    aliases = {
        "date": ["date", "trade_date", "日期", "交易日期", "time"],
        "code": ["code", "代码", "证券代码"],
        "name": ["name", "名称", "股票名称"],
        "open": ["open", "开盘", "开盘价"],
        "high": ["high", "最高", "最高价"],
        "low": ["low", "最低", "最低价"],
        "close": ["close", "收盘", "收盘价"],
        "volume": ["volume", "vol", "成交量"],
        "amount": ["amount", "成交额"],
        "pct_chg": ["pct_chg", "pctchg", "涨跌幅", "涨幅"],
    }
    out = pd.DataFrame()
    for target, names in aliases.items():
        found = None
        for name in names:
            if name in src.columns:
                found = name
                break
            if name.lower() in lower:
                found = lower[name.lower()]
                break
        if found is not None:
            out[target] = src[found]
    missing = [c for c in ["open", "high", "low", "close"] if c not in out.columns]
    if missing:
        raise ValueError("缺少必要字段: " + ",".join(missing))
    if "date" not in out.columns:
        out["date"] = pd.date_range("2000-01-01", periods=len(out), freq="B")
    for c in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.replace([np.inf, -np.inf], np.nan)
    out = out.dropna(subset=["date", "open", "high", "low", "close"])
    out = out[(out.open > 0) & (out.high > 0) & (out.low > 0) & (out.close > 0) & (out.high >= out.low)]
    out["volume"] = out["volume"].fillna(0.0).clip(lower=0.0)
    out = out.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if "pct_chg" not in out.columns or out["pct_chg"].abs().sum() == 0:
        prev = out.close.shift(1)
        out["pct_chg"] = (out.close / prev - 1.0) * 100.0
        out.loc[prev <= 0, "pct_chg"] = 0.0
        out["pct_chg"] = out["pct_chg"].replace([np.inf, -np.inf], 0.0).fillna(0.0)
    return out


def _mean_tail(s: pd.Series, n: int) -> float:
    return _f(pd.to_numeric(s.tail(n), errors="coerce").mean())


def _slope_pct(s: pd.Series) -> float:
    vals = pd.to_numeric(s, errors="coerce").dropna().to_numpy(float)
    if len(vals) < 3 or np.nanmean(vals) <= 0:
        return 0.0
    return _div(np.polyfit(np.arange(len(vals), dtype=float), vals, 1)[0], np.nanmean(vals), 0.0)


def _tr_pct(d: pd.DataFrame) -> pd.Series:
    pc = d.close.shift(1)
    tr = pd.concat([(d.high - d.low), (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    return tr / d.close.replace(0, np.nan)


def _cv(vol: pd.Series, n: int) -> float:
    v = pd.to_numeric(vol.tail(n), errors="coerce")
    v = v[v > 0].dropna()
    if len(v) < max(3, n // 3):
        return float("nan")
    return _div(v.std(ddof=0), v.mean(), float("nan"))


def _drawdown(close: pd.Series, n: int) -> float:
    c = pd.to_numeric(close.tail(n), errors="coerce").dropna()
    if len(c) < 2:
        return 0.0
    return abs(_f((c / c.cummax() - 1.0).min(), 0.0))


def _ret(close: pd.Series, n: int) -> float:
    if len(close) <= n:
        return 0.0
    return _div(close.iloc[-1], close.iloc[-n - 1], 1.0) - 1.0


def _resample(d: pd.DataFrame, rule: str) -> pd.DataFrame:
    t = d.copy().set_index(pd.to_datetime(d.date, errors="coerce"))
    o = t.resample(rule).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum"))
    return o.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def _grade(score: float, trigger_ready: bool, enough: bool) -> str:
    if not enough:
        return "数据不足"
    if score < 40:
        return "散锋"
    if score < 60:
        return "聚锋"
    if score < 75:
        return "养锋"
    if score < 90:
        return "藏锋"
    return "出鞘候选" if trigger_ready else "极致藏锋"


def _bias(score: float, trigger_ready: bool, enough: bool) -> str:
    if not enough:
        return "不参与：样本不足。"
    if score < 60:
        return "普通观察：压缩不足。"
    if score < 75:
        return "跟踪观察：已有蓄势，但爆发前夜质量不够。"
    if score < 90:
        return "重点观察：进入藏锋区，等待高质量突破或回踩确认。"
    return "临界观察：接近出鞘，仍必须等放量突破、站稳和RR合格。" if trigger_ready else "高度压缩：触发不足，不能提前买。"


def calculate_zangfeng(df: pd.DataFrame, *, pressure_price: Optional[float] = None, support_price: Optional[float] = None, trigger_price: Optional[float] = None, next_pressure_price: Optional[float] = None, config: Optional[ZangfengConfig] = None) -> Dict[str, Any]:
    cfg = config or ZangfengConfig()
    d = normalize_ohlcv(df)
    if len(d) < max(25, cfg.mid_window + 5):
        return {"indicator": INDICATOR_NAME, "version": INDICATOR_VERSION, "ok": False, "score": 0.0, "grade": "数据不足", "action_bias": "不参与：有效K线太少。", "dimensions": {"锋势": 0.0, "锋气": 0.0, "锋骨": 0.0, "锋意": 0.0, "出鞘准备": 0.0}, "flags": ["数据不足"], "notes": ["有效K线少于最低计算窗口，禁止解读为交易信号。"], "metrics": {"rows": int(len(d))}}

    flags: List[str] = []
    notes: List[str] = []
    enough = len(d) >= cfg.min_rows
    close, high, low, open_, volume = d.close, d.high, d.low, d.open, d.volume
    last = _f(close.iloc[-1])
    rng = (high - low) / close.replace(0, np.nan)
    body = (close - open_).abs() / close.replace(0, np.nan)
    tr = _tr_pct(d)

    range_ratio = _div(_mean_tail(rng, cfg.short_window), _f(rng.iloc[-cfg.long_window:-cfg.short_window].mean(), _mean_tail(rng, cfg.short_window)), 1.0)
    atr_ratio = _div(_mean_tail(tr, cfg.short_window), _mean_tail(tr, cfg.long_window), 1.0)
    body_ratio = _div(_mean_tail(body, cfg.short_window), _f(body.iloc[-cfg.long_window:-cfg.short_window].mean(), _mean_tail(body, cfg.short_window)), 1.0)
    close_std = _div(close.tail(cfg.short_window).std(ddof=0), close.tail(cfg.short_window).mean(), 0.0)
    low_slope = _slope_pct(low.tail(cfg.mid_window))
    center_slope = _slope_pct(close.tail(cfg.mid_window))

    price_score = 0.0
    price_score += _low_score(range_ratio, 0.70, 1.10, 6.0)
    price_score += _low_score(atr_ratio, 0.72, 1.12, 5.0)
    price_score += _low_score(body_ratio, 0.75, 1.15, 4.0)
    price_score += _low_score(close_std, 0.022, 0.065, 5.0)
    price_score += _high_score(low_slope, 0.0020, -0.0010, 3.0)
    price_score += _high_score(center_slope, 0.0015, -0.0015, 2.0)
    price_score = _clamp(price_score, 0.0, cfg.price_weight)

    cv_ratio = _div(_cv(volume, cfg.short_window), _cv(volume, cfg.long_window), 1.0)
    recent_vol = _mean_tail(volume, cfg.short_window)
    prior_vol = _f(volume.iloc[-cfg.long_window:-cfg.short_window].mean(), recent_vol)
    vol_level = _div(recent_vol, prior_vol, 1.0)
    rv = volume.tail(cfg.short_window)
    rv = rv[rv > 0].dropna()
    flat_ratio = float(((rv / rv.median()).between(0.85, 1.15)).mean()) if len(rv) >= 5 and rv.median() > 0 else 0.0
    daily_ret = close.pct_change()
    tail_vol = volume.tail(cfg.mid_window)
    up_vol = _f(tail_vol[daily_ret.tail(cfg.mid_window) > 0].mean(), float("nan"))
    down_vol = _f(tail_vol[daily_ret.tail(cfg.mid_window) < 0].mean(), float("nan"))
    down_up_ratio = _div(down_vol, up_vol, 1.0)
    pb_days = daily_ret.tail(cfg.short_window) < 0
    pb_vol = _f(volume.tail(cfg.short_window)[pb_days].mean(), recent_vol)
    pb_shrink = _div(pb_vol, recent_vol, 1.0)

    volume_score = 0.0
    volume_score += _low_score(cv_ratio, 0.68, 1.12, 5.0)
    volume_score += _high_score(flat_ratio, 0.60, 0.20, 4.0)
    volume_score += _band_score(vol_level, 0.55, 1.45, 4.0, 0.45)
    volume_score += _low_score(down_up_ratio, 0.82, 1.35, 4.0)
    volume_score += _low_score(pb_shrink, 0.78, 1.15, 3.0)
    volume_score = _clamp(volume_score, 0.0, cfg.volume_weight)

    high20 = _f(high.tail(cfg.mid_window).max(), last)
    low20 = _f(low.tail(cfg.mid_window).min(), last)
    width20 = _div(high20 - low20, last, 0.0)
    dd20 = _drawdown(close, cfg.mid_window)
    vol_ratio_prev = volume / volume.shift(1).replace(0, np.nan)
    bear_body = (close - open_) / open_.replace(0, np.nan)
    destructive = int(((bear_body <= -0.045) & (vol_ratio_prev >= 1.55) & (((high - low) / close.replace(0, np.nan)) >= 0.055)).tail(cfg.mid_window).sum())

    pressure = _f(pressure_price)
    support = _f(support_price)
    if math.isfinite(pressure) and pressure > 0:
        pressure_dist = _div(pressure - last, last, 9.99)
        pressure_score = _band_score(pressure_dist, -0.01, cfg.pressure_near_pct, 5.0, 0.08)
    else:
        pressure_dist = float("nan")
        pressure_score = 2.0
        flags.append("缺核心压力线")
    if math.isfinite(support) and 0 < support < last:
        defense_dist = _div(last - support, last, 9.99)
        defense_score = _band_score(defense_dist, cfg.defense_min_pct, cfg.defense_max_pct, 4.0, 0.06)
    else:
        defense_dist = float("nan")
        defense_score = 1.0
        flags.append("缺真实防守位")

    structure_score = _clamp(_low_score(width20, 0.085, 0.22, 6.0) + _low_score(dd20, 0.075, 0.20, 5.0) + _low_score(destructive, 0.0, 3.0, 5.0) + pressure_score + defense_score, 0.0, cfg.structure_weight)

    weekly = _resample(d, "W")
    monthly = _resample(d, "ME")
    weekly_score, weekly_range, weekly_spread = 3.0, float("nan"), float("nan")
    if len(weekly) >= 12:
        wr = (weekly.high - weekly.low) / weekly.close.replace(0, np.nan)
        weekly_range = _div(wr.tail(4).mean(), wr.tail(12).head(8).mean(), 1.0)
        ma = [weekly.close.rolling(n).mean().iloc[-1] for n in (5, 10, 20)]
        weekly_spread = _div(max(ma) - min(ma), weekly.close.iloc[-1], 0.0) if all(math.isfinite(_f(x)) for x in ma) else float("nan")
        weekly_score = _low_score(weekly_range, 0.72, 1.15, 5.0) + _low_score(weekly_spread, 0.035, 0.12, 3.0) + _high_score(_slope_pct(weekly.low.tail(8)), 0.0020, -0.0020, 2.0)
    else:
        flags.append("周线样本不足")
    monthly_score, monthly_range, monthly_body = 3.0, float("nan"), float("nan")
    if len(monthly) >= 8:
        mr = (monthly.high - monthly.low) / monthly.close.replace(0, np.nan)
        mb = (monthly.close - monthly.open).abs() / monthly.close.replace(0, np.nan)
        monthly_range = _div(mr.tail(3).mean(), mr.tail(8).head(5).mean(), 1.0)
        monthly_body = _div(mb.tail(3).mean(), mb.tail(8).head(5).mean(), 1.0)
        monthly_score = _low_score(monthly_range, 0.78, 1.18, 4.0) + _low_score(monthly_body, 0.80, 1.25, 3.0) + _low_score(_ret(monthly.close, min(6, len(monthly) - 2)), 0.30, 0.90, 3.0)
    else:
        flags.append("月线样本不足")
    cycle_score = _clamp(weekly_score + monthly_score, 0.0, cfg.cycle_weight)

    trigger = _f(trigger_price)
    if not math.isfinite(trigger) or trigger <= 0:
        trigger = high20
        flags.append("触发线使用20日高点近似")
    trigger_dist = _div(trigger - last, last, 9.99)
    trigger_near_score = _band_score(trigger_dist, -0.006, cfg.trigger_near_pct, 3.0, 0.065)
    next_pressure = _f(next_pressure_price)
    if math.isfinite(next_pressure) and math.isfinite(support) and next_pressure > last and 0 < support < last:
        rr = _div(next_pressure - last, last - support, 0.0)
        rr_score = _high_score(rr, 2.2, 1.0, 3.0)
    else:
        rr = float("nan")
        rr_score = 1.0
        flags.append("RR数据不足")
    ret20 = _ret(close, cfg.mid_window)
    trigger_score = _clamp(trigger_near_score + rr_score + _low_score(ret20, 0.12, cfg.overheat_20d_pct, 2.0) + _high_score(_div(last, high20, 0.0), 0.985, 0.92, 2.0), 0.0, cfg.trigger_weight)
    trigger_ready = trigger_dist <= cfg.trigger_near_pct and ret20 <= cfg.overheat_20d_pct

    score = _clamp(price_score + volume_score + structure_score + cycle_score + trigger_score, 0.0, 100.0)
    if not enough:
        flags.append("样本低于推荐阈值")
        notes.append(f"有效K线{len(d)}根，低于推荐阈值{cfg.min_rows}根，分数仅作参考。")
    if destructive >= 2:
        flags.append("近20日破坏性放量阴线偏多")
    if ret20 > cfg.overheat_20d_pct:
        flags.append("近20日涨幅过热")
    if width20 > 0.22:
        flags.append("平台过宽")
    if close_std <= 0.022 and volume_score >= 14:
        flags.append("价量同步压缩")
    if score >= 75 and trigger_ready:
        flags.append("藏锋临界")
    elif score >= 75:
        flags.append("藏锋未触发")

    dimensions = {"锋势": round(price_score, 2), "锋气": round(volume_score, 2), "锋骨": round(structure_score, 2), "锋意": round(cycle_score, 2), "出鞘准备": round(trigger_score, 2)}
    metrics = {
        "rows": int(len(d)), "last_close": round(last, 4), "range_contract_ratio": round(_f(range_ratio, 0), 4), "atr_contract_ratio": round(_f(atr_ratio, 0), 4), "body_contract_ratio": round(_f(body_ratio, 0), 4), "close_std_pct_10d": round(_f(close_std, 0), 4),
        "low_slope_20d": round(_f(low_slope, 0), 6), "center_slope_20d": round(_f(center_slope, 0), 6), "volume_cv_contract_ratio": round(_f(cv_ratio, 0), 4), "flat_volume_ratio_10d": round(_f(flat_ratio, 0), 4), "vol_level_ratio": round(_f(vol_level, 0), 4),
        "down_up_vol_ratio": round(_f(down_up_ratio, 0), 4), "pullback_shrink_ratio": round(_f(pb_shrink, 0), 4), "platform_width_20d": round(_f(width20, 0), 4), "max_drawdown_20d": round(_f(dd20, 0), 4), "destructive_count_20d": destructive,
        "pressure_distance": None if not math.isfinite(pressure_dist) else round(pressure_dist, 4), "defense_distance": None if not math.isfinite(defense_dist) else round(defense_dist, 4), "weekly_range_ratio": None if not math.isfinite(weekly_range) else round(weekly_range, 4),
        "weekly_ma_spread": None if not math.isfinite(weekly_spread) else round(weekly_spread, 4), "monthly_range_ratio": None if not math.isfinite(monthly_range) else round(monthly_range, 4), "monthly_body_ratio": None if not math.isfinite(monthly_body) else round(monthly_body, 4),
        "trigger_distance": round(_f(trigger_dist, 0), 4), "rr_ratio": None if not math.isfinite(rr) else round(rr, 4), "return_20d": round(_f(ret20, 0), 4),
    }
    return {"indicator": INDICATOR_NAME, "version": INDICATOR_VERSION, "ok": True, "score": round(score, 2), "grade": _grade(score, trigger_ready, enough), "action_bias": _bias(score, trigger_ready, enough), "dimensions": dimensions, "flags": list(dict.fromkeys(flags)), "notes": notes, "metrics": metrics}


def build_builtin_sample(n: int = 120, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = 10 + np.cumsum(rng.normal(0.015, 0.08, n))
    noise = np.concatenate([rng.normal(0, 0.22, max(0, n - 40)), rng.normal(0, 0.045, min(40, n))])[:n]
    close = np.maximum(base + noise, 1.0)
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0.006, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0.006, 0.003, n)))
    volume = np.maximum(np.concatenate([rng.normal(1300000, 260000, max(0, n - 40)), rng.normal(1250000, 70000, min(40, n))])[:n], 10000)
    return pd.DataFrame({"date": pd.date_range("2024-01-01", periods=n, freq="B"), "open": open_, "high": high, "low": low, "close": close, "volume": volume})


def self_check() -> Dict[str, Any]:
    good_df = build_builtin_sample()
    last = float(good_df.close.iloc[-1])
    good = calculate_zangfeng(good_df, pressure_price=last * 1.025, support_price=last * 0.94, trigger_price=last * 1.018, next_pressure_price=last * 1.20)
    rng = np.random.default_rng(17)
    n = 120
    close = np.maximum(5 + np.cumsum(rng.normal(0.0, 0.45, n)), 1.0)
    open_ = close * (1 + rng.normal(0, 0.035, n))
    bad = calculate_zangfeng(pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * (1 + np.abs(rng.normal(0.05, 0.025, n))), "low": np.minimum(open_, close) * (1 - np.abs(rng.normal(0.05, 0.025, n))), "close": close, "volume": rng.lognormal(mean=14.0, sigma=0.75, size=n)}))
    short = calculate_zangfeng(good_df.tail(12))
    checks = {"good_score_positive": good["score"] >= 45, "bad_score_lower_than_good": bad["score"] <= good["score"], "short_sample_rejected": short["ok"] is False and short["grade"] == "数据不足", "score_range_good": 0 <= good["score"] <= 100, "score_range_bad": 0 <= bad["score"] <= 100, "dimension_sum_capped": sum(good["dimensions"].values()) <= 100.01, "required_keys_present": all(k in good for k in ["score", "grade", "dimensions", "metrics", "flags", "action_bias"])}
    return {"ok": all(checks.values()), "checks": checks, "good_score": good["score"], "good_grade": good["grade"], "bad_score": bad["score"], "bad_grade": bad["grade"], "short_grade": short["grade"]}
