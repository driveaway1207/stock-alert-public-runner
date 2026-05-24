# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "employee0_reports"
EVENT_PATH = os.getenv("GITHUB_EVENT_PATH", "")
EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "manual")
SHA = os.getenv("GITHUB_SHA", "")
RUN_ID = os.getenv("GITHUB_RUN_ID", "")
RUN_NUMBER = os.getenv("GITHUB_RUN_NUMBER", "")
ACTOR = os.getenv("GITHUB_ACTOR", "")
REPO = os.getenv("GITHUB_REPOSITORY", "driveaway1207/stock-alert-public-runner")
SCOPE = os.getenv("EMPLOYEE0_GATE_SCOPE", "event").strip().lower()
LIMIT = int(os.getenv("EMPLOYEE0_GATE_LIMIT", "5") or "5")

LOCKED_PATHS = [
    "stock_alert.py",
    ".github/workflows/",
    "kline_cache",
    "employee5_runner.py",
    "employee6_doc_curator.py",
    "employee6_employee_archiver.py",
]
BAD_CRON = re.compile(r"cron:\s*['\"]\*/(1|2|3|4|5|10|15|30)\s+\*\s+\*\s+\*\s+\*['\"]", re.I)
BAD_CALL_PARTS = [("ev", "al("), ("ex", "ec("), ("os.", "system(")]
MONKEY_WORDS = ["wrapper", "quick fix", "临时", "兜一下", "先这样", "TODO", "FIXME", "hardcode", "硬编码"]


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


def shas_to_check() -> List[str]:
    event = read_event()
    shas: List[str] = []
    if SCOPE == "event" and EVENT_NAME == "push":
        for c in event.get("commits", []) or []:
            s = c.get("id") or c.get("sha")
            if s:
                shas.append(str(s))
    if SCOPE == "head" and SHA:
        shas.append(SHA)
    if not shas:
        out = sh(["git", "log", f"-{LIMIT}", "--pretty=%H"])
        shas.extend(x.strip() for x in out.splitlines() if x.strip())
    return list(dict.fromkeys(x for x in shas if x))[:LIMIT]


def commit_message(sha: str) -> str:
    return sh(["git", "log", "-1", "--pretty=%B", sha])


def commit_files(sha: str) -> List[str]:
    return [x for x in sh(["git", "show", "--name-only", "--pretty=format:", sha]).splitlines() if x.strip()]


def added_text(sha: str) -> str:
    patch = sh(["git", "show", "--format=", "--unified=0", sha])[:80000]
    lines: List[str] = []
    for line in patch.splitlines():
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
    return "\n".join(lines)


def audit_one(sha: str) -> Dict:
    files = commit_files(sha)
    msg = commit_message(sha)
    added = added_text(sha)
    flags: List[str] = []
    block = False
    for f in files:
        if any(f == p or f.startswith(p) for p in LOCKED_PATHS):
            flags.append(f"P1触碰受保护路径：{f}")
    if BAD_CRON.search(added):
        block = True
        flags.append("P0阻断：新增高频 schedule，可能抢占正式员工 runner。")
    low_added = added.lower()
    for left, right in BAD_CALL_PARTS:
        if (left + right) in low_added:
            block = True
            flags.append("P0阻断：新增危险动态执行或系统调用。")
    for w in MONKEY_WORDS:
        if w.lower() in low_added or w.lower() in msg.lower():
            flags.append(f"P2提示：疑似猴子代码信号 {w}")
    return {"sha": sha, "short_sha": sha[:12], "message": msg.splitlines()[0] if msg else "", "files": files, "flags": flags, "block": block}


def build_report(items: List[Dict]) -> str:
    blocking = any(x["block"] for x in items)
    lines = [
        "# 零号员工门禁报告",
        "",
        f"- 时间：{now_text()}",
        f"- 事件：`{EVENT_NAME}`",
        f"- 仓库：`{REPO}`",
        f"- 触发人：`{ACTOR or 'unknown'}`",
        f"- 运行：`{RUN_NUMBER or RUN_ID or 'local'}`",
        f"- 范围：`{SCOPE}`，数量：{len(items)}",
        f"- 门禁结论：{'阻断' if blocking else '通过'}",
        "",
    ]
    for it in items:
        lines += [f"## `{it['short_sha']}`", "", f"- message：{it['message'] or '无'}", "- 修改路径："]
        lines.extend(f"  - `{f}`" for f in it["files"][:50])
        if it["flags"]:
            lines.append("- 审计提示：")
            lines.extend(f"  - {x}" for x in it["flags"])
        else:
            lines.append("- 审计提示：未发现阻断项。")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    items = [audit_one(s) for s in shas_to_check()]
    report = build_report(items)
    blocking = any(x["block"] for x in items)
    (REPORT_DIR / "employee0_gate_report.md").write_text(report, encoding="utf-8")
    (REPORT_DIR / "employee0_gate_report.json").write_text(json.dumps({"blocking": blocking, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report, flush=True)
    if blocking:
        sys.exit(2)


if __name__ == "__main__":
    main()
