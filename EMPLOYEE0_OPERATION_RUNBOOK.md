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

## 6. 禁止事项

- 不要直接修改生产代码。
- 不要推送 Telegram。
- 不要把审计结果包装成成功经验。
- 不要替六号员工写长期文档账本。
- 不要误伤 PAT、secrets、token。

## 7. 一句话总结

```text
零号员工负责抓代码风险，六号员工负责把所有人的动作和经验记录进档案。
```
