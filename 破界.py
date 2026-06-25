# -*- coding: utf-8 -*-
from __future__ import annotations

"""
破界.py
从零重写版｜员工三号破界思路精简落地

定位：
1）只做“历史核心共振线 + 近500日共振触发线 + 最近20日高质量突破”的海选；
2）核心线不再使用旧破界指标、融合界、VBP包装、宽压力带描述；
3）先找可交易的主评测线，再看突破质量、接受质量、交易防守位、上方空间和RR；
4）外部生产链路不改：仍读取公共kline_cache，仍输出破界报告目录，仍可选Telegram推送。
"""

import json
import math
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

BOOT = "POJIE_EMPLOYEE3_DUAL_LINE_BREAKOUT_REWRITE_V1_20260625"
START_TS = time.time()

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "破界报告"
OUTPUT_MD = REPORT_DIR / "核心线突破海选报告.md"
OUTPUT_CSV = REPORT_DIR / "核心线突破海选明细.csv"
OUTPUT_JSON = REPORT_DIR / "核心线突破海选数据.json"
SELF_CHECK_JSON = REPORT_DIR / "破界自检.json"

CACHE_DIRS = [
    ROOT / "kline_cache",
    ROOT / "employee5_kline_cache",
    ROOT / "data" / "kline_cache",
    ROOT / "cache" / "kline_cache",
    ROOT.parent / "kline_cache",
]

TARGET_ENV_KEYS = [
    "POJIE_TARGET_DATE",
    "EMPLOYEE3_TARGET_DATE",
    "SELECTION_TRADE_DATE",
    "TARGET_TRADE_DATE",
    "DATA_GATE_TARGET_DATE",
    "LAST_TRADE_DAY",
    "LAST_TRADE_DAY_OVERRIDE",
]

BOT = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()
CHAT = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
ENABLE_TELEGRAM = (os.getenv("POJIE_SEND_TELEGRAM") or os.getenv("ENABLE_TELEGRAM") or "0").strip() in {"1", "true", "True", "YES", "yes", "发送"}

# -------------------- 参数：只保留破界必要项 --------------------
MAX_STOCKS = int(os.getenv("POJIE_MAX_STOCKS", os.getenv("MAX_STOCKS", "0")))
MIN_ROWS = int(os.getenv("POJIE_MIN_ROWS", "80"))
BREAKOUT_LOOKBACK_DAYS = int(os.getenv("POJIE_BREAKOUT_LOOKBACK_DAYS", "20"))
FIVE_HUNDRED_DAY_LOOKBACK = int(os.getenv("POJIE_TRIGGER_LOOKBACK_DAYS", "500"))
MONTHLY_MIN_ROWS = int(os.getenv("POJIE_MONTHLY_MIN_ROWS", "60"))

CORE_LINE_TOL = float(os.getenv("POJIE_CORE_LINE_TOL", "0.010"))
CORE_LINE_BAND_TOL = float(os.getenv("POJIE_CORE_LINE_BAND_TOL", "0.015"))
BODY_TOP_EDGE_TOL = float(os.getenv("POJIE_BODY_TOP_EDGE_TOL", "0.005"))
MIN_CORE_RESONANCE = int(os.getenv("POJIE_MIN_CORE_RESONANCE", "3"))
LINE_TOP_CANDIDATE_LIMIT = int(os.getenv("POJIE_LINE_TOP_CANDIDATE_LIMIT", "10"))

BREAK_PREV_BELOW_PCT = float(os.getenv("POJIE_BREAK_PREV_BELOW_PCT", "0.005"))
BREAK_PREV_NEAR_TOL = float(os.getenv("POJIE_BREAK_PREV_NEAR_TOL", "0.002"))
BREAK_CLOSE_ABOVE_PCT = float(os.getenv("POJIE_BREAK_CLOSE_ABOVE_PCT", "0.003"))
BREAK_MIN_PCT_CHG = float(os.getenv("POJIE_BREAK_MIN_PCT_CHG", "1.0"))
BREAK_MIN_BODY_PCT = float(os.getenv("POJIE_BREAK_MIN_BODY_PCT", "0.005"))
BREAK_BODY_RATIO_MIN = float(os.getenv("POJIE_BREAK_BODY_RATIO_MIN", "0.28"))
BREAK_CLOSE_POS_MIN = float(os.getenv("POJIE_BREAK_CLOSE_POS_MIN", "0.66"))
BREAK_UPPER_SHADOW_MAX = float(os.getenv("POJIE_BREAK_UPPER_SHADOW_MAX", "0.36"))
BREAK_ENTITY_ABOVE_LINE_MIN = float(os.getenv("POJIE_BREAK_ENTITY_ABOVE_LINE_MIN", "0.32"))
BREAK_MIN_VOLUME_RATIO = float(os.getenv("POJIE_BREAK_MIN_VOLUME_RATIO", "1.15"))
BREAK_HEALTHY_VOLUME_RATIO = float(os.getenv("POJIE_BREAK_HEALTHY_VOLUME_RATIO", "1.45"))

DEFENSE_BUFFER_PCT = float(os.getenv("POJIE_DEFENSE_BUFFER_PCT", "0.015"))
MAX_DEFENSE_DISTANCE_PCT = float(os.getenv("POJIE_MAX_DEFENSE_DISTANCE_PCT", "10.5"))
MAX_DISTANCE_LINE_PCT = float(os.getenv("POJIE_MAX_DISTANCE_LINE_PCT", "18.0"))
MIN_FORMAL_SCORE = float(os.getenv("POJIE_FORMAL_MIN_SCORE", "78"))
MIN_OBSERVE_SCORE = float(os.getenv("POJIE_OBSERVE_MIN_SCORE", "60"))
MIN_FORMAL_RR = float(os.getenv("POJIE_FORMAL_MIN_RR", "1.25"))
MIN_FORMAL_SPACE_PCT = float(os.getenv("POJIE_FORMAL_MIN_SPACE_PCT", "7.0"))
OVERHEAD_PRESSURE_MIN_ABOVE_PCT = float(os.getenv("POJIE_OVERHEAD_PRESSURE_MIN_ABOVE_PCT", "0.025"))
OVERHEAD_PRESSURE_BAND_TOL = float(os.getenv("POJIE_OVERHEAD_PRESSURE_BAND_TOL", "0.018"))
PRICE_DISCOVERY_MAX_RISK_PCT = float(os.getenv("POJIE_PRICE_DISCOVERY_MAX_RISK_PCT", "8.5"))
PRICE_DISCOVERY_MAX_DISTANCE_PCT = float(os.getenv("POJIE_PRICE_DISCOVERY_MAX_DISTANCE_PCT", "12.0"))
HOT_20D_PCT = float(os.getenv("POJIE_HOT_20D_PCT", "25.0"))
MIN_AMOUNT20 = float(os.getenv("POJIE_MIN_AMOUNT20", "50000000"))

TOP_LIMIT = int(os.getenv("POJIE_TOP_LIMIT", "3"))
OBSERVE_LIMIT = int(os.getenv("POJIE_OBSERVE_LIMIT", "20"))
CACHE_PROGRESS_EVERY = int(os.getenv("POJIE_CACHE_PROGRESS_EVERY", "1000"))
SCREEN_PROGRESS_EVERY = int(os.getenv("POJIE_SCREEN_PROGRESS_EVERY", "200"))
PARALLEL = (os.getenv("POJIE_PARALLEL") or "1").strip() not in {"0", "false", "False", "no", "NO"}
WORKERS = max(1, int(os.getenv("POJIE_WORKERS", str(min(4, max(1, os.cpu_count() or 1))))))

