# stock-alert-public-runner

## AI 工程师唯一默认入口 / Employee System Source of Truth

本仓库 `driveaway1207/stock-alert-public-runner` 是当前员工系统唯一默认事实源。

任何新的 AI 工程师、Claude Cowork、ChatGPT/Codex 或人工维护者，只要接到关于员工系统、选股系统、归因系统、workflow、runner、报告、artifact、Telegram、操作说明、工程修改的需求，默认必须先进入本 public 仓库。

不要优先进入或引用：

```text
driveaway1207/stock-alert
```

该私有仓库不作为当前员工系统事实源。除非用户明确点名要求处理私有仓库，否则不要读取私有仓库作为当前依据。

---

## 进入仓库后的强制读取顺序

任何员工相关需求，先读：

1. `README.md`
2. `AI_ENGINEER_START_HERE.md`
3. `EMPLOYEE_SYSTEM_ROLES.md`
4. 对应员工的 `EMPLOYEE*_OPERATION_RUNBOOK.md`
5. 对应 runner 主脚本
6. 对应 workflow
7. 对应报告目录或 artifact 说明

没有读取 `AI_ENGINEER_START_HERE.md` 之前，不要修改代码、workflow、文档或员工规则。

---

## 当前一级文件

仓库要保持干净，一级规则文件只保留少数入口文件：

```text
README.md
AI_ENGINEER_START_HERE.md
EMPLOYEE_SYSTEM_ROLES.md
```

不要随意新增 `FINAL`、`V2`、`TEMP`、`PATCH`、`CHANGE_LOG`、`DOCUMENT_MAP`、`RULES_INDEX` 等散乱说明文件。能合并进一级入口或员工 runbook 的内容，不要新建文件。

---

## 当前员工体系

员工身份、职责边界、协作关系，以 `EMPLOYEE_SYSTEM_ROLES.md` 为准。

员工专项运行规则，以对应 `EMPLOYEE*_OPERATION_RUNBOOK.md` 为准。

当前已落地员工包括零号、一号、二号、三号、四号、五号、六号，以及后续已经在本 public 仓库完成身份定义、runbook、runner 或 workflow 支持的员工。

未落地员工不能写成当前已支持员工。

---

## 工程修改验收规则

不能只在聊天里说“已经改好”。任何工程修改必须以 GitHub 实际文件、commit sha、复查结果和运行证据为准。

回复用户时必须区分：

- 已提交：GitHub 已返回 commit sha。
- 已复查：重新读取 GitHub 文件，确认内容真实存在。
- 已验证：workflow、脚本、artifact、日志或消息推送产生预期结果。

如果工具报错、被拦截、冲突、没有返回 commit sha，必须明确说明没有落地，不能假装成功。

只改文档而没有改真正生效的代码或 workflow，不能说已经修好。

---

## 六号员工与文档清洁

六号员工是已落地的自动实时跟踪员和单文件文档清洁员。

它负责记录仓库 push、员工 workflow 完成事件、修改路径和散文档检查结果；记录只写入 `EMPLOYEE6_OPERATION_RUNBOOK.md`，不得制造散乱账本或新总规则文件。

---

## 一句话原则

整个员工系统默认只从这里开始：

```text
driveaway1207/stock-alert-public-runner
```

如果你不是被用户明确要求处理私有仓库，就不要从 `driveaway1207/stock-alert` 开始。