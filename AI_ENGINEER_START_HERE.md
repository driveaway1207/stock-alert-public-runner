# AI 工程师从这里开始

本文件是后续 AI 工程师、Claude Cowork、ChatGPT/Codex 或人工维护者处理员工系统时的第一操作入口，也是员工系统的一级工程纪律档案。

核心原则：仓库要干净，文件不能乱建，关键规则要集中，按一级、二级、三级分层管理。长期有效的用户要求不能只执行不记录，必须写入对应档案。

---

## 1. 唯一默认仓库

当前员工系统唯一默认事实源是：

```text
driveaway1207/stock-alert-public-runner
```

当前已落地员工以本 public 仓库实际文件为准，包括零号员工、一号员工、二号员工、三号员工、四号员工、五号员工、六号员工，以及后续已经在本 public 仓库完成身份定义、runbook、runner 或 workflow 支持的员工。

不允许把尚未有代码、runbook、workflow 或用户明确确认的新编号员工写成“当前已支持员工”。未落地员工只能写为“待建设/待确认”，不能写成事实。

## 2. 私有仓库处理规则

`driveaway1207/stock-alert` 不作为当前员工系统事实源。

除非用户明确点名要求处理私有仓库，否则不要进入私有仓库查找员工当前规则、当前代码、当前 workflow 或当前说明。

禁止把私有仓库旧文件、旧文档、旧 workflow、旧员工分工、旧代码当作当前事实。

## 3. 文件分层与文件清洁硬规则

用户明确要求：文件建立不能乱建，仓库必须保持干净，关键规则要放在一起，按一级、二级、三级分层管理，不允许为了每个想法新建一堆文件。

### 3.1 一级文件：全局入口与总规则

一级文件只放全局、跨员工、最高优先级规则。当前一级文件固定为：

```text
README.md
AI_ENGINEER_START_HERE.md
EMPLOYEE_SYSTEM_ROLES.md
```

除非用户明确批准，不要再新建新的一级总规则文件。

### 3.2 二级文件：单个员工操作手册

二级文件只放具体员工的职责、运行方式、输入输出、workflow、报告、禁止事项和建设档案。

格式应统一为：

```text
EMPLOYEE0_OPERATION_RUNBOOK.md
EMPLOYEE1_OPERATION_RUNBOOK.md
EMPLOYEE2_OPERATION_RUNBOOK.md
EMPLOYEE3_OPERATION_RUNBOOK.md
EMPLOYEE4_OPERATION_RUNBOOK.md
EMPLOYEE5_OPERATION_RUNBOOK.md
EMPLOYEE6_OPERATION_RUNBOOK.md
```

只有当某个新增员工已经有用户明确确认，并且具备身份定义、runbook、runner/workflow 或明确建设计划时，才允许新增对应 `EMPLOYEE*_OPERATION_RUNBOOK.md`。禁止提前把代码不支持、链路不存在的员工写成当前员工。

如果新增或重构某个已确认员工，必须同步更新 `EMPLOYEE_SYSTEM_ROLES.md`、对应员工 runbook，以及实际 runner/workflow。

不能只写代码、不记档案；不能只执行、不记录。

### 3.3 三级文件：代码、workflow、报告产物

三级文件包括 `employee*_runner.py`、`employee*_*.py`、`.github/workflows/*.yml`、`employee*_reports/`。

三级文件只承载实际运行逻辑和产物，不承载全局规则。全局规则必须回写到一级文件；员工专项规则必须回写到对应二级 runbook。

### 3.4 禁止乱建文件

禁止为单个临时想法新建总规则文件；禁止把本应写进 `AI_ENGINEER_START_HERE.md` 的一级规则拆成多个文件；禁止把本应写进员工 runbook 的专项规则散落在多个说明文件；禁止建一堆 `FINAL`、`V2`、`NEW`、`TEMP`、`PATCH`、`NOTE` 类文件；禁止只新增文件不更新入口索引或员工档案；禁止只执行用户要求不把可复用规则记录在案；禁止把尚未落地支持的员工编号写入当前支持范围。

## 4. 用户要求必须入档

