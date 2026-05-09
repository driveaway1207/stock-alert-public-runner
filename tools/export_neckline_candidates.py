# -*- coding: utf-8 -*-
"""
全A股“最高收盘共振线/平台颈线候选”导出脚本
只做结构线识别验证，不做买卖，不推送Telegram。
"""

import os
import time
import traceback
from datetime import datetime

import pandas as pd
import akshare as ak


OUT_DIR = "outputs"
os.makedirs(OUT_DIR, exist_ok=True)

LOOKBACK_LIST = [120, 250]

TOUCH_TOL = 0.02
MIN_TOUCH = 2
MIN_BARS = 80
EXCLUDE_RECENT_IPO_BARS = 60
MAX_OUTPUT_PER_STOCK = 2


def fetch_stock_list():
    df = ak.stock_info_a_code_name()
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df[["code", "name"]]


def fetch_daily(code: str):

    df = ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        adjust="qfq"
    )

    if df is None or df.empty:
        return None

    df = df.rename(columns={
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "涨跌幅": "pct_chg",
    })

    df["date"] = pd.to_datetime(df["date"])

    for col in ["open", "close", "high", "low", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["open", "close", "high", "low", "volume"])

    df = df.sort_values("date").reset_index(drop=True)

    return df


def find_highest_close_line(df, lookback):

    if len(df) < max(MIN_BARS, lookback // 2):
        return None

    recent = df.tail(lookback).copy()

    if len(recent) < 60:
        return None

    # 排除上市初期
    recent = recent.iloc[EXCLUDE_RECENT_IPO_BARS:]

    if recent.empty:
        return None

    idx = recent["close"].idxmax()

    line_price = float(df.loc[idx, "close"])

    line_date = df.loc[idx, "date"]

    # 过滤最近孤立点
    if idx > len(df) - 5:
        return None

    window = df.tail(lookback).copy()

    near = window[
        (window["close"] >= line_price * (1 - TOUCH_TOL)) &
        (window["close"] <= line_price * (1 + TOUCH_TOL))
    ]

    touch_count = len(near)

    pressure = window[
        (window["high"] >= line_price * (1 - TOUCH_TOL)) &
        (window["close"] <= line_price * (1 + TOUCH_TOL))
    ]

    pressure_count = len(pressure)

    if touch_count < MIN_TOUCH and pressure_count < MIN_TOUCH:
        return None

    last = df.iloc[-1]

    is_above = last["close"] > line_price

    breakout = None

    after_line = df[df.index > idx].copy()

    for j, r in after_line.iterrows():

        body_high = max(r["open"], r["close"])

        body_low = min(r["open"], r["close"])

        body_stand = (
            r["close"] > line_price and
            body_low > line_price
        )

        gap_stand = r["low"] > line_price

        if body_stand or gap_stand:

            breakout = (
                j,
                r,
                "跳空站上" if gap_stand else "实体站上"
            )

            break

    if breakout is None:

        breakout_date = ""

        breakout_type = ""

        fell_back_3 = ""

        fell_back_5 = ""

        status = "接近/未突破"

    else:

        bidx, brow, breakout_type = breakout

        breakout_date = brow["date"].strftime("%Y-%m-%d")

        next3 = df.iloc[bidx + 1:bidx + 4]

        next5 = df.iloc[bidx + 1:bidx + 6]

        fell_back_3 = (
            "是"
            if (
                not next3.empty and
                (next3["close"] < line_price).any()
            )
            else "否"
        )

        fell_back_5 = (
            "是"
            if (
                not next5.empty and
                (next5["close"] < line_price).any()
            )
            else "否"
        )

        status = "已突破"

    score = 0

    score += min(touch_count, 5) * 10

    score += min(pressure_count, 5) * 8

    if is_above:
        score += 15

    if breakout_type:
        score += 20

    if fell_back_3 == "否":
        score += 10

    score = min(score, 100)

    return {
        "候选线价格": round(line_price, 2),
        "候选线类型": f"{lookback}日最高收盘共振线",
        "线来源日期": line_date.strftime("%Y-%m-%d"),
        "线来源K线最高价": round(float(df.loc[idx, "high"]), 2),
        "线来源K线收盘价": round(float(df.loc[idx, "close"]), 2),
        "线附近触碰次数": int(touch_count),
        "线附近受压次数": int(pressure_count),
        "当前收盘是否站上": "是" if is_above else "否",
        "突破日期": breakout_date,
        "突破方式": breakout_type,
        "突破后3日是否跌回": fell_back_3,
        "突破后5日是否跌回": fell_back_5,
        "结构状态": status,
        "模型可信度": round(score / 100, 2),
        "模型理由": f"{lookback}日最高收盘价，附近触碰{touch_count}次，受压{pressure_count}次，状态={status}",
    }


def main():

    today = datetime.now().strftime("%Y-%m-%d")

    out_path = os.path.join(
        OUT_DIR,
        f"structure_line_candidates_{today}.csv"
    )

    stocks = fetch_stock_list()

    rows = []

    success = 0

    failed = 0

    total = len(stocks)

    print(f"[START] stocks={total}")

    for i, row in stocks.iterrows():

        code = row["code"]

        name = row["name"]

        try:

            df = fetch_daily(code)

            if df is None or len(df) < MIN_BARS:
                failed += 1
                continue

            candidates = []

            for lb in LOOKBACK_LIST:

                item = find_highest_close_line(df, lb)

                if item:

                    item.update({
                        "股票代码": code,
                        "股票名称": name,
                        "最新日期": df.iloc[-1]["date"].strftime("%Y-%m-%d"),
                        "最新收盘": round(float(df.iloc[-1]["close"]), 2),
                        "人工评价": "",
                        "错误类型": "",
                        "备注": "",
                    })

                    candidates.append(item)

            candidates = sorted(
                candidates,
                key=lambda x: x["模型可信度"],
                reverse=True
            )

            rows.extend(candidates[:MAX_OUTPUT_PER_STOCK])

            success += 1

        except Exception as e:

            failed += 1

            print(f"[ERROR] {code} {name}: {e}")

            traceback.print_exc(limit=1)

        if (i + 1) % 100 == 0:

            print(
                f"[PROGRESS] "
                f"{i+1}/{total}, "
                f"success={success}, "
                f"failed={failed}, "
                f"candidates={len(rows)}"
            )

        time.sleep(0.05)

    out = pd.DataFrame(rows)

    if not out.empty:

        cols = [
            "股票代码",
            "股票名称",
            "最新日期",
            "最新收盘",
            "候选线价格",
            "候选线类型",
            "线来源日期",
            "线来源K线最高价",
            "线来源K线收盘价",
            "线附近触碰次数",
            "线附近受压次数",
            "当前收盘是否站上",
            "突破日期",
            "突破方式",
            "突破后3日是否跌回",
            "突破后5日是否跌回",
            "结构状态",
            "模型可信度",
            "模型理由",
            "人工评价",
            "错误类型",
            "备注"
        ]

        out = out[cols]

        out.to_csv(
            out_path,
            index=False,
            encoding="utf-8-sig"
        )

    print(
        f"[DONE] success={success}, "
        f"failed={failed}, "
        f"candidates={len(rows)}"
    )

    print(f"[CSV] {out_path}")


if __name__ == "__main__":
    main()
