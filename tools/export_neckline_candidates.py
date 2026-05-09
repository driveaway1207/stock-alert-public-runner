# -*- coding: utf-8 -*-
"""
A股颈线/最高收盘共振线候选导出：缓存优先 + 自动增量更新版

核心逻辑：
1）优先读 kline_cache
2）检查缓存最后日期
3）若缓存过期，自动补最近60天K线
4）合并去重后重新保存缓存
5）再做最高收盘共振线/平台颈线候选识别
6）导出CSV给人工审核

数据源顺序：
1）kline_cache 本地缓存
2）BaoStock 主源
3）AkShare 兜底
4）东方财富直连兜底
"""

import os
import time
import glob
import traceback
from datetime import datetime, timedelta

import requests
import pandas as pd
import baostock as bs
import akshare as ak


OUT_DIR = "outputs"
KLINE_CACHE_DIR = "kline_cache"

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(KLINE_CACHE_DIR, exist_ok=True)

LOOKBACK_LIST = [120, 250]

TOUCH_TOL = 0.02
MIN_TOUCH = 2
MIN_BARS = 80
EXCLUDE_RECENT_IPO_BARS = 60
MAX_OUTPUT_PER_STOCK = 2

INCREMENTAL_DAYS = 60

FAILED_ROWS = []
BAOSTOCK_READY = False


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


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def baostock_code(code):
    code = str(code).zfill(6)
    return "sh." + code if code.startswith("6") else "sz." + code


def eastmoney_secid(code):
    code = str(code).zfill(6)
    return "1." + code if code.startswith("6") else "0." + code