用户提出的长期有效工程规则、员工定位、运行方式、禁止事项、文件治理要求，必须记录到仓库档案中。

入档规则：

1. 全局规则写入 `AI_ENGINEER_START_HERE.md`。
2. 员工身份/边界写入 `EMPLOYEE_SYSTEM_ROLES.md`。
3. 某个已存在员工的运行/建设/报告规则写入对应 `EMPLOYEE*_OPERATION_RUNBOOK.md`。
4. 代码实现细节同时写入代码注释或对应 runbook。
5. 用户提到尚未落地的新员工时，不得直接写成当前员工；只能记录为“待确认/待建设”，并明确当前代码不支持。

特别记录：用户明确要求“文件干净、关键文件放一起、分层分级、不要弄一堆文件、执行后必须记录在案”。该规则适用于整个员工系统；六号员工是已落地的自动实时跟踪员和单文件文档清洁员，专门负责把关键事实记录进自己的唯一主手册，不生成散乱文档。

## 5. 强制读取顺序

任何员工相关需求，先按以下顺序读取：

1. `README.md`
2. `AI_ENGINEER_START_HERE.md`
3. `EMPLOYEE_SYSTEM_ROLES.md`
4. 对应员工的 `EMPLOYEE*_OPERATION_RUNBOOK.md`
5. 对应 runner 主脚本
6. 对应 workflow
7. 对应报告目录或 artifact 说明

如果对应员工的 runbook 暂不存在，先读 `EMPLOYEE_SYSTEM_ROLES.md`，再读取实际存在的 runner 和 workflow；同时必须提示该员工档案缺失，不能凭旧仓库或记忆猜测。

## 6. 员工定位总规则

所有员工均以本 public 仓库中的当前文件为准。

当前员工体系的基础分工应从 `EMPLOYEE_SYSTEM_ROLES.md` 读取；每个员工的运行细节应从对应 `EMPLOYEE*_OPERATION_RUNBOOK.md` 读取。

新增员工必须先有明确身份、边界、运行入口、输出产物和用户确认；在这些条件不存在时，不能把该员工写入当前体系，不能在文档中制造“已经支持”的假事实。

## 7. 工程修改验收规则

不能只在聊天里说“已经改好”。任何工程修改必须以 GitHub 实际文件、commit sha、复查结果和运行证据为准。

回复用户时必须区分：已提交、已复查、已验证。

如果工具报错、被拦截、冲突、没有返回 commit sha，必须明确说明没有落地，不能假装成功。

只改文档而没有改真正生效的代码或 workflow，不能说已经修好。

## 8. 禁止事项

- 禁止先搜私有仓库。
- 禁止把私有仓库结果覆盖 public 仓库结果。
- 禁止因为文件名相似就把旧仓库当当前仓库。
- 禁止只看单个员工文档，不看全局入口规则。
- 禁止没读 runbook 就改 workflow。
- 禁止乱建一级规则文件。
- 禁止把关键长期规则散落在多个临时文件。
- 禁止只执行用户要求、不记录用户要求。
- 禁止新增员工但不更新 `EMPLOYEE_SYSTEM_ROLES.md` 和对应 runbook。
- 禁止把尚未落地支持的新员工编号写成当前事实。
- 禁止没有 commit sha 却说已提交。
- 禁止没有复查文件却说已复查。
- 禁止没有 workflow、artifact、日志或消息证据却说已验证。

## 9. 一句话原则

整个员工系统只有一个默认事实源：`driveaway1207/stock-alert-public-runner`。

零号、一号、二号、三号、四号、五号、六号及所有已确认、已落地的后续员工，均从本 public 仓库读取规则、代码、workflow、报告和操作手册；私有仓库默认不作为当前依据。

仓库文件必须干净、集中、分层：一级规则进 `README.md` / `AI_ENGINEER_START_HERE.md` / `EMPLOYEE_SYSTEM_ROLES.md`，二级员工规则进对应 `EMPLOYEE*_OPERATION_RUNBOOK.md`，不要乱建一堆文件，也不要把未落地的员工写成当前支持范围。
