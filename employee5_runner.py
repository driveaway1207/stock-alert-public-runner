# -*- coding: utf-8 -*-
from __future__ import annotations

"""五号员工：大涨/涨停归因学习引擎。
缓存优先；缓存缺失才用 BaoStock 兜底；不荐股。
V5.2：内存安全扫描 + JSON安全输出 + 季线核心线价格带识别。
"""

import json
import math
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    import requests
except Exception:
    requests = None
try:
    import baostock as bs
except Exception:
    bs = None

BOOT = "EMPLOYEE5_PUBLIC_BOOT_20260528_CORE_LINE_V52_MEMSAFE_JSONSAFE"
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "employee5_reports"
TARGET_RAW = os.getenv("EMPLOYEE5_TARGET_DATE") or datetime.now().strftime("%Y%m%d")
TARGET = re.sub(r"\D", "", str(TARGET_RAW))[:8] or datetime.now().strftime("%Y%m%d")
TARGET_DASH = f"{TARGET[:4]}-{TARGET[4:6]}-{TARGET[6:8]}"
TOP_N = int(os.getenv("EMPLOYEE5_TOP_N", "3"))
MIN_ROWS = int(os.getenv("EMPLOYEE5_MIN_CACHE_ROWS", "22"))
SCAN_KEEP_ROWS = int(os.getenv("EMPLOYEE5_SCAN_KEEP_ROWS", "80"))
ALLOW_BAOSTOCK_FALLBACK = os.getenv("EMPLOYEE5_ALLOW_BAOSTOCK_FALLBACK", "1") != "0"
BAOSTOCK_LIMIT = int(os.getenv("EMPLOYEE5_BAOSTOCK_FALLBACK_LIMIT", "0"))
BOT = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
CACHE_DIRS = [ROOT / "kline_cache", ROOT / "employee5_kline_cache", ROOT / "data" / "kline_cache", ROOT / "cache" / "kline_cache", ROOT.parent / "kline_cache"]
MAIN_CACHE_DIR = ROOT / "kline_cache"
CACHE_FILE_MAP: Dict[str, Path] = {}


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        return float(str(x).replace("%", "").replace(",", ""))
    except Exception:
        return default


def rd(x: Any, n: int = 2) -> float:
    v = sf(x)
    return 0.0 if math.isnan(v) or math.isinf(v) else round(v, n)


def norm_date(x: Any) -> str:
    s = re.sub(r"\D", "", ss(x)[:10])
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else ss(x)[:10]


def code_of(x: Any) -> str:
    s = x.stem if isinstance(x, Path) else ss(x)
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else ""


def valid_code(code: str) -> bool:
    return bool(code) and code.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4"))


def bs_code(code: str) -> str:
    c = code_of(code)
    if c.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh." + c
    if c.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz." + c
    if c.startswith(("920", "8", "4")):
        return "bj." + c
    return c


def limit_pct(code: str, name: str = "") -> float:
    if "ST" in ss(name).upper():
        return 5.0
    if code.startswith(("688", "689", "300", "301")):
        return 20.0
    if code.startswith(("920", "8", "4")):
        return 30.0
    return 10.0


def normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    mp = {"日期": "date", "交易日期": "date", "date": "date", "time": "date", "代码": "code", "code": "code", "开盘": "open", "open": "open", "开盘价": "open", "收盘": "close", "close": "close", "收盘价": "close", "最高": "high", "high": "high", "最高价": "high", "最低": "low", "low": "low", "最低价": "low", "成交量": "volume", "volume": "volume", "vol": "volume", "成交额": "amount", "amount": "amount", "涨跌幅": "pct_chg", "pctChg": "pct_chg", "pct_chg": "pct_chg", "涨幅": "pct_chg"}
    d = df.rename(columns={c: mp.get(str(c), mp.get(str(c).lower(), c)) for c in df.columns}).copy()
    if not set(["date", "open", "high", "low", "close"]).issubset(d.columns):
        return pd.DataFrame()
    for c in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if c in d.columns:
            d[c] = d[c].map(sf)
    if "volume" not in d.columns:
        d["volume"] = 0.0
    if "amount" not in d.columns:
        d["amount"] = 0.0
    d["date"] = d["date"].map(norm_date)
    d = d[(d.date != "") & (d.open > 0) & (d.high > 0) & (d.low > 0) & (d.close > 0)]
    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    d = d[d.date <= TARGET_DASH].reset_index(drop=True)
    if "pct_chg" not in d.columns or d["pct_chg"].abs().sum() == 0:
        prev = d.close.shift(1)
        d["pct_chg"] = (d.close / prev - 1.0) * 100.0
        d.loc[prev <= 0, "pct_chg"] = 0.0
    return d


