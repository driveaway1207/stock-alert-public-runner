from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import requests
except Exception:
    requests = None

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "employee3_reports"
OUTPUT_MD = REPORT_DIR / "core_line_breakout_screen.md"
OUTPUT_JSON = REPORT_DIR / "core_line_breakout_screen.json"
GUARD_JSON = REPORT_DIR / "employee3_report_guard.json"

BOT = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", os.getenv("EMPLOYEE3_SEND_TELEGRAM", "0"))


def now_bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
            return default
        return float(str(x).replace("%", "").replace(",", ""))
    except Exception:
        return default


def rd(x: Any, n: int = 2) -> float:
    return round(sf(x), n)


def norm_date(x: Any) -> str:
    s = ss(x)
    if not s:
        return ""
    s = s.replace("年", "-").replace("月", "-").replace("日", "")
    s = s.replace("/", "-").replace(".", "-").replace("_", "-")
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return s[:10]
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def load_payload() -> Dict[str, Any]:
    if not OUTPUT_JSON.exists():
        return {"rows": [], "stat": {}, "target_dash": "", "load_error": f"missing {OUTPUT_JSON}"}
    try:
        return json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"rows": [], "stat": {}, "target_dash": "", "load_error": f"json_load_error: {exc}"}


def get_row_date(row: Dict[str, Any]) -> str:
    for key in ("data_last_date", "数据截至", "last_date", "date"):
        d = norm_date(row.get(key))
        if d:
            return d
    detail = ss(row.get("data_freshness_detail"))
    if "数据截至" in detail:
        return norm_date(detail.split("数据截至", 1)[1].split("，", 1)[0])
    return ""


def row_text(row: Dict[str, Any]) -> str:
    keys = [
        "操作建议", "trade_action", "候选池", "deep_pool", "扣分原因", "deep_negative_reasons",
        "风险标签", "risk_flags", "当前状态", "deep_state", "technical_risk_detail",
        "trade_pricing_detail", "data_freshness_detail",
    ]
    return "；".join(ss(row.get(k)) for k in keys if ss(row.get(k)))


def is_data_mismatch(row: Dict[str, Any], target: str) -> bool:
    if row.get("data_is_target_fresh") is False:
        return True
    d = get_row_date(row)
    return bool(target and d and d != target)


def classify_row(row: Dict[str, Any], target: str) -> str:
    text = row_text(row)
    if is_data_mismatch(row, target) or "数据日期未对齐" in text:
        return "数据日期未对齐"
    if bool(row.get("risk_block")):
        if "突破失败" in text or "跌回线下" in text:
            return "突破失败跌回线下"
        return "risk_block硬风险"
    if "次新样本不足" in text or "次新股专项约束" in text:
        return "次新专项约束"
    if "硬风险" in text:
        return "硬风险"
    if "突破失败" in text or "跌回线下" in text:
        return "突破失败跌回线下"
    pool = ss(row.get("候选池") or row.get("deep_pool"))
    if "剔除" in text or pool == "剔除":
        return "其他硬剔除"
    if "不推" in text or pool == "不推":
        return "不推"
    if "观察" in text or pool == "观察候选":
        return "观察候选"
    return "未归类"


def is_hard_rejected(row: Dict[str, Any], target: str) -> bool:
    return classify_row(row, target) in {
        "数据日期未对齐", "突破失败跌回线下", "risk_block硬风险", "次新专项约束", "硬风险", "其他硬剔除"
    }


def grade_rank(g: Any) -> int:
    return {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}.get(ss(g).upper(), 0)


