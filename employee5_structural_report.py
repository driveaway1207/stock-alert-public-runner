# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import statistics
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

import employee5_runner as base

REPORT_DIR = Path(__file__).resolve().parent / "employee5_reports"


def sf(x: Any, default: float = 0.0) -> float:
    return base.sf(x, default)


def rd(x: Any, n: int = 2) -> float:
    return base.rd(x, n)


def div(a: Any, b: Any) -> float:
    b = sf(b)
    return sf(a) / b if b else 0.0


def pct(a: Any, b: Any) -> float:
    return base.pct(a, b)


def ss(x: Any) -> str:
    return base.ss(x)


def agg_n(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """固定根数聚合K。五号口径：20日≈月线观察，60日≈季线观察，250日≈年线观察。"""
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy().reset_index(drop=True)
    rows: List[Dict[str, Any]] = []
    for start in range(0, len(d), n):
        sub = d.iloc[start:start + n]
        if len(sub) < max(3, n // 3):
            continue
        rows.append({
            "date": ss(sub.iloc[-1].get("date")),
            "start_date": ss(sub.iloc[0].get("date")),
            "open": sf(sub.iloc[0].open),
            "high": sf(sub.high.max()),
            "low": sf(sub.low.min()),
            "close": sf(sub.iloc[-1].close),
            "volume": sf(sub.volume.sum()),
            "amount": sf(sub.amount.sum()) if "amount" in sub.columns else 0.0,
        })
    return pd.DataFrame(rows).reset_index(drop=True)


def candle(row: pd.Series) -> Dict[str, Any]:
    op, hi, lo, cl = sf(row.open), sf(row.high), sf(row.low), sf(row.close)
    rng = max(hi - lo, 1e-6)
    body = abs(cl - op)
    return {
        "is_yang": bool(cl > op),
        "is_bear": bool(cl < op),
        "entity_pct": rd((cl / op - 1) * 100 if op else 0),
        "bear_entity_pct": rd((op / cl - 1) * 100 if cl and cl < op else 0),
        "body_ratio": rd(body / rng, 3),
        "close_pos": rd((cl - lo) / rng, 3),
        "upper_ratio": rd(max(hi - max(op, cl), 0) / rng, 3),
        "lower_ratio": rd(max(min(op, cl) - lo, 0) / rng, 3),
        "body_floor": min(op, cl),
        "body_top": max(op, cl),
        "body_mid": (op + cl) / 2,
        "body_upper_third": min(op, cl) + body * 2 / 3,
    }


def cv(vals: List[float]) -> float:
    vals = [sf(v) for v in vals if sf(v) > 0]
    if len(vals) < 2:
        return 0.0
    mean = statistics.mean(vals)
    return statistics.pstdev(vals) / mean if mean else 0.0


def rel_vol(df: pd.DataFrame, idx: int, prefer: int = 6) -> Dict[str, Any]:
    if df.empty or idx < 0 or idx >= len(df):
        return {"ratio": 0.0, "window": 0, "rank_pct": 0.0, "volume": 0.0, "mean_volume": 0.0}
    windows = [prefer, 12, 6, 3, 1]
    chosen, mean_vol = 0, 0.0
    for w in windows:
        if idx >= w:
            chosen = w
            mean_vol = sf(df.iloc[idx - w:idx].volume.mean())
            break
    if not chosen and idx > 0:
        chosen = idx
        mean_vol = sf(df.iloc[:idx].volume.mean())
    v = sf(df.iloc[idx].volume)
    hist = [sf(x) for x in df.iloc[max(0, idx - max(chosen, 12)):idx + 1].volume.tolist() if sf(x) > 0]
    rank_pct = sum(1 for x in hist if x <= v) / len(hist) if hist else 0.0
    return {"ratio": rd(div(v, mean_vol)), "window": int(chosen), "rank_pct": rd(rank_pct, 3), "volume": rd(v, 0), "mean_volume": rd(mean_vol, 0)}


def flat_grade(v1: float, v2: float) -> Dict[str, Any]:
    if v1 <= 0 or v2 <= 0:
        return {"flat": False, "grade": "无效", "diff_pct": 100.0}
    diff = abs(v2 - v1) / max(v1, v2) * 100
    if diff <= 5:
        return {"flat": True, "grade": "强平量", "diff_pct": rd(diff)}
    if diff <= 8:
        return {"flat": True, "grade": "合格平量", "diff_pct": rd(diff)}
    if diff <= 15:
        return {"flat": True, "grade": "宽松平量", "diff_pct": rd(diff)}
    return {"flat": False, "grade": "不算平量", "diff_pct": rd(diff)}


def strong_yang(row: pd.Series, vol_ratio: float) -> Dict[str, Any]:
    c = candle(row)
    if vol_ratio >= 5:
        req = {"entity_pct": 30, "body_ratio": 0.60, "close_pos": 0.75, "upper_ratio": 0.25}
    elif vol_ratio >= 3:
        req = {"entity_pct": 22, "body_ratio": 0.55, "close_pos": 0.70, "upper_ratio": 0.30}
    else:
        req = {"entity_pct": 15, "body_ratio": 0.50, "close_pos": 0.65, "upper_ratio": 0.35}
    ok = bool(c["is_yang"] and c["entity_pct"] >= req["entity_pct"] and c["body_ratio"] >= req["body_ratio"] and c["close_pos"] >= req["close_pos"] and c["upper_ratio"] <= req["upper_ratio"])
    return {"ok": ok, "metrics": c, "required": req}


def small_bear(row: pd.Series) -> bool:
    c = candle(row)
    return bool(c["is_bear"] and c["bear_entity_pct"] <= 12 and c["body_ratio"] <= 0.65)


def event(eid: str, name: str, role: str, hit: bool, evidence: str, formula: str, folk: str, wall: str, behavior: str) -> Dict[str, Any]:
    return {"id": eid, "name": name, "role": role, "hit": bool(hit), "evidence": evidence, "formula": formula, "folk": folk, "wall_street_mapping": wall, "market_behavior": behavior}


def failed_impulse_background(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df) < 8:
        return None
    idx = int(df.iloc[:-1].volume.idxmax())
    r = df.iloc[idx]
    rv = rel_vol(df, idx)
    c = candle(r)
    if not (c["is_yang"] and (rv["ratio"] >= 1.8 or rv["rank_pct"] >= 0.90)):
        return None
    post = df.iloc[idx + 1:min(len(df), idx + 8)]
    broke_floor = any(sf(x) < c["body_floor"] for x in post.close.tolist())
    broke_low = any(sf(x) < sf(r.low) for x in post.close.tolist())
    vols = post.volume.tolist()
    decay = len(vols) >= 2 and sf(vols[-1]) < sf(vols[0])
    if broke_floor or broke_low:
        return {"idx": idx, "date": ss(r.date), "high": sf(r.high), "low": sf(r.low), "body_floor": c["body_floor"], "rv": rv, "decay": decay, "evidence": f"{ss(r.date)}最大量强阳，局部量比={rv['ratio']}，后续跌破实底={broke_floor}/虚底={broke_low}，后续缩量={decay}"}
    return None


def second_effort(df: pd.DataFrame, bg: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not bg:
        return None
    start = int(bg["idx"]) + 1
    end = len(df) - 1
    if end - start < 3:
        return None
    sub = df.iloc[start:end]
    idx = int(sub.volume.idxmax())
    r = df.iloc[idx]
    rv = rel_vol(df, idx)
    visible = rv["rank_pct"] >= 0.80 or rv["ratio"] >= 1.30
    if visible:
        return {"idx": idx, "date": ss(r.date), "level": sf(r.high), "rv": rv, "evidence": f"{ss(r.date)}失败后二次努力，高点={rd(r.high)}，局部量比={rv['ratio']}，低于前强阳高点={sf(r.high) < sf(bg['high']) * 0.995}"}
    return None


def core_line(df: pd.DataFrame, sec: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not sec:
        return None
    level = sf(sec["level"])
    pts = []
    for i in range(0, max(0, int(sec["idx"]) - 3)):
        r = df.iloc[i]
        diff = abs(sf(r.high) - level) / level * 100 if level else 999
        rv = rel_vol(df, i)
        c = candle(r)
        if diff <= 6 and (c["upper_ratio"] >= 0.18 or sf(r.high) >= max(sf(r.open), sf(r.close)) * 1.03) and (rv["ratio"] >= 1.3 or rv["rank_pct"] >= 0.80):
            pts.append({"idx": i, "date": ss(r.date), "diff_pct": rd(diff), "rv": rv})
    if not pts:
        return None
    left = min(pts, key=lambda x: x["diff_pct"])
    breaches = []
    for i in range(left["idx"] + 1, int(sec["idx"])):
        r = df.iloc[i]
        if sf(r.low) <= level <= sf(r.high):
            op, cl = sf(r.open), sf(r.close)
            if min(op, cl) <= level <= max(op, cl) or abs(cl - level) / level <= 0.03:
                breaches.append(i)
    clustered = True if not breaches else (max(breaches) - min(breaches) + 1 <= 4 or (max(breaches) - min(breaches) + 1) / max(1, int(sec["idx"]) - left["idx"]) <= 0.25)
    if clustered and len(breaches) <= 6:
        return {"level": level, "left": left, "right": sec, "breach_count": len(breaches), "evidence": f"核心线={rd(level)}，左侧{left['date']}带量上影差异{left['diff_pct']}%，右侧{sec['date']}二次努力高点，中间穿越集中={clustered}，穿越次数={len(breaches)}"}
    return None


def high_volume_flat_pair(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    best = None
    for i in range(1, len(df) - 1):
        r1, r2 = df.iloc[i], df.iloc[i + 1]
        rv1, rv2 = rel_vol(df, i), rel_vol(df, i + 1)
        fg = flat_grade(sf(r1.volume), sf(r2.volume))
        q1 = strong_yang(r1, rv1["ratio"])
        c1 = candle(r1)
        no_destroy = sf(r2.close) >= c1["body_upper_third"]
        high_volume = rv1["ratio"] >= 1.8 or rv2["ratio"] >= 1.8 or rv1["rank_pct"] >= 0.85 or rv2["rank_pct"] >= 0.85
        if fg["flat"] and high_volume and q1["ok"]:
            quality = 1 + int(no_destroy) + int(fg["grade"] == "强平量")
            item = {"idx1": i, "idx2": i + 1, "date1": ss(r1.date), "date2": ss(r2.date), "rv1": rv1, "rv2": rv2, "flat": fg, "q1": q1, "no_destroy": no_destroy, "level": c1["body_floor"], "quality": quality, "evidence": f"{ss(r1.date)}/{ss(r2.date)}严格相邻高倍量平量，{fg['grade']}量差{fg['diff_pct']}%，第一根实底={rd(c1['body_floor'])}，第二根守上三分之一={no_destroy}"}
            if best is None or item["quality"] > best["quality"]:
                best = item
    return best


def regime_shift(df: pd.DataFrame, pair: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pair:
        return None
    idx = int(pair["idx1"])
    left, right = df.iloc[max(0, idx - 6):idx], df.iloc[idx + 1:min(len(df), idx + 7)]
    if len(left) < 3 or len(right) < 3:
        return None
    lm, rm = sf(left.volume.mean()), sf(right.volume.mean())
    lcv, rcv = cv(left.volume.tolist()), cv(right.volume.tolist())
    price_ok = sf(right.close.mean()) >= sf(left.close.mean()) * 0.98
    if lm > 0 and rm >= lm * 1.15 and price_ok:
        return {"left_mean": lm, "right_mean": rm, "left_cv": lcv, "right_cv": rcv, "evidence": f"启动前均量={rd(lm,0)}，启动后均量={rd(rm,0)}，抬升={rd(div(rm,lm))}倍；量能CV {rd(lcv)}→{rd(rcv)}"}
    return None


def stability_improving(df: pd.DataFrame, pair: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pair:
        return None
    idx = int(pair["idx1"])
    left, right = df.iloc[max(0, idx - 6):idx], df.iloc[idx + 1:min(len(df), idx + 7)]
    if len(left) < 3 or len(right) < 3:
        return None
    lm, rm = sf(left.volume.mean()), sf(right.volume.mean())
    lcv, rcv = cv(left.volume.tolist()), cv(right.volume.tolist())
    ext_l = sum(1 for v in left.volume.tolist() if lm and (sf(v) > lm * 1.8 or sf(v) < lm * 0.5))
    ext_r = sum(1 for v in right.volume.tolist() if rm and (sf(v) > rm * 1.8 or sf(v) < rm * 0.5))
    if lm and rm >= lm * 1.15 and rcv <= lcv * 0.80 and ext_r <= ext_l and sf(right.close.mean()) >= sf(left.close.mean()) * 0.98:
        return {"evidence": f"均量抬升{rd(div(rm,lm))}倍，量能CV下降{rd(lcv)}→{rd(rcv)}，极端量柱{ext_l}→{ext_r}"}
    return None


def regular_pullback(df: pd.DataFrame, pair: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pair:
        return None
    level = sf(pair["level"])
    start = int(pair["idx1"]) + 1
    left_mean = sf(df.iloc[max(0, int(pair["idx1"]) - 6):int(pair["idx1"])].volume.mean())
    for i in range(start, min(len(df) - 2, start + 8)):
        sub = df.iloc[i:i + 3]
        bear_ok = sum(1 for _, r in sub.iterrows() if small_bear(r)) >= 2
        broke = any(sf(r.close) < level for _, r in sub.iterrows())
        vols = [sf(x) for x in sub.volume.tolist()]
        if bear_ok and broke and vols[0] > vols[1] > vols[2] > 0:
            d1, d2 = (vols[0] - vols[1]) / vols[0] * 100, (vols[1] - vols[2]) / vols[1] * 100
            regular = 5 <= d1 <= 25 and 5 <= d2 <= 25 and abs(d1 - d2) <= 8
            if regular:
                elevated = sf(sub.volume.mean()) >= left_mean * 1.15 if left_mean else False
                return {"dates": [ss(x) for x in sub.date.tolist()], "decay_pct": [rd(d1), rd(d2)], "elevated": elevated, "evidence": f"{[ss(x) for x in sub.date.tolist()]}小阴组合，缩量比例={rd(d1)}%/{rd(d2)}%，量能较左侧抬升={elevated}"}
    return None


def line_reactions(df: pd.DataFrame, pair: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pair:
        return None
    level = sf(pair["level"])
    hits, weighted = [], 0
    for i in range(int(pair["idx1"]) + 1, len(df)):
        r = df.iloc[i]
        vals = [sf(r.open), sf(r.close), sf(r.high), sf(r.low)]
        if any(level and abs(v - level) / level * 100 <= 5 for v in vals if v > 0):
            hits.append(ss(r.date))
            rv = rel_vol(df, i)
            if rv["ratio"] >= 1.2 or rv["rank_pct"] >= 0.75:
                weighted += 1
    if len(hits) >= 3:
        return {"count": len(hits), "weighted": weighted, "dates": hits[:6], "evidence": f"逻辑分析线右侧反应{len(hits)}次，带量/相对显眼反应{weighted}次，样例={hits[:6]}"}
    return None


def bullish_ladder(df: pd.DataFrame, level: float = 0.0) -> Optional[Dict[str, Any]]:
    for i in range(max(0, len(df) - 20), max(0, len(df) - 2)):
        for length in (3, 4, 5):
            sub = df.iloc[i:i + length]
            if len(sub) < length:
                continue
            vols = [sf(x) for x in sub.volume.tolist()]
            if not all(vols[j] < vols[j + 1] for j in range(len(vols) - 1)):
                continue
            inc = [(vols[j + 1] / vols[j] - 1) * 100 for j in range(len(vols) - 1) if vols[j] > 0]
            if not inc or not all(8 <= x <= 45 for x in inc) or cv(inc) > 0.60:
                continue
            yang_count = sum(1 for _, r in sub.iterrows() if sf(r.close) > sf(r.open))
            entities = [abs(sf(r.close) - sf(r.open)) / max(sf(r.open), 1e-6) * 100 for _, r in sub.iterrows()]
            last = candle(sub.iloc[-1])
            last_strong = entities[-1] >= max(entities[:-1] or [0]) and last["is_yang"] and last["close_pos"] >= 0.70 and last["upper_ratio"] <= 0.30
            if yang_count >= max(2, length - 1) and last_strong and (sf(sub.iloc[-1].close) >= level if level else True):
                return {"idx": i, "length": length, "dates": [ss(x) for x in sub.date.tolist()], "inc_pct": [rd(x) for x in inc], "inc_cv": rd(cv(inc)), "yang_count": yang_count, "evidence": f"{length}根规则阳梯量，递增={ [rd(x) for x in inc] }%，递增CV={rd(cv(inc))}，阳线{yang_count}/{length}，最后一根量价最强"}
    return None


def core_breakout(df: pd.DataFrame, core: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not core:
        return None
    level = sf(core["level"])
    for i in range(1, len(df)):
        p, r = df.iloc[i - 1], df.iloc[i]
        if sf(p.close) < level <= sf(r.close):
            c = candle(r)
            avg = sf(df.iloc[max(0, i - 6):i].volume.mean())
            entity_above = max(0.0, sf(r.close) - max(sf(r.open), level)) / max(abs(sf(r.close) - sf(r.open)), 1e-6)
            valid = bool(sf(r.close) >= level * 1.02 and entity_above >= 0.50 and c["close_pos"] >= 0.70 and c["upper_ratio"] <= 0.30 and sf(r.volume) >= avg * 1.15)
            return {"idx": i, "date": ss(r.date), "valid": valid, "entity_above": rd(entity_above), "evidence": f"{ss(r.date)}突破核心线{rd(level)}，收盘距线={rd(pct(r.close, level))}%，实体在线上占比={rd(entity_above)}，量/前6均量={rd(div(r.volume, avg))}，有效={valid}"}
    return None


def acceptance(df: pd.DataFrame, br: Optional[Dict[str, Any]], core: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not br or not core:
        return None
    idx, level = int(br["idx"]), sf(core["level"])
    prior_avg = sf(df.iloc[max(0, idx - 6):idx].volume.mean())
    post = df.iloc[idx:min(len(df), idx + 3)]
    if len(post) < 2 or prior_avg <= 0:
        return None
    above = sum(1 for v in post.volume.tolist() if sf(v) >= prior_avg)
    strong = sum(1 for v in post.volume.tolist() if sf(v) >= prior_avg * 1.15)
    fast_fail = any(sf(x) < level * 0.97 for x in post.close.tolist())
    if above >= 2 and not fast_fail:
        return {"evidence": f"突破后3根内{above}根量能≥前6均量，{strong}根≥1.15倍，2根内未收盘跌破核心线3%"}
    return None


def retest_support(df: pd.DataFrame, br: Optional[Dict[str, Any]], core: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not br or not core:
        return None
    level = sf(core["level"])
    for i in range(int(br["idx"]) + 1, len(df)):
        r = df.iloc[i]
        near = sf(r.low) <= level * 1.08 and sf(r.high) >= level * 0.94
        hold = sf(r.close) >= level * 0.97
        if near and hold:
            return {"idx": i, "date": ss(r.date), "evidence": f"{ss(r.date)}回踩核心线附近，low/line={rd(div(r.low, level))}，close/line={rd(div(r.close, level))}，收盘未有效跌破"}
    return None


def stable_engulf(df: pd.DataFrame, retest: Optional[Dict[str, Any]], core: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not retest:
        return None
    level = sf(core["level"]) if core else 0
    start = max(1, int(retest["idx"]))
    for i in range(start, min(len(df), start + 5)):
        p, r = df.iloc[i - 1], df.iloc[i]
        engulf = sf(p.close) < sf(p.open) and sf(r.close) > sf(r.open) and sf(r.close) >= max(sf(p.open), sf(p.close))
        fg = flat_grade(sf(p.volume), sf(r.volume))
        local_cv = cv(df.iloc[max(0, i - 2):min(len(df), i + 3)].volume.tolist())
        if engulf and fg["flat"] and local_cv <= 0.40 and (level <= 0 or sf(r.close) >= level * 0.97):
            return {"date": ss(r.date), "evidence": f"{ss(r.date)}核心线支撑后阳包阴，量差={fg['diff_pct']}%({fg['grade']})，附近量能CV={rd(local_cv)}"}
    return None


def build_pool(hist: pd.DataFrame) -> Dict[str, Any]:
    df20 = agg_n(hist, 20)
    bg = failed_impulse_background(df20)
    sec = second_effort(df20, bg)
    core = core_line(df20, sec)
    pair = high_volume_flat_pair(df20)
    reg = regime_shift(df20, pair)
    stable = stability_improving(df20, pair)
    pull = regular_pullback(df20, pair)
    react = line_reactions(df20, pair)
    logic_level = sf(pair["level"]) if pair else 0.0
    core_level = sf(core["level"]) if core else 0.0
    lad_logic = bullish_ladder(df20, logic_level) if pair else None
    lad_core = bullish_ladder(df20, core_level) if core else None
    br = core_breakout(df20, core)
    acc = acceptance(df20, br, core)
    ret = retest_support(df20, br, core)
    eng = stable_engulf(df20, ret, core)
    events = [
        event("BKG001", "高量强阳失败背景", "背景过程", bg is not None, bg["evidence"] if bg else "未识别", "高量强阳后若干聚合K收盘跌破强阳实底或虚底。只作为背景，不算正向原因。", "物极必反只是过程，不是好事", "failed high-volume impulse", "前一次强攻击失败，后续必须看修复链"),
        event("BKG002", "失败后的二次努力供应线", "背景过程", sec is not None, sec["evidence"] if sec else "未识别", "BKG001后出现局部显眼量上攻高点。", "再次努力的极限高点", "renewed effort with limited result", "供应仍重"),
        event("CORE001", "隔断式带量上影共振核心线", "结构原因", core is not None, core["evidence"] if core else "未识别", "二次努力高点与远期带量上影高点价差≤6%，中间穿越必须集中。", "长期双上影共振核心线", "market memory", "长期供应记忆"),
        event("VOL001", "局部相对量能确认", "基础规则", True, "所有量能均按当时局部均量、局部排名确认，不做跨年份绝对量比较。", "量/前N均量、局部分位；样本不足下钻更细周期。", "当时显眼才算带量", "relative volume", "量能价值来自局部语境"),
        event("HVF001", "严格相邻并肩高倍量平量", "结构原因", pair is not None, pair["evidence"] if pair else "未识别", "两根必须相邻；量差≤5%强平量，≤8%合格，≤15%宽松；两根均为局部高量。", "并肩高倍量平量", "volume consistency", "连续资金强度高度接近"),
        event("HVF002", "第一根高倍量强阳量价合一", "结构原因", bool(pair and pair["q1"]["ok"]), json.dumps(pair["q1"] if pair else {}, ensure_ascii=False), "高量越大，实体、收盘位置、上影要求越高。", "大量必须配大实体", "effort vs result", "资金努力与价格结果匹配"),
        event("HVF003", "第二根不破坏第一根上三分之一", "结构原因", bool(pair and pair["no_destroy"]), pair["evidence"] if pair else "未识别", "第二根收盘≥第一根实体上三分之一。", "第二根不能破坏第一根强阳", "high-level acceptance", "高位价格接受"),
        event("LOG001", "逻辑分析线成立", "分析锚点", pair is not None, f"逻辑分析线={rd(logic_level)}，来自强阳实底；不是核心线" if pair else "未识别", "强阳实底+高倍量平量+右侧验证，才可作为逻辑分析线。", "截图颜色只是临时标注，正式叫逻辑分析线", "change-of-character anchor", "解释资金状态切换"),
        event("REG001", "启动后右侧均量抬升", "正向原因", reg is not None, reg["evidence"] if reg else "未识别", "启动后6根均量≥启动前6根均量×1.15，价格中枢不明显下移。", "启动后量能变好", "volume regime shift", "资金参与从冷转热"),
        event("REG002", "均量抬升后的量能稳定", "正向原因", stable is not None, stable["evidence"] if stable else "未识别", "右侧均量≥左侧1.15倍，右侧CV≤左侧CV×0.80，极端量柱不增加。", "越来越稳定不是低量死水", "lower dispersion", "资金更有秩序"),
        event("PULL001", "逻辑分析线跌破后的规律性缩量小阴", "正向原因", pull is not None, pull["evidence"] if pull else "未识别", "3根内至少2根小阴；V1>V2>V3；缩量比例5%-25%，两次差≤8%。", "三小阴规律缩量", "controlled pullback", "回撤有秩序"),
        event("PULL002", "缩量回落但均量仍高于左侧", "正向原因", bool(pull and pull.get("elevated")), pull["evidence"] if pull else "未识别", "小阴组合均量≥启动前低迷期均量×1.15。", "下跌量也比左侧抬升", "elevated orderly pullback", "活跃度提高后的消化"),
        event("REACT001", "逻辑分析线右侧多点共振", "正向原因", react is not None, react["evidence"] if react else "未识别", "逻辑分析线生成后，右侧实体/影线/收盘接近该线≥3次。", "右侧多次记住这条线", "reaction clustering", "市场继续验证该分析线"),
        event("LAD001", "规则阳梯量推进", "正向原因", lad_logic is not None or lad_core is not None, (lad_core or lad_logic or {}).get("evidence", "未识别"), "连续3-5根量逐级递增；单次增幅8%-45%；增幅CV≤0.60；阳线占优。", "阳梯量有规律递增", "progressive demand", "主动需求有节奏增强"),
        event("LAD002", "梯量最后一根量价最强", "正向原因", lad_logic is not None or lad_core is not None, (lad_core or lad_logic or {}).get("evidence", "未识别"), "最后一根为最大量，且实体为窗口最大/最强，close_pos≥0.70，上影≤0.30。", "最后一击量最大、实体也最大", "final effort-result alignment", "最后推进同步增强"),
        event("CORE002", "核心线有效突破", "确认原因", bool(br and br["valid"]), br["evidence"] if br else "未识别", "收盘≥核心线×1.02，实体在线上≥50%，close_pos≥0.70，上影≤0.30，量≥前6均量×1.15。", "核心线要有效打穿", "valid breakout", "供应记忆被打穿"),
        event("CORE003", "突破后量能接受", "确认原因", acc is not None, acc["evidence"] if acc else "未识别", "突破后3根内至少2根量能≥突破前6根均量。", "突破后量能仍在均量上方", "acceptance above breakout", "新价格区间被接受"),
        event("CORE004", "突破后未快速跌回核心线", "确认原因", acc is not None, acc["evidence"] if acc else "未识别", "20日聚合K突破后2根内，不能收盘跌破核心线3%以上。", "没有快速跌回", "no fast failed breakout", "突破没有立刻失败"),
        event("CORE005", "核心线回踩支撑确认", "确认原因", ret is not None, ret["evidence"] if ret else "未识别", "回踩low≤核心线×1.08且high≥核心线×0.94，收盘≥核心线×0.97。", "压力转支撑", "breakout-retest", "供应转支撑"),
        event("REV001", "核心线支撑后的平量阳包阴", "确认原因", eng is not None, eng["evidence"] if eng else "未识别", "前阴后阳，阳线收盘≥前阴实体顶；两根量差≤15%；附近5根量能CV≤0.40。", "平量阳包阴", "stable participation reversal", "稳定资金修复反包"),
    ]
    positive_ids = [e["id"] for e in events if e["hit"] and e["role"] in ("结构原因", "正向原因", "确认原因")]
    background_ids = [e["id"] for e in events if e["hit"] and e["role"] == "背景过程"]
    return {"period_rows": {"20d_agg": len(df20)}, "core_line": core, "logic_analysis_line": {"level": logic_level, "pair": pair} if pair else None, "events": events, "positive_ids": positive_ids, "background_ids": background_ids}


def summarize(deep: List[Dict[str, Any]]) -> Tuple[Dict[str, int], Dict[str, int]]:
    positive: Dict[str, int] = {}
    background: Dict[str, int] = {}
    for item in deep:
        for e in item.get("events", []):
            if not e.get("hit"):
                continue
            key = f"{e['id']} {e['name']}"
            if e.get("role") == "背景过程":
                background[key] = background.get(key, 0) + 1
            elif e.get("role") in ("结构原因", "正向原因", "确认原因"):
                positive[key] = positive.get(key, 0) + 1
    return positive, background


def main() -> None:
    start = time.time()
    report_path = REPORT_DIR / "limit_up_research_report.json"
    if not report_path.exists():
        raise FileNotFoundError("employee5 base report missing: employee5_reports/limit_up_research_report.json")
    data = json.loads(report_path.read_text(encoding="utf-8"))
    target_date = str(data.get("target_date") or base.latest_trade_date()).replace("-", "")
    deep: List[Dict[str, Any]] = []
    hist_source_count: Dict[str, int] = {}
    for item in data.get("deep_samples", [])[:3]:
        code, name = ss(item.get("code")), ss(item.get("name"))
        hist = base.fetch_hist(code, target_date)
        if hist.empty or len(hist) < 30:
            continue
        source = ss(hist.get("hist_source", pd.Series(["unknown"])).iloc[-1]) if "hist_source" in hist.columns else "unknown"
        hist_source_count[source] = hist_source_count.get(source, 0) + 1
        cause = build_pool(hist)
        one = dict(item)
        one.update({"hist_source": source, "cause_model": cause, "events": cause["events"]})
        deep.append(one)
    positive_count, background_count = summarize(deep)
    lines = [
        "🧬【五号员工-暴涨原因口径池报告】",
        f"日期：{target_date}",
        f"耗时：{base.fmt_seconds(time.time()-start)}",
        "口径：五号不是选股打分器。本报告把背景过程、正向原因、确认原因分开；背景过程不算暴涨正向原因。",
        "周期映射：用户说的月线，在五号聚合K体系中优先对应20日聚合K；季线≈60日聚合K；年线≈250日聚合K。",
        "命名规则：截图颜色只是讲课临时标注，正式报告和代码只允许使用“核心线”“逻辑分析线”。",
        f"历史K线来源：{json.dumps(hist_source_count, ensure_ascii=False)}",
        "",
        "一、今日正向/确认原因高频统计：",
    ]
    lines += [f"- {k}：{v}只" for k, v in sorted(positive_count.items(), key=lambda x: x[1], reverse=True)] if positive_count else ["- 未识别出足够正向原因；禁止硬凑。"]
    lines += ["", "二、背景过程统计，注意不算正向原因："]
    lines += [f"- {k}：{v}只" for k, v in sorted(background_count.items(), key=lambda x: x[1], reverse=True)] if background_count else ["- 无明显背景过程命中。"]
    lines += ["", "三、深度样本原因归因："]
    for i, item in enumerate(deep, 1):
        model = item.get("cause_model", {})
        lines.append(f"\n【样本{i}】{item.get('name')}({item.get('code')})｜20日/月线涨幅{item.get('returns',{}).get('20d')}%｜K线源={item.get('hist_source')}")
        lines.append("正向/确认命中：" + ("；".join(model.get("positive_ids", [])) if model.get("positive_ids") else "未命中，保留未识别结论"))
        lines.append("背景过程命中：" + ("；".join(model.get("background_ids", [])) if model.get("background_ids") else "无"))
        core = model.get("core_line")
        logic = model.get("logic_analysis_line")
        lines.append(f"核心线识别：{core.get('evidence') if core else '未识别；不能硬画核心线'}")
        lines.append(f"逻辑分析线识别：{logic.get('pair',{}).get('evidence') if logic else '未识别；不能硬画逻辑分析线'}")
        for e in item.get("events", []):
            if e.get("hit"):
                mark = "⚙️" if e.get("role") == "背景过程" else "✅"
                lines.append(f"{mark} {e['id']}【{e['role']}｜{e['name']}】{e['evidence']}")
    lines += ["", "四、五号质量门槛：", "- 背景过程不能冒充正向原因。", "- 不输出股票买入分、推荐分、交易优先级分。", "- 每个口径必须有公式、阈值、证据；未识别就写未识别。", "- 当前口径池是今天讲课内容的第一批量化落地，后续只做增量优化。"]
    text = "\n".join(lines)
    out_json = {"target_date": target_date, "hist_source_count": hist_source_count, "positive_type_count": positive_count, "background_type_count": background_count, "deep_samples": deep, "cause_pool_version": "20260524_quantified_initial_pool_no_color_names"}
    (REPORT_DIR / "limit_up_cause_pool_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "limit_up_cause_pool_report.json").write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "limit_up_structural_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "limit_up_structural_report.json").write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")
    print(text, flush=True)
    base.send_msg(text)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = "❌ 五号员工暴涨原因口径池报告失败\n" + str(e) + "\n" + traceback.format_exc()
        print(err, flush=True)
        base.send_msg(err)
        raise
