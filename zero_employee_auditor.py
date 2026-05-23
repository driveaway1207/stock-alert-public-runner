# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "zero_employee_reports"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CRITICAL_FILES = [
    "EMPLOYEE_SYSTEM_ROLES.md",
    "MODELING_CORE_PRINCIPLES.md",
    "EMPLOYEE5_LIMIT_UP_RESEARCHER_SPEC.md",
    "EMPLOYEE4_LIMIT_DOWN_RESEARCHER_SPEC.md",
    "ZERO_EMPLOYEE_MODEL_AUDITOR_SPEC.md",
]


def bj_now() -> str:
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def send_tg(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram missing; skip")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text[:3900], "disable_web_page_preview": True}, timeout=30)


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    missing = [p for p in CRITICAL_FILES if not (ROOT / p).exists()]
    report = {
        "generated_at_bj": bj_now(),
        "role": "零号员工-系统总审计官",
        "missing_critical_files": missing,
        "p0": [],
        "p1": [],
        "p2": [],
        "constraints": [
            "禁止猴子代码、伪代码、包装代码、概念包装、画大饼代码。",
            "未验证规律不能自动写入生产模型。",
            "员工报告只读，自动改代码必须经过用户确认。",
        ],
    }
    if missing:
        report["p0"].append({"issue": "关键说明文件缺失", "files": missing})
    text = "🧭【零号员工-系统审计】\n" + f"时间：{report['generated_at_bj']}\n" + f"P0数量：{len(report['p0'])}\n" + ("关键文件缺失：" + "、".join(missing) if missing else "关键文件检查通过。")
    (REPORT_DIR / "model_audit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "model_audit_report.md").write_text(text, encoding="utf-8")
    print(text)
    send_tg(text)


if __name__ == "__main__":
    main()