def rows_from_obj(obj: Any) -> Any:
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in ["rows", "data", "klines", "kline", "daily", "history", "records"]:
            if k in obj:
                return obj[k]
    return []


def read_cache_file(path: Path) -> pd.DataFrame:
    try:
        suf = path.suffix.lower()
        if suf == ".json":
            return normalize_hist(pd.DataFrame(rows_from_obj(json.loads(path.read_text(encoding="utf-8")))))
        if suf in [".csv", ".txt"]:
            return normalize_hist(pd.read_csv(path))
        if suf in [".pkl", ".pickle"]:
            return normalize_hist(pd.read_pickle(path))
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame()


def iter_cache_files() -> List[Path]:
    files, seen_dirs = [], set()
    for d in CACHE_DIRS:
        key = str(d.resolve()) if d.exists() else str(d)
        if key in seen_dirs or not d.exists():
            continue
        seen_dirs.add(key)
        for pat in ["*.json", "*.csv", "*.txt", "*.pkl", "*.pickle"]:
            files.extend(d.rglob(pat))
    out, seen = [], set()
    for f in files:
        key = str(f.resolve())
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def load_cache() -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    files = iter_cache_files()
    hist: Dict[str, pd.DataFrame] = {}
    stat = {"source": "cache", "cache_files": len(files), "cache_hit": 0, "cache_bad": 0, "cache_short": 0, "target_date": TARGET, "cache_dirs": [str(x) for x in CACHE_DIRS], "scan_keep_rows": SCAN_KEEP_ROWS, "memsafe_scan": True}
    for i, p in enumerate(files, 1):
        c = code_of(p)
        if not valid_code(c):
            continue
        df = read_cache_file(p)
        if df.empty:
            stat["cache_bad"] += 1
            continue
        if len(df) < MIN_ROWS or df.iloc[-1].date.replace("-", "") < TARGET:
            stat["cache_short"] += 1
            continue
        CACHE_FILE_MAP[c] = p
        hist[c] = df.tail(max(30, SCAN_KEEP_ROWS)).reset_index(drop=True)
        stat["cache_hit"] += 1
        if i % 500 == 0:
            print(f"cache scan {i}/{len(files)} hit={stat['cache_hit']}", flush=True)
    return hist, stat


