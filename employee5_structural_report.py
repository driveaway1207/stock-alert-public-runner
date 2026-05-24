# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

import employee5_runner as base

REPORT_DIR = Path(__file__).resolve().parent / "employee5_reports"
_KEY = os.getenv("TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_TOKEN", "")
_DEST = os.getenv("TELEGRAM_CHAT_ID", "")


def sf(x: Any, default: float = 0.0) -> float:
    return base.sf(x, default)


def rd(x: Any, n: int = 2) -> float:
    return base.rd(x, n)


def div(a: Any, b: Any) -> float:
    return base.div(a, b)


def pct(a: Any, b: Any) -> float:
    return base.pct(a, b)


def ss(x: Any) -> str:
    return base.ss(x)


def send_msg(text: str) -> None:
    if not _KEY or not _DEST:
        print("employee5 cause-pool telegram missing; skip", flush=True)
        return
    chunks, buf = [], ""
    for line in text.splitlines():
        if len(buf) + len(line) + 1 > 3500:
            if buf:
                chunks.append(buf)
            buf = line
        else:
            buf = line if not buf else buf + "\n" + line
    if buf:
        chunks.append(buf)
    url = "https://api." + "tele" + "gram.org/bot" + _KEY + "/sendMessage"
    for i, chunk in enumerate(chunks or [text[:3500]], 1):
        r = requests.post(url, json={"chat_id": _DEST, "text": chunk, "disable_web_page_preview": True}, timeout=30)
        print(f"employee5 cause-pool chunk {i} status={r.status_code}", flush=True)
        time.sleep(0.35)


def aggregate_n_bars(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """把日K按固定N根聚合。五号口径：20日聚合K≈用户说的月线，60日≈季线，250日≈年线。"""
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy().reset_index(drop=True)
    rows: List[Dict[str, Any]] = []
    for start in range(0, len(d), n):
        sub = d.iloc[start:start+n]
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
            "bars": int(len(sub)),
        })
    return pd.DataFrame(rows)


def candle(row: pd.Series) -> Dict[str, float]:
    op, hi, lo, cl = sf(row.open), sf(row.high), sf(row.low), sf(row.close)
    rng = max(hi - lo, 1e-6)
    body = abs(cl - op)
    return {
        "is_yang": cl > op,
        "entity_pct": rd((cl / op - 1) * 100 if op else 0),
        "body_ratio": rd(body / rng, 3),
        "close_pos": rd((cl - lo) / rng, 3),
        "upper_ratio": rd(max(hi - max(op, cl), 0) / rng, 3),
        "lower_ratio": rd(max(min(op, cl) - lo, 0) / rng, 3),
        "body_floor": min(op, cl),
        "body_top": max(op, cl),
        "body_mid": (op + cl) / 2,
        "body_upper_third": min(op, cl) + abs(cl - op) * 2 / 3,
    }


def rel_volume(df: pd.DataFrame, idx: int, prefer: int = 20) -> Dict[str, Any]:
    """局部相对量能：先看同级聚合K，历史不足时自动降级窗口。"""
    if df.empty or idx < 0 or idx >= len(df):
        return {"ratio": 0.0, "window": 0, "rank_pct": 0.0, "reason": "no-data"}
    windows = [prefer, 10, 5, 3, 1]
    chosen = 0
    mean_vol = 0.0
    for w in windows:
        if idx >= w:
            chosen = w
            mean_vol = sf(df.iloc[idx-w:idx].volume.mean())
            break
    if not chosen and idx > 0:
        chosen = idx
        mean_vol = sf(df.iloc[:idx].volume.mean())
    v = sf(df.iloc[idx].volume)
    start = max(0, idx - max(chosen, 12))
    hist = [sf(x) for x in df.iloc[start:idx+1].volume.tolist() if sf(x) > 0]
    rank_pct = sum(1 for x in hist if x <= v) / len(hist) if hist else 0.0
    return {"ratio": rd(div(v, mean_vol)), "window": int(chosen), "rank_pct": rd(rank_pct, 3), "mean_volume": rd(mean_vol, 0), "volume": rd(v, 0)}


