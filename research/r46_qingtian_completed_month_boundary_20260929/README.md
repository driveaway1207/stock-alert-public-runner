# R46 擎天完成月K边界审计

本目录只保存研究审计，不包含任何生产模型修改。

## 结论

`擎天.py`第294—313行以自然月末判断“完成月”；`employee3_runner.py`第1274行固定删除聚合月K最后一行。对2025年1—5月五个已完成交易月边界进行固定计算：

- 擎天误删2/5：2025-01-27、2025-05-30。
- 三号核心线固定排除5/5。
- 两路径在3/5日期上口径不一致。
- 断言8/8通过。

这是计算口径/确认时效证据，不是连板优势证据。股票结果、Top1/Top5变更和可成交收益均未运行，因此源码保持不变。

## 文件

- `audit_r46_qingtian_month_boundary.js`：可复现实验脚本（Node.js）
- `fixed_config.json`：冻结参数与验收/回退门
- `boundary_samples.csv`：5个边界日期清单
- `result.json`：完整机器可读结果
- `receipt.md`：中文回执
- `six_indicator_decision.md`：六指标决策表
- `continuity_append.md`：待追加至稳定continuity.md的同内容条目

## 复现

```bash
node research/r46_qingtian_completed_month_boundary_20260929/audit_r46_qingtian_month_boundary.js
```

官方交易日依据：

- [上交所2025年春节休市公告](https://www.sse.com.cn/disclosure/announcement/general/c/c_20250123_10770570.shtml)
- [上交所2025年劳动节休市公告](https://www.sse.com.cn/disclosure/announcement/general/c/c_20250424_10777645.shtml)
- [上交所2025年部分节假日休市通知](https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml)
