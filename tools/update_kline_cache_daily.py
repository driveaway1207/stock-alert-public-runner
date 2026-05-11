# -*- coding: utf-8 -*-
"""
A股K线缓存每日增量更新 V3：快速新鲜度闸门 + 快照优先 + 单票防卡死

目标：
1. 每天一号员工运行前，先确认/更新最新交易日K线。
2. 已经是目标交易日的股票直接跳过，不联网。
3. 优先用全市场快照一次性补当天K线；失败/缺失再少量逐股兜底。
4. 识别疑似停牌/无新K，不把它当缓存损坏。
5. 输出人能看懂的进度、预计剩余时间、覆盖率与是否允许一号员工运行。

重要口径：
- 缓存价格：前复权 OHLC。
- 缓存字段：date, open, high, low, close, volume, amount。
- 每日快照通常来自东方财富现价快照，不用于全历史修复，只补目标交易日一根K。
- 成交量单位会按旧缓存推断：旧缓存疑似“股”则快照成交量 *100；旧缓存疑似“手”则保持不变。
"""

import os
import time
import json
import signal
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from collections import Counter

import requests
import pandas as pd
import baostock as bs

try:
    import akshare as ak
except Exception:
    ak = None


KLINE_CACHE_DIR = "kline_cache"
OUT_DIR = "outputs"
os.makedirs(KLINE_CACHE_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))
MAX_RUNTIME_MINUTES = int(os.getenv("MAX_RUNTIME_MINUTES", "45"))
SOFT_STOP_BUFFER_MINUTES = int(os.getenv("SOFT_STOP_BUFFER_MINUTES", "5"))
PER_STOCK_TIMEOUT = int(os.getenv("PER_STOCK_TIMEOUT", "18"))
PROGRESS_EVERY = int(os.getenv("PROGRESS_EVERY", "100"))
MIN_FRESH_COVERAGE = float(os.getenv("MIN_FRESH_COVERAGE", "0.965"))
ALLOW_STOCK_ALERT_IF_STALE_ONLY = os.getenv("ALLOW_STOCK_ALERT_IF_STALE_ONLY", "1") == "1"

BAOSTOCK_READY = False

SAMPLE_CODES = [
    "000001", "000002", "000333", "000651", "000858",
    "600000", "600036", "600519", "601318", "300750",
]


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


def progress_bar(done, total, width=30):
    ratio = min(max(done / max(total, 1), 0), 1)
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
    return df if not df.empty else None


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
    return normalize_daily_columns(pd.concat([old_df, new_df], ignore_index=True))


def get_last_date(df):
    if df is None or df.empty:
        return None
    return pd.to_datetime(df["date"].max()).date()


def list_cache_codes():
    codes = []
    for f in os.listdir(KLINE_CACHE_DIR):
        if f.lower().endswith(".csv") and not f.startswith("_"):
            code = f[:6]
            if code.isdigit():
                codes.append(code)
    return sorted(set(codes))


def infer_cache_volume_unit(df):
    """返回 hand/share/unknown。"""
    if df is None or df.empty:
        return "unknown"
    tmp = df[(df["volume"] > 0) & (df["amount"] > 0) & (df["close"] > 0)].tail(120).copy()
    if len(tmp) < 20:
        return "unknown"
    ratio = tmp["amount"] / (tmp["volume"] * tmp["close"])
    ratio = ratio.replace([float("inf"), -float("inf")], pd.NA).dropna()
    ratio = ratio[(ratio > 0) & (ratio < 100000)]
    if len(ratio) < 20:
        return "unknown"
    med = float(ratio.median())
    if 50 <= med <= 200:
        return "hand"
    if 0.5 <= med <= 2:
        return "share"
    return "unknown"


def baostock_login_once():
    global BAOSTOCK_READY
    if BAOSTOCK_READY:
        return True
    lg = bs.login()
    log(f"[BAOSTOCK LOGIN] {lg.error_code} {lg.error_msg}")
    if lg.error_code == "0":
        BAOSTOCK_READY = True
        return True
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


def fetch_baostock_window(code, start_date, end_date):
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
        rows, fields = [], rs.fields
        while rs.next():
            rows.append(rs.get_row_data())
    if not rows:
        return None
    return normalize_daily_columns(pd.DataFrame(rows, columns=fields))


