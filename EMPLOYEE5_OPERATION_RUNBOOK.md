# 五号员工运行与建设总手册

更新时间：2026-05-24

后续任何 AI 员工处理五号员工，必须先读本文件，再改代码、运行链路、报告或文档。五号员工相关说明统一合并到本文件；五号只保留本手册，避免文档四处分散。

---

## 0. 用户每次对五号员工的硬性要求

### 0.1 按需运行，不是定时等待

用户说“跑五号”，AI 必须立即触发五号员工运行。用户要的是按需触发，不是每几分钟自动跑，也不是让用户手动点页面。

难点：很多看起来像触发的动作，只是文件更新或 Issue 更新，并不等于五号已经运行。不能把定时运行当成按需运行。

正确方法：只走 public runner 固定入口。

```text
仓库：driveaway1207/stock-alert-public-runner
固定 Issue：#2
固定标签：run-employee5
workflow：.github/workflows/fifth_employee.yml
运行名：五号员工
```

首选方式：先移除 #2 的 `run-employee5`，再重新添加。若工具不能操作标签，就在 #2 下评论：`run-employee5`。

验收：看到 Actions 运行名、报告产物或 Telegram 报告之一，才算真正跑起来。

### 0.2 不能走错仓库和错 Issue

五号按需触发只认 `stock-alert-public-runner` 的 Issue #2。不要再操作旧主仓库 `stock-alert` 的 #5/#6/#7，不要用高频定时冒充按需触发。

### 0.3 没跑通前不能写成功经验

必须区分：

```text
已落地：代码、文档或评论真实写入
已触发：Actions 出现五号员工运行
已运行：run 完成
已产出：报告产物或 Telegram 报告出现
```

只有“已产出”才能写成功经验。

### 0.4 五号不是荐股员工

五号只做涨停样本研究，不做买入推荐。报告只写研究样本、归因观察、待验证规律，不写直接买入建议，不自动改一号/二号/三号生产模型。

### 0.5 涨停池必须尽量全量，尤其北交所

覆盖主板、中小板、创业板、科创板、北交所、ST 和非 ST。涨停阈值原则：ST 约 5%，主板/中小板约 10%，创业板/科创板约 20%，北交所约 30%。报告必须输出涨停总数、板块分布、10cm/20cm/30cm 分布、北交所数量、数据来源和失败数量。

### 0.6 普通涨停只做统计，不堆股票名单

Telegram 主报告只做大类统计，完整名单放报告产物。不要把 100 多只股票塞满主报告。

### 0.7 每天只选 3 只重点深度样本

先识别涨停池，对可取得历史 K 线的股票计算 20 日/月线窗口涨幅，取前三名做深度归因。若深度样本为空，必须说明原因，不能伪造。

### 0.8 每只深度样本至少 30 个独立维度

不能把同一件事拆成 30 句凑数，不能写“走势较强、资金关注、形态不错”等空话。维度要跨大周期、压力带、筹码、形态、趋势、洗盘、波动压缩、量能、试盘、供应吸收、当前触发、K线质量、位置过热、板块、风险、数据质量等。

### 0.9 必须从大周期历史路径开始

五号研究的是周期性历史路径，不是当天涨停故事。应从 250 日、100 日、60 日、20 日窗口往当前触发逐层分析。

### 0.10 每次运行和修改都要留证据

每次都填写本文档第 10 节建设档案，记录修改文件、commit、触发方式、评论 ID 或标签动作、运行名、报告产物、Telegram 是否收到、报告质量检查。

---

## 1. 五号员工身份

五号员工不是买入推荐员工，也不是明天追涨停的交易员工。

五号员工是：涨停样本研究员、极强样本归因研究员、强势结构考古员、经验归纳员、给一号/二号/三号/零号提供验证素材的数据员工。

五号输出的是研究样本，不是直接买入指令。

---

## 2. 当前代码和运行入口

```text
主脚本：employee5_runner.py
结构报告脚本：employee5_structural_report.py
workflow：.github/workflows/fifth_employee.yml
报告目录：employee5_reports/
固定触发 Issue：#2
固定触发关键词：run-employee5
```

依赖必须包含：

```bash
pip install baostock akshare pandas requests
```

---

## 3. 报告结构与 Telegram 规范

五号报告分两层。

第一层：全市场涨停大类统计，包括日期、耗时、涨停总数、板块分布、涨停制度分布、北交所数量、K线来源统计、失败数量、大类标签统计。

