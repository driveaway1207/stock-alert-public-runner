# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent
EVENT_PATH = os.getenv("GITHUB_EVENT_PATH", "")
EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "manual")
RUN_ID = os.getenv("GITHUB_RUN_ID", "")
RUN_NUMBER = os.getenv("GITHUB_RUN_NUMBER", "")
ACTOR = os.getenv("GITHUB_ACTOR", "")
REPO = os.getenv("GITHUB_REPOSITORY", "driveaway1207/stock-alert-public-runner")
SHA = os.getenv("GITHUB_SHA", "")
RUNBOOK = ROOT / "EMPLOYEE6_OPERATION_RUNBOOK.md"
AUTO_COMMIT_PREFIX = "[employee6-skip]"

SCATTER_PATTERNS = [
    "EMPLOYEE*_CHANGE_LOG.md",
    "EMPLOYEE*_REPORT_SPEC.md",
    "EMPLOYEE*_DIMENSION_SPEC.md",
    "EMPLOYEE*_STRUCTURE_*SPEC.md",
]

GLOBAL_LEDGER_FILES = [
    "AI_ENGINEER_CHANGE_LOG.md",
    "AI_ENGINEER_SUCCESS_LEDGER.md",
    "AI_ENGINEER_DOCUMENT_MAP.md",
    "AI_ENGINEER_FINAL_RULES_INDEX.md",
    "AI_ENGINEER_STRATEGY_REGISTRY.md",
]


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


def list_matches(pattern: str) -> list[str]:
    return sorted(p.name for p in ROOT.glob(pattern) if p.is_file())


def current_sha_from_event(event: Dict) -> str:
    if EVENT_NAME == "workflow_run":
        wr = event.get("workflow_run", {}) or {}
        return str(wr.get("head_sha") or "")
    if EVENT_NAME == "push":
        return str(event.get("after") or SHA or "")
    return SHA or sh(["git", "rev-parse", "HEAD"])


def commit_message(sha: str) -> str:
    return sh(["git", "log", "-1", "--pretty=%B", sha]) if sha else ""


def commit_files(sha: str) -> list[str]:
    if not sha:
        return []
    return [x for x in sh(["git", "show", "--name-only", "--pretty=format:", sha]).splitlines() if x.strip()]


def is_employee6_skip(msg: str) -> bool:
    return msg.strip().startswith(AUTO_COMMIT_PREFIX)


def classify(files: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in files:
        p = f.lower()
        if p.startswith(".github/workflows/"):
            k = "workflow"
        elif p.endswith(".py"):
            k = "code"
        elif p.endswith(".md"):
            k = "docs"
        elif p.endswith(".json"):
            k = "json"
        else:
            k = "other"
        out[k] = out.get(k, 0) + 1
    return out


def ensure_section(text: str) -> str:
    if "## 自动跟踪记录" in text:
        return text
    return text.rstrip() + "\n\n---\n\n## 自动跟踪记录\n\n本区由六号员工自动追加，只记录关键事实：触发事件、commit、修改路径、是否发现散文档。六号不得在本区判断成功经验。\n"


def marker(sha: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.:-]", "_", sha[:12] or RUN_ID or RUN_NUMBER or "manual")
    return f"<!-- employee6-track:{safe} -->"


def build_entry(sha: str, msg: str, files: list[str], scattered: list[str], global_ledgers: list[str]) -> str:
    title_msg = msg.splitlines()[0] if msg else "无"
    changed_paths = "\n".join(f"  - `{x}`" for x in files[:40]) if files else "  - 无"
    scattered_text = "\n".join(f"  - `{x}`" for x in scattered) if scattered else "  - 无"
    global_text = "\n".join(f"  - `{x}`" for x in global_ledgers) if global_ledgers else "  - 无"
    return "\n".join([
        "",
        marker(sha),
        f"### {now_text()}｜{EVENT_NAME}｜`{sha[:12] if sha else 'unknown'}`",
        "",
        f"- 运行：`{RUN_NUMBER or RUN_ID or 'local'}`｜触发人：`{ACTOR or 'unknown'}`｜仓库：`{REPO}`",
        f"- commit message：{title_msg}",
        f"- 文件归类：{json.dumps(classify(files), ensure_ascii=False)}",
        "- 修改路径：",
        changed_paths,
        "- 当前散文档检查：",
        scattered_text,
        "- 当前旧总账检查：",
        global_text,
        "- 六号处理：只记录事实；不生成散文档；不判断成功经验。",
        "",
    ])


def main() -> None:
    event = read_event()
    sha = current_sha_from_event(event)
    msg = commit_message(sha)
    if is_employee6_skip(msg):
        print("employee6 skip self commit")
        return

    files = commit_files(sha)
    scattered: list[str] = []
    for pattern in SCATTER_PATTERNS:
        scattered.extend(list_matches(pattern))
    scattered = sorted(set(scattered))
    global_ledgers = sorted(name for name in GLOBAL_LEDGER_FILES if (ROOT / name).exists())

    text = RUNBOOK.read_text(encoding="utf-8") if RUNBOOK.exists() else "# 六号员工运行总手册\n"
    text = ensure_section(text)
    mk = marker(sha)
    if mk in text:
        print("employee6 tracking already recorded")
        return
    text = text.rstrip() + "\n" + build_entry(sha, msg, files, scattered, global_ledgers)
    RUNBOOK.write_text(text.rstrip() + "\n", encoding="utf-8")
    print("employee6 appended single-file tracking record")


if __name__ == "__main__":
    main()