def fetch_eastmoney_window(code, start_date, end_date):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": eastmoney_secid(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "101", "fqt": "1",
        "beg": start_date.replace("-", ""),
        "end": end_date.replace("-", ""),
        "lmt": "1000",
    }
    with time_limit(PER_STOCK_TIMEOUT, f"eastmoney {code}"):
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}, timeout=PER_STOCK_TIMEOUT)
        r.raise_for_status()
        js = r.json()
    klines = (js.get("data") or {}).get("klines") or []
    rows = []
    for item in klines:
        p = item.split(",")
        if len(p) >= 7:
            rows.append({"date": p[0], "open": p[1], "close": p[2], "high": p[3], "low": p[4], "volume": p[5], "amount": p[6]})
    return normalize_daily_columns(pd.DataFrame(rows)) if rows else None


def scan_cache_last_dates(codes):
    rows = []
    for code in codes:
        df = read_cache(code)
        if df is None or df.empty:
            rows.append({"code": code, "last_date": None, "rows": 0})
        else:
            ld = get_last_date(df)
            rows.append({"code": code, "last_date": ld, "rows": len(df)})
    return rows


def determine_target_trade_date(cache_last_rows):
    """先用样本K线探测数据源最新交易日；失败则用缓存中出现最多/最大最新日兜底。"""
    today = datetime.now().date()
    start = (today - timedelta(days=LOOKBACK_DAYS + 10)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    detected = []

    log("[目标交易日] 正在用样本股票探测数据源最新K线日期...")
    for code in SAMPLE_CODES:
        try:
            df = fetch_baostock_window(code, start, end)
            if df is not None and not df.empty:
                ld = get_last_date(df)
                if ld:
                    detected.append(ld)
                    log(f"[目标交易日样本] {code} BaoStock latest={ld}")
                    continue
        except Exception as e:
            log(f"[目标交易日样本] {code} BaoStock失败 {repr(e)}")
        try:
            df = fetch_eastmoney_window(code, start, end)
            if df is not None and not df.empty:
                ld = get_last_date(df)
                if ld:
                    detected.append(ld)
                    log(f"[目标交易日样本] {code} EastMoney latest={ld}")
        except Exception as e:
            log(f"[目标交易日样本] {code} EastMoney失败 {repr(e)}")

    cache_dates = [r["last_date"] for r in cache_last_rows if r.get("last_date")]
    cache_majority = None
    cache_max = None
    if cache_dates:
        cache_majority = Counter(cache_dates).most_common(1)[0][0]
        cache_max = max(cache_dates)

    if detected:
        target = max(detected)
        method = "sample_probe"
    elif cache_max:
        target = cache_max
        method = "cache_max_fallback"
    else:
        target = today
        method = "today_fallback"

    log(f"[目标交易日] target={target} method={method} cache_majority={cache_majority} cache_max={cache_max}")
    return target, method, cache_majority, cache_max


def build_snapshot_map(target_date):
    """用AkShare东方财富快照一次性拿全市场当天OHLCV。失败返回空。"""
    if ak is None:
        log("[快照] akshare不可用，跳过快照通道。")
        return {}
    try:
        log("[快照] 正在拉取全市场实时/收盘快照 stock_zh_a_spot_em...")
        with time_limit(60, "akshare spot snapshot"):
            spot = ak.stock_zh_a_spot_em()
        if spot is None or spot.empty:
            log("[快照] 返回空。")
            return {}
        rename = {
            "代码": "code", "名称": "name", "最新价": "close", "今开": "open", "最高": "high", "最低": "low",
            "成交量": "volume", "成交额": "amount",
        }
        spot = spot.rename(columns={c: rename.get(c, c) for c in spot.columns})
        required = ["code", "open", "high", "low", "close", "volume", "amount"]
        if not all(c in spot.columns for c in required):
            log(f"[快照] 字段不完整 columns={list(spot.columns)}")
            return {}
        spot["code"] = spot["code"].astype(str).str.zfill(6)
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            spot[col] = pd.to_numeric(spot[col], errors="coerce")
        spot = spot.dropna(subset=["open", "high", "low", "close"])
        spot = spot[(spot["open"] > 0) & (spot["high"] > 0) & (spot["low"] > 0) & (spot["close"] > 0)]
        mp = {}
        for _, r in spot.iterrows():
            code = str(r["code"]).zfill(6)
            mp[code] = {
                "date": pd.to_datetime(target_date),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),  # 东方财富快照通常为手，后面按旧缓存单位转换
                "amount": float(r["amount"]),
            }
        log(f"[快照] 可用股票数={len(mp)}")
        return mp
    except Exception as e:
        log(f"[快照] 失败：{repr(e)}")
        return {}


