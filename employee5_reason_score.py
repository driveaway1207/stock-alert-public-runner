# -*- coding: utf-8 -*-
"""
五号员工：归因口径命中强度评分。

本脚本只给“已经进入五号深度归因的涨停/大涨样本”计算归因解释强度，
不输出买入分、推荐分、交易优先级分。

评分含义：这只大涨股背后的可解释原因链条有多完整。
不是：明天是否可以买。
"""
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Tuple

import employee5_runner as base

REPORT_DIR = Path(__file__).resolve().parent / "employee5_reports"
INPUT_JSON = REPORT_DIR / "limit_up_cause_pool_report.json"
OUTPUT_MD = REPORT_DIR / "limit_up_reason_score_report.md"
OUTPUT_JSON = REPORT_DIR / "limit_up_reason_score_report.json"

# 口径角色权重。背景过程只是背景，权重低；结构、正向、确认权重更高。
# 基础规则 VOL001 属于计算前提，不用于抬高归因分。
ROLE_SCORE_WEIGHT = {
    "背景过程": 4,
    "结构原因": 8,
    "正向原因": 10,
    "确认原因": 12,
    "分析锚点": 5,
    "基础规则": 0,
    "风险反证": -8,
}

ROLE_ORDER = ["背景过程", "结构原因", "正向原因", "确认原因", "分析锚点", "基础规则", "风险反证"]


def sf(x: Any, default: float = 0.0) -> float:
    return base.sf(x, default)


def ss(x: Any) -> str:
    return base.ss(x)


def reason_grade(score: float) -> str:
    if score >= 85:
        return "归因链非常完整"
    if score >= 70:
        return "归因链较完整"
    if score >= 55:
        return "归因链中等"
    if score >= 35:
        return "归因链偏弱"
    return "归因链较弱"


def score_one_sample(item: Dict[str, Any]) -> Dict[str, Any]:
    events = item.get("events", []) or []
    hit_events = [e for e in events if e.get("hit")]

    role_counts: Dict[str, int] = {role: 0 for role in ROLE_ORDER}
    role_points: Dict[str, float] = {role: 0.0 for role in ROLE_ORDER}
    hit_ids: List[str] = []
    scored_hit_ids: List[str] = []

    for e in hit_events:
        role = ss(e.get("role")) or "未分类"
        eid = ss(e.get("id"))
        name = ss(e.get("name"))
        key = f"{eid} {name}".strip()
        hit_ids.append(key)
        if role not in role_counts:
            role_counts[role] = 0
            role_points[role] = 0.0
        role_counts[role] += 1
        weight = ROLE_SCORE_WEIGHT.get(role, 0)
        role_points[role] += weight
        if weight != 0:
            scored_hit_ids.append(key)

    raw_score = sum(role_points.values())
    # 归因分只用于解释强度，封顶100；不因基础规则自动加分。
    score = max(0.0, min(100.0, raw_score))
    valid_reason_count = sum(role_counts.get(role, 0) for role in ["背景过程", "结构原因", "正向原因", "确认原因", "分析锚点", "风险反证"])
    positive_reason_count = sum(role_counts.get(role, 0) for role in ["结构原因", "正向原因", "确认原因", "分析锚点"])
    background_count = role_counts.get("背景过程", 0)
    confirmation_count = role_counts.get("确认原因", 0)

    return {
        "code": ss(item.get("code")),
        "name": ss(item.get("name")),
        "board": ss(item.get("board")),
        "hist_source": ss(item.get("hist_source")),
        "returns": item.get("returns", {}),
        "reason_score": round(score, 2),
        "reason_grade": reason_grade(score),
        "raw_hit_count": len(hit_events),
        "valid_reason_count": valid_reason_count,
        "positive_reason_count": positive_reason_count,
        "background_count": background_count,
        "confirmation_count": confirmation_count,
        "role_counts": role_counts,
        "role_points": {k: round(v, 2) for k, v in role_points.items()},
        "hit_ids": hit_ids,
        "scored_hit_ids": scored_hit_ids,
        "score_formula": "背景过程×4 + 结构原因×8 + 正向原因×10 + 确认原因×12 + 分析锚点×5 - 风险反证×8，基础规则不加分，封顶100",
    }


