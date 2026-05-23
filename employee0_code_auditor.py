# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "employee0_reports"
EVENT_PATH = os.getenv("GITHUB_EVENT_PATH", "")
EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "manual")
SHA = os.getenv("GITHUB_SHA", "")
ACTOR = os.getenv("GITHUB_ACTOR", "")
REPO = os.getenv("GITHUB_REPOSITORY", "driveaway1207/stock-alert-public-runner")
RUN_ID = os.getenv("GITHUB_RUN_ID", "")
RUN_NUMBER = os.getenv("GITHUB_RUN_NUMBER", "")

PROTECTED_PATHS = [
    "stock_alert.py",
    ".github/workflows/",
    "kline_cache",
    "employee5_runner.py",
    "employee6_doc_curator.py",
    "employee6_employee_archiver.py",
]
PROTECTED_KEYWORDS = [
    "PAT", "GH_PAT", "GITHUB_TOKEN", "secrets", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "DATA_GATE_TARGET_DATE", "LAST_TRADE_DAY", "BaoStock", "baostock", "cache", "artifact",
]
MONKEY_HINTS = [
    "monkey", "patch", "wrapper", "quick fix", "临时", "兜一下", "先这样", "TODO", "FIXME",
    "hardcode", "硬编码", "eval(", "exec(", "os.system(", "subprocess.call(",
]
EMPLOYEE_HINTS = {
    0: ["employee0", "zero", "零号"],
    1: ["employee1", "stock_alert.py", "一号"],
    2: ["employee2", "二号"],
    3: ["employee3", "三号"],
    4: ["employee4", "四号"],
    5: ["employee5", "fifth_employee", "五号"],
    6: ["employee6", "六号", "AI_ENGINEER", "SUCCESS_LEDGER", "CHANGE_LOG"],
}


def now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def sh(cmd: List[str]) -> str:
    try:
        return subprocess.check_output(cmd, cwd=str(ROOT), text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def read_event() -> Dict:
    try:
        return json.loads(Path(EVENT_PATH).read_text(encoding="utf-8")) if EVENT_PATH else {}
    except Exception:
        return {}


def recent_shas(limit: int = 20) -> List[str]:
    event = read_event()
    shas: List[str] = []
    if EVENT_NAME == "push":
        for c in event.get("commits", []) or []:
            s = c.get("id") or c.get("sha")
            if s:
                shas.append(str(s))
        if SHA:
            shas.append(SHA)
    elif SHA:
        shas.append(SHA)
    out = sh(["git", "log", f"-{limit}", "--pretty=%H"])
    shas.extend(x.strip() for x in out.splitlines() if x.strip())
    return list(dict.fromkeys(x for x in shas if x))[:30]


def commit_message(sha: str) -> str:
    return sh(["git", "log", "-1", "--pretty=%B", sha])


def commit_files(sha: str) -> List[str]:
    return [x for x in sh(["git", "show", "--name-only", "--pretty=format:", sha]).splitlines() if x.strip()]


def commit_patch(sha: str) -> str:
    return sh(["git", "show", "--format=", "--unified=0", sha])[:50000]


def detect_employees(text: str, files: List[str]) -> List[int]:
    source = (text + "\n" + "\n".join(files)).lower()
    found: Set[int] = set()
    for n, hints in EMPLOYEE_HINTS.items():
        if any(h.lower() in source for h in hints):
            found.add(n)
    return sorted(found)


def audit_commit(sha: str) -> Dict:
    msg = commit_message(sha)
    files = commit_files(sha)
    patch = commit_patch(sha)
    risk_flags: List[str] = []
    for f in files:
        if any(f == p or f.startswith(p) for p in PROTECTED_PATHS):
            risk_flags.append(f"触碰受保护路径：{f}")
    for kw in PROTECTED_KEYWORDS:
        if kw.lower() in patch.lower() or kw.lower() in msg.lower():
            risk_flags.append(f"触碰敏感关键词：{kw}")
    for kw in MONKEY_HINTS:
        if kw.lower() in patch.lower() or kw.lower() in msg.lower():
            risk_flags.append(f"疑似猴子代码/临时补丁信号：{kw}")
    risk_flags = list(dict.fromkeys(risk_flags))[:30]
    risk_level = "LOW"
    if any("受保护路径" in x for x in risk_flags) or any("敏感关键词" in x for x in risk_flags):
        risk_level = "MEDIUM"
    if any(x in patch for x in ["PAT=", "GH_PAT=", "TELEGRAM_BOT_TOKEN="]) or "eval(" in patch or "exec(" in patch:
        risk_level = "HIGH"
    return {
        "sha": sha,
        "short_sha": sha[:12],
        "message": msg.splitlines()[0] if msg else "",
        "files": files,
        "employees": detect_employees(msg, files),
        "risk_level": risk_level,
        "risk_flags": risk_flags,
    }


def build_report(items: List[Dict]) -> str:
    lines = [
        "# 零号员工代码审计报告",
        "",
        f"- 时间：{now_text()}",
        f"- 事件：`{EVENT_NAME}`",
        f"- 仓库：`{REPO}`",
        f"- 触发人：`{ACTOR or 'unknown'}`",
        f"- 运行：`{RUN_NUMBER or RUN_ID or 'local'}`",
        f"- 审计 commit 数：{len(items)}",
        "",
        "## 审计结果",
    ]
    for it in items:
        lines += [
            "",
            f"### `{it['short_sha']}`｜风险：{it['risk_level']}",
            "",
            f"- message：{it['message'] or '无'}",
            f"- 涉及员工：{it['employees'] or '未识别'}",
            "- 修改路径：",
        ]
        lines.extend(f"  - `{f}`" for f in it["files"][:40])
        if it["risk_flags"]:
            lines.append("- 风险/审计提示：")
            lines.extend(f"  - {x}" for x in it["risk_flags"])
        else:
            lines.append("- 风险/审计提示：未发现受保护路径、敏感关键词或明显猴子补丁信号。")
    return "\n".join(lines) + "\n"


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    items = [audit_commit(s) for s in recent_shas(20)]
    report = build_report(items)
    data = {
        "generated_at": now_text(),
        "event_name": EVENT_NAME,
        "repo": REPO,
        "actor": ACTOR,
        "run_id": RUN_ID,
        "run_number": RUN_NUMBER,
        "items": items,
    }
    (REPORT_DIR / "employee0_audit_report.md").write_text(report, encoding="utf-8")
    (REPORT_DIR / "employee0_audit_report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report, flush=True)


if __name__ == "__main__":
    main()
