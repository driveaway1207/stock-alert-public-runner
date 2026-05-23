# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "employee3_reports"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def bj_now() -> str:
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def send_tg(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram missing; skip")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text[:3900], "disable_web_page_preview": True}, timeout=30)


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    pools = {
        "employee1": load_json(ROOT / "stock_candidates.json", {}),
        "employee2": load_json(ROOT / "market_context.json", {}),
        "strategy_pool": load_json(ROOT / "strategy_pool.json", {}),
        "zero_constraints": load_json(ROOT / "zero_employee_reports" / "audit_runtime_constraints.json", {}),
    }
    report = {
        "generated_at_bj": bj_now(),
        "role": "三号员工-最终交易调度员",
        "status": "ready",
        "decision": [],
        "watch": [],
        "reject": [],
        "inputs_available": {k: bool(v) for k, v in pools.items()},
        "rule": "只做最终调度，不重复一号/二号大选股；无防守、无RR、无明确买点不得进入买入池。",
    }
    text = "🎯【三号员工-最终交易调度】\n" + f"时间：{report['generated_at_bj']}\n" + "当前公共 runner 已就绪。等待一号/二号/战法池输入后输出最终买入池、观察池、放弃池。"
    (REPORT_DIR / "final_decision_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "final_decision_report.md").write_text(text, encoding="utf-8")
    print(text)
    send_tg(text)


if __name__ == "__main__":
    main()