def update_by_snapshot(code, old_df, target_date, row):
    unit = infer_cache_volume_unit(old_df)
    new_row = dict(row)
    if unit == "share":
        new_row["volume"] = new_row["volume"] * 100.0
    elif unit == "unknown":
        return None, "unknown_volume_unit"

    one = pd.DataFrame([new_row])
    merged = merge_kline(old_df, one)
    if merged is None or merged.empty:
        return None, "merge_failed"
    return merged, "ok"


def update_one_by_network(code, old_df, target_date):
    old_last = get_last_date(old_df)
    start_date = old_last - timedelta(days=LOOKBACK_DAYS)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = target_date.strftime("%Y-%m-%d")
    notes = []
    for source_name, func in [("BaoStock", fetch_baostock_window), ("EastMoney", fetch_eastmoney_window)]:
        try:
            df_new = func(code, start_str, end_str)
            if df_new is not None and not df_new.empty:
                merged = merge_kline(old_df, df_new)
                return merged, source_name, ""
        except Exception as e:
            notes.append(f"{source_name}:{repr(e)}")
    return None, "none", "；".join(notes[:2])


def main():
    start_ts = time.time()
    today = today_str()
    log("========== A股K线缓存每日增量更新 V3 ==========")
    log(f"[配置] LOOKBACK_DAYS={LOOKBACK_DAYS}, MAX_RUNTIME_MINUTES={MAX_RUNTIME_MINUTES}, PER_STOCK_TIMEOUT={PER_STOCK_TIMEOUT}, MIN_FRESH_COVERAGE={MIN_FRESH_COVERAGE}")

    codes = list_cache_codes()
    total = len(codes)
    log(f"[缓存股票数] {total}")

    cache_rows = scan_cache_last_dates(codes)
    target_date, target_method, cache_majority, cache_max = determine_target_trade_date(cache_rows)

    old_map = {r["code"]: r for r in cache_rows}
    already_fresh = [c for c in codes if old_map.get(c, {}).get("last_date") and old_map[c]["last_date"] >= target_date]
    need_update = [c for c in codes if not old_map.get(c, {}).get("last_date") or old_map[c]["last_date"] < target_date]

    log("---------- 新鲜度预判 ----------")
    log(f"目标交易日: {target_date}")
    log(f"已是目标交易日，直接跳过联网: {len(already_fresh)}")
    log(f"需要尝试增量更新: {len(need_update)}")
    log("--------------------------------")

    snapshot_map = {}
    if need_update:
        snapshot_map = build_snapshot_map(target_date)

    rows = []
    updated = 0
    skipped_fresh = 0
    snapshot_updated = 0
    network_updated = 0
    stale = 0
    failed = 0
    no_cache = 0
    stopped_by_time = False

    # 先记录直接新鲜跳过的票，不联网
    for c in already_fresh:
        r = old_map[c]
        rows.append({
            "code": c, "股票代码": stock_code(c), "status": "fresh_skip_no_network",
            "old_last_date": r["last_date"].strftime("%Y-%m-%d"), "new_last_date": r["last_date"].strftime("%Y-%m-%d"),
            "source": "cache", "rows_before": r.get("rows", ""), "rows_after": r.get("rows", ""), "note": "已是目标交易日，未联网",
        })
        skipped_fresh += 1

    update_count = len(need_update)
    for idx, code in enumerate(need_update, start=1):
        if should_soft_stop(start_ts):
            stopped_by_time = True
            log("[时间保护] 接近本轮时间上限，安全收尾。")
            break
        t0 = time.time()
        old_df = read_cache(code)
        if old_df is None or old_df.empty:
            rows.append({"code": code, "股票代码": stock_code(code), "status": "no_cache", "old_last_date": "", "new_last_date": "", "source": "", "rows_before": 0, "rows_after": 0, "note": "缓存不存在或不可读"})
            no_cache += 1
            status = "no_cache"
            source = ""
            old_last_s = ""
            new_last_s = ""
        else:
            old_last = get_last_date(old_df)
            old_last_s = old_last.strftime("%Y-%m-%d") if old_last else ""
            status = ""
            source = ""
            note = ""
            merged = None
            # 快照优先
            if code in snapshot_map:
                merged, snap_note = update_by_snapshot(code, old_df, target_date, snapshot_map[code])
                if merged is not None:
                    source = "AkShareSnapshot"
                    snapshot_updated += 1
                else:
                    note = f"快照未用:{snap_note}"
            # 逐股兜底
            if merged is None:
                try:
                    merged, source, net_note = update_one_by_network(code, old_df, target_date)
                    if net_note:
                        note = (note + "；" + net_note).strip("；")
                except Exception as e:
                    merged = None
                    note = (note + "；" + repr(e)).strip("；")
                    traceback.print_exc(limit=1)
            if merged is not None and not merged.empty:
                new_last = get_last_date(merged)
                new_last_s = new_last.strftime("%Y-%m-%d") if new_last else ""
                if not atomic_save_cache(code, merged):
                    status = "save_failed"
                    failed += 1
                    new_last_s = old_last_s
                elif new_last and new_last >= target_date and old_last and new_last > old_last:
                    status = "updated_to_target"
                    updated += 1
                    if source != "AkShareSnapshot":
                        network_updated += 1
                elif new_last and new_last >= target_date:
                    status = "fresh_after_check"
                    skipped_fresh += 1
                else:
                    status = "stale_or_suspended"
                    stale += 1
                    note = (note + "；增量后仍未到目标交易日，疑似停牌/无交易/数据源未同步").strip("；")
                rows.append({"code": code, "股票代码": stock_code(code), "status": status, "old_last_date": old_last_s, "new_last_date": new_last_s, "source": source, "rows_before": len(old_df), "rows_after": len(merged), "note": note})
            else:
                status = "stale_or_suspended"
                stale += 1
                rows.append({"code": code, "股票代码": stock_code(code), "status": status, "old_last_date": old_last_s, "new_last_date": old_last_s, "source": source or "none", "rows_before": len(old_df), "rows_after": len(old_df), "note": (note + "；无可用增量，疑似停牌/无交易/数据源未同步").strip("；")})
                new_last_s = old_last_s
        if idx <= 20 or idx % PROGRESS_EVERY == 0 or idx == update_count:
            elapsed = time.time() - start_ts
            avg = elapsed / max(idx, 1)
            eta = avg * max(update_count - idx, 0)
            log(
                f"[增量进度] {idx}/{update_count} {progress_bar(idx, max(update_count,1))} "
                f"{idx/max(update_count,1)*100:.2f}% | code={code} status={status} source={source} "
                f"old={old_last_s} new={new_last_s} cost={fmt_seconds(time.time()-t0)} | "
                f"updated={updated} snapshot={snapshot_updated} network={network_updated} stale={stale} failed={failed} | "
                f"预计剩余={fmt_seconds(eta)}"
            )
        time.sleep(0.002)

    processed = len(rows)
    fresh_like_status = {"fresh_skip_no_network", "updated_to_target", "fresh_after_check"}
    fresh_count = sum(1 for r in rows if r.get("status") in fresh_like_status)
    fresh_coverage = fresh_count / max(total, 1)
    # 如果剩余不是失败，只是停牌/无新K，覆盖率达标即可运行；否则阻断。
    should_run_stock_alert = fresh_coverage >= MIN_FRESH_COVERAGE and failed == 0 and no_cache <= 5
    if ALLOW_STOCK_ALERT_IF_STALE_ONLY and failed <= 5 and fresh_coverage >= MIN_FRESH_COVERAGE:
        should_run_stock_alert = True

    df = pd.DataFrame(rows)
    out_path = os.path.join(OUT_DIR, f"daily_kline_update_report_{today}.csv")
    summary_path = os.path.join(OUT_DIR, f"daily_kline_update_summary_{today}.csv")
    state_path = os.path.join(OUT_DIR, "daily_kline_update_state.json")
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    summary = {
        "日期": today,
        "目标交易日": target_date.strftime("%Y-%m-%d"),
        "目标交易日判断方式": target_method,
        "缓存股票数": total,
        "已新鲜跳过联网数": len(already_fresh),
        "需要尝试更新数": len(need_update),
        "本轮处理记录数": processed,
        "updated_成功更新到目标日数": updated,
        "snapshot_快照更新数": snapshot_updated,
        "network_逐股更新数": network_updated,
        "stale_疑似停牌或无新K数": stale,
        "no_cache_无缓存数": no_cache,
        "failed_失败数": failed,
        "fresh_coverage": round(fresh_coverage, 6),
        "fresh_coverage_pct": f"{fresh_coverage*100:.2f}%",
        "should_run_stock_alert": should_run_stock_alert,
        "stopped_by_time": stopped_by_time,
        "elapsed": fmt_seconds(time.time() - start_ts),
    }
    pd.DataFrame([summary]).to_csv(summary_path, index=False, encoding="utf-8-sig")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    log("========== 每日增量更新 V3 完成 ==========")
    for k, v in summary.items():
        log(f"{k}: {v}")
    log(f"[输出] {out_path}")
    log(f"[输出] {summary_path}")
    log(f"[输出] {state_path}")


if __name__ == "__main__":
    try:
        main()
    finally:
        baostock_logout_once()
