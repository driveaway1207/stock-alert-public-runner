# AI 工程师文档地图

本文件由六号员工维护，用于告诉后续 AI 工程师：不同类型文档应该放在哪里、先读什么、改代码后应该同步更新哪些文档。

## 仓库入口类

- `README.md`
- `AI_ENGINEER_START_HERE.md`
- `AI_ENGINEER_FINAL_RULES_INDEX.md`

## 员工主手册

- `EMPLOYEE0_OPERATION_RUNBOOK.md`
- `EMPLOYEE5_OPERATION_RUNBOOK.md`
- `EMPLOYEE6_OPERATION_RUNBOOK.md`

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
