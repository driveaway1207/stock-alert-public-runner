# -*- coding: utf-8 -*-
from __future__ import annotations

"""五号员工：大涨/涨停归因学习引擎。

核心线 V57：上影线最大共振核心线 + 三问必须作答。
- 候选线只来自已完成的大周期聚合K的 high；
- 最新一根20日/月线窗口、60日/季线窗口默认视为未完成，不参与核心线候选与评分；
- 统计候选线被多少根K的“实体顶—最高价”区间打到；
- 收盘/实体顶/最高价在 ±0.5% 内都算同一条线的共振并加分；
- 切实体超过0.5%每根扣1分；切实体达到2根及以上，不算工整线；
- 每只样本的三个核心问题必须给出基于字段的回答，不再只列问题。
"""

import json, math, os, re, time
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

BOOT = "EMPLOYEE5_PUBLIC_BOOT_20260529_CORE_LINE_V57_QUESTION_ANSWERS"
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "employee5_reports"
TARGET_RAW = os.getenv("EMPLOYEE5_TARGET_DATE") or datetime.now().strftime("%Y%m%d")
TARGET = re.sub(r"\D", "", str(TARGET_RAW))[:8] or datetime.now().strftime("%Y%m%d")
TARGET_DASH = f"{TARGET[:4]}-{TARGET[4:6]}-{TARGET[6:8]}"
TOP_N = int(os.getenv("EMPLOYEE5_TOP_N", "3"))
MIN_ROWS = int(os.getenv("EMPLOYEE5_MIN_CACHE_ROWS", "22"))
SCAN_KEEP_ROWS = int(os.getenv("EMPLOYEE5_SCAN_KEEP_ROWS", "80"))
TOL = float(os.getenv("EMPLOYEE5_CORE_LINE_TOL", "0.005"))
MIN_CORE_HIT_COUNT = int(os.getenv("EMPLOYEE5_MIN_CORE_HIT_COUNT", "3"))
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
        if x is None or pd.isna(x): return default
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


def valid_code(c: str) -> bool:
    return bool(c) and c.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4"))


def bs_code(code: str) -> str:
    c = code_of(code)
    if c.startswith(("600", "601", "603", "605", "688", "689")): return "sh." + c
    if c.startswith(("000", "001", "002", "003", "300", "301")): return "sz." + c
    if c.startswith(("920", "8", "4")): return "bj." + c
    return c


def limit_pct(code: str, name: str = "") -> float:
    if "ST" in ss(name).upper(): return 5.0
    if code.startswith(("688", "689", "300", "301")): return 20.0
    if code.startswith(("920", "8", "4")): return 30.0
    return 10.0


def normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty: return pd.DataFrame()
    mp = {"日期":"date","交易日期":"date","date":"date","time":"date","代码":"code","code":"code","开盘":"open","open":"open","开盘价":"open","收盘":"close","close":"close","收盘价":"close","最高":"high","high":"high","最高价":"high","最低":"low","low":"low","最低价":"low","成交量":"volume","volume":"volume","vol":"volume","成交额":"amount","amount":"amount","涨跌幅":"pct_chg","pctChg":"pct_chg","pct_chg":"pct_chg","涨幅":"pct_chg"}
    d = df.rename(columns={c: mp.get(str(c), mp.get(str(c).lower(), c)) for c in df.columns}).copy()
    if not {"date", "open", "high", "low", "close"}.issubset(d.columns): return pd.DataFrame()
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if col in d.columns: d[col] = d[col].map(sf)
    if "volume" not in d.columns: d["volume"] = 0.0
    if "amount" not in d.columns: d["amount"] = 0.0
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
    if isinstance(obj, list): return obj
    if isinstance(obj, dict):
        for k in ["rows", "data", "klines", "kline", "daily", "history", "records"]:
            if k in obj: return obj[k]
    return []


def read_cache_file(p: Path) -> pd.DataFrame:
    try:
        suf = p.suffix.lower()
        if suf == ".json": return normalize_hist(pd.DataFrame(rows_from_obj(json.loads(p.read_text(encoding="utf-8")))))
        if suf in [".csv", ".txt"]: return normalize_hist(pd.read_csv(p))
        if suf in [".pkl", ".pickle"]: return normalize_hist(pd.read_pickle(p))
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame()


