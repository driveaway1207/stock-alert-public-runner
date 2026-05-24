# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent

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
]


def list_matches(pattern: str) -> list[str]:
    return sorted(p.name for p in ROOT.glob(pattern) if p.is_file())


def main() -> None:
    print("【六号员工】手动文档清洁检查")
    print("六号已降级：不再自动生成文档、不再自动写总账、不再自动提交。")
    print("每个员工默认只保留 EMPLOYEEX_OPERATION_RUNBOOK.md 一个主手册。")

    scattered: list[str] = []
    for pattern in SCATTER_PATTERNS:
        scattered.extend(list_matches(pattern))

    if scattered:
        print("发现应合并/删除的员工散文档：")
        for name in sorted(set(scattered)):
            print(f"- {name}")
    else:
        print("未发现员工散文档。")

    existing_global = [name for name in GLOBAL_LEDGER_FILES if (ROOT / name).exists()]
    if existing_global:
        print("发现旧总账类文档，建议只保留最终规则索引或并入 README：")
        for name in existing_global:
            print(f"- {name}")
    else:
        print("未发现旧总账类文档。")

    runbooks = sorted(p.name for p in ROOT.glob("EMPLOYEE*_OPERATION_RUNBOOK.md"))
    print("当前员工主手册：")
    for name in runbooks:
        print(f"- {name}")


if __name__ == "__main__":
    main()
