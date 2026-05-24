# 六号员工运行总手册

更新时间：2026-05-24

六号员工最终定位：**自动实时跟踪员 + 单文件文档清洁员**。

六号必须自动跟踪仓库和员工 workflow 的变化，但只能把记录写进本文件 `EMPLOYEE6_OPERATION_RUNBOOK.md`，不得再生成任何散文档或总账文件。

---

## 1. 新身份定位

六号员工现在负责：

```text
自动跟踪仓库 push 和零号到五号员工 workflow 完成事件，记录关键事实；同时检查是否出现散乱文档、重复文档、自动生成垃圾文档。
```

六号不负责：

- 判断股票。
- 判断交易。
- 判断代码是不是猴子代码；这是零号职责。
- 自动判断某次尝试是不是成功经验。
- 自动生成 `CHANGE_LOG`。
- 自动生成 `REPORT_SPEC`。
- 自动生成 `DIMENSION_SPEC`。
- 自动生成 `STRUCTURE_SPEC`。
- 自动写 `AI_ENGINEER_CHANGE_LOG`。
- 自动写 `AI_ENGINEER_SUCCESS_LEDGER`。
- 自动写 `AI_ENGINEER_FINAL_RULES_INDEX`。
- 自动写 `AI_ENGINEER_DOCUMENT_MAP`。

---

## 2. 为什么这样设计

旧六号的问题是：记录太碎，生成大量没有阅读价值的散文件，还会把已经删除的 `CHANGE_LOG / REPORT_SPEC` 自动生成回来，形成二次污染。

纯手动六号也不对，因为用户要求员工变化要能实时跟踪记录。

最终折中方案：

```text
自动运行，但不高频；
自动记录，但只写一个文件；
自动检查，但不自动判断成功；
自动提交，但只提交 EMPLOYEE6_OPERATION_RUNBOOK.md；
不再生成任何新文档。
```

---

## 3. 当前保留内容

六号现在只保留：

```text
EMPLOYEE6_OPERATION_RUNBOOK.md
employee6_doc_curator.py
.github/workflows/employee6_doc_curator.yml
```

其中：

- `EMPLOYEE6_OPERATION_RUNBOOK.md`：六号唯一记录文件，包含身份、规则和自动跟踪记录。
- `employee6_doc_curator.py`：事件跟踪脚本，只把记录追加到本手册。
- `.github/workflows/employee6_doc_curator.yml`：事件驱动 workflow。

---

## 4. workflow 规则

六号允许自动触发，但只允许低污染触发：

```yaml
on:
  push:
    branches:
      - main
  workflow_run:
    workflows: ['零号员工', '一号员工', '二号员工', '三号员工', '四号员工', '五号员工']
    types:
      - completed
  workflow_dispatch:
```

不允许：

- 高频 `schedule`。
- 每 5 分钟轮询。
- 自动生成散文件。
- 自动写多个账本。
- 自动提交除 `EMPLOYEE6_OPERATION_RUNBOOK.md` 以外的文件。

允许自动提交的唯一文件：

```text
EMPLOYEE6_OPERATION_RUNBOOK.md
```

---

## 5. 自动记录内容

六号每次记录只写事实，不写结论。

记录字段：

```text
触发事件
运行编号
触发人
commit sha
commit message
文件归类
修改路径
当前是否发现员工散文档
当前是否发现旧总账类文档
六号处理说明：只记录事实，不判断成功经验
```

六号不得把“提交成功”写成“运行成功”，不得把“尝试过”写成“成功经验”。

---

## 6. 文档清洁规则

仓库文档原则：

```text
每个员工默认只保留一个主手册：EMPLOYEEX_OPERATION_RUNBOOK.md
```

需要删除或合并的散文档类型：

```text
EMPLOYEE*_CHANGE_LOG.md
EMPLOYEE*_REPORT_SPEC.md
EMPLOYEE*_DIMENSION_SPEC.md
EMPLOYEE*_STRUCTURE_*SPEC.md
AI_ENGINEER_CHANGE_LOG.md
AI_ENGINEER_SUCCESS_LEDGER.md
AI_ENGINEER_DOCUMENT_MAP.md
AI_ENGINEER_FINAL_RULES_INDEX.md
AI_ENGINEER_STRATEGY_REGISTRY.md
```

例外：如果用户明确要求保留某份独立文档，才允许保留。

---

## 7. 禁止事项

