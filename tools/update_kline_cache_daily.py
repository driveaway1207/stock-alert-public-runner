# -*- coding: utf-8 -*-
"""
A股K线缓存每日增量更新脚本

用途：
1. 只更新 kline_cache/*.csv 的最近增量K线。
2. 不做全历史回补，不做选股，不改模型。
3. 每天一号员工推送前先运行，确保尽量拿到当天收盘后的最新日K。
4. 自动处理成交量单位：尽量保持每只股票原缓存的 volume 单位不被新数据源破坏。

缓存口径：
- 价格：前复权日K
- 字段：date, open, high, low, close, volume, amount
- 路径：kline_cache/000001.csv
"""

import os
import json
import time
import signal
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

try:
    import akshare as ak
except Exception:
    ak = None

try:
    import baostock as bs
except Exception:
    bs = None


KLINE_CACHE_DIR = "kline_cache"
OUT_DIR = "outputs"
os.makedirs(KLINE_CACHE_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))
STALE_LOOKBACK_DAYS = int(os.getenv("STALE_LOOKBACK_DAYS", "15"))
MAX_RUNTIME_MINUTES = int(os.getenv("DAILY_UPDATE_MAX_RUNTIME_MINUTES", "45"))
SOFT_STOP_BUFFER_MINUTES = int(os.getenv("DAILY_UPDATE_SOFT_STOP_BUFFER_MINUTES", "5"))
PROGRESS_EVERY = int(os.getenv("PROGRESS_EVERY", "300"))

EASTMONEY_TIMEOUT = int(os.getenv("EASTMONEY_TIMEOUT", "12"))
AKSHARE_TIMEOUT = int(os.getenv("AKSHARE_TIMEOUT", "18"))
BAOSTOCK_TIMEOUT = int(os.getenv("BAOSTOCK_TIMEOUT", "20"))

# 每日增量优先 EastMoney/AkShare，速度快；成交量单位会按旧缓存口径转换。
SOURCE_ORDER = [s.strip() for s in os.getenv("DAILY_SOURCE_ORDER", "EastMoney,AkShare,BaoStock").split(",") if s.strip()]

BAOSTOCK_READY = False


class FetchTimeoutError(Exception):
    pass


@contextmanager
def time_limit(seconds, label="operation"):
    if seconds <= 0:
        yield
        return

    def signal_handler(signum, frame):
        raise FetchTimeoutError(f"{label} timeout after {seconds}s")

    old_handler = signal.signal(signal.SIGALRM, signal_handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def log(msg):
    print(msg, flush=True)


def cn_now():
    return datetime.now(timezone.utc) + timedelta(hours=8)


def cn_today_date():
    return cn_now().date()


def today_str():
    return cn_today_date().strftime("%Y-%m-%d")


def today_yyyymmdd():
    return cn_today_date().strftime("%Y%m%d")


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


def progress_bar(done, total, width=24):
    ratio = 0 if total <= 0 else min(max(done / total, 0), 1)
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled) + f" {ratio * 100:6.2f}%"


def elapsed_seconds(start_ts):
    return time.time() - start_ts


def should_soft_stop(start_ts):
    budget = MAX_RUNTIME_MINUTES * 60
    buffer_sec = SOFT_STOP_BUFFER_MINUTES * 60
    return elapsed_seconds(start_ts) >= max(60, budget - buffer_sec)


def stock_code(code):
    return str(code).zfill(6)


def market_prefix_code(code):
    code = stock_code(code)
    return ("sh." if code.startswith("6") else "sz.") + code


def eastmoney_secid(code):
    code = stock_code(code)
    return ("1." if code.startswith("6") else "0.") + code


def cache_path(code):
    return os.path.join(KLINE_CACHE_DIR, f"{stock_code(code)}.csv")