def is_local_high_volume(df: pd.DataFrame, idx: int, min_ratio: float = 1.5) -> bool:
    rv = rel_volume(df, idx)
    return bool(rv["ratio"] >= min_ratio or rv["rank_pct"] >= 0.85)


def strong_yang_quality(row: pd.Series, period_n: int = 20, vol_ratio: float = 0.0) -> Dict[str, Any]:
    c = candle(row)
    # 初始阈值：后续用样本校准。20日聚合K约月线；量越大，对实体与收盘要求越高。
    if period_n >= 20:
        if vol_ratio >= 5:
            req_entity, req_body, req_close, req_upper = 30, 0.60, 0.75, 0.25
        elif vol_ratio >= 3:
            req_entity, req_body, req_close, req_upper = 22, 0.55, 0.70, 0.30
        else:
            req_entity, req_body, req_close, req_upper = 15, 0.50, 0.65, 0.35
    else:
        req_entity, req_body, req_close, req_upper = 8, 0.50, 0.65, 0.35
    ok = bool(c["is_yang"] and c["entity_pct"] >= req_entity and c["body_ratio"] >= req_body and c["close_pos"] >= req_close and c["upper_ratio"] <= req_upper)
    return {"ok": ok, "metrics": c, "required": {"entity_pct": req_entity, "body_ratio": req_body, "close_pos": req_close, "upper_ratio": req_upper}}


def volume_flat_grade(v1: float, v2: float) -> Dict[str, Any]:
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


def find_high_volume_flat_pair(df20: pd.DataFrame) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    for i in range(1, len(df20) - 1):
        r1, r2 = df20.iloc[i], df20.iloc[i+1]
        rv1, rv2 = rel_volume(df20, i), rel_volume(df20, i+1)
        flat = volume_flat_grade(sf(r1.volume), sf(r2.volume))
        q1 = strong_yang_quality(r1, 20, rv1["ratio"])
        c1, c2 = candle(r1), candle(r2)
        no_destroy = sf(r2.close) >= c1["body_upper_third"]
        high_vol = rv1["ratio"] >= 1.8 or rv2["ratio"] >= 1.8 or rv1["rank_pct"] >= 0.85 or rv2["rank_pct"] >= 0.85
        if flat["flat"] and high_vol and q1["ok"]:
            quality = 3
            if no_destroy:
                quality += 2
            if flat["grade"] == "强平量":
                quality += 2
            if best is None or quality > best["quality"]:
                best = {"idx1": i, "idx2": i+1, "date1": ss(r1.date), "date2": ss(r2.date), "rv1": rv1, "rv2": rv2, "flat": flat, "q1": q1, "no_destroy": no_destroy, "white_line": c1["body_floor"], "quality": quality, "evidence": f"{ss(r1.date)}/{ss(r2.date)} 相邻高倍量平量，{flat['grade']} 差异{flat['diff_pct']}%，第一根实底={rd(c1['body_floor'])}，第二根收盘守上三分之一={no_destroy}"}
    return best


def find_failed_max_impulse(df20: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df20) < 8:
        return None
    idx = int(df20.iloc[:-1].volume.idxmax())
    r = df20.loc[idx]
    rv = rel_volume(df20, idx)
    q = strong_yang_quality(r, 20, rv["ratio"])
    c = candle(r)
    if not (c["is_yang"] and (rv["ratio"] >= 1.8 or rv["rank_pct"] >= 0.9)):
        return None
    post = df20.iloc[idx+1:min(len(df20), idx+8)]
    if post.empty:
        return None
    broke_low = any(sf(x) < sf(r.low) for x in post.close.tolist())
    broke_floor = any(sf(x) < c["body_floor"] for x in post.close.tolist())
    volumes = post.volume.tolist()
    decay = len(volumes) >= 2 and sf(volumes[-1]) < sf(volumes[0])
    if broke_low or broke_floor:
        return {"idx": idx, "date": ss(r.date), "high": sf(r.high), "low": sf(r.low), "body_floor": c["body_floor"], "rv": rv, "quality": q, "broke_low": broke_low, "broke_floor": broke_floor, "decay": decay, "evidence": f"最大量强阳{ss(r.date)}，局部量比={rv['ratio']}，后续跌破实底={broke_floor}/虚底={broke_low}，后续缩量={decay}"}
    return None


