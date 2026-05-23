# AI 工程师文档地图

本文件由六号员工维护，用于告诉后续 AI 工程师：不同类型文档应该放在哪里、先读什么、改代码后应该同步更新哪些文档。

## 1. 仓库入口类

- `README.md`：仓库第一入口，只放最重要的先读顺序和总方向。
- `AI_ENGINEER_START_HERE.md`：AI 工程师总入口，说明全局维护原则、员工定位、禁止事项。
- `AI_ENGINEER_FINAL_RULES_INDEX.md`：最终规则索引，汇总已经定下来的全局规则。

## 2. 全局规则类

- `AI_ENGINEER_KLINE_PERIOD_RULES.md`：所有员工通用 K 线周期换算规则。
- `AI_ENGINEER_DOCUMENT_MAP.md`：本文档，负责文档分类。

后续如新增全局规则，可新增：

- `GLOBAL_DATA_SOURCE_RULES.md`
- `GLOBAL_WORKFLOW_TRIGGER_RULES.md`
- `GLOBAL_REPORT_STYLE_RULES.md`
- `GLOBAL_SYSTEM_BUILDING_PRINCIPLES.md`

## 3. 员工专属操作手册

- `EMPLOYEE5_OPERATION_RUNBOOK.md`：五号员工运行总手册。
- `EMPLOYEE6_OPERATION_RUNBOOK.md`：六号员工运行总手册。

未来新增员工时，统一命名：

```text
EMPLOYEE1_OPERATION_RUNBOOK.md
EMPLOYEE2_OPERATION_RUNBOOK.md
EMPLOYEE3_OPERATION_RUNBOOK.md
EMPLOYEE7_OPERATION_RUNBOOK.md
```

## 4. 员工报告规范

未来建议新增：

```text
EMPLOYEE1_REPORT_SPEC.md
EMPLOYEE3_REPORT_SPEC.md
EMPLOYEE5_REPORT_SPEC.md
EMPLOYEE6_REPORT_SPEC.md
```

报告规范只写报告结构、字段、样式、推送长度、artifact 口径，不写模型算法。

## 5. 成功经验和改动流水账

- `AI_ENGINEER_CHANGE_LOG.md`：所有进入 GitHub 的代码/文档/workflow 改动流水账。
- `AI_ENGINEER_SUCCESS_LEDGER.md`：已落地、未被用户拒绝、可复用的成功经验沉淀账。
- `AI_ENGINEER_FINAL_RULES_INDEX.md`：最终定下来的规则索引。

## 6. workflow 触发文档

当前五号员工触发规则写在：

- `EMPLOYEE5_OPERATION_RUNBOOK.md`
- `.github/workflows/fifth_employee.yml`

未来建议新增：

```text
WORKFLOW_TRIGGER_RUNBOOK.md
EMPLOYEE5_TRIGGER_RUNBOOK.md
EMPLOYEE6_TRIGGER_RUNBOOK.md
```

## 7. 数据源规则文档

当前五号员工数据源规则写在：

- `EMPLOYEE5_OPERATION_RUNBOOK.md`
- `AI_ENGINEER_START_HERE.md`

已定口径：

```text
历史K线：BaoStock/Bostock 优先，AKShare 辅助，东方财富最后兜底。
北交所：保留 AKShare / 东方财富兜底。
```

未来可拆成：

```text
DATA_SOURCE_PRIORITY.md
BAOSTOCK_USAGE_RULES.md
NORTH_EXCHANGE_DATA_RULES.md
```

## 8. 新员工/新战法自动建档规则

六号员工发现新员工路径或新战法关键词后，应至少更新：

- `AI_ENGINEER_CHANGE_LOG.md`
- `AI_ENGINEER_SUCCESS_LEDGER.md`
- `AI_ENGINEER_FINAL_RULES_INDEX.md`
- 本文档

如果出现新员工，例如 `employee7_*.py`、`EMPLOYEE7_*.md`、七号员工 workflow，应自动生成或提示生成：

```text
EMPLOYEE7_OPERATION_RUNBOOK.md
EMPLOYEE7_REPORT_SPEC.md
```

如果出现新战法，应先记录为：

```text
已落地代码规则 / 待复盘验证规则
```

不能在未验证前直接写成最终成功经验。

## 9. 一句话总结

```text
README 负责入口，AI_ENGINEER_START_HERE 负责总纲，EMPLOYEE*_OPERATION_RUNBOOK 负责员工手册，AI_ENGINEER_CHANGE_LOG 负责所有改动，AI_ENGINEER_SUCCESS_LEDGER 负责成功经验，AI_ENGINEER_FINAL_RULES_INDEX 负责最终规则索引。
```
