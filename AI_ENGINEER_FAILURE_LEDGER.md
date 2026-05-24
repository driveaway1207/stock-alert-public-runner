# AI 工程师失败教训账

失败不是丢脸，失败不记录才危险。本文件记录所有没有落地、半落地、被工具拦截、产生副作用或后续被用户否定的工程尝试。

## 1. 记录原则

以下情况必须记录：

- 工具报错或安全检查拦截。
- 409 冲突或没有返回 commit sha。
- 只改了文档，没有改真正生效的代码或 workflow。
- 口头说已经修好，但复查发现没有落地。
- workflow 配置导致重复触发、递归触发、高频抢 runner。
- 成功经验被证明错误。
- 用户明确否定某个方案。

## 2. 标准格式

```text
## YYYY-MM-DD｜失败事项

- 失败动作：
- 失败原因：
- 是否已替代解决：
- 禁止误判：
- 后续处理：
```

## 3. 已记录失败教训

## 2026-05-24｜六号员工高频补扫问题没有第一时间真正落地

- 失败动作：曾口头说明六号员工每 5 分钟补扫问题已经优化，但后续复查 `.github/workflows/employee6_doc_curator.yml` 时发现实际文件仍保留 `*/5 * * * *`。
- 失败原因：当时部分修改只落到了文档或尝试修改 workflow 被工具拦截，没有完成最终文件复查。
- 是否已替代解决：已将 workflow 改成 daily 低频兜底，并复查实际文件。
- 禁止误判：以后不能把“尝试修改”当成“已经落地”。
- 后续处理：所有工程修改必须记录 commit sha、复查文件、运行验证状态。

## 2026-05-24｜尝试新建最高规则文件被拦截

- 失败动作：尝试新建 `AI_ENGINEER_IMPLEMENTATION_VERIFICATION_RULES.md`。
- 失败原因：GitHub 工具安全检查拦截，没有返回 commit sha。
- 是否已替代解决：已先把核心规则写入 `README.md`，并新增 `ENGINEERING_MAINTENANCE_MANUAL.md` 作为工程维护手册。
- 禁止误判：该独立文件目前未落地，不能让后续工程师以为它存在。
- 后续处理：若未来仍需单独文件，必须重新创建并完成复查。

## 2026-05-24｜尝试更新 START_HERE 与 FINAL_RULES_INDEX 被拦截

- 失败动作：尝试同步更新 `AI_ENGINEER_START_HERE.md` 和 `AI_ENGINEER_FINAL_RULES_INDEX.md` 的最高落地核验规则。
- 失败原因：GitHub 工具安全检查拦截，没有返回 commit sha。
- 是否已替代解决：已先把规则写入 `README.md` 和 `ENGINEERING_MAINTENANCE_MANUAL.md`。
- 禁止误判：这两个文件对应更新当时未落地，后续需要复查后再补。
- 后续处理：能更新时再补充，并记录 commit sha 和复查结果。
