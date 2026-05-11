# -*- coding: utf-8 -*-
"""
A股K线缓存每日增量更新 V2

用途：
1. 读取 kline_cache/xxxxxx.csv
2. 每只股票只补最近几天K线，不做全历史回补
3. 防止单只股票卡死
4. 输出每日增量更新报告
5. 更新完成后供一号员工 stock_alert.py 读取最新缓存

核心原则：
- 不重建全历史
- 不筛股
- 不评分
- 不推送
- 只负责把缓存尽量更新到最新交易日
"""

import os
import time
import json
import signal
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta

import requests
import pandas as pd
import baostock as bs


KLINE_CACHE_DIR = "kline_cache"
OUT_DIR = "outputs"

os.makedirs(KLINE_CACHE_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))
MAX_RUNTIME_MINUTES = int(os.getenv("MAX_RUNTIME_MINUTES", "40"))
SOFT_STOP_BUFFER_MINUTES = int(os.getenv("SOFT_STOP_BUFFER_MINUTES", "5"))

PER_STOCK_TIMEOUT = int(os.getenv("PER_STOCK_TIMEOUT", "25"))
PROGRESS_EVERY = int(os.getenv("PROGRESS_EVERY", "25"))

BAOSTOCK_READY = False


class TimeoutError(Exception):
    pass


@contextmanager
def time_limit(seconds, label="operation"):
    def handler(signum, frame):
        raise TimeoutError(f"{label} timeout after {seconds}s")

    old_handler = signal.signal(signal.SIGALRM, handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


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


def progress_bar(done, total, width=26):
    if total <= 0:
        ratio = 0
    else:
        ratio = min(max(done / total, 0), 1)
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)


def stock_code(code):
    code = str(code).zfill(6)
    return ("SH." if code.startswith("6") else "SZ.") + code


def baostock_code(code):
    code = str(code).zfill(6)
    return ("sh." if code.startswith("6") else "sz.") + code


def eastmoney_secid(code):
    code = str(code).zfill(6)
    return ("1." if code.startswith("6") else "0.") + code


def cache_path(code):
    return os.path.join(KLINE_CACHE_DIR, f"{str(code).zfill(6)}.csv")


