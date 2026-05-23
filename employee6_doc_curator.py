# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

ROOT = Path(__file__).resolve().parent
CHANGE_LOG = ROOT / "AI_ENGINEER_CHANGE_LOG.md"
SUCCESS_LEDGER = ROOT / "AI_ENGINEER_SUCCESS_LEDGER.md"
FINAL_RULES_INDEX = ROOT / "AI_ENGINEER_FINAL_RULES_INDEX.md"
DOCUMENT_MAP = ROOT / "AI_ENGINEER_DOCUMENT_MAP.md"
EVENT_PATH = os.getenv("GITHUB_EVENT_PATH", "")
TOKEN = os.getenv("GITHUB_TOKEN", "") or os.getenv("GH_TOKEN", "")
REPO = os.getenv("GITHUB_REPOSITORY", "driveaway1207/stock-alert-public-runner")
EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "manual")
RUN_ID = os.getenv("GITHUB_RUN_ID", "")
RUN_NUMBER = os.getenv("GITHUB_RUN_NUMBER", "")
SHA = os.getenv("GITHUB_SHA", "")
ACTOR = os.getenv("GITHUB_ACTOR", "")
API = "https://api.github.com"


def now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def read_json(path: str) -> Dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    except Exception:
        return {}


def api_get(path: str) -> Dict[str, Any]:
    if not TOKEN:
        return {}
    url = path if path.startswith("http") else API + path
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code >= 400:
            print(f"employee6 api_get failed {r.status_code}: {url} {r.text[:160]}", flush=True)
            return {}
        return r.json()
    except Exception as exc:
        print(f"employee6 api_get exception: {type(exc).__name__} {url}", flush=True)
        return {}


