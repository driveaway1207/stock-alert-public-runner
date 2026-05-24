# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import requests

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "zero_employee_reports"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CRITICAL_FILES = [
    "README.md",
    "AI_ENGINEER_START_HERE.md",
    "AI_ENGINEER_FINAL_RULES_INDEX.md",
    "AI_ENGINEER_DOCUMENT_MAP.md",
    "EMPLOYEE_SYSTEM_ROLES.md",
    "EMPLOYEE0_OPERATION_RUNBOOK.md",
    "EMPLOYEE5_OPERATION_RUNBOOK.md",
]

IDENTITY_LOCKS: Dict[str, Dict[str, str]] = {
    "零号员工": {
        "role": "系统总审计官 / 验证官",
        "script": "zero_employee_auditor.py",
        "workflow": ".github/workflows/zero_employee.yml",
        "report_dir": "zero_employee_reports/",
    },
    "一号员工": {
        "role": "民间战法 Alpha 引擎",
    },
    "二号员工": {
        "role": "华尔街体系 Alpha 引擎",
    },
    "三号员工": {
        "role": "最终交易调度员",
        "script": "employee3_final_decision.py",
        "workflow": ".github/workflows/third_employee.yml",
        "report_dir": "employee3_reports/",
    },
    "四号员工": {
        "role": "跌停/极弱样本归因研究员",
        "script": "employee4_risk_researcher.py",
        "workflow": ".github/workflows/fourth_employee.yml",
        "report_dir": "employee4_reports/",
    },
    "五号员工": {
        "role": "涨停/极强样本归因研究员",
        "script": "employee5_runner.py",
        "workflow": ".github/workflows/fifth_employee.yml",
        "report_dir": "employee5_reports/",
    },
}

WORKFLOW_NAME_LOCKS = {
    ".github/workflows/zero_employee.yml": "name: 零号员工",
    ".github/workflows/third_employee.yml": "name: 三号员工",
    ".github/workflows/fourth_employee.yml": "name: 四号员工",
    ".github/workflows/fifth_employee.yml": "name: 五号员工",
}

BANNED_IDENTITY_DRIFT_PATTERNS = [
    "四号员工：模型审计官",
    "四号员工-模型审计官",
    "四号员工模型审计官",
    "employee4_model_auditor.py",
    "EMPLOYEE4_MODEL_AUDITOR_SPEC.md",
]


def bj_now() -> str:
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def read_text(rel_path: str) -> str:
    path = ROOT / rel_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def send_tg(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram missing; skip")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text[:3900], "disable_web_page_preview": True}, timeout=30)


def add_issue(bucket: List[dict], issue: str, files: List[str], detail: str) -> None:
    bucket.append({"issue": issue, "files": files, "detail": detail})


def audit_identity_locks() -> List[dict]:
    issues: List[dict] = []
    roles_text = read_text("EMPLOYEE_SYSTEM_ROLES.md")

    for name, lock in IDENTITY_LOCKS.items():
        if name not in roles_text:
            add_issue(
                issues,
                "员工中文名字缺失或被改名",
                ["EMPLOYEE_SYSTEM_ROLES.md"],
                f"员工总纲中没有找到固定中文名：{name}",
            )
        role = lock.get("role")
        if role and role not in roles_text:
            add_issue(
                issues,
                "员工身份定位缺失或漂移",
                ["EMPLOYEE_SYSTEM_ROLES.md"],
                f"{name} 的固定身份定位未在总纲中找到：{role}",
            )
        script = lock.get("script")
        if script and script not in roles_text:
            add_issue(
                issues,
                "员工主脚本记录缺失或漂移",
                ["EMPLOYEE_SYSTEM_ROLES.md"],
                f"{name} 的固定主脚本未在总纲中找到：{script}",
            )
        workflow = lock.get("workflow")
        if workflow and workflow not in roles_text:
            add_issue(
                issues,
                "员工 workflow 记录缺失或漂移",
                ["EMPLOYEE_SYSTEM_ROLES.md"],
                f"{name} 的固定 workflow 未在总纲中找到：{workflow}",
            )
        report_dir = lock.get("report_dir")
        if report_dir and report_dir not in roles_text:
            add_issue(
                issues,
                "员工报告目录记录缺失或漂移",
                ["EMPLOYEE_SYSTEM_ROLES.md"],
                f"{name} 的固定报告目录未在总纲中找到：{report_dir}",
            )

    for workflow, expected_line in WORKFLOW_NAME_LOCKS.items():
        text = read_text(workflow)
        if not text:
            add_issue(
                issues,
                "员工 workflow 文件缺失",
                [workflow],
                f"没有找到固定 workflow：{workflow}",
            )
            continue
        if expected_line not in text:
            add_issue(
                issues,
                "员工 workflow 显示名被改或缺失",
                [workflow],
                f"应包含固定显示名 `{expected_line}`，但当前文件未找到。",
            )

    files_to_scan = []
    for pattern in ("*.md", "*.py", ".github/workflows/*.yml"):
        files_to_scan.extend(ROOT.glob(pattern))
    for path in sorted(set(files_to_scan)):
        if not path.is_file():
            continue
        rel = str(path.relative_to(ROOT))
        text = path.read_text(encoding="utf-8", errors="ignore")
        for banned in BANNED_IDENTITY_DRIFT_PATTERNS:
            if banned in text:
                add_issue(
                    issues,
                    "发现旧身份或未授权身份漂移痕迹",
                    [rel],
                    f"发现禁止回流的身份漂移关键词：{banned}",
                )

    return issues


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    missing = [p for p in CRITICAL_FILES if not (ROOT / p).exists()]
    identity_issues = audit_identity_locks()

    report = {
        "generated_at_bj": bj_now(),
        "role": "零号员工-系统总审计官",
        "missing_critical_files": missing,
        "identity_lock_issues": identity_issues,
        "p0": [],
        "p1": [],
        "p2": [],
        "constraints": [
            "禁止猴子代码、伪代码、包装代码、概念包装、画大饼代码。",
            "未验证规律不能自动写入生产模型。",
            "员工报告只读，自动改代码必须经过用户确认。",
            "未经用户明确要求，任何员工名字、编号、身份、职责、workflow 显示名、报告标题和主脚本身份都不能被改。",
        ],
    }
    if missing:
        report["p0"].append({"issue": "关键说明文件缺失", "files": missing})
    if identity_issues:
        report["p0"].append({"issue": "员工身份或命名锁定审计失败", "items": identity_issues})

    text_lines = [
        "🧭【零号员工-系统审计】",
        f"时间：{report['generated_at_bj']}",
        f"P0数量：{len(report['p0'])}",
    ]
    if missing:
        text_lines.append("关键文件缺失：" + "、".join(missing))
    else:
        text_lines.append("关键文件检查通过。")
    if identity_issues:
        text_lines.append(f"员工身份锁定审计失败：{len(identity_issues)} 项。")
        for item in identity_issues[:8]:
            text_lines.append(f"- {item['issue']}：{item['detail']}")
    else:
        text_lines.append("员工身份锁定审计通过。")

    text = "\n".join(text_lines)
    (REPORT_DIR / "model_audit_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "model_audit_report.md").write_text(text, encoding="utf-8")
    print(text)
    send_tg(text)


if __name__ == "__main__":
    main()
