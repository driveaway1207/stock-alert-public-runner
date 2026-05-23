# 六号员工运行总手册

六号员工是文档审计员、代码变更归档员、成功经验沉淀员、最终规则索引维护员。

## 1. 身份定位

六号员工不负责选股、不负责交易、不负责预测。

六号员工只负责让后续 AI 工程师知道：

- 最近改了什么代码。
- 修改路径在哪里。
- 哪些 workflow 跑过。
- 哪些规则最终定下来了。
- 哪些成功经验已经落地。
- 哪些员工手册需要同步更新。

## 2. 是否自动运行

六号员工应该自动运行，但不能像交易员工那样频繁推送 Telegram。

推荐运行方式：

1. `push` 后自动运行，记录本次代码/文档/workflow 改动。
2. `workflow_run` 后自动运行，记录五号员工等关键 workflow 的运行结果。
3. `workflow_dispatch` 保留手动补跑入口。

六号员工默认只更新文档和 artifact，不主动推送 Telegram，避免每次代码提交都打扰用户。

## 3. 新员工/新战法时的文档生成规则

以后如果新建员工或新建战法，六号员工必须自动生成或补充对应文档入口。

### 新员工

如果出现以下路径或命名：

```text
employee7_*.py
EMPLOYEE7_*.md
.github/workflows/*employee7*.yml
七号员工
```

六号员工应自动识别为新员工，并生成或提示生成：

```text
EMPLOYEE7_OPERATION_RUNBOOK.md
EMPLOYEE7_REPORT_SPEC.md
```

同时更新：

```text
AI_ENGINEER_DOCUMENT_MAP.md
AI_ENGINEER_FINAL_RULES_INDEX.md
AI_ENGINEER_CHANGE_LOG.md
AI_ENGINEER_SUCCESS_LEDGER.md
```

### 新战法

如果 commit message 或文件内容出现明显战法名称，例如：

```text
黄金二倍凹口
核心压力线突破
BOLL缩口
二阶画线
Event/Context/Confirmation
```

六号员工应自动把它归类到对应员工或全局战法文档中。

新战法不能直接写成“已成功”，只能先写成：

- 已落地代码规则；
- 待复盘验证规则；
- 用户确认后升级为最终成功经验。

## 4. 三本账

### 4.1 改动流水账

文件：`AI_ENGINEER_CHANGE_LOG.md`

记录所有进入 GitHub 的改动：

- commit sha
- 时间
- 修改路径
- 文件归类
- commit message
- workflow 结果

### 4.2 成功经验账

文件：`AI_ENGINEER_SUCCESS_LEDGER.md`

只记录已经落地、未被用户拒绝、可复用的正确经验。

如果经验后来被用户否定，六号员工必须更正。

### 4.3 最终规则索引

文件：`AI_ENGINEER_FINAL_RULES_INDEX.md`

维护当前已经定下来的全局规则和员工规则入口。

## 5. 文档地图

文件：`AI_ENGINEER_DOCUMENT_MAP.md`

六号员工必须维护文档分类，例如：

- 仓库入口类。
- 全局规则类。
- 员工专属手册。
- 报告规范。
- workflow 触发规则。
- 数据源规则。
- 成功经验库。
- 改动流水账。

## 6. 自动判断边界

六号员工可以自动记录全部代码修改和运行结果。

但成功经验不能胡乱编造：

- 用户明确认可的，可以进入成功经验账。
- 已经落地且没有被用户拒绝的稳定工程规则，可以作为已落地经验记录。
- 新战法、新模型如果没经过复盘验证，只能写成待验证规则，不能写成最终成功。

## 7. 禁止事项

- 不要把失败尝试包装成成功经验。
- 不要把未验证战法写成最终规则。
- 不要修改 PAT、secrets、Telegram token。
- 不要频繁推送 Telegram。
- 不要覆盖用户已经定下来的员工职责。
- 不要只改代码不记文档。

## 8. 一句话总结

```text
六号员工负责把所有代码改动、路径、运行结果和最终成功经验沉淀成文档，让后续 AI 工程师直接继承最终定下来的思路，而不是每次重新踩坑。
```