def should_soft_stop(start_ts):
    elapsed = time.time() - start_ts
    limit = MAX_RUNTIME_MINUTES * 60
    buffer_sec = SOFT_STOP_BUFFER_MINUTES * 60
    return elapsed >= max(60, limit - buffer_sec)


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

    required = ["date", "open", "high", "low", "close", "volume"]
    if not all(c in df.columns for c in required):
        return None

    if "amount" not in df.columns:
        df["amount"] = 0

    df = df[["date", "open", "high", "low", "close", "volume", "amount"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date"])
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)

    if df.empty:
        return None

    return df


def read_cache(code):
    path = cache_path(code)
    if not os.path.exists(path):
        return None

    try:
        return normalize_daily_columns(pd.read_csv(path))
    except Exception as e:
        log(f"[读取缓存失败] code={code} err={repr(e)}")
        return None


def atomic_save_cache(code, df):
    df = normalize_daily_columns(df)
    if df is None or df.empty:
        return False

    path = cache_path(code)
    tmp_path = path + ".tmp"

    try:
        df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        log(f"[保存缓存失败] code={code} err={repr(e)}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def merge_kline(old_df, new_df):
    old_df = normalize_daily_columns(old_df)
    new_df = normalize_daily_columns(new_df)

    if old_df is None or old_df.empty:
        return new_df
    if new_df is None or new_df.empty:
        return old_df

    df = pd.concat([old_df, new_df], ignore_index=True)
    return normalize_daily_columns(df)


def get_last_date(df):
    if df is None or df.empty:
        return None
    return pd.to_datetime(df["date"].max()).date()


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
            log(f"[BAOSTOCK LOGOUT WARN] {repr(e)}")

    BAOSTOCK_READY = False


def fetch_increment_from_baostock(code, start_date, end_date):
    if not baostock_login_once():
        return None

    with time_limit(PER_STOCK_TIMEOUT, f"baostock {code}"):
        rs = bs.query_history_k_data_plus(
            baostock_code(code),
            "date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=end_date,
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


def fetch_increment_from_eastmoney(code, start_date, end_date):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    params = {
        "secid": eastmoney_secid(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "101",
        "fqt": "1",
        "beg": start_date.replace("-", ""),
        "end": end_date.replace("-", ""),
        "lmt": "1000",
    }

    with time_limit(PER_STOCK_TIMEOUT, f"eastmoney {code}"):
        r = requests.get(
            url,
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/",
            },
            timeout=PER_STOCK_TIMEOUT,
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

    if not rows:
        return None

    return normalize_daily_columns(pd.DataFrame(rows))


def list_cache_codes():
    codes = []
    for f in os.listdir(KLINE_CACHE_DIR):
        if f.lower().endswith(".csv") and not f.startswith("_"):
            code = f[:6]
            if code.isdigit():
                codes.append(code)
    return sorted(set(codes))


def update_one_stock(code):
    old_df = read_cache(code)

    if old_df is None or old_df.empty:
        return {
            "code": code,
            "股票代码": stock_code(code),
            "status": "no_cache",
            "old_last_date": "",
            "new_last_date": "",
            "source": "",
            "rows_before": 0,
            "rows_after": 0,
            "note": "缓存不存在或不可读，每日增量不做全历史重建",
        }

    old_last = get_last_date(old_df)
    if old_last is None:
        return {
            "code": code,
            "股票代码": stock_code(code),
            "status": "bad_cache",
            "old_last_date": "",
            "new_last_date": "",
            "source": "",
            "rows_before": len(old_df),
            "rows_after": len(old_df),
            "note": "缓存无有效日期",
        }

    start_date = old_last - timedelta(days=LOOKBACK_DAYS)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today_str()

    source_used = ""
    new_df = None
    err_notes = []

    try:
        new_df = fetch_increment_from_baostock(code, start_str, end_str)
        if new_df is not None and not new_df.empty:
            source_used = "BaoStock"
    except Exception as e:
        err_notes.append(f"BaoStock失败:{repr(e)}")

    if new_df is None or new_df.empty:
        try:
            new_df = fetch_increment_from_eastmoney(code, start_str, end_str)
            if new_df is not None and not new_df.empty:
                source_used = "EastMoney"
        except Exception as e:
            err_notes.append(f"EastMoney失败:{repr(e)}")

    if new_df is None or new_df.empty:
        return {
            "code": code,
            "股票代码": stock_code(code),
            "status": "stale_or_suspended",
            "old_last_date": old_last.strftime("%Y-%m-%d"),
            "new_last_date": old_last.strftime("%Y-%m-%d"),
            "source": "none",
            "rows_before": len(old_df),
            "rows_after": len(old_df),
            "note": "增量源无新数据，可能停牌/未交易/数据源未同步；" + "；".join(err_notes[:2]),
        }

    merged = merge_kline(old_df, new_df)
    if merged is None or merged.empty:
        return {
            "code": code,
            "股票代码": stock_code(code),
            "status": "merge_failed",
            "old_last_date": old_last.strftime("%Y-%m-%d"),
            "new_last_date": old_last.strftime("%Y-%m-%d"),
            "source": source_used,
            "rows_before": len(old_df),
            "rows_after": len(old_df),
            "note": "增量合并失败",
        }

    new_last = get_last_date(merged)

    if not atomic_save_cache(code, merged):
        return {
            "code": code,
            "股票代码": stock_code(code),
            "status": "save_failed",
            "old_last_date": old_last.strftime("%Y-%m-%d"),
            "new_last_date": new_last.strftime("%Y-%m-%d") if new_last else "",
            "source": source_used,
            "rows_before": len(old_df),
            "rows_after": len(merged),
            "note": "写入缓存失败",
        }

    if new_last and new_last > old_last:
        status = "updated"
    else:
        status = "fresh_or_no_new_bar"

    return {
        "code": code,
        "股票代码": stock_code(code),
        "status": status,
        "old_last_date": old_last.strftime("%Y-%m-%d"),
        "new_last_date": new_last.strftime("%Y-%m-%d") if new_last else "",
        "source": source_used,
        "rows_before": len(old_df),
        "rows_after": len(merged),
        "note": "",
    }


def main():
    start_ts = time.time()
    today = today_str()

    codes = list_cache_codes()
    total = len(codes)

    log("========== A股K线缓存每日增量更新 V2 ==========")
    log(f"[配置] LOOKBACK_DAYS={LOOKBACK_DAYS}, MAX_RUNTIME_MINUTES={MAX_RUNTIME_MINUTES}, PER_STOCK_TIMEOUT={PER_STOCK_TIMEOUT}")
    log(f"[缓存股票数] {total}")
    log("=============================================")

    rows = []
    updated = 0
    fresh = 0
    stale = 0
    failed = 0
    no_cache = 0
    stopped_by_time = False

    for idx, code in enumerate(codes, start=1):
        if should_soft_stop(start_ts):
            stopped_by_time = True
            log("[时间保护] 接近本轮时间上限，安全收尾。")
            break

        t0 = time.time()

        try:
            result = update_one_stock(code)
        except Exception as e:
            result = {
                "code": code,
                "股票代码": stock_code(code),
                "status": "exception",
                "old_last_date": "",
                "new_last_date": "",
                "source": "",
                "rows_before": "",
                "rows_after": "",
                "note": repr(e),
            }
            traceback.print_exc(limit=1)

        rows.append(result)

        status = result.get("status", "")

        if status == "updated":
            updated += 1
        elif status == "fresh_or_no_new_bar":
            fresh += 1
        elif status == "stale_or_suspended":
            stale += 1
        elif status == "no_cache":
            no_cache += 1
        else:
            failed += 1

        if idx <= 20 or idx % PROGRESS_EVERY == 0 or idx == total:
            elapsed = time.time() - start_ts
            log(
                f"[进度] {idx}/{total} {progress_bar(idx, total)} "
                f"{idx/total*100:.2f}% | "
                f"code={code} status={status} source={result.get('source','')} "
                f"old={result.get('old_last_date','')} new={result.get('new_last_date','')} "
                f"cost={fmt_seconds(time.time() - t0)} | "
                f"updated={updated} fresh={fresh} stale={stale} failed={failed}"
            )

        time.sleep(0.002)

    df = pd.DataFrame(rows)

    out_path = os.path.join(OUT_DIR, f"daily_kline_update_report_{today}.csv")
    summary_path = os.path.join(OUT_DIR, f"daily_kline_update_summary_{today}.csv")
    state_path = os.path.join(OUT_DIR, "daily_kline_update_state.json")

    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    summary = {
        "日期": today,
        "缓存股票数": total,
        "本轮处理数": len(rows),
        "updated_成功更新数": updated,
        "fresh_本来新鲜或无新K数": fresh,
        "stale_疑似停牌或无新数据数": stale,
        "no_cache_无缓存数": no_cache,
        "failed_失败数": failed,
        "stopped_by_time": stopped_by_time,
        "elapsed": fmt_seconds(time.time() - start_ts),
    }

    pd.DataFrame([summary]).to_csv(summary_path, index=False, encoding="utf-8-sig")

    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    log("========== 每日增量更新完成 ==========")
    for k, v in summary.items():
        log(f"{k}: {v}")

    log(f"[输出] {out_path}")
    log(f"[输出] {summary_path}")
    log(f"[输出] {state_path}")

    baostock_logout_once()


if __name__ == "__main__":
    try:
        main()
    finally:
        baostock_logout_once()