- 不要自动创建新文档。
- 不要自动写成功经验。
- 不要自动写变更流水账到散文件。
- 不要高频运行。
- 不要把失败尝试包装成成功经验。
- 不要制造 `CHANGE_LOG / REPORT_SPEC` 这类散文件。
- 不要碰一号员工生产链路。
- 不要碰 PAT、token、secrets、Telegram 凭证。

---

## 8. 一句话总结

```text
六号员工不是废掉，也不是纯手动；它应该自动实时跟踪，但只能把关键事实写进自己的唯一主手册，绝不能再制造散乱账本和垃圾文档。
```

---

## 自动跟踪记录

本区由六号员工自动追加，只记录关键事实：触发事件、commit、修改路径、是否发现散文档。六号不得在本区判断成功经验。

<!-- employee6-track:3170171ace31 -->
### 2026-05-24 10:44:01 UTC｜push｜`3170171ace31`

- 运行：`73`｜触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Make employee6 record events into one runbook only
- 文件归类：{"code": 1}
- 修改路径：
  - `employee6_doc_curator.py`
- 当前散文档检查：
  - 无
- 当前旧总账检查：
  - 无
- 六号处理：只记录事实；不生成散文档；不判断成功经验。

<!-- employee6-track:6ac571cccafc -->
### 2026-05-24 10:45:29 UTC｜push｜`6ac571cccafc`

- 运行：`75`｜触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Update employee6 runbook to event-driven single-file tracking
- 文件归类：{"docs": 1}
- 修改路径：
  - `EMPLOYEE6_OPERATION_RUNBOOK.md`
- 当前散文档检查：
  - 无
- 当前旧总账检查：
  - 无
- 六号处理：只记录事实；不生成散文档；不判断成功经验。

<!-- employee6-track:bc6d5ad65c0b -->
### 2026-05-24 10:50:48 UTC｜push｜`bc6d5ad65c0b`

- 运行：`77`｜触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- workflow结果：无，本次不是 workflow_run 事件。
- commit message：Add workflow result details to employee6 tracking
- 文件归类：{"code": 1}
- 修改路径：
  - `employee6_doc_curator.py`
- 当前散文档检查：
  - 无
- 当前旧总账检查：
  - 无
- 六号处理：只记录事实；不生成散文档；不判断成功经验。

<!-- employee6-track:7c8bdfdabada -->
### 2026-05-24 12:40:55 UTC｜push｜`7c8bdfdabada`

- 运行：`79`｜触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- workflow结果：无，本次不是 workflow_run 事件。
- commit message：Refactor employee5 structural report into cause-pool attribution
- 文件归类：{"code": 1}
- 修改路径：
  - `employee5_structural_report.py`
- 当前散文档检查：
  - 无
- 当前旧总账检查：
  - 无
- 六号处理：只记录事实；不生成散文档；不判断成功经验。

<!-- employee6-track:22a772d2a252 -->
### 2026-05-24 12:43:06 UTC｜push｜`22a772d2a252`

- 运行：`81`｜触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- workflow结果：无，本次不是 workflow_run 事件。
- commit message：Document employee5 cause-pool modeling principles
- 文件归类：{"docs": 1}
- 修改路径：
  - `EMPLOYEE5_OPERATION_RUNBOOK.md`
- 当前散文档检查：
  - 无
- 当前旧总账检查：
  - 无
- 六号处理：只记录事实；不生成散文档；不判断成功经验。

<!-- employee6-track:833d1aa1660a -->
### 2026-05-24 12:59:03 UTC｜push｜`833d1aa1660a`

- 运行：`83`｜触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- workflow结果：无，本次不是 workflow_run 事件。
- commit message：Quantify employee5 cause pool with formal names
- 文件归类：{"code": 1}
- 修改路径：
  - `employee5_structural_report.py`
- 当前散文档检查：
  - 无
- 当前旧总账检查：
  - 无
- 六号处理：只记录事实；不生成散文档；不判断成功经验。

<!-- employee6-track:3d5d9b384db2 -->
### 2026-05-24 13:00:38 UTC｜push｜`3d5d9b384db2`

- 运行：`85`｜触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- workflow结果：无，本次不是 workflow_run 事件。
- commit message：Update employee5 runbook with quantified cause-pool formulas
- 文件归类：{"docs": 1}
- 修改路径：
  - `EMPLOYEE5_OPERATION_RUNBOOK.md`
- 当前散文档检查：
  - 无
- 当前旧总账检查：
  - 无
- 六号处理：只记录事实；不生成散文档；不判断成功经验。
