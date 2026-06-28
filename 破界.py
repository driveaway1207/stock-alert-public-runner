
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
破界.py｜三号员工｜600584 单票验证版
核心线：先找共振带，再取共振带内最高有效实顶定线。
workflow 不改：仍读缓存、输出破界报告、可发 Telegram。
"""
import json, math, os, re, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import requests
except Exception:
    requests = None

BOOT = "POJIE_SINGLE_600584_BAND_HIGHEST_BODYTOP_V1_20260628"
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
    "1", "true", "True", "YES", "yes", "发送"
}

TARGET_CODES = [
    x for x in re.split(r"[,，\s]+", os.getenv("POJIE_TARGET_CODES", "600584"))
    if re.fullmatch(r"\d{6}", x)
] or ["600584"]

MIN_ROWS = int(os.getenv("POJIE_MIN_ROWS", "80"))

# 共振带容差、触线容差、实体顶贴线容差：默认都是 0.5%。
BAND_TOL = float(os.getenv("POJIE_BAND_TOL", "0.005"))
TOUCH_TOL = float(os.getenv("POJIE_TOUCH_TOL", "0.005"))
EDGE_TOL = float(os.getenv("POJIE_EDGE_TOL", "0.005"))

# 正式核心线默认只保留 S-Core/A-Core；B-React 只做调试，不抢近端/远端。
FORMAL_ONLY = (os.getenv("POJIE_FORMAL_CORE_ONLY") or "1").strip() not in {"0", "false", "False", "no", "NO"}

PERIODS = [
    ("Y", "年线", 4, 3),
    ("Q", "季线", 8, 2),
    ("M", "月线", 10, 1),
]

BREAKOUT_LOOKBACK_DAYS = int(os.getenv("POJIE_BREAKOUT_LOOKBACK_DAYS", "260"))
PULLBACK_LOOKBACK_AFTER_BREAK = int(os.getenv("POJIE_PULLBACK_LOOKBACK_AFTER_BREAK", "80"))


def log(msg: str) -> None:
    print(f"[破界][{time.time() - START_TS:7.1f}s] {msg}", flush=True)


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


def pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0 if b else 0.0


def now_bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def target_raw() -> str:
    for key in TARGET_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value
    bj = now_bj()
    if bj.weekday() >= 5 or bj.hour < 20 or (bj.hour == 20 and bj.minute < 30):
        bj = bj - timedelta(days=1)
    while bj.weekday() >= 5:
        bj = bj - timedelta(days=1)
    return bj.strftime("%Y%m%d")


TARGET = re.sub(r"\D", "", target_raw())[:8]
TARGET_DASH = f"{TARGET[:4]}-{TARGET[4:6]}-{TARGET[6:8]}" if len(TARGET) == 8 else ""


def norm_date(x: Any) -> str:
    s = ss(x)
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return ""


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

    d = d[(d["date"] != "") & (d["open"] > 0) & (d["high"] > 0) & (d["low"] > 0) & (d["close"] > 0)]
    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)

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
    for directory in CACHE_DIRS:
        if not directory.exists():
            continue
        for p in directory.glob(f"*{code}*"):
            if p.suffix.lower() in {".csv", ".json"}:
                hits.append(p)
    if not hits:
        return None
    return sorted(hits, key=lambda x: len(str(x)))[0]


def read_cache(path: Path) -> pd.DataFrame:
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


def aggregate_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    d = normalize_hist(df)
    if d.empty:
        return pd.DataFrame()

    dt = pd.to_datetime(d["date"], errors="coerce")
    d = d[dt.notna()].copy()
    if d.empty:
        return pd.DataFrame()

    d["_period"] = pd.to_datetime(d["date"]).dt.to_period(period).astype(str)
    rows: List[Dict[str, Any]] = []

    for p, g in d.groupby("_period", sort=True):
        g = g.sort_values("date").reset_index(drop=True)
        rows.append({
            "period": ss(p),
            "start": ss(g.iloc[0]["date"]),
            "end": ss(g.iloc[-1]["date"]),
            "open": sf(g.iloc[0]["open"]),
            "high": sf(g["high"].max()),
            "low": sf(g["low"].min()),
            "close": sf(g.iloc[-1]["close"]),
            "volume": sf(g["volume"].sum()) if "volume" in g.columns else 0.0,
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

    # 排除当前未完成周期，避免正在形成的大年K/季K/月K污染核心线。
    if len(k) <= 1:
        return pd.DataFrame()
    return k.iloc[:-1].reset_index(drop=True)


def make_resonance_bands(k: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    先找共振带，不直接定线。
    候选节点可以来自 high/body_top/close；最终线不能直接用这些节点，
    必须再回到共振带内找最高有效实顶。
    """
    nodes: List[Dict[str, Any]] = []

    for idx, r in k.iterrows():
        for kind, price in [
            ("high", sf(r["high"])),
            ("body_top", sf(r["body_top"])),
            ("close", sf(r["close"])),
        ]:
            if price > 0:
                nodes.append({
                    "idx": int(idx),
                    "kind": kind,
                    "price": rd(price),
                    "period": ss(r["period"]),
                    "end": ss(r["end"]),
                })

    if not nodes:
        return []

    nodes = sorted(nodes, key=lambda x: sf(x["price"]))
    groups: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []

    for node in nodes:
        price = sf(node["price"])
        if not cur:
            cur = [node]
            continue

        group_prices = [sf(x["price"]) for x in cur]
        group_mid = float(np.median(group_prices)) if group_prices else price

        if group_mid > 0 and abs(price - group_mid) / group_mid <= BAND_TOL:
            cur.append(node)
        else:
            groups.append(cur)
            cur = [node]

    if cur:
        groups.append(cur)

    bands: List[Dict[str, Any]] = []
    for g in groups:
        prices = [sf(x["price"]) for x in g]
        bands.append({
            "low": rd(min(prices)),
            "high": rd(max(prices)),
            "mid": rd(float(np.median(prices))),
            "node_count": len(g),
            "nodes": g,
        })
    return bands


