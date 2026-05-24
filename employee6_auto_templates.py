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
CN_NUM = {0:"零",1:"一",2:"二",3:"三",4:"四",5:"五",6:"六",7:"七",8:"八",9:"九",10:"十"}
STRATEGY_KEYWORDS = ["黄金二倍凹口","黄金倍量","核心压力线","核心压力带","BOLL","布林","BBI","二阶画线","Event/Context/Confirmation","VBP","筹码压力带","Liquidity Sweep","假突破","倍量后平量","台阶平台","20日/月线","60日/季线","250日/年线","BaoStock","Bostock","AKShare","东方财富","北交所","涨停样本","战法","模型"]

# 文档合并原则：
# 每个员工默认只保留 EMPLOYEEX_OPERATION_RUNBOOK.md 一个主手册。
# 六号不得再自动创建 EMPLOYEEX_REPORT_SPEC.md、EMPLOYEEX_CHANGE_LOG.md、DIMENSION_SPEC、STRUCTURE_SPEC 等散文档。

def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def read_event() -> Dict:
    try:
        return json.loads(Path(EVENT_PATH).read_text(encoding="utf-8")) if EVENT_PATH else {}
    except Exception:
        return {}

def sh(cmd: List[str]) -> str:
    try:
        return subprocess.check_output(cmd, cwd=str(ROOT), text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""

def collect_text() -> str:
    event = read_event()
    parts: List[str] = []
    for c in event.get("commits", []) or []:
        parts.append(str(c.get("message", "")))
        for k in ["added", "modified", "removed"]:
            parts.extend(str(x) for x in c.get(k, []) or [])
    if not parts:
        parts.append(sh(["git", "log", "-1", "--pretty=%B"]))
        parts.extend(sh(["git", "show", "--name-only", "--pretty=format:", "HEAD"]).splitlines())
    return "\n".join(parts)

def employee_ids(text: str) -> Set[int]:
    ids: Set[int] = set()
    for m in re.finditer(r"employee[_-]?(\d+)", text, flags=re.I):
        try:
            ids.add(int(m.group(1)))
        except Exception:
            pass
    for n, cn in CN_NUM.items():
        if f"{cn}号员工" in text:
            ids.add(n)
    return {x for x in ids if 0 <= x <= 30}

def employee_name(n: int) -> str:
    return f"{CN_NUM.get(n, str(n))}号员工"

def ensure_employee_runbooks(ids: Set[int]) -> bool:
    changed = False
    for n in sorted(ids):
        runbook = ROOT / f"EMPLOYEE{n}_OPERATION_RUNBOOK.md"
        if runbook.exists():
            continue
        runbook.write_text(f"""# {employee_name(n)}运行总手册

本文件由六号员工自动创建，因为仓库出现了 {employee_name(n)} 相关代码、文档或 workflow。

## 1. 身份定位

待补充：说明 {employee_name(n)} 是什么员工，不是什么员工。

## 2. 输入

待补充：说明数据来源、触发条件、依赖文件。

## 3. 输出

待补充：说明报告、artifact、机器可读 JSON 或推送内容。

## 4. workflow 与运行入口

待补充：说明主脚本、workflow 文件、触发方式、并发锁。

## 5. 成功标准

待补充：说明一次合格运行必须满足什么条件。

## 6. 禁止事项

- 不要把未验证规则写成最终成功经验。
- 不要只改代码不更新本主手册。
- 不要新建一堆散文档；内容统一写入本手册。

## 7. 六号员工备注

本手册是自动占位文档。后续 AI 工程师必须根据用户最终定下来的思路补全内容。
""", encoding="utf-8")
        changed = True
    return changed

def marker(kind: str, key: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.:-]", "_", key)[:120]
    return f"<!-- employee6-{kind}:{safe} -->"

def ensure_strategy_registry(text: str) -> bool:
    path = ROOT / "AI_ENGINEER_STRATEGY_REGISTRY.md"
    header = "# AI 工程师战法/模型规则登记册\n\n本文件由六号员工维护。新战法、新模型、新规则先登记为已落地/待验证，不得在未验证前写成最终成功。\n"
    current = path.read_text(encoding="utf-8") if path.exists() else header
    changed = False
    lower = text.lower()
    sha = sh(["git", "rev-parse", "--short=12", "HEAD"]) or "unknown"
    for kw in STRATEGY_KEYWORDS:
        if kw.lower() not in lower:
            continue
        mk = marker("strategy", f"{sha}-{kw}")
        if mk in current:
            continue
        entry = f"\n{mk}\n## {now()}｜识别规则/战法：{kw}\n\n- 来源 commit：`{sha}`\n- 状态：已检测到代码/文档落地痕迹；如未经过用户确认或复盘验证，只能视为待验证规则。\n- 六号员工处理：记录到战法登记册，并等待后续用户确认、复盘结果或员工主手册补充。\n"
        current = current.rstrip() + "\n" + entry
        changed = True
    if changed or not path.exists():
        path.write_text(current.rstrip()+"\n", encoding="utf-8")
        return True
    return False

def md_list(items: List[str]) -> str:
    return "\n".join(f"- `{x}`" for x in items) if items else "- 无"

def refresh_document_map() -> bool:
    runbooks = sorted(p.name for p in ROOT.glob("EMPLOYEE*_OPERATION_RUNBOOK.md"))
    text = f"""# AI 工程师文档地图

本文件由六号员工维护，用于告诉后续 AI 工程师：不同类型文档应该放在哪里、先读什么、改代码后应该同步更新哪些文档。

## 仓库入口类

- `README.md`
- `AI_ENGINEER_START_HERE.md`
- `AI_ENGINEER_FINAL_RULES_INDEX.md`

## 员工主手册

{md_list(runbooks)}

## 文档合并原则

- 每个员工默认只保留一个 `EMPLOYEEX_OPERATION_RUNBOOK.md`。
- 不再自动创建 `EMPLOYEEX_REPORT_SPEC.md`、`EMPLOYEEX_CHANGE_LOG.md`、`EMPLOYEEX_DIMENSION_SPEC.md`、`EMPLOYEEX_STRUCTURE_*SPEC.md` 等散文档。
- 报告规范、维度规范、结构规范、变更经验，都合并进对应员工主手册。
- 只有代码、workflow、真实报告产物可以作为独立文件保留。

## 成功经验和全局规则

- `AI_ENGINEER_SUCCESS_LEDGER.md`
- `AI_ENGINEER_FINAL_RULES_INDEX.md`
- `AI_ENGINEER_STRATEGY_REGISTRY.md`

## 新员工/新战法自动建档规则

- 出现 `employeeN_*.py`、`EMPLOYEEN_*.md`、`N号员工` 或相关 workflow 时，六号员工只允许自动生成 `EMPLOYEEN_OPERATION_RUNBOOK.md` 占位文档。
- 出现新战法或模型关键词时，六号员工登记到 `AI_ENGINEER_STRATEGY_REGISTRY.md`。
- 未经用户确认或复盘验证的新战法，只能写成待验证规则，不能写成最终成功经验。
"""
    p = ROOT / "AI_ENGINEER_DOCUMENT_MAP.md"
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    if old.strip() != text.strip():
        p.write_text(text, encoding="utf-8")
        return True
    return False

def main() -> None:
    text = collect_text()
    changed = False
    changed = ensure_employee_runbooks(employee_ids(text)) or changed
    changed = ensure_strategy_registry(text) or changed
    changed = refresh_document_map() or changed
    print("employee6 auto templates updated" if changed else "employee6 auto templates no changes")

if __name__ == "__main__":
    main()
