# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import math
import os
import signal
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import akshare as ak
import pandas as pd
import requests

try:
    import baostock as bs
except Exception:
    bs = None

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "employee5_reports"
TARGET_DATE_ENV = os.getenv("EMPLOYEE5_TARGET_DATE", "").strip()
MAX_POOL_SCAN = int(os.getenv("EMPLOYEE5_MAX_STOCKS", "500"))
DEEP_SAMPLE_COUNT = int(os.getenv("EMPLOYEE5_DEEP_SAMPLE_COUNT", "3"))
DEEP_HIST_SCAN_LIMIT = int(os.getenv("EMPLOYEE5_DEEP_HIST_SCAN_LIMIT", "500"))
AK_TIMEOUT_SECONDS = int(os.getenv("EMPLOYEE5_AK_TIMEOUT_SECONDS", "18"))
REQUEST_SLEEP = float(os.getenv("EMPLOYEE5_REQUEST_SLEEP", "0.10"))
PROGRESS_EVERY = int(os.getenv("EMPLOYEE5_PROGRESS_EVERY", "1"))
MA_PERIODS = [5, 10, 20, 30, 60, 100, 250]
RET_WINDOWS = [5, 20, 60, 100, 250]
PERIOD_MEANING = {
    5: "5日线≈5根日K聚合，可理解为周线观察窗口",
    20: "20日线≈20根日K聚合，可理解为月线观察窗口",
    60: "60日线≈60根日K聚合，可理解为季线观察窗口",
    100: "100日线≈100根日K聚合，可理解为中期修复窗口",
    250: "250日线≈250根日K聚合，可理解为年线观察窗口",
}
HIST_FAILURE_SAMPLES: List[Dict[str, str]] = []
BAOSTOCK_LOGGED_IN = False


def env_by_codes(codes: List[int]) -> str:
    return os.getenv("".join(chr(x) for x in codes), "")


_KEY = env_by_codes([84,69,76,69,71,82,65,77,95,66,79,84,95,84,79,75,69,78]) or env_by_codes([84,69,76,69,71,82,65,77,95,84,79,75,69,78])
_DEST = env_by_codes([84,69,76,69,71,82,65,77,95,67,72,65,84,95,73,68])


class AkTimeout(Exception):
    pass


@contextmanager
def timeout_guard(seconds: int, label: str):
    def handler(signum, frame):
        raise AkTimeout(f"{label} timeout {seconds}s")
    old = signal.signal(signal.SIGALRM, handler)
    signal.alarm(max(1, int(seconds)))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        v = float(str(x).replace("%", "").replace(",", ""))
        return default if math.isnan(v) or math.isinf(v) else v
    except Exception:
        return default


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def rd(x: Any, n: int = 2) -> float:
    return round(sf(x), n)


def div(a: Any, b: Any) -> float:
    b = sf(b)
    return sf(a) / b if b else 0.0


def pct(a: Any, b: Any) -> float:
    b = sf(b)
    return (sf(a) / b - 1.0) * 100 if b else 0.0


def first_col(df: pd.DataFrame, cols: Iterable[str]) -> Optional[str]:
    return next((c for c in cols if c in df.columns), None)


def ymd_to_dash(x: str) -> str:
    x = x.replace("-", "")
    return f"{x[:4]}-{x[4:6]}-{x[6:8]}" if len(x) == 8 else x


