# -*- coding: utf-8 -*-
"""
全A股“最高收盘共振线/平台颈线候选”导出脚本

数据源顺序：
1）BaoStock 主源
2）AkShare 兜底
3）东方财富直连兜底

只做结构线识别验证，不做买卖，不推送Telegram。
"""

import os
import time
import math
import json
import traceback
from datetime import datetime

import requests
import pandas as pd
import baostock as bs
import akshare as ak


OUT_DIR = "outputs"
os.makedirs(OUT_DIR, exist_ok=True)

LOOKBACK_LIST = [120, 250]

TOUCH_TOL = 0.02
MIN_TOUCH = 2
MIN_BARS = 80
EXCLUDE_RECENT_IPO_BARS = 60
MAX_OUTPUT_PER_STOCK = 2

FAILED_ROWS = []


def log(msg):
    print(msg, flush=True)


def fmt_seconds(sec):
    sec = int(max(sec, 0))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h:
        return f"{h}h{m}m{s}s"
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def baostock_code(code):
    code = str(code).zfill(6)
    if code.startswith("6"):
        return "sh." + code
    return "sz." + code


def eastmoney_secid(code):
    code = str(code).zfill(6)
    if code.startswith("6"):
        return "1." + code
    return "0." + code


def fetch_stock_list_from_baostock():
    lg = bs.login()
    log(f"[BAOSTOCK LOGIN LIST] {lg.error_code} {lg.error_msg}")

    rs = bs.query_all_stock(day=datetime.now().strftime("%Y-%m-%d"))

    data = []
    fields = rs.fields

    while rs.next():
        data.append(rs.get_row_data())

    bs.logout()

    if not data:
        raise RuntimeError("baostock query_all_stock returned empty")

    df = pd.DataFrame(data, columns=fields)
    df = df[df["code"].str.startswith(("sh.6", "sz.0", "sz.3"))].copy()
    df["code"] = df["code"].str[-6:]
    df["name"] = df["code"]

    return df[["code", "name"]]


def fetch_stock_list_from_akshare():
    df = ak.stock_info_a_code_name()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["name"] = df["name"].astype(str)
    return df[["code", "name"]]


def fetch_stock_list():
    for attempt in range(3):
        try:
            log(f"[FETCH LIST] source=baostock attempt={attempt+1}")
            return fetch_stock_list_from_baostock()
        except Exception as e:
            log(f"[WARN] baostock stock list failed attempt={attempt+1}: {e}")
            time.sleep(5)

    for attempt in range(3):
        try:
            log(f"[FETCH LIST] source=akshare attempt={attempt+1}")
            return fetch_stock_list_from_akshare()
        except Exception as e:
            log(f"[WARN] akshare stock list failed attempt={attempt+1}: {e}")
            time.sleep(5)

    raise RuntimeError("all stock list sources failed")


def clean_daily_df(df):
    if df is None or df.empty:
        return None

    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    for col in ["open", "close", "high", "low", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date", "open", "close", "high", "low", "volume"])
    df = df.sort_values("date").reset_index(drop=True)

    if len(df) < MIN_BARS:
        return None

    return df


def fetch_daily_from_baostock(code):
    bs_code = baostock_code(code)

    lg = bs.login()

    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,open,high,low,close,volume,amount",
        start_date="2023-01-01",
        end_date=datetime.now().strftime("%Y-%m-%d"),
        frequency="d",
        adjustflag="2"
    )

    data = []
    fields = rs.fields

    while rs.next():
        data.append(rs.get_row_data())

    bs.logout()

    if not data:
        return None

    df = pd.DataFrame(data, columns=fields)
    return clean_daily_df(df)


def fetch_daily_from_akshare(code):
    df = ak.stock_zh_a_hist(
        symbol=str(code).zfill(6),
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
    })

    return clean_daily_df(df)


def fetch_daily_from_eastmoney(code):
    secid = eastmoney_secid(code)

    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "101",
        "fqt": "1",
        "beg": "20230101",
        "end": datetime.now().strftime("%Y%m%d"),
        "lmt": "1000000",
    }

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()

    js = r.json()

    data = js.get("data") or {}
    klines = data.get("klines") or []

    if not klines:
        return None

    rows = []

    for item in klines:
        parts = item.split(",")
        if len(parts) < 7:
            continue

        rows.append({
            "date": parts[0],
            "open": parts[1],
            "close": parts[2],
            "high": parts[3],
            "low": parts[4],
            "volume": parts[5],
            "amount": parts[6],
        })

    df = pd.DataFrame(rows)
    return clean_daily_df(df)