def build_report(data: Dict[str, Any], scored: List[Dict[str, Any]], elapsed: float) -> str:
    target_date = ss(data.get("target_date"))
    lines = [
        "🧬【五号员工-归因口径命中强度评分】",
        f"日期：{target_date}",
        f"耗时：{base.fmt_seconds(elapsed)}",
        "说明：这是大涨归因解释强度分，不是买入分、推荐分、交易优先级分。",
        "评分公式：背景过程×4 + 结构原因×8 + 正向原因×10 + 确认原因×12 + 分析锚点×5 - 风险反证×8；基础规则不加分；封顶100。",
        "原则：命中的有效口径越多，分值越高；确认原因比背景过程更值钱。",
        "",
        "一、三只深度样本归因分排名：",
    ]
    if not scored:
        lines.append("- 未生成评分：原因口径池报告中没有可评分深度样本。")
        return "\n".join(lines)

    ranked = sorted(scored, key=lambda x: (sf(x.get("reason_score")), sf(x.get("positive_reason_count")), sf(x.get("confirmation_count"))), reverse=True)
    for i, item in enumerate(ranked, 1):
        ret20 = item.get("returns", {}).get("20d")
        lines.append(
            f"{i}. {item['name']}({item['code']})｜归因分 {item['reason_score']}/100｜{item['reason_grade']}｜"
            f"有效口径{item['valid_reason_count']}个，正向/确认{item['positive_reason_count']}个，确认{item['confirmation_count']}个，背景{item['background_count']}个｜20日涨幅{ret20}%"
        )

    lines += ["", "二、逐股口径命中明细："]
    for item in ranked:
        lines.append(f"\n【{item['name']}({item['code']})】归因分：{item['reason_score']}/100｜{item['reason_grade']}")
        role_counts = item.get("role_counts", {})
        role_points = item.get("role_points", {})
        lines.append(
            "命中数量："
            f"背景{role_counts.get('背景过程', 0)}，"
            f"结构{role_counts.get('结构原因', 0)}，"
            f"正向{role_counts.get('正向原因', 0)}，"
            f"确认{role_counts.get('确认原因', 0)}，"
            f"分析锚点{role_counts.get('分析锚点', 0)}，"
            f"风险反证{role_counts.get('风险反证', 0)}。"
        )
        lines.append(
            "得分拆解："
            f"背景{role_points.get('背景过程', 0)} + "
            f"结构{role_points.get('结构原因', 0)} + "
            f"正向{role_points.get('正向原因', 0)} + "
            f"确认{role_points.get('确认原因', 0)} + "
            f"分析锚点{role_points.get('分析锚点', 0)} + "
            f"风险{role_points.get('风险反证', 0)}。"
        )
        hits = item.get("scored_hit_ids", [])
        if hits:
            lines.append("计分口径：" + "；".join(hits))
        else:
            lines.append("计分口径：无，说明本样本当前没有形成可解释的完整原因链。")

    lines += [
        "",
        "三、解释边界：",
        "- 分数越高，代表这只大涨股背后的口径链越完整、越容易被复盘解释。",
        "- 背景过程只是背景，不能单独解释大涨，所以权重低。",
        "- 确认原因代表突破、接受、回踩、修复被验证，所以权重最高。",
        "- 该分数不用于明日买入，不替代一号、二号、三号员工。",
    ]
    return "\n".join(lines)


def main() -> None:
    start = time.time()
    if not INPUT_JSON.exists():
        raise FileNotFoundError("missing employee5 cause pool report json: employee5_reports/limit_up_cause_pool_report.json")
    data = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    deep_samples = data.get("deep_samples", []) or []
    scored = [score_one_sample(item) for item in deep_samples]
    out = {
        "target_date": ss(data.get("target_date")),
        "score_name": "五号员工归因口径命中强度分",
        "not_trade_score": True,
        "score_formula": "背景过程×4 + 结构原因×8 + 正向原因×10 + 确认原因×12 + 分析锚点×5 - 风险反证×8，基础规则不加分，封顶100",
        "role_score_weight": ROLE_SCORE_WEIGHT,
        "samples": scored,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    text = build_report(data, scored, time.time() - start)
    OUTPUT_MD.write_text(text, encoding="utf-8")
    print(text, flush=True)
    base.send_msg(text)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = "❌ 五号员工归因口径命中强度评分失败\n" + str(e) + "\n" + traceback.format_exc()
        print(err, flush=True)
        base.send_msg(err)
        raise