def load_full_history(code: str, fallback: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    p = CACHE_FILE_MAP.get(code_of(code))
    if p is not None and Path(p).exists():
        df = read_cache_file(Path(p))
        if not df.empty:
            return df
    return fallback if fallback is not None else pd.DataFrame()


def save_cache_file(code: str, df: pd.DataFrame) -> None:
    try:
        MAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (MAIN_CACHE_DIR / f"{code}.json").write_text(json.dumps({"target_date": TARGET, "rows": df.to_dict("records")}, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        print(f"cache save failed {code}: {exc}", flush=True)


def baostock_all_codes() -> List[Tuple[str, str]]:
    if bs is None:
        return []
    lg = bs.login()
    print(f"baostock login: {getattr(lg, 'error_code', '')} {getattr(lg, 'error_msg', '')}", flush=True)
    rs = bs.query_all_stock(day=TARGET_DASH)
    out = []
    while rs.error_code == "0" and rs.next():
        row = rs.get_row_data()
        c = code_of(row[0] if row else "")
        name = row[1] if len(row) > 1 else c
        if valid_code(c):
            out.append((c, name or c))
    dedup, seen = [], set()
    for c, n in out:
        if c not in seen:
            seen.add(c)
            dedup.append((c, n))
    return dedup


def baostock_fetch_hist(code: str) -> pd.DataFrame:
    if bs is None:
        return pd.DataFrame()
    start = (datetime.strptime(TARGET_DASH, "%Y-%m-%d") - timedelta(days=140)).strftime("%Y-%m-%d")
    fields = "date,code,open,high,low,close,volume,amount,pctChg"
    try:
        rs = bs.query_history_k_data_plus(bs_code(code), fields, start_date=start, end_date=TARGET_DASH, frequency="d", adjustflag="3")
        data = []
        while rs.error_code == "0" and rs.next():
            data.append(rs.get_row_data())
        return normalize_hist(pd.DataFrame(data, columns=fields.split(","))) if data else pd.DataFrame()
    except Exception as exc:
        print(f"baostock hist failed {code}: {exc}", flush=True)
        return pd.DataFrame()


def build_baostock_cache() -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    stat = {"source": "baostock_fallback", "cache_files": 0, "cache_hit": 0, "cache_bad": 0, "cache_short": 0, "target_date": TARGET, "baostock_used": True}
    hist: Dict[str, pd.DataFrame] = {}
    if not ALLOW_BAOSTOCK_FALLBACK:
        stat["baostock_disabled"] = True
        return hist, stat
    codes = baostock_all_codes()
    if BAOSTOCK_LIMIT > 0:
        codes = codes[:BAOSTOCK_LIMIT]
    stat["baostock_universe"] = len(codes)
    start_time = time.time()
    for i, (code, _) in enumerate(codes, 1):
        df = baostock_fetch_hist(code)
        if df.empty:
            stat["cache_bad"] += 1
            continue
        if len(df) < MIN_ROWS or df.iloc[-1].date.replace("-", "") < TARGET:
            stat["cache_short"] += 1
            continue
        hist[code] = df.tail(max(30, SCAN_KEEP_ROWS)).reset_index(drop=True)
        stat["cache_hit"] += 1
        save_cache_file(code, df)
        if i == 1 or i % 200 == 0 or i == len(codes):
            print(f"baostock fallback {i}/{len(codes)} hit={stat['cache_hit']} speed={i / max(time.time() - start_time, 0.001):.2f}/s current={code}", flush=True)
    try:
        if bs is not None:
            bs.logout()
    except Exception:
        pass
    stat["cache_files"] = len(list(MAIN_CACHE_DIR.glob("*.json"))) if MAIN_CACHE_DIR.exists() else 0
    return hist, stat


def gain20(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df) < 22:
        return None
    a, b = df.iloc[-21], df.iloc[-1]
    g = (sf(b.close) / sf(a.close) - 1.0) * 100 if sf(a.close) else 0.0
    return {"gain_20d": rd(g), "start_date": a.date, "end_date": b.date, "start_close": rd(a.close), "end_close": rd(b.close)}


def pick_samples(hist: Dict[str, pd.DataFrame]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    a_rows, b_rows = [], []
    for i, (code, df) in enumerate(hist.items(), 1):
        if df is None or df.empty:
            continue
        last = df.iloc[-1]
        pct, lp = sf(last.pct_chg), limit_pct(code)
        if pct >= lp - 0.35 or pct >= min(8.0, lp * 0.75):
            a_rows.append({"code": code, "name": code, "date": last.date, "close": rd(last.close), "pct_chg": rd(pct), "sample_type": "涨停/近涨停" if pct >= lp - 0.35 else "极强上涨"})
        g = gain20(df)
        if g:
            b_rows.append({"code": code, "name": code, **g})
        if i % 500 == 0:
            print(f"sample scan {i}/{len(hist)} A={len(a_rows)} B={len(b_rows)}", flush=True)
    A, B = pd.DataFrame(a_rows), pd.DataFrame(b_rows)
    if not A.empty:
        A = A.sort_values(["pct_chg", "close"], ascending=[False, False]).head(TOP_N).reset_index(drop=True)
    if not B.empty:
        B = B.sort_values("gain_20d", ascending=False).head(TOP_N).reset_index(drop=True)
    return A, B


def aggregate_bars(df: pd.DataFrame, window: int) -> pd.DataFrame:
    if df is None or df.empty or len(df) < max(22, window * 3):
        return pd.DataFrame()
    d = df.copy().reset_index(drop=True)
    d["grp"] = [(len(d) - 1 - i) // window for i in range(len(d))]
    bars = []
    for _, g in d.groupby("grp"):
        g = g.sort_index()
        bars.append({"start": g.iloc[0].date, "end": g.iloc[-1].date, "open": sf(g.iloc[0].open), "high": sf(g.high.max()), "low": sf(g.low.min()), "close": sf(g.iloc[-1].close), "volume": sf(g.volume.sum())})
    k = pd.DataFrame(bars).sort_values("end").reset_index(drop=True)
    if k.empty:
        return k
    rng = (k.high - k.low).replace(0, pd.NA)
    k["body_top"] = k[["open", "close"]].max(axis=1)
    k["body_bottom"] = k[["open", "close"]].min(axis=1)
    k["body_mid"] = (k.body_top + k.body_bottom) / 2.0
    k["body_ratio"] = ((k.close - k.open).abs() / rng).fillna(0.0)
    k["upper_shadow_ratio"] = ((k.high - k.body_top) / rng).fillna(0.0)
    k["lower_shadow_ratio"] = ((k.body_bottom - k.low) / rng).fillna(0.0)
    vol_ma = k.volume.rolling(4, min_periods=1).mean().shift(1)
    k["rel_vol"] = (k.volume / vol_ma.replace(0, pd.NA)).fillna(1.0)
    k["vol_rank_pct"] = k.volume.rolling(8, min_periods=1).rank(pct=True).fillna(0.5)
    return k


def near(a: float, b: float, pct: float) -> bool:
    return sf(a) > 0 and sf(b) > 0 and abs(sf(a) - sf(b)) / sf(b) <= pct


def line_band_width(price: float) -> float:
    return max(0.15, sf(price) * 0.022)


def build_candidate_centers(k: pd.DataFrame) -> List[float]:
    vals = []
    for c in ["close", "body_top", "body_bottom", "body_mid"]:
        vals.extend([sf(x) for x in k[c].tolist() if sf(x) > 0])
    supply = k[(k.upper_shadow_ratio >= 0.25) | (k.rel_vol >= 1.3)]
    vals.extend([sf(x) for x in supply.high.tolist() if sf(x) > 0])
    vals.extend([sf(x) for x in k.high.nlargest(min(3, len(k))).tolist() if sf(x) > 0])
    centers = []
    for v in vals:
        bw = line_band_width(v)
        centers.append(round(round(v / bw) * bw, 2))
    out = []
    for c in sorted(set(centers)):
        if c > 0 and (not out or abs(c - out[-1]) / max(c, 1e-9) > 0.012):
            out.append(c)
    return out


def score_price_band(k: pd.DataFrame, center: float, timeframe: str) -> Dict[str, Any]:
    bw = line_band_width(center)
    tol = max(0.03, bw / max(center, 1e-9))
    max_high, n = sf(k.high.max()), len(k)
    close_score = body_score = supply_score = volume_score = recent_score = 0.0
    close_hits = body_hits = supply_hits = volume_hits = recent_hits = crossing_count = reactive_cross_count = 0
    anchor_dates, anchor_types = [], []
    single_extreme_hit = near(center, max_high, max(0.012, tol / 2))
    for idx, row in k.iterrows():
        recency = 1.5 if idx >= n - 4 else (1.2 if idx >= n - 8 else 1.0)
        rel_vol, vol_rank = sf(row.rel_vol, 1.0), sf(row.vol_rank_pct, 0.5)
        vol_weight = 1.35 if (rel_vol >= 1.8 or vol_rank >= 0.9) else (1.18 if (rel_vol >= 1.3 or vol_rank >= 0.8) else 1.0)
        line_cross = sf(row.low) <= center <= sf(row.high)
        crossing_count += 1 if line_cross else 0
        hit = False
        if near(row.close, center, tol):
            close_hits += 1; hit = True; close_score += 2.8 * recency * vol_weight; anchor_types.append("close")
        if near(row.body_top, center, tol) or near(row.body_bottom, center, tol):
            body_hits += 1; hit = True; body_score += 2.2 * recency * vol_weight; anchor_types.append("body_edge")
        elif sf(row.body_bottom) <= center <= sf(row.body_top):
            body_hits += 1; hit = True; body_score += 1.1 * recency * vol_weight; anchor_types.append("body_cross")
        if near(row.high, center, max(tol, 0.035)) and sf(row.upper_shadow_ratio) >= 0.22 and sf(row.close) <= center * 1.025:
            supply_hits += 1; hit = True; supply_score += (1.5 + min(sf(row.upper_shadow_ratio), 0.8)) * recency * vol_weight; anchor_types.append("upper_supply")
        if near(row.low, center, max(tol, 0.035)) and sf(row.lower_shadow_ratio) >= 0.18 and sf(row.close) >= center * 0.975:
            body_hits += 1; hit = True; body_score += 1.2 * recency * vol_weight; anchor_types.append("lower_support")
        if hit:
            anchor_dates.append(f"{row.start}~{row.end}")
            reactive_cross_count += 1 if line_cross else 0
            if rel_vol >= 1.3 or vol_rank >= 0.8:
                volume_hits += 1; volume_score += 1.8 * recency
            if idx >= n - 8:
                recent_hits += 1; recent_score += 1.5 * recency
    anchor_count = len(set(anchor_dates))
    cleanliness = reactive_cross_count / crossing_count if crossing_count else 1.0
    single_extreme_only = bool(single_extreme_hit and anchor_count <= 1 and close_hits <= 1 and body_hits <= 1)
    single_extreme_penalty = 35.0 if single_extreme_only else 0.0
    chaos_penalty = 12.0 if crossing_count >= 6 and cleanliness < 0.42 else (8.0 if crossing_count >= 4 and cleanliness < 0.35 else 0.0)
    score = min(30, close_score * 3.2) + min(25, body_score * 3.0) + min(15, supply_score * 2.5) + min(10, volume_score * 2.0) + min(10, recent_score * 2.0) + min(10, max(0.0, cleanliness) * 10.0) - single_extreme_penalty - chaos_penalty
    score = max(0.0, min(100.0, score))
    if single_extreme_only:
        line_type, level = "upper_extreme_pressure", "极值压力"
    elif score >= 70 and anchor_count >= 3 and (close_hits + body_hits) >= 2 and volume_hits >= 1:
        line_type, level = "core_line", "高置信核心线"
    elif score >= 58 and anchor_count >= 3 and (close_hits + body_hits) >= 2:
        line_type, level = "core_line", "核心线候选"
    elif score >= 45 and anchor_count >= 2:
        line_type, level = "logic_analysis_line", "逻辑分析线候选"
    else:
        line_type, level = "non_core", "未成线"
    return {"center": rd(center), "band_low": rd(center - bw), "band_high": rd(center + bw), "score": rd(score), "line_type": line_type, "level": level, "timeframe": timeframe, "anchor_count": anchor_count, "close_hits": close_hits, "body_hits": body_hits, "supply_hits": supply_hits, "volume_hits": volume_hits, "recent_hits": recent_hits, "crossing_count": crossing_count, "cleanliness": rd(cleanliness, 3), "single_extreme_only": single_extreme_only, "single_extreme_penalty": rd(single_extreme_penalty), "chaos_penalty": rd(chaos_penalty), "anchor_dates": anchor_dates[:8], "anchor_types": sorted(set(anchor_types)), "max_high": rd(max_high)}


def find_core_line_on_agg(df: pd.DataFrame, window: int, timeframe: str) -> Dict[str, Any]:
    k = aggregate_bars(df, window)
    if k.empty or len(k) < 6:
        return {"level": "数据不足", "line": None, "line_type": "none", "timeframe": timeframe, "text": f"{timeframe}聚合K不足，不能硬画核心线。"}
    scored = sorted([score_price_band(k, c, timeframe) for c in build_candidate_centers(k)], key=lambda x: (x.get("line_type") == "core_line", sf(x.get("score")), sf(x.get("anchor_count"))), reverse=True)
    core = next((x for x in scored if x.get("line_type") == "core_line"), None)
    logic = next((x for x in scored if x.get("line_type") == "logic_analysis_line"), None)
    extreme = next((x for x in scored if x.get("line_type") == "upper_extreme_pressure"), None) or {"center": rd(k.high.max()), "level": "上方极值压力", "line_type": "upper_extreme_pressure", "score": 0}
    if core:
        core["logic_analysis_line"] = logic.get("center") if logic else None
        core["upper_extreme_pressure"] = extreme.get("center")
        core["all_candidates"] = scored[:6]
        core["line"] = core["center"]
        core["text"] = f"{timeframe}核心线约{core['center']}元，区间{core['band_low']}~{core['band_high']}元；反应点{core['anchor_count']}个，收盘命中{core['close_hits']}次，实体/支撑命中{core['body_hits']}次，带量反应{core['volume_hits']}次，穿越洁净度{core['cleanliness']}。上方极值压力约{extreme.get('center')}元，不能与核心线混用。"
        return core
    if logic:
        logic["logic_analysis_line"] = logic.get("center")
        logic["upper_extreme_pressure"] = extreme.get("center")
        logic["all_candidates"] = scored[:6]
        logic["line"] = None
        logic["text"] = f"{timeframe}未识别到高置信核心线；仅识别逻辑分析线约{logic['center']}元，区间{logic['band_low']}~{logic['band_high']}元。上方极值压力约{extreme.get('center')}元。"
        return logic
    return {"level": "未识别", "line": None, "line_type": "none", "timeframe": timeframe, "upper_extreme_pressure": extreme.get("center"), "all_candidates": scored[:6], "text": f"{timeframe}未识别到合格核心线；上方极值压力约{extreme.get('center')}元，不得硬叫核心线。"}


def core_line(df: pd.DataFrame) -> Dict[str, Any]:
    if len(df) < 80:
        return {"level": "数据不足", "line": None, "line_type": "none", "text": "历史K线不足，不能硬画核心线。"}
    q = find_core_line_on_agg(df, 60, "60日聚合K/季线")
    if q.get("line_type") == "core_line":
        return q
    m = find_core_line_on_agg(df, 20, "20日聚合K/月线")
    if m.get("line_type") == "core_line":
        m["seasonal_fallback"] = {k: v for k, v in q.items() if k not in ("monthly_fallback", "seasonal_fallback")}
        return m
    if q.get("line_type") == "logic_analysis_line":
        q["monthly_fallback"] = {k: v for k, v in m.items() if k not in ("monthly_fallback", "seasonal_fallback")}
        return q
    return m if m.get("line_type") == "logic_analysis_line" else q


def core_line_summary(cl: Dict[str, Any]) -> str:
    if cl.get("line_type") == "core_line":
        return f"核心线约 {cl.get('line')} 元｜{cl.get('level')}｜{cl.get('timeframe')}｜区间 {cl.get('band_low')}~{cl.get('band_high')}"
    if cl.get("line_type") == "logic_analysis_line":
        return f"未确认核心线｜逻辑分析线约 {cl.get('logic_analysis_line')} 元｜{cl.get('level')}｜上方极值压力 {cl.get('upper_extreme_pressure')}"
    return f"未识别到核心线｜上方极值压力 {cl.get('upper_extreme_pressure')}"


def safe_jsonable(obj: Any, seen: Optional[set] = None) -> Any:
    if seen is None:
        seen = set()
    if obj is None or isinstance(obj, (str, int, float, bool)):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        return obj
    oid = id(obj)
    if isinstance(obj, (dict, list, tuple, set)):
        if oid in seen:
            return "<circular_ref_removed>"
        seen.add(oid)
        out = {str(k): safe_jsonable(v, seen) for k, v in obj.items()} if isinstance(obj, dict) else [safe_jsonable(v, seen) for v in list(obj)]
        seen.discard(oid)
        return out
    if isinstance(obj, pd.DataFrame):
        return [safe_jsonable(x, seen) for x in obj.to_dict("records")]
    if isinstance(obj, pd.Series):
        return safe_jsonable(obj.to_dict(), seen)
    try:
        if pd.isna(obj):
            return None
    except Exception:
        pass
    if hasattr(obj, "item"):
        try:
            return safe_jsonable(obj.item(), seen)
        except Exception:
            pass
    return ss(obj)


def build_report(hist: Dict[str, pd.DataFrame], stat: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    A, B = pick_samples(hist) if hist else (pd.DataFrame(), pd.DataFrame())
    lines = ["# 五号员工：大涨/涨停归因学习报告", "", f"- 日期：{TARGET}", f"- 启动指纹：{BOOT}", "- 运行纪律：优先读缓存；缓存缺失才用 BaoStock 兜底；不调用 AkShare 逐票历史接口；不荐股。", "- 核心线纪律：季线优先找市场记忆线；最高点只允许叫上方极值压力，不能硬叫核心线。", f"- 数据来源：{stat.get('source')}", f"- 缓存/数据命中：{stat.get('cache_hit', 0)} / 文件数 {stat.get('cache_files', 0)}", f"- 扫描内存保护：全市场仅保留最近{SCAN_KEEP_ROWS}根，入选样本回读完整历史。", "", "## 核心线状态分布"]
    merged = pd.concat([A.assign(_group="A组"), B.assign(_group="B组")], ignore_index=True) if not (A.empty and B.empty) else pd.DataFrame()
    results = []
    if merged.empty:
        lines.append("- 没有有效样本：这是缓存/数据源未覆盖目标日，不代表市场没有涨停/大涨股。")
    else:
        for _, r in merged.iterrows():
            c = ss(r.get("code"))
            cl = core_line(load_full_history(c, hist.get(c, pd.DataFrame())))
            lines.append(f"- {r.get('_group')} {c}：{core_line_summary(cl)}")
    lines += ["", "## A组：当日涨停/极强样本"]
    if A.empty:
        lines.append("- A组为空：未反推出目标日涨停/极强样本。")
    else:
        for i, r in A.iterrows():
            lines.append(f"{i+1}. {r.code}：{r.sample_type}｜涨幅{r.pct_chg}%｜收盘{r.close}元")
    lines += ["", "## B组：近20个交易日累计涨幅前三"]
    if B.empty:
        lines.append("- B组为空：未能计算近20日涨幅。")
    else:
        for i, r in B.iterrows():
            lines.append(f"{i+1}. {r.code}：{r.gain_20d}%｜{r.start_date}→{r.end_date}")
    lines += ["", "## 逐只故事归因"]
    for group, pool in [("A组", A), ("B组", B)]:
        for _, r in pool.iterrows():
            c = ss(r.get("code"))
            cl = core_line(load_full_history(c, hist.get(c, pd.DataFrame())))
            lines += [f"### {c}｜{group}", f"- 核心线状态：{core_line_summary(cl)}", "", cl.get("text", ""), "", "候选线证据："]
            for item in cl.get("all_candidates", [])[:4]:
                lines.append(f"- {item.get('line_type')}｜{item.get('center')}元｜score={item.get('score')}｜anchor={item.get('anchor_count')} close={item.get('close_hits')} body={item.get('body_hits')} supply={item.get('supply_hits')} vol={item.get('volume_hits')} clean={item.get('cleanliness')}")
            lines += ["", "这只票只作为归因样本，不输出买入建议。", "", "**三个核心问题**", "1. 这条线为什么有效，还是只是极值压力？", "2. 资金为什么在这个时间点发动？", "3. 能否沉淀成一号员工可提前识别的因子？", ""]
            results.append({"group": group, "code": c, "sample": r.to_dict(), "core_line": cl})
    payload = {"target_date": TARGET, "boot_id": BOOT, "cache_stats": stat, "a_pool": A.to_dict("records") if not A.empty else [], "b_pool": B.to_dict("records") if not B.empty else [], "results": results, "research_only": True, "core_line_method": "seasonal_agg60_price_band_reaction_density_v1_memsafe_jsonsafe"}
    return "\n".join(lines), payload


def send_report(text: str) -> None:
    print(f"telegram_env_present token={bool(BOT)} chat={bool(CHAT)} requests={requests is not None}", flush=True)
    if not BOT or not CHAT or requests is None:
        print("telegram skipped; report preview below:", flush=True)
        print(text[:1800], flush=True)
        return
    url = f"https://api.telegram.org/bot{BOT}/sendMessage"
    for idx, part in enumerate([text[i:i + 3600] for i in range(0, len(text), 3600)], 1):
        try:
            resp = requests.post(url, json={"chat_id": CHAT, "text": part, "disable_web_page_preview": True}, timeout=30)
            print(f"telegram chunk {idx} status={getattr(resp, 'status_code', 'NA')} body={getattr(resp, 'text', '')[:160]}", flush=True)
        except Exception as exc:
            print(f"telegram failed chunk {idx}: {exc}", flush=True)
        time.sleep(0.4)


def write_outputs(md: str, payload: Dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe_payload = safe_jsonable(payload)
    feedback = safe_jsonable({"boot_id": BOOT, "target_date": TARGET, "network_hist_allowed": False, "data_source": payload.get("cache_stats", {}).get("source"), "core_line_method": payload.get("core_line_method"), "scan_keep_rows": SCAN_KEEP_ROWS, "memsafe_scan": True, "jsonsafe_payload": True})
    files = {"limit_up_research_report.md": md, "big_rise_story_report.md": md, "left_trace_research_report.md": md, "limit_up_research_report.json": json.dumps(safe_payload, ensure_ascii=False, indent=2, allow_nan=False), "employee5_runtime_feedback.json": json.dumps(feedback, ensure_ascii=False, indent=2, allow_nan=False)}
    for name, content in files.items():
        (REPORT_DIR / name).write_text(content, encoding="utf-8")


def main() -> None:
    print(BOOT, flush=True)
    print(f"file={Path(__file__).resolve()}", flush=True)
    print(f"target_date={TARGET} network_hist_allowed=False baostock_fallback={ALLOW_BAOSTOCK_FALLBACK}", flush=True)
    print("cache_dirs=" + " | ".join(str(x) for x in CACHE_DIRS), flush=True)
    hist, stat = load_cache()
    print(f"cache_stats={stat}", flush=True)
    if not hist and ALLOW_BAOSTOCK_FALLBACK:
        print("cache empty; start baostock fallback", flush=True)
        hist, stat = build_baostock_cache()
        print(f"baostock_stats={stat}", flush=True)
    md, payload = build_report(hist, stat)
    write_outputs(md, payload)
    send_report(md[:9000])
    print(f"Employee5 done. Reports: {REPORT_DIR}", flush=True)


if __name__ == "__main__":
    main()