def normalize_daily_columns(df):
    if df is None or df.empty:
        return None

    rename_map = {
        "日期": "date", "交易日期": "date", "trade_date": "date", "Date": "date", "datetime": "date", "time": "date",
        "开盘": "open", "开盘价": "open", "Open": "open",
        "收盘": "close", "收盘价": "close", "Close": "close",
        "最高": "high", "最高价": "high", "High": "high",
        "最低": "low", "最低价": "low", "Low": "low",
        "成交量": "volume", "成交量(手)": "volume", "vol": "volume", "Volume": "volume",
        "成交额": "amount", "成交额(元)": "amount", "turnover": "amount", "Amount": "amount",
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
        log(f"[WARN] read cache failed code={code} err={repr(e)}")
        return None


def save_cache(code, df):
    df = normalize_daily_columns(df)
    if df is None or df.empty:
        return False
    try:
        df.to_csv(cache_path(code), index=False, encoding="utf-8-sig")
        return True
    except Exception as e:
        log(f"[WARN] save cache failed code={code} err={repr(e)}")
        return False


def merge_kline(old_df, new_df):
    old_df = normalize_daily_columns(old_df)
    new_df = normalize_daily_columns(new_df)
    if old_df is None or old_df.empty:
        return new_df
    if new_df is None or new_df.empty:
        return old_df
    return normalize_daily_columns(pd.concat([old_df, new_df], ignore_index=True))


def infer_existing_volume_unit(df):
    """
    用 amount / (volume * close) 推断旧缓存 volume 单位。
    约 1：股；约 100：手。
    """
    if df is None or df.empty:
        return "unknown"

    tmp = df.copy()
    tmp = tmp[(tmp["volume"] > 0) & (tmp["amount"] > 0) & (tmp["close"] > 0)].copy()
    if len(tmp) < 60:
        return "unknown"

    ratio = tmp["amount"] / (tmp["volume"] * tmp["close"])
    ratio = ratio.replace([float("inf"), -float("inf")], pd.NA).dropna()
    ratio = ratio[(ratio > 0) & (ratio < 100000)]
    if len(ratio) < 60:
        return "unknown"

    med = float(ratio.median())
    if 0.5 <= med <= 2:
        return "share"
    if 50 <= med <= 200:
        return "lot"
    return "unknown"


def convert_volume_unit(new_df, source, target_unit):
    """
    尽量保持新数据 volume 单位与旧缓存一致。
    EastMoney / AkShare A股历史成交量通常按“手”。
    BaoStock 通常按“股”。
    """
    df = normalize_daily_columns(new_df)
    if df is None or df.empty:
        return df

    source_unit = "unknown"
    if source in ("EastMoney", "AkShare"):
        source_unit = "lot"
    elif source == "BaoStock":
        source_unit = "share"

    if target_unit == "unknown" or source_unit == "unknown" or target_unit == source_unit:
        return df

    if source_unit == "lot" and target_unit == "share":
        df["volume"] = df["volume"] * 100
    elif source_unit == "share" and target_unit == "lot":
        df["volume"] = df["volume"] / 100

    return df


def fetch_daily_from_eastmoney(code, beg_yyyymmdd, end_yyyymmdd):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": eastmoney_secid(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "101",
        "fqt": "1",
        "beg": beg_yyyymmdd,
        "end": end_yyyymmdd,
        "lmt": "1000000",
    }
    r = requests.get(
        url,
        params=params,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
            "Accept": "application/json,text/plain,*/*",
        },
        timeout=EASTMONEY_TIMEOUT,
    )
    r.raise_for_status()
    js = r.json()
    klines = (js.get("data") or {}).get("klines") or []
    rows = []
    for item in klines:
        p = item.split(",")
        if len(p) >= 7:
            rows.append({
                "date": p[0], "open": p[1], "close": p[2], "high": p[3], "low": p[4],
                "volume": p[5], "amount": p[6],
            })
    return normalize_daily_columns(pd.DataFrame(rows))


def fetch_daily_from_akshare(code, beg_yyyymmdd, end_yyyymmdd):
    if ak is None:
        return None
    with time_limit(AKSHARE_TIMEOUT, f"akshare {code}"):
        df = ak.stock_zh_a_hist(
            symbol=stock_code(code),
            period="daily",
            start_date=beg_yyyymmdd,
            end_date=end_yyyymmdd,
            adjust="qfq",
        )
    return normalize_daily_columns(df)


def baostock_login_once():
    global BAOSTOCK_READY
    if BAOSTOCK_READY:
        return True
    if bs is None:
        return False
    lg = bs.login()
    log(f"[BAOSTOCK LOGIN] {lg.error_code} {lg.error_msg}")
    if lg.error_code == "0":
        BAOSTOCK_READY = True
        return True
    return False