def sh(cmd: List[str]) -> str:
    try:
        return subprocess.check_output(cmd, cwd=str(ROOT), text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def recent_local_shas(limit: int = 40) -> List[str]:
    out = sh(["git", "log", f"-{limit}", "--pretty=%H"])
    return [x.strip() for x in out.splitlines() if x.strip()]


def list_recent_commit_shas(event: Dict[str, Any]) -> List[str]:
    """Realtime + backfill.

    push 事件会记录本次 push 的 commit；同时每次运行都扫描最近 40 个 commit，
    这样即使某次 GitHub Actions 没触发、排队被取消、或当时 workflow 还没启用 push，
    后续任意一次六号员工运行也会补记未记录过的提交。
    """
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
    shas.extend(recent_local_shas(40))
    return list(dict.fromkeys([s for s in shas if s]))[:60]


def fetch_commit(sha: str) -> Dict[str, Any]:
    data = api_get(f"/repos/{REPO}/commits/{sha}")
    if data:
        return data
    msg = sh(["git", "log", "-1", "--pretty=%B", sha]) or ""
    files = sh(["git", "show", "--name-only", "--pretty=format:", sha]).splitlines()
    return {"sha": sha, "commit": {"message": msg}, "files": [{"filename": f} for f in files if f]}


def classify_path(path: str) -> str:
    p = path.lower()
    if p.startswith(".github/workflows/"):
        return "workflow"
    if re.search(r"employee1|employee_1|一号", p):
        return "employee1"
    if re.search(r"employee2|employee_2|二号", p):
        return "employee2"
    if re.search(r"employee3|employee_3|三号", p):
        return "employee3"
    if re.search(r"employee4|employee_4|四号", p):
        return "employee4"
    if re.search(r"employee5|employee_5|fifth_employee|五号", p):
        return "employee5"
    if re.search(r"employee6|employee_6|六号|success_ledger|change_log|final_rules|document_map|strategy_registry", p):
        return "employee6_docs"
    if p.endswith(".md") or p.startswith("docs/") or "ai_engineer" in p or "runbook" in p:
        return "global_docs"
    if p.endswith(".py"):
        return "code"
    return "other"


def infer_purpose(message: str, files: List[str]) -> str:
    m = message.lower()
    joined = " ".join(files).lower()
    if "baostock" in m or "baostock" in joined or "bostock" in m:
        return "历史K线数据源口径：BaoStock/Bostock优先，AKShare辅助，东方财富最后兜底。"
    if "20-day" in m or "20日" in message or "monthly" in m or "月线" in message:
        return "周期样本选择/周期口径：20日/月线窗口用于深度样本排序或报告表达。"
    if "progress" in m or "eta" in m or "进度" in message:
        return "运行可观测性：进度条、ETA、阶段日志或artifact进度文件。"
    if "trigger" in m or "workflow" in m or "issue" in m or ".github/workflows" in joined:
        return "自动触发/运行链路：workflow、固定Issue标签、并发锁或依赖安装。"
    if "employee" in joined or "员工" in message or "employee" in m:
        return "员工体系更新：可能涉及新员工、员工手册、workflow或报告规范。"
    if "战法" in message or "模型" in message or any(x in m for x in ["strategy", "model"]):
        return "战法/模型规则更新：需要进入战法登记册，未复盘前不得写成最终成功。"
    if "document" in m or "runbook" in m or "ledger" in m or "docs" in m or any(f.endswith(".md") for f in files):
        return "文档和成功经验沉淀：更新工程入口、操作手册、规则索引或经验库。"
    return "代码或文档常规更新。"


def build_commit_summary(commit: Dict[str, Any]) -> Dict[str, Any]:
    sha = str(commit.get("sha", ""))
    msg = ((commit.get("commit") or {}).get("message") or "").strip()
    files = [f.get("filename", "") for f in commit.get("files", []) if f.get("filename")]
    classes: Dict[str, int] = {}
    for f in files:
        c = classify_path(f)
        classes[c] = classes.get(c, 0) + 1
    return {"sha": sha, "short_sha": sha[:12], "message": msg, "files": files, "classes": classes, "purpose": infer_purpose(msg, files)}


def workflow_context(event: Dict[str, Any]) -> Dict[str, Any]:
    if EVENT_NAME != "workflow_run":
        return {}
    wr = event.get("workflow_run", {}) or {}
    return {
        "workflow_name": wr.get("name", ""),
        "run_number": wr.get("run_number", ""),
        "conclusion": wr.get("conclusion", ""),
        "status": wr.get("status", ""),
        "html_url": wr.get("html_url", ""),
        "head_sha": wr.get("head_sha", ""),
    }


def marker(prefix: str, key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.:-]", "_", key)[:160]
    return f"<!-- employee6-{prefix}:{safe} -->"


def ensure_file(path: Path, header: str) -> None:
    if not path.exists():
        path.write_text(header.rstrip() + "\n", encoding="utf-8")


def insert_after_title(path: Path, header: str, entry: str, unique_marker: str) -> bool:
    ensure_file(path, header)
    text = path.read_text(encoding="utf-8")
    if unique_marker in text:
        return False
    lines = text.splitlines()
    if lines and lines[0].startswith("#"):
        new_lines = [lines[0], "", unique_marker, entry.rstrip(), ""] + lines[1:]
        path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    else:
        path.write_text(unique_marker + "\n" + entry.rstrip() + "\n\n" + text, encoding="utf-8")
    return True


def md_list(items: List[str]) -> str:
    return "\n".join(f"- `{x}`" for x in items) if items else "- 无"


def build_change_entry(summaries: List[Dict[str, Any]], wf: Dict[str, Any]) -> Tuple[str, str]:
    key = f"{EVENT_NAME}-{RUN_ID or 'local'}-" + "-".join(s.get("short_sha", "") for s in summaries)[:80]
    unique = marker("change", key)
    lines = [f"## {now_text()}｜事件：{EVENT_NAME}｜运行：{RUN_NUMBER or RUN_ID or 'local'}", "", f"- 触发人：`{ACTOR or 'unknown'}`", f"- 仓库：`{REPO}`"]
    if wf:
        lines += [f"- 关联 workflow：`{wf.get('workflow_name')}` #{wf.get('run_number')}，status=`{wf.get('status')}`，conclusion=`{wf.get('conclusion')}`", f"- workflow 链接：{wf.get('html_url') or '无'}"]
    for s in summaries:
        lines += ["", f"### Commit `{s['short_sha']}`", "", f"- message：{s['message'].splitlines()[0] if s['message'] else '无'}", f"- 自动归类：{json.dumps(s['classes'], ensure_ascii=False)}", f"- 可能目的：{s['purpose']}", "- 修改路径：", md_list(s["files"])]
    return unique, "\n".join(lines)


def build_success_entries(summaries: List[Dict[str, Any]], wf: Dict[str, Any]) -> List[Tuple[str, str]]:
    entries: List[Tuple[str, str]] = []
    for s in summaries:
        files, classes, msg = s["files"], s["classes"], s["message"]
        if not files:
            continue
        if not ("workflow" in classes or "employee5" in classes or "employee6_docs" in classes or "global_docs" in classes or any(f.endswith(".md") for f in files)):
            continue
        unique = marker("ledger", s["sha"][:12])
        entry = "\n".join([
            f"## {now_text()}｜自动沉淀：`{s['short_sha']}`", "",
            "- 状态：已落地代码/文档经验，后续若用户否定，六号员工必须更正或移出成功经验库。",
            f"- commit message：{msg.splitlines()[0] if msg else '无'}",
            f"- 经验归纳：{s['purpose']}",
            f"- 影响范围：{json.dumps(classes, ensure_ascii=False)}",
            "- 对应路径：", md_list(files[:30])])
        entries.append((unique, entry))
    if wf:
        unique = marker("ledger", f"workflow-{wf.get('workflow_name')}-{wf.get('run_number')}-{wf.get('conclusion')}")
        entry = "\n".join([f"## {now_text()}｜workflow运行结果：`{wf.get('workflow_name')}` #{wf.get('run_number')}", "", f"- 状态：status=`{wf.get('status')}`，conclusion=`{wf.get('conclusion')}`", f"- 链接：{wf.get('html_url') or '无'}", "- 经验归纳：六号员工必须记录关键 workflow 结果；如果运行失败，进入变更流水账，不能伪装成成功。"])
        entries.append((unique, entry))
    return entries


def existing_employee_docs(pattern: str) -> List[str]:
    return sorted(p.name for p in ROOT.glob(pattern))


def write_final_rules_index() -> bool:
    runbooks = existing_employee_docs("EMPLOYEE*_OPERATION_RUNBOOK.md")
    header = "# AI 工程师最终规则索引\n\n这里维护已经定下来的全局规则入口。六号员工会实时监听 push，并用最近 commit 回扫做防漏补记。\n"
    body = f"""
## 全局入口

- `README.md`：仓库第一入口。
- `AI_ENGINEER_START_HERE.md`：AI 工程师总入口。
- `AI_ENGINEER_KLINE_PERIOD_RULES.md`：所有员工通用周期口径。
- `AI_ENGINEER_DOCUMENT_MAP.md`：文档分类地图。
- `AI_ENGINEER_CHANGE_LOG.md`：所有代码/文档改动流水账。
- `AI_ENGINEER_SUCCESS_LEDGER.md`：已落地成功经验/候选经验沉淀。
- `AI_ENGINEER_STRATEGY_REGISTRY.md`：战法/模型规则登记册。

## 已定下来的关键规则

- 5日≈周线窗口，20日≈月线窗口，60日≈季线窗口，250日≈年线窗口。
- 五号员工是涨停样本研究员，不是买入推荐系统。
- 五号员工深度样本按全涨停池中可取得K线股票的 20日/月线窗口涨幅前三名选取。
- 五号员工历史K线：BaoStock/Bostock 优先，AKShare 辅助，东方财富最后兜底。
- 北交所必须单独保留 AKShare / 东方财富兜底，不能因为 BaoStock 覆盖不稳定而漏掉。
- 五号员工自动触发：固定 Issue #2 + `run-employee5` 标签；同一轮修改最后只触发一次。
- 六号员工必须实时记录所有员工代码修改：监听 main 分支所有 push，不按员工号过滤。
- 六号员工必须防漏：每次运行都回扫最近 40 个 commit，已记录的 marker 不重复写，漏掉的自动补上。
- 六号员工自己的 `[employee6-skip]` 自动提交不得再次触发，避免无限循环。
- 新员工出现时，六号员工自动生成 `EMPLOYEE{{N}}_OPERATION_RUNBOOK.md` 与 `EMPLOYEE{{N}}_REPORT_SPEC.md` 占位文档。
- 新战法出现时，六号员工自动登记到 `AI_ENGINEER_STRATEGY_REGISTRY.md`，未验证前不得写成最终成功。

## 员工手册入口

{md_list(runbooks)}

## 六号员工维护原则

- 所有代码修改都进入 `AI_ENGINEER_CHANGE_LOG.md`。
- 用户认可、未被拒绝、已落地的正确经验进入 `AI_ENGINEER_SUCCESS_LEDGER.md`。
- AI 工程师自己确认的稳定工程经验，也要主动写入文档。
- 错误尝试不能包装成成功经验。
""".strip()
    old = FINAL_RULES_INDEX.read_text(encoding="utf-8") if FINAL_RULES_INDEX.exists() else ""
    new = header + "\n" + body + "\n"
    if old.strip() != new.strip():
        FINAL_RULES_INDEX.write_text(new, encoding="utf-8")
        return True
    return False


def main() -> None:
    event = read_json(EVENT_PATH)
    shas = list_recent_commit_shas(event)
    commits = [fetch_commit(s) for s in shas]
    summaries = [build_commit_summary(c) for c in commits if c]
    wf = workflow_context(event)
    changed = False
    change_header = "# AI 工程师代码变更流水账\n\n本文件由六号员工自动维护。它记录所有进入 GitHub 的代码/文档/workflow 改动路径、用途归类和运行结果。\n"
    success_header = "# AI 工程师成功经验沉淀账\n\n本文件由六号员工自动维护。只沉淀已经落地、可复用、未被用户否定的正确经验；如果后续被用户否定，必须更正。\n"
    if summaries or wf:
        unique, entry = build_change_entry(summaries, wf)
        changed = insert_after_title(CHANGE_LOG, change_header, entry, unique) or changed
        for u, e in build_success_entries(summaries, wf):
            changed = insert_after_title(SUCCESS_LEDGER, success_header, e, u) or changed
    changed = write_final_rules_index() or changed
    print("employee6 updated documentation ledgers" if changed else "employee6 no documentation changes needed", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("employee6 failed:", type(exc).__name__, str(exc), flush=True)
        sys.exit(1)
