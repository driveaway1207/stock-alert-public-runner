# AI 工程师从这里开始

本文件是后续 AI 工程师、Claude Cowork、ChatGPT/Codex 或人工维护者处理员工系统时的第一操作入口。

## 1. 唯一默认仓库

当前员工系统唯一默认事实源是：

```text
driveaway1207/stock-alert-public-runner
```

适用范围包括：

- 零号员工
- 一号员工
- 二号员工
- 三号员工
- 四号员工
- 五号员工
- 后续新增员工
- 所有 runner
- 所有 workflow
- 所有报告与 artifact
- 所有 Telegram 推送链路
- 所有操作手册与 AI 工程说明

## 2. 私有仓库处理规则

```text
driveaway1207/stock-alert
```

不作为当前员工系统事实源。

除非用户明确点名要求处理私有仓库，否则不要进入私有仓库查找员工当前规则、当前代码、当前 workflow 或当前说明。

禁止把私有仓库旧文件、旧文档、旧 workflow、旧员工分工、旧代码当作当前事实。

## 3. 强制读取顺序

任何员工相关需求，先按以下顺序读取：

1. `README.md`
2. `AI_ENGINEER_START_HERE.md`
3. `EMPLOYEE_SYSTEM_ROLES.md`
4. 对应员工的 `EMPLOYEE*_OPERATION_RUNBOOK.md`
5. 对应 runner 主脚本
6. 对应 workflow
7. 对应报告目录或 artifact 说明

如果对应员工的 runbook 暂不存在，先读 `EMPLOYEE_SYSTEM_ROLES.md`，再读取实际存在的 runner 和 workflow，不能凭旧仓库或记忆猜测。

## 4. 员工定位总规则

所有员工均以本 public 仓库中的当前文件为准。

当前员工体系的基础分工应从 `EMPLOYEE_SYSTEM_ROLES.md` 读取；每个员工的运行细节应从对应 `EMPLOYEE*_OPERATION_RUNBOOK.md` 读取。

不要只因为用户提到某个员工，就去私有仓库或旧文件里搜索同名脚本。

## 5. 工程修改验收规则

不能只在聊天里说“已经改好”。任何工程修改必须以 GitHub 实际文件、commit sha、复查结果和运行证据为准。

回复用户时必须区分：

- 已提交：GitHub 已返回 commit sha。
- 已复查：重新读取 GitHub 文件，确认内容真实存在。
- 已验证：workflow 或脚本运行成功，并产生预期输出。

如果工具报错、被拦截、冲突、没有返回 commit sha，必须明确说明没有落地，不能假装成功。

只改文档而没有改真正生效的代码或 workflow，不能说已经修好。

## 6. 禁止事项

- 禁止先搜私有仓库。
- 禁止把私有仓库结果覆盖 public 仓库结果。
- 禁止因为文件名相似就把旧仓库当当前仓库。
- 禁止只看单个员工文档，不看全局入口规则。
- 禁止没读 runbook 就改 workflow。
- 禁止没有 commit sha 却说已提交。
- 禁止没有复查文件却说已复查。
- 禁止没有 workflow、artifact、日志或 Telegram 证据却说已验证。

## 7. 一句话原则

整个员工系统只有一个默认事实源：

```text
driveaway1207/stock-alert-public-runner
```

零号、一号、二号、三号、四号、五号及后续员工，均从本 public 仓库读取规则、代码、workflow、报告和操作手册；私有仓库默认忽略。