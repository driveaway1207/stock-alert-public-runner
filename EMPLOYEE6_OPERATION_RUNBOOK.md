# 六号员工运行总手册

六号员工是文档审计员、代码变更归档员、成功经验沉淀员、最终规则索引维护员。

## 1. 身份定位

六号员工不负责选股、不负责交易、不负责预测，不负责判断代码是不是猴子代码。

六号员工负责让后续 AI 工程师知道：

- 最近改了什么代码。
- 修改路径在哪里。
- 哪些 workflow 跑过。
- 哪些规则最终定下来了。
- 哪些成功经验已经落地。
- 哪些员工手册需要同步更新。
- 每个员工自己的变更档案在哪里。

## 2. 六号员工与零号员工分工

正确流程不是让六号员工同时当裁判和档案员。

最终分工：

```text
任意员工 push / workflow 运行
  ↓
零号员工：审计代码质量、猴子代码、生产链路风险
  ↓
六号员工：记录改动路径、运行结果、零号审计结果、成功经验和员工档案
```

零号员工负责审计风险；六号员工负责沉淀文档。

## 3. 自动运行频率原则

六号员工必须自动运行，但不能高频占用 runner，不能影响一号到五号员工的正式工作。

最终原则：

1. 主触发：`workflow_run`，在零号、一号、二号、三号、四号、五号员工完成后归档。
2. 辅助触发：`workflow_dispatch`，必要时手动补跑。
3. 兜底触发：只能低频定时补扫，建议每天一次，不允许每 5 分钟跑一次。
4. 六号员工不推送 Telegram，只写文档账本和分员工档案。
5. 六号员工每次运行都回扫最近 commit，已经记录过的 marker 不重复写，漏掉的自动补上。

## 4. 分员工档案规则

六号员工不是只记五号员工。所有员工有动作，都必须记。

总账：

```text
AI_ENGINEER_CHANGE_LOG.md
AI_ENGINEER_SUCCESS_LEDGER.md
AI_ENGINEER_FINAL_RULES_INDEX.md
AI_ENGINEER_DOCUMENT_MAP.md
AI_ENGINEER_STRATEGY_REGISTRY.md
```

分员工档案：

```text
EMPLOYEE0_CHANGE_LOG.md
EMPLOYEE1_CHANGE_LOG.md
EMPLOYEE2_CHANGE_LOG.md
EMPLOYEE3_CHANGE_LOG.md
EMPLOYEE4_CHANGE_LOG.md
EMPLOYEE5_CHANGE_LOG.md
EMPLOYEE6_CHANGE_LOG.md
EMPLOYEE7_CHANGE_LOG.md
...
```

如果某次 commit 触碰多个员工，六号员工必须分别写入多个员工档案。

## 5. 新员工/新战法时的文档生成规则

以后如果新建员工或新建战法，六号员工必须自动生成或补充对应文档入口。

新员工应自动识别并生成或提示生成：

```text
EMPLOYEE7_OPERATION_RUNBOOK.md
EMPLOYEE7_REPORT_SPEC.md
EMPLOYEE7_CHANGE_LOG.md
```

新战法不能直接写成“已成功”，只能先写成：

- 已落地代码规则；
- 待复盘验证规则；
- 用户确认后升级为最终成功经验。

## 6. 三本账

### 6.1 改动流水账

文件：`AI_ENGINEER_CHANGE_LOG.md`

记录所有进入 GitHub 的改动：commit sha、时间、修改路径、文件归类、commit message、workflow 结果。

### 6.2 成功经验账

文件：`AI_ENGINEER_SUCCESS_LEDGER.md`

只记录已经落地、未被用户拒绝、可复用的正确经验。如果经验后来被用户否定，六号员工必须更正。

### 6.3 最终规则索引

文件：`AI_ENGINEER_FINAL_RULES_INDEX.md`

维护当前已经定下来的全局规则和员工规则入口。

## 7. 禁止事项

- 不要把失败尝试包装成成功经验。
- 不要把未验证战法写成最终规则。
- 不要修改 PAT、secrets、Telegram token。
- 不要频繁推送 Telegram。
- 不要高频定时运行占用 runner。
- 不要每 5 分钟补扫。
- 不要覆盖用户已经定下来的员工职责。
- 不要只改代码不记文档。
- 不要只写总账不写分员工档案。

## 8. 一句话总结

```text
零号员工负责抓代码风险，六号员工负责把所有员工的代码动作、路径、运行结果和最终成功经验沉淀成总账与分员工档案；六号员工必须低频兜底、事件驱动归档，不能每 5 分钟抢占正式员工资源。
```
