# 六号员工运行总手册

六号员工是文档审计员、代码变更归档员、成功经验沉淀员、最终规则索引维护员。

六号员工相关说明统一合并到本文件；六号只保留本手册，避免 `REPORT_SPEC`、`CHANGE_LOG` 等散文件继续膨胀。

---

## 1. 身份定位

六号员工不负责选股、不负责交易、不负责预测，不负责判断代码是不是猴子代码。

六号员工负责让后续 AI 工程师知道：

- 最近改了什么代码。
- 修改路径在哪里。
- 哪些 workflow 跑过。
- 哪些规则最终定下来了。
- 哪些成功经验已经落地。
- 哪些员工手册需要同步更新。
- 哪些失败经验不能再重复。

---

## 2. 六号员工与零号员工分工

正确流程不是让六号员工同时当裁判和档案员。

```text
任意员工 push / workflow 运行
  ↓
零号员工：审计代码质量、猴子代码、生产链路风险
  ↓
六号员工：记录改动路径、运行结果、零号审计结果、成功经验和员工档案
```

零号员工负责审计风险；六号员工负责沉淀文档。

---

## 3. 自动运行频率原则

六号员工必须自动运行，但不能高频占用 runner，不能影响一号到五号员工的正式工作。

原则：

1. 主触发：`workflow_run`，在零号、一号、二号、三号、四号、五号员工完成后归档。
2. 辅助触发：`workflow_dispatch`，必要时手动补跑。
3. 兜底触发：只能低频定时补扫，建议每天一次，不允许每 5 分钟跑一次。
4. 六号员工不推送 Telegram，只写文档账本和员工手册。
5. 六号员工每次运行都回扫最近 commit，已记录 marker 不重复写，漏掉的自动补上。

---

## 4. 文档合并原则

用户要求：文件不要越建越多。每个员工尽量只保留一个主运行手册。

六号必须执行：

- 五号相关说明合并进 `EMPLOYEE5_OPERATION_RUNBOOK.md`。
- 六号相关说明合并进 `EMPLOYEE6_OPERATION_RUNBOOK.md`。
- `*_REPORT_SPEC.md`、`*_DIMENSION_SPEC.md`、`*_STRUCTURE_*SPEC.md`、`*_CHANGE_LOG.md` 等散文件，如果内容已并入主手册，就删除。
- 只有当某文件是代码、workflow 或真实产物，不是说明文档，才保留。

六号禁止因为自动归档而不断新建散文档。

---

## 5. 报告/归档规范

六号员工报告是文档审计/归档报告，不是研究报告、推荐报告或交易报告。

必须包含：

- 日期 / 运行时间。
- 归档对象。
- 修改文件。
- commit sha。
- 运行结果。
- 是否已写入对应员工主手册。
- 是否有失败尝试被误写成成功经验。
- 是否需要删除或合并散文档。

禁止写法：

- 不要用空话凑维度。
- 不要把未验证内容写成最终结论。
- 不要输出与员工定位无关的内容。
- 不要把失败尝试包装成成功经验。

---

## 6. 三本账和入口

总账可以保留，但员工自己的信息优先写入对应员工主手册。

总账包括：

```text
AI_ENGINEER_CHANGE_LOG.md
AI_ENGINEER_SUCCESS_LEDGER.md
AI_ENGINEER_FINAL_RULES_INDEX.md
AI_ENGINEER_DOCUMENT_MAP.md
AI_ENGINEER_STRATEGY_REGISTRY.md
```

员工主手册示例：

```text
EMPLOYEE0_OPERATION_RUNBOOK.md
EMPLOYEE5_OPERATION_RUNBOOK.md
EMPLOYEE6_OPERATION_RUNBOOK.md
```

后续新员工不应自动生成三四个散文档；先生成一个 `EMPLOYEEX_OPERATION_RUNBOOK.md`，确需拆分时必须得到用户认可。

---