def find_second_effort_line(df20: pd.DataFrame, failed: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not failed:
        return None
    start = int(failed["idx"]) + 1
    end = len(df20) - 1
    if end - start < 3:
        return None
    sub = df20.iloc[start:end]
    idx = int(sub.volume.idxmax())
    r = df20.iloc[idx]
    rv = rel_volume(df20, idx)
    lower_high = sf(r.high) < sf(failed["high"]) * 0.995
    visible = rv["rank_pct"] >= 0.80 or rv["ratio"] >= 1.3
    if visible:
        return {"idx": idx, "date": ss(r.date), "line": sf(r.high), "rv": rv, "lower_high": lower_high, "evidence": f"失败后缩量段二次努力{ss(r.date)}，高点={rd(r.high)}，局部量比={rv['ratio']}，低于前最大强阳高点={lower_high}"}
    return None


def high_points_near(df20: pd.DataFrame, level: float, right_idx: int, tol_pct: float = 6.0) -> List[Dict[str, Any]]:
    pts: List[Dict[str, Any]] = []
    if level <= 0:
        return pts
    for i in range(0, max(0, right_idx - 3)):
        r = df20.iloc[i]
        diff = abs(sf(r.high) - level) / level * 100
        if diff <= tol_pct:
            rv = rel_volume(df20, i)
            c = candle(r)
            upper_reaction = c["upper_ratio"] >= 0.18 or sf(r.high) >= max(sf(r.open), sf(r.close)) * 1.03
            if upper_reaction and (rv["ratio"] >= 1.3 or rv["rank_pct"] >= 0.80):
                pts.append({"idx": i, "date": ss(r.date), "high": sf(r.high), "diff_pct": rd(diff), "rv": rv})
    return pts


def clustered_breaches(df20: pd.DataFrame, level: float, left_idx: int, right_idx: int) -> Dict[str, Any]:
    breaches: List[int] = []
    if level <= 0 or right_idx <= left_idx:
        return {"ok": True, "breach_count": 0, "clustered": True, "span": 0}
    for i in range(left_idx + 1, right_idx):
        r = df20.iloc[i]
        if sf(r.low) <= level <= sf(r.high):
            # 只有实体或收盘显著穿越才记为隔断；轻微影线不算严重。
            op, cl = sf(r.open), sf(r.close)
            if min(op, cl) <= level <= max(op, cl) or abs(cl - level) / level <= 0.03:
                breaches.append(i)
    if not breaches:
        return {"ok": True, "breach_count": 0, "clustered": True, "span": 0}
    span = max(breaches) - min(breaches) + 1
    clustered = span <= 4 or span / max(1, right_idx - left_idx) <= 0.25
    ok = clustered and len(breaches) <= 6
    return {"ok": ok, "breach_count": len(breaches), "clustered": clustered, "span": int(span)}


def detect_core_green_line(df20: pd.DataFrame, second: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not second:
        return None
    level = sf(second["line"])
    pts = high_points_near(df20, level, int(second["idx"]), tol_pct=6.0)
    if not pts:
        return None
    best = min(pts, key=lambda x: x["diff_pct"])
    br = clustered_breaches(df20, level, int(best["idx"]), int(second["idx"]))
    if br["ok"]:
        return {"line": level, "left": best, "right": second, "breaches": br, "evidence": f"绿线={rd(level)}，左侧{best['date']}带量上影高点差异{best['diff_pct']}%，右侧{second['date']}二次努力高点，中间隔断={br}"}
    return None


def count_reactions_right(df20: pd.DataFrame, level: float, start_idx: int, tol_pct: float = 5.0) -> Dict[str, Any]:
    hits = []
    for i in range(max(0, start_idx + 1), len(df20)):
        r = df20.iloc[i]
        vals = [sf(r.open), sf(r.close), sf(r.high), sf(r.low)]
        if any(level > 0 and abs(v - level) / level * 100 <= tol_pct for v in vals if v > 0):
            hits.append({"idx": i, "date": ss(r.date)})
    return {"count": len(hits), "dates": [x["date"] for x in hits[:6]]}


def volume_decay_regular(df20: pd.DataFrame, level: float, start_idx: int) -> Optional[Dict[str, Any]]:
    for i in range(start_idx + 1, min(len(df20) - 2, start_idx + 8)):
        sub = df20.iloc[i:i+3]
        if len(sub) < 3:
            continue
        bearish = all(sf(r.close) < sf(r.open) for _, r in sub.iterrows())
        closes_below = any(sf(r.close) < level for _, r in sub.iterrows())
        vols = [sf(x) for x in sub.volume.tolist()]
        if bearish and closes_below and vols[0] > vols[1] > vols[2] > 0:
            d1 = (vols[0] - vols[1]) / vols[0] * 100
            d2 = (vols[1] - vols[2]) / vols[1] * 100
            regular = abs(d1 - d2) <= 8 and 5 <= d1 <= 25 and 5 <= d2 <= 25
            if regular:
                return {"idx": i, "dates": [ss(x) for x in sub.date.tolist()], "decay_pct": [rd(d1), rd(d2)], "evidence": f"三根小阴{[ss(x) for x in sub.date.tolist()]}，缩量比例={rd(d1)}%/{rd(d2)}%，跌破逻辑线但量能规律收缩"}
    return None


def bullish_ladder(df20: pd.DataFrame, level: float = 0.0) -> Optional[Dict[str, Any]]:
    for i in range(max(0, len(df20) - 16), max(0, len(df20) - 3)):
        for length in [3, 4, 5]:
            sub = df20.iloc[i:i+length]
            if len(sub) < length:
                continue
            yang = [sf(r.close) > sf(r.open) for _, r in sub.iterrows()]
            vols = [sf(x) for x in sub.volume.tolist()]
            entities = [abs(sf(r.close) - sf(r.open)) / max(sf(r.open), 1e-6) * 100 for _, r in sub.iterrows()]
            if sum(yang) >= length - 1 and all(vols[j] < vols[j+1] for j in range(len(vols)-1)):
                last_strong = entities[-1] >= max(entities[:-1] or [0]) and sf(sub.iloc[-1].close) > sf(sub.iloc[-1].open)
                breaks_level = sf(sub.iloc[-1].close) >= level if level else True
                if last_strong and breaks_level:
                    return {"idx": i, "dates": [ss(x) for x in sub.date.tolist()], "length": length, "evidence": f"阳梯量{[ss(x) for x in sub.date.tolist()]}，量能逐级递增，最后一根实体最大/最强，突破参考线={breaks_level}"}
    return None


def post_breakout_acceptance(df20: pd.DataFrame, level: float) -> Optional[Dict[str, Any]]:
    if level <= 0:
        return None
    for i in range(1, len(df20) - 2):
        prev = df20.iloc[i-1]
        cur = df20.iloc[i]
        if sf(prev.close) < level <= sf(cur.close):
            avg = sf(df20.iloc[max(0, i-6):i].volume.mean())
            post = df20.iloc[i:min(len(df20), i+4)]
            above_vol = sum(1 for x in post.volume.tolist() if sf(x) >= avg) if avg else 0
            no_fall_back = all(sf(x) >= level * 0.97 for x in post.close.tolist())
            if above_vol >= 2 and no_fall_back:
                return {"idx": i, "date": ss(cur.date), "evidence": f"{ss(cur.date)}突破核心线{rd(level)}，后续{above_vol}根量能在均量上方，未快速跌回"}
    return None


def core_retest_support(df20: pd.DataFrame, level: float) -> Optional[Dict[str, Any]]:
    if level <= 0:
        return None
    br = post_breakout_acceptance(df20, level)
    if not br:
        return None
    for i in range(int(br["idx"]) + 1, len(df20) - 1):
        r = df20.iloc[i]
        near = sf(r.low) <= level * 1.08 and sf(r.high) >= level * 0.96
        hold = sf(r.close) >= level * 0.97
        if near and hold:
            return {"idx": i, "date": ss(r.date), "evidence": f"突破后{ss(r.date)}回踩核心线{rd(level)}附近，收盘未有效跌破，形成压力转支撑验证"}
    return None


def flat_volume_bullish_engulf(df20: pd.DataFrame, level: float) -> Optional[Dict[str, Any]]:
    for i in range(max(1, len(df20) - 8), len(df20)):
        p, c = df20.iloc[i-1], df20.iloc[i]
        prev_bear = sf(p.close) < sf(p.open)
        bull = sf(c.close) > sf(c.open)
        engulf = bull and prev_bear and sf(c.close) >= max(sf(p.open), sf(p.close))
        flat = volume_flat_grade(sf(p.volume), sf(c.volume))
        above_core = level <= 0 or sf(c.close) >= level * 0.97
        if engulf and flat["flat"] and above_core:
            return {"idx": i, "date": ss(c.date), "evidence": f"{ss(c.date)}平量阳包阴，量差{flat['diff_pct']}%({flat['grade']})，位于核心线附近/上方={above_core}"}
    return None


def reason(no: str, name: str, hit: bool, evidence: str, folk: str, wall: str, behavior: str, pre_visible: str = "是") -> Dict[str, Any]:
    return {"id": no, "name": name, "hit": bool(hit), "evidence": evidence, "folk": folk, "wall_street_mapping": wall, "market_behavior": behavior, "pre_visible": pre_visible}


def build_reason_pool(hist: pd.DataFrame) -> Dict[str, Any]:
    df20 = aggregate_n_bars(hist, 20)
    df60 = aggregate_n_bars(hist, 60)
    failed = find_failed_max_impulse(df20)
    second = find_second_effort_line(df20, failed)
    green = detect_core_green_line(df20, second)
    pair = find_high_volume_flat_pair(df20)
    white_line = sf(pair["white_line"]) if pair else 0.0
    white_start = int(pair["idx1"]) if pair else 0
    decay = volume_decay_regular(df20, white_line, white_start) if pair else None
    white_react = count_reactions_right(df20, white_line, white_start) if pair else {"count": 0, "dates": []}
    ladder_white = bullish_ladder(df20, white_line) if pair else None
    green_line = sf(green["line"]) if green else 0.0
    ladder_green = bullish_ladder(df20, green_line) if green else None
    accept = post_breakout_acceptance(df20, green_line) if green else None
    retest = core_retest_support(df20, green_line) if green else None
    engulf = flat_volume_bullish_engulf(df20, green_line) if green else None
    left_mean = sf(df20.iloc[max(0, white_start-6):white_start].volume.mean()) if pair else 0
    right_mean = sf(df20.iloc[white_start+1:min(len(df20), white_start+8)].volume.mean()) if pair else 0
    right_improve = bool(pair and left_mean > 0 and right_mean > left_mean * 1.15)
    reasons = [
        reason("R001", "大周期高量强阳失败/物极必反", failed is not None, failed["evidence"] if failed else "未识别最大量强阳失败链", "物极必反：最大量大阳被后续缩量阴线跌坏", "high-volume impulse failure / failed acceptance", "最高级别主动攻击没有被市场接受，后续形成失败事件"),
        reason("R002", "强阳失败后的二次努力供应线", second is not None, second["evidence"] if second else "未识别失败后二次努力高点", "第一次最大努力失败后，第二次放量努力的极限高点", "effort vs result / lower high on renewed volume", "资金再次努力但仍未完全解放左侧供应，形成供应反应位"),
        reason("R003", "隔断式长周期带量双上影线共振核心线", green is not None, green["evidence"] if green else "未识别双端带量上影共振核心线", "隔断式长周期双上影线共振，允许集中隔断，不允许多年乱穿", "market memory level / supply reaction confluence", "不同历史阶段在同一区域发生供应反应，形成长期市场记忆"),
        reason("R004", "局部相对量能确认", True, "所有带量反应点均按所处阶段的局部相对量能判断；20日聚合K不足时需下钻周线/日线验证", "历史量能不能跨年份绝对比较，要看当时是否显眼", "relative volume / contextual volume significance", "量能价值来自局部阶段突出性，而不是绝对大小"),
        reason("R005", "大周期并肩高倍量平量强阳结构", pair is not None, pair["evidence"] if pair else "未识别严格相邻的高倍量平量强阳结构", "并肩必须相邻；平量必须严格；第一根实体好，第二根不破坏", "volume consistency + sustained initiative demand + price acceptance", "连续两个高周期里资金强度高度接近，并且价格推进被接受"),
        reason("R006", "大周期高倍量量价合一", pair is not None and pair["q1"]["ok"], json.dumps(pair["q1"] if pair else {}, ensure_ascii=False), "月线/20日聚合K高倍量必须配得上大实体、短上影、强收盘", "effort vs result", "成交量是努力，实体推进和收盘质量是结果；高量低效要归为风险"),
        reason("R007", "逻辑分析线", pair is not None, f"白线/逻辑线={rd(white_line)}，来自并肩高倍量强阳实底；注意它不是核心线" if pair else "未生成白线逻辑分析线", "白线不是核心线，而是解释资金状态切换和右侧逻辑的分析线", "change-of-character anchor / regime shift reference", "用一条逻辑线观察右侧量价是否从冷转热"),
        reason("R008", "启动式高倍量平量后的右侧量能改善", right_improve, f"左侧均量={rd(left_mean,0)}，右侧均量={rd(right_mean,0)}，改善={right_improve}", "启动后右侧量能逐渐好转", "volume regime shift / change of character", "资金参与状态从低迷转为活跃"),
        reason("R009", "逻辑线跌破后的规律性缩量小阴", decay is not None, decay["evidence"] if decay else "未识别逻辑线跌破后的三小阴规律缩量", "跌破不一定坏，关键看是否小阴、规律缩量、均量抬升", "controlled pullback / orderly volume decay", "价格回落但资金没有恐慌撤退，更像可控消化"),
        reason("R010", "逻辑分析线右侧多点共振", white_react["count"] >= 3, f"右侧共振次数={white_react['count']}，样例={white_react['dates']}", "白线画出后，右侧锤子线/实体/上影线继续记住它", "multi-touch validation / reaction clustering", "逻辑线被后续市场多次验证"),
        reason("R011", "规则阳梯量推进", ladder_green is not None or ladder_white is not None, (ladder_green or ladder_white or {}).get("evidence", "未识别规则阳梯量推进"), "量能逐级递增，最后一根量最大且实体最强", "progressive demand expansion / initiative buying buildup", "主动需求有节奏增强，价格推进同步增强"),
        reason("R012", "核心线突破后的接受确认", accept is not None, accept["evidence"] if accept else "未识别核心线突破后的持续接受", "突破核心绿线后量能仍在均量上方，不快速退潮", "acceptance above breakout level", "突破后市场继续在新价格区间成交并接受"),
        reason("R013", "核心线回踩支撑确认", retest is not None, retest["evidence"] if retest else "未识别核心线突破后的回踩支撑", "绿线由压力转支撑，回落不有效跌破", "breakout-retest / resistance turns support", "历史供应被吸收后，核心线转为支撑"),
        reason("R014", "核心线上方平量阳包阴", engulf is not None, engulf["evidence"] if engulf else "未识别核心线上方平量阳包阴", "绿线支撑后出现平量阳包阴，稳定反包", "stable participation reversal", "核心线支撑后，资金用稳定量能完成修复"),
    ]
    return {"df20_rows": len(df20), "df60_rows": len(df60), "green_line": green, "white_line": {"line": white_line, "pair": pair} if pair else None, "reasons": reasons}


def summarize_reason_hits(deep: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in deep:
        for r in item.get("reasons", []):
            if r.get("hit"):
                key = f"{r['id']} {r['name']}"
                counts[key] = counts.get(key, 0) + 1
    return counts


def hit_ids(item: Dict[str, Any]) -> List[str]:
    return [f"{r['id']} {r['name']}" for r in item.get("reasons", []) if r.get("hit")]


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
        code, name, board = ss(item.get("code")), ss(item.get("name")), ss(item.get("board"))
        hist = base.fetch_hist(code, target_date)
        if hist.empty or len(hist) < 30:
            continue
        source = ss(hist.get("hist_source", pd.Series(["unknown"])).iloc[-1]) if "hist_source" in hist.columns else "unknown"
        hist_source_count[source] = hist_source_count.get(source, 0) + 1
        cause = build_reason_pool(hist)
        one = dict(item)
        one.update({"hist_source": source, "reason_model": cause, "reasons": cause["reasons"]})
        deep.append(one)
    reason_count = summarize_reason_hits(deep)
    lines = [
        "🧬【五号员工-暴涨原因口径池报告】",
        f"日期：{target_date}",
        f"耗时：{base.fmt_seconds(time.time()-start)}",
        "口径：五号不是选股打分器；本报告只做大涨原因标签、共振统计和反证记录。",
        "周期映射：用户说的月线，在五号聚合K体系中优先对应20日聚合K；季线≈60日聚合K；年线≈250日聚合K。",
        "融合原则：民间战法与华尔街体系必须映射到底层市场行为；同源逻辑不得重复计分。",
        f"历史K线来源：{json.dumps(hist_source_count, ensure_ascii=False)}",
        "",
        "一、今日高频暴涨原因口径：",
    ]
    if reason_count:
        for k, v in sorted(reason_count.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- {k}：{v}只")
    else:
        lines.append("- 本次深度样本未形成高频原因命中；禁止硬凑维度。")
    lines += ["", "二、深度样本原因归因："]
    for i, item in enumerate(deep, 1):
        lines.append(f"\n【样本{i}】{item.get('name')}({item.get('code')})｜20日/月线涨幅{item.get('returns',{}).get('20d')}%｜K线源={item.get('hist_source')}")
        lines.append("命中原因：" + ("；".join(hit_ids(item)) if hit_ids(item) else "未命中当前R001-R014原因池，需保留未识别结论"))
        gm = item.get("reason_model", {}).get("green_line")
        wm = item.get("reason_model", {}).get("white_line")
        lines.append(f"核心线/绿线识别：{gm.get('evidence') if gm else '未识别；不能硬画核心线'}")
        lines.append(f"逻辑线/白线识别：{wm.get('pair',{}).get('evidence') if wm else '未识别；不能硬画逻辑线'}")
        for r in item.get("reasons", []):
            if r.get("hit"):
                lines.append(f"✅ {r['id']}【{r['name']}】{r['evidence']}")
        miss = [r for r in item.get("reasons", []) if not r.get("hit")]
        if miss:
            lines.append("未命中/反证提示：" + "；".join([f"{r['id']} {r['name']}" for r in miss[:5]]))
    lines += ["", "三、五号质量门槛：", "- 不输出股票买入分、推荐分、交易优先级分。", "- 不把涨幅、普通突破、调试字段当暴涨原因。", "- 只统计原因口径命中与共振；未识别就写未识别。", "- 当前R001-R014只是第一批口径，后续根据用户人工复盘继续扩充。"]
    text = "\n".join(lines)
    out_json = {"target_date": target_date, "hist_source_count": hist_source_count, "reason_type_count": reason_count, "deep_samples": deep, "reason_pool_version": "R001-R014 initial cause-pool from 688260 manual review"}
    (REPORT_DIR / "limit_up_cause_pool_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "limit_up_cause_pool_report.json").write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "limit_up_structural_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "limit_up_structural_report.json").write_text(json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")
    print(text, flush=True)
    send_msg(text)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = "❌ 五号员工暴涨原因口径池报告失败\n" + str(e) + "\n" + traceback.format_exc()
        print(err, flush=True)
        send_msg(err)
        raise
