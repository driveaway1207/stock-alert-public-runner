# -*- coding: utf-8 -*-
"""
A股数据层体检表导出

目的：
只检查K线数据是否可靠，不做结构、不做颈线、不做推荐。

输出：
outputs/data_health_check_日期.csv
outputs/data_health_failed_日期.csv

用户只需要审核：
人工审核
人工备注
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

MIN_DAILY_BARS = 120
FULL_DAILY_BARS = 250
INCREMENTAL_DAYS = 60

BAOSTOCK_READY = False
FAILED_ROWS = []


def log(msg):
    print(msg, flush=True)


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


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


def stock_code(code):
    code = str(code).zfill(6)
    if code.startswith("6"):
        return "SH." + code
    return "SZ." + code


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


def cache_path(code):
    return os.path.join(KLINE_CACHE_DIR, f"{str(code).zfill(6)}.csv")


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

    required = ["date", "open", "close", "high", "low", "volume"]
    if not all(c in df.columns for c in required):
        return None

    if "amount" not in df.columns:
        df["amount"] = 0

    df = df[["date", "open", "close", "high", "low", "volume", "amount"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    for col in ["open", "close", "high", "low", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return df


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
        if df is None or df.empty:
            return
        df.to_csv(cache_path(code), index=False, encoding="utf-8-sig")
    except Exception as e:
        log(f"[WARN] save cache failed code={code} err={e}")


def merge_kline(old_df, new_df):
    old_df = normalize_daily_columns(old_df)
    new_df = normalize_daily_columns(new_df)

    if old_df is None or old_df.empty:
        return new_df
    if new_df is None or new_df.empty:
        return old_df

    df = pd.concat([old_df, new_df], ignore_index=True)
    return normalize_daily_columns(df)


def cache_is_fresh(df):
    if df is None or df.empty or "date" not in df.columns:
        return False

    last_date = pd.to_datetime(df["date"].max()).date()
    now_date = datetime.now().date()

    # 周末/节假日/停牌允许一定宽松度
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


def fetch_stock_list_from_akshare():
    df = ak.stock_info_a_code_name()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["name"] = df["name"].astype(str)
    return df[["code", "name"]]


def fetch_stock_list_from_baostock():
    if not baostock_login_once():
        raise RuntimeError("baostock login failed")

    rs = bs.query_all_stock(day=today_str())

    rows = []
    fields = rs.fields

    while rs.next():
        rows.append(rs.get_row_data())

    if not rows:
        raise RuntimeError("baostock query_all_stock returned empty")

    df = pd.DataFrame(rows, columns=fields)
    df = df[df["code"].str.startswith(("sh.6", "sz.0", "sz.3"))].copy()
    df["code"] = df["code"].str[-6:]
    df["name"] = df.get("code_name", df["code"])
    return df[["code", "name"]]


def fetch_stock_list():
    for attempt in range(3):
        try:
            log(f"[FETCH LIST] source=akshare attempt={attempt + 1}")
            df = fetch_stock_list_from_akshare()
            if df is not None and not df.empty:
                return df.drop_duplicates("code").reset_index(drop=True)
        except Exception as e:
            log(f"[WARN] akshare stock list failed attempt={attempt + 1}: {e}")
            time.sleep(3)

    for attempt in range(3):
        try:
            log(f"[FETCH LIST] source=baostock attempt={attempt + 1}")
            df = fetch_stock_list_from_baostock()
            if df is not None and not df.empty:
                return df.drop_duplicates("code").reset_index(drop=True)
        except Exception as e:
            log(f"[WARN] baostock stock list failed attempt={attempt + 1}: {e}")
            time.sleep(3)

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

    rows = []
    fields = rs.fields

    while rs.next():
        rows.append(rs.get_row_data())

    if not rows:
        return None

    return normalize_daily_columns(pd.DataFrame(rows, columns=fields))


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
        timeout=15,
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
        if df is not None and not df.empty:
            return df, "增量更新成功"
    except Exception as e:
        log(f"[WARN] baostock update failed code={code} err={e}")

    try:
        df = fetch_daily_from_akshare(code)
        if df is not None and not df.empty:
            return df, "补充更新成功"
    except Exception as e:
        log(f"[WARN] akshare update failed code={code} err={e}")

    try:
        df = fetch_daily_from_eastmoney(code, start_str)
        if df is not None and not df.empty:
            return df, "补充更新成功"
    except Exception as e:
        log(f"[WARN] eastmoney update failed code={code} err={e}")

    return None, "更新失败"


def get_daily_kline(code):
    code = str(code).zfill(6)
    cache_df = read_cache(code)
    cache_exists = cache_df is not None and not cache_df.empty

    if cache_exists and cache_is_fresh(cache_df):
        return cache_df, "有", "新鲜", "无需更新"

    if cache_exists:
        new_df, update_status = fetch_incremental(code, cache_df)
        merged = merge_kline(cache_df, new_df)
        if merged is not None and not merged.empty:
            save_cache(code, merged)
            freshness = "新鲜" if cache_is_fresh(merged) else "偏旧"
            return merged, "有", freshness, update_status

    full_df, update_status = fetch_incremental(code, None)
    if full_df is not None and not full_df.empty:
        save_cache(code, full_df)
        freshness = "新鲜" if cache_is_fresh(full_df) else "偏旧"
        return full_df, "无", freshness, "首次建立缓存"

    return None, "无", "不可用", "更新失败"


def count_recent_bars(df, days):
    if df is None or df.empty:
        return 0

    latest = pd.to_datetime(df["date"].max())
    start = latest - pd.Timedelta(days=days)
    return int((df["date"] >= start).sum())


def first_bad_date(df, mask):
    bad = df[mask]
    if bad.empty:
        return ""
    return bad.iloc[0]["date"].strftime("%Y-%m-%d")


def inspect_kline(df, code, name, cache_exists, freshness, update_status):
    result = {
        "股票代码": stock_code(code),
        "股票名称": name,
        "市场": "沪市" if str(code).startswith("6") else "深市",
        "是否ST": "是" if "ST" in name.upper() else "否",
        "是否N/C新股": "是" if name.startswith(("N", "C")) else "否",
        "缓存是否存在": cache_exists,
        "缓存是否新鲜": freshness,
        "缓存更新状态": update_status,
        "K线起始日期": "",
        "K线最新日期": "",
        "K线总根数": 0,
        "最近30日K线数量": 0,
        "最近60日K线数量": 0,
        "是否少于120根K": "是",
        "是否少于250根K": "是",
        "上市成熟度": "数据不可用",
        "是否存在开高低收缺失": "是",
        "是否存在价格为0": "未知",
        "是否存在high_low错误": "未知",
        "是否存在high小于开收": "未知",
        "是否存在low大于开收": "未知",
        "价格异常K线数量": 0,
        "价格异常日期示例": "",
        "是否存在成交量缺失": "未知",
        "是否存在成交量为0": "未知",
        "最近20日是否长期无量": "未知",
        "是否存在成交额缺失": "未知",
        "成交量异常日期示例": "",
        "数据体检结论": "不通过",
        "可进入模型": "不可进入",
        "不可进入原因": "K线数据不可用",
        "人工审核": "",
        "人工备注": "",
    }

    if df is None or df.empty:
        return result

    df = normalize_daily_columns(df)
    if df is None or df.empty:
        return result

    n = len(df)
    latest_date = pd.to_datetime(df["date"].max())
    first_date = pd.to_datetime(df["date"].min())

    result["K线起始日期"] = first_date.strftime("%Y-%m-%d")
    result["K线最新日期"] = latest_date.strftime("%Y-%m-%d")
    result["K线总根数"] = n
    result["最近30日K线数量"] = count_recent_bars(df, 45)
    result["最近60日K线数量"] = count_recent_bars(df, 90)
    result["是否少于120根K"] = "是" if n < 120 else "否"
    result["是否少于250根K"] = "是" if n < 250 else "否"

    if n < 120 or name.startswith(("N", "C")):
        result["上市成熟度"] = "新股/次新股，不进入结构模型"
    elif n < 250:
        result["上市成熟度"] = "数据偏短，仅短周期观察"
    else:
        result["上市成熟度"] = "成熟，可进入基础结构模型"

    missing_ohlc_mask = df[["open", "high", "low", "close"]].isna().any(axis=1)
    zero_price_mask = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    high_low_error_mask = df["high"] < df["low"]
    high_less_oc_mask = (df["high"] < df["open"]) | (df["high"] < df["close"])
    low_greater_oc_mask = (df["low"] > df["open"]) | (df["low"] > df["close"])

    price_error_mask = (
        missing_ohlc_mask |
        zero_price_mask |
        high_low_error_mask |
        high_less_oc_mask |
        low_greater_oc_mask
    )

    result["是否存在开高低收缺失"] = "是" if missing_ohlc_mask.any() else "否"
    result["是否存在价格为0"] = "是" if zero_price_mask.any() else "否"
    result["是否存在high_low错误"] = "是" if high_low_error_mask.any() else "否"
    result["是否存在high小于开收"] = "是" if high_less_oc_mask.any() else "否"
    result["是否存在low大于开收"] = "是" if low_greater_oc_mask.any() else "否"
    result["价格异常K线数量"] = int(price_error_mask.sum())
    result["价格异常日期示例"] = first_bad_date(df, price_error_mask)

    volume_missing_mask = df["volume"].isna()
    volume_zero_mask = df["volume"] <= 0
    amount_missing_mask = df["amount"].isna()

    recent20 = df.tail(20)
    long_zero_volume = len(recent20) >= 10 and (recent20["volume"] <= 0).sum() >= 10

    volume_error_mask = volume_missing_mask | volume_zero_mask | amount_missing_mask

    result["是否存在成交量缺失"] = "是" if volume_missing_mask.any() else "否"
    result["是否存在成交量为0"] = "是" if volume_zero_mask.any() else "否"
    result["最近20日是否长期无量"] = "是" if long_zero_volume else "否"
    result["是否存在成交额缺失"] = "是" if amount_missing_mask.any() else "否"
    result["成交量异常日期示例"] = first_bad_date(df, volume_error_mask)

    reasons = []

    if name.startswith(("N", "C")):
        reasons.append("N/C新股")
    if "ST" in name.upper():
        reasons.append("ST股票")
    if n < 120:
        reasons.append("K线少于120根")
    if price_error_mask.any():
        reasons.append("存在价格硬错误")
    if long_zero_volume:
        reasons.append("最近20日长期无量")
    if freshness == "不可用":
        reasons.append("缓存不可用")

    if reasons:
        result["数据体检结论"] = "不通过"
        result["可进入模型"] = "不可进入"
        result["不可进入原因"] = "；".join(reasons)
    elif n < 250:
        result["数据体检结论"] = "限制使用"
        result["可进入模型"] = "仅日线短周期观察"
        result["不可进入原因"] = "K线少于250根，不进入周线/月线/大周期结构模型"
    else:
        result["数据体检结论"] = "通过"
        result["可进入模型"] = "日线/周线/基础压力区/平台/倍量"
        result["不可进入原因"] = ""

    return result


def save_outputs(rows, failed_rows, elapsed, success, failed):
    today = today_str()
    out_path = os.path.join(OUT_DIR, f"data_health_check_{today}.csv")
    failed_path = os.path.join(OUT_DIR, f"data_health_failed_{today}.csv")

    df = pd.DataFrame(rows)

    cols = [
        "股票代码",
        "股票名称",
        "市场",
        "是否ST",
        "是否N/C新股",
        "缓存是否存在",
        "缓存是否新鲜",
        "缓存更新状态",
        "K线起始日期",
        "K线最新日期",
        "K线总根数",
        "最近30日K线数量",
        "最近60日K线数量",
        "是否少于120根K",
        "是否少于250根K",
        "上市成熟度",
        "是否存在开高低收缺失",
        "是否存在价格为0",
        "是否存在high_low错误",
        "是否存在high小于开收",
        "是否存在low大于开收",
        "价格异常K线数量",
        "价格异常日期示例",
        "是否存在成交量缺失",
        "是否存在成交量为0",
        "最近20日是否长期无量",
        "是否存在成交额缺失",
        "成交量异常日期示例",
        "数据体检结论",
        "可进入模型",
        "不可进入原因",
        "人工审核",
        "人工备注",
    ]

    if not df.empty:
        df = df[cols]
        df.to_csv(out_path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame([{
            "诊断": "未生成数据体检结果",
            "success": success,
            "failed": failed,
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

    try:
        stocks = fetch_stock_list()
        total = len(stocks)

        log(f"[START] data health check stocks={total}")

        for i, row in stocks.iterrows():
            code = str(row["code"]).zfill(6)
            name = str(row["name"])

            try:
                df, cache_exists, freshness, update_status = get_daily_kline(code)
                item = inspect_kline(df, code, name, cache_exists, freshness, update_status)
                rows.append(item)
                success += 1

            except Exception as e:
                failed += 1
                FAILED_ROWS.append({
                    "股票代码": stock_code(code),
                    "股票名称": name,
                    "失败原因": str(e),
                })
                log(f"[ERROR] code={code} name={name}: {e}")
                traceback.print_exc(limit=1)

            done = i + 1
            if done % 100 == 0 or done == total:
                elapsed = time.time() - start_ts
                avg = elapsed / max(done, 1)
                remain = avg * (total - done)
                pct = done / total * 100

                log(
                    f"[PROGRESS] {done}/{total} ({pct:.2f}%) | "
                    f"success={success} failed={failed} | "
                    f"elapsed={fmt_seconds(elapsed)} eta={fmt_seconds(remain)}"
                )

            time.sleep(0.003)

    finally:
        elapsed = time.time() - start_ts
        save_outputs(rows, FAILED_ROWS, elapsed, success, failed)
        baostock_logout_once()
        log(f"[DONE] success={success}, failed={failed}, elapsed={fmt_seconds(elapsed)}")


if __name__ == "__main__":
    main()