HARD_RISK_NAME_KEYWORDS = tuple(x for x in os.getenv("POJIE_HARD_RISK_NAME_KEYWORDS", "ST,*ST,退,退市").split(",") if x)


def log(msg: str) -> None:
    print(f"[破界重写][{time.time() - START_TS:7.1f}s] {msg}", flush=True)


def now_bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def previous_workday(d: datetime) -> datetime:
    x = d
    while x.weekday() >= 5:
        x -= timedelta(days=1)
    return x


def target_raw() -> str:
    for key in TARGET_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value
    bj = now_bj()
    if bj.weekday() >= 5 or bj.hour < 20 or (bj.hour == 20 and bj.minute < 30):
        bj = previous_workday(bj - timedelta(days=1))
    return bj.strftime("%Y%m%d")


TARGET = re.sub(r"\D", "", target_raw())[:8]
TARGET_DASH = f"{TARGET[:4]}-{TARGET[4:6]}-{TARGET[6:8]}" if len(TARGET) == 8 else ""


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.replace(",", "").replace("%", "").strip()
            if x == "":
                return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def rd(x: Any, n: int = 3) -> float:
    return round(sf(x), n)


def code_of(x: Any) -> str:
    text = ss(x)
    m = re.search(r"(\d{6})", text)
    return m.group(1) if m else ""


def valid_code(code: Any) -> bool:
    c = code_of(code)
    return bool(c) and len(c) == 6 and c[0] in "0368"


def norm_date(x: Any) -> str:
    s = ss(x)
    if not s:
        return ""
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return ""


def pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0 if b else 0.0


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


# -------------------- 数据读取 --------------------
def normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    col_map = {
        "日期": "date", "交易日期": "date", "date": "date", "time": "date",
        "代码": "code", "股票代码": "code", "证券代码": "code", "symbol": "code", "code": "code",
        "名称": "name", "股票名称": "name", "股票简称": "name", "name": "name",
        "开盘": "open", "开盘价": "open", "open": "open",
        "最高": "high", "最高价": "high", "high": "high",
        "最低": "low", "最低价": "low", "low": "low",
        "收盘": "close", "收盘价": "close", "close": "close",
        "成交量": "volume", "volume": "volume", "vol": "volume",
        "成交额": "amount", "amount": "amount",
        "涨跌幅": "pct_chg", "涨幅": "pct_chg", "pct_chg": "pct_chg", "pctChg": "pct_chg",
    }
    d = df.rename(columns={c: col_map.get(str(c), col_map.get(str(c).lower(), c)) for c in df.columns}).copy()
    if not {"date", "open", "high", "low", "close"}.issubset(d.columns):
        return pd.DataFrame()
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if col in d.columns:
            d[col] = d[col].map(sf)
    for col, default in [("volume", 0.0), ("amount", 0.0), ("code", ""), ("name", "")]:
        if col not in d.columns:
            d[col] = default
    d["date"] = d["date"].map(norm_date)
    d["code"] = d["code"].map(code_of)
    d["name"] = d["name"].map(ss)
    d = d[(d["date"] != "") & (d["open"] > 0) & (d["high"] > 0) & (d["low"] > 0) & (d["close"] > 0)]
    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if TARGET_DASH:
        d = d[d["date"] <= TARGET_DASH].reset_index(drop=True)
    if d.empty:
        return pd.DataFrame()
    if "pct_chg" not in d.columns or float(d["pct_chg"].abs().sum()) == 0:
        prev = d["close"].shift(1)
        d["pct_chg"] = (d["close"] / prev - 1.0) * 100.0
        d.loc[prev <= 0, "pct_chg"] = 0.0
    return d


def read_cache_file(path: Path) -> pd.DataFrame:
    try:
        if path.suffix.lower() == ".csv":
            return normalize_hist(pd.read_csv(path))
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            rows = obj.get("rows") or obj.get("data") or obj.get("klines") or obj.get("items") or []
        else:
            rows = obj
        return normalize_hist(pd.DataFrame(rows))
    except Exception:
        return pd.DataFrame()


def iter_cache_files() -> List[Path]:
    seen: Dict[str, Path] = {}
    for directory in CACHE_DIRS:
        if not directory.exists():
            continue
        for p in sorted(directory.glob("*")):
            if p.suffix.lower() not in {".csv", ".json"}:
                continue
            code = code_of(p.name)
            if valid_code(code) and code not in seen:
                seen[code] = p
    files = list(seen.values())
    if MAX_STOCKS > 0:
        files = files[:MAX_STOCKS]
    return files


def valid_stock_display_name(code: Any, name: Any) -> bool:
    c = code_of(code)
    n = ss(name)
    if not n:
        return False
    bad = {"nan", "none", "null", "--", "-", "名称待补", "名称缺失"}
    if n.lower() in bad or n in bad:
        return False
    if c and n in {c, f"sh.{c}", f"sz.{c}", f"bj.{c}", f"SH.{c}", f"SZ.{c}", f"BJ.{c}"}:
        return False
    return True


