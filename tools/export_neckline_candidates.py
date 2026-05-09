# -*- coding: utf-8 -*-
"""
人工审核友好版：A股最高收盘共振线/平台颈线候选导出

特点：
1. 继续使用 kline_cache，速度快
2. 自动增量更新缓存
3. 输出富途代码：SZ.000001 / SH.600519
4. 补全股票名称
5. 字段改成人能看懂
6. 结构质量分拆成几个子项
7. 新股/次新股过滤
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
MIN_BARS = 120
MIN_LIST_DAYS = 120
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


def futu_code(code):
    code = str(code).zfill(6)
    if code.startswith("6"):
        return "SH." + code
    return "SZ." + code


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
    df["name"] = df.get("code_name", df["code"])
    return df[["code", "name"]]


def fetch_stock_list_from_akshare():
    df = ak.stock_info_a_code_name()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["name"] = df["name"].astype(str)
    return df[["code", "name"]]


def fetch_stock_list():
    stocks = None

    for attempt in range(3):
        try:
            log(f"[FETCH LIST] source=akshare attempt={attempt + 1}")
            stocks = fetch_stock_list_from_akshare()
            break
        except Exception as e:
            log(f"[WARN] akshare stock list failed attempt={attempt + 1}: {e}")
            time.sleep(3)

    if stocks is None:
        for attempt in range(3):
            try:
                log(f"[FETCH LIST] source=baostock attempt={attempt + 1}")
                stocks = fetch_stock_list_from_baostock()
                break
            except Exception as e:
                log(f"[WARN] baostock stock list failed attempt={attempt + 1}: {e}")
                time.sleep(3)

    if stocks is None:
        raise RuntimeError("all stock list sources failed")

    stocks["code"] = stocks["code"].astype(str).str.zfill(6)
    stocks["name"] = stocks["name"].astype(str)

    return stocks.drop_duplicates("code").reset_index(drop=True)


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
        "富途代码": futu_code(code),
        "股票名称": name,
        "失败原因": "all_daily_sources_failed",
    })
    return None, "failed"


def count_separated_touches(window, line_price, tol=TOUCH_TOL, min_gap=5):
    mask = (
        (window["high"] >= line_price * (1 - tol)) &
        (window["close"] <= line_price * (1 + tol))
    )
    idxs = list(window[mask].index)

    if not idxs:
        return 0

    count = 1
    last = idxs[0]
    for idx in idxs[1:]:
        if idx - last >= min_gap:
            count += 1
            last = idx

    return count


def line_quality_level(score):
    if score >= 80:
        return "A-较强"
    if score >= 65:
        return "B-可观察"
    if score >= 50:
        return "C-一般"
    return "D-偏弱"


def find_highest_close_line(df, lookback):
    if len(df) < max(MIN_BARS, lookback):
        return None

    if len(df) < MIN_LIST_DAYS:
        return None

    recent = df.tail(lookback).copy()
    if len(recent) < lookback * 0.8:
        return None

    recent_for_line = recent.iloc[EXCLUDE_RECENT_IPO_BARS:]
    if recent_for_line.empty:
        return None

    idx = recent_for_line["close"].idxmax()
    source = df.loc[idx]

    line_price = float(source["close"])
    line_date = source["date"]

    # 排除最近5天刚形成的新高点，避免孤立点
    if idx > len(df) - 5:
        return None

    window = df.tail(lookback).copy()

    separated_touch_count = count_separated_touches(window, line_price)

    near_close = window[
        (window["close"] >= line_price * (1 - TOUCH_TOL)) &
        (window["close"] <= line_price * (1 + TOUCH_TOL))
    ]
    near_close_days = len(near_close)

    close_above_days = len(window[window["close"] > line_price])
    high_break_days = len(window[window["high"] > line_price])

    last = df.iloc[-1]
    current_price = float(last["close"])
    distance_pct = (line_price - current_price) / current_price * 100

    source_open = float(source["open"])
    source_close = float(source["close"])
    source_high = float(source["high"])
    source_low = float(source["low"])
    source_volume = float(source["volume"])

    body_top = max(source_open, source_close)
    body_bottom = min(source_open, source_close)
    is_bull = source_close > source_open
    close_pos = (source_close - source_low) / max(source_high - source_low, 0.01)

    # 是否最大量阳K
    bull_window = window[window["close"] > window["open"]].copy()
    if not bull_window.empty:
        max_vol_bull_idx = bull_window["volume"].idxmax()
        max_vol_bull = df.loc[max_vol_bull_idx]
        max_vol_bull_high = float(max_vol_bull["high"])
        max_vol_bull_body_low = min(float(max_vol_bull["open"]), float(max_vol_bull["close"]))
        is_max_vol_bull_source = idx == max_vol_bull_idx
        vol_high_gap_pct = abs(max_vol_bull_high - source_high) / max(source_high, 0.01) * 100
    else:
        max_vol_bull_idx = None
        max_vol_bull_high = None
        max_vol_bull_body_low = None
        is_max_vol_bull_source = False
        vol_high_gap_pct = None

    # 突破识别
    breakout = None
    after_line = df[df.index > idx].copy()

    for j, r in after_line.iterrows():
        body_high = max(float(r["open"]), float(r["close"]))
        body_low = min(float(r["open"]), float(r["close"]))
        body_size = max(body_high - body_low, 0.01)

        entity_above_ratio = max(0, body_high - max(body_low, line_price)) / body_size
        body_stand = float(r["close"]) > line_price and entity_above_ratio >= 0.5
        gap_stand = float(r["low"]) > line_price

        if body_stand or gap_stand:
            breakout = (j, r, "跳空站上" if gap_stand else "实体站上")
            break

    if breakout is None:
        breakout_date = ""
        breakout_type = "未有效站上"
        fell_back_3 = ""
        fell_back_5 = ""
        structure_status = "接近/未突破"
    else:
        bidx, brow, breakout_type = breakout
        breakout_date = brow["date"].strftime("%Y-%m-%d")

        next3 = df.iloc[bidx + 1:bidx + 4]
        next5 = df.iloc[bidx + 1:bidx + 6]

        fell_back_3 = "是" if (not next3.empty and (next3["close"] < line_price).any()) else "否"
        fell_back_5 = "是" if (not next5.empty and (next5["close"] < line_price).any()) else "否"
        structure_status = "已突破"

    # 结构质量分拆解，满分100
    line_source_score = 20
    if is_bull and close_pos >= 0.65:
        line_source_score += 5
    if is_max_vol_bull_source:
        line_source_score += 5
    line_source_score = min(line_source_score, 30)

    platform_score = min(separated_touch_count * 6, 24)
    if near_close_days >= 20:
        platform_score += 4
    platform_score = min(platform_score, 30)

    breakout_score = 0
    if breakout_type == "实体站上":
        breakout_score = 18
    elif breakout_type == "跳空站上":
        breakout_score = 22
    if fell_back_3 == "否" and breakout_type != "未有效站上":
        breakout_score += 5
    breakout_score = min(breakout_score, 30)

    distance_score = 10
    if distance_pct > 15:
        distance_score = 2
    elif distance_pct > 8:
        distance_score = 5
    elif distance_pct < -10:
        distance_score = 4

    raw_score = line_source_score + platform_score + breakout_score + distance_score
    quality_score = min(100, round(raw_score, 1))

    if structure_status == "已突破":
        conclusion = f"候选线已被{breakout_type}，重点核对突破是否真实、是否为平台上沿/阶段高点。"
    elif abs(distance_pct) <= 5:
        conclusion = "当前接近候选线但未有效突破，重点核对这条线是否为真实压力/平台上沿。"
    elif distance_pct > 5:
        conclusion = "候选线在当前价上方，属于上方压力观察线。"
    else:
        conclusion = "当前已在线上方，但未识别到清晰有效突破动作，需人工核对是否漏判。"

    review_focus = (
        "请人工判断：这条线是否来自平台上沿、阶段最高收盘或阶段高点压力；"
        "不是前低、现价线、孤立尖峰或上市初期乱波动。"
    )

    return {
        "候选线价格": round(line_price, 2),
        "候选线类型": f"{lookback}日最高收盘共振线",
        "候选线来源K日期": line_date.strftime("%Y-%m-%d"),
        "来源K开盘": round(source_open, 2),
        "来源K收盘": round(source_close, 2),
        "来源K最高": round(source_high, 2),
        "来源K最低": round(source_low, 2),
        "来源K成交量": round(source_volume, 0),
        "来源K是否阳线": "是" if is_bull else "否",
        "来源K收盘位置": round(close_pos, 2),
        "当前价到候选线距离%": round(distance_pct, 2),
        "有效触碰次数": int(separated_touch_count),
        "线附近收盘天数": int(near_close_days),
        "突破日期": breakout_date,
        "突破方式": breakout_type,
        "突破后3日是否跌回": fell_back_3,
        "突破后5日是否跌回": fell_back_5,
        "结构状态": structure_status,
        "是否最大量阳K来源": "是" if is_max_vol_bull_source else "否",
        "最大量阳K高点": round(max_vol_bull_high, 2) if max_vol_bull_high is not None else "",
        "最大量阳K实体实底": round(max_vol_bull_body_low, 2) if max_vol_bull_body_low is not None else "",
        "最大量阳K高点与来源K高点距离%": round(vol_high_gap_pct, 2) if vol_high_gap_pct is not None else "",
        "线来源评分": line_source_score,
        "平台共振评分": platform_score,
        "突破确认评分": breakout_score,
        "位置距离评分": distance_score,
        "结构质量分": quality_score,
        "结构质量等级": line_quality_level(quality_score),
        "模型结论": conclusion,
        "人工审核重点": review_focus,
    }


def save_outputs(rows, failed_rows, source_count, success, failed, elapsed):
    today = datetime.now().strftime("%Y-%m-%d")

    out_path = os.path.join(OUT_DIR, f"structure_line_candidates_review_{today}.csv")
    failed_path = os.path.join(OUT_DIR, f"structure_line_failed_{today}.csv")

    out = pd.DataFrame(rows)

    if not out.empty:
        cols = [
            "富途代码",
            "原始代码",
            "股票名称",
            "数据源",
            "数据更新日期",
            "当前最新K日期",
            "当前收盘价",
            "候选线价格",
            "候选线类型",
            "候选线来源K日期",
            "来源K开盘",
            "来源K收盘",
            "来源K最高",
            "来源K最低",
            "来源K成交量",
            "来源K是否阳线",
            "来源K收盘位置",
            "当前价到候选线距离%",
            "有效触碰次数",
            "线附近收盘天数",
            "突破日期",
            "突破方式",
            "突破后3日是否跌回",
            "突破后5日是否跌回",
            "结构状态",
            "是否最大量阳K来源",
            "最大量阳K高点",
            "最大量阳K实体实底",
            "最大量阳K高点与来源K高点距离%",
            "线来源评分",
            "平台共振评分",
            "突破确认评分",
            "位置距离评分",
            "结构质量分",
            "结构质量等级",
            "模型结论",
            "人工审核重点",
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
        pd.DataFrame(failed_rows).to_csv(failed_path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame([{"诊断": "无失败股票"}]).to_csv(failed_path, index=False, encoding="utf-8-sig")

    log(f"[CSV] {out_path}")
    log(f"[FAILED CSV] {failed_path}")


def main():
    start_ts = time.time()

    rows = []
    success = 0
    failed = 0
    skipped_new_stock = 0
    source_count = {}

    try:
        stocks = fetch_stock_list()
        total = len(stocks)

        log(f"[START] stocks={total}")

        for i, row in stocks.iterrows():
            code = str(row["code"]).zfill(6)
            name = str(row["name"])

            try:
                if name.startswith(("N", "C")):
                    skipped_new_stock += 1
                    continue

                df, source = fetch_daily(code, name)
                source_count[source] = source_count.get(source, 0) + 1

                if df is None or len(df) < MIN_LIST_DAYS:
                    skipped_new_stock += 1
                    continue

                candidates = []

                for lb in LOOKBACK_LIST:
                    item = find_highest_close_line(df, lb)
                    if item:
                        item.update({
                            "富途代码": futu_code(code),
                            "原始代码": code,
                            "股票名称": name,
                            "数据源": source,
                            "数据更新日期": today_str(),
                            "当前最新K日期": df.iloc[-1]["date"].strftime("%Y-%m-%d"),
                            "当前收盘价": round(float(df.iloc[-1]["close"]), 2),
                            "人工评价": "",
                            "错误类型": "",
                            "备注": "",
                        })
                        candidates.append(item)

                candidates = sorted(
                    candidates,
                    key=lambda x: x["结构质量分"],
                    reverse=True
                )

                rows.extend(candidates[:MAX_OUTPUT_PER_STOCK])
                success += 1

            except Exception as e:
                failed += 1
                source_count["failed"] = source_count.get("failed", 0) + 1

                FAILED_ROWS.append({
                    "富途代码": futu_code(code),
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
                    f"success={success} failed={failed} skipped_new={skipped_new_stock} "
                    f"candidates={len(rows)} | sources={source_count} | "
                    f"elapsed={fmt_seconds(elapsed)} eta={fmt_seconds(remain)}"
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
            f"[DONE] success={success}, failed={failed}, skipped_new={skipped_new_stock}, "
            f"candidates={len(rows)}, sources={source_count}, elapsed={fmt_seconds(elapsed)}"
        )


if __name__ == "__main__":
    main()
