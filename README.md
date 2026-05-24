# stock-alert-public-runner

## Start here

后续任何 AI 工程师进入仓库，接到用户需求后的第一件事，必须先读文档，再动代码、workflow、报告或员工体系。

先读顺序：

1. `README.md`
2. `AI_ENGINEER_START_HERE.md`
3. `AI_ENGINEER_FINAL_RULES_INDEX.md`
4. `AI_ENGINEER_KLINE_PERIOD_RULES.md`
5. `EMPLOYEE_SYSTEM_ROLES.md`
6. 对应员工的 `EMPLOYEE*_OPERATION_RUNBOOK.md`
7. 对应 workflow 和主脚本

## Highest engineering rule

不能只在聊天里说“已经改好”。任何工程修改必须以 GitHub 实际文件、commit sha、复查结果和运行证据为准。

回复用户时必须区分：

- 已提交：GitHub 已返回 commit sha。
- 已复查：重新读取 GitHub 文件，确认内容真实存在。
- 已验证：workflow 或脚本运行成功，并产生预期输出。

如果工具报错、被拦截、冲突、没有返回 commit sha，必须明确说明没有落地，不能假装成功。

只改文档而没有改真正生效的代码或 workflow，不能说已经修好。

## Global rule

`AI_ENGINEER_KLINE_PERIOD_RULES.md` 是所有员工通用规则，不只属于五号员工。

核心周期口径：

- 5日 / 5根日K ≈ 周线窗口
- 20日 / 20根日K ≈ 月线窗口
- 60日 / 60根日K ≈ 季线窗口
- 250日 / 250根日K ≈ 年线窗口

所有员工写报告和改模型时，都要把这些窗口当作交易周期结构，而不是孤立均线数字。

## Fifth employee

五号员工是涨停样本研究员，不是买入推荐系统。

Key fifth employee files:

- `employee5_runner.py`
- `.github/workflows/fifth_employee.yml`
- `EMPLOYEE5_OPERATION_RUNBOOK.md`

五号员工深度样本最终定下来的规则：

- 先全量识别涨停池。
- 对涨停池内可取得K线的股票计算 20日/月线窗口涨幅。
- 按 20日/月线窗口涨幅从高到低取前三名。
- 再对这3只做30+战法/华尔街维度归因。

所有已落地并未被用户拒绝的成功经验，都必须同步记录进 `AI_ENGINEER_START_HERE.md` 和 `EMPLOYEE5_OPERATION_RUNBOOK.md`。