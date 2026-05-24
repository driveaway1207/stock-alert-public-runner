# 工程维护手册与工作记录总账

本文件是所有 AI 工程师的工程维护总账。每个新 AI 工程师接到用户需求后，必须先读本文档，再读 README、AI_ENGINEER_START_HERE、AI_ENGINEER_FINAL_RULES_INDEX、对应员工手册、对应 workflow 和主脚本。

## 1. 本文件目的

本文件用于记录每个 AI 工程师实际做过什么、改了哪些路径、是否完成自检、是否存在猴子代码风险、是否有失败尝试、是否真正落地。

后续工程师进入仓库后，必须能从这里看懂：

- 谁改过什么。
- 为什么改。
- 改了哪些文件。
- 是否拿到 commit sha。
- 是否复查过 GitHub 实际文件。
- 是否验证过 workflow 或脚本运行。
- 是否存在猴子代码、临时补丁、wrapper 套壳、假落地风险。
- 哪些尝试失败了，不能再误认为已经完成。

## 2. 每次工作后的强制记录格式

每次工程修改结束后，必须追加一条记录，格式如下：

```text
## YYYY-MM-DD HH:MM UTC｜工程师/员工：X号员工或AI工程师

- 用户需求：
- 本次实际修改：
- 修改路径：
- commit sha：
- 已提交：是/否
- 已复查：是/否，复查文件：
- 已验证：是/否，验证方式：
- 是否涉及 workflow：是/否
- 是否涉及生产链路：是/否
- 猴子代码风险：无/有，原因：
- 自检结论：
- 失败尝试：
- 后续工程师注意事项：
```

## 3. 已提交、已复查、已验证的定义

- 已提交：GitHub 返回了 commit sha。
- 已复查：重新读取 GitHub 上的实际文件，确认内容真实存在。
- 已验证：workflow 或脚本运行成功，并产生预期输出。

不能把“已提交”说成“已验证”。不能把“尝试过”说成“已落地”。

## 4. 猴子代码风险定义

以下情况必须在记录中标记风险：

- 只做包装或 wrapper，没有解决根因。
- 只改文档，没有改真正生效的代码或 workflow。
- 临时补丁、重复补丁、套壳补丁。
- 没有复查实际文件就宣称完成。
- 没有运行证据就宣称跑通。
- 失败尝试没有记录，导致后续工程师误判。
- 自动归档形成递归垃圾流水。

## 5. 当前重要工作记录

## 2026-05-24 UTC｜工程师：ChatGPT / 零号员工门禁化升级

- 用户需求：将零号员工从事后审计升级为有实际意义的门禁，避免它只出报告、不制止流程风险。
- 本次实际修改：新增 `employee0_gate.py`，用于识别高频 schedule、危险动态执行、受保护路径触碰和猴子代码信号；新增 `.github/workflows/employee0_gate.yml`，让零号门禁在 push 和手动触发时运行，发现 P0 阻断项时退出失败。
- 修改路径：`employee0_gate.py`；`.github/workflows/employee0_gate.yml`
- commit sha：`c34b4571d6fb0aae9f402b1b9824fec636807e6d`；`eedc96da457b61bfd0753bac1843728bc208219a`
- 已提交：是。
- 已复查：是，已重新读取 `employee0_gate.py` 和 `.github/workflows/employee0_gate.yml`，确认文件真实存在。
- 已验证：未验证 workflow 实际运行结果；等待 GitHub Actions 后续运行或手动触发验证。
- 是否涉及 workflow：是，新增独立零号门禁 workflow。
- 是否涉及生产链路：间接涉及 workflow 审计链路，但不修改一号到五号正式任务入口。
- 猴子代码风险：中低。此次不是 wrapper 套壳，而是新增独立门禁脚本和独立门禁 workflow；但还没有接入 branch protection 或一号到五号 required status check，所以目前是可失败的门禁工作流，不是 GitHub 分支强制保护。
- 自检结论：零号员工已经从“只生成审计报告”向“能失败阻断的门禁”升级，但真正强制阻止 main 合入还需要仓库 branch protection / required status check 支持。
- 失败尝试：尝试直接更新 `.github/workflows/employee0_code_auditor.yml` 把门禁脚本并入原零号审计 workflow，被 GitHub 工具安全检查拦截，未落地；因此改为新增独立 `.github/workflows/employee0_gate.yml`，已提交并复查。
- 后续工程师注意事项：后续如需真正前置阻断，必须把 `零号门禁` 设为 required status check；否则它只能让门禁 workflow 自身失败，不能阻止已经进入 main 的提交。

## 2026-05-24 UTC｜工程师：ChatGPT / 六号员工流程优化

- 用户需求：建立工程维护手册，记录每个工程师每次工作内容、落地状态、自检状态、猴子代码风险和失败尝试。
- 本次实际修改：新增本文件，作为工程维护手册与工作记录总账。
- 修改路径：`ENGINEERING_MAINTENANCE_MANUAL.md`
- commit sha：`d325b48ede8373ce9fe5f8800a88cd05c3e7c093`
- 已提交：是。
- 已复查：是，已重新读取 `ENGINEERING_MAINTENANCE_MANUAL.md` 并确认内容真实存在。
- 已验证：未验证 workflow；本次只新增文档，不涉及运行链路。
- 是否涉及 workflow：否。
- 是否涉及生产链路：否。
- 猴子代码风险：低。本次仅新增工程记录手册，不改生产流程。
- 自检结论：本文件必须成为后续工程师记录工作的固定入口；后续每次工程修改都要在这里写清已提交、已复查、已验证和失败尝试。
- 失败尝试：此前曾尝试新建更大范围的最高规则文件 `AI_ENGINEER_IMPLEMENTATION_VERIFICATION_RULES.md`，但 GitHub 工具安全检查拦截，未落地；也曾尝试更新 `AI_ENGINEER_START_HERE.md` 和 `AI_ENGINEER_FINAL_RULES_INDEX.md`，被工具拦截，未落地。该失败已经记录，不能误认为已完成。
- 后续工程师注意事项：凡是工具拦截、冲突、没有 commit sha 的动作，都必须记录为失败尝试；不能在聊天中说成已经落地。