def fmt_seconds(seconds: float) -> str:
    try:
        seconds = int(max(0, seconds))
    except Exception:
        return "未知"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def progress_bar(done: int, total: int, width: int = 22) -> str:
    if total <= 0:
        return "[" + "░" * width + "]"
    filled = int(width * min(max(done / total, 0), 1))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def write_progress(stage: str, done: int, total: int, start_ts: float, extra: str = "") -> None:
    elapsed = time.time() - start_ts
    speed = done / elapsed if elapsed > 0 and done > 0 else 0.0
    eta = (total - done) / speed if speed > 0 and total >= done else 0.0
    pct_done = done / total * 100 if total else 0.0
    line = f"【五号员工进度】{stage} {progress_bar(done, total)} {done}/{total} ({pct_done:.1f}%) | 已耗时 {fmt_seconds(elapsed)} | 预计剩余 {fmt_seconds(eta)}"
    if speed:
        line += f" | 速度 {speed:.2f}/秒"
    if extra:
        line += f" | {extra}"
    print(line, flush=True)
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "stage": stage,
            "done": done,
            "total": total,
            "percent": round(pct_done, 2),
            "elapsed_seconds": round(elapsed, 2),
            "elapsed_text": fmt_seconds(elapsed),
            "eta_seconds": round(eta, 2),
            "eta_text": fmt_seconds(eta),
            "speed_per_second": round(speed, 4),
            "extra": extra,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        (REPORT_DIR / "employee5_progress.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def latest_weekday(today: datetime) -> str:
    d = today
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def latest_trade_date() -> str:
    if TARGET_DATE_ENV:
        return TARGET_DATE_ENV.replace("-", "")
    today = datetime.now()
    today_ymd = today.strftime("%Y-%m-%d")
    try:
        df = ak.tool_trade_date_hist_sina()
        if df is not None and not df.empty and "trade_date" in df.columns:
            values = [str(x)[:10] for x in df["trade_date"].tolist() if str(x)[:10] <= today_ymd]
            if values:
                return max(values).replace("-", "")
    except Exception as e:
        print(f"trade calendar failed: {type(e).__name__}", flush=True)
    return latest_weekday(today)


def board_limit(code: str, name: str) -> Tuple[str, float]:
    code, name = ss(code).zfill(6), ss(name).upper()
    if "ST" in name:
        return "ST", 5.0
    if code.startswith(("920", "8", "4")):
        return "北交所", 30.0
    if code.startswith(("688", "689")):
        return "科创板", 20.0
    if code.startswith(("300", "301")):
        return "创业板", 20.0
    if code.startswith("002"):
        return "中小板", 10.0
    return "主板", 10.0


def limit_style(limit_pct: float) -> str:
    if limit_pct <= 5:
        return "5cm/ST涨停"
    if limit_pct <= 10:
        return "10cm涨停"
    if limit_pct <= 20:
        return "20cm涨停"
    return "30cm涨停"


def is_limit_up(pct_chg: float, limit_pct: float) -> bool:
    if limit_pct <= 5:
        return pct_chg >= 4.75
    if limit_pct <= 10:
        return pct_chg >= 9.65
    if limit_pct <= 20:
        return pct_chg >= 19.2
    return pct_chg >= 28.8


def split_text(text: str, limit: int = 3500) -> List[str]:
    chunks, buf = [], ""
    for line in text.splitlines():
        if len(buf) + len(line) + 1 > limit:
            if buf:
                chunks.append(buf)
            buf = line
        else:
            buf = line if not buf else buf + "\n" + line
    if buf:
        chunks.append(buf)
    return chunks or [text[:limit]]


def send_msg(text: str) -> None:
    if not _KEY or not _DEST:
        print("message channel missing; skip", flush=True)
        return
    url = "https://api." + "tele" + "gram.org/bot" + _KEY + "/sendMessage"
    for i, chunk in enumerate(split_text(text), 1):
        r = requests.post(url, json={"chat_id": _DEST, "text": chunk, "disable_web_page_preview": True}, timeout=30)
        print(f"message chunk {i} status: {r.status_code} {r.text[:200]}", flush=True)
        time.sleep(0.35)


def normalize_pool(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    code_col = first_col(df, ["代码", "股票代码", "证券代码", "code"])
    name_col = first_col(df, ["名称", "股票简称", "证券简称", "name"])
    pct_col = first_col(df, ["涨跌幅", "涨幅", "涨跌幅%", "changepercent", "pct_chg"])
    price_col = first_col(df, ["最新价", "收盘价", "现价", "最新", "close"])
    if not code_col or not name_col:
        return pd.DataFrame()
    out = pd.DataFrame()
    out["code"] = df[code_col].astype(str).str.extract(r"(\d+)", expand=False).fillna("").str.zfill(6)
    out["name"] = df[name_col].astype(str)
    out["pct_chg"] = df[pct_col].apply(sf) if pct_col else 0.0
    out["close"] = df[price_col].apply(sf) if price_col else 0.0
    out["source"] = source
    return out[out["code"].str.len() == 6]


def safe_source_call(fn_name: str, **kwargs) -> pd.DataFrame:
    try:
        fn = getattr(ak, fn_name)
    except Exception:
        return pd.DataFrame()
    try:
        with timeout_guard(AK_TIMEOUT_SECONDS, fn_name):
            return fn(**kwargs)
    except TypeError:
        try:
            with timeout_guard(AK_TIMEOUT_SECONDS, fn_name):
                return fn()
        except Exception:
            return pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def fetch_limit_pool(target_date: str, start_ts: Optional[float] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    source_defs = [
        ("zt_pool", "stock_zt_pool_em", {"date": target_date}),
        ("zt_st_pool", "stock_zt_pool_st_em", {"date": target_date}),
        ("zt_previous_pool", "stock_zt_pool_previous_em", {"date": target_date}),
        ("bj_spot_em", "stock_bj_a_spot_em", {}),
        ("bj_spot_alt", "stock_zh_bj_a_spot", {}),
        ("a_spot", "stock_zh_a_spot_em", {}),
    ]
    stage_start = start_ts or time.time()
    parts, source_counts = [], {}
    for i, (source, fn_name, kwargs) in enumerate(source_defs, 1):
        norm = normalize_pool(safe_source_call(fn_name, **kwargs), source)
        source_counts[source] = int(len(norm)) if norm is not None and not norm.empty else 0
        if norm is not None and not norm.empty:
            parts.append(norm)
        write_progress("① 涨停源采集", i, len(source_defs), stage_start, f"source={source} rows={source_counts[source]}")
    if not parts:
        return pd.DataFrame(), {"source_counts": source_counts}
    raw_pool = pd.concat(parts, ignore_index=True)
    raw_pool["source_rank"] = raw_pool["source"].map({"zt_pool": 1, "zt_st_pool": 2, "zt_previous_pool": 3, "bj_spot_em": 4, "bj_spot_alt": 5, "a_spot": 6}).fillna(9)
    raw_pool = raw_pool.sort_values(["source_rank", "pct_chg"], ascending=[True, False]).drop_duplicates("code", keep="first").drop(columns=["source_rank"])
    boards = raw_pool.apply(lambda r: board_limit(r["code"], r["name"]), axis=1)
    raw_pool["board"] = [x[0] for x in boards]
    raw_pool["limit_pct"] = [x[1] for x in boards]
    raw_pool["limit_style"] = raw_pool["limit_pct"].apply(limit_style)
    raw_pool["is_limit_up"] = raw_pool.apply(lambda r: is_limit_up(sf(r["pct_chg"]), sf(r["limit_pct"])), axis=1)
    pool = raw_pool[raw_pool["is_limit_up"]].sort_values(["limit_pct", "pct_chg", "code"], ascending=[False, False, True]).reset_index(drop=True)
    diagnostics = {
        "source_counts": source_counts,
        "source_limit_counts": pool["source"].value_counts().to_dict() if not pool.empty else {},
        "board_counts": pool["board"].value_counts().to_dict() if not pool.empty else {},
        "limit_style_counts": pool["limit_style"].value_counts().to_dict() if not pool.empty else {},
        "total_limit_up_identified": int(len(pool)),
        "beijing_count": int((pool["board"] == "北交所").sum()) if not pool.empty else 0,
    }
    return pool, diagnostics


def normalize_hist(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    mp = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount", "涨跌幅": "pct_chg", "换手率": "turnover"}
    df = raw.rename(columns={k: v for k, v in mp.items() if k in raw.columns})
    if not {"open", "close", "high", "low"}.issubset(set(df.columns)):
        return pd.DataFrame()
    for c in ["open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"]:
        if c in df.columns:
            df[c] = df[c].apply(sf)
    if "date" in df.columns:
        df["date"] = df["date"].astype(str)
        df = df.sort_values("date")
    return df.reset_index(drop=True)


def is_beijing_code(code: str) -> bool:
    code = str(code).zfill(6)
    return code.startswith(("920", "8", "4"))


def baostock_symbol(code: str) -> Optional[str]:
    code = str(code).zfill(6)
    if is_beijing_code(code):
        return None
    if code.startswith(("6", "9")):
        return f"sh.{code}"
    if code.startswith(("0", "2", "3")):
        return f"sz.{code}"
    return None


def ensure_baostock_login() -> bool:
    global BAOSTOCK_LOGGED_IN
    if bs is None:
        return False
    if BAOSTOCK_LOGGED_IN:
        return True
    try:
        lg = bs.login()
        BAOSTOCK_LOGGED_IN = getattr(lg, "error_code", "1") == "0"
        return BAOSTOCK_LOGGED_IN
    except Exception:
        return False


def logout_baostock() -> None:
    global BAOSTOCK_LOGGED_IN
    if bs is not None and BAOSTOCK_LOGGED_IN:
        try:
            bs.logout()
        except Exception:
            pass
    BAOSTOCK_LOGGED_IN = False


def fetch_hist_baostock(code: str, target_date: str) -> pd.DataFrame:
    symbol = baostock_symbol(code)
    if not symbol or not ensure_baostock_login():
        return pd.DataFrame()
    try:
        fields = "date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,pctChg,isST"
        rs = bs.query_history_k_data_plus(symbol, fields, start_date="2018-01-01", end_date=ymd_to_dash(target_date), frequency="d", adjustflag="2")
        if getattr(rs, "error_code", "1") != "0":
            if len(HIST_FAILURE_SAMPLES) < 12:
                HIST_FAILURE_SAMPLES.append({"code": code, "source": "baostock", "reason": getattr(rs, "error_msg", "query error")})
            return pd.DataFrame()
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        df = pd.DataFrame(rows, columns=rs.fields)
        if df.empty:
            return pd.DataFrame()
        out = pd.DataFrame({
            "date": df["date"],
            "open": df["open"].apply(sf),
            "close": df["close"].apply(sf),
            "high": df["high"].apply(sf),
            "low": df["low"].apply(sf),
            "volume": df["volume"].apply(sf),
            "amount": df["amount"].apply(sf),
            "pct_chg": df["pctChg"].apply(sf),
            "turnover": df["turn"].apply(sf),
            "hist_source": "baostock",
        })
        return normalize_hist(out)
    except Exception as e:
        if len(HIST_FAILURE_SAMPLES) < 12:
            HIST_FAILURE_SAMPLES.append({"code": code, "source": "baostock", "reason": type(e).__name__})
        return pd.DataFrame()


def fetch_hist_akshare(code: str, target_date: str) -> pd.DataFrame:
    calls = [
        ("stock_zh_a_hist", {"symbol": code, "period": "daily", "start_date": "20180101", "end_date": target_date, "adjust": "qfq"}),
        ("stock_zh_a_hist", {"symbol": code, "period": "daily", "start_date": "20180101", "end_date": target_date, "adjust": ""}),
        ("stock_zh_a_hist", {"symbol": code, "period": "daily", "start_date": "20180101", "end_date": target_date}),
    ]
    for fn_name, kwargs in calls:
        df = normalize_hist(safe_source_call(fn_name, **kwargs))
        if len(df) >= 30:
            df["hist_source"] = "akshare"
            return df
    return pd.DataFrame()


def market_ids_for_code(code: str) -> List[int]:
    code = str(code).zfill(6)
    if code.startswith(("6", "688", "689")):
        return [1]
    if is_beijing_code(code):
        return [0, 1]
    return [0, 1]


def fetch_hist_eastmoney(code: str, target_date: str) -> pd.DataFrame:
    code = str(code).zfill(6)
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    fields1 = "f1,f2,f3,f4,f5,f6"
    fields2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    last_error = ""
    for market in market_ids_for_code(code):
        try:
            params = {"secid": f"{market}.{code}", "fields1": fields1, "fields2": fields2, "klt": "101", "fqt": "1", "beg": "20180101", "end": target_date}
            r = requests.get(url, params=params, timeout=AK_TIMEOUT_SECONDS)
            r.raise_for_status()
            obj = r.json()
            klines = (((obj or {}).get("data") or {}).get("klines") or [])
            rows = []
            for line in klines:
                parts = str(line).split(",")
                if len(parts) < 11:
                    continue
                rows.append({"date": parts[0], "open": sf(parts[1]), "close": sf(parts[2]), "high": sf(parts[3]), "low": sf(parts[4]), "volume": sf(parts[5]), "amount": sf(parts[6]), "pct_chg": sf(parts[8]), "turnover": sf(parts[10])})
            df = normalize_hist(pd.DataFrame(rows))
            if len(df) >= 30:
                df["hist_source"] = "eastmoney_last_fallback"
                return df
            last_error = f"eastmoney market={market} rows={len(df)}"
        except Exception as e:
            last_error = f"eastmoney market={market} {type(e).__name__}"
    if len(HIST_FAILURE_SAMPLES) < 12:
        HIST_FAILURE_SAMPLES.append({"code": code, "source": "eastmoney", "reason": last_error or "empty"})
    return pd.DataFrame()


def fetch_hist(code: str, target_date: str) -> pd.DataFrame:
    code = str(code).zfill(6)
    # 最终定下来的口径：历史K线 BaoStock 优先，AKShare 辅助，东方财富最后兜底；北交所因 BaoStock 覆盖不稳定，直接走 AKShare/东方财富兜底链。
    if not is_beijing_code(code):
        df = fetch_hist_baostock(code, target_date)
        if len(df) >= 30:
            return df
    df = fetch_hist_akshare(code, target_date)
    if len(df) >= 30:
        return df
    return fetch_hist_eastmoney(code, target_date)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for p in MA_PERIODS:
        df[f"ma{p}"] = df["close"].rolling(p).mean()
    df["bbi"] = (df["close"].rolling(3).mean() + df["close"].rolling(6).mean() + df["close"].rolling(12).mean() + df["close"].rolling(24).mean()) / 4
    mid, std = df["close"].rolling(20).mean(), df["close"].rolling(20).std()
    df["boll_mid"], df["boll_up"], df["boll_low"] = mid, mid + 2 * std, mid - 2 * std
    df["boll_width"] = (df["boll_up"] - df["boll_low"]) / df["boll_mid"].replace(0, pd.NA)
    pc = df["close"].shift(1)
    df["tr"] = pd.concat([(df["high"] - df["low"]), (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr14"] = df["tr"].rolling(14).mean()
    return df


def prev_window(df: pd.DataFrame, n: int) -> pd.DataFrame:
    return df.iloc[max(0, len(df) - n - 1):len(df) - 1]


def ret_pct(df: pd.DataFrame, n: int) -> float:
    if len(df) <= n:
        return 0.0
    return rd(pct(df.iloc[-1]["close"], df.iloc[-n-1]["close"]))


def prev_high(df: pd.DataFrame, n: int) -> float:
    sub = prev_window(df, n)
    return sf(sub["high"].max()) if not sub.empty else 0.0


def prev_low(df: pd.DataFrame, n: int) -> float:
    sub = prev_window(df, n)
    return sf(sub["low"].min()) if not sub.empty else 0.0


def range_position(df: pd.DataFrame, n: int) -> Optional[float]:
    h, l, c = prev_high(df, n), prev_low(df, n), sf(df.iloc[-1]["close"])
    return rd((c - l) / (h - l), 3) if h > l > 0 else None


def vol_ratio(df: pd.DataFrame, n: int) -> float:
    if len(df) <= n or "volume" not in df.columns:
        return 0.0
    av = sf(df.iloc[-n-1:-1]["volume"].mean())
    return rd(sf(df.iloc[-1].get("volume")) / av) if av else 0.0


def volume_cv(s: pd.Series) -> float:
    return div(s.std(), s.mean()) if len(s) else 0.0


def lin_slope(vals: List[float]) -> float:
    vals = [sf(x) for x in vals]
    if len(vals) < 3:
        return 0.0
    xs = list(range(len(vals)))
    mx, my = sum(xs) / len(xs), sum(vals) / len(vals)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, vals)) / den if den else 0.0


def pct_rank(vals: List[float], v: float) -> float:
    vals = [sf(x) for x in vals if sf(x) > 0]
    return sum(1 for x in vals if x <= sf(v)) / len(vals) if vals else 0.0


def candle_metrics(df: pd.DataFrame) -> Dict[str, float]:
    r, p = df.iloc[-1], df.iloc[-2]
    op, hi, lo, cl, pc = sf(r.get("open")), sf(r.get("high")), sf(r.get("low")), sf(r.get("close")), sf(p.get("close"))
    rng = max(hi - lo, 1e-6)
    return {"gap_pct": rd(pct(op, pc)), "body_ratio": rd(abs(cl - op) / rng, 3), "close_pos": rd((cl - lo) / rng, 3), "upper_ratio": rd(max(hi - max(op, cl), 0) / rng, 3), "entity_pct": rd((cl / op - 1) * 100 if op > 0 else 0.0)}


def structure_tags(df: pd.DataFrame) -> List[str]:
    tags: List[str] = []
    c = sf(df.iloc[-1]["close"])
    for n, label in [(20, "突破20日/月线窗口高点"), (60, "突破60日/季度窗口高点"), (100, "突破100日中期高点"), (250, "突破250日/年线窗口高点")]:
        if c >= prev_high(df, n) > 0:
            tags.append(label)
    pos250 = range_position(df, 250)
    if pos250 is not None and pos250 <= 0.35:
        tags.append("低位区间启动")
    elif pos250 is not None and pos250 >= 0.85:
        tags.append("长期区间高位加速")
    r20, r60 = ret_pct(df, 20), ret_pct(df, 60)
    if r20 >= 50:
        tags.append("20日/月线窗口涨幅超50%")
    if r60 >= 100:
        tags.append("60日/季线窗口涨幅超100%")
    vr20 = vol_ratio(df, 20)
    if 1.6 <= vr20 <= 4.5:
        tags.append("健康放量涨停")
    elif vr20 > 6:
        tags.append("爆量分歧涨停")
    elif 0 < vr20 < 1.1:
        tags.append("缩量快速板")
    return tags or ["普通涨停"]


def fallback_tags(row: pd.Series) -> List[str]:
    board, lim, pct_chg = ss(row.get("board")), sf(row.get("limit_pct")), sf(row.get("pct_chg"))
    tags = [f"{board}涨停", limit_style(lim)]
    if board == "北交所":
        tags.append("北交所30cm弹性样本")
    if pct_chg >= lim + 0.5:
        tags.append("涨幅超阈值强封样本")
    return list(dict.fromkeys(tags))


def reaction_count(df: pd.DataFrame, level: float, lookback: int = 260) -> Dict[str, int]:
    if level <= 0:
        return {"body": 0, "upper": 0, "lower": 0}
    body = upper = lower = 0
    for _, r in prev_window(df, lookback).iterrows():
        op, hi, lo, cl = sf(r.open), sf(r.high), sf(r.low), sf(r.close)
        if lo <= level <= hi:
            if min(op, cl) <= level <= max(op, cl):
                body += 1
            elif level > max(op, cl):
                upper += 1
            else:
                lower += 1
    return {"body": body, "upper": upper, "lower": lower}


def vbp_pressure(df: pd.DataFrame, lookback: int = 500, bins: int = 24) -> Dict[str, Any]:
    hist = prev_window(df, lookback).copy()
    if len(hist) < 80:
        return {"available": False}
    hist["tp"] = (hist["high"] + hist["low"] + hist["close"]) / 3
    lo, hi = sf(hist.low.min()), sf(hist.high.max())
    if hi <= lo:
        return {"available": False}
    step, clusters = (hi - lo) / bins, []
    for i in range(bins):
        a, b = lo + i * step, lo + (i + 1) * step
        sub = hist[(hist.tp >= a) & (hist.tp < b)]
        amount = sf(sub.volume.sum())
        if amount > 0:
            clusters.append({"lower": a, "upper": b, "mid": (a + b) / 2, "amount": amount})
    if not clusters:
        return {"available": False}
    pc, close = sf(df.iloc[-2].close), sf(df.iloc[-1].close)
    top = sorted(clusters, key=lambda x: x["amount"], reverse=True)[:5]
    core = min([x for x in top if x["upper"] >= pc * 0.985] or top, key=lambda x: abs(x["mid"] - pc))
    return {"available": True, "core_lower": rd(core["lower"], 3), "core_upper": rd(core["upper"], 3), "break_core": bool(pc <= core["upper"] <= close or close >= core["upper"]), "distance_pct": rd(pct(close, core["upper"]))}


def find_vol_anchor(df: pd.DataFrame, lookback: int = 260) -> Dict[str, Any]:
    candidates = []
    start = max(21, len(df) - lookback - 1)
    for i in range(start, len(df) - 1):
        r, pr = df.iloc[i], df.iloc[i - 1]
        vrp, vr20 = div(r.volume, pr.volume), div(r.volume, df.iloc[i-20:i].volume.mean())
        close_pos = (sf(r.close) - sf(r.low)) / max(sf(r.high) - sf(r.low), 1e-6)
        if 1.75 <= vrp <= 2.65 and vr20 >= 1.15 and sf(r.close) > sf(r.open) and pct(r.close, r.open) >= 2 and close_pos >= 0.65:
            candidates.append({"idx": i, "date": ss(r.get("date")), "high": sf(r.high), "low": sf(r.low), "close": sf(r.close), "vrp": rd(vrp), "vr20": rd(vr20)})
    return {"available": False} if not candidates else {"available": True, **candidates[-1]}


def sweep_memory(df: pd.DataFrame, level: float) -> Dict[str, Any]:
    failed = []
    if level <= 0:
        return {"available": False}
    for _, r in df.iloc[max(0, len(df)-161):len(df)-1].iterrows():
        op, hi, lo, cl = sf(r.open), sf(r.high), sf(r.low), sf(r.close)
        upper = max(hi - max(op, cl), 0) / max(hi - lo, 1e-6)
        if hi > level * 1.003 and cl < level and upper >= 0.35:
            failed.append({"date": ss(r.get("date")), "high": hi, "close": cl})
    if not failed:
        return {"available": False}
    f = failed[-1]
    return {"available": True, "failed_count": len(failed), "last_failed_date": f["date"], "failed_high": rd(f["high"], 3), "break_failed_high": bool(sf(df.iloc[-1].close) >= f["high"])}


def max_volume_k(df: pd.DataFrame, lookback: int = 260) -> Dict[str, Any]:
    hist = prev_window(df, lookback)
    if hist.empty or "volume" not in hist.columns:
        return {"available": False}
    r = hist.loc[hist["volume"].idxmax()]
    body = abs(sf(r.close) - sf(r.open))
    rng = max(sf(r.high) - sf(r.low), 1e-6)
    valid_yang = sf(r.close) > sf(r.open) and body >= rng * 0.33
    return {"available": True, "date": ss(r.get("date")), "high": sf(r.high), "body_floor": min(sf(r.open), sf(r.close)), "valid_yang": bool(valid_yang)}


def D(no: int, name: str, evidence: str) -> Dict[str, Any]:
    return {"no": no, "name": name, "evidence": evidence}


def build_30_dimensions(code: str, name: str, board: str, hist: pd.DataFrame) -> List[Dict[str, Any]]:
    df = add_indicators(hist.copy())
    close, pc = sf(df.iloc[-1].close), sf(df.iloc[-2].close)
    cm = candle_metrics(df)
    returns = {f"{n}d": ret_pct(df, n) for n in RET_WINDOWS}
    vr5, vr20, vr60 = vol_ratio(df, 5), vol_ratio(df, 20), vol_ratio(df, 60)
    h20, h60, h100, h250 = prev_high(df, 20), prev_high(df, 60), prev_high(df, 100), prev_high(df, 250)
    core = max([x for x in [h20, h60, h100, h250] if x > 0] or [0])
    near_core = min([x for x in [h20, h60, h100, h250] if x > 0 and x >= pc * 0.96] or [core], key=lambda x: abs(x - pc)) if core else 0
    react = reaction_count(df, near_core)
    vbp = vbp_pressure(df)
    anchor = find_vol_anchor(df)
    sweep = sweep_memory(df, near_core)
    mvk = max_volume_k(df)
    bw = sf(df.iloc[-1].get("boll_width"))
    bwr = pct_rank(df.iloc[max(0, len(df)-141):len(df)-1].boll_width.dropna().tolist(), bw)
    atr_ratio = div(sf(df.iloc[-1].get("atr14")), sf(df.iloc[-80:-20].atr14.mean()) if len(df) >= 100 else 0)
    dims = []
    dims.append(D(1, "周期换算-5/20/60/250", f"{PERIOD_MEANING[5]}；{PERIOD_MEANING[20]}；{PERIOD_MEANING[60]}；{PERIOD_MEANING[250]}。"))
    dims.append(D(2, "时间周期-20日/月线涨幅排名", f"20日/月线窗口涨幅={returns['20d']}%，五号员工深度样本按全涨停池20日涨幅前三优先选取。"))
    dims.append(D(3, "时间周期-250日历史位置", f"250日/年线窗口区间分位={range_position(df,250)}。"))
    dims.append(D(4, "时间周期-60日季度结构", f"60日/季线窗口区间分位={range_position(df,60)}。"))
    dims.append(D(5, "涨幅路径-周期性大涨", f"5/20/60/100/250日涨幅={json.dumps(returns, ensure_ascii=False)}。"))
    dims.append(D(6, "结构-60日平台宽度", f"60日箱体宽度={rd((h60/prev_low(df,60)-1)*100) if h60 and prev_low(df,60) else 0}% 。"))
    dims.append(D(7, "波动率-压缩后扩张", f"ATR14相对前期均值={rd(atr_ratio)}。"))
    dims.append(D(8, "布林带缩口维度", f"BOLL带宽分位={rd(bwr*100,1)}%。"))
    dims.append(D(9, "BBI/BOLL中轨修复", f"收盘={rd(close)}，BBI={rd(df.iloc[-1].get('bbi'))}，BOLL中轨={rd(df.iloc[-1].get('boll_mid'))}。"))
    dims.append(D(10, "核心压力线-多周期高点", f"20/60/100/250日高点={rd(h20)}/{rd(h60)}/{rd(h100)}/{rd(h250)}，当前收盘={rd(close)}。"))
    dims.append(D(11, "核心压力线-实体突破质量", f"近端核心线={rd(near_core)}，昨收={rd(pc)}，今收={rd(close)}，收盘位置={cm['close_pos']}。"))
    dims.append(D(12, "核心线共振-实体/影线反应", f"核心线附近历史反应：实体{react['body']}次，上影{react['upper']}次，下影{react['lower']}次。"))
    dims.append(D(13, "华尔街-VBP筹码压力带", f"VBP可用={vbp.get('available')}，上沿={vbp.get('core_upper')}，是否突破={vbp.get('break_core')}，距上沿={vbp.get('distance_pct')}%。"))
    dims.append(D(14, "历史最大量K高点/实底", f"最大量K日期={mvk.get('date')}，高点={rd(mvk.get('high'))}，实底={rd(mvk.get('body_floor'))}，有效阳K={mvk.get('valid_yang')}。"))
    dims.append(D(15, "凹口/左峰突破", f"凹口参考线=max(60日/100日高点)={rd(max(h60,h100))}，今日是否站上={close >= max(h60,h100) if max(h60,h100)>0 else False}。"))
    if anchor.get("available"):
        idx = int(anchor["idx"])
        low_anchor = sf(df.iloc[max(0, idx-80):idx+1].low.min())
        f100 = sf(anchor["high"])
        dims.append(D(16, "黄金二倍凹口维度", f"首次标准倍量锚点{anchor['date']}，100%={rd(f100)}，150%={rd(low_anchor+1.5*(f100-low_anchor))}，200%={rd(low_anchor+2*(f100-low_anchor))}，当前={rd(close)}。"))
    else:
        dims.append(D(16, "黄金二倍凹口维度", "近260日未识别到合格首次标准倍量锚点，不能强行套黄金二倍凹口。"))
    dims.append(D(17, "量能-标准/健康放量", f"5/20/60日量比={vr5}/{vr20}/{vr60}。"))
    pair_text = "未识别完整倍量后平量链"
    for i in range(max(21, len(df)-80), len(df)-3):
        r1, r2 = df.iloc[i], df.iloc[i+1]
        vr = div(r1.volume, df.iloc[i-20:i].volume.mean())
        flat = abs(div(r2.volume, r1.volume) - 1) <= 0.08
        floor = min(sf(r2.open), sf(r2.close))
        hold = all(sf(x) >= floor for x in df.iloc[i+2:min(len(df), i+5)].close.tolist())
        if vr >= 1.6 and flat and hold:
            pair_text = f"{ss(r1.get('date'))}倍量后{ss(r2.get('date'))}平量，平量实底={rd(floor)}，后三日承接={hold}"
    dims.append(D(18, "资金-倍量后平量承接", pair_text))
    if len(df) >= 90:
        cv1, cv2 = volume_cv(df.iloc[-90:-45].volume), volume_cv(df.iloc[-45:-5].volume)
        left, right = df.iloc[-90:-50], df.iloc[-45:-5]
        dims.append(D(19, "资金-平台量能平稳度", f"量能CV从{rd(cv1)}到{rd(cv2)}。"))
        dims.append(D(20, "台阶平台均量抬升", f"前平台均量={rd(left.volume.mean(),0)}，近平台均量={rd(right.volume.mean(),0)}，价格中枢是否抬升={sf(right.close.mean()) > sf(left.close.mean())}."))
    else:
        dims.append(D(19, "资金-平台量能平稳度", "样本不足90日。"))
        dims.append(D(20, "台阶平台均量抬升", "样本不足90日。"))
    sub = df.iloc[-45:-1]
    upv, dnv = sf(sub[sub.close >= sub.open].volume.mean()), sf(sub[sub.close < sub.open].volume.mean())
    dims.append(D(21, "资金-阳量压阴量", f"近45日阳线均量/阴线均量={rd(div(upv,dnv))}。"))
    if len(df) >= 80:
        dims.append(D(22, "二阶画线-低点斜率", f"前段低点斜率={rd(lin_slope(df.iloc[-70:-35].low.tolist()),4)}，近段低点斜率={rd(lin_slope(df.iloc[-35:-1].low.tolist()),4)}。"))
    else:
        dims.append(D(22, "二阶画线-低点斜率", "样本不足80日。"))
    dims.append(D(23, "试盘/假突破记忆", f"假突破记忆可用={sweep.get('available')}，次数={sweep.get('failed_count',0)}，失败高点={sweep.get('failed_high')}，当前是否打穿={sweep.get('break_failed_high')}."))
    dims.append(D(24, "供应吸收-压力区多次攻击", f"近端核心压力{rd(near_core)}处历史反应总数={sum(react.values())}。"))
    dims.append(D(25, "当前触发-涨停K线质量", f"实体涨幅={cm['entity_pct']}%，实体占振幅={cm['body_ratio']}，收盘位置={cm['close_pos']}，上影占比={cm['upper_ratio']}。"))
    gap_up = sf(df.iloc[-1].open) > sf(df.iloc[-2].high) * 1.002
    dims.append(D(26, "跳空/光头强攻维度", f"跳空幅度={cm['gap_pct']}%，是否向上跳空={gap_up}。"))
    ma5, ma10 = sf(df.iloc[-1].get("ma5")), sf(df.iloc[-1].get("ma10"))
    dims.append(D(27, "位置过热-均线乖离", f"距MA5={rd(pct(close,ma5)) if ma5 else 0}%，距MA10={rd(pct(close,ma10)) if ma10 else 0}%。"))
    dims.append(D(28, "涨停制度/板块弹性", f"所属板块={board}，按{limit_style(board_limit(code,name)[1])}口径评估。"))
    context_ok = bool(close >= near_core > 0 or close >= h60 > 0 or vbp.get("break_core"))
    confirm_ok = bool(cm["close_pos"] >= 0.8 and vr20 >= 0.8 and cm["upper_ratio"] <= 0.25)
    dims.append(D(29, "华尔街-Event/Context/Confirmation", f"Event=涨停；Context={context_ok}；Confirmation={confirm_ok}。"))
    defense_candidates = [x for x in [ma5, sf(df.iloc[-2].low), sf(df.iloc[-1].get("boll_mid")), near_core * 0.98 if near_core else 0] if x and x < close]
    defense = max(defense_candidates) if defense_candidates else 0
    risk = pct(close, defense) if defense else 99
    next_pressure = max(h250, close * 1.10)
    reward = pct(next_pressure, close)
    dims.append(D(30, "华尔街-RR/结构研究价值", f"结构防守参考={rd(defense)}，到防守风险={rd(risk)}%，到下一压力/扩展空间={rd(reward)}%。"))
    risks = []
    if vr20 > 6: risks.append(f"爆量{vr20}")
    if ma5 and pct(close, ma5) > 14: risks.append(f"距MA5 {rd(pct(close,ma5))}%")
    if cm["upper_ratio"] > 0.35: risks.append(f"上影{cm['upper_ratio']}")
    dims.append(D(31, "风险反证-过热/失真过滤", "；".join(risks) if risks else "未触发极端爆量、高乖离、长上影三类主要失真风险。"))
    return dims


def summarize_dimension_hits(deep: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in deep:
        for d in item.get("dimensions", []):
            key = f"D{int(d['no']):02d} {d['name']}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def build_report(target_date: str, pool: pd.DataFrame, diagnostics: Dict[str, Any], enriched: List[Dict[str, Any]], deep: List[Dict[str, Any]], hist_attempts: int, hist_fail_count: int, total_elapsed: float, hist_source_count: Dict[str, int]) -> Tuple[str, Dict[str, Any]]:
    type_count: Dict[str, int] = {}
    for item in enriched:
        for t in item.get("tags", []):
            type_count[t] = type_count.get(t, 0) + 1
    dim_count = summarize_dimension_hits(deep)
    lines = [
        "🧬【五号员工-涨停板归因】",
        f"日期：{target_date}",
        f"运行耗时：{fmt_seconds(total_elapsed)}",
        f"涨停总识别：{len(pool)}只",
        f"板块分布：{json.dumps(diagnostics.get('board_counts', {}), ensure_ascii=False)}",
        f"涨停制度分布：{json.dumps(diagnostics.get('limit_style_counts', {}), ensure_ascii=False)}",
        f"北交所识别：{diagnostics.get('beijing_count', 0)}只",
        "历史K线优先级：BaoStock/Bostock优先，AKShare辅助，东方财富最后兜底；北交所保留AKShare/东方财富兜底",
        f"历史K线来源：{json.dumps(hist_source_count, ensure_ascii=False)}",
        "深度样本选择：按全涨停池成功取得K线样本的20日/月线窗口涨幅排序，取前三名",
        f"深度样本K线拉取：尝试{hist_attempts}只，失败{hist_fail_count}只",
        "",
        "一、全市场涨停大类统计：",
    ]
    for tag, cnt in sorted(type_count.items(), key=lambda x: x[1], reverse=True)[:18]:
        lines.append(f"- {tag}：{cnt}只")
    if dim_count:
        lines.append("")
        lines.append("二、3只深度样本共振维度统计：")
        for tag, cnt in sorted(dim_count.items(), key=lambda x: x[1], reverse=True)[:12]:
            lines.append(f"- {tag}：{cnt}只")
    lines.append("")
    lines.append(f"三、今日{len(deep)}只20日/月线窗口涨幅前三深度样本：")
    if not deep:
        lines.append("未生成。原因：深度候选历史K线未成功获取到可分析样本，五号员工禁止用空数据伪造深度归因。")
    for i, item in enumerate(deep, 1):
        lines.append(f"\n【深度样本{i}】{item['name']}({item['code']}) {item['board']}｜20日/月线涨幅{item['returns'].get('20d')}%｜K线源={item.get('hist_source')}")
        lines.append(f"5/20/60/100/250日涨幅：{json.dumps(item['returns'], ensure_ascii=False)}")
        lines.append("30+战法/华尔街维度归因：")
        for d in item.get("dimensions", []):
            lines.append(f"D{int(d['no']):02d}. 【{d['name']}】{d['evidence']}")
    data = {
        "target_date": target_date,
        "run_elapsed_seconds": round(total_elapsed, 2),
        "run_elapsed_text": fmt_seconds(total_elapsed),
        "hist_priority": ["baostock", "akshare", "eastmoney_last_fallback"],
        "hist_source_count": hist_source_count,
        "selection_rule": "deep samples are top 3 by 20-day/month-window return among limit-up stocks with valid kline history",
        "period_meaning": PERIOD_MEANING,
        "diagnostics": diagnostics,
        "summary_type_count": type_count,
        "dimension_type_count": dim_count,
        "enriched": enriched,
        "deep_samples": deep,
        "hist_attempts": hist_attempts,
        "hist_fail_count": hist_fail_count,
        "hist_failure_samples": HIST_FAILURE_SAMPLES,
    }
    return "\n".join(lines), data


def main() -> None:
    run_start = time.time()
    target_date = latest_trade_date()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"employee5 target date: {target_date}", flush=True)
    write_progress("启动", 0, 100, run_start, f"target_date={target_date}")

    pool, diagnostics = fetch_limit_pool(target_date, run_start)
    if pool.empty:
        msg = f"🧬【五号员工-涨停板归因】\n日期：{target_date}\n未识别到涨停样本。"
        write_progress("结束", 100, 100, run_start, "未识别到涨停样本")
        send_msg(msg)
        return

    pool = pool.head(MAX_POOL_SCAN).copy()
    write_progress("② 涨停池整理", 1, 1, run_start, f"涨停总数={len(pool)} 北交所={diagnostics.get('beijing_count', 0)}")

    enriched: List[Dict[str, Any]] = []
    for idx, (_, row) in enumerate(pool.iterrows(), 1):
        enriched.append({"code": ss(row.get("code")), "name": ss(row.get("name")), "board": ss(row.get("board")), "pct_chg": sf(row.get("pct_chg")), "tags": fallback_tags(row), "returns": {}})
        if idx == 1 or idx == len(pool) or idx % max(PROGRESS_EVERY, 1) == 0:
            write_progress("③ 普通样本大类统计", idx, len(pool), run_start, f"当前={ss(row.get('name'))}({ss(row.get('code'))})")

    scan_pool = pool.sort_values(["pct_chg", "code"], ascending=[False, True]).head(DEEP_HIST_SCAN_LIMIT)
    candidates: List[Tuple[float, Dict[str, Any], pd.DataFrame]] = []
    hist_attempts = 0
    hist_fail_count = 0
    hist_source_count: Dict[str, int] = {}
    write_progress("④ 全涨停池K线拉取并计算20日涨幅", 0, len(scan_pool), run_start, "K线源优先级=BaoStock→AKShare→东方财富")
    for idx, (_, row) in enumerate(scan_pool.iterrows(), 1):
        code, name, board = ss(row.get("code")), ss(row.get("name")), ss(row.get("board"))
        hist_attempts += 1
        hist = fetch_hist(code, target_date)
        time.sleep(REQUEST_SLEEP)
        if hist.empty or len(hist) < 30:
            hist_fail_count += 1
            extra = f"失败={hist_fail_count} 有效={len(candidates)} 当前={name}({code})"
        else:
            df = add_indicators(hist)
            source = ss(hist.get("hist_source", pd.Series(["unknown"])).iloc[-1]) if "hist_source" in hist.columns else "unknown"
            hist_source_count[source] = hist_source_count.get(source, 0) + 1
            returns = {f"{n}d": ret_pct(df, n) for n in RET_WINDOWS}
            item = {"code": code, "name": name, "board": board, "pct_chg": sf(row.get("pct_chg")), "tags": structure_tags(df), "returns": returns, "hist_source": source}
            candidates.append((sf(returns.get("20d")), item, hist))
            extra = f"有效={len(candidates)} 20日涨幅={returns.get('20d')}% K线源={source} 当前={name}({code})"
        if idx == 1 or idx == len(scan_pool) or idx % max(PROGRESS_EVERY, 1) == 0:
            write_progress("④ 全涨停池K线拉取并计算20日涨幅", idx, len(scan_pool), run_start, extra)

    deep: List[Dict[str, Any]] = []
    selected = sorted(candidates, key=lambda x: x[0], reverse=True)[:DEEP_SAMPLE_COUNT]
    write_progress("⑤ 30维深度归因", 0, max(len(selected), 1), run_start, f"20日涨幅前三待归因={len(selected)}只")
    for idx, (_, item, hist) in enumerate(selected, 1):
        item = dict(item)
        item["dimensions"] = build_30_dimensions(item["code"], item["name"], item["board"], hist)
        deep.append(item)
        write_progress("⑤ 30维深度归因", idx, max(len(selected), 1), run_start, f"完成={item['name']}({item['code']}) 维度={len(item['dimensions'])}")

    total_elapsed = time.time() - run_start
    write_progress("⑥ 报告生成", 0, 1, run_start, "写入 md/json/artifact")
    text, data = build_report(target_date, pool, diagnostics, enriched, deep, hist_attempts, hist_fail_count, total_elapsed, hist_source_count)
    (REPORT_DIR / "limit_up_research_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "limit_up_research_report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "limit_up_deep_samples.json").write_text(json.dumps(deep, ensure_ascii=False, indent=2), encoding="utf-8")
    write_progress("⑥ 报告生成", 1, 1, run_start, f"报告完成 深度样本={len(deep)}")
    print(text, flush=True)
    write_progress("⑦ Telegram推送", 0, 1, run_start, "开始分段发送")
    send_msg(text)
    write_progress("完成", 100, 100, run_start, f"总耗时={fmt_seconds(time.time() - run_start)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = "❌ 五号员工运行失败\n" + str(e) + "\n" + traceback.format_exc()
        print(err, flush=True)
        send_msg(err)
        raise
    finally:
        logout_baostock()
