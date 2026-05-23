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
EVENT_PATH = os.getenv("GITHUB_EVENT_PATH", "")
EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "manual")
SHA = os.getenv("GITHUB_SHA", "")
RUN_ID = os.getenv("GITHUB_RUN_ID", "")
RUN_NUMBER = os.getenv("GITHUB_RUN_NUMBER", "")
ACTOR = os.getenv("GITHUB_ACTOR", "")
REPO = os.getenv("GITHUB_REPOSITORY", "driveaway1207/stock-alert-public-runner")

CN_NUM = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
CN_TO_NUM = {v: k for k, v in CN_NUM.items()}

WORKFLOW_EMPLOYEE_HINTS = {
    "stock_alert": 1,
    "one_employee": 1,
    "employee1": 1,
    "first_employee": 1,
    "second_employee": 2,
    "employee2": 2,
    "third_employee": 3,
    "employee3": 3,
    "fifth_employee": 5,
    "employee5": 5,
    "employee6": 6,
    "doc_curator": 6,
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


def recent_shas(limit: int = 40) -> List[str]:
    event = read_event()
    shas: List[str] = []
    if EVENT_NAME == "push":
        for c in event.get("commits", []) or []:
            s = c.get("id") or c.get("sha")
            if s:
                shas.append(str(s))
        if SHA:
            shas.append(SHA)
    elif EVENT_NAME == "workflow_run":
        wr = event.get("workflow_run", {}) or {}
        if wr.get("head_sha"):
            shas.append(str(wr["head_sha"]))
    elif SHA:
        shas.append(SHA)
    out = sh(["git", "log", f"-{limit}", "--pretty=%H"])
    shas.extend(x.strip() for x in out.splitlines() if x.strip())
    return list(dict.fromkeys(x for x in shas if x))[:60]


def commit_message(sha: str) -> str:
    return sh(["git", "log", "-1", "--pretty=%B", sha])


def commit_files(sha: str) -> List[str]:
    return [x for x in sh(["git", "show", "--name-only", "--pretty=format:", sha]).splitlines() if x.strip()]


def detect_employees_from_text(text: str) -> Set[int]:
    ids: Set[int] = set()
    lower = text.lower()
    for m in re.finditer(r"employee[_-]?(\d+)", lower):
        try:
            ids.add(int(m.group(1)))
        except Exception:
            pass
    for n, cn in CN_NUM.items():
        if f"{cn}号员工" in text:
            ids.add(n)
    return {x for x in ids if 1 <= x <= 30}


def detect_employees_from_files(files: List[str]) -> Set[int]:
    ids: Set[int] = set()
    for f in files:
        p = f.lower()
        ids |= detect_employees_from_text(f)
        if p.startswith(".github/workflows/"):
            for key, n in WORKFLOW_EMPLOYEE_HINTS.items():
                if key in p:
                    ids.add(n)
        if p == "stock_alert.py" or p.endswith("/stock_alert.py"):
            ids.add(1)
        if p.startswith("employee5_reports/"):
            ids.add(5)
        if p in {"ai_engineer_change_log.md", "ai_engineer_success_ledger.md", "ai_engineer_final_rules_index.md", "ai_engineer_document_map.md", "ai_engineer_strategy_registry.md"}:
            ids.add(6)
    return {x for x in ids if 1 <= x <= 30}


def classify_files(files: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for f in files:
        p = f.lower()
        if p.startswith(".github/workflows/"):
            k = "workflow"
        elif p.endswith(".md"):
            k = "docs"
        elif p.endswith(".py"):
            k = "code"
        elif p.endswith(".json"):
            k = "json"
        else:
            k = "other"
        out[k] = out.get(k, 0) + 1
    return out


def purpose(message: str, files: List[str]) -> str:
    txt = (message + "\n" + "\n".join(files)).lower()
    if "baostock" in txt or "bostock" in txt:
        return "数据源/历史K线口径更新。"
    if "workflow" in txt or ".github/workflows" in txt:
        return "workflow/自动运行链路更新。"
    if "report" in txt or "报告" in txt:
        return "报告输出/格式更新。"
    if "doc" in txt or "runbook" in txt or "文档" in txt:
        return "文档/操作手册/成功经验更新。"
    if "strategy" in txt or "model" in txt or "战法" in txt or "模型" in txt:
        return "战法/模型逻辑更新。"
    return "员工代码或文档常规更新。"


def marker(employee_id: int, sha: str) -> str:
    return f"<!-- employee6-employee-{employee_id}:{sha[:12]} -->"


def employee_file(employee_id: int) -> Path:
    return ROOT / f"EMPLOYEE{employee_id}_CHANGE_LOG.md"


def employee_header(employee_id: int) -> str:
    cn = CN_NUM.get(employee_id, str(employee_id))
    return f"# {cn}号员工变更档案\n\n本文件由六号员工自动维护。凡是识别为 {cn}号员工 相关的代码、文档、workflow、报告规范、运行链路改动，都会实时记录到这里。\n"


def append_entry(employee_id: int, sha: str, msg: str, files: List[str]) -> bool:
    path = employee_file(employee_id)
    header = employee_header(employee_id)
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = header
    mk = marker(employee_id, sha)
    if mk in text:
        return False
    entry = "\n".join([
        "",
        mk,
        f"## {now_text()}｜Commit `{sha[:12]}`",
        "",
        f"- 事件：`{EVENT_NAME}`｜运行：`{RUN_NUMBER or RUN_ID or 'local'}`",
        f"- 触发人：`{ACTOR or 'unknown'}`｜仓库：`{REPO}`",
        f"- commit message：{msg.splitlines()[0] if msg else '无'}",
        f"- 自动归类：{json.dumps(classify_files(files), ensure_ascii=False)}",
        f"- 归档判断：{purpose(msg, files)}",
        "- 修改路径：",
        "\n".join(f"  - `{f}`" for f in files[:40]) if files else "  - 无",
        "",
    ])
    text = text.rstrip() + "\n" + entry
    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    changed = False
    for sha in recent_shas(40):
        msg = commit_message(sha)
        files = commit_files(sha)
        ids = detect_employees_from_text(msg) | detect_employees_from_files(files)
        if not ids:
            continue
        for employee_id in sorted(ids):
            changed = append_entry(employee_id, sha, msg, files) or changed
    print("employee6 per-employee archives updated" if changed else "employee6 per-employee archives no changes")


if __name__ == "__main__":
    main()
