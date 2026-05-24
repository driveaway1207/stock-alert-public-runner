# AI_ENGINEER_START_HERE

本文件是员工系统最高优先级入口文档。默认事实源是：

```text
driveaway1207/stock-alert-public-runner
```

私有仓库 `driveaway1207/stock-alert` 默认不作为当前事实源，除非用户明确点名要求。

## 强制读取顺序

任何员工相关需求，必须先读：

1. `README.md`
2. `AI_ENGINEER_START_HERE.md`
3. `EMPLOYEE_SYSTEM_ROLES.md`
4. 对应 `EMPLOYEE*_OPERATION_RUNBOOK.md`
5. 对应 runner 主脚本
6. 对应 workflow

## 文件治理硬规则

仓库必须保持干净，规则集中，按一级、二级、三级分层管理。

一级总规则文件固定为：

```text
README.md
AI_ENGINEER_START_HERE.md
EMPLOYEE_SYSTEM_ROLES.md
```

二级员工手册固定使用：

```text
EMPLOYEE0_OPERATION_RUNBOOK.md
EMPLOYEE1_OPERATION_RUNBOOK.md
EMPLOYEE2_OPERATION_RUNBOOK.md
EMPLOYEE3_OPERATION_RUNBOOK.md
EMPLOYEE4_OPERATION_RUNBOOK.md
EMPLOYEE5_OPERATION_RUNBOOK.md
EMPLOYEE6_OPERATION_RUNBOOK.md
```

禁止乱建 `FINAL`、`V2`、`TEMP`、`PATCH`、`NOTE`、`CHANGE_LOG`、`DOCUMENT_MAP` 等散文件。长期规则必须写回一级总文档或对应员工 runbook，不能只在聊天里执行不入档。

## 员工 Python 文件硬规则

硬性规则：任何已落地员工的新增功能、评分、报告、归因、审计、修复或增强逻辑，默认必须合并进该员工已有主脚本或已有报告脚本。

不允许为了单个功能单独新建新的 `.py` 文件。

例如：五号员工的归因命中分必须合并进既有五号脚本，不能单独新建 `employee5_reason_score.py`、`employee5_score.py`、`employee5_patch.py` 等散文件。

只有用户明确单独批准，并同步更新 `EMPLOYEE_SYSTEM_ROLES.md`、对应员工 runbook、workflow 和验收说明后，才允许新增员工级 Python 文件。否则，新增散 PY 一律视为文件治理违规和猴子代码风险，必须立即删除并合并回既有脚本。

## 工程验收规则

不能只在聊天里说已经改好。必须以 GitHub 实际文件、commit sha、复查结果和运行证据为准。

回复用户时必须区分：

- 已提交：GitHub 已返回 commit sha。
- 已复查：重新读取 GitHub 文件，确认内容真实存在。
- 已验证：workflow、artifact、日志或 Telegram 消息产生预期结果。

如果工兛报错、被拦截、冲筁或没有返回 commit sha，必须明确说明没有落地，不能假装成功。

## 禁止事项

- 禁止先搜私有仓库。
- 禁止把私有仓库结果覆盖 public 仓库结果。
- 禁止没读 runbook 就改 workflow。
- 禁止只执行用户要求、不记录用户要求。
- 禁止为员工单个功能、补丁、评分或报告增强单独新建 `.py` 文件；必须合并进既有员工脚本，除非用户明确批准。
- 禁止没有 commit sha 却说已提交。
- 禁止没有复查文件却说已复查。
- 禁止没有 workflow、artifact、日志或消息证据却说已验证。

## 一句话原则

整个员工系统只有一个默认事实源：`driveaway1207/stock-alert-public-runner`。

仓库文件必须干净、集中、分层；不要乱建一堆文件，也不要为单个员工功能单独新建散乱 Python 文件。
