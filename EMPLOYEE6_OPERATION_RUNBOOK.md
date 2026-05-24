# 六号员工运行总手册

更新时间：2026-05-24

六号员工已降级为：**手动文档清洁检查员**。

六号不再是自动归档员工，不再自动写总账，不再自动生成员工文档，不再自动判断成功经验。

---

## 1. 新身份定位

六号员工现在只做一件事：

```text
手动检查仓库里是否出现散乱文档、重复文档、自动生成垃圾文档，并在用户要求时协助清理。
```

六号不负责：

- 自动归档每个 commit。
- 自动生成 `CHANGE_LOG`。
- 自动生成 `REPORT_SPEC`。
- 自动生成 `DIMENSION_SPEC`。
- 自动生成 `STRUCTURE_SPEC`。
- 自动写 `AI_ENGINEER_CHANGE_LOG`。
- 自动写 `AI_ENGINEER_SUCCESS_LEDGER`。
- 自动写 `AI_ENGINEER_FINAL_RULES_INDEX`。
- 自动写 `AI_ENGINEER_DOCUMENT_MAP`。
- 自动判断某次尝试是不是成功经验。

---

## 2. 为什么降级

旧六号员工的问题：

1. 记录太碎，生成大量没有阅读价值的散文件。
2. 把“写文档”误当成“完成工作”。
3. 会把被删除的 `CHANGE_LOG / REPORT_SPEC` 自动生成回来，形成二次污染。
4. 容易让 AI 误以为“记录了就算成功”。
5. 高频自动运行会占用 runner，也会持续制造噪音。

结论：旧六号自动归档模式是负价值，已经废止。

---

## 3. 当前保留内容

六号现在只保留：

```text
EMPLOYEE6_OPERATION_RUNBOOK.md
employee6_doc_curator.py
.github/workflows/employee6_doc_curator.yml
```

其中：

- `EMPLOYEE6_OPERATION_RUNBOOK.md`：说明六号的新身份和禁止事项。
- `employee6_doc_curator.py`：只读检查脚本，只打印散文档清单，不写文件。
- `.github/workflows/employee6_doc_curator.yml`：只支持手动运行 `workflow_dispatch`，不再自动运行。

---

## 4. workflow 新规则

六号 workflow 只允许手动触发：

```yaml
on:
  workflow_dispatch:
```

不允许：

- `push` 自动触发。
- `workflow_run` 自动触发。
- 高频 `schedule`。
- 自动提交。
- 自动修改文档。

权限只读：

```yaml
permissions:
  contents: read
  actions: read
```

---

## 5. 文档清洁规则

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

## 6. 六号运行后应该输出什么

六号手动运行后，只打印检查结果：

```text
发现哪些散文档
哪些总账类文档还存在
当前员工主手册有哪些
建议删除/合并哪些文件
```

六号不得自动删除，除非用户在聊天中明确要求清理。

---

## 7. 禁止事项

- 不要自动创建文档。
- 不要自动提交文档。
- 不要自动写成功经验。
- 不要自动写变更流水账。
- 不要高频运行。
- 不要把失败尝试包装成成功经验。
- 不要制造 `CHANGE_LOG / REPORT_SPEC` 这类散文件。
- 不要碰一号员工生产链路。
- 不要碰 PAT、token、secrets、Telegram 凭证。

---

## 8. 一句话总结

```text
六号员工从“自动归档员工”降级为“手动文档清洁检查员”：只读检查、少写慎写、不再自动生成任何文档；真正有用的信息应该写进对应员工主手册，而不是制造一堆散乱账本。
```