def baostock_logout_once():
    global BAOSTOCK_READY
    if BAOSTOCK_READY and bs is not None:
        try:
            bs.logout()
            log("[BAOSTOCK LOGOUT] success")
        except Exception as e:
            log(f"[WARN] baostock logout failed: {repr(e)}")
    BAOSTOCK_READY = False


def fetch_daily_from_baostock(code, beg_yyyy_mm_dd, end_yyyy_mm_dd):
    if bs is None:
        return None
    if not baostock_login_once():
        return None
    with time_limit(BAOSTOCK_TIMEOUT, f"baostock {code}"):
        rs = bs.query_history_k_data_plus(
            market_prefix_code(code),
            "date,open,high,low,close,volume,amount",
            start_date=beg_yyyy_mm_dd,
            end_date=end_yyyy_mm_dd,
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


def fetch_incremental_window(code, begin_date, end_date, target_unit):
    beg_yyyymmdd = begin_date.strftime("%Y%m%d")
    end_yyyymmdd = end_date.strftime("%Y%m%d")
    beg_dash = begin_date.strftime("%Y-%m-%d")
    end_dash = end_date.strftime("%Y-%m-%d")

    errors = []

    for source in SOURCE_ORDER:
        try:
            t0 = time.time()
            if source == "EastMoney":
                df = fetch_daily_from_eastmoney(code, beg_yyyymmdd, end_yyyymmdd)
            elif source == "AkShare":
                df = fetch_daily_from_akshare(code, beg_yyyymmdd, end_yyyymmdd)
            elif source == "BaoStock":
                df = fetch_daily_from_baostock(code, beg_dash, end_dash)
            else:
                continue

            if df is not None and not df.empty:
                df = convert_volume_unit(df, source, target_unit)
                return df, source, "", fmt_seconds(time.time() - t0)

            errors.append(f"{source}:empty")
        except Exception as e:
            errors.append(f"{source}:{repr(e)}")

    return None, "全部失败", " | ".join(errors), ""


def list_cache_codes():
    codes = []
    if not os.path.exists(KLINE_CACHE_DIR):
        return codes
    for f in os.listdir(KLINE_CACHE_DIR):
        if f.lower().endswith(".csv") and not f.startswith("_"):
            code = f[:6]
            if code.isdigit():
                codes.append(code)
    return sorted(set(codes))


def update_one(code):
    df = read_cache(code)
    if df is None or df.empty:
        return {"code": code, "status": "skip_no_cache", "reason": "缓存不存在或不可读"}

    old_last = pd.to_datetime(df["date"].max()).date()
    end_date = cn_today_date()

    # 今天已经有K线，跳过。
    if old_last >= end_date:
        return {
            "code": code,
            "status": "already_today",
            "old_last": old_last.strftime("%Y-%m-%d"),
            "new_last": old_last.strftime("%Y-%m-%d"),
            "source": "cache",
            "rows": len(df),
        }

    gap_days = (end_date - old_last).days
    lookback = STALE_LOOKBACK_DAYS if gap_days > LOOKBACK_DAYS else LOOKBACK_DAYS
    begin_date = old_last - timedelta(days=lookback)

    target_unit = infer_existing_volume_unit(df)
    new_df, source, err, cost = fetch_incremental_window(code, begin_date, end_date, target_unit)

    if new_df is None or new_df.empty:
        return {
            "code": code,
            "status": "fetch_failed",
            "old_last": old_last.strftime("%Y-%m-%d"),
            "new_last": old_last.strftime("%Y-%m-%d"),
            "source": source,
            "reason": err,
            "rows": len(df),
        }

    merged = merge_kline(df, new_df)
    if merged is None or merged.empty:
        return {
            "code": code,
            "status": "merge_failed",
            "old_last": old_last.strftime("%Y-%m-%d"),
            "new_last": old_last.strftime("%Y-%m-%d"),
            "source": source,
            "reason": "合并后为空",
            "rows": len(df),
        }

    new_last = pd.to_datetime(merged["date"].max()).date()
    ok = save_cache(code, merged)
    if not ok:
        return {
            "code": code,
            "status": "save_failed",
            "old_last": old_last.strftime("%Y-%m-%d"),
            "new_last": new_last.strftime("%Y-%m-%d"),
            "source": source,
            "reason": "保存失败",
            "rows": len(merged),
        }

    if new_last > old_last:
        status = "updated"
    else:
        status = "refreshed_no_new_date"

    return {
        "code": code,
        "status": status,
        "old_last": old_last.strftime("%Y-%m-%d"),
        "new_last": new_last.strftime("%Y-%m-%d"),
        "source": source,
        "reason": "",
        "cost": cost,
        "target_volume_unit": target_unit,
        "rows": len(merged),
    }


def main():
    start_ts = time.time()
    today = today_str()
    codes = list_cache_codes()
    total = len(codes)

    log("========== A股K线缓存每日增量更新 ==========")
    log(f"[日期] 北京时间 today={today}")
    log(f"[缓存] stock csv count={total}")
    log(f"[窗口] 默认回退 {LOOKBACK_DAYS} 自然日；陈旧缓存回退 {STALE_LOOKBACK_DAYS} 自然日")
    log(f"[数据源顺序] {SOURCE_ORDER}")
    log(f"[时间保护] 最多 {MAX_RUNTIME_MINUTES} 分钟，提前 {SOFT_STOP_BUFFER_MINUTES} 分钟收尾")
    log("==========================================")

    rows = []
    stat = {
        "total": total,
        "processed": 0,
        "updated": 0,
        "already_today": 0,
        "refreshed_no_new_date": 0,
        "failed": 0,
        "skip_no_cache": 0,
        "stopped_by_time": False,
    }

    for idx, code in enumerate(codes, start=1):
        if should_soft_stop(start_ts):
            stat["stopped_by_time"] = True
            log("[时间保护] 即将到达时间上限，开始安全收尾。")
            break

        result = update_one(code)
        rows.append(result)
        stat["processed"] += 1

        status = result.get("status", "unknown")
        if status == "updated":
            stat["updated"] += 1
        elif status == "already_today":
            stat["already_today"] += 1
        elif status == "refreshed_no_new_date":
            stat["refreshed_no_new_date"] += 1
        elif status == "skip_no_cache":
            stat["skip_no_cache"] += 1
        elif "failed" in status:
            stat["failed"] += 1

        if idx <= 20 or idx % PROGRESS_EVERY == 0 or idx == total:
            log(
                f"[进度] {idx}/{total} {progress_bar(idx,total)} | "
                f"code={code} status={status} source={result.get('source','')} "
                f"old={result.get('old_last','')} new={result.get('new_last','')}"
            )

        time.sleep(0.001)

    result_df = pd.DataFrame(rows)

    # 统计更新后全局最新日期覆盖情况
    latest_dates = []
    for code in codes:
        df = read_cache(code)
        if df is not None and not df.empty:
            latest_dates.append(pd.to_datetime(df["date"].max()).strftime("%Y-%m-%d"))

    latest_series = pd.Series(latest_dates, dtype="object")
    max_latest = latest_series.max() if not latest_series.empty else ""
    max_latest_count = int((latest_series == max_latest).sum()) if max_latest else 0

    summary = {
        "生成日期": today,
        "缓存股票数": total,
        "本轮处理数": stat["processed"],
        "已更新到新日期数量": stat["updated"],
        "本来已是今天数量": stat["already_today"],
        "刷新但无新日期数量": stat["refreshed_no_new_date"],
        "失败数量": stat["failed"],
        "无缓存跳过数量": stat["skip_no_cache"],
        "是否时间保护收尾": stat["stopped_by_time"],
        "当前全局最新K日期": max_latest,
        "全局最新K日期覆盖数量": max_latest_count,
        "耗时": fmt_seconds(elapsed_seconds(start_ts)),
    }

    detail_path = os.path.join(OUT_DIR, f"daily_kline_update_detail_{today}.csv")
    summary_path = os.path.join(OUT_DIR, f"daily_kline_update_summary_{today}.csv")
    state_path = os.path.join(OUT_DIR, "daily_kline_update_state.json")

    result_df.to_csv(detail_path, index=False, encoding="utf-8-sig")
    pd.DataFrame([summary]).to_csv(summary_path, index=False, encoding="utf-8-sig")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    log("========== 每日增量更新完成 ==========")
    for k, v in summary.items():
        log(f"{k}: {v}")
    log(f"[输出] {summary_path}")
    log(f"[输出] {detail_path}")

    baostock_logout_once()


if __name__ == "__main__":
    main()