def iter_cache_files() -> List[Path]:
    out, seen_dirs = [], set()
    for d in CACHE_DIRS:
        try: key = str(d.resolve())
        except Exception: key = str(d)
        if key in seen_dirs or not d.exists(): continue
        seen_dirs.add(key)
        for pat in ["*.json", "*.csv", "*.txt", "*.pkl", "*.pickle"]: out.extend(d.rglob(pat))
    uniq, seen = [], set()
    for f in out:
        try: key = str(f.resolve())
        except Exception: key = str(f)
        if key not in seen:
            seen.add(key); uniq.append(f)
    return uniq


def load_cache() -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    files = iter_cache_files(); hist: Dict[str, pd.DataFrame] = {}
    stat = {"source":"cache","cache_files":len(files),"cache_hit":0,"cache_bad":0,"cache_short":0,"target_date":TARGET,"cache_dirs":[str(x) for x in CACHE_DIRS],"scan_keep_rows":SCAN_KEEP_ROWS,"memsafe_scan":True}
    for i, p in enumerate(files, 1):
        c = code_of(p)
        if not valid_code(c): continue
        df = read_cache_file(p)
        if df.empty: stat["cache_bad"] += 1; continue
        if len(df) < MIN_ROWS or df.iloc[-1]["date"].replace("-", "") < TARGET: stat["cache_short"] += 1; continue
        CACHE_FILE_MAP[c] = p; hist[c] = df.tail(max(30, SCAN_KEEP_ROWS)).reset_index(drop=True); stat["cache_hit"] += 1
        if i % 500 == 0: print(f"cache scan {i}/{len(files)} hit={stat['cache_hit']}", flush=True)
    return hist, stat


