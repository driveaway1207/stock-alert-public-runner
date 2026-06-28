# -*- coding: utf-8 -*-
from __future__ import annotations

"""
破界.py｜三号员工｜600584 单票验证版 V4

本版只做单票验证，默认只跑 600584 长电科技。
核心线逻辑：
1）只从年线 / 季线 / 月线聚合K中找；
2）候选线必须来自K线实体顶 body_top = max(open, close)，不再让普通高点/影线直接定线；
3）先用所有实体顶候选线反查影线/高点/实顶/收盘共振；
4）同一主共振簇内，先锁定外部影线/高点共振最强的簇，再取该簇内最高有效实顶；
5）切实体=0是硬条件；
6）正式核心线只保留 S-Core / A-Core；
7）近端/远端只从正式核心线里按距离当前价选择，最多两条。
"""

import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import requests
except Exception:
    requests = None

BOOT = "POJIE_SINGLE_600584_MAIN_CLUSTER_BODYTOP_V4_20260628"
RUN_MODE = "single_stock_only; no_full_market_scan"
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
ENABLE_TELEGRAM = (os.getenv("POJIE_SEND_TELEGRAM") or os.getenv("ENABLE_TELEGRAM") or "0").strip() in {
    "1", "true", "True", "yes", "YES", "发送"
}

TARGET_CODES = [
    x for x in re.split(r"[,，\s]+", os.getenv("POJIE_TARGET_CODES", "600584"))
    if re.fullmatch(r"\d{6}", x)
] or ["600584"]

MIN_DAILY_ROWS = int(os.getenv("POJIE_MIN_DAILY_ROWS", "80"))
TOUCH_TOL = float(os.getenv("POJIE_TOUCH_TOL", "0.005"))
EDGE_TOL = float(os.getenv("POJIE_EDGE_TOL", "0.005"))

# 年线 / 季线 / 月线：周期代码、中文名、最低总共振数、最低外部影线/高点共振数、周期权重
PERIOD_RULES = [
    ("Y", "年线", 4, 2, 3),
    ("Q", "季线", 8, 3, 2),
    ("M", "月线", 10, 4, 1),
]

BREAKOUT_LOOKBACK_DAYS = int(os.getenv("POJIE_BREAKOUT_LOOKBACK_DAYS", "260"))
PULLBACK_LOOKBACK_AFTER_BREAK = int(os.getenv("POJIE_PULLBACK_LOOKBACK_AFTER_BREAK", "80"))


def log(msg: str) -> None:
    print(f"[破界单票][{time.time() - START_TS:7.1f}s] {msg}", flush=True)


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.replace(",", "").replace("%", "").strip()
            if not x:
                return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def rd(x: Any, n: int = 3) -> float:
    return round(sf(x), n)


def pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0 if b else 0.0


def norm_date(x: Any) -> str:
    s = ss(x)
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return ""


def now_bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def target_raw() -> str:
    for key in TARGET_ENV_KEYS:
        v = os.getenv(key)
        if v:
            return v

    d = now_bj()
    if d.weekday() >= 5 or d.hour < 20 or (d.hour == 20 and d.minute < 30):
        d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d.strftime("%Y%m%d")


TARGET = re.sub(r"\D", "", target_raw())[:8]
TARGET_DASH = f"{TARGET[:4]}-{TARGET[4:6]}-{TARGET[6:8]}" if len(TARGET) == 8 else ""


def code_of(x: Any) -> str:
    m = re.search(r"(\d{6})", ss(x))
    return m.group(1) if m else ""


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

    d = d[
        (d["date"] != "")
        & (d["open"] > 0)
        & (d["high"] > 0)
        & (d["low"] > 0)
        & (d["close"] > 0)
    ].sort_values("date").drop_duplicates("date").reset_index(drop=True)

    if TARGET_DASH:
        d = d[d["date"] <= TARGET_DASH].reset_index(drop=True)

    if d.empty:
        return d

    if "pct_chg" not in d.columns or float(d["pct_chg"].abs().sum()) == 0:
        prev = d["close"].shift(1)
        d["pct_chg"] = (d["close"] / prev - 1.0) * 100.0
        d.loc[prev <= 0, "pct_chg"] = 0.0

    return d


