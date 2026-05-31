# -*- coding: utf-8 -*-
"""
Tushare 前复权口径单点测试脚本。
只用于验证 TUSHARE_TOKEN 和 301360.SZ 在 2024-10-08 的前复权数据是否接近主流看盘软件口径。
不写入缓存，不修改五号员工主逻辑。
"""
from __future__ import annotations

import os
import sys

EXPECTED = {
    "ts_code": "301360.SZ",
    "trade_date": "20241008",
    "high": 51.25,
    "close": 49.38,
}
TOL_PCT = float(os.getenv("TUSHARE_QFQ_TEST_TOL_PCT", "0.01"))  # 默认 1%


def pct_diff(actual: float, expected: float) -> float:
    if expected == 0:
        return 0.0 if actual == 0 else 999.0
    return abs(actual - expected) / abs(expected)


def main() -> int:
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        print("ERROR: 缺少环境变量 TUSHARE_TOKEN。请先在 GitHub Secrets 里添加。")
        return 2

    try:
        import tushare as ts
    except Exception as exc:
        print(f"ERROR: 未安装 tushare 或导入失败：{exc}")
        return 2

    ts.set_token(token)

    try:
        df = ts.pro_bar(
            ts_code=EXPECTED["ts_code"],
            start_date=EXPECTED["trade_date"],
            end_date=EXPECTED["trade_date"],
            adj="qfq",
            freq="D",
        )
    except Exception as exc:
        print(f"ERROR: Tushare 拉取失败：{exc}")
        return 2

    if df is None or df.empty:
        print("ERROR: Tushare 返回空数据。可能是权限不足、积分不足、接口限制或代码/日期错误。")
        return 2

    row = df.iloc[0]
    actual_high = float(row["high"])
    actual_close = float(row["close"])
    actual_open = float(row["open"])
    actual_low = float(row["low"])

    high_diff = pct_diff(actual_high, EXPECTED["high"])
    close_diff = pct_diff(actual_close, EXPECTED["close"])

    print("Tushare 前复权测试：")
    print(f"股票：{EXPECTED['ts_code']}  日期：{EXPECTED['trade_date']}")
    print(f"Tushare：open={actual_open:.4f} high={actual_high:.4f} low={actual_low:.4f} close={actual_close:.4f}")
    print(f"参考值：high={EXPECTED['high']:.4f} close={EXPECTED['close']:.4f}")
    print(f"偏差：high={high_diff:.2%} close={close_diff:.2%}  容忍={TOL_PCT:.2%}")

    if high_diff <= TOL_PCT and close_diff <= TOL_PCT:
        print("PASS: Tushare 前复权口径接近看盘软件，可继续小范围接入五号员工核心线。")
        return 0

    print("FAIL: Tushare 前复权口径与看盘软件不一致，不能用于五号员工核心线。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