def top_rows(rows: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    def key(row: Dict[str, Any]) -> Tuple[int, float, float, str]:
        grade = row.get("深度等级") or row.get("deep_grade")
        score = row.get("深度得分") or row.get("deep_score")
        q = row.get("主评测线突破质量") or row.get("breakout_quality")
        date = ss(row.get("主评测线突破日期") or row.get("breakout_date"))
        return (grade_rank(grade), sf(score), sf(q), date)
    return sorted(rows, key=key, reverse=True)[:limit]


def summarize(rows: List[Dict[str, Any]], target: str) -> Dict[str, Any]:
    by_reason: Dict[str, int] = {}
    by_date: Dict[str, int] = {}
    hard = 0
    for row in rows:
        reason = classify_row(row, target)
        by_reason[reason] = by_reason.get(reason, 0) + 1
        hard += 1 if is_hard_rejected(row, target) else 0
        d = get_row_date(row) or "未知"
        by_date[d] = by_date.get(d, 0) + 1
    total = len(rows)
    data_mismatch = by_reason.get("数据日期未对齐", 0)
    fresh = total - data_mismatch
    return {
        "total_rows": total,
        "hard_rejected": hard,
        "data_mismatch": data_mismatch,
        "fresh_rows": fresh,
        "fresh_coverage": round(fresh / total, 4) if total else 0.0,
        "reason_counts": dict(sorted(by_reason.items(), key=lambda kv: kv[1], reverse=True)),
        "date_counts": dict(sorted(by_date.items(), key=lambda kv: kv[1], reverse=True)),
    }


def short(text: Any, n: int = 80) -> str:
    s = ss(text).replace("\n", "；")
    return s if len(s) <= n else s[: n - 1] + "…"


def format_counts(counts: Dict[str, int], limit: int = 8) -> str:
    return "；".join(f"{k}{v}只" for k, v in list(counts.items())[:limit]) if counts else "无"


def format_dates(counts: Dict[str, int], limit: int = 6) -> str:
    return "；".join(f"{k}:{v}" for k, v in list(counts.items())[:limit]) if counts else "无"


def build_guard_report(payload: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    stat = payload.get("stat") if isinstance(payload.get("stat"), dict) else {}
    target = norm_date(payload.get("target_dash") or os.getenv("EMPLOYEE3_TARGET_DATE") or os.getenv("TARGET_TRADE_DATE"))
    summary = summarize(rows, target)
    total = int(summary["total_rows"])
    hard = int(summary["hard_rejected"])
    data_mismatch = int(summary["data_mismatch"])
    coverage = float(summary["fresh_coverage"])
    load_error = ss(payload.get("load_error"))

    guard_status = "PASS"
    guard_action = "正常展示三号员工结果"
    title = f"三号员工Top5深度精选｜{target or payload.get('target') or ''}"
    if load_error:
        guard_status = "FAIL"
        guard_action = "报告JSON读取失败，停止正式推送"
        title = f"三号员工数据异常｜停止选股｜{target or ''}"
    elif total == 0:
        guard_status = "WARN"
        guard_action = "今日无核心线突破深度命中"
        title = f"三号员工Top5｜无深度命中｜{target or ''}"
    elif data_mismatch == total or (total >= 20 and coverage < 0.80):
        guard_status = "DATA_STALE"
        guard_action = "目标日缓存覆盖不足，停止正式选股；这不是无股票"
        title = f"三号员工数据未更新｜停止选股｜{target or ''}"
    elif hard == total:
        guard_status = "ALL_HARD_REJECTED"
        guard_action = "全部命中票被硬剔除，需按原因分布复盘"
        title = f"三号员工Top5观察池｜全硬剔除复盘｜{target or ''}"
    else:
        formal = [r for r in rows if ss(r.get("深度等级") or r.get("deep_grade")).upper() in {"S", "A"} and not is_hard_rejected(r, target)]
        title = f"三号员工Top5深度精选｜{target or ''}" if formal else f"三号员工Top5观察池｜无A级/S级｜{target or ''}"

    lines: List[str] = [
        title,
        f"守门状态:{guard_status}｜处理:{guard_action}",
        f"核心线突破深度命中{total}只｜硬剔除{hard}只｜数据未对齐{data_mismatch}只｜目标日覆盖率{coverage:.1%}",
        f"缓存文件{stat.get('cache_files', '未知')}｜有效缓存{stat.get('cache_hit', '未知')}｜坏缓存{stat.get('bad', 0)}｜短缓存{stat.get('short', 0)}",
        f"硬剔除/状态分布:{format_counts(summary['reason_counts'])}",
        f"数据日期分布:{format_dates(summary['date_counts'])}",
    ]
    if guard_status == "DATA_STALE":
        lines += ["", "结论：当前结果按数据异常处理，不按‘市场无票’处理。", "动作：先更新公共K线缓存，或手动允许三号员工 BaoStock 补拉最近K线后重跑。"]
    elif guard_status == "ALL_HARD_REJECTED":
        lines += ["", "结论：有深度命中，但全部被硬闸门挡住；按上方原因分布复盘。"]
    elif total == 0:
        lines += ["", "结论：今日没有识别到最近20日高质量突破核心线的深度命中。"]
    if load_error:
        lines.append(f"读取错误:{load_error}")

    display = top_rows(rows, 5)
    if display:
        lines += ["", "排查Top5｜仅按深度分/等级排序，不等于买入："]
        for idx, r in enumerate(display, 1):
            code = ss(r.get("股票代码") or r.get("code"))
            name = ss(r.get("股票中文名称") or r.get("name"))
            grade = ss(r.get("深度等级") or r.get("deep_grade"))
            score = rd(r.get("深度得分") or r.get("deep_score"), 2)
            line_price = rd(r.get("主评测线价位") or r.get("line"), 3)
            br_date = ss(r.get("主评测线突破日期") or r.get("breakout_date"))
            reason = classify_row(r, target)
            action = short(r.get("操作建议") or r.get("trade_action"), 46)
            data_date = get_row_date(r) or "未知"
            lines += [
                f"{idx}. {code} {name}｜{grade} {score}分｜线:{line_price}｜破:{br_date}｜数据:{data_date}",
                f"   状态:{reason}｜{action}",
            ]

    report = "\n".join(lines)
    if len(report) > 3900:
        report = report[:3850].rstrip() + "\n……\n报告过长，已压缩；完整明细见CSV/JSON。"
    guard = {
        "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "guard_status": guard_status,
        "guard_action": guard_action,
        "target_dash": target,
        "summary": summary,
        "stat": stat,
    }
    return report, guard


def send_report(md: str) -> None:
    print(f"guard_telegram_env enable={ENABLE_TELEGRAM} token={bool(BOT)} chat={bool(CHAT)} requests={requests is not None}", flush=True)
    if ENABLE_TELEGRAM != "1" or not BOT or not CHAT or requests is None:
        print("guard telegram skipped; report preview below:", flush=True)
        print(md[:3000], flush=True)
        return
    url = f"https://api.telegram.org/bot{BOT}/sendMessage"
    chunks = [md[i:i + 3600] for i in range(0, len(md), 3600)] or [md]
    for idx, part in enumerate(chunks, 1):
        try:
            resp = requests.post(url, json={"chat_id": CHAT, "text": part, "disable_web_page_preview": True}, timeout=30)
            print(f"guard telegram chunk {idx} status={getattr(resp, 'status_code', 'NA')} body={getattr(resp, 'text', '')[:120]}", flush=True)
        except Exception as exc:
            print(f"guard telegram failed chunk {idx}: {exc}", flush=True)
        time.sleep(0.4)


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = load_payload()
    md, guard = build_guard_report(payload)
    OUTPUT_MD.write_text(md, encoding="utf-8")
    GUARD_JSON.write_text(json.dumps(guard, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"employee3_report_guard status={guard.get('guard_status')} action={guard.get('guard_action')}", flush=True)
    print(md[:3000], flush=True)
    send_report(md)


if __name__ == "__main__":
    main()
