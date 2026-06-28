# -*- coding: utf-8 -*-
from __future__ import annotations

"""
破界.py｜从零重写版｜年线/季线/月线核心线破界海选

核心原则：
1）只找一条真正有交易价值的核心线，不再使用500日触发线；
2）先找年线：年线有效共振点 >= 4 且不切实体，直接采用年线核心线；
3）年线没有，再找季线：季线有效共振点 >= 8 且不切实体；
4）季线没有，再找月线：月线有效共振点 >= 10 且不切实体；
5）月线仍没有，则该股没有核心线；
6）同一周期内，共振中若存在大阳线实顶，优先以该大阳线实顶作为核心线；
7）核心线只负责给出价格、周期、来源和共振证据；近20日破界只是后续交易筛选。

输出：
- 破界报告/核心线全市场明细.csv
- 破界报告/核心线破界候选.csv
- 破界报告/核心线破界报告.md
- 破界报告/核心线数据.json
"""

import json
import math
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

BOOT = "POJIE_CORE_LINE_REWRITE_YQM_ZERO_CUT_V1_20260627"
START_TS = time.time()
ROOT = Path(__file__).resolve().parent

REPORT_DIR = ROOT / "破界报告"
OUTPUT_MD = REPORT_DIR / "核心线破界报告.md"
OUTPUT_ALL_CSV = REPORT_DIR / "核心线全市场明细.csv"
OUTPUT_HIT_CSV = REPORT_DIR / "核心线破界候选.csv"
OUTPUT_JSON = REPORT_DIR / "核心线数据.json"
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

MAX_STOCKS = int(os.getenv("POJIE_MAX_STOCKS", os.getenv("MAX_STOCKS", "0")))
MIN_DAILY_ROWS = int(os.getenv("POJIE_MIN_DAILY_ROWS", "80"))
BREAKOUT_LOOKBACK_DAYS = int(os.getenv("POJIE_BREAKOUT_LOOKBACK_DAYS", "20"))

CORE_LINE_TOL = float(os.getenv("POJIE_CORE_LINE_TOL", "0.010"))
BODY_TOP_EDGE_TOL = float(os.getenv("POJIE_BODY_TOP_EDGE_TOL", "0.005"))

YEAR_MIN_RESONANCE = int(os.getenv("POJIE_YEAR_MIN_RESONANCE", "4"))
QUARTER_MIN_RESONANCE = int(os.getenv("POJIE_QUARTER_MIN_RESONANCE", "8"))
MONTH_MIN_RESONANCE = int(os.getenv("POJIE_MONTH_MIN_RESONANCE", "10"))

YEAR_BIG_BULL_BODY_PCT = float(os.getenv("POJIE_YEAR_BIG_BULL_BODY_PCT", "0.20"))
QUARTER_BIG_BULL_BODY_PCT = float(os.getenv("POJIE_QUARTER_BIG_BULL_BODY_PCT", "0.16"))
MONTH_BIG_BULL_BODY_PCT = float(os.getenv("POJIE_MONTH_BIG_BULL_BODY_PCT", "0.12"))
BIG_BULL_BODY_RATIO_MIN = float(os.getenv("POJIE_BIG_BULL_BODY_RATIO_MIN", "0.42"))

BREAK_CLOSE_ABOVE_PCT = float(os.getenv("POJIE_BREAK_CLOSE_ABOVE_PCT", "0.003"))
BREAK_PREV_BELOW_PCT = float(os.getenv("POJIE_BREAK_PREV_BELOW_PCT", "0.005"))
BREAK_MIN_PCT_CHG = float(os.getenv("POJIE_BREAK_MIN_PCT_CHG", "1.0"))
BREAK_MIN_BODY_PCT = float(os.getenv("POJIE_BREAK_MIN_BODY_PCT", "0.005"))
BREAK_BODY_RATIO_MIN = float(os.getenv("POJIE_BREAK_BODY_RATIO_MIN", "0.28"))
BREAK_CLOSE_POS_MIN = float(os.getenv("POJIE_BREAK_CLOSE_POS_MIN", "0.66"))
BREAK_UPPER_SHADOW_MAX = float(os.getenv("POJIE_BREAK_UPPER_SHADOW_MAX", "0.36"))
BREAK_ENTITY_ABOVE_LINE_MIN = float(os.getenv("POJIE_BREAK_ENTITY_ABOVE_LINE_MIN", "0.32"))
BREAK_MIN_VOLUME_RATIO = float(os.getenv("POJIE_BREAK_MIN_VOLUME_RATIO", "1.15"))

