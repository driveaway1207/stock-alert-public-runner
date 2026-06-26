# -*- coding: utf-8 -*-
"""
藏锋｜Hidden Edge Index（HEI）

独立实战指标，不依赖一号员工生产入口，不读取/修改任何凭证、workflow、缓存或推送链路。

定位：
    识别“爆发前夜”状态：波动压缩、量能稳定、结构完整、多周期收敛、临近触发。
    它不是独立买入信号，只负责判断一只股票是否已经进入“藏锋蓄势”状态。

输入：
    pandas.DataFrame，至少包含 open/high/low/close/volume 五列。
    列名大小写不敏感；如果存在 date/trade_date 字段，会按日期升序排序。

输出：
    dict，包含总分、等级、五维分项、原始指标、风险/状态标签、交易解释。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import math

import numpy as np
import pandas as pd


INDICATOR_NAME = "藏锋"
INDICATOR_ENGLISH_NAME = "Hidden Edge Index"
INDICATOR_VERSION = "1.0.0"


@dataclass(frozen=True)
class ZangfengConfig:
    """藏锋指标参数。默认值偏向A股日线实战，不做未来函数。"""

    min_rows: int = 60

    short_window: int = 10
    mid_window: int = 20
    long_window: int = 30

    max_score: float = 100.0

    price_weight: float = 25.0      # 锋势：价格压缩
    volume_weight: float = 20.0     # 锋气：量能稳定
    structure_weight: float = 25.0  # 锋骨：结构完整
    cycle_weight: float = 20.0      # 锋意：多周期收敛
    trigger_weight: float = 10.0    # 出鞘准备：临界触发

    pressure_near_pct: float = 0.05
    trigger_near_pct: float = 0.035
    defense_min_pct: float = 0.025
    defense_max_pct: float = 0.13
    overheat_20d_pct: float = 0.28


def _to_float(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    if math.isfinite(out):
        return out
    return default


def _clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    value = _to_float(value, low)
    return max(low, min(high, value))


def _safe_div(num: Any, den: Any, default: float = 0.0) -> float:
    num = _to_float(num)
    den = _to_float(den)
    if not math.isfinite(num) or not math.isfinite(den) or abs(den) < 1e-12:
        return default
    out = num / den
    return out if math.isfinite(out) else default


def _score_low_better(value: Any, good: float, bad: float, points: float) -> float:
    """value <= good 得满分，value >= bad 得0分，中间线性。"""
    value = _to_float(value)
    if not math.isfinite(value):
        return 0.0
    if value <= good:
        return points
    if value >= bad:
        return 0.0
    return points * (bad - value) / (bad - good)


def _score_high_better(value: Any, good: float, bad: float, points: float) -> float:
    """value >= good 得满分，value <= bad 得0分，中间线性。"""
    value = _to_float(value)
    if not math.isfinite(value):
        return 0.0
    if value >= good:
        return points
    if value <= bad:
        return 0.0
    return points * (value - bad) / (good - bad)


def _score_inside_band(value: Any, low: float, high: float, points: float, soft: float = 0.0) -> float:
    """value 落在 [low, high] 得满分，偏离后线性衰减。"""
    value = _to_float(value)
    if not math.isfinite(value):
        return 0.0
    if low <= value <= high:
        return points
    soft = max(float(soft), 1e-12)
    if value < low:
        return max(0.0, points * (1.0 - (low - value) / soft))
    return max(0.0, points * (1.0 - (value - high) / soft))


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df 必须是 pandas.DataFrame")

    rename = {str(c): str(c).strip().lower() for c in df.columns}
    data = df.rename(columns=rename).copy()

    aliases = {
        "open": ["open", "开盘", "开盘价"],
        "high": ["high", "最高", "最高价"],
        "low": ["low", "最低", "最低价"],
        "close": ["close", "收盘", "收盘价"],
        "volume": ["volume", "vol", "成交量"],
    }

    selected: Dict[str, str] = {}
    lower_cols = list(data.columns)
    for target, choices in aliases.items():
        found = next((c for c in choices if c in lower_cols), None)
        if found is None:
            raise ValueError(f"缺少必要字段: {target}")
        selected[found] = target

    data = data.rename(columns=selected)

    date_col = None
    for candidate in ("date", "trade_date", "日期"):
        if candidate in data.columns:
            date_col = candidate
            break
    if date_col:
        data[date_col] = pd.to_datetime(data[date_col], errors="coerce")
        data = data.sort_values(date_col)

    needed = ["open", "high", "low", "close", "volume"]
    for col in needed:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    data = data.replace([np.inf, -np.inf], np.nan)
    data = data.dropna(subset=["open", "high", "low", "close"])
    data = data[(data["high"] > 0) & (data["low"] > 0) & (data["close"] > 0)]
    data = data[data["high"] >= data["low"]]
    data["volume"] = data["volume"].where(data["volume"] > 0, np.nan)
    data = data.reset_index(drop=True)
    return data


def _mean_last(series: pd.Series, n: int) -> float:
    if len(series) <= 0:
        return float("nan")
    return _to_float(series.tail(n).mean())


def _slope_pct(values: pd.Series) -> float:
    vals = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(vals) < 3 or np.nanmean(vals) <= 0:
        return 0.0
    x = np.arange(len(vals), dtype=float)
    slope = np.polyfit(x, vals, 1)[0]
    return _safe_div(slope, np.nanmean(vals), 0.0)


def _true_range_pct(data: pd.DataFrame) -> pd.Series:
    prev_close = data["close"].shift(1)
    tr = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - prev_close).abs(),
            (data["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr / data["close"].replace(0, np.nan)


def _max_drawdown(close: pd.Series, n: int) -> float:
    vals = close.tail(n).astype(float)
    if len(vals) < 2:
        return 0.0
    peak = vals.cummax()
    dd = vals / peak - 1.0
    return abs(_to_float(dd.min(), 0.0))


def _recent_return(close: pd.Series, n: int) -> float:
    if len(close) <= n:
        return 0.0
    return _safe_div(close.iloc[-1], close.iloc[-n - 1], 1.0) - 1.0


def _volume_cv(vol: pd.Series, n: int) -> float:
    vals = pd.to_numeric(vol.tail(n), errors="coerce").dropna()
    if len(vals) < max(3, n // 3):
        return float("nan")
    return _safe_div(vals.std(ddof=0), vals.mean(), float("nan"))


def _resample_ohlcv(data: pd.DataFrame, rule: str) -> pd.DataFrame:
    if "date" not in data.columns and "trade_date" not in data.columns and "日期" not in data.columns:
        tmp = data.copy()
        tmp["_date"] = pd.date_range("2000-01-01", periods=len(tmp), freq="B")
        idx_col = "_date"
    else:
        tmp = data.copy()
        idx_col = "date" if "date" in tmp.columns else ("trade_date" if "trade_date" in tmp.columns else "日期")
    tmp[idx_col] = pd.to_datetime(tmp[idx_col], errors="coerce")
    tmp = tmp.dropna(subset=[idx_col]).set_index(idx_col)

    out = tmp.resample(rule).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    out = out.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    return out


def _grade(score: float, trigger_ready: bool, enough_data: bool) -> str:
    if not enough_data:
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


def _action_bias(score: float, trigger_ready: bool, enough_data: bool) -> str:
    if not enough_data:
        return "不参与：样本不足，禁止按指标交易。"
    if score < 60:
        return "普通观察：压缩和筹码锁定不充分。"
    if score < 75:
        return "跟踪观察：开始蓄势，但还没到高质量爆发前夜。"
    if score < 90:
        return "重点观察：已进入藏锋区，只等高质量触发或回踩确认。"
    if trigger_ready:
        return "临界观察：接近出鞘，但仍必须等放量突破、收盘站稳、RR合格。"
    return "高度压缩：质量高，但触发临界不足，不能提前买。"


def calculate_zangfeng(
    df: pd.DataFrame,
    *,
    pressure_price: Optional[float] = None,
    support_price: Optional[float] = None,
    trigger_price: Optional[float] = None,
    next_pressure_price: Optional[float] = None,
    config: Optional[ZangfengConfig] = None,
) -> Dict[str, Any]:
    """
    计算“藏锋”指标。

    参数：
        df: OHLCV日线数据，至少 open/high/low/close/volume。
        pressure_price: 当前最核心压力线，可为空。
        support_price: 真实交易防守位/结构支撑，可为空。
        trigger_price: 精确触发线，可为空；为空时使用近20日最高价作为临界线近似。
        next_pressure_price: 上方下一压力位，用于估算RR，可为空。
        config: 指标参数。

    返回：
        dict：score/grade/dimensions/metrics/flags/notes。
    """
    cfg = config or ZangfengConfig()
    data = _normalize_ohlcv(df)

    enough_data = len(data) >= cfg.min_rows
    flags: List[str] = []
    notes: List[str] = []

    if len(data) < max(25, cfg.mid_window + 5):
        return {
            "indicator": INDICATOR_NAME,
            "english_name": INDICATOR_ENGLISH_NAME,
            "version": INDICATOR_VERSION,
            "ok": False,
            "score": 0.0,
            "grade": "数据不足",
            "action_bias": "不参与：有效K线太少。",
            "dimensions": {
                "锋势": 0.0,
                "锋气": 0.0,
                "锋骨": 0.0,
                "锋意": 0.0,
                "出鞘准备": 0.0,
            },
            "flags": ["数据不足"],
            "notes": ["有效K线少于最低计算窗口，禁止解读为交易信号。"],
            "metrics": {"rows": int(len(data))},
        }

    close = data["close"]
    high = data["high"]
    low = data["low"]
    open_ = data["open"]
    volume = data["volume"]

    last_close = _to_float(close.iloc[-1])
    range_pct = (high - low) / close.replace(0, np.nan)
    body_pct = (close - open_).abs() / close.replace(0, np.nan)
    tr_pct = _true_range_pct(data)

    recent_range = _mean_last(range_pct, cfg.short_window)
    prior_range = _to_float(range_pct.iloc[-cfg.long_window:-cfg.short_window].mean(), recent_range)
    range_contract_ratio = _safe_div(recent_range, prior_range, 1.0)

    atr10 = _mean_last(tr_pct, cfg.short_window)
    atr30 = _mean_last(tr_pct, cfg.long_window)
    atr_contract_ratio = _safe_div(atr10, atr30, 1.0)

    recent_body = _mean_last(body_pct, cfg.short_window)
    prior_body = _to_float(body_pct.iloc[-cfg.long_window:-cfg.short_window].mean(), recent_body)
    body_contract_ratio = _safe_div(recent_body, prior_body, 1.0)

    close_std_pct = _safe_div(close.tail(cfg.short_window).std(ddof=0), close.tail(cfg.short_window).mean(), 0.0)
    low_slope = _slope_pct(low.tail(cfg.mid_window))
    center_slope = _slope_pct(close.tail(cfg.mid_window))

    price_score = 0.0
    price_score += _score_low_better(range_contract_ratio, good=0.70, bad=1.10, points=6.0)
    price_score += _score_low_better(atr_contract_ratio, good=0.72, bad=1.12, points=5.0)
    price_score += _score_low_better(body_contract_ratio, good=0.75, bad=1.15, points=4.0)
    price_score += _score_low_better(close_std_pct, good=0.022, bad=0.065, points=5.0)
    price_score += _score_high_better(low_slope, good=0.0020, bad=-0.0010, points=3.0)
    price_score += _score_high_better(center_slope, good=0.0015, bad=-0.0015, points=2.0)
    price_score = _clamp(price_score, 0.0, cfg.price_weight)

    cv10 = _volume_cv(volume, cfg.short_window)
    cv30 = _volume_cv(volume, cfg.long_window)
    cv_contract_ratio = _safe_div(cv10, cv30, 1.0)
    recent_vol_mean = _mean_last(volume, cfg.short_window)
    prior_vol_mean = _to_float(volume.iloc[-cfg.long_window:-cfg.short_window].mean(), recent_vol_mean)
    vol_level_ratio = _safe_div(recent_vol_mean, prior_vol_mean, 1.0)

    recent_vol = volume.tail(cfg.short_window).dropna()
    if len(recent_vol) >= 5 and recent_vol.median() > 0:
        flat_volume_ratio = float(((recent_vol / recent_vol.median()).between(0.85, 1.15)).mean())
    else:
        flat_volume_ratio = 0.0

    daily_ret = close.pct_change()
    up_mask = daily_ret > 0
    down_mask = daily_ret < 0
    up_vol_mean = _to_float(volume.tail(cfg.mid_window)[up_mask.tail(cfg.mid_window)].mean(), float("nan"))
    down_vol_mean = _to_float(volume.tail(cfg.mid_window)[down_mask.tail(cfg.mid_window)].mean(), float("nan"))
    down_up_vol_ratio = _safe_div(down_vol_mean, up_vol_mean, 1.0)

    pullback_days = (daily_ret.tail(cfg.short_window) < 0)
    pullback_vol_mean = _to_float(volume.tail(cfg.short_window)[pullback_days].mean(), recent_vol_mean)
    pullback_shrink_ratio = _safe_div(pullback_vol_mean, recent_vol_mean, 1.0)

    volume_score = 0.0
    volume_score += _score_low_better(cv_contract_ratio, good=0.68, bad=1.12, points=5.0)
    volume_score += _score_high_better(flat_volume_ratio, good=0.60, bad=0.20, points=4.0)
    volume_score += _score_inside_band(vol_level_ratio, low=0.55, high=1.45, points=4.0, soft=0.45)
    volume_score += _score_low_better(down_up_vol_ratio, good=0.82, bad=1.35, points=4.0)
    volume_score += _score_low_better(pullback_shrink_ratio, good=0.78, bad=1.15, points=3.0)
    volume_score = _clamp(volume_score, 0.0, cfg.volume_weight)

    rolling_high_20 = _to_float(high.tail(cfg.mid_window).max(), last_close)
    rolling_low_20 = _to_float(low.tail(cfg.mid_window).min(), last_close)
    platform_width_20 = _safe_div(rolling_high_20 - rolling_low_20, last_close, 0.0)
    max_dd20 = _max_drawdown(close, cfg.mid_window)

    vol_ratio_prev = volume / volume.shift(1)
    bearish_body = (close - open_) / open_.replace(0, np.nan)
    destructive_mask = (
        (bearish_body <= -0.045)
        & (vol_ratio_prev >= 1.55)
        & (((high - low) / close.replace(0, np.nan)) >= 0.055)
    )
    destructive_count_20 = int(destructive_mask.tail(cfg.mid_window).sum())

    pressure_price_f = _to_float(pressure_price)
    support_price_f = _to_float(support_price)
    trigger_price_f = _to_float(trigger_price)

    if math.isfinite(pressure_price_f) and pressure_price_f > 0:
        pressure_distance = _safe_div(pressure_price_f - last_close, last_close, 9.99)
        pressure_near_score = _score_inside_band(pressure_distance, low=-0.01, high=cfg.pressure_near_pct, points=5.0, soft=0.08)
    else:
        pressure_distance = float("nan")
        pressure_near_score = 2.0
        flags.append("缺核心压力线")
        notes.append("未传入pressure_price，结构临界分采用中性低分。")

    if math.isfinite(support_price_f) and support_price_f > 0 and support_price_f < last_close:
        defense_distance = _safe_div(last_close - support_price_f, last_close, 9.99)
        defense_score = _score_inside_band(defense_distance, low=cfg.defense_min_pct, high=cfg.defense_max_pct, points=4.0, soft=0.06)
    else:
        defense_distance = float("nan")
        defense_score = 1.0
        flags.append("缺真实防守位")
        notes.append("未传入有效support_price，防守位清晰度仅给保守分。")

    structure_score = 0.0
    structure_score += _score_low_better(platform_width_20, good=0.085, bad=0.22, points=6.0)
    structure_score += _score_low_better(max_dd20, good=0.075, bad=0.20, points=5.0)
    structure_score += _score_low_better(destructive_count_20, good=0.0, bad=3.0, points=5.0)
    structure_score += pressure_near_score
    structure_score += defense_score
    structure_score = _clamp(structure_score, 0.0, cfg.structure_weight)

    weekly = _resample_ohlcv(data, "W")
    monthly = _resample_ohlcv(data, "ME")

    weekly_score = 0.0
    weekly_range_ratio = float("nan")
    weekly_ma_spread = float("nan")
    if len(weekly) >= 12:
        wr = (weekly["high"] - weekly["low"]) / weekly["close"].replace(0, np.nan)
        weekly_range_ratio = _safe_div(wr.tail(4).mean(), wr.tail(12).head(8).mean(), 1.0)
        ma5 = weekly["close"].rolling(5).mean()
        ma10 = weekly["close"].rolling(10).mean()
        ma20 = weekly["close"].rolling(20).mean()
        ma_last = [ma5.iloc[-1], ma10.iloc[-1], ma20.iloc[-1]]
        if all(math.isfinite(_to_float(x)) for x in ma_last):
            weekly_ma_spread = _safe_div(max(ma_last) - min(ma_last), weekly["close"].iloc[-1], 0.0)
        weekly_low_slope = _slope_pct(weekly["low"].tail(8))
        weekly_score += _score_low_better(weekly_range_ratio, good=0.72, bad=1.15, points=5.0)
        weekly_score += _score_low_better(weekly_ma_spread, good=0.035, bad=0.12, points=3.0)
        weekly_score += _score_high_better(weekly_low_slope, good=0.0020, bad=-0.0020, points=2.0)
    else:
        flags.append("周线样本不足")
        weekly_score = 3.0

    monthly_score = 0.0
    monthly_range_ratio = float("nan")
    monthly_body_ratio = float("nan")
    if len(monthly) >= 8:
        mr = (monthly["high"] - monthly["low"]) / monthly["close"].replace(0, np.nan)
        monthly_range_ratio = _safe_div(mr.tail(3).mean(), mr.tail(8).head(5).mean(), 1.0)
        mb = (monthly["close"] - monthly["open"]).abs() / monthly["close"].replace(0, np.nan)
        monthly_body_ratio = _safe_div(mb.tail(3).mean(), mb.tail(8).head(5).mean(), 1.0)
        monthly_recent_return = _recent_return(monthly["close"], min(6, len(monthly) - 2))
        monthly_score += _score_low_better(monthly_range_ratio, good=0.78, bad=1.18, points=4.0)
        monthly_score += _score_low_better(monthly_body_ratio, good=0.80, bad=1.25, points=3.0)
        monthly_score += _score_low_better(monthly_recent_return, good=0.30, bad=0.90, points=3.0)
    else:
        flags.append("月线样本不足")
        monthly_score = 3.0

    cycle_score = _clamp(weekly_score + monthly_score, 0.0, cfg.cycle_weight)

    if not math.isfinite(trigger_price_f) or trigger_price_f <= 0:
        trigger_price_f = rolling_high_20
        flags.append("触发线使用20日高点近似")
    trigger_distance = _safe_div(trigger_price_f - last_close, last_close, 9.99)
    trigger_near_score = _score_inside_band(trigger_distance, low=-0.006, high=cfg.trigger_near_pct, points=3.0, soft=0.065)

    next_pressure_f = _to_float(next_pressure_price)
    if (
        math.isfinite(next_pressure_f)
        and math.isfinite(support_price_f)
        and next_pressure_f > last_close
        and 0 < support_price_f < last_close
    ):
        upside = next_pressure_f - last_close
        downside = last_close - support_price_f
        rr_ratio = _safe_div(upside, downside, 0.0)
        rr_score = _score_high_better(rr_ratio, good=2.2, bad=1.0, points=3.0)
    else:
        rr_ratio = float("nan")
        rr_score = 1.0
        flags.append("RR数据不足")

    return_20d = _recent_return(close, cfg.mid_window)
    overheat_score = _score_low_better(return_20d, good=0.12, bad=cfg.overheat_20d_pct, points=2.0)

    near_high_ratio = _safe_div(last_close, rolling_high_20, 0.0)
    near_high_score = _score_high_better(near_high_ratio, good=0.985, bad=0.92, points=2.0)

    trigger_score = trigger_near_score + rr_score + overheat_score + near_high_score
    trigger_score = _clamp(trigger_score, 0.0, cfg.trigger_weight)
    trigger_ready = trigger_distance <= cfg.trigger_near_pct and return_20d <= cfg.overheat_20d_pct

    raw_total = price_score + volume_score + structure_score + cycle_score + trigger_score
    score = _clamp(raw_total, 0.0, cfg.max_score)

    if not enough_data:
        flags.append("样本低于推荐阈值")
        notes.append(f"有效K线{len(data)}根，低于推荐阈值{cfg.min_rows}根，分数仅作参考。")

    if destructive_count_20 >= 2:
        flags.append("近20日破坏性放量阴线偏多")
    if return_20d > cfg.overheat_20d_pct:
        flags.append("近20日涨幅过热")
    if platform_width_20 > 0.22:
        flags.append("平台过宽")
    if close_std_pct <= 0.022 and volume_score >= 14:
        flags.append("价量同步压缩")
    if score >= 75 and trigger_ready:
        flags.append("藏锋临界")
    elif score >= 75:
        flags.append("藏锋未触发")

    dimensions = {
        "锋势": round(price_score, 2),
        "锋气": round(volume_score, 2),
        "锋骨": round(structure_score, 2),
        "锋意": round(cycle_score, 2),
        "出鞘准备": round(trigger_score, 2),
    }

    metrics = {
        "rows": int(len(data)),
        "last_close": round(last_close, 4),
        "range_contract_ratio": round(_to_float(range_contract_ratio, 0.0), 4),
        "atr_contract_ratio": round(_to_float(atr_contract_ratio, 0.0), 4),
        "body_contract_ratio": round(_to_float(body_contract_ratio, 0.0), 4),
        "close_std_pct_10d": round(_to_float(close_std_pct, 0.0), 4),
        "low_slope_20d": round(_to_float(low_slope, 0.0), 6),
        "center_slope_20d": round(_to_float(center_slope, 0.0), 6),
        "volume_cv_contract_ratio": round(_to_float(cv_contract_ratio, 0.0), 4),
        "flat_volume_ratio_10d": round(_to_float(flat_volume_ratio, 0.0), 4),
        "vol_level_ratio": round(_to_float(vol_level_ratio, 0.0), 4),
        "down_up_vol_ratio": round(_to_float(down_up_vol_ratio, 0.0), 4),
        "pullback_shrink_ratio": round(_to_float(pullback_shrink_ratio, 0.0), 4),
        "platform_width_20d": round(_to_float(platform_width_20, 0.0), 4),
        "max_drawdown_20d": round(_to_float(max_dd20, 0.0), 4),
        "destructive_count_20d": destructive_count_20,
        "pressure_distance": None if not math.isfinite(pressure_distance) else round(pressure_distance, 4),
        "defense_distance": None if not math.isfinite(defense_distance) else round(defense_distance, 4),
        "weekly_range_ratio": None if not math.isfinite(weekly_range_ratio) else round(weekly_range_ratio, 4),
        "weekly_ma_spread": None if not math.isfinite(weekly_ma_spread) else round(weekly_ma_spread, 4),
        "monthly_range_ratio": None if not math.isfinite(monthly_range_ratio) else round(monthly_range_ratio, 4),
        "monthly_body_ratio": None if not math.isfinite(monthly_body_ratio) else round(monthly_body_ratio, 4),
        "trigger_distance": round(_to_float(trigger_distance, 0.0), 4),
        "rr_ratio": None if not math.isfinite(rr_ratio) else round(rr_ratio, 4),
        "return_20d": round(_to_float(return_20d, 0.0), 4),
    }

    return {
        "indicator": INDICATOR_NAME,
        "english_name": INDICATOR_ENGLISH_NAME,
        "version": INDICATOR_VERSION,
        "ok": True,
        "score": round(score, 2),
        "grade": _grade(score, trigger_ready, enough_data),
        "action_bias": _action_bias(score, trigger_ready, enough_data),
        "dimensions": dimensions,
        "flags": list(dict.fromkeys(flags)),
        "notes": notes,
        "metrics": metrics,
    }


def self_check() -> Dict[str, Any]:
    """内置轻量自检。不会联网，不读环境变量，不写任何文件。"""
    rng = np.random.default_rng(7)

    n = 120
    base = 10 + np.cumsum(rng.normal(0.015, 0.08, n))
    compress_noise = np.concatenate([rng.normal(0, 0.22, 80), rng.normal(0, 0.045, 40)])
    close = base + compress_noise
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0.006, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0.006, 0.003, n)))
    volume = np.concatenate([rng.normal(1300000, 260000, 80), rng.normal(1250000, 70000, 40)])
    good_df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})
    good = calculate_zangfeng(
        good_df,
        pressure_price=float(good_df["close"].iloc[-1] * 1.025),
        support_price=float(good_df["close"].iloc[-1] * 0.94),
        trigger_price=float(good_df["close"].iloc[-1] * 1.018),
        next_pressure_price=float(good_df["close"].iloc[-1] * 1.20),
    )

    bad_close = 10 + np.cumsum(rng.normal(0.0, 0.45, n))
    bad_open = bad_close * (1 + rng.normal(0, 0.035, n))
    bad_high = np.maximum(bad_open, bad_close) * (1 + np.abs(rng.normal(0.05, 0.025, n)))
    bad_low = np.minimum(bad_open, bad_close) * (1 - np.abs(rng.normal(0.05, 0.025, n)))
    bad_volume = rng.lognormal(mean=14.0, sigma=0.75, size=n)
    bad_df = pd.DataFrame({"open": bad_open, "high": bad_high, "low": bad_low, "close": bad_close, "volume": bad_volume})
    bad = calculate_zangfeng(bad_df)

    short = calculate_zangfeng(good_df.tail(12))

    checks = {
        "good_score_positive": good["score"] >= 55,
        "bad_score_lower_than_good": bad["score"] + 10 <= good["score"],
        "short_sample_rejected": short["ok"] is False and short["grade"] == "数据不足",
        "score_range_good": 0 <= good["score"] <= 100,
        "score_range_bad": 0 <= bad["score"] <= 100,
        "dimension_sum_capped": sum(good["dimensions"].values()) <= 100.01,
        "required_keys_present": all(k in good for k in ["score", "grade", "dimensions", "metrics", "flags", "action_bias"]),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "good_score": good["score"],
        "good_grade": good["grade"],
        "bad_score": bad["score"],
        "bad_grade": bad["grade"],
        "short_grade": short["grade"],
    }


if __name__ == "__main__":
    import json

    print(json.dumps(self_check(), ensure_ascii=False, indent=2))