def find_cache_file(code: str) -> Optional[Path]:
    hits: List[Path] = []
    for root in CACHE_DIRS:
        if not root.exists():
            continue
        hits.extend([p for p in root.glob(f"*{code}*") if p.suffix.lower() in {".csv", ".json"}])
    return sorted(hits, key=lambda p: len(str(p)))[0] if hits else None


def read_cache(path: Path) -> pd.DataFrame:
    try:
        if path.suffix.lower() == ".csv":
            return normalize_hist(pd.read_csv(path))
        obj = json.loads(path.read_text(encoding="utf-8"))
        rows = (obj.get("rows") or obj.get("data") or obj.get("klines") or obj.get("items") or []) if isinstance(obj, dict) else obj
        return normalize_hist(pd.DataFrame(rows))
    except Exception:
        return pd.DataFrame()


def aggregate_period(df: pd.DataFrame, period_code: str) -> pd.DataFrame:
    d = normalize_hist(df)
    if d.empty:
        return pd.DataFrame()

    dt = pd.to_datetime(d["date"], errors="coerce")
    d = d[dt.notna()].copy()
    if d.empty:
        return pd.DataFrame()

    d["_period"] = pd.to_datetime(d["date"]).dt.to_period(period_code).astype(str)
    rows: List[Dict[str, Any]] = []

    for period, g in d.groupby("_period", sort=True):
        g = g.sort_values("date").reset_index(drop=True)
        rows.append({
            "period": ss(period),
            "start": ss(g.iloc[0]["date"]),
            "end": ss(g.iloc[-1]["date"]),
            "open": sf(g.iloc[0]["open"]),
            "high": sf(g["high"].max()),
            "low": sf(g["low"].min()),
            "close": sf(g.iloc[-1]["close"]),
            "volume": sf(g["volume"].sum()),
            "amount": sf(g["amount"].sum()) if "amount" in g.columns else 0.0,
        })

    k = pd.DataFrame(rows).sort_values("end").reset_index(drop=True)
    if k.empty:
        return k

    k["body_top"] = k[["open", "close"]].max(axis=1)
    k["body_bottom"] = k[["open", "close"]].min(axis=1)
    rng = (k["high"] - k["low"]).replace(0, np.nan)
    k["body_ratio"] = ((k["close"] - k["open"]).abs() / rng).fillna(0.0)
    k["close_pos"] = ((k["close"] - k["low"]) / rng).fillna(0.0)
    k["upper_shadow_ratio"] = ((k["high"] - k["body_top"]) / rng).fillna(0.0)
    k["is_bull"] = k["close"] > k["open"]
    k["vol_ratio_prev"] = k["volume"] / k["volume"].shift(1).replace(0, np.nan)
    k["vol_ratio_prev"] = k["vol_ratio_prev"].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # 当前正在形成的大周期K线不参与历史核心线定线，避免未完成K污染。
    if len(k) <= 1:
        return pd.DataFrame()
    return k.iloc[:-1].reset_index(drop=True)


def is_body_cut(row: Any, line: float) -> bool:
    body_top = sf(row["body_top"])
    body_bottom = sf(row["body_bottom"])
    near_body_top = abs(body_top - line) / line <= EDGE_TOL if line > 0 else False
    return body_bottom < line < body_top and not near_body_top


def resonance_flags(row: Any, line: float) -> Dict[str, bool]:
    high = sf(row["high"])
    close = sf(row["close"])
    body_top = sf(row["body_top"])

    near_high = abs(high - line) / line <= TOUCH_TOL if line > 0 else False
    shadow_cross = body_top <= line <= high if line > 0 else False
    near_body_top = abs(body_top - line) / line <= EDGE_TOL if line > 0 else False
    near_close = abs(close - line) / line <= TOUCH_TOL if line > 0 else False

    return {
        "near_high": bool(near_high),
        "shadow_cross": bool(shadow_cross),
        "near_body_top": bool(near_body_top),
        "near_close": bool(near_close),
        "high_shadow": bool(near_high or shadow_cross),
        "touched": bool(near_high or shadow_cross or near_body_top or near_close),
    }


