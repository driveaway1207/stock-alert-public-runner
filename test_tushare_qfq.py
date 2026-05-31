import os
import sys
import pandas as pd
import tushare as ts

TOKEN = os.getenv("TUSHARE_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("缺少 TUSHARE_TOKEN")

TS_CODE = os.getenv("TUSHARE_TEST_CODE", "301360.SZ").strip()
START_DATE = os.getenv("TUSHARE_TEST_START_DATE", "20240101").strip()
END_DATE = os.getenv("TUSHARE_TEST_END_DATE", "20260529").strip()
ANCHOR_DATE = os.getenv("TUSHARE_TEST_ANCHOR_DATE", "20241008").strip()
EXPECTED_HIGH = float(os.getenv("TUSHARE_EXPECTED_HIGH", "51.25"))
EXPECTED_CLOSE = float(os.getenv("TUSHARE_EXPECTED_CLOSE", "49.38"))
TOL = float(os.getenv("TUSHARE_TOL", "0.01"))

ts.set_token(TOKEN)

print("Tushare 前复权窗口测试：")
print(f"股票: {TS_CODE}  窗口: {START_DATE}~{END_DATE}  校验日: {ANCHOR_DATE}")
print("说明：前复权必须拉到最新基准日后，再回看历史日期；不能只拉单日。")

try:
    df = ts.pro_bar(ts_code=TS_CODE, start_date=START_DATE, end_date=END_DATE, adj="qfq", freq="D")
except Exception as e:
    raise SystemExit(f"Tushare 拉取失败: {type(e).__name__}: {e}")

if df is None or df.empty:
    raise SystemExit("Tushare 返回空数据")

# Tushare usually returns desc by trade_date; keep stable.
df = df.copy()
df["trade_date"] = df["trade_date"].astype(str)
row = df[df["trade_date"] == ANCHOR_DATE]
if row.empty:
    print(df[["trade_date", "open", "high", "low", "close", "vol"]].head(10).to_string(index=False))
    raise SystemExit(f"未找到校验日 {ANCHOR_DATE}")

r = row.iloc[0]
high = float(r["high"])
close = float(r["close"])
high_diff = abs(high - EXPECTED_HIGH) / EXPECTED_HIGH
close_diff = abs(close - EXPECTED_CLOSE) / EXPECTED_CLOSE

print("校验日数据:")
print(row[["trade_date", "open", "high", "low", "close", "vol"]].to_string(index=False))
print(f"参考值: high={EXPECTED_HIGH:.4f} close={EXPECTED_CLOSE:.4f}")
print(f"偏差: high={high_diff:.2%} close={close_diff:.2%} 容忍={TOL:.2%}")

latest = df.sort_values("trade_date").tail(1)
print("窗口最后一条数据:")
print(latest[["trade_date", "open", "high", "low", "close", "vol"]].to_string(index=False))

if high_diff <= TOL and close_diff <= TOL:
    print("PASS: Tushare 全窗口前复权口径与看盘软件一致，可以继续小范围接五号员工。")
else:
    print("FAIL: Tushare 全窗口前复权口径仍与看盘软件不一致，不能用于五号员工核心线。")
    sys.exit(1)