def load_full_history(code: str, fallback: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    c = code_of(code); p = CACHE_FILE_MAP.get(c)
    if p is not None and Path(p).exists():
        df = read_cache_file(Path(p))
        if not df.empty: return df
    return fallback if fallback is not None else pd.DataFrame()


def save_cache_file(code: str, df: pd.DataFrame) -> None:
    try:
        MAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (MAIN_CACHE_DIR / f"{code}.json").write_text(json.dumps({"target_date": TARGET, "rows": df.to_dict("records")}, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        print(f"cache save failed {code}: {exc}", flush=True)


def baostock_all_codes() -> List[Tuple[str, str]]:
    if bs is None:
        print("baostock package missing", flush=True); return []
    lg = bs.login(); print(f"baostock login: {getattr(lg, 'error_code', '')} {getattr(lg, 'error_msg', '')}", flush=True)
    rs = bs.query_all_stock(day=TARGET_DASH); out: List[Tuple[str, str]] = []
    while rs.error_code == "0" and rs.next():
        row = rs.get_row_data(); c = code_of(row[0] if row else ""); name = row[1] if len(row) > 1 else ""
        if valid_code(c): out.append((c, name or c))
    dedup, seen = [], set()
    for c, n in out:
        if c not in seen: seen.add(c); dedup.append((c, n))
    return dedup


def baostock_fetch_hist(code: str) -> pd.DataFrame:
    if bs is None: return pd.DataFrame()
    target_dt = datetime.strptime(TARGET_DASH, "%Y-%m-%d")
    start = (target_dt - timedelta(days=140)).strftime("%Y-%m-%d")
    fields = "date,code,open,high,low,close,volume,amount,pctChg"
    try:
        rs = bs.query_history_k_data_plus(bs_code(code), fields, start_date=start, end_date=TARGET_DASH, frequency="d", adjustflag="3")
        data = []
        while rs.error_code == "0" and rs.next(): data.append(rs.get_row_data())
        return normalize_hist(pd.DataFrame(data, columns=fields.split(","))) if data else pd.DataFrame()
    except Exception as exc:
        print(f"baostock hist failed {code}: {exc}", flush=True); return pd.DataFrame()


def build_baostock_cache() -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    stat = {"source":"baostock_fallback","cache_files":0,"cache_hit":0,"cache_bad":0,"cache_short":0,"target_date":TARGET,"baostock_used":True}; hist: Dict[str, pd.DataFrame] = {}
    if not ALLOW_BAOSTOCK_FALLBACK: stat["baostock_disabled"] = True; return hist, stat
    codes = baostock_all_codes(); codes = codes[:BAOSTOCK_LIMIT] if BAOSTOCK_LIMIT > 0 else codes; stat["baostock_universe"] = len(codes); start_time = time.time()
    for i, (code, _) in enumerate(codes, 1):
        df = baostock_fetch_hist(code)
        if df.empty: stat["cache_bad"] += 1; continue
        if len(df) < MIN_ROWS or df.iloc[-1]["date"].replace("-", "") < TARGET: stat["cache_short"] += 1; continue
        hist[code] = df.tail(max(30, SCAN_KEEP_ROWS)).reset_index(drop=True); stat["cache_hit"] += 1; save_cache_file(code, df)
        if i == 1 or i % 200 == 0 or i == len(codes): print(f"baostock fallback {i}/{len(codes)} hit={stat['cache_hit']} speed={i/max(time.time()-start_time,0.001):.2f}/s current={code}", flush=True)
    try:
        if bs is not None: bs.logout()
    except Exception: pass
    stat["cache_files"] = len(list(MAIN_CACHE_DIR.glob("*.json"))) if MAIN_CACHE_DIR.exists() else 0
    return hist, stat


def gain20(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df) < 22: return None
    a, b = df.iloc[-21], df.iloc[-1]
    g = (sf(b.close) / sf(a.close) - 1.0) * 100 if sf(a.close) else 0.0
    return {"gain_20d":rd(g),"start_date":a.date,"end_date":b.date,"start_close":rd(a.close),"end_close":rd(b.close)}


def pick_samples(hist: Dict[str, pd.DataFrame]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    a_rows, b_rows = [], []
    for i, (code, df) in enumerate(hist.items(), 1):
        if df is None or df.empty: continue
        last = df.iloc[-1]; pct = sf(last.pct_chg); lp = limit_pct(code)
        if pct >= lp - 0.35 or pct >= min(8.0, lp * 0.75): a_rows.append({"code":code,"name":code,"date":last.date,"close":rd(last.close),"pct_chg":rd(pct),"sample_type":"涨停/近涨停" if pct >= lp - 0.35 else "极强上涨"})
        g = gain20(df)
        if g: b_rows.append({"code":code,"name":code,**g})
        if i % 500 == 0: print(f"sample scan {i}/{len(hist)} A={len(a_rows)} B={len(b_rows)}", flush=True)
    A, B = pd.DataFrame(a_rows), pd.DataFrame(b_rows)
    if not A.empty: A = A.sort_values(["pct_chg","close"], ascending=[False,False]).head(TOP_N).reset_index(drop=True)
    if not B.empty: B = B.sort_values("gain_20d", ascending=False).head(TOP_N).reset_index(drop=True)
    return A, B


def aggregate_bars(df: pd.DataFrame, window: int) -> pd.DataFrame:
    if df is None or df.empty or len(df) < max(22, window * 3): return pd.DataFrame()
    d = df.copy().reset_index(drop=True); d["grp"] = [(len(d) - 1 - i) // window for i in range(len(d))]
    bars = []
    for _, g in d.groupby("grp"):
        g = g.sort_index(); bars.append({"start":g.iloc[0].date,"end":g.iloc[-1].date,"open":sf(g.iloc[0].open),"high":sf(g.high.max()),"low":sf(g.low.min()),"close":sf(g.iloc[-1].close),"volume":sf(g.volume.sum())})
    k = pd.DataFrame(bars).sort_values("end").reset_index(drop=True)
    if k.empty: return k
    rng = (k.high - k.low).replace(0, pd.NA)
    k["body_top"] = k[["open","close"]].max(axis=1); k["body_bottom"] = k[["open","close"]].min(axis=1)
    k["upper_shadow_ratio"] = ((k.high - k.body_top) / rng).fillna(0.0)
    vol_ma = k.volume.rolling(4, min_periods=1).mean().shift(1)
    k["rel_vol"] = (k.volume / vol_ma.replace(0, pd.NA)).fillna(1.0)
    k["vol_rank_pct"] = k.volume.rolling(8, min_periods=1).rank(pct=True).fillna(0.5)
    return k


def latest_bar_snapshot(k: pd.DataFrame) -> Dict[str, Any]:
    if k is None or k.empty: return {}
    r = k.iloc[-1]
    return {"start": ss(r.start), "end": ss(r.end), "open": rd(r.open), "high": rd(r.high), "low": rd(r.low), "close": rd(r.close), "body_top": rd(r.body_top), "volume": rd(r.volume)}


def completed_bars_for_coreline(k: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """核心线只用已经完成的历史大周期K。最新一根聚合K默认视为当前未完成，不参与候选与评分。"""
    if k is None or k.empty:
        return pd.DataFrame(), {}
    excluded = latest_bar_snapshot(k)
    if len(k) <= 1:
        return pd.DataFrame(), excluded
    return k.iloc[:-1].reset_index(drop=True), excluded


def near(a: float, b: float, tol: float = TOL) -> bool:
    a, b = sf(a), sf(b)
    return bool(a > 0 and b > 0 and abs(a - b) / b <= tol)


def score_shadow_line(k: pd.DataFrame, line: float, timeframe: str) -> Dict[str, Any]:
    L = sf(line); score = 0.0; hit = close_touch = body_touch = high_touch = vol_hit = body_cut = upper_hit = top_as_shadow = 0
    hit_dates, cut_dates, evidence = [], [], []
    for _, r in k.iterrows():
        bt, bb, hi, cl = sf(r.body_top), sf(r.body_bottom), sf(r.high), sf(r.close)
        if L <= 0 or bt <= 0 or hi <= 0: continue
        tag = f"{r.start}~{r.end}"; is_hit = bt * (1 - TOL) <= L <= hi * (1 + TOL); is_cut = bb < L < bt * (1 - TOL)
        if is_hit:
            hit += 1; score += 1.0; hit_dates.append(tag)
            if bt * (1 + TOL) < L <= hi * (1 + TOL): upper_hit += 1
            if near(bt, L): body_touch += 1; top_as_shadow += 1; score += 2.0
            if near(cl, L): close_touch += 1; score += 2.0
            if near(hi, L): high_touch += 1; score += 1.0
            if sf(r.rel_vol, 1.0) >= 1.3 or sf(r.vol_rank_pct, 0.5) >= 0.8: vol_hit += 1; score += 1.0
            evidence.append({"date":tag,"open":rd(r.open),"high":rd(hi),"close":rd(cl),"body_top":rd(bt),"close_touch":near(cl,L),"body_top_touch":near(bt,L),"high_touch":near(hi,L),"rel_vol":rd(r.rel_vol,2),"vol_rank_pct":rd(r.vol_rank_pct,2)})
        if is_cut:
            body_cut += 1; score -= 1.0; cut_dates.append(tag)
    clean = body_cut < 2
    if clean and hit >= 5 and score >= 5: line_type, level = "core_line", "高置信上影共振核心线"
    elif clean and hit >= MIN_CORE_HIT_COUNT and score >= 3: line_type, level = "core_line", "上影共振核心线候选"
    elif hit >= 2: line_type, level = "logic_analysis_line", "逻辑分析线候选" if clean else "不工整逻辑线"
    else: line_type, level = "non_core", "未成线"
    return {"line":rd(L),"core_line":rd(L),"line_type":line_type,"level":level,"timeframe":timeframe,"score":rd(score),"hit_count":hit,"upper_shadow_hit_count":upper_hit,"entity_top_as_shadow_count":top_as_shadow,"close_touch_count":close_touch,"body_top_touch_count":body_touch,"high_touch_count":high_touch,"volume_hit_count":vol_hit,"body_cut_count":body_cut,"clean_core_line":clean,"tol_pct":rd(TOL*100,3),"hit_dates":hit_dates[:20],"cut_dates":cut_dates[:20],"evidence":evidence[:20],"upper_extreme_pressure":rd(k.high.max()) if not k.empty else 0.0,"core_band_low":rd(L*(1-TOL)),"core_band_high":rd(L*(1+TOL)),"confirmation_source":"max_upper_shadow_resonance_lowest_high"}


def find_shadow_coreline(df: pd.DataFrame, window: int, timeframe: str) -> Dict[str, Any]:
    raw_k = aggregate_bars(df, window)
    k, excluded = completed_bars_for_coreline(raw_k)
    if raw_k.empty or len(raw_k) < 6:
        return {"level":"数据不足","line":None,"line_type":"none","timeframe":timeframe,"text":f"{timeframe}聚合K不足，不能硬画核心线。"}
    if k.empty or len(k) < 5:
        return {"level":"已排除当前未完成K后数据不足","line":None,"line_type":"none","timeframe":timeframe,"excluded_current_bar":excluded,"text":f"{timeframe}已排除最新未完成聚合K后，历史完成K数量不足，不能硬画核心线。"}
    scored = [score_shadow_line(k, c, timeframe) for c in sorted(set(rd(x) for x in k.high.tolist() if sf(x) > 0))]
    scored = sorted(scored, key=lambda x:(int(x.get("clean_core_line",False)), int(x.get("hit_count",0)), sf(x.get("score")), int(x.get("close_touch_count",0))+int(x.get("body_top_touch_count",0)), int(x.get("high_touch_count",0)), -sf(x.get("line"))), reverse=True)
    best = next((x for x in scored if x.get("clean_core_line") and x.get("hit_count",0) >= MIN_CORE_HIT_COUNT), scored[0] if scored else None)
    if not best: return {"level":"未识别","line":None,"line_type":"none","timeframe":timeframe,"excluded_current_bar":excluded,"text":f"{timeframe}未识别到有效候选。"}
    best["all_candidates"] = scored[:10]
    best["excluded_current_bar"] = excluded
    best["excluded_current_bar_note"] = "最新一根聚合K默认视为当前未完成，未参与核心线候选与评分。"
    if best.get("line_type") == "core_line":
        best["text"] = f"{timeframe}按上影线最大共振法识别核心线：{best['line']}元。已排除最新未完成聚合K（{excluded.get('start')}~{excluded.get('end')}）。共有{best['hit_count']}根完成K的实体顶—最高价区间打到它；收盘贴线{best['close_touch_count']}次、实体顶贴线{best['body_top_touch_count']}次、最高价贴线{best['high_touch_count']}次、带量打到{best['volume_hit_count']}次；实体切割{best['body_cut_count']}次。容差为±{best['tol_pct']}%，容差只统计共振，不改变核心线锚点。"
    else:
        best["text"] = f"{timeframe}已排除最新未完成聚合K（{excluded.get('start')}~{excluded.get('end')}），未形成工整高置信核心线；最强逻辑线为{best.get('line')}元，打到{best.get('hit_count')}根完成K，实体切割{best.get('body_cut_count')}根，score={best.get('score')}。"
    return best


def core_line(df: pd.DataFrame) -> Dict[str, Any]:
    if len(df) < 80: return {"level":"数据不足","line":None,"line_type":"none","text":"历史K线不足，不能硬画核心线。"}
    s, m = find_shadow_coreline(df, 60, "60日聚合K/季线"), find_shadow_coreline(df, 20, "20日聚合K/月线")
    cand = [x for x in [s, m] if x and x.get("line") is not None]
    if not cand: return s if s else m
    tw = {"60日聚合K/季线":1.0,"20日聚合K/月线":0.0}
    best = sorted(cand, key=lambda x:(x.get("line_type")=="core_line", int(x.get("clean_core_line",False)), int(x.get("hit_count",0)), sf(x.get("score")), tw.get(ss(x.get("timeframe")),0.0), -sf(x.get("line"))), reverse=True)[0]
    best["seasonal_candidate"] = s; best["monthly_candidate"] = m
    return best


def core_line_summary(cl: Dict[str, Any]) -> str:
    if ss(cl.get("line_type")) == "core_line": return f"核心线 {cl.get('line')} 元｜{cl.get('level')}｜{cl.get('timeframe')}｜打到{cl.get('hit_count')}根｜收盘贴线{cl.get('close_touch_count')}｜实体顶贴线{cl.get('body_top_touch_count')}｜最高价贴线{cl.get('high_touch_count')}｜实体切割{cl.get('body_cut_count')}｜score={cl.get('score')}｜已排除当前未完成K"
    if ss(cl.get("line_type")) == "logic_analysis_line": return f"未确认工整核心线｜逻辑线 {cl.get('line')} 元｜{cl.get('level')}｜{cl.get('timeframe')}｜打到{cl.get('hit_count')}根｜实体切割{cl.get('body_cut_count')}｜score={cl.get('score')}｜已排除当前未完成K"
    return f"未识别到核心线｜上方极值压力 {cl.get('upper_extreme_pressure')}"


def safe_jsonable(obj: Any, seen: Optional[set] = None) -> Any:
    if seen is None: seen = set()
    if obj is None or isinstance(obj, (str, int, float, bool)): return None if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)) else obj
    oid = id(obj)
    if isinstance(obj, (dict, list, tuple, set)):
        if oid in seen: return "<circular_ref_removed>"
        seen.add(oid); out = {str(k):safe_jsonable(v, seen) for k, v in obj.items()} if isinstance(obj, dict) else [safe_jsonable(v, seen) for v in list(obj)]; seen.discard(oid); return out
    if isinstance(obj, pd.DataFrame): return [safe_jsonable(x, seen) for x in obj.to_dict("records")]
    if isinstance(obj, pd.Series): return safe_jsonable(obj.to_dict(), seen)
    try:
        if pd.isna(obj): return None
    except Exception: pass
    if hasattr(obj, "item"):
        try: return safe_jsonable(obj.item(), seen)
        except Exception: pass
    return ss(obj)


def candidate_line_brief(x: Dict[str, Any]) -> str:
    return f"- {x.get('line_type')}｜线{x.get('line')}元｜打到{x.get('hit_count')}根｜收盘贴线{x.get('close_touch_count')}｜实体顶贴线{x.get('body_top_touch_count')}｜最高价贴线{x.get('high_touch_count')}｜带量{x.get('volume_hit_count')}｜切实体{x.get('body_cut_count')}｜score={x.get('score')}｜工整={x.get('clean_core_line')}"


def sample_ref_price(sample: Any) -> float:
    if isinstance(sample, pd.Series): sample = sample.to_dict()
    if isinstance(sample, dict):
        for key in ["close", "end_close"]:
            v = sf(sample.get(key))
            if v > 0: return v
    return 0.0


def three_question_answer_lines(cl: Dict[str, Any], sample: Any) -> List[str]:
    line = sf(cl.get("line")); px = sample_ref_price(sample); lt = ss(cl.get("line_type")); clean = bool(cl.get("clean_core_line"))
    hit = int(sf(cl.get("hit_count"))); close_n = int(sf(cl.get("close_touch_count"))); body_n = int(sf(cl.get("body_top_touch_count"))); high_n = int(sf(cl.get("high_touch_count"))); vol_n = int(sf(cl.get("volume_hit_count"))); cut_n = int(sf(cl.get("body_cut_count"))); score = rd(cl.get("score"))
    if lt == "core_line":
        a1 = f"答：这条线具备有效性，不是单根极值。证据是有{hit}根已完成大周期K打到它，收盘贴线{close_n}次、实体顶贴线{body_n}次、最高价贴线{high_n}次、带量打到{vol_n}次，实体切割{cut_n}次，score={score}。"
    elif lt == "logic_analysis_line":
        a1 = f"答：这条线暂时只能算逻辑线，不是高置信核心线。它打到{hit}根，实体切割{cut_n}次，score={score}，还需要更多干净共振验证。"
    else:
        a1 = "答：当前没有识别到合格核心线，不能把普通高点硬解释成核心线。"
    if line > 0 and px > 0:
        diff = (px / line - 1.0) * 100.0
        if abs(diff) <= TOL * 100:
            a2 = f"答：样本价{rd(px)}元贴近核心线{rd(line)}元，说明样本正处在核心线反应区，后续要看是否放量站稳或回踩不破。"
        elif diff > 0:
            a2 = f"答：样本价{rd(px)}元高于核心线{rd(line)}元约{rd(diff)}%，更像越过旧供应后的反应；后续重点看是否跌回线下。"
        else:
            a2 = f"答：样本价{rd(px)}元低于核心线{rd(line)}元约{rd(abs(diff))}%，说明这条线不是当前发动的直接触发点，只能作为上方确认位继续观察。"
    else:
        a2 = "答：样本价格或核心线缺失，暂时不能判断发动点与核心线的直接关系。"
    if lt == "core_line" and clean and hit >= MIN_CORE_HIT_COUNT:
        a3 = "答：可以沉淀为一号员工的结构因子：大周期形成干净上影共振线，后续日线若高质量突破或突破后回踩不破，可进入候选评分。"
    else:
        a3 = "答：暂时不适合沉淀为一号员工正式因子，只能继续作为五号员工观察样本积累。"
    return ["1. 这条线为什么有效，还是只是极值压力？", a1, "2. 为什么在这个时间点发动？", a2, "3. 能否沉淀成一号员工可提前识别的因子？", a3]


def build_report(hist: Dict[str, pd.DataFrame], stat: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    A, B = pick_samples(hist) if hist else (pd.DataFrame(), pd.DataFrame())
    lines = ["# 五号员工：大涨/涨停归因学习报告","",f"- 日期：{TARGET}",f"- 启动指纹：{BOOT}","- 运行纪律：优先读缓存；缓存缺失才用 BaoStock 兜底；不调用 AkShare 逐票历史接口；不荐股。","- 核心线纪律：使用上影线最大共振法；候选线来自已完成大周期 high；最新一根20日/月线窗口、60日/季线窗口默认未完成，不参与候选与评分；打到K线越多越核心；收盘/实体顶/最高价±0.5%共振额外加分；切实体两根以上不算工整线。","- 三问纪律：每只样本必须给出答案，不能只列问题。",f"- 数据来源：{stat.get('source')}",f"- 缓存/数据命中：{stat.get('cache_hit',0)} / 文件数 {stat.get('cache_files',0)}",f"- 扫描内存保护：全市场仅保留最近{SCAN_KEEP_ROWS}根，入选样本回读完整历史。","","## 核心线状态分布"]
    merged = pd.concat([A.assign(_group="A组"), B.assign(_group="B组")], ignore_index=True) if not (A.empty and B.empty) else pd.DataFrame(); results = []
    if merged.empty: lines.append("- 没有有效样本：这是缓存/数据源未覆盖目标日，不代表市场没有涨停/大涨股。")
    else:
        for _, r in merged.iterrows():
            c = ss(r.get("code")); cl = core_line(load_full_history(c, hist.get(c, pd.DataFrame()))); lines.append(f"- {r.get('_group')} {c}：{core_line_summary(cl)}")
    lines += ["","## A组：当日涨停/极强样本"]
    if A.empty: lines.append("- A组为空：未反推出目标日涨停/极强样本。")
    else:
        for i, r in A.iterrows(): lines.append(f"{i+1}. {r.code}：{r.sample_type}｜涨幅{r.pct_chg}%｜收盘{r.close}元")
    lines += ["","## B组：近20个交易日累计涨幅前三"]
    if B.empty: lines.append("- B组为空：未能计算近20日涨幅。")
    else:
        for i, r in B.iterrows(): lines.append(f"{i+1}. {r.code}：{r.gain_20d}%｜{r.start_date}→{r.end_date}")
    lines += ["","## 逐只故事归因"]
    for group, pool in [("A组", A), ("B组", B)]:
        for _, r in pool.iterrows():
            c = ss(r.get("code")); cl = core_line(load_full_history(c, hist.get(c, pd.DataFrame()))); qa = three_question_answer_lines(cl, r)
            lines += [f"### {c}｜{group}",f"- 核心线状态：{core_line_summary(cl)}","",cl.get("text", ""),"",f"排除的当前未完成K：{cl.get('excluded_current_bar', {})}","","候选线证据："]
            for item in cl.get("all_candidates", [])[:6]: lines.append(candidate_line_brief(item))
            lines += ["","关键共振K："]
            for ev in cl.get("evidence", [])[:8]: lines.append(f"- {ev.get('date')}｜高{ev.get('high')}｜收{ev.get('close')}｜实体顶{ev.get('body_top')}｜收盘贴线={ev.get('close_touch')} 实体顶贴线={ev.get('body_top_touch')} 最高价贴线={ev.get('high_touch')}")
            lines += ["","这只票只作为归因样本，不输出交易建议。","","**三个核心问题与回答**"] + qa + [""]
            results.append({"group":group,"code":c,"sample":r.to_dict(),"core_line":cl,"three_question_answers":qa})
    payload = {"target_date":TARGET,"boot_id":BOOT,"cache_stats":stat,"a_pool":A.to_dict("records") if not A.empty else [],"b_pool":B.to_dict("records") if not B.empty else [],"results":results,"research_only":True,"core_line_method":"max_upper_shadow_resonance_lowest_high_v57_question_answers_tol_0_5pct","core_line_tol":TOL,"exclude_current_agg_bar":True,"three_questions_must_answer":True}
    return "\n".join(lines), payload


def send_report(text: str) -> None:
    print(f"telegram_env_present token={bool(BOT)} chat={bool(CHAT)} requests={requests is not None}", flush=True)
    if not BOT or not CHAT or requests is None: print("telegram skipped; report preview below:", flush=True); print(text[:1800], flush=True); return
    url = f"https://api.telegram.org/bot{BOT}/sendMessage"
    for idx, part in enumerate([text[i:i+3600] for i in range(0, len(text), 3600)], 1):
        try:
            resp = requests.post(url, json={"chat_id":CHAT,"text":part,"disable_web_page_preview":True}, timeout=30); print(f"telegram chunk {idx} status={getattr(resp,'status_code','NA')} body={getattr(resp,'text','')[:160]}", flush=True)
        except Exception as exc: print(f"telegram failed chunk {idx}: {exc}", flush=True)
        time.sleep(0.4)


def write_outputs(md: str, payload: Dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True); safe = safe_jsonable(payload)
    feedback = safe_jsonable({"boot_id":BOOT,"target_date":TARGET,"network_hist_allowed":False,"data_source":payload.get("cache_stats",{}).get("source"),"core_line_method":payload.get("core_line_method"),"core_line_tol":TOL,"exclude_current_agg_bar":True,"three_questions_must_answer":True,"scan_keep_rows":SCAN_KEEP_ROWS,"memsafe_scan":True,"jsonsafe_payload":True})
    files = {"limit_up_research_report.md":md,"big_rise_story_report.md":md,"left_trace_research_report.md":md,"limit_up_research_report.json":json.dumps(safe, ensure_ascii=False, indent=2, allow_nan=False),"employee5_runtime_feedback.json":json.dumps(feedback, ensure_ascii=False, indent=2, allow_nan=False)}
    for name, content in files.items(): (REPORT_DIR / name).write_text(content, encoding="utf-8")


def main() -> None:
    print(BOOT, flush=True); print(f"file={Path(__file__).resolve()}", flush=True); print(f"target_date={TARGET} network_hist_allowed=False baostock_fallback={ALLOW_BAOSTOCK_FALLBACK}", flush=True); print("cache_dirs=" + " | ".join(str(x) for x in CACHE_DIRS), flush=True)
    hist, stat = load_cache(); print(f"cache_stats={stat}", flush=True)
    if not hist and ALLOW_BAOSTOCK_FALLBACK:
        print("cache empty; start baostock fallback", flush=True); hist, stat = build_baostock_cache(); print(f"baostock_stats={stat}", flush=True)
    md, payload = build_report(hist, stat); write_outputs(md, payload); send_report(md[:9000]); print(f"Employee5 done. Reports: {REPORT_DIR}", flush=True)


if __name__ == "__main__":
    main()