def choose_highest_bodytop_in_band(k: pd.DataFrame, band: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    核心步骤：
    共振带不是最终核心线。
    共振带内最高有效实顶，才是最终定线价格。
    """
    b_low = sf(band["low"])
    b_high = sf(band["high"])
    if b_low <= 0 or b_high <= 0:
        return None

    expanded_low = b_low * (1.0 - EDGE_TOL)
    expanded_high = b_high * (1.0 + EDGE_TOL)

    candidates: List[Dict[str, Any]] = []

    for idx, r in k.iterrows():
        body_top = sf(r["body_top"])
        if body_top <= 0:
            continue
        if expanded_low <= body_top <= expanded_high:
            candidates.append({
                "line": rd(body_top),
                "anchor_idx": int(idx),
                "anchor_period": ss(r["period"]),
                "anchor_end": ss(r["end"]),
                "anchor_open": rd(r["open"]),
                "anchor_high": rd(r["high"]),
                "anchor_low": rd(r["low"]),
                "anchor_close": rd(r["close"]),
                "anchor_body_ratio": rd(r["body_ratio"]),
                "anchor_close_pos": rd(r["close_pos"]),
                "anchor_vol_ratio_prev": rd(r["vol_ratio_prev"]),
                "anchor_is_bull": bool(r["is_bull"]),
            })

    if not candidates:
        return None

    # 取共振带内最高实顶；不是均值，不是最低点，不是普通高点。
    return max(candidates, key=lambda x: sf(x["line"]))


def score_final_line_from_bodytop(k: pd.DataFrame, line: float) -> Dict[str, Any]:
    """用最高实顶线反查共振数量、放量实顶、倍量实顶、切实体。"""
    cut_count = 0
    samples: List[Dict[str, Any]] = []

    for _, r in k.iterrows():
        open_ = sf(r["open"])
        high = sf(r["high"])
        low = sf(r["low"])
        close = sf(r["close"])
        volume = sf(r["volume"])
        body_top = max(open_, close)
        body_bottom = min(open_, close)

        if line <= 0:
            continue

        near_high = abs(high - line) / line <= TOUCH_TOL
        shadow_cross = body_top <= line <= high
        near_body_top = abs(body_top - line) / line <= EDGE_TOL
        near_close = abs(close - line) / line <= TOUCH_TOL

        # 切实体硬杀：进入实体内部，且不是实顶贴线。
        inside_body = body_bottom < line < body_top
        is_cut = inside_body and not near_body_top
        if is_cut:
            cut_count += 1

        touched = near_high or shadow_cross or near_body_top or near_close
        if not touched:
            continue

        good_bodytop = (
            bool(r["is_bull"])
            and sf(r["body_ratio"]) >= 0.35
            and sf(r["close_pos"]) >= 0.55
            and near_body_top
        )
        vr = sf(r["vol_ratio_prev"])

        if good_bodytop and vr > 2.5:
            quality, weight = "极端倍量实顶", 2.0
        elif good_bodytop and 1.8 <= vr <= 2.5:
            quality, weight = "标准倍量实顶", 1.8
        elif good_bodytop and 1.6 <= vr < 1.8:
            quality, weight = "强放量实顶", 1.6
        elif good_bodytop and 1.3 <= vr < 1.6:
            quality, weight = "普通放量实顶", 1.4
        elif near_body_top:
            quality, weight = "普通实顶", 1.1
        else:
            quality, weight = "普通共振", 1.0

        samples.append({
            "period": ss(r["period"]),
            "end": ss(r["end"]),
            "quality": quality,
            "weight": weight,
            "vol_ratio_prev": rd(vr),
            "volume_bodytop": quality in {"普通放量实顶", "强放量实顶", "标准倍量实顶", "极端倍量实顶"},
            "double_bodytop": quality in {"标准倍量实顶", "极端倍量实顶"},
            "near_high": bool(near_high),
            "shadow_cross": bool(shadow_cross),
            "near_body_top": bool(near_body_top),
            "near_close": bool(near_close),
            "open": rd(open_),
            "high": rd(high),
            "low": rd(low),
            "close": rd(close),
            "volume": rd(volume),
        })

    return {
        "line": rd(line),
        "touch_count": len(samples),
        "cut_count": cut_count,
        "volume_bodytop_count": int(sum(x["volume_bodytop"] for x in samples)),
        "double_bodytop_count": int(sum(x["double_bodytop"] for x in samples)),
        "weighted_score": rd(sum(sf(x["weight"]) for x in samples)),
        "samples": samples,
    }


def classify_core(stats: Dict[str, Any], min_touch: int) -> str:
    if int(stats.get("touch_count", 0)) < min_touch:
        return "None"
    if int(stats.get("cut_count", 0)) > 0:
        return "None"

    volume_bodytop = int(stats.get("volume_bodytop_count", 0))
    double_bodytop = int(stats.get("double_bodytop_count", 0))

    if volume_bodytop >= 2 and double_bodytop >= 1:
        return "S-Core"
    if volume_bodytop >= 1:
        return "A-Core"
    return "B-React"


def scan_period_core_lines(df: pd.DataFrame, period: str, label: str, min_touch: int, period_rank: int) -> List[Dict[str, Any]]:
    k = aggregate_period(df, period)
    if k.empty:
        return []

    bands = make_resonance_bands(k)
    out: List[Dict[str, Any]] = []

    for band in bands:
        anchor = choose_highest_bodytop_in_band(k, band)
        if not anchor:
            continue

        line = sf(anchor["line"])
        stats = score_final_line_from_bodytop(k, line)
        grade = classify_core(stats, min_touch)

        if grade == "None":
            continue
        if FORMAL_ONLY and grade == "B-React":
            continue

        stats.update({
            "period_type": period,
            "period_label": label,
            "period_rank": period_rank,
            "min_touch": min_touch,
            "core_grade": grade,
            "core_rank": {"S-Core": 3, "A-Core": 2, "B-React": 1}.get(grade, 0),
            "band_low": band["low"],
            "band_high": band["high"],
            "band_mid": band["mid"],
            "band_node_count": band["node_count"],
            "anchor_rule": "共振带内最高有效实顶",
            **anchor,
        })
        out.append(stats)

    return out


def dedupe_core_lines(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: List[List[Dict[str, Any]]] = []

    for x in sorted(lines, key=lambda z: sf(z["line"])):
        placed = False
        for g in groups:
            base = sf(g[0]["line"])
            if base > 0 and abs(sf(x["line"]) - base) / base <= BAND_TOL:
                g.append(x)
                placed = True
                break
        if not placed:
            groups.append([x])

    def rank_key(x: Dict[str, Any]) -> Tuple[Any, ...]:
        return (
            int(x.get("core_rank", 0)),
            int(x.get("period_rank", 0)),
            int(x.get("double_bodytop_count", 0)),
            int(x.get("volume_bodytop_count", 0)),
            sf(x.get("weighted_score")),
            int(x.get("touch_count", 0)),
            sf(x.get("line")),
        )

    return sorted([max(g, key=rank_key) for g in groups], key=lambda z: sf(z["line"]))


def scan_core_lines(df: pd.DataFrame) -> List[Dict[str, Any]]:
    lines: List[Dict[str, Any]] = []
    for period, label, min_touch, period_rank in PERIODS:
        lines.extend(scan_period_core_lines(df, period, label, min_touch, period_rank))
    return dedupe_core_lines(lines)


def select_near_far(lines: List[Dict[str, Any]], current_close: float) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    valid = [
        dict(x, distance_pct=abs(pct(current_close, sf(x["line"]))))
        for x in lines
        if sf(x.get("line")) > 0 and ss(x.get("core_grade")) in {"S-Core", "A-Core"}
    ]

    if not valid:
        return None, None

    valid = sorted(valid, key=lambda x: sf(x["distance_pct"]))
    near = valid[0]
    far = valid[1] if len(valid) >= 2 else None
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
            int(best.get("score_rank", 0)),
            -sf(best.get("distance_pct", 999)),
            int(best.get("idx", 0)),
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
            f"{prefix}距离%": None,
            f"{prefix}定线规则": "",
            f"{prefix}锚点周期": "",
        }

    return {
        f"{prefix}核心线": rd(x["line"]),
        f"{prefix}等级": x["core_grade"],
        f"{prefix}周期": x["period_label"],
        f"{prefix}共振次数": int(x["touch_count"]),
        f"{prefix}放量实顶数": int(x["volume_bodytop_count"]),
        f"{prefix}倍量实顶数": int(x["double_bodytop_count"]),
        f"{prefix}切实体数": int(x["cut_count"]),
        f"{prefix}距离%": rd(x.get("distance_pct")),
        f"{prefix}定线规则": x.get("anchor_rule", ""),
        f"{prefix}锚点周期": x.get("anchor_period", ""),
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

    if near_grade in {"S-Core", "A-Core"}:
        return "C｜核心线成立，等触发"

    return "C"


def analyze_one(code: str) -> Dict[str, Any]:
    path = find_cache_file(code)
    if not path:
        return {"股票代码": code, "状态": "未找到缓存"}

    df = read_cache(path)
    if df.empty or len(df) < MIN_ROWS:
        return {
            "股票代码": code,
            "状态": "缓存无效",
            "缓存路径": str(path),
            "行数": len(df),
        }

    name = "长电科技" if code == "600584" else "名称待补"
    if "name" in df.columns:
        vals = [ss(x) for x in df["name"].tolist() if ss(x)]
        if vals:
            name = vals[-1]

    current_close = sf(df.iloc[-1]["close"])

    core_lines = scan_core_lines(df)
    near, far = select_near_far(core_lines, current_close)

    breakout = best_breakout(df, sf(near["line"])) if near else {"grade": "无突破", "score_rank": 0}
    pullback = best_pullback(df, sf(near["line"]), breakout) if near else {"grade": "无回踩", "score_rank": 0}

    row: Dict[str, Any] = {
        "股票代码": code,
        "股票中文名称": name,
        "最新日期": ss(df.iloc[-1]["date"]),
        "当前收盘": rd(current_close),
        "状态": "完成",
        "缓存路径": str(path),
        "正式核心线数量": len(core_lines),
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
        f"目标股票={','.join(TARGET_CODES)}",
        "逻辑：先找共振带，再取共振带内最高有效实顶定线；正式核心线只保留 S-Core / A-Core。",
        "",
    ]

    for r in rows:
        lines.extend([
            f"{r.get('股票代码')} {r.get('股票中文名称')}｜收盘 {r.get('当前收盘')}｜{r.get('最终等级')}",
            f"近端核心线：{r.get('近端核心线')}｜{r.get('近端等级')}｜{r.get('近端周期')}｜共振{r.get('近端共振次数')}｜放量实顶{r.get('近端放量实顶数')}｜倍量实顶{r.get('近端倍量实顶数')}｜切实体{r.get('近端切实体数')}｜距现价{r.get('近端距离%')}%",
            f"远端核心线：{r.get('远端核心线')}｜{r.get('远端等级')}｜{r.get('远端周期')}｜共振{r.get('远端共振次数')}｜放量实顶{r.get('远端放量实顶数')}｜倍量实顶{r.get('远端倍量实顶数')}｜切实体{r.get('远端切实体数')}｜距现价{r.get('远端距离%')}%",
            f"近端定线：{r.get('近端定线规则')}｜锚点：{r.get('近端锚点周期')}",
            f"远端定线：{r.get('远端定线规则')}｜锚点：{r.get('远端锚点周期')}",
            f"突破：{r.get('突破等级')}｜{r.get('突破日期')}｜{r.get('突破说明')}",
            f"回踩：{r.get('回踩等级')}｜{r.get('回踩日期')}｜{r.get('回踩说明')}",
            "",
        ])

    if len(lines) == 5:
        lines.append("无结果。")

    report = "\n".join(lines)
    if len(report) > 3900:
        report = report[:3850].rstrip() + "\n……\n报告过长，完整明细见 artifact。"
    return report


def write_outputs(rows: List[Dict[str, Any]], md: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(md, encoding="utf-8")

    flat_rows = []
    for r in rows:
        flat_rows.append({k: v for k, v in r.items() if not isinstance(v, (dict, list))})
    pd.DataFrame(flat_rows).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    payload = {
        "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "boot": BOOT,
        "target": TARGET,
        "target_dash": TARGET_DASH,
        "target_codes": TARGET_CODES,
        "config": {
            "band_tol": BAND_TOL,
            "touch_tol": TOUCH_TOL,
            "edge_tol": EDGE_TOL,
            "formal_only": FORMAL_ONLY,
            "periods": PERIODS,
            "breakout_lookback_days": BREAKOUT_LOOKBACK_DAYS,
            "pullback_lookback_after_break": PULLBACK_LOOKBACK_AFTER_BREAK,
        },
        "rows": rows,
    }
    OUTPUT_JSON.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2), encoding="utf-8")

    self_check = {
        "status": "PASS",
        "boot": BOOT,
        "target": TARGET,
        "target_codes": TARGET_CODES,
        "cache_dirs": [str(x) for x in CACHE_DIRS],
        "model": "band_then_highest_bodytop",
    }
    SELF_CHECK_JSON.write_text(json.dumps(self_check, ensure_ascii=False, indent=2), encoding="utf-8")


def send_report(md: str) -> None:
    if not ENABLE_TELEGRAM or not BOT or not CHAT or requests is None:
        log(f"Telegram跳过 enable={ENABLE_TELEGRAM} token={bool(BOT)} chat={bool(CHAT)} requests={requests is not None}")
        print(md, flush=True)
        return

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT}/sendMessage",
            json={"chat_id": CHAT, "text": md, "disable_web_page_preview": True},
            timeout=30,
        )
        log(f"Telegram status={getattr(resp, 'status_code', 'NA')} body={getattr(resp, 'text', '')[:160]}")
    except Exception as exc:
        log(f"Telegram发送失败：{exc}")


def main() -> None:
    print(BOOT, flush=True)
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

    md = build_report(rows)
    write_outputs(rows, md)
    send_report(md)
    log(f"done report={OUTPUT_MD} csv={OUTPUT_CSV} json={OUTPUT_JSON}")


if __name__ == "__main__":
    main()
