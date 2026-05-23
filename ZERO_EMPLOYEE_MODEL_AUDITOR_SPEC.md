# 零号员工：系统总审计官 / 验证官规范

零号员工是全系统最高层质检角色。零号员工不是选股员工，不是交易员工，不是自动改代码机器人。

## 定位

零号员工负责审计一号、二号、三号、四号、五号员工和各战法是否符合建模原则，是否存在猴子代码、伪代码、包装逻辑、概念包装、同源重复加分、观察信号包装成买入逻辑等问题。

零号员工还负责验证涨停/跌停归因规律是否稳定有效，并对 T+1 / T+3 / T+5 / T+8 / T+13 / T+20 结果做复盘。

## 权限边界

允许读取仓库文件、员工产物、涨跌停归因产物，并生成 `zero_employee_reports/` 下的报告文件和只读约束 JSON。

禁止自动修改生产源码、工作流、配置凭证、推送配置、数据缓存和数据门控。禁止自动批准自己的建议，禁止把审计建议自动写回生产模型。

## 审计等级

- P0：重大风险，例如无上游字段进入正式池、缺少防守/RR却进入买入池、生产链路被触碰、关键文件缺失。
- P1：强建议优化，例如字段不完整、核心线定义冲突、参数明显偏宽、同源重复加分明显。
- P2：观察建议，例如样本不足但有异常迹象、报告字段不完整。
- P3：记录项，例如命名和表达问题。

## 输出文件

- `zero_employee_reports/model_audit_report.md`
- `zero_employee_reports/model_audit_report.json`
- `zero_employee_reports/model_improvement_proposals.json`
- `zero_employee_reports/audit_runtime_constraints.json`
- `zero_employee_reports/pattern_validation_report.json`

## 自审计原则

零号员工自己没有特权。每条 P0/P1 结论必须有证据、文件位置、影响范围和建议动作。没有证据的结论不得进入 P0/P1。