def fetch_daily(code, name):
    # 1）BaoStock 主源
    for attempt in range(2):
        try:
            df = fetch_daily_from_baostock(code)
            if df is not None:
                return df, "baostock"
        except Exception as e:
            log(f"[WARN] source=baostock code={code} name={name} attempt={attempt+1} err={e}")
            time.sleep(1.5 + attempt)

    # 2）AkShare 兜底
    for attempt in range(2):
        try:
            df = fetch_daily_from_akshare(code)
            if df is not None:
                return df, "akshare"
        except Exception as e:
            log(f"[WARN] source=akshare code={code} name={name} attempt={attempt+1} err={e}")
            time.sleep(2 + attempt * 2)

    # 3）东方财富直连兜底
    for attempt in range(2):
        try:
            df = fetch_daily_from_eastmoney(code)
            if df is not None:
                return df, "eastmoney"
        except Exception as e:
            log(f"[WARN] source=eastmoney code={code} name={name} attempt={attempt+1} err={e}")
            time.sleep(2 + attempt * 2)

    FAILED_ROWS.append({
        "股票代码": code,
        "股票名称": name,
        "失败原因": "all_daily_sources_failed",
    })

    return None, "failed"


def find_highest_close_line(df, lookback):
    if len(df) < max(MIN_BARS, lookback // 2):
        return None

    recent = df.tail(lookback).copy()

    if len(recent) < 60:
        return None

    recent = recent.iloc[EXCLUDE_RECENT_IPO_BARS:]

    if recent.empty:
        return None

    idx = recent["close"].idxmax()

    line_price = float(df.loc[idx, "close"])
    line_date = df.loc[idx, "date"]

    # 不能是最近几天刚冒出来的孤立点
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
        body_size = max(body_high - body_low, 0.01)

        entity_above_ratio = max(0, body_high - max(body_low, line_price)) / body_size

        body_stand = (
            r["close"] > line_price and
            entity_above_ratio >= 0.5
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
    start_ts = time.time()
    today = datetime.now().strftime("%Y-%m-%d")

    out_path = os.path.join(
        OUT_DIR,
        f"structure_line_candidates_{today}.csv"
    )

    failed_path = os.path.join(
        OUT_DIR,
        f"structure_line_failed_{today}.csv"
    )

    stocks = fetch_stock_list()

    rows = []
    success = 0
    failed = 0

    source_count = {
        "baostock": 0,
        "akshare": 0,
        "eastmoney": 0,
        "failed": 0,
    }

    total = len(stocks)

    log(f"[START] stocks={total}")

    for i, row in stocks.iterrows():
        code = str(row["code"]).zfill(6)
        name = str(row["name"])

        try:
            df, source = fetch_daily(code, name)
            source_count[source] = source_count.get(source, 0) + 1

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
                        "数据源": source,
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
            source_count["failed"] += 1
            FAILED_ROWS.append({
                "股票代码": code,
                "股票名称": name,
                "失败原因": str(e),
            })
            log(f"[ERROR] code={code} name={name}: {e}")
            traceback.print_exc(limit=1)

        done = i + 1

        if done % 50 == 0 or done == total:
            elapsed = time.time() - start_ts
            avg = elapsed / max(done, 1)
            remain = avg * (total - done)
            pct = done / total * 100

            log(
                f"[PROGRESS] {done}/{total} "
                f"({pct:.2f}%) | "
                f"success={success} failed={failed} "
                f"candidates={len(rows)} | "
                f"sources={source_count} | "
                f"elapsed={fmt_seconds(elapsed)} "
                f"eta={fmt_seconds(remain)}"
            )

        time.sleep(0.03)

    out = pd.DataFrame(rows)

    if not out.empty:
        cols = [
            "股票代码",
            "股票名称",
            "数据源",
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
            "备注",
        ]

        out = out[cols]
        out.to_csv(out_path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame([{
            "诊断": "没有生成任何候选线",
            "success": success,
            "failed": failed,
            "source_count": str(source_count),
        }]).to_csv(out_path, index=False, encoding="utf-8-sig")

    if FAILED_ROWS:
        pd.DataFrame(FAILED_ROWS).to_csv(
            failed_path,
            index=False,
            encoding="utf-8-sig"
        )
    else:
        pd.DataFrame([{
            "诊断": "无失败股票"
        }]).to_csv(
            failed_path,
            index=False,
            encoding="utf-8-sig"
        )

    elapsed = time.time() - start_ts

    log(
        f"[DONE] success={success}, failed={failed}, "
        f"candidates={len(rows)}, sources={source_count}, "
        f"elapsed={fmt_seconds(elapsed)}"
    )

    log(f"[CSV] {out_path}")
    log(f"[FAILED CSV] {failed_path}")


if __name__ == "__main__":
    main()
