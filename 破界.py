# -*- coding: utf-8 -*-
from __future__ import annotations

"""
破界.py｜三号员工｜600584 单票验证版 V7

本版核心修正：
1）候选线仍然来自实体顶 body_top；
2）high / body_top / close 是离散主反应点；
3）上影线穿越不再无条件等于主共振，但会作为“外部影线接受/反应”计入有效共振；
4）排序先看外部有效反应和总有效共振，再看放量/倍量实顶，最后才看更高实顶；
5）最终线必须零切实体；
6）正式核心线只保留 S-Core / A-Core；
7）默认只跑 600584 长电科技，不做全市场扫描。

workflow 不需要改，仍然执行：
python -u 破界.py
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


BOOT = "POJIE_SINGLE_600584_REACTION_SHADOW_BODYTOP_V7_20260628"
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
SEND_TELEGRAM = (os.getenv("POJIE_SEND_TELEGRAM") or os.getenv("ENABLE_TELEGRAM") or "0").strip() in {
    "1", "true", "True", "yes", "YES", "发送"
}

TARGET_CODES = [
    x for x in re.split(r"[,，\s]+", os.getenv("POJIE_TARGET_CODES", "600584"))
    if re.fullmatch(r"\d{6}", x)
] or ["600584"]

MIN_ROWS = int(os.getenv("POJIE_MIN_ROWS", "80"))

# 主反应点聚类容差。这里是核心容差，不是上影线穿越容差。
POINT_TOL = float(os.getenv("POJIE_POINT_TOL", "0.005"))

# 实顶贴线容差。线压在实顶边缘不算切实体。
BODY_EDGE_TOL = float(os.getenv("POJIE_BODY_EDGE_TOL", "0.005"))

# 上影线共振不再作为任意穿越直接成立，但可以作为外部有效反应。
# 影线共振要求：line 在上影线内部，且不是极端长影中随便穿过的孤立位置。
# 为避免过严，默认允许上影线内部穿越，但排序上低于 high/body_top/close 离散点。
SHADOW_REACTION_WEIGHT = float(os.getenv("POJIE_SHADOW_REACTION_WEIGHT", "0.70"))

PERIOD_SPECS = [
    # period, label, min_effective_unique, period_rank
    ("Y", "年线", 4, 3),
    ("Q", "季线", 8, 2),
    ("M", "月线", 10, 1),
]


def min_seed_discrete_count(min_effective_unique: int) -> int:
    """
    V7 修正：
    主簇种子不能直接要求达到年/季/月完整共振门槛。
    因为你的逻辑是“实顶 + 其他影线能共振上”，影线也属于有效反应。
    如果建簇阶段就强制年线离散点>=4，会把“2个离散点 + 2个影线反应”的正确核心线直接杀掉。
    """
    if min_effective_unique <= 4:
        return 2
    if min_effective_unique <= 8:
        return 3
    return 4

BREAKOUT_LOOKBACK_DAYS = int(os.getenv("POJIE_BREAKOUT_LOOKBACK_DAYS", "260"))
PULLBACK_LOOKBACK_AFTER_BREAK = int(os.getenv("POJIE_PULLBACK_LOOKBACK_AFTER_BREAK", "80"))


def log(msg: str) -> None:
    print(f"[破界V5][{time.time() - START_TS:7.1f}s] {msg}", flush=True)


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


def now_bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def resolve_target_raw() -> str:
    for key in TARGET_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value

    d = now_bj()
    if d.weekday() >= 5 or d.hour < 20 or (d.hour == 20 and d.minute < 30):
        d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d.strftime("%Y%m%d")


TARGET = re.sub(r"\D", "", resolve_target_raw())[:8]
TARGET_DASH = f"{TARGET[:4]}-{TARGET[4:6]}-{TARGET[6:8]}" if len(TARGET) == 8 else ""


def normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    col_map = {
        "日期": "date", "交易日期": "date", "date": "date", "time": "date",
        "代码": "code", "股票代码": "code", "证券代码": "code", "symbol": "code", "code": "code",
        "名称": "name", "股票名称": "name", "股票简称": "name", "证券简称": "name", "name": "name",
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

    if not hits:
        return None

    return sorted(hits, key=lambda p: (len(str(p)), str(p)))[0]


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
            "volume": sf(g["volume"].sum()),
            "amount": sf(g["amount"].sum()) if "amount" in g.columns else 0.0,
        })

    k = pd.DataFrame(rows).sort_values("end").reset_index(drop=True)
    if k.empty:
        return pd.DataFrame()

    k["body_top"] = k[["open", "close"]].max(axis=1)
    k["body_bottom"] = k[["open", "close"]].min(axis=1)
    rng = (k["high"] - k["low"]).replace(0, np.nan)
    k["body_ratio"] = ((k["close"] - k["open"]).abs() / rng).fillna(0.0)
    k["close_pos"] = ((k["close"] - k["low"]) / rng).fillna(0.0)
    k["upper_shadow_ratio"] = ((k["high"] - k["body_top"]) / rng).fillna(0.0)
    k["is_bull"] = k["close"] > k["open"]
    k["vol_ratio_prev"] = k["volume"] / k["volume"].shift(1).replace(0, np.nan)
    k["vol_ratio_prev"] = k["vol_ratio_prev"].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # 排除当前未完成的大周期K，避免正在形成的年/季/月K污染核心线。
    if len(k) <= 1:
        return pd.DataFrame()
    return k.iloc[:-1].reset_index(drop=True)


def is_volume_bodytop(row: Any) -> bool:
    return (
        bool(row["is_bull"])
        and sf(row["body_ratio"]) >= 0.35
        and sf(row["close_pos"]) >= 0.55
        and sf(row["vol_ratio_prev"]) >= 1.30
    )


def is_double_bodytop(row: Any) -> bool:
    return (
        bool(row["is_bull"])
        and sf(row["body_ratio"]) >= 0.35
        and sf(row["close_pos"]) >= 0.55
        and 1.80 <= sf(row["vol_ratio_prev"]) <= 2.50
    )


def reaction_point_weight(kind: str, row: Any) -> float:
    if kind == "body_top":
        if is_double_bodytop(row):
            return 1.8
        if is_volume_bodytop(row):
            vr = sf(row["vol_ratio_prev"])
            if vr >= 1.60:
                return 1.6
            return 1.4
        return 1.2
    if kind == "high":
        return 1.0
    if kind == "close":
        return 1.0
    return 1.0


def build_reaction_points(k: pd.DataFrame) -> List[Dict[str, Any]]:
    points: List[Dict[str, Any]] = []

    for idx, row in k.iterrows():
        for kind, price in [
            ("high", sf(row["high"])),
            ("body_top", sf(row["body_top"])),
            ("close", sf(row["close"])),
        ]:
            if price <= 0:
                continue
            points.append({
                "bar_index": int(idx),
                "period": ss(row["period"]),
                "end": ss(row["end"]),
                "kind": kind,
                "price": rd(price, 4),
                "weight": reaction_point_weight(kind, row),
                "is_volume_bodytop": bool(kind == "body_top" and is_volume_bodytop(row)),
                "is_double_bodytop": bool(kind == "body_top" and is_double_bodytop(row)),
                "open": rd(row["open"]),
                "high": rd(row["high"]),
                "low": rd(row["low"]),
                "close": rd(row["close"]),
                "body_top": rd(row["body_top"]),
                "volume": rd(row["volume"]),
                "vol_ratio_prev": rd(row["vol_ratio_prev"]),
            })

    return sorted(points, key=lambda x: sf(x["price"]))


def points_near(points: List[Dict[str, Any]], center: float, tol: float) -> List[Dict[str, Any]]:
    if center <= 0:
        return []
    return [p for p in points if abs(sf(p["price"]) - center) / center <= tol]


def choose_best_point_per_bar(points: List[Dict[str, Any]], prefer_line: Optional[float] = None) -> List[Dict[str, Any]]:
    by_bar: Dict[int, Dict[str, Any]] = {}

    def key(p: Dict[str, Any]) -> Tuple[Any, ...]:
        dist_key = -abs(sf(p["price"]) - sf(prefer_line)) if prefer_line else 0.0
        kind_rank = {"body_top": 3, "high": 2, "close": 1}.get(ss(p["kind"]), 0)
        return (sf(p["weight"]), kind_rank, dist_key)

    for p in points:
        b = int(p["bar_index"])
        if b not in by_bar or key(p) > key(by_bar[b]):
            by_bar[b] = p

    return sorted(by_bar.values(), key=lambda x: int(x["bar_index"]))


def cluster_from_seed(points: List[Dict[str, Any]], seed_price: float, min_seed_unique: int) -> Optional[Dict[str, Any]]:
    raw = points_near(points, seed_price, POINT_TOL)
    uniq = choose_best_point_per_bar(raw, seed_price)

    if len(uniq) < min_seed_unique:
        return None

    bodytops = [p for p in raw if ss(p["kind"]) == "body_top"]
    if not bodytops:
        return None

    prices = [sf(p["price"]) for p in uniq]
    center = float(np.average(prices, weights=[max(sf(p["weight"]), 0.1) for p in uniq]))
    low = min(prices)
    high = max(prices)

    return {
        "seed": rd(seed_price),
        "center": rd(center),
        "low": rd(low),
        "high": rd(high),
        "primary_unique": len(uniq),
        "primary_points": uniq,
        "raw_points": raw,
        "bodytop_candidates": sorted(bodytops, key=lambda x: sf(x["price"])),
        "primary_weight": rd(sum(sf(p["weight"]) for p in uniq)),
        "primary_periods": ",".join(ss(p["period"]) for p in uniq),
        "primary_kinds": ",".join(ss(p["kind"]) for p in uniq),
    }


def dedupe_clusters(clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not clusters:
        return []

    ordered = sorted(clusters, key=lambda c: sf(c["center"]))
    groups: List[List[Dict[str, Any]]] = []

    for c in ordered:
        placed = False
        for g in groups:
            if abs(sf(c["center"]) - sf(g[0]["center"])) / max(sf(g[0]["center"]), 1e-9) <= POINT_TOL:
                g.append(c)
                placed = True
                break
        if not placed:
            groups.append([c])

    def cluster_rank(c: Dict[str, Any]) -> Tuple[Any, ...]:
        return (
            int(c["primary_unique"]),
            sf(c["primary_weight"]),
            len(c["bodytop_candidates"]),
            sf(c["center"]),
        )

    return [max(g, key=cluster_rank) for g in groups]


def count_cut_entities(k: pd.DataFrame, line: float) -> int:
    cuts = 0

    for _, r in k.iterrows():
        body_top = sf(r["body_top"])
        body_bottom = sf(r["body_bottom"])

        inside_body = body_bottom < line < body_top
        near_top = abs(body_top - line) / max(line, 1e-9) <= BODY_EDGE_TOL

        if inside_body and not near_top:
            cuts += 1

    return cuts


def shadow_reactions(k: pd.DataFrame, line: float, used_bars: set[int], anchor_bar: int) -> List[Dict[str, Any]]:
    """
    外部影线反应：
    - 只看锚点之外的大周期K；
    - 如果 line 落在上影线 body_top~high 之间，说明该价位在影线供应区被打到过；
    - 但它的权重低于 high/body_top/close 离散点；
    - 同一根K如果已经有 high/body_top/close 离散点命中，不重复计数。
    """
    out: List[Dict[str, Any]] = []

    for idx, r in k.iterrows():
        i = int(idx)
        if i == int(anchor_bar) or i in used_bars:
            continue

        body_top = sf(r["body_top"])
        high = sf(r["high"])

        if body_top < line < high:
            out.append({
                "bar_index": i,
                "period": ss(r["period"]),
                "end": ss(r["end"]),
                "kind": "shadow",
                "price": rd(line, 4),
                "weight": SHADOW_REACTION_WEIGHT,
                "is_volume_bodytop": False,
                "is_double_bodytop": False,
                "open": rd(r["open"]),
                "high": rd(r["high"]),
                "low": rd(r["low"]),
                "close": rd(r["close"]),
                "body_top": rd(r["body_top"]),
                "volume": rd(r["volume"]),
                "vol_ratio_prev": rd(r["vol_ratio_prev"]),
            })

    return out


def validate_bodytop_line(
    k: pd.DataFrame,
    points: List[Dict[str, Any]],
    cluster: Dict[str, Any],
    bodytop_point: Dict[str, Any],
    min_primary_unique: int,
) -> Optional[Dict[str, Any]]:
    line = sf(bodytop_point["price"])
    if line <= 0:
        return None

    # 离散主反应点：high/body_top/close。
    primary_raw = points_near(points, line, POINT_TOL)
    primary = choose_best_point_per_bar(primary_raw, line)

    anchor_bar = int(bodytop_point["bar_index"])
    primary_bar_set = {int(p["bar_index"]) for p in primary}
    external_primary = [p for p in primary if int(p["bar_index"]) != anchor_bar]

    # 外部影线反应：允许 line 穿过影线，但低权重，不重复同一根K。
    shadows = shadow_reactions(k, line, primary_bar_set, anchor_bar)

    effective_samples = sorted(primary + shadows, key=lambda x: int(x["bar_index"]))
    effective_bar_set = {int(p["bar_index"]) for p in effective_samples}
    external_effective = [p for p in effective_samples if int(p["bar_index"]) != anchor_bar]

    # 这是 V6 的关键：不是只看离散点，也不是只看影线穿越；
    # 而是要求“锚点实顶 + 外部离散/影线有效反应”共同达到周期门槛。
    if len(effective_bar_set) < min_primary_unique:
        return None

    if len(external_effective) < max(2, min_primary_unique - 1):
        return None

    # 至少要有 2 个 high/body_top/close 离散点，其中一个是锚点实顶。
    # 这样防止“单个实顶 + 一堆长上影内部穿越”乱成线。
    if len(primary_bar_set) < 2:
        return None

    # 候选实顶必须落在离散主簇附近。这里允许略超出 cluster 高低点 0.5%，
    # 因为主簇可能由 high/close 点形成，而最高实顶刚好在簇边缘。
    cluster_low = sf(cluster["low"])
    cluster_high = sf(cluster["high"])
    if not (cluster_low * (1 - POINT_TOL) <= line <= cluster_high * (1 + POINT_TOL)):
        return None

    cut_count = count_cut_entities(k, line)
    if cut_count != 0:
        return None

    volume_bodytop_count = sum(1 for p in primary if bool(p.get("is_volume_bodytop")))
    double_bodytop_count = sum(1 for p in primary if bool(p.get("is_double_bodytop")))

    if volume_bodytop_count >= 2 and double_bodytop_count >= 1:
        grade = "S-Core"
    elif volume_bodytop_count >= 1:
        grade = "A-Core"
    else:
        return None

    effective_weight = sum(sf(p["weight"]) for p in effective_samples)
    shadow_periods = [ss(p["period"]) for p in shadows]

    return {
        "line": rd(line),
        "core_grade": grade,
        "touch_count": len(effective_bar_set),
        "primary_unique_count": len(primary_bar_set),
        "effective_unique_count": len(effective_bar_set),
        "external_primary_count": len(external_primary),
        "external_shadow_count": len(shadows),
        "external_effective_count": len(external_effective),
        "volume_bodytop_count": int(volume_bodytop_count),
        "double_bodytop_count": int(double_bodytop_count),
        "cut_count": int(cut_count),
        "primary_weight": rd(sum(sf(p["weight"]) for p in primary)),
        "effective_weight": rd(effective_weight),
        "cluster_center": rd(cluster["center"]),
        "cluster_low": rd(cluster["low"]),
        "cluster_high": rd(cluster["high"]),
        "cluster_primary_unique": int(cluster["primary_unique"]),
        "cluster_primary_weight": rd(cluster["primary_weight"]),
        "anchor_period": ss(bodytop_point["period"]),
        "anchor_end": ss(bodytop_point["end"]),
        "anchor_kind": ss(bodytop_point["kind"]),
        "anchor_price": rd(bodytop_point["price"]),
        "sample_periods": ",".join(ss(p["period"]) for p in effective_samples),
        "sample_kinds": ",".join(ss(p["kind"]) for p in effective_samples),
        "primary_sample_periods": ",".join(ss(p["period"]) for p in primary),
        "primary_sample_kinds": ",".join(ss(p["kind"]) for p in primary),
        "external_sample_periods": ",".join(ss(p["period"]) for p in external_effective),
        "external_sample_kinds": ",".join(ss(p["kind"]) for p in external_effective),
        "shadow_count": int(len(shadows)),
        "shadow_periods": ",".join(shadow_periods),
        "samples": effective_samples,
    }


def scan_period_core_lines(df: pd.DataFrame, period: str, label: str, min_effective_unique: int, period_rank: int) -> List[Dict[str, Any]]:
    k = aggregate_period(df, period)
    if k.empty:
        return []

    points = build_reaction_points(k)
    if not points:
        return []

    # V7：建簇阶段只要求“离散反应种子”够形成区域，不要求直接满足完整共振门槛；
    # 完整门槛留到 validate_bodytop_line，用“离散点 + 外部影线有效反应”一起验证。
    seed_need = min_seed_discrete_count(min_effective_unique)

    clusters = []
    for p in points:
        c = cluster_from_seed(points, sf(p["price"]), seed_need)
        if c:
            clusters.append(c)

    clusters = dedupe_clusters(clusters)

    lines: List[Dict[str, Any]] = []

    for cluster in clusters:
        # V7：候选实顶不只取 cluster raw points 里的 body_top。
        # 如果某个 body_top 位于主簇容差范围内，也应该参与定线。
        bodytop_candidates: List[Dict[str, Any]] = []
        for p in points:
            if ss(p.get("kind")) != "body_top":
                continue
            price = sf(p.get("price"))
            center = sf(cluster.get("center"))
            if center > 0 and abs(price - center) / center <= POINT_TOL:
                bodytop_candidates.append(p)

        # 保留旧 raw bodytops 作为补充。
        for p in cluster.get("bodytop_candidates", []):
            if all(int(p["bar_index"]) != int(q["bar_index"]) or abs(sf(p["price"]) - sf(q["price"])) > 1e-9 for q in bodytop_candidates):
                bodytop_candidates.append(p)

        valid_candidates: List[Dict[str, Any]] = []
        for bodytop in bodytop_candidates:
            v = validate_bodytop_line(k, points, cluster, bodytop, min_effective_unique)
            if v:
                valid_candidates.append(v)

        if not valid_candidates:
            continue

        # V7：最高实顶是最后的 tie-breaker；
        # 先看外部有效反应是否足够、有效共振是否够多、外部离散和外部影线是否共同支撑。
        def candidate_rank(x: Dict[str, Any]) -> Tuple[Any, ...]:
            return (
                int(x["external_effective_count"]),
                int(x["effective_unique_count"]),
                int(x["external_primary_count"]),
                int(x["external_shadow_count"]),
                int(x["double_bodytop_count"]),
                int(x["volume_bodytop_count"]),
                sf(x["effective_weight"]),
                sf(x["line"]),
            )

        best = max(valid_candidates, key=candidate_rank)
        best.update({
            "period_type": period,
            "period_label": label,
            "period_rank": int(period_rank),
            "core_rank": {"S-Core": 3, "A-Core": 2}.get(ss(best["core_grade"]), 0),
            "min_primary_unique": int(min_effective_unique),
            "seed_discrete_need": int(seed_need),
        })
        lines.append(best)

    return dedupe_lines(lines)


def dedupe_lines(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not lines:
        return []

    ordered = sorted(lines, key=lambda x: sf(x["line"]))
    groups: List[List[Dict[str, Any]]] = []

    for line in ordered:
        placed = False
        for g in groups:
            if abs(sf(line["line"]) - sf(g[0]["line"])) / max(sf(g[0]["line"]), 1e-9) <= POINT_TOL:
                g.append(line)
                placed = True
                break
        if not placed:
            groups.append([line])

    def line_rank(x: Dict[str, Any]) -> Tuple[Any, ...]:
        return (
            int(x.get("core_rank", 0)),
            int(x.get("period_rank", 0)),
            int(x.get("external_effective_count", 0)),
            int(x.get("effective_unique_count", 0)),
            int(x.get("external_primary_count", 0)),
            int(x.get("external_shadow_count", 0)),
            int(x.get("double_bodytop_count", 0)),
            int(x.get("volume_bodytop_count", 0)),
            sf(x.get("effective_weight")),
            sf(x.get("line")),
        )

    return sorted([max(g, key=line_rank) for g in groups], key=lambda x: sf(x["line"]))


def scan_core_lines(df: pd.DataFrame) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    for period, label, min_primary_unique, period_rank in PERIOD_SPECS:
        out.extend(scan_period_core_lines(df, period, label, min_primary_unique, period_rank))

    return dedupe_lines(out)


def select_near_far(lines: List[Dict[str, Any]], current_close: float) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    valid = []
    for x in lines:
        price = sf(x.get("line"))
        if price <= 0:
            continue
        y = dict(x)
        y["distance_pct"] = abs(pct(current_close, price))
        valid.append(y)

    if not valid:
        return None, None

    near = min(valid, key=lambda x: sf(x["distance_pct"]))
    rest = [
        x for x in valid
        if abs(sf(x["line"]) - sf(near["line"])) / max(sf(near["line"]), 1e-9) > POINT_TOL
    ]

    if not rest:
        return near, None

    # 远端也按距离选第二条正式核心线；近端/远端只是实操距离，不改变线本身等级。
    far = min(rest, key=lambda x: sf(x["distance_pct"]))
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
        "upper_shadow_ratio": max(0.0, high - body_top) / rng,
        "entity_above_line_ratio": max(0.0, body_top - max(line, body_bottom)) / max(body, 1e-9),
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

    if bool(f["gap_break"]) and 1.80 <= vr <= 2.50:
        return "S突破", 5
    if 1.80 <= vr <= 2.50:
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
            "score_rank": int(rank),
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

        if (
            int(obj["score_rank"]),
            -sf(obj["distance_pct"]),
            int(obj["idx"]),
        ) > (
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
        bad_long_bear = (
            close < open_
            and pct_chg <= -3
            and breakout_volume > 0
            and volume >= breakout_volume * 0.80
        )

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
            f"{prefix}有效共振": 0,
            f"{prefix}离散共振": 0,
            f"{prefix}外部有效": 0,
            f"{prefix}外部离散": 0,
            f"{prefix}外部影线": 0,
            f"{prefix}放量实顶": 0,
            f"{prefix}倍量实顶": 0,
            f"{prefix}切实体": 0,
            f"{prefix}距现价%": None,
            f"{prefix}锚点": "",
            f"{prefix}样本": "",
            f"{prefix}样本类型": "",
            f"{prefix}簇区间": "",
        }

    return {
        f"{prefix}核心线": rd(x.get("line")),
        f"{prefix}等级": ss(x.get("core_grade")),
        f"{prefix}周期": ss(x.get("period_label")),
        f"{prefix}有效共振": int(x.get("effective_unique_count", x.get("primary_unique_count", 0))),
        f"{prefix}离散共振": int(x.get("primary_unique_count", 0)),
        f"{prefix}外部有效": int(x.get("external_effective_count", 0)),
        f"{prefix}外部离散": int(x.get("external_primary_count", 0)),
        f"{prefix}外部影线": int(x.get("external_shadow_count", 0)),
        f"{prefix}放量实顶": int(x.get("volume_bodytop_count", 0)),
        f"{prefix}倍量实顶": int(x.get("double_bodytop_count", 0)),
        f"{prefix}切实体": int(x.get("cut_count", 0)),
        f"{prefix}距现价%": rd(x.get("distance_pct")),
        f"{prefix}锚点": ss(x.get("anchor_period")),
        f"{prefix}样本": ss(x.get("sample_periods")),
        f"{prefix}样本类型": ss(x.get("sample_kinds")),
        f"{prefix}簇区间": f"{rd(x.get('cluster_low'))}-{rd(x.get('cluster_high'))}",
    }


def final_grade(near: Optional[Dict[str, Any]], breakout: Dict[str, Any], pullback: Dict[str, Any]) -> str:
    if not near:
        return "无核心线"

    core = ss(near.get("core_grade"))
    pb = ss(pullback.get("grade"))
    br = ss(breakout.get("grade"))

    if core == "S-Core" and pb == "S回踩":
        return "S"
    if core in {"S-Core", "A-Core"} and pb in {"S回踩", "A回踩"}:
        return "A"
    if br in {"S突破", "A+突破", "A突破"}:
        return "B｜突破有效，等回踩"
    return "C｜核心线成立，等触发"


def analyze_one(code: str) -> Dict[str, Any]:
    path = find_cache_file(code)
    if not path:
        return {"股票代码": code, "状态": "未找到缓存"}

    df = read_cache(path)
    if df.empty or len(df) < MIN_ROWS:
        return {"股票代码": code, "状态": "缓存无效", "缓存路径": str(path), "行数": len(df)}

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
        f"RUN_MODE={RUN_MODE}",
        f"目标股票={','.join(TARGET_CODES)}",
        "逻辑：实体顶候选；high/body_top/close 先形成离散种子簇；再用外部影线作为低权重有效反应补足共振；先看外部有效反应和总有效共振，最后才看更高实顶。",
        "",
    ]

    for r in rows:
        lines.extend([
            f"{r.get('股票代码')} {r.get('股票中文名称')}｜收盘 {r.get('当前收盘')}｜{r.get('最终等级')}",
            f"近端核心线：{r.get('近端核心线')}｜{r.get('近端等级')}｜{r.get('近端周期')}｜有效共振{r.get('近端有效共振')}｜离散{r.get('近端离散共振')}｜外部有效{r.get('近端外部有效')}｜外部影线{r.get('近端外部影线')}｜放量实顶{r.get('近端放量实顶')}｜倍量实顶{r.get('近端倍量实顶')}｜切实体{r.get('近端切实体')}｜距现价{r.get('近端距现价%')}%",
            f"远端核心线：{r.get('远端核心线')}｜{r.get('远端等级')}｜{r.get('远端周期')}｜有效共振{r.get('远端有效共振')}｜离散{r.get('远端离散共振')}｜外部有效{r.get('远端外部有效')}｜外部影线{r.get('远端外部影线')}｜放量实顶{r.get('远端放量实顶')}｜倍量实顶{r.get('远端倍量实顶')}｜切实体{r.get('远端切实体')}｜距现价{r.get('远端距现价%')}%",
            f"近端定线：锚点{r.get('近端锚点')}｜主簇{r.get('近端簇区间')}｜样本{r.get('近端样本')}｜类型{r.get('近端样本类型')}",
            f"远端定线：锚点{r.get('远端锚点')}｜主簇{r.get('远端簇区间')}｜样本{r.get('远端样本')}｜类型{r.get('远端样本类型')}",
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

    flat_rows = [{k: v for k, v in r.items() if not isinstance(v, (dict, list))} for r in rows]
    pd.DataFrame(flat_rows).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    payload = {
        "boot": BOOT,
        "run_mode": RUN_MODE,
        "target": TARGET,
        "target_dash": TARGET_DASH,
        "target_codes": TARGET_CODES,
        "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "point_tol": POINT_TOL,
            "body_edge_tol": BODY_EDGE_TOL,
            "shadow_reaction_weight": SHADOW_REACTION_WEIGHT,
            "period_specs": PERIOD_SPECS,
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
        "cache_dirs": [str(x) for x in CACHE_DIRS],
    }
    SELF_CHECK_JSON.write_text(json.dumps(self_check, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram(text: str) -> None:
    if not SEND_TELEGRAM or not BOT or not CHAT or requests is None:
        log(f"Telegram跳过 enable={SEND_TELEGRAM} token={bool(BOT)} chat={bool(CHAT)} requests={requests is not None}")
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