## 7. 成功经验写入标准

只有满足以下条件，才能写入成功经验：

1. 已有 GitHub 文件或代码落地。
2. 已有 commit sha。
3. 运行链路实际成功，或用户明确确认该规则正确。
4. 未被用户否定。
5. 写入位置是对应员工主手册或总成功账。

失败尝试只能写入“踩坑复盘”，不能写成成功经验。

---

## 8. 禁止事项

- 不要把失败尝试包装成成功经验。
- 不要把未验证战法写成最终规则。
- 不要修改 PAT、secrets、Telegram token。
- 不要频繁推送 Telegram。
- 不要高频定时运行占用 runner。
- 不要每 5 分钟补扫。
- 不要覆盖用户已经定下来的员工职责。
- 不要只改代码不记文档。
- 不要新建大量散文档。
- 不要只写总账不写对应员工主手册。

---

## 9. 一句话总结

```text
零号员工负责抓代码风险，六号员工负责把所有员工的代码动作、路径、运行结果和最终成功经验沉淀到总账与对应员工主手册；六号必须低频兜底、事件驱动归档，不能每 5 分钟抢占正式员工资源，也不能制造一堆散乱文档。
```

---

## 自动归档记录

本区由六号员工追加，记录该员工相关代码、文档、workflow、报告规范、运行链路改动。

<!-- employee6-employee-6:0f47090ecdf3 -->
### 2026-05-24 10:24:22 UTC｜Commit `0f47090ecdf3`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Stop employee6 from regenerating employee change logs
- 自动归类：{"code": 1}
- 归档判断：员工代码或文档常规更新。
- 修改路径：
  - `employee6_employee_archiver.py`

<!-- employee6-employee-6:081eaffa5ddf -->
### 2026-05-24 10:24:22 UTC｜Commit `081eaffa5ddf`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Stop employee6 from generating scattered employee docs
- 自动归类：{"code": 1}
- 归档判断：文档/操作手册/成功经验更新。
- 修改路径：
  - `employee6_auto_templates.py`

<!-- employee6-employee-6:d756a301ae86 -->
### 2026-05-24 10:24:22 UTC｜Commit `d756a301ae86`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Remove merged employee6 change log
- 自动归类：{"docs": 1}
- 归档判断：员工代码或文档常规更新。
- 修改路径：
  - `EMPLOYEE6_CHANGE_LOG.md`

<!-- employee6-employee-6:d8277fb4ae96 -->
### 2026-05-24 10:24:22 UTC｜Commit `d8277fb4ae96`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Remove merged employee6 report spec
- 自动归类：{"docs": 1}
- 归档判断：报告输出/格式更新。
- 修改路径：
  - `EMPLOYEE6_REPORT_SPEC.md`

<!-- employee6-employee-6:2106daf6dea7 -->
### 2026-05-24 10:24:22 UTC｜Commit `2106daf6dea7`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Consolidate employee6 docs into operation runbook
- 自动归类：{"docs": 1}
- 归档判断：文档/操作手册/成功经验更新。
- 修改路径：
  - `EMPLOYEE6_OPERATION_RUNBOOK.md`

<!-- employee6-employee-6:0923f123a543 -->
### 2026-05-24 10:24:22 UTC｜Commit `0923f123a543`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：docs: add global employee identity lock rule
- 自动归类：{"docs": 1}
- 归档判断：文档/操作手册/成功经验更新。
- 修改路径：
  - `AI_ENGINEER_FINAL_RULES_INDEX.md`

<!-- employee6-employee-6:02e2c1bca273 -->
### 2026-05-24 10:27:09 UTC｜Commit `02e2c1bca273`

- 事件：`workflow_run`｜运行：`69`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Delete regenerated employee6 change log after disabling generator
- 自动归类：{"docs": 1}
- 归档判断：员工代码或文档常规更新。
- 修改路径：
  - `EMPLOYEE6_CHANGE_LOG.md`
