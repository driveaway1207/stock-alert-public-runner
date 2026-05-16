# -*- coding: utf-8 -*-
"""
破界｜核心线突破独立选股模型 V1.0

定位：独立战法员工，不改一号员工主模型。
核心思想：
1）先找“多方法论共振核心线”，不是普通压力线；
2）再看突破前左侧蓄势：阶段对阶段量能/成交额中枢抬升、价格重心抬升、突破前夕平量稳定；
3）最后看日线高级别突破K、空间/RR、风险过滤。

默认复用仓库现有一号员工的数据入口：get_a_stock_list / get_daily_kline / send_telegram。
因此本文件可以单独放在 GitHub 仓库根目录运行，不篡改原 stock_alert.py。
"""

import os
import sys
import json
import math
import time
import argparse
import importlib.util
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
import pandas as pd


MODEL_VERSION = "破界V1.0｜核心线突破独立战法"
DEFAULT_BASE_MODEL_FILE = os.environ.get("破界_基础模型文件", os.environ.get("POJIE_BASE_MODEL_FILE", "stock_alert.py"))
OUTPUT_DIR = os.environ.get("破界_输出目录", os.environ.get("POJIE_OUTPUT_DIR", "outputs/pojie"))


def safe_float(x, default=0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, float) and math.isnan(x):
            return default
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def pct(a, b, default=0.0) -> float:
    a = safe_float(a)
    b = safe_float(b)
    if b == 0:
        return default
    return a / b - 1


def now_bj() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def load_base_module(path: str):
    """动态导入现有一号员工文件，只复用数据入口和Telegram，不改原文件。"""
    candidates = []
    if path:
        candidates.append(path)
    candidates.extend(["stock_alert.py", "stock_alert_v25_6_hvn_dedup.py", "stock_alert_v25_5.py"])
    selected = None
    for p in candidates:
        if os.path.exists(p):
            selected = p
            break
    if selected is None:
        raise FileNotFoundError(f"找不到基础模型文件，已尝试：{candidates}")
    spec = importlib.util.spec_from_file_location("base_stock_model_for_pojie", selected)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    print(f"破界：已加载基础模型文件 {selected}")
    return mod