第二层：3 只周期性大涨深度样本。每只包括名称代码、板块、K线来源、5/20/60/100/250 日涨幅、30+ 跨维度归因。

Telegram 正文必须写给用户看，重点回答：今日3只样本共同体现什么结构规律；每只样本属于什么大级别结构原型；启动前有哪些可量化端倪；哪些维度在多只样本中共振；哪些共性值得转化为一号员工候选优化因子。

后台诊断字段，例如接口失败明细、K线拉取尝试数、source_count、工程进度、workflow 说明，放到 JSON、artifact 或日志，不塞进 Telegram 正文。

---

## 4. 深度维度白名单

每只深度样本至少 D01-D30，维度必须可统计、可复盘、可转化为后续候选因子。

不可计入维度：纯解释口径、数据源口径、运行口径、空泛判断、与共性总结无关的描述。

维度池：

1. 20日/月线窗口涨幅强度。
2. 60日/季线窗口加速。
3. 100日中期修复路径。
4. 250日/年线位置。
5. 重大高点后消化周期。
6. 20/60日平台压缩。
7. 凹口/左峰突破。
8. 破底翻修复。
9. 台阶抬升。
10. 二阶画线低点斜率。
11. BOLL缩口。
12. BBI/BOLL中轨修复。
13. 压缩后扩张。
14. 多周期核心压力线共振。
15. 实体站稳核心压力线。
16. 影线/实体反应共振。
17. VBP筹码密集压力带突破。
18. 历史最大量阳K高点/实底。
19. 首次标准倍量锚点。
20. 二次倍量突破100%位。
21. 150%/200%扩展路径。
22. 高扩展回落后的100%回抽风险。
23. 健康放量。
24. 倍量后平量承接。
25. 平台量能稳定。
26. 台阶平台均量抬升。
27. 阳量压阴量。
28. 假突破/Liquidity Sweep 记忆。
29. 压力区多次攻击吸收。
30. 上影试盘后真突破。
31. 涨停K实体质量。
32. 跳空/光头光脚强攻。
33. 假阴真阳质量。
34. MA5金叉MA10倍量启动。
35. 100日攻击记忆。
36. 板块/题材强度。
37. Event/Context/Confirmation。
38. RR/防守位/空间。
39. 过热/失真过滤。
40. 数据有效性过滤。

三只样本汇总时，应输出共振维度排行：维度名称、命中样本数、加权命中分、典型证据。

---

## 5. 大级别结构原型库

五号深度归因必须先识别结构原型，再拆成维度。不能先堆指标，再硬凑结构。看不出结构时，必须标记“未识别明确结构形态”。

每只样本应输出：主结构原型、辅助结构原型、大级别周期、核心结构线、启动前端倪、触发确认、反证。

结构原型库：

1. AR01 大级别头肩顶供给释放后再启动。
2. AR02 大级别平台跌破后破底翻。
3. AR03 历史最大量阳K高点/实底再突破。
4. AR04 远期最大量高点 + 更高试盘高点 + 长时间消化 + 二次确认。
5. AR05 大级别圆弧底/碗形底后颈线突破。
6. AR06 双底/W底后右侧突破。
7. AR07 长周期箱体吸收后一次性打穿上沿。
8. AR08 台阶式平台推进。
9. AR09 核心压力线多次试盘后真突破。
10. AR10 凹口攻击 + 再平台 + 最终突破。
11. AR11 BOLL/BBI大周期缩口修复后爆发。
12. AR12 长期下跌后量能枯竭 + 量能恢复。
13. AR13 强势头肩顶失败后的再突破。
14. AR14 多周期结构共振爆发。

结构原型字段模板：

```text
archetype_id：结构编号
archetype_name：结构名称
cycle_level：日/周/月/季/年
formation_bars：结构形成K线数量
left_structure：左侧结构
breakdown_or_retest：跌破/回抽/试盘过程
bottom_or_absorption：底部磨底/吸收过程
pre_launch_clues：启动前端倪
trigger_event：触发事件
volume_signature：量能签名
core_level：核心结构线
invalid_evidence：反证/不成立条件
convertible_factors：可转化为一号员工的因子
```

---

## 6. 自动触发机制

五号支持定时触发、GitHub Actions 手动触发、固定 Issue 按需触发。用户最关心的是固定 Issue 按需触发。

固定 Issue 按需触发信息：

```text
仓库：driveaway1207/stock-alert-public-runner
固定 Issue：#2
固定标签：run-employee5
```