DEFENSE_BUFFER_PCT = float(os.getenv("POJIE_DEFENSE_BUFFER_PCT", "0.015"))
MAX_DISTANCE_LINE_PCT = float(os.getenv("POJIE_MAX_DISTANCE_LINE_PCT", "18.0"))
MAX_RISK_PCT = float(os.getenv("POJIE_MAX_RISK_PCT", "10.5"))
HOT_20D_PCT = float(os.getenv("POJIE_HOT_20D_PCT", "25.0"))
MIN_AMOUNT20 = float(os.getenv("POJIE_MIN_AMOUNT20", "50000000"))
TOP_LIMIT = int(os.getenv("POJIE_TOP_LIMIT", "3"))

PARALLEL = (os.getenv("POJIE_PARALLEL") or "1").strip() not in {"0", "false", "False", "no", "NO"}
WORKERS = max(1, int(os.getenv("POJIE_WORKERS", str(min(4, max(1, os.cpu_count() or 1))))))
CACHE_PROGRESS_EVERY = int(os.getenv("POJIE_CACHE_PROGRESS_EVERY", "1000"))
SCREEN_PROGRESS_EVERY = int(os.getenv("POJIE_SCREEN_PROGRESS_EVERY", "200"))
HARD_RISK_NAME_KEYWORDS = tuple(x for x in os.getenv("POJIE_HARD_RISK_NAME_KEYWORDS", "ST,*ST,退,退市").split(",") if x)

PERIOD_SPECS = [
    {"period": "Y", "period_name": "年线", "min_resonance": YEAR_MIN_RESONANCE, "big_bull_body_pct": YEAR_BIG_BULL_BODY_PCT},
    {"period": "Q", "period_name": "季线", "min_resonance": QUARTER_MIN_RESONANCE, "big_bull_body_pct": QUARTER_BIG_BULL_BODY_PCT},
    {"period": "M", "period_name": "月线", "min_resonance": MONTH_MIN_RESONANCE, "big_bull_body_pct": MONTH_BIG_BULL_BODY_PCT},
]


def log(msg: str) -> None:
    print(f"[破界][{time.time() - START_TS:7.1f}s] {msg}", flush=True)


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
    m = re.search(r"(\d{6})", ss(x))
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
        rows = obj.get("rows") or obj.get("data") or obj.get("klines") or obj.get("items") or [] if isinstance(obj, dict) else obj
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
        files = sorted(list(directory.glob("*.csv")) + list(directory.glob("*.json")), key=lambda x: x.stat().st_mtime, reverse=True)[:80]
        for p in files:
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
                    rows: List[Dict[str, Any]] = []
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if isinstance(v, str):
                                rows.append({"代码": k, "名称": v})
                            elif isinstance(v, dict):
                                vv = dict(v)
                                vv.setdefault("代码", k)
                                rows.append(vv)
                    elif isinstance(obj, list):
                        rows = obj
                    scan_name_frame(name_map, pd.DataFrame(rows))
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
        elif len(df) < MIN_DAILY_ROWS:
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


