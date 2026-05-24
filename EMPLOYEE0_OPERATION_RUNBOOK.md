# 零号员工运行总手册

零号员工是代码审计员、猴子代码拦截员、生产链路守门员。

## 1. 身份定位

零号员工不负责选股、不负责交易、不负责推送荐股报告。

零号员工负责在任何员工代码、workflow、文档或运行链路发生变化时，审计是否出现：

- 猴子代码。
- 临时补丁。
- wrapper 套壳。
- 伪逻辑。
- 生产入口误改。
- workflow 误改。
- PAT / token / secrets 误伤。
- 缓存、Telegram、BaoStock、artifact 等生产链路误伤。
- 员工名字、身份、职责、workflow 显示名、报告身份未经用户明确要求被改。

## 1.1 员工身份与命名锁定红线

所有员工的中文名字、编号、身份定位、职责边界、workflow 显示名、报告标题和主脚本身份，默认全部锁定。

正确原则：

- 用户明确要求改名、改身份、改职责，才允许修改。
- 用户没有明确要求时，AI 工程师不能自己理解、发挥、包装、重命名或重新解释员工身份。
- “为了更专业”“为了统一命名”“为了架构升级”都不能成为擅自改名的理由。
- 任何未授权的名字漂移、身份漂移、职责漂移，都按猴子代码风险处理。
- 零号员工必须把这类问题列为 P0/P1 审计项，而不是当成文档小问题。

审计时必须检查：

- `EMPLOYEE_SYSTEM_ROLES.md` 中员工身份是否仍是用户确认过的口径。
- `.github/workflows/*.yml` 的 `name` / `run-name` / job name 是否擅自改了员工名。
- 员工主脚本、报告标题、artifact 名、报告目录是否出现未经授权的新名字或旧名字回流。
- 是否出现“把某员工改成另一个员工职责”的情况，例如把研究员改成审计官、把归因员工改成买入推荐员工。

允许修改的前提只有一个：用户明确说要改，并且本次修改记录清楚说明“这是用户要求”。

## 2. 与六号员工分工

正确流程：

```text
任意员工 push / workflow 运行
  ↓
零号员工审计代码质量和生产链路风险
  ↓
六号员工记录改动路径、运行结果、零号审计结果和成功经验
  ↓
分别写入总账和对应员工档案
```

零号员工负责判断有没有风险；六号员工负责把结果沉淀成文档。

## 3. 触发方式

零号员工应监听：

- `push`：任何员工代码进入 main 后审计。
- `workflow_dispatch`：需要时手动补审。
- `schedule`：每天兜底扫描。

## 4. 输出

零号员工输出到：

```text
employee0_reports/employee0_audit_report.md
employee0_reports/employee0_audit_report.json
```

## 5. 审计重点

- 受保护文件：`stock_alert.py`、`.github/workflows/`、各员工主脚本、缓存目录、推送链路。
- 敏感关键词：PAT、GH_PAT、GITHUB_TOKEN、secrets、Telegram token、DATA_GATE_TARGET_DATE、LAST_TRADE_DAY、BaoStock、cache、artifact。
- 猴子代码信号：monkey、patch、wrapper、quick fix、临时、TODO、FIXME、hardcode、eval、exec、os.system 等。
- 身份漂移信号：员工名字、编号、职责、workflow 显示名、报告标题、报告目录、主脚本身份在没有用户明确要求时被改。

## 6. 禁止事项

- 不要直接修改生产代码。
- 不要推送 Telegram。
- 不要把审计结果包装成成功经验。
- 不要替六号员工写长期文档账本。
- 不要误伤 PAT、secrets、token。
- 不要未经用户明确要求修改任何员工的名字、身份、职责、workflow 显示名或报告身份。

## 7. 一句话总结

```text
零号员工负责抓代码风险、生产链路风险和员工身份漂移风险；六号员工负责把所有人的动作和经验记录进档案。
```

---

## 自动归档记录

本区由六号员工追加，记录该员工相关代码、文档、workflow、报告规范、运行链路改动。

<!-- employee6-employee-0:3fff1d7c8f5a -->
### 2026-05-24 10:24:22 UTC｜Commit `3fff1d7c8f5a`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：docs: add employee identity drift audit rule
- 自动归类：{"docs": 1}
- 归档判断：文档/操作手册/成功经验更新。
- 修改路径：
  - `EMPLOYEE0_OPERATION_RUNBOOK.md`