def scan_name_frame(name_map: Dict[str, str], df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    code_cols = [c for c in ["代码", "股票代码", "证券代码", "code", "symbol", "原始代码"] if c in df.columns]
    name_cols = [c for c in ["名称", "股票名称", "股票简称", "证券简称", "name", "股票中文名称"] if c in df.columns]
    for cc in code_cols:
        for nc in name_cols:
            for _, r in df[[cc, nc]].dropna(how="all").iterrows():
                code = code_of(r.get(cc, ""))
                name = ss(r.get(nc, ""))
                if valid_code(code) and valid_stock_display_name(code, name) and code not in name_map:
                    name_map[code] = name


def load_name_map() -> Dict[str, str]:
    name_map: Dict[str, str] = {}
    search_dirs = [ROOT, ROOT / "outputs", ROOT / "data", ROOT / "cache", ROOT.parent / "outputs"] + CACHE_DIRS
    seen: set = set()
    for directory in search_dirs:
        if not directory.exists():
            continue
        for p in sorted(list(directory.glob("*.csv")) + list(directory.glob("*.json")), key=lambda x: x.stat().st_mtime, reverse=True)[:80]:
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            low = p.name.lower()
            if not any(k in low for k in ["universe", "stock", "name", "股票", "status", "map", "usable"]):
                continue
            try:
                if p.suffix.lower() == ".csv":
                    scan_name_frame(name_map, pd.read_csv(p, dtype=str))
                else:
                    obj = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(obj, dict):
                        rows = []
                        for k, v in obj.items():
                            if isinstance(v, str):
                                rows.append({"代码": k, "名称": v})
                            elif isinstance(v, dict):
                                vv = dict(v)
                                vv.setdefault("代码", k)
                                rows.append(vv)
                        scan_name_frame(name_map, pd.DataFrame(rows))
                    elif isinstance(obj, list):
                        scan_name_frame(name_map, pd.DataFrame(obj))
            except Exception:
                continue
    return name_map


def load_cache() -> Tuple[Dict[str, pd.DataFrame], Dict[str, str], Dict[str, Any]]:
    files = iter_cache_files()
    names = load_name_map()
    hist: Dict[str, pd.DataFrame] = {}
    stat = {"cache_files": len(files), "cache_hit": 0, "bad": 0, "short": 0}
    log(f"开始读取缓存：files={len(files)}")
    for i, p in enumerate(files, 1):
        code = code_of(p.name)
        df = read_cache_file(p)
        if df.empty:
            stat["bad"] += 1
        elif len(df) < MIN_ROWS:
            stat["short"] += 1
        else:
            if code and "code" in df.columns:
                df.loc[df["code"].astype(str).str.len() == 0, "code"] = code
            if code and code not in names and "name" in df.columns:
                vals = [ss(x) for x in df["name"].tolist() if valid_stock_display_name(code, x)]
                if vals:
                    names[code] = vals[-1]
            hist[code] = df
            stat["cache_hit"] += 1
        if i == 1 or i % CACHE_PROGRESS_EVERY == 0 or i == len(files):
            log(f"缓存进度 {i}/{len(files)}｜命中{stat['cache_hit']}｜坏{stat['bad']}｜短{stat['short']}｜当前{code}")
    return hist, names, stat


# -------------------- 月线核心线 --------------------
def aggregate_monthly_bars(df: pd.DataFrame) -> pd.DataFrame:
    d = normalize_hist(df)
    if d.empty or len(d) < MONTHLY_MIN_ROWS:
        return pd.DataFrame()
    dt = pd.to_datetime(d["date"], errors="coerce")
    d = d[dt.notna()].copy()
    if d.empty:
        return pd.DataFrame()
    d["_month"] = pd.to_datetime(d["date"], errors="coerce").dt.to_period("M").astype(str)
    bars: List[Dict[str, Any]] = []
    for month, g in d.groupby("_month", sort=True):
        g = g.sort_values("date").reset_index(drop=True)
        bars.append({
            "month": ss(month),
            "start": ss(g.iloc[0]["date"]),
            "end": ss(g.iloc[-1]["date"]),
            "open": sf(g.iloc[0]["open"]),
            "high": sf(g["high"].max()),
            "low": sf(g["low"].min()),
            "close": sf(g.iloc[-1]["close"]),
            "volume": sf(g["volume"].sum()) if "volume" in g.columns else 0.0,
            "amount": sf(g["amount"].sum()) if "amount" in g.columns else 0.0,
        })
    k = pd.DataFrame(bars).sort_values("end").reset_index(drop=True)
    if k.empty:
        return k
    k["body_top"] = k[["open", "close"]].max(axis=1)
    k["body_bottom"] = k[["open", "close"]].min(axis=1)
    rng = (k["high"] - k["low"]).replace(0, np.nan)
    k["body_ratio"] = ((k["close"] - k["open"]).abs() / rng).fillna(0.0)
    k["upper_shadow_ratio"] = ((k["high"] - k["body_top"]) / rng).fillna(0.0)
    return k


def line_candidate_sources(k: pd.DataFrame) -> Dict[float, str]:
    """压力线候选只取上沿类价位；普通收盘价不再单独主导核心线。"""
    sources: Dict[float, set] = {}

    def add(price: Any, source: str) -> None:
        v = rd(price, 3)
        if v > 0:
            sources.setdefault(v, set()).add(source)

    prev_volume = k["volume"].shift(1) if "volume" in k.columns else pd.Series([0.0] * len(k))
    vol_med = sf(k["volume"].median()) if "volume" in k.columns and not k.empty else 0.0

    for idx, r in k.iterrows():
        high = sf(r.get("high"))
        body_top = sf(r.get("body_top"))
        close = sf(r.get("close"))
        open_ = sf(r.get("open"))
        volume = sf(r.get("volume"))
        prev_vol = sf(prev_volume.iloc[idx]) if idx < len(prev_volume) else 0.0
        body_ratio = sf(r.get("body_ratio"))
        upper_shadow_ratio = sf(r.get("upper_shadow_ratio"))

        add(high, "月高点/上影边界")
        add(body_top, "月实体顶")

        if close > open_ and body_ratio >= 0.25 and volume > 0 and (volume >= max(prev_vol * 1.15, vol_med * 1.10)):
            add(close, "放量阳线收盘确认")
        if upper_shadow_ratio >= 0.22 and high > body_top:
            add(high, "长上影反应高点")

    return {line: "+".join(sorted(srcs)) for line, srcs in sources.items()}


def batch_score_lines(k: pd.DataFrame, sources: Dict[float, str], tol: float = CORE_LINE_TOL, chunk_size: int = 768) -> List[Dict[str, Any]]:
    if k.empty or not sources:
        return []
    need = {"high", "body_top", "body_bottom", "close", "volume"}
    if not need.issubset(k.columns):
        return []

    hi = pd.to_numeric(k["high"], errors="coerce").fillna(0.0).to_numpy(dtype=float)[:, None]
    bt = pd.to_numeric(k["body_top"], errors="coerce").fillna(0.0).to_numpy(dtype=float)[:, None]
    bb = pd.to_numeric(k["body_bottom"], errors="coerce").fillna(0.0).to_numpy(dtype=float)[:, None]
    cl = pd.to_numeric(k["close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)[:, None]
    vol = pd.to_numeric(k["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)[:, None]
    valid = (hi > 0) & (bt > 0) & (bb > 0)
    if not bool(valid.any()):
        return []

    vol_flat = vol[:, 0]
    valid_flat = valid[:, 0]
    vol_med = sf(np.median(vol_flat[valid_flat])) if bool(valid_flat.any()) else 0.0
    is_volume_bar = (vol_med > 0) & (vol >= vol_med * 1.30)

    lines = sorted(sf(x) for x in sources.keys() if sf(x) > 0)
    out: List[Dict[str, Any]] = []

    for start in range(0, len(lines), max(32, chunk_size)):
        part = lines[start:start + max(32, chunk_size)]
        L = np.array(part, dtype=float)[None, :]
        denom = np.maximum(L, 1e-9)

        entity_accept = valid & (bb > L)
        raw_entity_cut = valid & (bb < L) & (L < bt)
        edge_touch = valid & (np.abs(bt - L) / denom <= BODY_TOP_EDGE_TOL)
        entity_cut = raw_entity_cut & (~edge_touch)
        normal_zone = valid & (~entity_cut) & (~entity_accept)

        high_touch = normal_zone & (np.abs(hi - L) / denom <= tol)
        upper_shadow_hit = normal_zone & (bt <= L) & (L <= hi)
        body_top_touch = normal_zone & (np.abs(bt - L) / denom <= tol)
        close_aux_touch = normal_zone & (np.abs(cl - L) / denom <= tol) & ((np.abs(hi - L) / denom <= CORE_LINE_BAND_TOL) | (np.abs(bt - L) / denom <= CORE_LINE_BAND_TOL))

        primary_touch = high_touch | upper_shadow_hit | body_top_touch
        touch = primary_touch | close_aux_touch

        hit_arr = touch.sum(axis=0).astype(int)
        primary_arr = primary_touch.sum(axis=0).astype(int)
        high_arr = high_touch.sum(axis=0).astype(int)
        upper_arr = (upper_shadow_hit & (~body_top_touch)).sum(axis=0).astype(int)
        body_arr = body_top_touch.sum(axis=0).astype(int)
        close_arr = close_aux_touch.sum(axis=0).astype(int)
        cut_arr = entity_cut.sum(axis=0).astype(int)
        accept_arr = entity_accept.sum(axis=0).astype(int)
        vol_res_arr = (touch & is_volume_bar).sum(axis=0).astype(int)
        vol_cut_arr = (entity_cut & is_volume_bar).sum(axis=0).astype(int)
        vol_accept_arr = (entity_accept & is_volume_bar).sum(axis=0).astype(int)

        net_arr = hit_arr + primary_arr * 0.15 + vol_res_arr * 0.75 - cut_arr * 0.80 - vol_cut_arr * 1.50

        for j, line in enumerate(part):
            hit = int(hit_arr[j])
            primary = int(primary_arr[j])
            net = float(net_arr[j])
            level = "核心线候选" if primary >= MIN_CORE_RESONANCE and net > 0 else "未成线"
            out.append({
                "line": rd(line, 3),
                "net_score": rd(net, 3),
                "effective_resonance_count": hit,
                "primary_resonance_count": primary,
                "volume_resonance_count": int(vol_res_arr[j]),
                "high_touch_count": int(high_arr[j]),
                "upper_shadow_hit_count": int(upper_arr[j]),
                "body_top_touch_count": int(body_arr[j]),
                "close_touch_count": int(close_arr[j]),
                "entity_cut_count": int(cut_arr[j]),
                "volume_entity_cut_count": int(vol_cut_arr[j]),
                "entity_accept_count": int(accept_arr[j]),
                "volume_entity_accept_count": int(vol_accept_arr[j]),
                "line_type": "core_line" if level == "核心线候选" else "non_core",
                "level": level,
                "source": sources.get(rd(line, 3), sources.get(line, "")),
            })
    return out


def group_by_band(scored: List[Dict[str, Any]], tol: float = CORE_LINE_BAND_TOL) -> List[List[Dict[str, Any]]]:
    xs = sorted([x for x in scored if sf(x.get("line")) > 0], key=lambda x: sf(x.get("line")))
    groups: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    base = 0.0
    for x in xs:
        line = sf(x.get("line"))
        if not cur or (base > 0 and abs(line - base) / base <= tol):
            cur.append(x)
            base = base or line
        else:
            groups.append(cur)
            cur = [x]
            base = line
    if cur:
        groups.append(cur)
    return groups


def rank_line(x: Dict[str, Any], mode: str, last_close: float = 0.0) -> Tuple[float, ...]:
    line = sf(x.get("line"))
    distance_penalty = abs(line / last_close - 1.0) if last_close > 0 else 0.0
    if mode == "trigger":
        return (
            sf(x.get("primary_resonance_count")),
            sf(x.get("volume_resonance_count")),
            sf(x.get("net_score")),
            -sf(x.get("entity_cut_count")),
            -distance_penalty,
            line,
        )
    return (
        sf(x.get("net_score")),
        sf(x.get("primary_resonance_count")),
        sf(x.get("volume_resonance_count")),
        -sf(x.get("entity_cut_count")),
        line,
    )


def choose_resonance_lines(df: pd.DataFrame, label: str, lookback_days: int = 0, mode: str = "historical") -> Dict[str, Any]:
    d = normalize_hist(df)
    if lookback_days > 0 and len(d) > lookback_days:
        d = d.tail(lookback_days).reset_index(drop=True)
    raw_k = aggregate_monthly_bars(d)
    if raw_k.empty or len(raw_k) < 3:
        return {"line": None, "level": "数据不足", "reason": f"{label}自然月K不足", "line_label": label, "top_candidates": []}

    # 排除当前未完成自然月，避免当月影线临时污染核心线。
    completed = raw_k.iloc[:-1].reset_index(drop=True)
    if completed.empty:
        return {"line": None, "level": "数据不足", "reason": f"{label}无已完成自然月K", "line_label": label, "top_candidates": []}

    sources = line_candidate_sources(completed)
    scored = [x for x in batch_score_lines(completed, sources) if ss(x.get("line_type")) == "core_line"]
    if not scored:
        return {"line": None, "level": "未识别", "reason": f"{label}未识别到有效核心线", "line_label": label, "top_candidates": []}

    last_close = sf(d.iloc[-1]["close"]) if not d.empty else 0.0
    band_winners = [max(g, key=lambda x: rank_line(x, mode, last_close)) for g in group_by_band(scored)]
    ranked = sorted(band_winners, key=lambda x: rank_line(x, mode, last_close), reverse=True)

    top: List[Dict[str, Any]] = []
    for item in ranked[:max(3, LINE_TOP_CANDIDATE_LIMIT)]:
        obj = dict(item)
        obj["line_label"] = label
        obj["rank_mode"] = mode
        obj["lookback_days"] = int(lookback_days)
        top.append(obj)

    best = dict(top[0])
    best.update({
        "top_candidates": top,
        "line_label": label,
        "rank_mode": mode,
        "lookback_days": int(lookback_days),
        "all_candidates_count": len(sources),
        "effective_candidates_count": len(scored),
        "band_candidates_count": len(band_winners),
        "excluded_current_bar_end": ss(raw_k.iloc[-1].get("end", "")),
    })
    return best


def choose_historical_core_resonance_line(df: pd.DataFrame) -> Dict[str, Any]:
    return choose_resonance_lines(df, "历史核心共振线", lookback_days=0, mode="historical")


def choose_five_hundred_day_resonance_trigger_line(df: pd.DataFrame) -> Dict[str, Any]:
    return choose_resonance_lines(df, "五百日共振触发线", lookback_days=FIVE_HUNDRED_DAY_LOOKBACK, mode="trigger")


# -------------------- 日线突破、接受、交易定价 --------------------
def kline_features(row: Any, prev_close: float = 0.0, line: float = 0.0) -> Dict[str, float]:
    open_ = sf(row.get("open"))
    high = sf(row.get("high"))
    low = sf(row.get("low"))
    close = sf(row.get("close"))
    volume = sf(row.get("volume"))
    rng = max(high - low, 1e-9)
    body = abs(close - open_)
    body_top = max(open_, close)
    body_bottom = min(open_, close)
    upper = max(0.0, high - body_top)
    pct_chg = sf(row.get("pct_chg"))
    if pct_chg == 0 and prev_close > 0:
        pct_chg = pct(close, prev_close)
    entity_above = 0.0
    if line > 0 and body > 0:
        entity_above = max(0.0, body_top - max(line, body_bottom)) / body
    return {
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
        "body_top": body_top, "body_bottom": body_bottom,
        "range": rng, "body": body,
        "body_pct": body / max(open_, 1e-9),
        "body_ratio": body / rng,
        "close_pos": (close - low) / rng,
        "upper_shadow_ratio": upper / rng,
        "pct_chg": pct_chg,
        "entity_above_line_ratio": entity_above,
        "is_limit_like": pct_chg >= 9.6 and abs(close - high) / max(close, 1e-9) <= 0.003,
    }


def daily_breakout_quality(df: pd.DataFrame, line: float) -> Dict[str, Any]:
    d = normalize_hist(df)
    if d.empty or line <= 0 or len(d) < 30:
        return {"is_breakout": False, "reason": "数据不足"}
    start = max(1, len(d) - BREAKOUT_LOOKBACK_DAYS)
    best: Optional[Dict[str, Any]] = None

    for idx in range(start, len(d)):
        r = d.iloc[idx]
        prev = d.iloc[idx - 1]
        prev_close = sf(prev["close"])
        close = sf(r["close"])
        high = sf(r["high"])
        open_ = sf(r["open"])
        low = sf(r["low"])
        if close <= 0 or high <= 0 or prev_close <= 0:
            continue

        prev_below = prev_close <= line * (1.0 - BREAK_PREV_BELOW_PCT)
        prev_near_from_below = prev_close <= line * (1.0 + BREAK_PREV_NEAR_TOL) and sf(prev["low"]) < line
        close_above = close >= line * (1.0 + BREAK_CLOSE_ABOVE_PCT)
        high_above = high >= line * (1.0 + BREAK_CLOSE_ABOVE_PCT)
        if not ((prev_below or prev_near_from_below) and close_above and high_above):
            continue

        f = kline_features(r, prev_close, line)
        body_positive = close > open_
        volume_window = d.iloc[max(0, idx - 20):idx]
        vol_med = sf(volume_window["volume"].median()) if not volume_window.empty else 0.0
        vol_ratio = safe_div(sf(r.get("volume")), vol_med, 0.0) if vol_med > 0 else 0.0
        volume_ok = vol_ratio >= BREAK_MIN_VOLUME_RATIO or f["is_limit_like"]

        hard_ok = (
            body_positive
            and f["pct_chg"] >= BREAK_MIN_PCT_CHG
            and f["body_pct"] >= BREAK_MIN_BODY_PCT
            and f["body_ratio"] >= BREAK_BODY_RATIO_MIN
            and f["close_pos"] >= BREAK_CLOSE_POS_MIN
            and f["upper_shadow_ratio"] <= BREAK_UPPER_SHADOW_MAX
            and f["entity_above_line_ratio"] >= BREAK_ENTITY_ABOVE_LINE_MIN
            and volume_ok
        )
        if not hard_ok:
            continue

        score = 48.0
        score += min(12.0, max(0.0, (f["close_pos"] - BREAK_CLOSE_POS_MIN) / max(1.0 - BREAK_CLOSE_POS_MIN, 1e-9) * 12.0))
        score += min(10.0, f["entity_above_line_ratio"] * 10.0)
        score += min(9.0, max(0.0, f["body_ratio"] - BREAK_BODY_RATIO_MIN) * 18.0)
        score += min(8.0, max(0.0, f["pct_chg"] - BREAK_MIN_PCT_CHG) * 0.9)
        if vol_ratio >= BREAK_HEALTHY_VOLUME_RATIO:
            score += 8.0
        elif vol_ratio >= BREAK_MIN_VOLUME_RATIO:
            score += 4.0
        if f["is_limit_like"]:
            score += 4.0
        score -= max(0.0, f["upper_shadow_ratio"] - 0.20) * 10.0

        obj = {
            "is_breakout": True,
            "idx": int(idx),
            "date": ss(r.get("date")),
            "line": rd(line, 3),
            "close": rd(close, 3),
            "pct_chg": rd(f["pct_chg"], 3),
            "body_ratio": rd(f["body_ratio"], 3),
            "close_pos": rd(f["close_pos"], 3),
            "upper_shadow_ratio": rd(f["upper_shadow_ratio"], 3),
            "entity_above_line_ratio": rd(f["entity_above_line_ratio"], 3),
            "volume_ratio": rd(vol_ratio, 3),
            "score": rd(score, 3),
            "reason": "有效日线破界",
        }
        if best is None or (obj["score"], idx) > (sf(best.get("score")), int(best.get("idx", 0))):
            best = obj

    return best if best is not None else {"is_breakout": False, "reason": "近20日无高质量从下向上突破"}


def evaluate_acceptance(df: pd.DataFrame, bidx: int, line: float) -> Dict[str, Any]:
    d = normalize_hist(df)
    if d.empty or bidx < 0 or bidx >= len(d) or line <= 0:
        return {"score": 0.0, "status": "无接受数据", "failed": True}
    post = d.iloc[bidx:].copy().reset_index(drop=True)
    last = d.iloc[-1]
    last_close = sf(last["close"])
    defense = line * (1.0 - DEFENSE_BUFFER_PCT)
    close_below_line = int((post["close"] < line * (1.0 - 0.006)).sum())
    close_below_defense = int((post["close"] < defense).sum())
    above_days = int((post["close"] >= line * (1.0 + BREAK_CLOSE_ABOVE_PCT)).sum())
    min_low = sf(post["low"].min())
    pullback_touched = min_low <= line * 1.025
    last_above = last_close >= line * (1.0 + BREAK_CLOSE_ABOVE_PCT)

    score = 4.0
    if last_above:
        score += 6.0
    if above_days >= 2:
        score += 4.0
    if above_days >= 4:
        score += 2.0
    if pullback_touched and close_below_defense == 0:
        score += 4.0
    if close_below_line == 0:
        score += 3.0
    elif close_below_line == 1 and last_above:
        score += 1.0
    if close_below_defense > 0:
        score -= 8.0
    if not last_above:
        score -= 6.0

    if close_below_defense > 0:
        status = "跌破交易防守，接受失败"
        failed = True
    elif not last_above:
        status = "突破后未站稳，等待重新收回线"
        failed = True
    elif pullback_touched and close_below_line <= 1:
        status = "突破后回踩/贴线接受"
        failed = False
    else:
        status = "突破后悬空接受"
        failed = False

    return {
        "score": rd(max(0.0, min(20.0, score)), 3),
        "status": status,
        "failed": failed,
        "above_days": above_days,
        "close_below_line_count": close_below_line,
        "close_below_defense_count": close_below_defense,
        "pullback_touched": bool(pullback_touched),
        "min_post_low": rd(min_low, 3),
    }


def pressure_touch_count(window: pd.DataFrame, line: float) -> int:
    if window.empty or line <= 0:
        return 0
    hi = pd.to_numeric(window["high"], errors="coerce").fillna(0.0)
    bt = window[["open", "close"]].max(axis=1)
    cl = pd.to_numeric(window["close"], errors="coerce").fillna(0.0)
    return int(((hi.sub(line).abs() / line <= CORE_LINE_TOL) | ((bt <= line) & (line <= hi)) | (bt.sub(line).abs() / line <= CORE_LINE_TOL) | (cl.sub(line).abs() / line <= CORE_LINE_TOL)).sum())


def build_overhead_pressure(df: pd.DataFrame, last_close: float) -> Dict[str, Any]:
    d = normalize_hist(df)
    if d.empty or last_close <= 0:
        return {"price": None, "space_pct": 999.0, "type": "无压力数据", "hit_count": 0}
    min_price = last_close * (1.0 + OVERHEAD_PRESSURE_MIN_ABOVE_PCT)
    raw: List[float] = []
    for _, r in d.iterrows():
        high = sf(r.get("high"))
        body_top = max(sf(r.get("open")), sf(r.get("close")))
        close = sf(r.get("close"))
        for price in (high, body_top, close):
            if price >= min_price:
                raw.append(rd(price, 3))
    if not raw:
        return {"price": None, "space_pct": 999.0, "type": "全历史价格发现", "hit_count": 0}
    raw = sorted(set(raw))
    groups: List[List[float]] = []
    cur: List[float] = []
    base = 0.0
    for price in raw:
        if not cur or abs(price - base) / max(base, 1e-9) <= OVERHEAD_PRESSURE_BAND_TOL:
            cur.append(price)
            base = base or price
        else:
            groups.append(cur)
            cur = [price]
            base = price
    if cur:
        groups.append(cur)

    best_groups = []
    for g in groups:
        price = min(g)
        hits = pressure_touch_count(d, price)
        best_groups.append({"price": price, "hit_count": hits, "width_pct": pct(max(g), min(g)) if min(g) else 0.0})
    reliable = [x for x in best_groups if x["hit_count"] >= 2]
    target = min(reliable or best_groups, key=lambda x: x["price"])
    return {
        "price": rd(target["price"], 3),
        "space_pct": rd(pct(target["price"], last_close), 3),
        "type": "第一有效压力" if target["hit_count"] >= 2 else "第一价格高点压力",
        "hit_count": int(target["hit_count"]),
        "width_pct": rd(target.get("width_pct", 0.0), 3),
    }


def evaluate_trade_pricing(df: pd.DataFrame, bidx: int, line: float) -> Dict[str, Any]:
    d = normalize_hist(df)
    if d.empty or line <= 0:
        return {"score": 0.0, "passed": False, "reason": "交易定价数据不足"}
    last_close = sf(d.iloc[-1]["close"])
    defense = line * (1.0 - DEFENSE_BUFFER_PCT)
    risk_pct = pct(last_close, defense) if defense > 0 else 999.0
    distance_line_pct = pct(last_close, line)
    pressure = build_overhead_pressure(d, last_close)
    pressure_price = pressure.get("price")
    space_pct = sf(pressure.get("space_pct"), 999.0)
    price_discovery = pressure_price is None
    rr = 99.0 if price_discovery and risk_pct > 0 else safe_div(space_pct, risk_pct, 0.0)

    if price_discovery:
        passed = risk_pct <= PRICE_DISCOVERY_MAX_RISK_PCT and distance_line_pct <= PRICE_DISCOVERY_MAX_DISTANCE_PCT
    else:
        passed = risk_pct <= MAX_DEFENSE_DISTANCE_PCT and distance_line_pct <= MAX_DISTANCE_LINE_PCT and space_pct >= MIN_FORMAL_SPACE_PCT and rr >= MIN_FORMAL_RR

    score = 0.0
    if risk_pct <= 5:
        score += 8
    elif risk_pct <= 8:
        score += 6
    elif risk_pct <= MAX_DEFENSE_DISTANCE_PCT:
        score += 3
    if distance_line_pct <= 6:
        score += 5
    elif distance_line_pct <= 12:
        score += 3
    elif distance_line_pct <= MAX_DISTANCE_LINE_PCT:
        score += 1
    if price_discovery:
        score += 7 if passed else 2
    else:
        if space_pct >= 15:
            score += 5
        elif space_pct >= MIN_FORMAL_SPACE_PCT:
            score += 3
        if rr >= 2:
            score += 5
        elif rr >= MIN_FORMAL_RR:
            score += 3

    reason = "价格发现，按移动止盈管理" if price_discovery else f"上方{pressure.get('type')} {pressure_price}"
    return {
        "score": rd(max(0.0, min(20.0, score)), 3),
        "passed": bool(passed),
        "reason": reason,
        "last_close": rd(last_close, 3),
        "defense_price": rd(defense, 3),
        "risk_pct": rd(risk_pct, 3),
        "distance_line_pct": rd(distance_line_pct, 3),
        "pressure_price": None if pressure_price is None else rd(pressure_price, 3),
        "pressure_type": pressure.get("type", ""),
        "space_pct": rd(space_pct, 3),
        "rr": rd(rr, 3),
        "price_discovery": bool(price_discovery),
    }


def amount20(df: pd.DataFrame) -> float:
    d = normalize_hist(df)
    if d.empty:
        return 0.0
    w = d.tail(20)
    if "amount" in w.columns and float(w["amount"].sum()) > 0:
        return sf(w["amount"].mean())
    # 兜底：不同缓存成交量单位不完全一致，因此只作为流动性提示，不做强估算。
    return 0.0


def risk_flags(code: str, name: str, df: pd.DataFrame, bidx: int, line: float, trade: Dict[str, Any]) -> Dict[str, Any]:
    d = normalize_hist(df)
    flags: List[str] = []
    hard = False
    if any(k and k in name for k in HARD_RISK_NAME_KEYWORDS):
        flags.append("名称命中ST/退市类硬风险")
        hard = True
    amt20 = amount20(d)
    if amt20 > 0 and amt20 < MIN_AMOUNT20:
        flags.append(f"20日成交额偏低 {rd(amt20 / 100000000, 2)}亿")
    elif amt20 <= 0:
        flags.append("成交额字段缺失或不可用")

    if len(d) >= 21:
        hot20 = pct(sf(d.iloc[-1]["close"]), sf(d.iloc[-21]["close"]))
    else:
        hot20 = 0.0
    if hot20 >= HOT_20D_PCT:
        flags.append(f"近20日涨幅{rd(hot20, 2)}%，有短线过热")
    if sf(trade.get("risk_pct")) > MAX_DEFENSE_DISTANCE_PCT:
        flags.append(f"防守距离{trade.get('risk_pct')}%，交易风险偏大")
    if sf(trade.get("distance_line_pct")) > MAX_DISTANCE_LINE_PCT:
        flags.append(f"距主评测线{trade.get('distance_line_pct')}%，追高距离偏大")

    return {"hard_reject": hard, "flags": flags, "amount20": rd(amt20, 2), "hot20_pct": rd(hot20, 3)}


# -------------------- 主评测线选择 --------------------
def line_level_score(line_info: Dict[str, Any], line_type: str) -> float:
    primary = sf(line_info.get("primary_resonance_count"))
    volume = sf(line_info.get("volume_resonance_count"))
    net = sf(line_info.get("net_score"))
    cut = sf(line_info.get("entity_cut_count"))
    score = 4.0 + min(8.0, primary * 1.6) + min(5.0, volume * 2.0) + min(4.0, max(0.0, net) * 0.25) - min(4.0, cut * 0.6)
    if line_type == "五百日触发线":
        score += 1.0
    return rd(max(0.0, min(20.0, score)), 3)


def breakout_scaled_score(br: Dict[str, Any]) -> float:
    raw = sf(br.get("score"))
    return rd(max(0.0, min(25.0, raw / 80.0 * 25.0)), 3)


def dedupe_line_options(options: List[Tuple[str, Dict[str, Any]]]) -> List[Tuple[str, Dict[str, Any]]]:
    out: List[Tuple[str, Dict[str, Any]]] = []
    for line_type, info in sorted(options, key=lambda x: sf(x[1].get("line"))):
        line = sf(info.get("line"))
        if line <= 0:
            continue
        duplicated = False
        for _, old in out:
            old_line = sf(old.get("line"))
            if old_line > 0 and abs(line - old_line) / old_line <= 0.004:
                duplicated = True
                # 保留共振更强的版本。
                if rank_line(info, "trigger" if line_type == "五百日触发线" else "historical") > rank_line(old, "trigger"):
                    old.update(info)
                break
        if not duplicated:
            out.append((line_type, info))
    return out


def assess_line_option(code: str, name: str, df: pd.DataFrame, line_type: str, line_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    line = sf(line_info.get("line"))
    if line <= 0:
        return None
    br = daily_breakout_quality(df, line)
    if not br.get("is_breakout"):
        return None
    bidx = int(br.get("idx", -1))
    trade = evaluate_trade_pricing(df, bidx, line)
    accept = evaluate_acceptance(df, bidx, line)
    risk = risk_flags(code, name, df, bidx, line, trade)

    line_score = line_level_score(line_info, line_type)
    br_score = breakout_scaled_score(br)
    accept_score = sf(accept.get("score"))
    pricing_score = sf(trade.get("score"))
    risk_penalty = 0.0
    if risk.get("hard_reject"):
        risk_penalty += 30.0
    if not trade.get("passed"):
        risk_penalty += 8.0
    if accept.get("failed"):
        risk_penalty += 8.0
    if sf(risk.get("hot20_pct")) >= HOT_20D_PCT:
        risk_penalty += 4.0
    if risk.get("amount20", 0) and sf(risk.get("amount20")) < MIN_AMOUNT20:
        risk_penalty += 4.0

    total = line_score + br_score + accept_score + pricing_score + 20.0 - risk_penalty
    total = max(0.0, min(100.0, total))

    formal_gate = (
        total >= MIN_FORMAL_SCORE
        and not risk.get("hard_reject")
        and bool(trade.get("passed"))
        and not bool(accept.get("failed"))
    )
    if formal_gate and total >= 88:
        grade = "S"
    elif formal_gate:
        grade = "A"
    elif total >= 70:
        grade = "B"
    elif total >= MIN_OBSERVE_SCORE:
        grade = "C"
    else:
        grade = "D"

    status = "正式/可交易" if formal_gate else "观察/待确认"
    reasons: List[str] = []
    risks: List[str] = []
    reasons.append(f"{line_type}共振{line_info.get('primary_resonance_count')}次，带量{line_info.get('volume_resonance_count')}次")
    reasons.append(f"{br.get('date')}日线有效突破，收盘位置{br.get('close_pos')}，量比{br.get('volume_ratio')}")
    reasons.append(ss(accept.get("status")))
    if trade.get("passed"):
        reasons.append(f"交易定价过闸，RR {trade.get('rr')}")
    else:
        risks.append("交易定价未过闸")
    if accept.get("failed"):
        risks.append(ss(accept.get("status")))
    risks.extend(risk.get("flags") or [])
    if risk.get("hard_reject"):
        risks.append("硬风险剔除")

    confirm = f"守住交易防守位{trade.get('defense_price')}，收盘继续保持在主评测线{rd(line, 3)}上方"
    pressure_price = trade.get("pressure_price")
    if pressure_price:
        confirm += f"，向第一有效压力{pressure_price}推进"
    else:
        confirm += "，价格发现状态下用MA10/前低移动跟踪"
    giveup = f"收盘跌破交易防守位{trade.get('defense_price')}，或重新跌回主评测线{rd(line, 3)}下方"

    return {
        "股票代码": code,
        "股票中文名称": name,
        "主评测线类型": line_type,
        "主评测线价位": rd(line, 3),
        "主评测线突破日期": br.get("date", ""),
        "当前收盘": trade.get("last_close"),
        "距主评测线%": trade.get("distance_line_pct"),
        "交易防守位": trade.get("defense_price"),
        "防守距离%": trade.get("risk_pct"),
        "第一压力": trade.get("pressure_price"),
        "压力空间%": trade.get("space_pct"),
        "估算赔率": trade.get("rr"),
        "价格发现": trade.get("price_discovery"),
        "深度等级": grade,
        "深度得分": rd(total, 2),
        "状态": status,
        "正式入选": bool(formal_gate),
        "操作建议": "可按破界后接受处理" if formal_gate else "观察，等待更强接受或更好RR",
        "加分原因": "；".join([x for x in reasons if x]),
        "扣分原因": "；".join([x for x in risks if x]) or "无明显硬扣分",
        "确认条件": confirm,
        "放弃条件": giveup,
        "line_level_score": line_score,
        "breakout_score": br_score,
        "acceptance_score": accept_score,
        "pricing_score": pricing_score,
        "risk_penalty": rd(risk_penalty, 3),
        "历史线": None,
        "触发线": None,
        "历史线详情": {},
        "触发线详情": {},
        "突破详情": br,
        "接受详情": accept,
        "交易详情": trade,
        "风险详情": risk,
    }


def build_dual_line_hit_candidate(code: str, name: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    d = normalize_hist(df)
    if d.empty or len(d) < MIN_ROWS:
        return None
    hist_line = choose_historical_core_resonance_line(d)
    trigger_line = choose_five_hundred_day_resonance_trigger_line(d)

    options: List[Tuple[str, Dict[str, Any]]] = []
    for info in hist_line.get("top_candidates", [])[:LINE_TOP_CANDIDATE_LIMIT]:
        options.append(("历史核心线", info))
    for info in trigger_line.get("top_candidates", [])[:LINE_TOP_CANDIDATE_LIMIT]:
        options.append(("五百日触发线", info))
    options = dedupe_line_options(options)

    assessed: List[Dict[str, Any]] = []
    for line_type, info in options:
        row = assess_line_option(code, name, d, line_type, info)
        if row:
            row["历史线"] = hist_line.get("line")
            row["触发线"] = trigger_line.get("line")
            row["历史线详情"] = {k: v for k, v in hist_line.items() if k != "top_candidates"}
            row["触发线详情"] = {k: v for k, v in trigger_line.items() if k != "top_candidates"}
            assessed.append(row)

    if not assessed:
        return None
    assessed.sort(key=lambda r: (bool(r.get("正式入选")), sf(r.get("深度得分")), -sf(r.get("距主评测线%"))), reverse=True)
    return assessed[0]


def _screen_one(args: Tuple[str, str, pd.DataFrame]) -> Optional[Dict[str, Any]]:
    code, name, df = args
    try:
        return build_dual_line_hit_candidate(code, name, df)
    except Exception as exc:
        return {"股票代码": code, "股票中文名称": name, "error": ss(exc)[:180], "深度得分": 0, "状态": "错误"}


def screen_all(hist: Dict[str, pd.DataFrame], names: Dict[str, str]) -> List[Dict[str, Any]]:
    items = []
    for code, df in hist.items():
        name = names.get(code, "")
        if not name and "name" in df.columns:
            vals = [ss(x) for x in df["name"].tolist() if valid_stock_display_name(code, x)]
            name = vals[-1] if vals else "名称待补"
        if not name:
            name = "名称待补"
        items.append((code, name, df))

    rows: List[Dict[str, Any]] = []
    log(f"开始破界扫描：stocks={len(items)} parallel={PARALLEL} workers={WORKERS if PARALLEL else 1}")
    start = time.time()
    if PARALLEL and len(items) > 50 and WORKERS > 1:
        with ProcessPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(_screen_one, item) for item in items]
            for i, fut in enumerate(as_completed(futs), 1):
                row = fut.result()
                if row and row.get("状态") != "错误" and sf(row.get("深度得分")) >= MIN_OBSERVE_SCORE:
                    rows.append(row)
                if i == 1 or i % SCREEN_PROGRESS_EVERY == 0 or i == len(futs):
                    speed = i / max(time.time() - start, 1e-9)
                    log(f"扫描进度 {i}/{len(futs)}｜命中{len(rows)}｜速度{speed:.2f}只/秒")
    else:
        for i, item in enumerate(items, 1):
            row = _screen_one(item)
            if row and row.get("状态") != "错误" and sf(row.get("深度得分")) >= MIN_OBSERVE_SCORE:
                rows.append(row)
            if i == 1 or i % SCREEN_PROGRESS_EVERY == 0 or i == len(items):
                speed = i / max(time.time() - start, 1e-9)
                log(f"扫描进度 {i}/{len(items)}｜命中{len(rows)}｜速度{speed:.2f}只/秒｜当前{item[0]}")

    rows.sort(key=lambda r: (bool(r.get("正式入选")), sf(r.get("深度得分")), sf(r.get("估算赔率"))), reverse=True)
    return rows


# -------------------- 输出 --------------------
def select_report_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    formal = [r for r in rows if r.get("正式入选")]
    observe = [r for r in rows if not r.get("正式入选")]
    formal.sort(key=lambda r: sf(r.get("深度得分")), reverse=True)
    observe.sort(key=lambda r: sf(r.get("深度得分")), reverse=True)
    selected = formal[:TOP_LIMIT]
    if len(selected) < TOP_LIMIT:
        selected.extend(observe[:TOP_LIMIT - len(selected)])
    return selected


def short_text(s: Any, n: int) -> str:
    x = ss(s)
    return x if len(x) <= n else x[: max(0, n - 1)] + "…"


def build_report(rows: List[Dict[str, Any]], stat: Dict[str, Any]) -> str:
    formal_count = sum(1 for r in rows if r.get("正式入选"))
    observe_count = len(rows) - formal_count
    selected = select_report_rows(rows)
    title = "破界 Top3" if formal_count else "破界 Top3观察池｜无正式"
    lines = [
        f"{title}｜{TARGET_DASH or TARGET}",
        f"缓存{stat.get('cache_files', 0)}｜命中{stat.get('cache_hit', 0)}｜正式{formal_count}｜观察{observe_count}｜事件{len(rows)}",
        "逻辑：历史核心共振线 + 五百日触发线，近20日出现高质量日线破界后，再看接受、交易防守位、上方空间和RR。",
        "",
    ]
    if not selected:
        lines.append("今日无符合破界条件的候选。")
        return "\n".join(lines)

    lines.append("【候选Top】")
    for idx, r in enumerate(selected, 1):
        lines.extend([
            f"{idx}. {r.get('股票代码')} {r.get('股票中文名称')}｜{r.get('深度等级')}｜{r.get('深度得分')}｜{r.get('状态')}｜{short_text(r.get('扣分原因'), 46)}",
            f"线:{r.get('主评测线价位')}｜类型:{r.get('主评测线类型')}｜突破:{r.get('主评测线突破日期')}｜收:{r.get('当前收盘')}｜距线:{r.get('距主评测线%')}%",
            f"防守:{r.get('交易防守位')}｜压力:{r.get('第一压力')}｜空间:{r.get('压力空间%')}%｜RR:{r.get('估算赔率')}",
            f"确认:{short_text(r.get('确认条件'), 74)}",
            f"放弃:{short_text(r.get('放弃条件'), 74)}",
        ])
    lines.append("完整明细见 GitHub Actions artifact：pojie-reports。")
    report = "\n".join(lines)
    if len(report) > 3900:
        report = report[:3850].rstrip() + "\n……\n报告过长，Telegram已压缩；完整明细见CSV/JSON。"
    return report


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {ss(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(x) for x in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if pd.isna(obj) if not isinstance(obj, (str, dict, list, tuple)) else False:
        return None
    return obj


def write_outputs(rows: List[Dict[str, Any]], md: str, stat: Dict[str, Any], self_check: Dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(md, encoding="utf-8")
    flat_rows = []
    for r in rows:
        flat = {k: v for k, v in r.items() if not isinstance(v, (dict, list))}
        flat_rows.append(flat)
    pd.DataFrame(flat_rows).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    payload = {
        "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "target": TARGET,
        "target_dash": TARGET_DASH,
        "boot": BOOT,
        "config": {
            "breakout_lookback_days": BREAKOUT_LOOKBACK_DAYS,
            "five_hundred_day_lookback": FIVE_HUNDRED_DAY_LOOKBACK,
            "core_line_tol": CORE_LINE_TOL,
            "core_line_band_tol": CORE_LINE_BAND_TOL,
            "min_core_resonance": MIN_CORE_RESONANCE,
            "min_formal_score": MIN_FORMAL_SCORE,
            "min_formal_rr": MIN_FORMAL_RR,
        },
        "stat": stat,
        "self_check": self_check,
        "rows": rows,
    }
    OUTPUT_JSON.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    SELF_CHECK_JSON.write_text(json.dumps(json_safe(self_check), ensure_ascii=False, indent=2), encoding="utf-8")


def send_report(md: str) -> None:
    if not ENABLE_TELEGRAM or not BOT or not CHAT or requests is None:
        log(f"Telegram跳过 enable={ENABLE_TELEGRAM} token={bool(BOT)} chat={bool(CHAT)} requests={requests is not None}")
        print(md[:2400], flush=True)
        return
    url = f"https://api.telegram.org/bot{BOT}/sendMessage"
    chunks = [md[i:i + 3600] for i in range(0, len(md), 3600)] or [md]
    for idx, part in enumerate(chunks, 1):
        try:
            resp = requests.post(url, json={"chat_id": CHAT, "text": part, "disable_web_page_preview": True}, timeout=30)
            log(f"Telegram chunk {idx} status={getattr(resp, 'status_code', 'NA')} body={getattr(resp, 'text', '')[:120]}")
        except Exception as exc:
            log(f"Telegram发送失败 chunk={idx} err={exc}")
        time.sleep(0.35)


def run_self_check() -> Dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    existing_dirs = [str(x) for x in CACHE_DIRS if x.exists()]
    files = iter_cache_files()
    status = "PASS" if files else "WARN"
    result = {
        "status": status,
        "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "target": TARGET,
        "target_dash": TARGET_DASH,
        "boot": BOOT,
        "cache_dirs": [str(x) for x in CACHE_DIRS],
        "existing_cache_dirs": existing_dirs,
        "cache_files": len(files),
        "telegram": {"enabled": ENABLE_TELEGRAM, "token_present": bool(BOT), "chat_present": bool(CHAT), "requests": requests is not None},
    }
    SELF_CHECK_JSON.write_text(json.dumps(json_safe(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    print(BOOT, flush=True)
    print(f"file={Path(__file__).resolve()}", flush=True)
    print(f"target={TARGET} target_dash={TARGET_DASH}", flush=True)
    print("cache_dirs=" + " | ".join(str(x) for x in CACHE_DIRS), flush=True)
    self_check = run_self_check()
    hist, names, stat = load_cache()
    rows = screen_all(hist, names) if hist else []
    md = build_report(rows, stat)
    write_outputs(rows, md, stat, self_check)
    send_report(md)
    log(f"done report={OUTPUT_MD} csv={OUTPUT_CSV} json={OUTPUT_JSON}")


if __name__ == "__main__":
    main()