def normalize_kline(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    if df is None or df.empty:
        return None
    d = df.copy()
    if "date" not in d.columns:
        return None
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c not in d.columns:
            if c == "amount":
                d[c] = 0.0
            else:
                return None
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    d = d[(d["open"] > 0) & (d["high"] > 0) & (d["low"] > 0) & (d["close"] > 0)]
    d = d.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if len(d) < 120:
        return None
    d["preclose"] = d["close"].shift(1)
    d["pct_chg"] = d["close"].pct_change().fillna(0) * 100
    d["body"] = d["close"] - d["open"]
    d["body_abs"] = d["body"].abs()
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    d["close_pos"] = ((d["close"] - d["low"]) / rng).fillna(0.5)
    d["upper_shadow_ratio"] = ((d["high"] - d[["open", "close"]].max(axis=1)) / rng).fillna(0)
    d["lower_shadow_ratio"] = ((d[["open", "close"]].min(axis=1) - d["low"]) / rng).fillna(0)
    d["body_top"] = d[["open", "close"]].max(axis=1)
    d["body_bottom"] = d[["open", "close"]].min(axis=1)
    d["body_mid"] = (d["body_top"] + d["body_bottom"]) / 2
    d["ma20"] = d["close"].rolling(20).mean()
    d["ma60"] = d["close"].rolling(60).mean()
    d["ma120"] = d["close"].rolling(120).mean()
    d["vol_ma20"] = d["volume"].rolling(20).mean()
    d["vr1"] = d["volume"] / d["volume"].shift(1).replace(0, np.nan)
    d["atr20_pct"] = ((d["high"] - d["low"]).rolling(20).mean() / d["close"].replace(0, np.nan)).fillna(0)
    return d


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    d = df.copy().set_index("date")
    out = pd.DataFrame()
    out["open"] = d["open"].resample(rule).first()
    out["high"] = d["high"].resample(rule).max()
    out["low"] = d["low"].resample(rule).min()
    out["close"] = d["close"].resample(rule).last()
    out["volume"] = d["volume"].resample(rule).sum()
    out["amount"] = d["amount"].resample(rule).sum()
    out = out.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index()
    return normalize_kline(out) if len(out) >= 20 else out


def detect_gap_candidates(d: pd.DataFrame, timeframe: str) -> List[Dict[str, Any]]:
    res = []
    for i in range(1, len(d)):
        pre_high = safe_float(d.loc[i - 1, "high"])
        pre_low = safe_float(d.loc[i - 1, "low"])
        low = safe_float(d.loc[i, "low"])
        high = safe_float(d.loc[i, "high"])
        date = str(pd.to_datetime(d.loc[i, "date"]).date())
        if low > pre_high * 1.003:
            res.append({"price": pre_high, "source": "向上跳空缺口下沿/前高", "timeframe": timeframe, "date": date, "weight": 2.4})
            res.append({"price": low, "source": "向上跳空缺口上沿/当日低", "timeframe": timeframe, "date": date, "weight": 2.0})
        if high < pre_low * 0.997:
            res.append({"price": pre_low, "source": "向下跳空缺口上沿/前低", "timeframe": timeframe, "date": date, "weight": 2.4})
            res.append({"price": high, "source": "向下跳空缺口下沿/当日高", "timeframe": timeframe, "date": date, "weight": 2.0})
    return res


def is_downtrend(d: pd.DataFrame, i: int, n: int = 5) -> bool:
    if i < n:
        return False
    seg = d.iloc[i - n:i]
    if seg.empty:
        return False
    return safe_float(seg["close"].iloc[-1]) < safe_float(seg["close"].iloc[0]) * 0.96 and int((seg["close"] < seg["open"]).sum()) >= max(2, n // 2)


def detect_semantic_coreline_candidates(d: pd.DataFrame, timeframe: str) -> List[Dict[str, Any]]:
    """核心：不是普通影线/实体共振，而是结构语义候选线。"""
    res = []
    if d is None or len(d) < 25:
        return res
    d = d.copy().reset_index(drop=True)
    vol_ma = d["volume"].rolling(10).mean()
    for i in range(6, len(d)):
        row = d.iloc[i]
        date = str(pd.to_datetime(row["date"]).date())
        o, h, l, c, v = [safe_float(row[x]) for x in ["open", "high", "low", "close", "volume"]]
        if c <= 0:
            continue
        body_pct = abs(c - o) / c
        rng_pct = (h - l) / c if c else 0
        vol_ratio = v / safe_float(vol_ma.iloc[i], v) if safe_float(vol_ma.iloc[i], 0) > 0 else 1
        up = c > o
        down = c < o

        # 1）连续下跌中的小阳线：下跌中继/弱修复成本线。
        if up and is_downtrend(d, i, 5) and body_pct <= 0.055 and rng_pct <= 0.12:
            res.append({"price": min(o, c), "source": "下跌中继小阳线实底", "timeframe": timeframe, "date": date, "weight": 2.2})
            res.append({"price": max(o, c), "source": "下跌中继小阳线实顶", "timeframe": timeframe, "date": date, "weight": 1.8})
            if l < min(o, c):
                res.append({"price": l, "source": "下跌中继小阳线虚底", "timeframe": timeframe, "date": date, "weight": 1.4})

        # 2）大阴线后高开阳线：修复资金实底/虚底。
        pre = d.iloc[i - 1]
        pre_o, pre_c = safe_float(pre["open"]), safe_float(pre["close"])
        pre_body_drop = (pre_o - pre_c) / pre_o if pre_o > 0 else 0
        if pre_body_drop >= 0.06 and up and o > pre_c * 1.003:
            res.append({"price": min(o, c), "source": "大阴后高开阳线实底", "timeframe": timeframe, "date": date, "weight": 2.8})
            res.append({"price": l, "source": "大阴后高开阳线虚底", "timeframe": timeframe, "date": date, "weight": 2.2})

        # 3）连续下跌后反抽最高点/上影线供应。
        if is_downtrend(d, i, 8) and safe_float(row.get("upper_shadow_ratio", 0)) >= 0.28:
            res.append({"price": h, "source": "连续下跌后反抽最高点/上影线", "timeframe": timeframe, "date": date, "weight": 2.6})
        if safe_float(row.get("upper_shadow_ratio", 0)) >= 0.38 and vol_ratio >= 1.1:
            res.append({"price": h, "source": "放量上影线反压", "timeframe": timeframe, "date": date, "weight": 2.0})
        if safe_float(row.get("lower_shadow_ratio", 0)) >= 0.38 and vol_ratio >= 1.1:
            res.append({"price": l, "source": "放量下影线承接", "timeframe": timeframe, "date": date, "weight": 1.8})

        # 4）大阳/大阴实体顶底：大换手供需边界。
        if body_pct >= 0.065 or vol_ratio >= 1.8:
            if up:
                res.append({"price": max(o, c), "source": "大阳线实顶", "timeframe": timeframe, "date": date, "weight": 1.8})
                res.append({"price": min(o, c), "source": "大阳线实底", "timeframe": timeframe, "date": date, "weight": 1.8})
            elif down:
                res.append({"price": max(o, c), "source": "大阴线实顶", "timeframe": timeframe, "date": date, "weight": 2.2})
                res.append({"price": min(o, c), "source": "大阴线实底", "timeframe": timeframe, "date": date, "weight": 2.4})

        # 5）最大量/次大量K线关键位。
        if i >= 30:
            rank = d["volume"].iloc[max(0, i - 120):i + 1].rank(pct=True).iloc[-1]
            if rank >= 0.96 and up and body_pct >= 0.025:
                res.append({"price": h, "source": "阶段最大量阳K高点", "timeframe": timeframe, "date": date, "weight": 2.6})
                res.append({"price": min(o, c), "source": "阶段最大量阳K实底", "timeframe": timeframe, "date": date, "weight": 2.4})
    res.extend(detect_gap_candidates(d, timeframe))
    return [x for x in res if safe_float(x.get("price")) > 0]


def cluster_corelines(candidates: List[Dict[str, Any]], current_price: float, atr_pct: float) -> List[Dict[str, Any]]:
    if not candidates:
        return []
    tol_pct = max(0.006, min(0.025, atr_pct * 0.45 if atr_pct > 0 else 0.010))
    cands = sorted(candidates, key=lambda x: safe_float(x["price"]))
    clusters = []
    current = []
    for c in cands:
        price = safe_float(c["price"])
        if not current:
            current.append(c)
            continue
        center = np.average([safe_float(x["price"]) for x in current], weights=[safe_float(x.get("weight", 1)) for x in current])
        if abs(price / center - 1) <= tol_pct:
            current.append(c)
        else:
            clusters.append(current)
            current = [c]
    if current:
        clusters.append(current)

    zones = []
    tf_weight = {"Y": 5.0, "Q": 4.0, "M": 3.2, "W": 2.2, "D": 1.0}
    semantic_keys = ["下跌中继", "大阴后", "反抽", "缺口", "大阳", "大阴", "上影", "下影", "最大量"]
    for mem in clusters:
        prices = [safe_float(x["price"]) for x in mem]
        weights = [safe_float(x.get("weight", 1)) * tf_weight.get(str(x.get("timeframe", "D")), 1.0) for x in mem]
        center = float(np.average(prices, weights=weights))
        low = min(prices)
        high = max(prices)
        sources = [str(x.get("source", "")) for x in mem]
        tfs = sorted(set(str(x.get("timeframe", "")) for x in mem))
        semantic_count = sum(1 for k in semantic_keys if any(k in s for s in sources))
        source_types = len(set(sources))
        reaction_count = len(mem)
        time_span = 0
        try:
            dates = pd.to_datetime([x.get("date") for x in mem], errors="coerce").dropna()
            if len(dates) >= 2:
                time_span = int((dates.max() - dates.min()).days)
        except Exception:
            time_span = 0
        multi_tf_score = min(25, sum(tf_weight.get(tf, 1) for tf in tfs) * 3.2)
        semantic_score = min(28, semantic_count * 4.5 + source_types * 1.4)
        reaction_score = min(22, reaction_count * 2.0)
        gap_bonus = 7 if any("缺口" in s for s in sources) else 0
        bigk_bonus = 5 if any(("大阳" in s or "大阴" in s or "最大量" in s) for s in sources) else 0
        stability_score = min(10, time_span / 90) if time_span else 0
        dist = abs(center / current_price - 1) if current_price > 0 else 99
        usability = 5 if 0.01 <= dist <= 0.35 else (2 if dist < 0.5 else -4)
        score = multi_tf_score + semantic_score + reaction_score + gap_bonus + bigk_bonus + stability_score + usability
        score = max(0, min(100, score))
        if score >= 85:
            level = "S"
        elif score >= 75:
            level = "A"
        elif score >= 62:
            level = "B"
        else:
            level = "C"
        zones.append({
            "center": center, "low": low, "high": high,
            "score": float(score), "level": level,
            "members": mem, "sources": sources[:18], "timeframes": tfs,
            "reaction_count": reaction_count, "source_types": source_types,
            "semantic_count": semantic_count, "time_span_days": time_span,
            "distance_to_current": center / current_price - 1 if current_price > 0 else 0,
        })
    return sorted(zones, key=lambda z: z["score"], reverse=True)


def build_timeframes(daily: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    # 年线/季线样本少时仍可输出，但评分会自然降权。
    return {
        "D": daily.tail(260).reset_index(drop=True),
        "W": resample_ohlcv(daily, "W-FRI"),
        "M": resample_ohlcv(daily, "ME"),
        "Q": resample_ohlcv(daily, "QE"),
        "Y": resample_ohlcv(daily, "YE"),
    }


def find_coreline_zones(daily: pd.DataFrame) -> List[Dict[str, Any]]:
    tfs = build_timeframes(daily)
    candidates: List[Dict[str, Any]] = []
    for tf, df in tfs.items():
        if df is None or len(df) < 15:
            continue
        lookback = {"D": 220, "W": 180, "M": 120, "Q": 80, "Y": 40}.get(tf, 100)
        candidates.extend(detect_semantic_coreline_candidates(df.tail(lookback).reset_index(drop=True), tf))
    cur = safe_float(daily["close"].iloc[-1])
    atr = safe_float(daily["atr20_pct"].iloc[-1], 0.02)
    return cluster_corelines(candidates, cur, atr)


def segment_metrics(seg: pd.DataFrame) -> Dict[str, float]:
    if seg is None or len(seg) < 5:
        return {"price_center": 0, "low_center": 0, "vol_mean": 0, "amount_mean": 0, "vol_cv": 9, "atr_pct": 9, "down_big": 9, "flat_ratio": 0}
    s = seg.copy()
    vol = pd.to_numeric(s["volume"], errors="coerce").replace(0, np.nan)
    amount = pd.to_numeric(s.get("amount", s["volume"] * s["close"]), errors="coerce").replace(0, np.nan)
    price_center = safe_float(s["close"].median())
    low_center = safe_float(s["low"].median())
    vol_mean = safe_float(vol.mean())
    amount_mean = safe_float(amount.mean())
    vol_cv = safe_float(vol.std() / vol_mean, 9) if vol_mean > 0 else 9
    atr_pct = safe_float(((s["high"] - s["low"]) / s["close"].replace(0, np.nan)).mean(), 9)
    down_big = int(((s["close"] < s["open"]) & (s["pct_chg"] <= -3.5) & (s["volume"] > s["volume"].rolling(10).mean() * 1.3)).sum())
    vr = vol / vol.shift(1)
    flat_ratio = safe_float(((vr >= 0.92) & (vr <= 1.08)).mean(), 0)
    return {"price_center": price_center, "low_center": low_center, "vol_mean": vol_mean, "amount_mean": amount_mean, "vol_cv": vol_cv, "atr_pct": atr_pct, "down_big": down_big, "flat_ratio": flat_ratio}


def score_left_buildup(d: pd.DataFrame, coreline: Dict[str, Any]) -> Dict[str, Any]:
    """突破前蓄势：阶段对阶段抬升 + 阶段内平量稳定。"""
    if d is None or len(d) < 90:
        return {"score": 0.0, "level": "不足", "reasons": ["样本不足"]}
    current_price = safe_float(d["close"].iloc[-1])
    line = safe_float(coreline["center"])
    # 只看突破前约90日，拆成旧平台/中平台/临界平台三段。
    pre = d.iloc[-91:-1].copy() if len(d) >= 91 else d.iloc[:-1].copy()
    if len(pre) < 45:
        return {"score": 0.0, "level": "不足", "reasons": ["突破前样本不足"]}
    parts = np.array_split(pre, 3)
    m1, m2, m3 = [segment_metrics(pd.DataFrame(x)) for x in parts]
    score = 0.0
    reasons = []

    # 阶段对阶段：量能/成交额中枢抬升，不是今天比昨天。
    if m2["vol_mean"] > m1["vol_mean"] * 1.03 and m3["vol_mean"] > m2["vol_mean"] * 0.98:
        score += 5
        reasons.append("阶段量能中枢较前一阶段温和抬升")
    if m2["amount_mean"] > m1["amount_mean"] * 1.05 and m3["amount_mean"] > m2["amount_mean"] * 0.98:
        score += 5
        reasons.append("阶段成交额中枢抬升，资金成本上移")
    if m2["price_center"] > m1["price_center"] * 1.01 and m3["price_center"] > m2["price_center"] * 1.005:
        score += 6
        reasons.append("价格收盘中枢阶段性抬升")
    if m2["low_center"] > m1["low_center"] * 1.005 and m3["low_center"] > m2["low_center"] * 1.002:
        score += 4
        reasons.append("阶段低点/回撤重心逐步抬高")

    # 爆发前夕：日与日之间平量、量能稳定，不是天天爆量。
    if m3["vol_cv"] < m2["vol_cv"] * 0.88 or m3["vol_cv"] <= 0.32:
        score += 5
        reasons.append("突破前夕量能波动率下降，平稳度提高")
    if m3["flat_ratio"] >= 0.38:
        score += 4
        reasons.append("临界平台平量比例较高，日间量能稳定")
    if m3["atr_pct"] < m2["atr_pct"] * 0.88 or m3["atr_pct"] <= 0.035:
        score += 4
        reasons.append("突破前价格波动压缩")
    if m3["down_big"] <= 1:
        score += 3
        reasons.append("临界平台放量长阴少，供应压力受控")

    # 核心线下方吸收：靠近但不被打下来。
    near = pre.tail(30)
    if line > 0 and not near.empty:
        close_near_ratio = safe_float(((near["close"] >= line * 0.90) & (near["close"] <= line * 1.015)).mean())
        fail_drop = int(((near["high"] >= line * 0.985) & (near["close"] < line * 0.94)).sum())
        if close_near_ratio >= 0.35:
            score += 4
            reasons.append("核心线下方/附近停留充分，有吸收过程")
        if fail_drop <= 2:
            score += 2
            reasons.append("多次靠近核心线后未出现明显大跌回落")

    # 攻击记忆：7%大阳、倍量、跳空等，但要避免高位乱炸。
    pre60 = d.tail(60)
    big7 = int(((pre60["pct_chg"] >= 7) & (pre60["close"] > pre60["open"]) & (pre60["close_pos"] >= 0.60)).sum())
    standard_vol = int(((pre60["vr1"] >= 1.8) & (pre60["vr1"] <= 2.5) & (pre60["close"] > pre60["open"])).sum())
    up_gap = int((pre60["low"] > pre60["high"].shift(1) * 1.003).sum())
    if big7 >= 1:
        score += 2
        reasons.append(f"近60日有{big7}次7%以上大阳攻击记忆")
    if standard_vol >= 1:
        score += 2
        reasons.append(f"近60日有{standard_vol}次标准倍量阳线")
    if up_gap >= 1:
        score += 1
        reasons.append(f"近60日有{up_gap}次向上跳空攻击记忆")

    # 防止死量横盘误判为好平量。
    if m3["vol_mean"] < m1["vol_mean"] * 0.72 and m3["amount_mean"] < m1["amount_mean"] * 0.75:
        score -= 7
        reasons.append("临界平台量能/成交额中枢明显下降，疑似冷门死量")
    if current_price > line * 1.12:
        score -= 4
        reasons.append("当前已明显远离核心线，突破后追高风险上升")

    score = max(0.0, min(35.0, score))
    level = "强" if score >= 27 else ("较好" if score >= 21 else ("一般" if score >= 14 else "弱"))
    return {"score": float(score), "level": level, "reasons": reasons[:10], "stage_metrics": {"旧平台": m1, "中平台": m2, "临界平台": m3}}


def detect_breakout(d: pd.DataFrame, zone: Dict[str, Any]) -> Dict[str, Any]:
    row = d.iloc[-1]
    pre = d.iloc[-2] if len(d) >= 2 else row
    close, open_, high, low = [safe_float(row[x]) for x in ["close", "open", "high", "low"]]
    zhigh = safe_float(zone["high"])
    zcenter = safe_float(zone["center"])
    zlow = safe_float(zone["low"])
    body_top = max(open_, close)
    body_bottom = min(open_, close)
    body_above_ratio = 0.0
    if body_top > body_bottom and zhigh > 0:
        body_above_ratio = max(0.0, min(1.0, (body_top - max(body_bottom, zhigh)) / (body_top - body_bottom)))
    close_pos = safe_float(row.get("close_pos", 0.5))
    upper = safe_float(row.get("upper_shadow_ratio", 0))
    vr1 = safe_float(row.get("vr1", 0))
    pct_chg = safe_float(row.get("pct_chg", 0))
    volr = safe_float(row["volume"] / safe_float(row.get("vol_ma20", 0), row["volume"]), 1)
    gap_break = safe_float(row["low"]) > safe_float(pre["high"]) * 1.003 and close > zhigh
    effective = close > zhigh * 1.003 and body_above_ratio >= 0.45 and close_pos >= 0.68 and upper <= 0.42
    score = 0.0
    reasons = []
    if close > zhigh * 1.003:
        score += 8; reasons.append("收盘有效站上核心线高沿")
    if body_above_ratio >= 0.55:
        score += 7; reasons.append("实体大部分位于核心线上方")
    elif body_above_ratio >= 0.35:
        score += 4; reasons.append("实体部分站上核心线")
    if close_pos >= 0.80:
        score += 5; reasons.append("收盘位置强")
    elif close_pos >= 0.68:
        score += 3; reasons.append("收盘位置尚可")
    if upper <= 0.25:
        score += 4; reasons.append("上影线短，非明显冲高回落")
    elif upper > 0.50:
        score -= 6; reasons.append("上影线较长，存在假突破风险")
    if 1.8 <= vr1 <= 2.5:
        score += 6; reasons.append("标准倍量突破")
    elif 1.3 <= vr1 <= 3.2 or 1.2 <= volr <= 3.8:
        score += 4; reasons.append("健康放量突破")
    elif vr1 > 5 or volr > 6:
        score -= 5; reasons.append("极端爆量，需防分歧派发")
    if gap_break:
        score += 4; reasons.append("跳空越过核心线")
    if pct_chg >= 3 and close > open_:
        score += 3; reasons.append("突破K为有效阳线")
    if close > zcenter and close <= zhigh * 1.003:
        reasons.append("进入核心线区域但未完全突破高沿")
    if high >= zhigh and close < zcenter:
        score -= 8; reasons.append("盘中冲线但收盘回落，假突破风险")
    score = max(0.0, min(30.0, score))
    return {"effective": bool(effective), "score": float(score), "reasons": reasons[:10], "body_above_ratio": body_above_ratio, "close_pos": close_pos, "vr1": vr1, "volr": volr}


def calc_space_rr(d: pd.DataFrame, zone: Dict[str, Any], all_zones: List[Dict[str, Any]]) -> Dict[str, Any]:
    close = safe_float(d["close"].iloc[-1])
    zlow = safe_float(zone["low"])
    zhigh = safe_float(zone["high"])
    if close <= 0:
        return {"score": 0, "rr": 0, "defense": 0, "next_pressure": 0, "space": 0, "reasons": []}
    defense_candidates = [zlow * 0.985, safe_float(d["body_bottom"].iloc[-1]) * 0.99, safe_float(d["low"].tail(10).min()) * 0.995]
    defense = max([x for x in defense_candidates if x > 0], default=zlow * 0.985)
    risk = max(0.001, close / defense - 1) if defense > 0 and close > defense else 0.08
    upper_zones = [z for z in all_zones if safe_float(z["center"]) > close * 1.03]
    next_pressure = min([safe_float(z["center"]) for z in upper_zones], default=0.0)
    if next_pressure <= 0:
        # 用近250日高点作为保守空间代理。
        next_pressure = safe_float(d["high"].tail(250).max())
        if next_pressure <= close * 1.03:
            next_pressure = close * 1.18
    space = max(0.0, next_pressure / close - 1)
    rr = space / risk if risk > 0 else 0
    score = 0.0
    reasons = []
    if space >= 0.18:
        score += 4; reasons.append("上方空间较大")
    elif space >= 0.10:
        score += 2; reasons.append("上方仍有一定空间")
    else:
        score -= 3; reasons.append("上方空间偏小")
    if rr >= 2.5:
        score += 5; reasons.append("风险收益比优秀")
    elif rr >= 1.8:
        score += 3; reasons.append("风险收益比可接受")
    else:
        score -= 4; reasons.append("风险收益比不足")
    if risk <= 0.06:
        score += 2; reasons.append("距离防守位较近")
    elif risk > 0.11:
        score -= 3; reasons.append("距离防守位偏远")
    return {"score": float(max(-8, min(12, score))), "rr": float(rr), "defense": float(defense), "next_pressure": float(next_pressure), "space": float(space), "risk": float(risk), "reasons": reasons}


def risk_filter(d: pd.DataFrame, row_meta: Dict[str, Any]) -> Dict[str, Any]:
    close = safe_float(d["close"].iloc[-1])
    high250 = safe_float(d["high"].tail(250).max())
    low250 = safe_float(d["low"].tail(250).min())
    long_pos = (close - low250) / (high250 - low250) if high250 > low250 else 0.5
    bias20 = pct(close, safe_float(d["ma20"].iloc[-1]))
    amount = safe_float(d["amount"].iloc[-1])
    penalty = 0.0
    reasons = []
    hard = False
    if amount > 0 and amount < float(os.environ.get("破界_最低成交额", "50000000")):
        penalty -= 8; reasons.append("成交额偏低，实盘执行风险高")
    if long_pos >= 0.86:
        penalty -= 8; reasons.append("250日位置过高，追高风险")
    elif long_pos >= 0.76:
        penalty -= 4; reasons.append("250日位置偏高")
    if bias20 > 0.18:
        penalty -= 6; reasons.append("20日乖离偏高")
    if safe_float(d["vr1"].iloc[-1]) > 5:
        penalty -= 5; reasons.append("单日量能极端放大")
    name = str(row_meta.get("名称", row_meta.get("name", "")))
    if "ST" in name or "退" in name:
        hard = True; penalty -= 50; reasons.append("ST/退市风险标的剔除")
    return {"penalty": float(penalty), "hard_exclude": hard, "reasons": reasons, "long_pos_250": float(long_pos), "bias20": float(bias20)}


def scan_one(symbol: str, name: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    d = normalize_kline(df)
    if d is None or len(d) < 180:
        return None
    zones = find_coreline_zones(d)
    if not zones:
        return None
    close = safe_float(d["close"].iloc[-1])
    # 破界关注：当前价附近或刚突破的S/A/B+核心线。
    valid_zones = [z for z in zones if z["level"] in ["S", "A", "B"] and safe_float(z["center"]) <= close * 1.10 and safe_float(z["center"]) >= close * 0.72]
    if not valid_zones:
        valid_zones = zones[:1]
    best_result = None
    for z in valid_zones[:5]:
        core_score = safe_float(z["score"])
        buildup = score_left_buildup(d, z)
        breakout = detect_breakout(d, z)
        rr = calc_space_rr(d, z, zones)
        risk = risk_filter(d, {"名称": name})
        if risk["hard_exclude"]:
            continue
        total = core_score * 0.30 + safe_float(buildup["score"]) * (25 / 35) + safe_float(breakout["score"]) * (25 / 30) + safe_float(rr["score"]) + safe_float(risk["penalty"])
        # 未有效突破只能观察，不能高分正式入选。
        if not breakout["effective"]:
            total = min(total, 72)
        total = max(0, min(100, total))
        if total >= 88 and breakout["effective"] and z["level"] == "S" and rr["rr"] >= 2.0:
            level = "S"
        elif total >= 80 and breakout["effective"]:
            level = "A"
        elif total >= 70:
            level = "B"
        else:
            level = "C"
        result = {
            "employee": "破界",
            "version": MODEL_VERSION,
            "symbol": symbol,
            "name": name,
            "date": str(pd.to_datetime(d["date"].iloc[-1]).date()),
            "close": close,
            "signal_level": level,
            "score": round(float(total), 2),
            "coreline": round(safe_float(z["center"]), 3),
            "coreline_zone": [round(safe_float(z["low"]), 3), round(safe_float(z["high"]), 3)],
            "coreline_level": z["level"],
            "coreline_score": round(core_score, 2),
            "coreline_sources": z["sources"][:12],
            "coreline_timeframes": z["timeframes"],
            "buildup_score": round(safe_float(buildup["score"]), 2),
            "buildup_level": buildup["level"],
            "buildup_reasons": buildup["reasons"],
            "breakout_effective": bool(breakout["effective"]),
            "breakout_score": round(safe_float(breakout["score"]), 2),
            "breakout_reasons": breakout["reasons"],
            "defense_price": round(safe_float(rr["defense"]), 3),
            "next_pressure": round(safe_float(rr["next_pressure"]), 3),
            "space": round(safe_float(rr["space"]), 4),
            "risk_distance": round(safe_float(rr["risk"]), 4),
            "rr": round(safe_float(rr["rr"]), 2),
            "space_reasons": rr["reasons"],
            "risk_reasons": risk["reasons"],
            "long_pos_250": round(safe_float(risk["long_pos_250"]), 4),
            "bias20": round(safe_float(risk["bias20"]), 4),
            "confirm_condition": "次日/后续不快速跌回核心线高沿下方，回踩核心线或突破K实体中位附近缩量止跌再转强。",
            "giveup_condition": "放量长上影回落到核心线下方，或跌破突破K实底/防守位，破界失败。",
        }
        if best_result is None or result["score"] > best_result["score"]:
            best_result = result
    return best_result


def build_report(results: List[Dict[str, Any]], scanned: int, failed: int) -> str:
    lines = []
    lines.append(f"【破界｜核心线突破独立战法】")
    lines.append(f"生成时间：{now_bj()}")
    lines.append(f"扫描：{scanned}只，失败/无数据：{failed}只，候选：{len(results)}只")
    lines.append("")
    if not results:
        lines.append("今日暂无破界候选。")
        return "\n".join(lines)
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['symbol']} {r['name']}｜{r['signal_level']}｜{r['score']}分｜收盘{r['close']}")
        lines.append(f"   核心线：{r['coreline']} 区间{r['coreline_zone']}｜{r['coreline_level']}级｜{r['coreline_score']}分｜周期{','.join(r['coreline_timeframes'])}")
        lines.append(f"   线源：{'；'.join(r['coreline_sources'][:5])}")
        lines.append(f"   蓄势：{r['buildup_level']} {r['buildup_score']}分｜{'；'.join(r['buildup_reasons'][:3])}")
        lines.append(f"   突破：{'有效' if r['breakout_effective'] else '观察'} {r['breakout_score']}分｜{'；'.join(r['breakout_reasons'][:3])}")
        lines.append(f"   防守：{r['defense_price']}｜上压：{r['next_pressure']}｜空间{r['space']:.1%}｜风险{r['risk_distance']:.1%}｜RR={r['rr']}")
        if r['risk_reasons']:
            lines.append(f"   风险：{'；'.join(r['risk_reasons'][:3])}")
        lines.append(f"   确认：{r['confirm_condition']}")
        lines.append(f"   放弃：{r['giveup_condition']}")
        lines.append("")
    return "\n".join(lines)



# ========================= 数据入口增强补丁：参考一号员工数据层 =========================
# 目标：破界战法仍然独立运行，但数据入口更稳：
# 1）优先复用一号员工的 get_a_stock_list / get_daily_kline；
# 2）股票池为空时，不再 raise 导致 GitHub Actions 失败；
# 3）自动尝试一号员工的缓存股票池、AkShare 股票池、本地 kline_cache 反推股票池；
# 4）最后启用应急股票池，保证流程能跑完并产出诊断报告；
# 5）K线优先读本地缓存，再走一号员工入口，再走 AkShare 直接兜底。

VALID_STOCK_PREFIXES_POJIE = (
    "sh.600", "sh.601", "sh.603", "sh.605", "sh.688",
    "sz.000", "sz.001", "sz.002", "sz.003", "sz.300", "sz.301",
)


def plain_code_from_any(value: Any) -> str:
    text = str(value).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:].zfill(6)
    return ""


def bs_code_from_plain(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("600", "601", "603", "605", "688")):
        return "sh." + code
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz." + code
    return ""


def normalize_stock_list_df(df: pd.DataFrame, source: str = "unknown") -> pd.DataFrame:
    """统一股票池字段为：代码、名称、bs_code。"""
    empty = pd.DataFrame(columns=["代码", "名称", "bs_code"])
    if df is None or getattr(df, "empty", True):
        return empty
    d = df.copy()

    # BaoStock 常见字段：code / code_name
    if "code" in d.columns and ("代码" not in d.columns):
        d["bs_code"] = d["code"].astype(str)
        d["代码"] = d["bs_code"].apply(plain_code_from_any)
        if "code_name" in d.columns:
            d["名称"] = d["code_name"].astype(str)
        elif "name" in d.columns:
            d["名称"] = d["name"].astype(str)
        else:
            d["名称"] = ""
    else:
        code_col = None
        name_col = None
        bs_col = None
        for c in ["代码", "股票代码", "symbol", "证券代码", "code"]:
            if c in d.columns:
                code_col = c
                break
        for c in ["名称", "股票名称", "股票简称", "name", "code_name"]:
            if c in d.columns:
                name_col = c
                break
        for c in ["bs_code", "baostock_code"]:
            if c in d.columns:
                bs_col = c
                break
        if code_col is None and bs_col is None:
            print(f"破界股票池标准化失败：source={source} columns={list(d.columns)[:20]}")
            return empty
        if code_col is not None:
            d["代码"] = d[code_col].apply(plain_code_from_any)
        else:
            d["代码"] = d[bs_col].apply(plain_code_from_any)
        d["名称"] = d[name_col].astype(str) if name_col else ""
        if bs_col:
            d["bs_code"] = d[bs_col].astype(str)
        else:
            d["bs_code"] = d["代码"].apply(bs_code_from_plain)

    d["代码"] = d["代码"].astype(str).str.zfill(6)
    d["名称"] = d["名称"].fillna("").astype(str)
    d["bs_code"] = d["bs_code"].fillna("").astype(str)
    bad_bs = ~d["bs_code"].str.startswith(("sh.", "sz."), na=False)
    if bad_bs.any():
        d.loc[bad_bs, "bs_code"] = d.loc[bad_bs, "代码"].apply(bs_code_from_plain)
    d = d[d["bs_code"].str.startswith(VALID_STOCK_PREFIXES_POJIE, na=False)].copy()
    d = d[~d["名称"].str.contains("ST|\\*ST|退", regex=True, na=False)].copy()
    d = d.drop_duplicates(subset=["代码"])
    return d[["代码", "名称", "bs_code"]].reset_index(drop=True)


def stock_list_from_local_kline_cache() -> pd.DataFrame:
    """没有股票池接口时，从 kline_cache 里的CSV文件名反推股票池。"""
    roots = []
    for env_key in ["FULL_HISTORY_CACHE_DIR", "CACHE_DIR"]:
        v = os.environ.get(env_key, "").strip()
        if v:
            roots.append(v)
    roots.extend(["kline_cache", "K线缓存", "kline_cache/base", "kline_cache/deep"])

    rows = []
    seen = set()
    for root in roots:
        if not root or not os.path.exists(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                if not fn.lower().endswith(".csv") or fn.startswith("_"):
                    continue
                code = plain_code_from_any(fn[:12])
                if not code or code in seen:
                    continue
                bs_code = bs_code_from_plain(code)
                if not bs_code:
                    continue
                rows.append({"代码": code, "名称": "", "bs_code": bs_code})
                seen.add(code)
    df = pd.DataFrame(rows)
    if not df.empty:
        print(f"破界：从本地K线缓存反推股票池成功，数量={len(df)}")
    return normalize_stock_list_df(df, source="local_cache")


def get_emergency_stock_list() -> pd.DataFrame:
    """应急股票池：只在所有正式股票池入口失败时使用，避免 GitHub Actions 因股票池为空直接失败。"""
    rows = [
        ("000001", "平安银行"), ("000002", "万科A"), ("000063", "中兴通讯"), ("000333", "美的集团"),
        ("000338", "潍柴动力"), ("000538", "云南白药"), ("000568", "泸州老窖"), ("000651", "格力电器"),
        ("000725", "京东方A"), ("000858", "五粮液"), ("000938", "紫光股份"), ("000977", "浪潮信息"),
        ("002049", "紫光国微"), ("002129", "TCL中环"), ("002156", "通富微电"), ("002230", "科大讯飞"),
        ("002241", "歌尔股份"), ("002371", "北方华创"), ("002415", "海康威视"), ("002475", "立讯精密"),
        ("002594", "比亚迪"), ("002709", "天赐材料"), ("002812", "恩捷股份"), ("002916", "深南电路"),
        ("300014", "亿纬锂能"), ("300033", "同花顺"), ("300059", "东方财富"), ("300122", "智飞生物"),
        ("300124", "汇川技术"), ("300274", "阳光电源"), ("300308", "中际旭创"), ("300316", "晶盛机电"),
        ("300394", "天孚通信"), ("300408", "三环集团"), ("300750", "宁德时代"), ("300760", "迈瑞医疗"),
        ("300782", "卓胜微"), ("300896", "爱美客"), ("600000", "浦发银行"), ("600009", "上海机场"),
        ("600010", "包钢股份"), ("600030", "中信证券"), ("600031", "三一重工"), ("600036", "招商银行"),
        ("600050", "中国联通"), ("600111", "北方稀土"), ("600276", "恒瑞医药"), ("600309", "万华化学"),
        ("600406", "国电南瑞"), ("600438", "通威股份"), ("600519", "贵州茅台"), ("600570", "恒生电子"),
        ("600585", "海螺水泥"), ("600690", "海尔智家"), ("600703", "三安光电"), ("600760", "中航沈飞"),
        ("600809", "山西汾酒"), ("600887", "伊利股份"), ("600893", "航发动力"), ("600900", "长江电力"),
        ("601012", "隆基绿能"), ("601088", "中国神华"), ("601166", "兴业银行"), ("601318", "中国平安"),
        ("601398", "工商银行"), ("601601", "中国太保"), ("601628", "中国人寿"), ("601668", "中国建筑"),
        ("601688", "华泰证券"), ("601857", "中国石油"), ("601888", "中国中免"), ("603019", "中科曙光"),
        ("603259", "药明康德"), ("603288", "海天味业"), ("603501", "韦尔股份"), ("603799", "华友钴业"),
        ("603986", "兆易创新"), ("688008", "澜起科技"), ("688012", "中微公司"), ("688036", "传音控股"),
        ("688111", "金山办公"), ("688126", "沪硅产业"), ("688256", "寒武纪"), ("688981", "中芯国际"),
    ]
    df = pd.DataFrame(rows, columns=["代码", "名称"])
    df["bs_code"] = df["代码"].apply(bs_code_from_plain)
    print(f"破界：启用应急股票池，数量={len(df)}")
    return normalize_stock_list_df(df, source="emergency")


def get_stock_list_from_akshare_direct() -> pd.DataFrame:
    try:
        import akshare as ak
    except Exception as e:
        print(f"破界：AkShare未安装或导入失败，无法直接取股票池：{e}")
        return pd.DataFrame(columns=["代码", "名称", "bs_code"])

    for attempt in range(1, 4):
        try:
            print(f"破界股票池获取：source=akshare stage=stock_zh_a_spot_em retry={attempt}/3")
            df = ak.stock_zh_a_spot_em()
            out = normalize_stock_list_df(df, source="akshare_direct")
            if not out.empty:
                print(f"破界：AkShare直接股票池获取成功，数量={len(out)}")
                return out
        except Exception as e:
            print(f"破界：AkShare直接股票池失败 retry={attempt}/3 error={str(e)[:180]}")
            time.sleep(1.2 * attempt)
    return pd.DataFrame(columns=["代码", "名称", "bs_code"])


def get_stock_list_safe(base) -> pd.DataFrame:
    """参考一号员工：缓存股票池优先，联网失败不阻断，最终应急池兜底。"""
    # 先尝试一号员工的缓存股票池函数，避免 BaoStock/AkShare 网络波动。
    for fn_name in ["get_a_stock_list_from_full_cache_universe", "get_a_stock_list"]:
        fn = getattr(base, fn_name, None)
        if fn is None:
            continue
        try:
            print(f"破界：尝试股票池入口 base.{fn_name}()")
            df = fn()
            out = normalize_stock_list_df(df, source=f"base.{fn_name}")
            if not out.empty:
                print(f"破界：股票池可用 source=base.{fn_name} stocks={len(out)}")
                return out
        except Exception as e:
            print(f"破界：股票池入口失败 source=base.{fn_name} error={str(e)[:180]}")

    # 再试一号员工暴露的 AkShare 股票池备用函数。
    fn = getattr(base, "get_a_stock_list_from_akshare", None)
    if fn is not None:
        try:
            print("破界：尝试 base.get_a_stock_list_from_akshare()")
            df = fn()
            out = normalize_stock_list_df(df, source="base.akshare")
            if not out.empty:
                print(f"破界：base AkShare备用股票池可用 stocks={len(out)}")
                return out
        except Exception as e:
            print(f"破界：base AkShare股票池失败 error={str(e)[:180]}")

    # 直接 AkShare。
    out = get_stock_list_from_akshare_direct()
    if not out.empty:
        return out

    # 本地缓存反推。
    out = stock_list_from_local_kline_cache()
    if not out.empty:
        return out

    # 最后应急股票池。可以通过 POJIE_DISABLE_EMERGENCY_UNIVERSE=1 关闭。
    if os.environ.get("POJIE_DISABLE_EMERGENCY_UNIVERSE", "0") != "1":
        return get_emergency_stock_list()

    return pd.DataFrame(columns=["代码", "名称", "bs_code"])


def normalize_external_kline_df(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """把一号员工缓存/AkShare/其它CSV统一成破界 normalize_kline 能识别的字段。"""
    if df is None or getattr(df, "empty", True):
        return None
    d = df.copy()
    rename_map = {
        "日期": "date", "交易日期": "date", "trade_date": "date", "Date": "date", "datetime": "date", "time": "date",
        "开盘": "open", "开盘价": "open", "Open": "open",
        "收盘": "close", "收盘价": "close", "Close": "close",
        "最高": "high", "最高价": "high", "High": "high",
        "最低": "low", "最低价": "low", "Low": "low",
        "成交量": "volume", "成交量(手)": "volume", "vol": "volume", "Volume": "volume",
        "成交额": "amount", "成交额(元)": "amount", "Amount": "amount",
        "涨跌幅": "pct_chg", "pctChg": "pct_chg",
        "换手率": "turnover", "turn": "turnover",
    }
    d = d.rename(columns={c: rename_map.get(c, c) for c in d.columns})
    if "date" not in d.columns or not all(c in d.columns for c in ["open", "high", "low", "close", "volume"]):
        return None
    if "amount" not in d.columns:
        d["amount"] = 0.0
    keep = ["date", "open", "high", "low", "close", "volume", "amount"]
    d = d[keep].copy()
    return normalize_kline(d)


def local_kline_candidate_paths(bs_code: str) -> List[str]:
    code = plain_code_from_any(bs_code)
    bs_file = str(bs_code).replace(".", "_")
    roots = []
    for env_key in ["FULL_HISTORY_CACHE_DIR", "CACHE_DIR"]:
        v = os.environ.get(env_key, "").strip()
        if v:
            roots.append(v)
    roots.extend(["kline_cache", "K线缓存", "kline_cache/base", "kline_cache/deep"])
    paths = []
    for root in roots:
        if not root:
            continue
        paths.extend([
            os.path.join(root, f"{code}.csv"),
            os.path.join(root, f"{bs_file}.csv"),
            os.path.join(root, "base", f"{bs_file}.csv"),
            os.path.join(root, "deep", f"{bs_file}.csv"),
            os.path.join(root, "base", f"{code}.csv"),
            os.path.join(root, "deep", f"{code}.csv"),
        ])
    # 去重保持顺序
    seen = set()
    out = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def read_local_kline_cache_for_pojie(bs_code: str) -> Optional[pd.DataFrame]:
    for path in local_kline_candidate_paths(bs_code):
        if not path or not os.path.exists(path):
            continue
        try:
            df = pd.read_csv(path, dtype={"date": str})
            out = normalize_external_kline_df(df)
            if out is not None and len(out) >= 120:
                print(f"破界K线：读取本地缓存成功 symbol={bs_code} file={path} rows={len(out)}")
                return out
        except Exception as e:
            print(f"破界K线：读取本地缓存失败 symbol={bs_code} file={path} error={str(e)[:120]}")
    return None


def get_daily_kline_akshare_direct(bs_code: str, lookback_days: int = 2600) -> Optional[pd.DataFrame]:
    try:
        import akshare as ak
    except Exception:
        return None
    symbol = plain_code_from_any(bs_code)
    if not symbol:
        return None
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=int(lookback_days))).strftime("%Y%m%d")
    for attempt in range(1, 3):
        try:
            time.sleep(0.15 * attempt)
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            out = normalize_external_kline_df(df)
            if out is not None and len(out) >= 120:
                print(f"破界K线：AkShare直接补拉成功 symbol={bs_code} rows={len(out)}")
                return out
        except Exception as e:
            if attempt <= 1:
                print(f"破界K线：AkShare直接补拉失败 symbol={bs_code} retry={attempt}/2 error={str(e)[:160]}")
    return None


def get_daily_kline_safe(base, bs_code: str) -> Optional[pd.DataFrame]:
    """优先缓存，其次一号员工数据入口，最后 AkShare 直接兜底。"""
    cached = read_local_kline_cache_for_pojie(bs_code)
    if cached is not None:
        return cached

    # 尽量复用一号员工成熟的 get_daily_kline：里面已有全历史缓存、旧缓存、BaoStock、AkShare兜底逻辑。
    if hasattr(base, "get_daily_kline"):
        try:
            df = base.get_daily_kline(bs_code, cache_scope="deep")
            out = normalize_external_kline_df(df)
            if out is not None:
                return out
        except TypeError:
            try:
                df = base.get_daily_kline(bs_code)
                out = normalize_external_kline_df(df)
                if out is not None:
                    return out
            except Exception as e:
                print(f"破界K线：base.get_daily_kline旧签名失败 symbol={bs_code} error={str(e)[:160]}")
        except Exception as e:
            print(f"破界K线：base.get_daily_kline失败 symbol={bs_code} error={str(e)[:160]}")

    return get_daily_kline_akshare_direct(bs_code)


def write_empty_report(reason: str, scanned: int = 0, failed: int = 0) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    payload = {
        "version": MODEL_VERSION,
        "generated_at": now_bj(),
        "scanned": scanned,
        "failed": failed,
        "results": [],
        "reason": reason,
    }
    json_path = os.path.join(OUTPUT_DIR, "pojie_signals.json")
    txt_path = os.path.join(OUTPUT_DIR, "pojie_report.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"【破界｜核心线突破独立战法】\n{reason}\n扫描：{scanned}只，失败/无数据：{failed}只\n")
    print(f"破界：{reason}")
    print(f"破界空报告已保存：{json_path} / {txt_path}")

def parse_args():
    p = argparse.ArgumentParser(description="破界：核心线突破独立战法")
    p.add_argument("--模式", default="daily", choices=["daily", "selfcheck"], help="daily=执行破界选股；selfcheck=只做自检")
    p.add_argument("--基础模型文件", default=DEFAULT_BASE_MODEL_FILE, help="复用的一号员工主文件，默认 stock_alert.py")
    p.add_argument("--最多股票数量", type=int, default=int(os.environ.get("破界_最多股票数量", "0")), help="调试用，0=不限制")
    p.add_argument("--输出数量", type=int, default=int(os.environ.get("破界_输出数量", "10")), help="破界候选最多输出数量")
    p.add_argument("--最低分", type=float, default=float(os.environ.get("破界_最低分", "70")), help="最低输出分数")
    p.add_argument("--发送Telegram", action="store_true", help="开启后调用基础模型 send_telegram 推送")
    p.add_argument("--不发送Telegram", action="store_true", help="强制不推送")
    return p.parse_args()


def main():
    args = parse_args()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 默认打开一号员工数据层兜底变量，避免 GitHub 上股票池/K线接口短暂失败就退出。
    os.environ.setdefault("USE_FULL_HISTORY_CACHE", "1")
    os.environ.setdefault("STOCK_LIST_FALLBACK_AKSHARE", "1")
    os.environ.setdefault("KLINE_FALLBACK_AKSHARE", "1")
    os.environ.setdefault("ALLOW_STALE_KLINE_CACHE", "1")

    base = load_base_module(args.基础模型文件)

    # BaoStock 登录不作为硬门槛；能登录就让一号员工数据层少报 you don't login，不能登录也继续走缓存/AkShare。
    if hasattr(base, "baostock_login"):
        try:
            ok = base.baostock_login()
            print(f"破界：尝试调用一号员工 BaoStock 登录，ok={ok}")
        except Exception as e:
            print(f"破界：BaoStock登录异常但不阻断：{e}")

    if args.模式 == "selfcheck":
        required = ["get_a_stock_list", "get_daily_kline"]
        missing = [x for x in required if not hasattr(base, x)]
        if missing:
            print(f"{MODEL_VERSION} 自检提醒：基础模型缺少 {missing}，但破界内置兜底仍可运行。")
        else:
            print(f"{MODEL_VERSION} 自检通过：基础模型入口存在。")
        return

    stock_list = get_stock_list_safe(base)
    stock_list = normalize_stock_list_df(stock_list, source="final")

    if stock_list is None or len(stock_list) == 0:
        write_empty_report("股票池为空：基础模型、AkShare、本地缓存、应急股票池均不可用，本次不强制失败。")
        return

    if args.最多股票数量 and args.最多股票数量 > 0:
        stock_list = stock_list.head(args.最多股票数量)

    print(f"破界：最终扫描股票池数量={len(stock_list)}")
    print(stock_list.head(20).to_string(index=False))

    results = []
    scanned = 0
    failed = 0
    no_kline = 0
    start = time.time()
    failed_items = []

    for _, row in stock_list.iterrows():
        scanned += 1
        code = str(row.get("代码", row.get("code", ""))).zfill(6)
        bs_code = str(row.get("bs_code", ""))
        name = str(row.get("名称", row.get("name", "")))
        if not bs_code or bs_code == "nan":
            bs_code = bs_code_from_plain(code)
        if not bs_code:
            failed += 1
            failed_items.append({"code": code, "name": name, "stage": "bad_bs_code"})
            continue

        try:
            df = get_daily_kline_safe(base, bs_code)
            if df is None or len(df) < 120:
                no_kline += 1
                failed_items.append({"code": code, "name": name, "bs_code": bs_code, "stage": "no_kline"})
                continue
            res = scan_one(code, name, df)
            if res and res["score"] >= args.最低分:
                results.append(res)
        except Exception as e:
            failed += 1
            failed_items.append({"code": code, "name": name, "bs_code": bs_code, "stage": "scan_exception", "error": str(e)[:200]})
            if failed <= 20:
                print(f"破界扫描失败：{code} {name} {e}")

        if scanned % 50 == 0:
            print(f"破界进度：{scanned}/{len(stock_list)} 候选={len(results)} 无K线={no_kline} 失败={failed} 耗时={int(time.time()-start)}s")

    results = sorted(results, key=lambda x: (x["signal_level"] == "S", x["score"]), reverse=True)[:args.输出数量]
    payload = {
        "version": MODEL_VERSION,
        "generated_at": now_bj(),
        "scanned": scanned,
        "failed": failed,
        "no_kline": no_kline,
        "results": results,
        "failed_items_sample": failed_items[:200],
    }
    json_path = os.path.join(OUTPUT_DIR, "pojie_signals.json")
    txt_path = os.path.join(OUTPUT_DIR, "pojie_report.txt")
    failed_path = os.path.join(OUTPUT_DIR, "pojie_failed_items.json")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    report = build_report(results, scanned, failed + no_kline)
    if not results:
        report += "\n\n诊断：本次流程已跑通，但没有达到最低分的破界候选。"
        if no_kline > 0:
            report += f"\nK线无数据/不足：{no_kline}只。请优先检查 kline_cache 是否有有效CSV，或稍后重跑 AkShare。"

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(report)
    with open(failed_path, "w", encoding="utf-8") as f:
        json.dump(failed_items, f, ensure_ascii=False, indent=2)

    print(report)
    print(f"破界结果已保存：{json_path} / {txt_path}")
    print(f"破界失败/无数据清单已保存：{failed_path}")

    if args.发送Telegram and not args.不发送Telegram and hasattr(base, "send_telegram"):
        try:
            base.send_telegram(report)
        except Exception as e:
            print(f"Telegram发送失败，但不影响本次运行：{e}")

    # 尽量登出 BaoStock，但不强制。
    if hasattr(base, "baostock_logout"):
        try:
            base.baostock_logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