def sample_quality(row: Any, line: float) -> Tuple[str, float, bool, bool]:
    flags = resonance_flags(row, line)
    vr = sf(row["vol_ratio_prev"])

    is_good_body_top = (
        flags["near_body_top"]
        and bool(row["is_bull"])
        and sf(row["body_ratio"]) >= 0.35
        and sf(row["close_pos"]) >= 0.55
    )

    if is_good_body_top and vr > 2.5:
        return "极端倍量实顶", 2.0, True, True
    if is_good_body_top and 1.8 <= vr <= 2.5:
        return "标准倍量实顶", 1.8, True, True
    if is_good_body_top and 1.6 <= vr < 1.8:
        return "强放量实顶", 1.6, True, False
    if is_good_body_top and 1.3 <= vr < 1.6:
        return "普通放量实顶", 1.4, True, False
    if flags["near_body_top"]:
        return "普通实顶", 1.1, False, False
    return "普通共振", 1.0, False, False


def evaluate_bodytop_candidate(k: pd.DataFrame, anchor_idx: int, line: float) -> Dict[str, Any]:
    cut_count = 0
    samples: List[Dict[str, Any]] = []

    for idx, row in k.iterrows():
        flags = resonance_flags(row, line)
        if is_body_cut(row, line):
            cut_count += 1
        if not flags["touched"]:
            continue

        quality, weight, volume_bodytop, double_bodytop = sample_quality(row, line)
        samples.append({
            "idx": int(idx),
            "period": ss(row["period"]),
            "end": ss(row["end"]),
            "quality": quality,
            "weight": weight,
            "vol_ratio_prev": rd(row["vol_ratio_prev"]),
            "near_high": flags["near_high"],
            "shadow_cross": flags["shadow_cross"],
            "near_body_top": flags["near_body_top"],
            "near_close": flags["near_close"],
            "high_shadow": flags["high_shadow"],
            "volume_bodytop": volume_bodytop,
            "double_bodytop": double_bodytop,
            "open": rd(row["open"]),
            "high": rd(row["high"]),
            "low": rd(row["low"]),
            "close": rd(row["close"]),
            "body_top": rd(row["body_top"]),
        })

    external_samples = [x for x in samples if x["idx"] != anchor_idx]
    external_high_shadow = sum(1 for x in external_samples if x["high_shadow"])
    external_near_high = sum(1 for x in external_samples if x["near_high"])
    external_shadow_cross = sum(1 for x in external_samples if x["shadow_cross"])
    bodytop_count = sum(1 for x in samples if x["near_body_top"])
    close_count = sum(1 for x in samples if x["near_close"])
    volume_bodytop_count = sum(1 for x in samples if x["volume_bodytop"])
    double_bodytop_count = sum(1 for x in samples if x["double_bodytop"])

    return {
        "line": rd(line),
        "anchor_idx": int(anchor_idx),
        "anchor_period": ss(k.iloc[anchor_idx]["period"]),
        "anchor_end": ss(k.iloc[anchor_idx]["end"]),
        "anchor_body_top": rd(k.iloc[anchor_idx]["body_top"]),
        "anchor_high": rd(k.iloc[anchor_idx]["high"]),
        "anchor_close": rd(k.iloc[anchor_idx]["close"]),
        "touch_count": len(samples),
        "cut_count": cut_count,
        "external_high_shadow_count": int(external_high_shadow),
        "external_near_high_count": int(external_near_high),
        "external_shadow_cross_count": int(external_shadow_cross),
        "bodytop_count": int(bodytop_count),
        "close_count": int(close_count),
        "volume_bodytop_count": int(volume_bodytop_count),
        "double_bodytop_count": int(double_bodytop_count),
        "weighted_score": rd(sum(sf(x["weight"]) for x in samples)),
        "sample_periods": ",".join([ss(x["period"]) for x in samples]),
        "sample_summary": "; ".join([f"{x['period']}:{x['quality']}" for x in samples]),
        "samples": samples,
    }


