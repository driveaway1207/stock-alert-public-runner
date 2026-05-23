# AI 工程师最终规则索引

这里维护已经定下来的全局规则入口。六号员工会实时监听 push，并用最近 commit 回扫做防漏补记。

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
- 新员工出现时，六号员工自动生成 `EMPLOYEE{N}_OPERATION_RUNBOOK.md` 与 `EMPLOYEE{N}_REPORT_SPEC.md` 占位文档。
- 新战法出现时，六号员工自动登记到 `AI_ENGINEER_STRATEGY_REGISTRY.md`，未验证前不得写成最终成功。

## 员工手册入口

- `EMPLOYEE0_OPERATION_RUNBOOK.md`
- `EMPLOYEE5_OPERATION_RUNBOOK.md`
- `EMPLOYEE6_OPERATION_RUNBOOK.md`

## 六号员工维护原则

- 所有代码修改都进入 `AI_ENGINEER_CHANGE_LOG.md`。
- 用户认可、未被拒绝、已落地的正确经验进入 `AI_ENGINEER_SUCCESS_LEDGER.md`。
- AI 工程师自己确认的稳定工程经验，也要主动写入文档。
- 错误尝试不能包装成成功经验。