首选方式是标签触发：移除再添加 `run-employee5`。如果标签操作不可用，则在 #2 下新增评论 `run-employee5`。同一轮只使用一种触发方式，不要标签和评论同时触发。

---

## 7. 成功运行标准

一次合格运行必须满足：

1. Actions 页面显示名称为 `五号员工`。
2. 只有一个最终成功 run。
3. Telegram 收到五号报告，或 artifact 出现完整报告。
4. 报告包含涨停总数、板块分布、北交所统计、3 只深度样本、30+ 维度归因和 K 线来源统计。
5. 如果深度样本为空，报告必须说明原因，不能伪造。

---

## 8. 数据源原则

涨停池和快照统计主要使用 AKShare 组合。历史 K 线优先使用 BaoStock / Bostock，AKShare 辅助，东方财富接口最后兜底。北交所必须保留 AKShare / 东方财富兜底。

筹码核心带、量能分析优先使用成交量或换手率，不优先使用成交额。

---

## 9. 常见问题与解决方法

### 9.1 涨停数量偏少

优先检查北交所是否纳入，检查 10cm/20cm/30cm 判断是否正确，检查是否只依赖单一接口。

### 9.2 深度样本为空

优先检查历史 K 线接口和兜底链路。不能因为前面一批样本失败就输出 0 只深度样本。

### 9.3 日志满屏英文

应收敛为中文摘要，只保留失败数量和少量样例，详细失败列表放报告产物。

### 9.4 Telegram 报告过长

主报告分段发送，普通涨停只做统计，完整名单放 JSON 报告。

### 9.5 触发后出现多个五号

同一轮只使用一种触发方式，不要标签和评论同时触发。保留单实例运行约束。

---

## 10. 建设档案：后续 AI 员工必须填写

### 10.1 修改记录

```text
日期：
执行者：
用户原始要求：
修改文件：
commit sha：
是否改 workflow：是/否
是否改一号生产链路：必须为否
```

### 10.2 触发记录

```text
是否触发五号：是/否
触发方式：标签 / 评论 / 手动 / 定时
固定 Issue：#2
评论 ID 或标签动作：
Actions run 名称：
Actions run ID：
artifact 名称：
Telegram 是否收到：是/否
```

### 10.3 报告质量

```text
涨停总数：
北交所数量：
10cm / 20cm / 30cm 数量：
深度样本数量：
每只样本维度数：
K线来源统计：
失败样本数量：
是否有英文刷屏：是/否
是否被截断：是/否
```

### 10.4 新增经验

```text
新发现结构：
新发现风险：
是否需要零号员工 T+验证：是/否
建议验证窗口：T+1 / T+3 / T+5 / T+8 / T+13 / T+20
是否允许进入其他员工模型：默认否，需人工复核
```

### 10.5 踩坑复盘

```text
踩坑点：
原因：
避免方法：
是否已写回本文档：是/否
```

---

## 11. 后续工程建议

建议未来拆成：

```text
employee5_runner.py       只负责涨停归因和生成报告
telegram_sender.py        固定不动，只负责读取报告并推送
fifth_employee.yml        固定不动，只负责运行链路
```

五号不应只生成日报，还应逐步沉淀样本库，供零号员工做 T+1/T+3/T+5/T+8/T+13/T+20 验证。

---

## 12. 禁止事项

禁止：做成直接买入推荐系统、用空数据伪造深度分析、漏掉北交所、混淆 10cm/20cm/30cm、把普通股票名单塞满 Telegram、刷屏英文底层错误、同时触发多个五号、随便新建临时 Issue 触发、自动把未验证规律写入其他员工生产模型。

---

## 13. 一句话总结

五号员工的正确定位是：每天全量统计涨停样本，按 20 日/月线窗口涨幅挑选 3 只周期性大涨的强势样本，用多数据源 K 线链路做 30+ 跨维度历史路径归因，把“什么样的股票容易涨停”沉淀成可验证的经验库，服务一号、二号、三号和零号员工，而不是让用户第二天追涨。

---

## 自动归档记录

本区由六号员工追加，记录该员工相关代码、文档、workflow、报告规范、运行链路改动。

<!-- employee6-employee-5:de977f3c62fd -->
### 2026-05-24 10:24:22 UTC｜Commit `de977f3c62fd`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Fix employee5 runbook retained-docs wording
- 自动归类：{"docs": 1}
- 归档判断：文档/操作手册/成功经验更新。
- 修改路径：
  - `EMPLOYEE5_OPERATION_RUNBOOK.md`