def aggregate_period_bars(df: pd.DataFrame, period: str) -> pd.DataFrame:
    d = normalize_hist(df)
    if d.empty:
        return pd.DataFrame()
    dt = pd.to_datetime(d["date"], errors="coerce")
    d = d[dt.notna()].copy()
    if d.empty:
        return pd.DataFrame()

    d["_period"] = pd.to_datetime(d["date"], errors="coerce").dt.to_period(period).astype(str)
    bars: List[Dict[str, Any]] = []
    for key, g in d.groupby("_period", sort=True):
        g = g.sort_values("date").reset_index(drop=True)
        bars.append({
            "period": ss(key),
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
    k["body"] = (k["close"] - k["open"]).abs()
    k["body_pct"] = k["body"] / k["open"].replace(0, np.nan)
    k["body_ratio"] = (k["body"] / rng).fillna(0.0)
    k["upper_shadow_ratio"] = ((k["high"] - k["body_top"]) / rng).fillna(0.0)
    k["is_bull"] = k["close"] > k["open"]
    return k


def completed_period_bars(df: pd.DataFrame, period: str) -> pd.DataFrame:
    raw = aggregate_period_bars(df, period)
    if raw.empty:
        return raw
    # 最后一根年/季/月K通常是当前未完成周期，不能参与历史核心线锚定。
    if len(raw) >= 2:
        return raw.iloc[:-1].reset_index(drop=True)
    return pd.DataFrame()


def is_big_bull_bar(row: pd.Series, big_bull_body_pct: float) -> bool:
    return bool(
        sf(row.get("close")) > sf(row.get("open"))
        and sf(row.get("body_pct")) >= big_bull_body_pct
        and sf(row.get("body_ratio")) >= BIG_BULL_BODY_RATIO_MIN
    )


def build_line_candidates(k: pd.DataFrame, period_name: str, big_bull_body_pct: float) -> Dict[float, Dict[str, Any]]:
    candidates: Dict[float, Dict[str, Any]] = {}

    def add(price: Any, anchor_kind: str, idx: int, r: pd.Series, priority: int) -> None:
        line = rd(price, 3)
        if line <= 0:
            return
        item = candidates.setdefault(line, {
            "line": line,
            "period_name": period_name,
            "anchor_kinds": set(),
            "anchors": [],
            "has_big_bull_body_top_anchor": False,
            "best_anchor_priority": 0,
        })
        item["anchor_kinds"].add(anchor_kind)
        item["best_anchor_priority"] = max(int(item.get("best_anchor_priority", 0)), priority)
        big_bull = is_big_bull_bar(r, big_bull_body_pct)
        if anchor_kind == "大阳线实顶" and big_bull:
            item["has_big_bull_body_top_anchor"] = True
        item["anchors"].append({
            "idx": int(idx),
            "period": ss(r.get("period")),
            "start": ss(r.get("start")),
            "end": ss(r.get("end")),
            "anchor_kind": anchor_kind,
            "open": rd(r.get("open")),
            "high": rd(r.get("high")),
            "low": rd(r.get("low")),
            "close": rd(r.get("close")),
            "body_top": rd(r.get("body_top")),
            "body_bottom": rd(r.get("body_bottom")),
            "volume": rd(r.get("volume"), 0),
            "is_big_bull": bool(big_bull),
        })

    for idx, r in k.iterrows():
        if is_big_bull_bar(r, big_bull_body_pct):
            add(r.get("body_top"), "大阳线实顶", idx, r, 5)
        else:
            add(r.get("body_top"), "实体顶", idx, r, 4)
        add(r.get("high"), "高点/上影线", idx, r, 3)
    return candidates


def resonance_contribution_by_prev_volume(volume: float, prev_volume: float) -> Tuple[float, str]:
    ratio = safe_div(volume, prev_volume, 0.0) if prev_volume > 0 else 0.0
    if ratio >= 3.0:
        return 2.0, "极端放量共振"
    if ratio >= 2.0:
        return 1.8, "标准倍量共振"
    if ratio >= 1.60:
        return 1.6, "强放量共振"
    if ratio >= 1.30:
        return 1.45, "明显放量共振"
    if ratio >= 1.15:
        return 1.30, "轻度放量共振"
    return 1.0, "普通共振"


def score_line(k: pd.DataFrame, line: float, candidate: Dict[str, Any], big_bull_body_pct: float) -> Dict[str, Any]:
    resonance_count = 0
    weighted_score = 0.0
    body_top_touch_count = 0
    high_touch_count = 0
    upper_shadow_hit_count = 0
    close_touch_count = 0
    big_bull_body_top_resonance_count = 0
    volume_resonance_count = 0
    entity_cut_count = 0
    volume_entity_cut_count = 0
    entity_accept_count = 0
    touch_rows: List[Dict[str, Any]] = []
    cut_rows: List[Dict[str, Any]] = []

    prev_volumes = k["volume"].shift(1).fillna(0.0).tolist() if "volume" in k.columns else [0.0] * len(k)

    for idx, r in k.iterrows():
        high = sf(r.get("high"))
        body_top = sf(r.get("body_top"))
        body_bottom = sf(r.get("body_bottom"))
        close = sf(r.get("close"))
        volume = sf(r.get("volume"))
        prev_volume = sf(prev_volumes[idx]) if idx < len(prev_volumes) else 0.0
        if line <= 0 or high <= 0 or body_top <= 0 or body_bottom <= 0:
            continue

        edge_touch = abs(body_top - line) / line <= BODY_TOP_EDGE_TOL
        entity_cut = body_bottom < line < body_top and not edge_touch
        entity_accept = body_bottom > line

        if entity_cut:
            entity_cut_count += 1
            contribution, volume_tag = resonance_contribution_by_prev_volume(volume, prev_volume)
            if contribution > 1.0:
                volume_entity_cut_count += 1
            cut_rows.append({
                "period": ss(r.get("period")),
                "end": ss(r.get("end")),
                "open": rd(r.get("open")),
                "close": rd(r.get("close")),
                "line": rd(line),
                "volume_tag": volume_tag,
            })
            continue

        if entity_accept:
            entity_accept_count += 1

        body_top_touch = abs(body_top - line) / line <= CORE_LINE_TOL
        high_touch = abs(high - line) / line <= CORE_LINE_TOL
        upper_shadow_hit = body_top <= line <= high
        close_touch = abs(close - line) / line <= CORE_LINE_TOL
        touched = bool(body_top_touch or high_touch or upper_shadow_hit or close_touch)
        if not touched:
            continue

        contribution, volume_tag = resonance_contribution_by_prev_volume(volume, prev_volume)
        resonance_count += 1
        weighted_score += contribution
        if contribution > 1.0:
            volume_resonance_count += 1
        if body_top_touch:
            body_top_touch_count += 1
        if high_touch:
            high_touch_count += 1
        if upper_shadow_hit and not body_top_touch:
            upper_shadow_hit_count += 1
        if close_touch:
            close_touch_count += 1
        big_bull_top = body_top_touch and is_big_bull_bar(r, big_bull_body_pct)
        if big_bull_top:
            big_bull_body_top_resonance_count += 1

        touch_rows.append({
            "period": ss(r.get("period")),
            "start": ss(r.get("start")),
            "end": ss(r.get("end")),
            "kind": "大阳线实顶" if big_bull_top else ("实体顶" if body_top_touch else ("上影穿线" if upper_shadow_hit else ("高点贴线" if high_touch else "收盘贴线"))),
            "open": rd(r.get("open")),
            "high": rd(r.get("high")),
            "low": rd(r.get("low")),
            "close": rd(r.get("close")),
            "body_top": rd(body_top),
            "volume": rd(volume, 0),
            "volume_ratio_prev": rd(safe_div(volume, prev_volume, 0.0), 3),
            "volume_tag": volume_tag,
            "contribution": contribution,
        })

    # 切实体只做诊断；正式选择强制要求 entity_cut_count == 0。
    nonlinear_cut_penalty = 0.0
    if entity_cut_count > 0:
        nonlinear_cut_penalty = entity_cut_count * entity_cut_count * 1.20 + volume_entity_cut_count * 1.50
    net_score = weighted_score - nonlinear_cut_penalty

    anchors = candidate.get("anchors", []) or []
    best_anchor = sorted(anchors, key=lambda x: (bool(x.get("is_big_bull")), sf(x.get("body_top")), ss(x.get("end"))), reverse=True)[0] if anchors else {}
    return {
        "line": rd(line),
        "period_name": candidate.get("period_name", ""),
        "source": "+".join(sorted(candidate.get("anchor_kinds", set()))),
        "has_big_bull_body_top_anchor": bool(candidate.get("has_big_bull_body_top_anchor")),
        "best_anchor_priority": int(candidate.get("best_anchor_priority", 0)),
        "anchor_period": best_anchor.get("period", ""),
        "anchor_start": best_anchor.get("start", ""),
        "anchor_end": best_anchor.get("end", ""),
        "anchor_kind": best_anchor.get("anchor_kind", ""),
        "anchor_open": best_anchor.get("open"),
        "anchor_high": best_anchor.get("high"),
        "anchor_low": best_anchor.get("low"),
        "anchor_close": best_anchor.get("close"),
        "resonance_count": int(resonance_count),
        "weighted_resonance_score": rd(weighted_score, 3),
        "net_score": rd(net_score, 3),
        "body_top_touch_count": int(body_top_touch_count),
        "high_touch_count": int(high_touch_count),
        "upper_shadow_hit_count": int(upper_shadow_hit_count),
        "close_touch_count": int(close_touch_count),
        "big_bull_body_top_resonance_count": int(big_bull_body_top_resonance_count),
        "volume_resonance_count": int(volume_resonance_count),
        "entity_cut_count": int(entity_cut_count),
        "volume_entity_cut_count": int(volume_entity_cut_count),
        "entity_accept_count": int(entity_accept_count),
        "touch_rows": touch_rows,
        "cut_rows": cut_rows,
    }


def choose_period_core_line(df: pd.DataFrame, spec: Dict[str, Any]) -> Dict[str, Any]:
    period = ss(spec["period"])
    period_name = ss(spec["period_name"])
    min_resonance = int(spec["min_resonance"])
    big_bull_body_pct = sf(spec["big_bull_body_pct"])
    k = completed_period_bars(df, period)
    if k.empty:
        return {"found": False, "period_name": period_name, "reason": f"{period_name}已完成K线不足", "candidates": []}

    candidates = build_line_candidates(k, period_name, big_bull_body_pct)
    scored = [score_line(k, line, candidate, big_bull_body_pct) for line, candidate in candidates.items()]
    valid = [x for x in scored if int(x.get("resonance_count", 0)) >= min_resonance and int(x.get("entity_cut_count", 0)) == 0]
    if not valid:
        top_failed = sorted(scored, key=lambda x: (int(x.get("entity_cut_count", 0)) == 0, int(x.get("resonance_count", 0)), sf(x.get("net_score"))), reverse=True)[:5]
        return {
            "found": False,
            "period_name": period_name,
            "reason": f"{period_name}未出现 共振>={min_resonance} 且零切实体 的核心线",
            "period_bar_count": int(len(k)),
            "candidates": top_failed,
        }

    big_bull_valid = [x for x in valid if bool(x.get("has_big_bull_body_top_anchor")) and int(x.get("big_bull_body_top_resonance_count", 0)) > 0]
    pool = big_bull_valid if big_bull_valid else valid
    chosen = sorted(
        pool,
        key=lambda x: (
            int(x.get("resonance_count", 0)),
            sf(x.get("weighted_resonance_score")),
            int(x.get("volume_resonance_count", 0)),
            int(x.get("body_top_touch_count", 0)),
            int(x.get("best_anchor_priority", 0)),
            sf(x.get("line")),
        ),
        reverse=True,
    )[0]
    chosen = dict(chosen)
    chosen.update({
        "found": True,
        "period_name": period_name,
        "min_resonance_required": min_resonance,
        "period_bar_count": int(len(k)),
        "selection_reason": "大阳线实顶优先" if big_bull_valid else "零切实体共振最多",
        "top_candidates": sorted(valid, key=lambda x: (int(x.get("resonance_count", 0)), sf(x.get("weighted_resonance_score"))), reverse=True)[:10],
    })
    return chosen


def choose_core_line(df: pd.DataFrame) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    for spec in PERIOD_SPECS:
        result = choose_period_core_line(df, spec)
        attempts.append({k: v for k, v in result.items() if k not in {"top_candidates", "touch_rows", "cut_rows"}})
        if result.get("found"):
            result["attempts"] = attempts
            return result
    return {
        "found": False,
        "line": None,
        "period_name": "无核心线",
        "reason": "年线未达4共振、季线未达8共振、月线未达10共振，或存在切实体",
        "attempts": attempts,
    }


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
        if close <= 0 or high <= 0 or prev_close <= 0:
            continue
        prev_from_below = prev_close <= line * (1.0 - BREAK_PREV_BELOW_PCT) or (prev_close <= line * 1.002 and sf(prev.get("low")) < line)
        close_above = close >= line * (1.0 + BREAK_CLOSE_ABOVE_PCT)
        high_above = high >= line * (1.0 + BREAK_CLOSE_ABOVE_PCT)
        if not (prev_from_below and close_above and high_above):
            continue

        f = kline_features(r, prev_close, line)
        vol_window = d.iloc[max(0, idx - 20):idx]
        vol_med = sf(vol_window["volume"].median()) if not vol_window.empty else 0.0
        vol_ratio = safe_div(sf(r.get("volume")), vol_med, 0.0) if vol_med > 0 else 0.0
        volume_ok = vol_ratio >= BREAK_MIN_VOLUME_RATIO or f["is_limit_like"]
        hard_ok = (
            close > open_
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
        score = 50.0
        score += min(12.0, max(0.0, f["close_pos"] - BREAK_CLOSE_POS_MIN) * 35.0)
        score += min(12.0, f["entity_above_line_ratio"] * 12.0)
        score += min(10.0, max(0.0, f["pct_chg"] - BREAK_MIN_PCT_CHG) * 0.9)
        score += min(8.0, max(0.0, vol_ratio - BREAK_MIN_VOLUME_RATIO) * 4.0)
        score -= max(0.0, f["upper_shadow_ratio"] - 0.20) * 10.0
        obj = {
            "is_breakout": True,
            "idx": int(idx),
            "date": ss(r.get("date")),
            "line": rd(line),
            "close": rd(close),
            "pct_chg": rd(f["pct_chg"], 3),
            "body_ratio": rd(f["body_ratio"], 3),
            "close_pos": rd(f["close_pos"], 3),
            "upper_shadow_ratio": rd(f["upper_shadow_ratio"], 3),
            "entity_above_line_ratio": rd(f["entity_above_line_ratio"], 3),
            "volume_ratio": rd(vol_ratio, 3),
            "score": rd(score, 3),
            "reason": "近20日高质量日线破界",
        }
        if best is None or (sf(obj.get("score")), int(obj.get("idx"))) > (sf(best.get("score")), int(best.get("idx"))):
            best = obj
    return best if best is not None else {"is_breakout": False, "reason": "近20日无高质量日线破界"}


def evaluate_acceptance(df: pd.DataFrame, bidx: int, line: float) -> Dict[str, Any]:
    d = normalize_hist(df)
    if d.empty or bidx < 0 or bidx >= len(d) or line <= 0:
        return {"status": "无接受数据", "score": 0.0, "failed": True}
    post = d.iloc[bidx:].copy().reset_index(drop=True)
    last_close = sf(d.iloc[-1]["close"])
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
    if close_below_defense > 0:
        score -= 8.0
    if not last_above:
        score -= 6.0
    if close_below_defense > 0:
        status = "跌破交易防守，接受失败"
        failed = True
    elif not last_above:
        status = "突破后未站稳"
        failed = True
    elif pullback_touched and close_below_line <= 1:
        status = "突破后回踩/贴线接受"
        failed = False
    else:
        status = "突破后悬空接受"
        failed = False
    return {
        "status": status,
        "score": rd(max(0.0, min(20.0, score)), 3),
        "failed": failed,
        "above_days": above_days,
        "close_below_line_count": close_below_line,
        "close_below_defense_count": close_below_defense,
        "pullback_touched": bool(pullback_touched),
        "min_post_low": rd(min_low),
    }


def amount20(df: pd.DataFrame) -> float:
    d = normalize_hist(df)
    if d.empty:
        return 0.0
    w = d.tail(20)
    if "amount" in w.columns and float(w["amount"].sum()) > 0:
        return sf(w["amount"].mean())
    return 0.0


def build_core_line_row(code: str, name: str, df: pd.DataFrame) -> Dict[str, Any]:
    d = normalize_hist(df)
    core = choose_core_line(d)
    if not core.get("found"):
        return {
            "股票代码": code,
            "股票中文名称": name,
            "是否有核心线": False,
            "核心线价格": None,
            "核心线周期": "无",
            "核心线来源": core.get("reason", "未识别"),
            "核心线共振次数": 0,
            "核心线切实体次数": None,
            "是否近20日破界": False,
            "破界日期": "",
            "破界得分": 0.0,
            "当前收盘": rd(d.iloc[-1]["close"]) if not d.empty else None,
            "距核心线%": None,
            "核心线详情": core,
        }

    line = sf(core.get("line"))
    br = daily_breakout_quality(d, line)
    last_close = sf(d.iloc[-1]["close"]) if not d.empty else 0.0
    defense = line * (1.0 - DEFENSE_BUFFER_PCT) if line > 0 else 0.0
    risk_pct = pct(last_close, defense) if defense > 0 else 999.0
    distance_pct = pct(last_close, line) if line > 0 else 999.0
    accept = evaluate_acceptance(d, int(br.get("idx", -1)), line) if br.get("is_breakout") else {"status": "未破界", "score": 0.0, "failed": True}
    amt20 = amount20(d)
    hot20 = pct(last_close, sf(d.iloc[-21]["close"])) if len(d) >= 21 and sf(d.iloc[-21]["close"]) > 0 else 0.0
    risk_flags: List[str] = []
    if any(k and k in name for k in HARD_RISK_NAME_KEYWORDS):
        risk_flags.append("名称命中ST/退市类硬风险")
    if amt20 > 0 and amt20 < MIN_AMOUNT20:
        risk_flags.append(f"20日成交额偏低{rd(amt20 / 100000000, 2)}亿")
    elif amt20 <= 0:
        risk_flags.append("成交额字段缺失")
    if hot20 >= HOT_20D_PCT:
        risk_flags.append(f"近20日涨幅{rd(hot20, 2)}%，短线过热")
    if risk_pct > MAX_RISK_PCT:
        risk_flags.append(f"防守距离{rd(risk_pct, 2)}%，偏大")
    if distance_pct > MAX_DISTANCE_LINE_PCT:
        risk_flags.append(f"距核心线{rd(distance_pct, 2)}%，偏远")

    breakout_score = sf(br.get("score")) if br.get("is_breakout") else 0.0
    line_score = min(50.0, sf(core.get("resonance_count")) * 5.0 + sf(core.get("weighted_resonance_score")) * 1.5 + (8.0 if core.get("has_big_bull_body_top_anchor") else 0.0))
    accept_score = sf(accept.get("score"))
    risk_penalty = 0.0
    if risk_flags:
        risk_penalty += min(18.0, len(risk_flags) * 4.0)
    total = max(0.0, min(100.0, line_score + breakout_score * 0.35 + accept_score - risk_penalty))

    return {
        "股票代码": code,
        "股票中文名称": name,
        "是否有核心线": True,
        "核心线价格": rd(line),
        "核心线周期": core.get("period_name"),
        "核心线来源": core.get("anchor_kind") or core.get("source"),
        "核心线选择理由": core.get("selection_reason"),
        "核心线锚点周期": core.get("anchor_period"),
        "核心线锚点结束日": core.get("anchor_end"),
        "核心线锚点开盘": core.get("anchor_open"),
        "核心线锚点最高": core.get("anchor_high"),
        "核心线锚点最低": core.get("anchor_low"),
        "核心线锚点收盘": core.get("anchor_close"),
        "核心线共振次数": core.get("resonance_count"),
        "核心线带量共振次数": core.get("volume_resonance_count"),
        "核心线大阳实顶共振次数": core.get("big_bull_body_top_resonance_count"),
        "核心线切实体次数": core.get("entity_cut_count"),
        "核心线加权共振分": core.get("weighted_resonance_score"),
        "核心线净分": core.get("net_score"),
        "是否近20日破界": bool(br.get("is_breakout")),
        "破界日期": br.get("date", "") if br.get("is_breakout") else "",
        "破界得分": rd(br.get("score")) if br.get("is_breakout") else 0.0,
        "破界收盘": br.get("close") if br.get("is_breakout") else None,
        "破界量比": br.get("volume_ratio") if br.get("is_breakout") else None,
        "接受状态": accept.get("status"),
        "接受得分": accept.get("score"),
        "当前收盘": rd(last_close),
        "距核心线%": rd(distance_pct, 3),
        "交易防守位": rd(defense),
        "防守距离%": rd(risk_pct, 3),
        "20日涨幅%": rd(hot20, 3),
        "20日成交额均值": rd(amt20, 2),
        "风险提示": "；".join(risk_flags) if risk_flags else "无明显硬风险提示",
        "综合得分": rd(total, 2),
        "核心线详情": core,
        "破界详情": br,
        "接受详情": accept,
    }


def _screen_one(args: Tuple[str, str, pd.DataFrame]) -> Dict[str, Any]:
    code, name, df = args
    try:
        return build_core_line_row(code, name, df)
    except Exception as exc:
        return {
            "股票代码": code,
            "股票中文名称": name,
            "是否有核心线": False,
            "核心线价格": None,
            "核心线周期": "错误",
            "核心线来源": ss(exc)[:180],
            "是否近20日破界": False,
            "综合得分": 0.0,
        }


def screen_all(hist: Dict[str, pd.DataFrame], names: Dict[str, str]) -> List[Dict[str, Any]]:
    items: List[Tuple[str, str, pd.DataFrame]] = []
    for code, df in hist.items():
        name = names.get(code, "")
        if not name and "name" in df.columns:
            vals = [ss(x) for x in df["name"].tolist() if valid_stock_display_name(code, x)]
            name = vals[-1] if vals else "名称待补"
        items.append((code, name or "名称待补", df))

    rows: List[Dict[str, Any]] = []
    log(f"开始扫描核心线：stocks={len(items)} parallel={PARALLEL} workers={WORKERS if PARALLEL else 1}")
    start = time.time()
    if PARALLEL and len(items) > 50 and WORKERS > 1:
        with ProcessPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(_screen_one, item) for item in items]
            for i, fut in enumerate(as_completed(futs), 1):
                rows.append(fut.result())
                if i == 1 or i % SCREEN_PROGRESS_EVERY == 0 or i == len(futs):
                    speed = i / max(time.time() - start, 1e-9)
                    hit = sum(1 for r in rows if r.get("是否有核心线"))
                    brk = sum(1 for r in rows if r.get("是否近20日破界"))
                    log(f"扫描进度 {i}/{len(futs)}｜核心线{hit}｜破界{brk}｜速度{speed:.2f}只/秒")
    else:
        for i, item in enumerate(items, 1):
            rows.append(_screen_one(item))
            if i == 1 or i % SCREEN_PROGRESS_EVERY == 0 or i == len(items):
                speed = i / max(time.time() - start, 1e-9)
                hit = sum(1 for r in rows if r.get("是否有核心线"))
                brk = sum(1 for r in rows if r.get("是否近20日破界"))
                log(f"扫描进度 {i}/{len(items)}｜核心线{hit}｜破界{brk}｜速度{speed:.2f}只/秒｜当前{item[0]}")
    return rows


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {ss(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(x) for x in obj]
    if isinstance(obj, set):
        return sorted(list(obj))
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    try:
        if not isinstance(obj, (str, dict, list, tuple, set)) and pd.isna(obj):
            return None
    except Exception:
        pass
    return obj


def flat_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if not isinstance(v, (dict, list, set))}


def build_report(rows: List[Dict[str, Any]], stat: Dict[str, Any]) -> str:
    core_rows = [r for r in rows if r.get("是否有核心线")]
    break_rows = [r for r in core_rows if r.get("是否近20日破界")]
    break_rows.sort(key=lambda r: (sf(r.get("综合得分")), sf(r.get("核心线共振次数")), -abs(sf(r.get("距核心线%")))), reverse=True)
    top = break_rows[:TOP_LIMIT]

    year_count = sum(1 for r in core_rows if r.get("核心线周期") == "年线")
    quarter_count = sum(1 for r in core_rows if r.get("核心线周期") == "季线")
    month_count = sum(1 for r in core_rows if r.get("核心线周期") == "月线")

    lines = [
        f"破界核心线报告｜{TARGET_DASH or TARGET}",
        f"缓存{stat.get('cache_files', 0)}｜命中{stat.get('cache_hit', 0)}｜有核心线{len(core_rows)}｜年线{year_count}｜季线{quarter_count}｜月线{month_count}｜近20日破界{len(break_rows)}",
        "口径：先年线>=4零切实体；年线没有再季线>=8零切实体；季线没有再月线>=10零切实体；月线没有则无核心线。已删除500日触发线口径。",
        "",
    ]
    if not top:
        lines.append("今日无近20日高质量破界候选。核心线全市场明细见CSV/JSON。")
        return "\n".join(lines)

    lines.append("【近20日破界Top】")
    for i, r in enumerate(top, 1):
        lines.extend([
            f"{i}. {r.get('股票代码')} {r.get('股票中文名称')}｜核心线:{r.get('核心线价格')}｜{r.get('核心线周期')}｜来源:{r.get('核心线来源')}｜共振:{r.get('核心线共振次数')}｜切实体:{r.get('核心线切实体次数')}",
            f"突破:{r.get('破界日期')}｜收:{r.get('当前收盘')}｜距线:{r.get('距核心线%')}%｜防守:{r.get('交易防守位')}｜风险:{r.get('风险提示')}",
            f"确认:{r.get('接受状态')}｜综合得分:{r.get('综合得分')}",
        ])
    lines.append("完整明细见 artifact：核心线全市场明细.csv / 核心线破界候选.csv / 核心线数据.json。")
    report = "\n".join(lines)
    if len(report) > 3900:
        report = report[:3850].rstrip() + "\n……\n报告过长，Telegram已压缩；完整明细见CSV/JSON。"
    return report


def write_outputs(rows: List[Dict[str, Any]], md: str, stat: Dict[str, Any], self_check: Dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(md, encoding="utf-8")
    all_flat = [flat_row(r) for r in rows]
    hit_flat = [flat_row(r) for r in rows if r.get("是否近20日破界")]
    pd.DataFrame(all_flat).to_csv(OUTPUT_ALL_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(hit_flat).to_csv(OUTPUT_HIT_CSV, index=False, encoding="utf-8-sig")
    payload = {
        "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "target": TARGET,
        "target_dash": TARGET_DASH,
        "boot": BOOT,
        "rules": {
            "year": f">={YEAR_MIN_RESONANCE} resonance and zero entity cut",
            "quarter": f">={QUARTER_MIN_RESONANCE} resonance and zero entity cut",
            "month": f">={MONTH_MIN_RESONANCE} resonance and zero entity cut",
            "removed": "500日触发线口径已删除",
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
    files = iter_cache_files()
    result = {
        "status": "PASS" if files else "WARN",
        "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "target": TARGET,
        "target_dash": TARGET_DASH,
        "boot": BOOT,
        "cache_dirs": [str(x) for x in CACHE_DIRS],
        "existing_cache_dirs": [str(x) for x in CACHE_DIRS if x.exists()],
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
    log(f"done md={OUTPUT_MD} all_csv={OUTPUT_ALL_CSV} hit_csv={OUTPUT_HIT_CSV} json={OUTPUT_JSON}")


if __name__ == "__main__":
    main()