def normalize_daily_columns(df):
    if df is None or df.empty:
        return None

    rename_map = {
        "日期": "date",
        "交易日期": "date",
        "trade_date": "date",
        "Date": "date",
        "datetime": "date",
        "time": "date",
        "开盘": "open",
        "开盘价": "open",
        "Open": "open",
        "收盘": "close",
        "收盘价": "close",
        "Close": "close",
        "最高": "high",
        "最高价": "high",
        "High": "high",
        "最低": "low",
        "最低价": "low",
        "Low": "low",
        "成交量": "volume",
        "成交量(手)": "volume",
        "vol": "volume",
        "Volume": "volume",
        "成交额": "amount",
        "成交额(元)": "amount",
        "turnover": "amount",
        "Amount": "amount",
    }

    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})

    need = ["date", "open", "close", "high", "low", "volume"]
    if not all(c in df.columns for c in need):
        return None

    if "amount" not in df.columns:
        df["amount"] = 0

    df = df[["date", "open", "close", "high", "low", "volume", "amount"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    for col in ["open", "close", "high", "low", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date", "open", "close", "high", "low", "volume"])
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)

    if len(df) < MIN_BARS:
        return None

    return df


def cache_path(code):
    return os.path.join(KLINE_CACHE_DIR, f"{str(code).zfill(6)}.csv")


def read_cache(code):
    path = cache_path(code)
    if not os.path.exists(path):
        return None
    try:
        return normalize_daily_columns(pd.read_csv(path))
    except Exception as e:
        log(f"[WARN] read cache failed code={code} err={e}")
        return None


def save_cache(code, df):
    try:
        df = normalize_daily_columns(df)
        if df is None:
            return
        df.to_csv(cache_path(code), index=False, encoding="utf-8-sig")
    except Exception as e:
        log(f"[WARN] save cache failed code={code} err={e}")


def merge_kline(old_df, new_df):
    if old_df is None:
        return normalize_daily_columns(new_df)
    if new_df is None:
        return normalize_daily_columns(old_df)

    df = pd.concat([old_df, new_df], ignore_index=True)
    return normalize_daily_columns(df)


def cache_is_fresh(df):
    if df is None or df.empty:
        return False

    last_date = pd.to_datetime(df["date"].max()).date()
    now_date = datetime.now().date()

    # 周末/节假日不强求今天有K，允许最近5天内为新
    return (now_date - last_date).days <= 5


def baostock_login_once():
    global BAOSTOCK_READY

    if BAOSTOCK_READY:
        return True

    lg = bs.login()
    log(f"[BAOSTOCK LOGIN] {lg.error_code} {lg.error_msg}")

    if lg.error_code == "0":
        BAOSTOCK_READY = True
        return True

    BAOSTOCK_READY = False
    return False


def baostock_logout_once():
    global BAOSTOCK_READY

    if BAOSTOCK_READY:
        try:
            bs.logout()
            log("[BAOSTOCK LOGOUT] success")
        except Exception as e:
            log(f"[WARN] baostock logout failed: {e}")

    BAOSTOCK_READY = False


def fetch_stock_list_from_baostock():
    if not baostock_login_once():
        raise RuntimeError("baostock login failed")

    rs = bs.query_all_stock(day=today_str())

    data = []
    fields = rs.fields

    while rs.next():
        data.append(rs.get_row_data())

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
    # 如果已经有缓存，优先用缓存股票池
    files = glob.glob(os.path.join(KLINE_CACHE_DIR, "*.csv"))
    cached_codes = []
    for f in files:
        base = os.path.basename(f)
        digits = "".join(ch for ch in base if ch.isdigit())
        if len(digits) >= 6:
            cached_codes.append(digits[-6:])

    if len(cached_codes) >= 1000:
        cached_codes = sorted(set(cached_codes))
        log(f"[STOCK LIST] using cache codes={len(cached_codes)}")
        return pd.DataFrame({"code": cached_codes, "name": cached_codes})

    for attempt in range(3):
        try:
            log(f"[FETCH LIST] source=baostock attempt={attempt + 1}")
            return fetch_stock_list_from_baostock()
        except Exception as e:
            log(f"[WARN] baostock stock list failed attempt={attempt + 1}: {e}")
            time.sleep(3)

    for attempt in range(3):
        try:
            log(f"[FETCH LIST] source=akshare attempt={attempt + 1}")
            return fetch_stock_list_from_akshare()
        except Exception as e:
            log(f"[WARN] akshare stock list failed attempt={attempt + 1}: {e}")
            time.sleep(5)

    raise RuntimeError("all stock list sources failed")


def fetch_daily_from_baostock(code, start_date="2023-01-01"):
    if not BAOSTOCK_READY:
        if not baostock_login_once():
            return None

    rs = bs.query_history_k_data_plus(
        baostock_code(code),
        "date,open,high,low,close,volume,amount",
        start_date=start_date,
        end_date=today_str(),
        frequency="d",
        adjustflag="2",
    )

    data = []
    fields = rs.fields

    while rs.next():
        data.append(rs.get_row_data())

    if not data:
        return None

    return normalize_daily_columns(pd.DataFrame(data, columns=fields))


def fetch_daily_from_akshare(code):
    df = ak.stock_zh_a_hist(
        symbol=str(code).zfill(6),
        period="daily",
        adjust="qfq",
    )
    return normalize_daily_columns(df)


def fetch_daily_from_eastmoney(code, beg="20230101"):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    params = {
        "secid": eastmoney_secid(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "101",
        "fqt": "1",
        "beg": beg.replace("-", ""),
        "end": datetime.now().strftime("%Y%m%d"),
        "lmt": "1000000",
    }

    r = requests.get(
        url,
        params=params,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15
    )

    r.raise_for_status()
    js = r.json()

    klines = (js.get("data") or {}).get("klines") or []

    rows = []

    for item in klines:
        p = item.split(",")
        if len(p) >= 7:
            rows.append({
                "date": p[0],
                "open": p[1],
                "close": p[2],
                "high": p[3],
                "low": p[4],
                "volume": p[5],
                "amount": p[6],
            })

    return normalize_daily_columns(pd.DataFrame(rows))


def fetch_incremental(code, cache_df):
    if cache_df is not None and not cache_df.empty:
        last_date = pd.to_datetime(cache_df["date"].max()).date()
        start_date = last_date - timedelta(days=INCREMENTAL_DAYS)
        start_str = start_date.strftime("%Y-%m-%d")
    else:
        start_str = "2023-01-01"

    try:
        df = fetch_daily_from_baostock(code, start_str)
        if df is not None:
            return df, "baostock_incremental"
    except Exception as e:
        log(f"[WARN] incremental baostock failed code={code} err={e}")

    try:
        df = fetch_daily_from_akshare(code)
        if df is not None:
            return df, "akshare_full"
    except Exception as e:
        log(f"[WARN] incremental akshare failed code={code} err={e}")

    try:
        df = fetch_daily_from_eastmoney(code, start_str)
        if df is not None:
            return df, "eastmoney_incremental"
    except Exception as e:
        log(f"[WARN] incremental eastmoney failed code={code} err={e}")

    return None, "failed"


def fetch_daily(code, name):
    code = str(code).zfill(6)

    cache_df = read_cache(code)

    if cache_df is not None and cache_is_fresh(cache_df):
        return cache_df, "cache_fresh"

    if cache_df is not None:
        new_df, source = fetch_incremental(code, cache_df)
        merged = merge_kline(cache_df, new_df)

        if merged is not None:
            save_cache(code, merged)
            return merged, f"cache_plus_{source}"

    full_df, source = fetch_incremental(code, None)

    if full_df is not None:
        save_cache(code, full_df)
        return full_df, source

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

        entity_above_ratio = max(
            0,
            body_high - max(body_low, line_price)
        ) / body_size

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


def save_outputs(rows, failed_rows, source_count, success, failed, elapsed):
    today = datetime.now().strftime("%Y-%m-%d")

    out_path = os.path.join(
        OUT_DIR,
        f"structure_line_candidates_{today}.csv"
    )

    failed_path = os.path.join(
        OUT_DIR,
        f"structure_line_failed_{today}.csv"
    )

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
            "elapsed": fmt_seconds(elapsed),
        }]).to_csv(out_path, index=False, encoding="utf-8-sig")

    if failed_rows:
        pd.DataFrame(failed_rows).to_csv(
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

    log(f"[CSV] {out_path}")
    log(f"[FAILED CSV] {failed_path}")


def main():
    start_ts = time.time()

    rows = []
    success = 0
    failed = 0

    source_count = {}

    try:
        stocks = fetch_stock_list()
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
                source_count["failed"] = source_count.get("failed", 0) + 1

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
                    f"[PROGRESS] {done}/{total} ({pct:.2f}%) | "
                    f"success={success} failed={failed} "
                    f"candidates={len(rows)} | "
                    f"sources={source_count} | "
                    f"elapsed={fmt_seconds(elapsed)} "
                    f"eta={fmt_seconds(remain)}"
                )

            time.sleep(0.005)

    finally:
        elapsed = time.time() - start_ts

        save_outputs(
            rows=rows,
            failed_rows=FAILED_ROWS,
            source_count=source_count,
            success=success,
            failed=failed,
            elapsed=elapsed,
        )

        baostock_logout_once()

        log(
            f"[DONE] success={success}, failed={failed}, "
            f"candidates={len(rows)}, sources={source_count}, "
            f"elapsed={fmt_seconds(elapsed)}"
        )


if __name__ == "__main__":
    main()