def grade_candidate(x: Dict[str, Any], min_touch: int, min_external_shadow: int) -> str:
    if int(x.get("cut_count", 0)) > 0:
        return "None"
    if int(x.get("touch_count", 0)) < min_touch:
        return "None"
    if int(x.get("external_high_shadow_count", 0)) < min_external_shadow:
        return "None"

    volume_bodytop = int(x.get("volume_bodytop_count", 0))
    double_bodytop = int(x.get("double_bodytop_count", 0))

    # 正式核心线只保留 A/S：必须至少有一个放量/倍量实顶共振。
    if volume_bodytop <= 0:
        return "None"

    if volume_bodytop >= 2 and double_bodytop >= 1:
        return "S-Core"
    return "A-Core"


def dedupe_by_main_cluster(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    同一主共振簇内，不直接选绝对最高价。
    先找外部影线/高点共振最强的一批，再在这批里取最高有效实顶。
    这样避免宽带边缘的孤立高实顶抢线。
    """
    groups: List[List[Dict[str, Any]]] = []

    for x in sorted(candidates, key=lambda z: sf(z["line"])):
        placed = False
        for g in groups:
            base = np.median([sf(y["line"]) for y in g])
            if base > 0 and abs(sf(x["line"]) - base) / base <= TOUCH_TOL:
                g.append(x)
                placed = True
                break
        if not placed:
            groups.append([x])

    winners: List[Dict[str, Any]] = []

    for g in groups:
        max_external = max(int(x.get("external_high_shadow_count", 0)) for x in g)
        main_cluster = [x for x in g if int(x.get("external_high_shadow_count", 0)) == max_external]

        def key(x: Dict[str, Any]) -> Tuple[Any, ...]:
            return (
                int(x.get("core_rank", 0)),
                int(x.get("double_bodytop_count", 0)),
                int(x.get("volume_bodytop_count", 0)),
                int(x.get("touch_count", 0)),
                sf(x.get("weighted_score")),
                sf(x.get("line")),  # 最后才取主簇内最高有效实顶
            )

        winners.append(max(main_cluster, key=key))

    return sorted(winners, key=lambda z: sf(z["line"]))


def scan_core_lines_for_period(df: pd.DataFrame, period_code: str, label: str, min_touch: int, min_external_shadow: int, period_rank: int) -> List[Dict[str, Any]]:
    k = aggregate_period(df, period_code)
    if k.empty:
        return []

    raw: List[Dict[str, Any]] = []

    # 候选线只来自实体顶，不从 high/close 直接定线。
    for idx, row in k.iterrows():
        line = sf(row["body_top"])
        if line <= 0:
            continue
        x = evaluate_bodytop_candidate(k, int(idx), line)
        g = grade_candidate(x, min_touch, min_external_shadow)
        if g == "None":
            continue

        x.update({
            "period_type": period_code,
            "period_label": label,
            "period_rank": period_rank,
            "core_grade": g,
            "core_rank": {"S-Core": 3, "A-Core": 2}.get(g, 0),
            "min_touch": min_touch,
            "min_external_shadow": min_external_shadow,
            "line_source": "影线/高点主共振簇内最高有效实顶",
        })
        raw.append(x)

    return dedupe_by_main_cluster(raw)


def scan_core_lines(df: pd.DataFrame) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for period_code, label, min_touch, min_external_shadow, period_rank in PERIOD_RULES:
        out.extend(scan_core_lines_for_period(df, period_code, label, min_touch, min_external_shadow, period_rank))

    # 不同周期之间再做一次合并，保留更高质量的正式核心线。
    groups: List[List[Dict[str, Any]]] = []
    for x in sorted(out, key=lambda z: sf(z["line"])):
        placed = False
        for g in groups:
            base = np.median([sf(y["line"]) for y in g])
            if base > 0 and abs(sf(x["line"]) - base) / base <= TOUCH_TOL:
                g.append(x)
                placed = True
                break
        if not placed:
            groups.append([x])

    def key(x: Dict[str, Any]) -> Tuple[Any, ...]:
        return (
            int(x.get("core_rank", 0)),
            int(x.get("period_rank", 0)),
            int(x.get("external_high_shadow_count", 0)),
            int(x.get("double_bodytop_count", 0)),
            int(x.get("volume_bodytop_count", 0)),
            int(x.get("touch_count", 0)),
            sf(x.get("weighted_score")),
            sf(x.get("line")),
        )

    return sorted([max(g, key=key) for g in groups], key=lambda z: sf(z["line"]))


def select_near_far(lines: List[Dict[str, Any]], current_close: float) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    formal = [dict(x, distance_pct=abs(pct(current_close, sf(x["line"])))) for x in lines if x.get("core_grade") in {"S-Core", "A-Core"}]
    if not formal:
        return None, None

    formal = sorted(formal, key=lambda x: sf(x["distance_pct"]))
    near = formal[0]

    rest = [x for x in formal[1:] if abs(sf(x["line"]) - sf(near["line"])) / max(sf(near["line"]), 1e-9) > TOUCH_TOL]
    far = rest[0] if rest else None
    return near, far


def daily_features(row: Any, prev: Any, line: float) -> Dict[str, Any]:
    open_ = sf(row["open"])
    high = sf(row["high"])
    low = sf(row["low"])
    close = sf(row["close"])
    volume = sf(row["volume"])
    prev_close = sf(prev["close"])
    prev_volume = sf(prev["volume"])

    rng = max(high - low, 1e-9)
    body = abs(close - open_)
    body_top = max(open_, close)
    body_bottom = min(open_, close)

    upper_shadow = max(0.0, high - body_top)
    entity_above_line = max(0.0, body_top - max(line, body_bottom)) / max(body, 1e-9)

    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "prev_close": prev_close,
        "prev_volume": prev_volume,
        "vol_ratio_prev": volume / prev_volume if prev_volume > 0 else 0.0,
        "gap_break": open_ >= line * 1.003 and prev_close < line,
        "close_pos": (close - low) / rng,
        "body_ratio": body / rng,
        "upper_shadow_ratio": upper_shadow / rng,
        "entity_above_line_ratio": entity_above_line,
        "positive": close > open_,
    }


def classify_breakout(f: Dict[str, Any]) -> Tuple[str, int]:
    base_ok = (
        bool(f["positive"])
        and sf(f["body_ratio"]) >= 0.30
        and sf(f["close_pos"]) >= 0.65
        and sf(f["upper_shadow_ratio"]) <= 0.35
        and sf(f["entity_above_line_ratio"]) >= 0.35
    )

    if not base_ok:
        return "C突破", 1

    vr = sf(f["vol_ratio_prev"])
    if bool(f["gap_break"]) and 1.8 <= vr <= 2.5:
        return "S突破", 5
    if 1.8 <= vr <= 2.5:
        return "A+突破", 4
    if bool(f["gap_break"]) and vr >= 1.45:
        return "A突破", 3
    if vr >= 1.45:
        return "B突破", 2
    return "C突破", 1


def best_breakout(df: pd.DataFrame, line: float) -> Dict[str, Any]:
    d = normalize_hist(df)
    best: Dict[str, Any] = {"grade": "无突破", "score_rank": 0}
    if d.empty or line <= 0:
        return best

    start = max(1, len(d) - BREAKOUT_LOOKBACK_DAYS)

    for idx in range(start, len(d)):
        row = d.iloc[idx]
        prev = d.iloc[idx - 1]
        prev_close = sf(prev["close"])
        prev_low = sf(prev["low"])
        close = sf(row["close"])
        high = sf(row["high"])

        from_below = prev_close <= line * 1.002 and prev_low < line
        close_above = close >= line * 1.005
        high_above = high >= line * 1.005

        if not (from_below and close_above and high_above):
            continue

        f = daily_features(row, prev, line)
        grade, rank = classify_breakout(f)
        distance = pct(close, line)

        if distance > 10:
            note = "突破有效但离线过远，等待回踩"
        elif distance > 6:
            note = "突破偏高，谨慎追高"
        else:
            note = "突破距离可接受"

        obj = {
            "grade": grade,
            "score_rank": rank,
            "date": ss(row["date"]),
            "idx": int(idx),
            "close": rd(close),
            "distance_pct": rd(distance),
            "note": note,
            "vol_ratio_prev": rd(f["vol_ratio_prev"]),
            "gap_break": bool(f["gap_break"]),
            "body_ratio": rd(f["body_ratio"]),
            "close_pos": rd(f["close_pos"]),
            "upper_shadow_ratio": rd(f["upper_shadow_ratio"]),
            "entity_above_line_ratio": rd(f["entity_above_line_ratio"]),
        }

        if (int(obj["score_rank"]), -sf(obj["distance_pct"]), int(obj["idx"])) > (
            int(best.get("score_rank", 0)), -sf(best.get("distance_pct", 999)), int(best.get("idx", 0))
        ):
            best = obj

    return best


def best_pullback(df: pd.DataFrame, line: float, breakout: Dict[str, Any]) -> Dict[str, Any]:
    d = normalize_hist(df)
    best: Dict[str, Any] = {"grade": "无回踩", "score_rank": 0}

    if d.empty or line <= 0 or breakout.get("idx") is None:
        return best

    bidx = int(breakout["idx"])
    if bidx < 0 or bidx >= len(d):
        return best

    breakout_volume = sf(d.iloc[bidx]["volume"])
    end = min(len(d), bidx + 1 + PULLBACK_LOOKBACK_AFTER_BREAK)

    for idx in range(bidx + 1, end):
        row = d.iloc[idx]
        prev = d.iloc[idx - 1]

        low = sf(row["low"])
        close = sf(row["close"])
        open_ = sf(row["open"])
        volume = sf(row["volume"])
        prev_close = sf(prev["close"])

        if low > line * 1.03:
            continue

        close_ok = close >= line * 0.995
        defense_ok = close >= line * 0.985
        shrink = breakout_volume > 0 and volume <= breakout_volume * 0.75
        pct_chg = pct(close, prev_close)
        bad_long_bear = close < open_ and pct_chg <= -3 and breakout_volume > 0 and volume >= breakout_volume * 0.80

        if not defense_ok or bad_long_bear:
            obj = {
                "grade": "失败",
                "score_rank": -1,
                "date": ss(row["date"]),
                "note": "跌破防守或放量长阴",
                "low": rd(low),
                "close": rd(close),
                "volume_vs_breakout": rd(volume / breakout_volume) if breakout_volume > 0 else 0.0,
            }
        elif close_ok and shrink and int(breakout.get("score_rank", 0)) >= 4:
            obj = {
                "grade": "S回踩",
                "score_rank": 5,
                "date": ss(row["date"]),
                "note": "强突破后的缩量回踩不破",
                "low": rd(low),
                "close": rd(close),
                "volume_vs_breakout": rd(volume / breakout_volume) if breakout_volume > 0 else 0.0,
            }
        elif close_ok and shrink and int(breakout.get("score_rank", 0)) >= 2:
            obj = {
                "grade": "A回踩",
                "score_rank": 4,
                "date": ss(row["date"]),
                "note": "有效突破后的缩量回踩不破",
                "low": rd(low),
                "close": rd(close),
                "volume_vs_breakout": rd(volume / breakout_volume) if breakout_volume > 0 else 0.0,
            }
        elif close_ok:
            obj = {
                "grade": "B观察",
                "score_rank": 2,
                "date": ss(row["date"]),
                "note": "回踩不破但量能或前置突破不足",
                "low": rd(low),
                "close": rd(close),
                "volume_vs_breakout": rd(volume / breakout_volume) if breakout_volume > 0 else 0.0,
            }
        else:
            obj = {
                "grade": "弱回踩",
                "score_rank": 1,
                "date": ss(row["date"]),
                "note": "触线但收盘偏弱",
                "low": rd(low),
                "close": rd(close),
                "volume_vs_breakout": rd(volume / breakout_volume) if breakout_volume > 0 else 0.0,
            }

        if int(obj["score_rank"]) > int(best.get("score_rank", 0)):
            best = obj

    return best


def pack_core(prefix: str, x: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not x:
        return {
            f"{prefix}核心线": None,
            f"{prefix}等级": "无",
            f"{prefix}周期": "",
            f"{prefix}共振次数": 0,
            f"{prefix}放量实顶数": 0,
            f"{prefix}倍量实顶数": 0,
            f"{prefix}切实体数": 0,
            f"{prefix}外部影线高点共振": 0,
            f"{prefix}锚点": "",
            f"{prefix}样本": "",
            f"{prefix}距离%": None,
        }

    return {
        f"{prefix}核心线": rd(x["line"]),
        f"{prefix}等级": ss(x["core_grade"]),
        f"{prefix}周期": ss(x["period_label"]),
        f"{prefix}共振次数": int(x["touch_count"]),
        f"{prefix}放量实顶数": int(x["volume_bodytop_count"]),
        f"{prefix}倍量实顶数": int(x["double_bodytop_count"]),
        f"{prefix}切实体数": int(x["cut_count"]),
        f"{prefix}外部影线高点共振": int(x["external_high_shadow_count"]),
        f"{prefix}锚点": ss(x["anchor_period"]),
        f"{prefix}样本": ss(x["sample_periods"]),
        f"{prefix}距离%": rd(x.get("distance_pct")),
    }


def final_grade(near: Optional[Dict[str, Any]], breakout: Dict[str, Any], pullback: Dict[str, Any]) -> str:
    if not near:
        return "无正式核心线"
    near_grade = ss(near.get("core_grade"))
    if near_grade in {"S-Core", "A-Core"} and pullback.get("grade") in {"S回踩", "A回踩"}:
        if near_grade == "S-Core" and pullback.get("grade") == "S回踩":
            return "S"
        return "A"
    if breakout.get("grade") in {"S突破", "A+突破", "A突破"}:
        return "B｜突破有效，等回踩"
    return "C｜核心线成立，等触发"


def analyze_one(code: str) -> Dict[str, Any]:
    path = find_cache_file(code)
    if not path:
        return {"股票代码": code, "状态": "未找到缓存"}

    df = read_cache(path)
    if df.empty or len(df) < MIN_DAILY_ROWS:
        return {"股票代码": code, "状态": "缓存无效", "缓存路径": str(path), "行数": len(df)}

    name = "长电科技" if code == "600584" else "名称待补"
    if "name" in df.columns:
        vals = [ss(x) for x in df["name"].tolist() if ss(x)]
        if vals:
            name = vals[-1]

    current_close = sf(df.iloc[-1]["close"])
    lines = scan_core_lines(df)
    near, far = select_near_far(lines, current_close)

    breakout = best_breakout(df, sf(near["line"])) if near else {"grade": "无突破", "score_rank": 0}
    pullback = best_pullback(df, sf(near["line"]), breakout) if near else {"grade": "无回踩", "score_rank": 0}

    row: Dict[str, Any] = {
        "股票代码": code,
        "股票中文名称": name,
        "最新日期": ss(df.iloc[-1]["date"]),
        "当前收盘": rd(current_close),
        "状态": "完成",
        "缓存路径": str(path),
        "正式核心线数量": len(lines),
        "突破等级": breakout.get("grade"),
        "突破日期": breakout.get("date", ""),
        "突破说明": breakout.get("note", ""),
        "回踩等级": pullback.get("grade"),
        "回踩日期": pullback.get("date", ""),
        "回踩说明": pullback.get("note", ""),
        "最终等级": final_grade(near, breakout, pullback),
        "近端详情": near or {},
        "远端详情": far or {},
        "突破详情": breakout,
        "回踩详情": pullback,
    }
    row.update(pack_core("近端", near))
    row.update(pack_core("远端", far))
    return row


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    try:
        if pd.isna(obj) and not isinstance(obj, (str, dict, list, tuple)):
            return None
    except Exception:
        pass
    return obj


def build_report(rows: List[Dict[str, Any]]) -> str:
    lines = [
        f"破界单票验证｜{TARGET_DASH or TARGET}",
        f"BOOT={BOOT}",
        f"RUN_MODE={RUN_MODE}",
        f"目标股票={','.join(TARGET_CODES)}",
        "逻辑：实体顶候选 → 外部影线/高点主共振簇 → 主簇内最高有效实顶 → 零切实体 → S/A-Core。",
        "",
    ]

    for r in rows:
        lines.extend([
            f"{r.get('股票代码')} {r.get('股票中文名称')}｜收盘 {r.get('当前收盘')}｜{r.get('最终等级')}",
            f"近端核心线：{r.get('近端核心线')}｜{r.get('近端等级')}｜{r.get('近端周期')}｜共振{r.get('近端共振次数')}｜放量实顶{r.get('近端放量实顶数')}｜倍量实顶{r.get('近端倍量实顶数')}｜外部影线高点{r.get('近端外部影线高点共振')}｜切实体{r.get('近端切实体数')}｜距现价{r.get('近端距离%')}%",
            f"远端核心线：{r.get('远端核心线')}｜{r.get('远端等级')}｜{r.get('远端周期')}｜共振{r.get('远端共振次数')}｜放量实顶{r.get('远端放量实顶数')}｜倍量实顶{r.get('远端倍量实顶数')}｜外部影线高点{r.get('远端外部影线高点共振')}｜切实体{r.get('远端切实体数')}｜距现价{r.get('远端距离%')}%",
            f"近端定线：锚点{r.get('近端锚点')}｜样本{r.get('近端样本')}",
            f"远端定线：锚点{r.get('远端锚点')}｜样本{r.get('远端样本')}",
            f"突破：{r.get('突破等级')}｜{r.get('突破日期')}｜{r.get('突破说明')}",
            f"回踩：{r.get('回踩等级')}｜{r.get('回踩日期')}｜{r.get('回踩说明')}",
            "",
        ])

    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3850].rstrip() + "\n……\n报告过长，完整明细见 artifact。"
    return text


def write_outputs(rows: List[Dict[str, Any]], report_text: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(report_text, encoding="utf-8")

    flat_rows: List[Dict[str, Any]] = []
    for r in rows:
        flat_rows.append({k: v for k, v in r.items() if not isinstance(v, (dict, list))})
    pd.DataFrame(flat_rows).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    payload = {
        "boot": BOOT,
        "run_mode": RUN_MODE,
        "target": TARGET,
        "target_dash": TARGET_DASH,
        "target_codes": TARGET_CODES,
        "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "touch_tol": TOUCH_TOL,
            "edge_tol": EDGE_TOL,
            "period_rules": PERIOD_RULES,
            "breakout_lookback_days": BREAKOUT_LOOKBACK_DAYS,
            "pullback_lookback_after_break": PULLBACK_LOOKBACK_AFTER_BREAK,
        },
        "rows": rows,
    }
    OUTPUT_JSON.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")

    self_check = {
        "status": "PASS",
        "boot": BOOT,
        "run_mode": RUN_MODE,
        "target": TARGET,
        "target_codes": TARGET_CODES,
        "single_stock_only": True,
        "full_market_scan": False,
        "cache_dirs": [str(x) for x in CACHE_DIRS],
    }
    SELF_CHECK_JSON.write_text(json.dumps(self_check, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram(text: str) -> None:
    if not ENABLE_TELEGRAM or not BOT or not CHAT or requests is None:
        log(f"Telegram跳过 enable={ENABLE_TELEGRAM} token={bool(BOT)} chat={bool(CHAT)} requests={requests is not None}")
        print(text, flush=True)
        return

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT}/sendMessage",
            json={"chat_id": CHAT, "text": text, "disable_web_page_preview": True},
            timeout=30,
        )
        log(f"Telegram status={getattr(resp, 'status_code', 'NA')} body={getattr(resp, 'text', '')[:160]}")
    except Exception as exc:
        log(f"Telegram发送失败：{exc}")


def main() -> None:
    print(BOOT, flush=True)
    print(f"RUN_MODE={RUN_MODE}", flush=True)
    print(f"file={Path(__file__).resolve()}", flush=True)
    print(f"target={TARGET} target_dash={TARGET_DASH}", flush=True)
    print("target_codes=" + ",".join(TARGET_CODES), flush=True)
    print("cache_dirs=" + " | ".join(str(x) for x in CACHE_DIRS), flush=True)

    rows: List[Dict[str, Any]] = []
    for code in TARGET_CODES:
        log(f"开始单票分析 {code}")
        row = analyze_one(code)
        rows.append(row)
        log(f"完成 {code}｜近端={row.get('近端核心线')}｜远端={row.get('远端核心线')}｜等级={row.get('最终等级')}")

    report_text = build_report(rows)
    write_outputs(rows, report_text)
    send_telegram(report_text)
    log(f"done report={OUTPUT_MD} csv={OUTPUT_CSV} json={OUTPUT_JSON}")


if __name__ == "__main__":
    main()