<!-- employee6-employee-5:4f386a9cfeed -->
### 2026-05-24 10:24:22 UTC｜Commit `4f386a9cfeed`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Remove merged employee5 change log
- 自动归类：{"docs": 1}
- 归档判断：员工代码或文档常规更新。
- 修改路径：
  - `EMPLOYEE5_CHANGE_LOG.md`

<!-- employee6-employee-5:751105943ee8 -->
### 2026-05-24 10:24:22 UTC｜Commit `751105943ee8`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Remove merged employee5 structure spec
- 自动归类：{"docs": 1}
- 归档判断：员工代码或文档常规更新。
- 修改路径：
  - `EMPLOYEE5_STRUCTURE_ARCHETYPE_SPEC.md`

<!-- employee6-employee-5:091d1bbcc3e3 -->
### 2026-05-24 10:24:22 UTC｜Commit `091d1bbcc3e3`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Remove merged employee5 report spec
- 自动归类：{"docs": 1}
- 归档判断：报告输出/格式更新。
- 修改路径：
  - `EMPLOYEE5_REPORT_SPEC.md`

<!-- employee6-employee-5:83aa72e4632c -->
### 2026-05-24 10:24:22 UTC｜Commit `83aa72e4632c`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Remove merged employee5 dimension spec
- 自动归类：{"docs": 1}
- 归档判断：员工代码或文档常规更新。
- 修改路径：
  - `EMPLOYEE5_DIMENSION_SPEC.md`

<!-- employee6-employee-5:bd6d8b388aff -->
### 2026-05-24 10:24:22 UTC｜Commit `bd6d8b388aff`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Consolidate employee5 docs into operation runbook
- 自动归类：{"docs": 1}
- 归档判断：文档/操作手册/成功经验更新。
- 修改路径：
  - `EMPLOYEE5_OPERATION_RUNBOOK.md`

<!-- employee6-employee-5:dea8dfece3eb -->
### 2026-05-24 10:24:22 UTC｜Commit `dea8dfece3eb`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Document employee5 user requirements difficulties and solutions
- 自动归类：{"docs": 1}
- 归档判断：文档/操作手册/成功经验更新。
- 修改路径：
  - `EMPLOYEE5_OPERATION_RUNBOOK.md`

<!-- employee6-employee-5:59cf804a22ec -->
### 2026-05-24 10:24:22 UTC｜Commit `59cf804a22ec`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Add issue comment trigger for employee5 structural report retry
- 自动归类：{"workflow": 1}
- 归档判断：workflow/自动运行链路更新。
- 修改路径：
  - `.github/workflows/fifth_employee.yml`

<!-- employee6-employee-5:02b5be7262b4 -->
### 2026-05-24 10:24:22 UTC｜Commit `02b5be7262b4`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Run employee5 structural archetype report after base data build
- 自动归类：{"workflow": 1}
- 归档判断：workflow/自动运行链路更新。
- 修改路径：
  - `.github/workflows/fifth_employee.yml`

<!-- employee6-employee-5:b99566ef8f32 -->
### 2026-05-24 10:24:22 UTC｜Commit `b99566ef8f32`

- 事件：`workflow_run`｜运行：`66`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Add valid employee5 structural archetype report
- 自动归类：{"code": 1}
- 归档判断：报告输出/格式更新。
- 修改路径：
  - `employee5_structural_report.py`

<!-- employee6-employee-5:4455a82734d3 -->
### 2026-05-24 10:25:03 UTC｜Commit `4455a82734d3`

- 事件：`workflow_run`｜运行：`67`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Delete regenerated employee5 change log after disabling generator
- 自动归类：{"docs": 1}
- 归档判断：员工代码或文档常规更新。
- 修改路径：
  - `EMPLOYEE5_CHANGE_LOG.md`

<!-- employee6-employee-5:86dc7ad88896 -->
### 2026-05-24 10:26:39 UTC｜Commit `86dc7ad88896`

- 事件：`workflow_run`｜运行：`68`
- 触发人：`driveaway1207`｜仓库：`driveaway1207/stock-alert-public-runner`
- commit message：Delete regenerated employee5 report spec after disabling generator
- 自动归类：{"docs": 1}
- 归档判断：报告输出/格式更新。
- 修改路径：
  - `EMPLOYEE5_REPORT_SPEC.md`
