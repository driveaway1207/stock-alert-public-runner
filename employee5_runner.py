# -*- coding: utf-8 -*-
from __future__ import annotations

"""五号员工：大涨/涨停归因学习引擎。V63

本版只优化核心线量化和报告精简：
1. 最大共振优先，普通触碰等权，一根K最多贡献1个普通共振点。
2. 放量/倍量共振额外加权，但不夸大；实体切断/实体接受按同一套实体损耗扣分。
3. 切实体规则保持简单：线进入实体内部就是切实体；唯一例外是实顶距线<=0.5%，算实顶贴线，不算切实体。
4. 核心线评分必须给出精确计数：普通共振、放量加权、切实体、实体接受、实体损耗、净分。
5. Markdown 报告只保留精简结论和候选线分数，不输出三问答、实体接受K列表、关键共振K列表等冗余内容。
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


BOOT = "EMPLOYEE5_PUBLIC_BOOT_20260530_CORE_LINE_V63_EXACT_SCORE_COMPACT_REPORT"
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
LOW_VOLUME_WINDOWS = {20: 12, 60: 6, 250: 3}

ALLOW_BAOSTOCK_FALLBACK = os.getenv("EMPLOYEE5_ALLOW_BAOSTOCK_FALLBACK", "1") != "0"
BAOSTOCK_LIMIT = int(os.getenv("EMPLOYEE5_BAOSTOCK_FALLBACK_LIMIT", "0"))

BOT = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

CACHE_DIRS = [
    ROOT / "kline_cache",
    ROOT / "employee5_kline_cache",
    ROOT / "data" / "kline_cache",
    ROOT / "cache" / "kline_cache",
    ROOT.parent / "kline_cache",
]
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


def valid_code(c: str) -> bool:
    return bool(c) and c.startswith((
        "000", "001", "002", "003", "300", "301",
        "600", "601", "603", "605", "688", "689",
        "920", "8", "4"
    ))


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

    mp = {
        "日期": "date", "交易日期": "date", "date": "date", "time": "date",
        "代码": "code", "code": "code",
        "开盘": "open", "open": "open", "开盘价": "open",
        "收盘": "close", "close": "close", "收盘价": "close",
        "最高": "high", "high": "high", "最高价": "high",
        "最低": "low", "low": "low", "最低价": "low",
        "成交量": "volume", "volume": "volume", "vol": "volume",
        "成交额": "amount", "amount": "amount",
        "涨跌幅": "pct_chg", "pctChg": "pct_chg", "pct_chg": "pct_chg", "涨幅": "pct_chg",
    }

    d = df.rename(columns={c: mp.get(str(c), mp.get(str(c).lower(), c)) for c in df.columns}).copy()
    if not {"date", "open", "high", "low", "close"}.issubset(d.columns):
        return pd.DataFrame()

    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if col in d.columns:
            d[col] = d[col].map(sf)

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


def read_cache_file(p: Path) -> pd.DataFrame:
    try:
        suf = p.suffix.lower()
        if suf == ".json":
            return normalize_hist(pd.DataFrame(rows_from_obj(json.loads(p.read_text(encoding="utf-8")))))
        if suf in [".csv", ".txt"]:
            return normalize_hist(pd.read_csv(p))
        if suf in [".pkl", ".pickle"]:
            return normalize_hist(pd.read_pickle(p))
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame()


def iter_cache_files() -> List[Path]:
    out, seen_dirs = [], set()

    for d in CACHE_DIRS:
        try:
            key = str(d.resolve())
        except Exception:
            key = str(d)

        if key in seen_dirs or not d.exists():
            continue

        seen_dirs.add(key)
        for pat in ["*.json", "*.csv", "*.txt", "*.pkl", "*.pickle"]:
            out.extend(d.rglob(pat))

    uniq, seen = [], set()
    for f in out:
        try:
            key = str(f.resolve())
        except Exception:
            key = str(f)

        if key not in seen:
            seen.add(key)
            uniq.append(f)

    return uniq


def load_cache() -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    files = iter_cache_files()
    hist: Dict[str, pd.DataFrame] = {}

    stat = {
        "source": "cache",
        "cache_files": len(files),
        "cache_hit": 0,
        "cache_bad": 0,
        "cache_short": 0,
        "target_date": TARGET,
        "cache_dirs": [str(x) for x in CACHE_DIRS],
        "scan_keep_rows": SCAN_KEEP_ROWS,
        "memsafe_scan": True,
    }

    for i, p in enumerate(files, 1):
        c = code_of(p)
        if not valid_code(c):
            continue

        df = read_cache_file(p)
        if df.empty:
            stat["cache_bad"] += 1
            continue

        if len(df) < MIN_ROWS or df.iloc[-1]["date"].replace("-", "") < TARGET:
            stat["cache_short"] += 1
            continue

        CACHE_FILE_MAP[c] = p
        hist[c] = df.tail(max(30, SCAN_KEEP_ROWS)).reset_index(drop=True)
        stat["cache_hit"] += 1

        if i % 500 == 0:
            print(f"cache scan {i}/{len(files)} hit={stat['cache_hit']}", flush=True)

    return hist, stat


def load_full_history(code: str, fallback: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    c = code_of(code)
    p = CACHE_FILE_MAP.get(c)

    if p is not None and Path(p).exists():
        df = read_cache_file(Path(p))
        if not df.empty:
            return df

    return fallback if fallback is not None else pd.DataFrame()


def save_cache_file(code: str, df: pd.DataFrame) -> None:
    try:
        MAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (MAIN_CACHE_DIR / f"{code}.json").write_text(
            json.dumps({"target_date": TARGET, "rows": df.to_dict("records")}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"cache save failed {code}: {exc}", flush=True)


def baostock_all_codes() -> List[Tuple[str, str]]:
    if bs is None:
        print("baostock package missing", flush=True)
        return []

    lg = bs.login()
    print(f"baostock login: {getattr(lg, 'error_code', '')} {getattr(lg, 'error_msg', '')}", flush=True)

    rs = bs.query_all_stock(day=TARGET_DASH)
    out: List[Tuple[str, str]] = []

    while rs.error_code == "0" and rs.next():
        row = rs.get_row_data()
        c = code_of(row[0] if row else "")
        name = row[1] if len(row) > 1 else ""
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

    target_dt = datetime.strptime(TARGET_DASH, "%Y-%m-%d")
    start = (target_dt - timedelta(days=140)).strftime("%Y-%m-%d")
    fields = "date,code,open,high,low,close,volume,amount,pctChg"

    try:
        rs = bs.query_history_k_data_plus(
            bs_code(code),
            fields,
            start_date=start,
            end_date=TARGET_DASH,
            frequency="d",
            adjustflag="3",
        )

        data = []
        while rs.error_code == "0" and rs.next():
            data.append(rs.get_row_data())

        return normalize_hist(pd.DataFrame(data, columns=fields.split(","))) if data else pd.DataFrame()
    except Exception as exc:
        print(f"baostock hist failed {code}: {exc}", flush=True)
        return pd.DataFrame()


def build_baostock_cache() -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    stat = {
        "source": "baostock_fallback",
        "cache_files": 0,
        "cache_hit": 0,
        "cache_bad": 0,
        "cache_short": 0,
        "target_date": TARGET,
        "baostock_used": True,
    }
    hist: Dict[str, pd.DataFrame] = {}

    if not ALLOW_BAOSTOCK_FALLBACK:
        stat["baostock_disabled"] = True
        return hist, stat

    codes = baostock_all_codes()
    codes = codes[:BAOSTOCK_LIMIT] if BAOSTOCK_LIMIT > 0 else codes
    stat["baostock_universe"] = len(codes)
    start_time = time.time()

    for i, (code, _) in enumerate(codes, 1):
        df = baostock_fetch_hist(code)
        if df.empty:
            stat["cache_bad"] += 1
            continue

        if len(df) < MIN_ROWS or df.iloc[-1]["date"].replace("-", "") < TARGET:
            stat["cache_short"] += 1
            continue

        hist[code] = df.tail(max(30, SCAN_KEEP_ROWS)).reset_index(drop=True)
        stat["cache_hit"] += 1
        save_cache_file(code, df)

        if i == 1 or i % 200 == 0 or i == len(codes):
            speed = i / max(time.time() - start_time, 0.001)
            print(f"baostock fallback {i}/{len(codes)} hit={stat['cache_hit']} speed={speed:.2f}/s current={code}", flush=True)

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

    return {
        "gain_20d": rd(g),
        "start_date": a.date,
        "end_date": b.date,
        "start_close": rd(a.close),
        "end_close": rd(b.close),
    }


def pick_samples(hist: Dict[str, pd.DataFrame]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    a_rows, b_rows = [], []

    for i, (code, df) in enumerate(hist.items(), 1):
        if df is None or df.empty:
            continue

        last = df.iloc[-1]
        pct = sf(last.pct_chg)
        lp = limit_pct(code)

        if pct >= lp - 0.35 or pct >= min(8.0, lp * 0.75):
            a_rows.append({
                "code": code,
                "name": code,
                "date": last.date,
                "close": rd(last.close),
                "pct_chg": rd(pct),
                "sample_type": "涨停/近涨停" if pct >= lp - 0.35 else "极强上涨",
            })

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
        bars.append({
            "start": g.iloc[0].date,
            "end": g.iloc[-1].date,
            "open": sf(g.iloc[0].open),
            "high": sf(g.high.max()),
            "low": sf(g.low.min()),
            "close": sf(g.iloc[-1].close),
            "volume": sf(g.volume.sum()),
        })

    k = pd.DataFrame(bars).sort_values("end").reset_index(drop=True)
    if k.empty:
        return k

    rng = (k.high - k.low).replace(0, pd.NA)
    k["body_top"] = k[["open", "close"]].max(axis=1)
    k["body_bottom"] = k[["open", "close"]].min(axis=1)
    k["upper_shadow_ratio"] = ((k.high - k.body_top) / rng).fillna(0.0)

    vol_ma = k.volume.rolling(4, min_periods=1).mean().shift(1)
    k["rel_vol"] = (k.volume / vol_ma.replace(0, pd.NA)).fillna(1.0)
    k["vol_rank_pct"] = k.volume.rolling(8, min_periods=1).rank(pct=True).fillna(0.5)

    return k


def latest_bar_snapshot(k: pd.DataFrame) -> Dict[str, Any]:
    if k is None or k.empty:
        return {}

    r = k.iloc[-1]
    return {
        "start": ss(r.start),
        "end": ss(r.end),
        "open": rd(r.open),
        "high": rd(r.high),
        "low": rd(r.low),
        "close": rd(r.close),
        "body_top": rd(r.body_top),
        "body_bottom": rd(r.body_bottom),
        "volume": rd(r.volume),
    }


def completed_bars_for_coreline(k: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    if k is None or k.empty:
        return pd.DataFrame(), {}

    excluded = latest_bar_snapshot(k)
    if len(k) <= 1:
        return pd.DataFrame(), excluded

    return k.iloc[:-1].reset_index(drop=True), excluded


def near(a: float, b: float, tol: float = TOL) -> bool:
    a, b = sf(a), sf(b)
    return bool(a > 0 and b > 0 and abs(a - b) / b <= tol)



def low_volume_window_for_timeframe(timeframe: str) -> int:
    if "250" in ss(timeframe):
        return LOW_VOLUME_WINDOWS.get(250, 3)
    if "60" in ss(timeframe):
        return LOW_VOLUME_WINDOWS.get(60, 6)
    return LOW_VOLUME_WINDOWS.get(20, 12)


def line_reaction_clusters(values: List[float], tol: float = TOL) -> List[List[float]]:
    """把候选价位聚成反应簇。簇只是为了减少重复候选；最终不再先追求少切实体。"""
    vals = sorted({rd(x) for x in values if sf(x) > 0})
    clusters: List[List[float]] = []
    current: List[float] = []

    for v in vals:
        if not current:
            current = [v]
            continue

        anchor = current[0]
        if round(abs(v - anchor) / anchor, 6) <= round(tol, 6):
            current.append(v)
        else:
            clusters.append(current)
            current = [v]

    if current:
        clusters.append(current)

    return clusters


# 兼容旧函数名；V62 已经不再只聚 high，而是聚所有候选反应价。
def high_resonance_clusters(highs: List[float], tol: float = TOL) -> List[List[float]]:
    return line_reaction_clusters(highs, tol)


def volume_event_bonus(r: Any) -> float:
    """放量/倍量共振加权。普通价格触碰一律等权，只有明显量能事件才额外加分。"""
    rv = sf(r.get("rel_vol", 1.0), 1.0) if hasattr(r, "get") else 1.0
    vr = sf(r.get("vol_rank_pct", 0.5), 0.5) if hasattr(r, "get") else 0.5

    # 权重刻意不设过大，避免量能单项压过最大共振主逻辑。
    if rv >= 3.0:
        return 1.5
    if rv >= 2.0:
        return 1.2
    if rv >= 1.8:
        return 0.8
    # 轻微高量只给辅助分，不能当作倍量证据。
    if rv >= 1.3 and vr >= 0.90:
        return 0.3
    return 0.0


def entity_loss_weight(r: Any) -> float:
    """实体切断和实体接受同权扣实体损耗；放量实体损耗加权，但不一票否决。"""
    rv = sf(r.get("rel_vol", 1.0), 1.0) if hasattr(r, "get") else 1.0

    # 普通实体损耗低于普通共振点，确保“最大共振第一”；放量/倍量实体损耗适度加权。
    if rv >= 3.0:
        return 1.5
    if rv >= 2.0:
        return 1.2
    if rv >= 1.8:
        return 1.0
    return 0.6


def candidate_lines_from_bars(k: pd.DataFrame) -> List[Dict[str, Any]]:
    """候选线来源：high + 实体顶 + 收盘；其中 high 尤其用于捕捉“最低有效最高点”。"""
    out: List[Dict[str, Any]] = []
    if k is None or k.empty:
        return out

    for idx, r in k.iterrows():
        tag = f"{r.start}~{r.end}"
        rv = sf(r.get("rel_vol", 1.0), 1.0) if hasattr(r, "get") else 1.0
        bt = sf(r.body_top)
        hi = sf(r.high)
        cl = sf(r.close)

        for source, val in [("high", hi), ("body_top", bt), ("close", cl)]:
            if sf(val) > 0:
                out.append({
                    "line": rd(val),
                    "source": source,
                    "date": tag,
                    "rel_vol": rd(rv, 2),
                    "is_volume_source": volume_event_bonus(r) > 0,
                })

    return out


def candidate_source_summary(candidates: List[Dict[str, Any]], line: float, tol: float = TOL) -> Dict[str, Any]:
    L = sf(line)
    out = {"high": 0, "body_top": 0, "close": 0, "volume_source": 0, "examples": []}
    for x in candidates:
        if near(sf(x.get("line")), L, tol):
            src = ss(x.get("source"))
            if src in out:
                out[src] += 1
            if x.get("is_volume_source"):
                out["volume_source"] += 1
            if len(out["examples"]) < 6:
                out["examples"].append({
                    "source": src,
                    "date": x.get("date"),
                    "line": x.get("line"),
                    "rel_vol": x.get("rel_vol"),
                })
    return out


def score_core_reaction_line(
    k: pd.DataFrame,
    line: float,
    timeframe: str,
    include_downshift: bool = True,
    evidence_limit: int = 80,
) -> Dict[str, Any]:
    """
    V63 精确核心线打分：
    1）普通价格共振等权，一根K最多贡献1个普通共振点；
    2）放量/倍量共振额外加权；
    3）切实体和实体接受统一扣实体损耗分，不做一票否决；
    4）实顶距线<=0.5%视为实顶贴线共振，不算切实体；
    5）输出逐K审计到 JSON，Markdown 报告只输出精简计数和分数。
    """
    L = sf(line)

    ordinary_hit = 0
    close_touch = 0
    body_touch = 0
    high_touch = 0
    upper_hit = 0
    vol_hit = 0
    body_cut = 0
    entity_accept = 0
    volume_body_cut = 0
    volume_entity_accept = 0
    stage_low_volume_high_touch = 0
    stage_low_volume_bar: Dict[str, Any] = {}

    ordinary_score = 0.0
    volume_bonus_score = 0.0
    body_cut_penalty = 0.0
    entity_accept_penalty = 0.0

    hit_dates: List[str] = []
    cut_dates: List[str] = []
    accept_dates: List[str] = []
    evidence: List[Dict[str, Any]] = []
    body_cut_events: List[Dict[str, Any]] = []
    entity_accept_events: List[Dict[str, Any]] = []
    resonance_events: List[Dict[str, Any]] = []

    for _, r in k.iterrows():
        bt, bb, hi, lo, op, cl = sf(r.body_top), sf(r.body_bottom), sf(r.high), sf(r.low), sf(r.open), sf(r.close)
        if L <= 0 or bt <= 0 or bb <= 0 or hi <= 0:
            continue

        tag = f"{r.start}~{r.end}"
        rel_vol = sf(r.get("rel_vol", 1.0), 1.0) if hasattr(r, "get") else 1.0
        vol_rank = sf(r.get("vol_rank_pct", 0.5), 0.5) if hasattr(r, "get") else 0.5
        base_row = {
            "date": tag,
            "open": rd(op),
            "high": rd(hi),
            "low": rd(lo),
            "close": rd(cl),
            "body_top": rd(bt),
            "body_bottom": rd(bb),
            "rel_vol": rd(rel_vol, 3),
            "vol_rank_pct": rd(vol_rank, 3),
        }

        is_body_top_touch = near(bt, L)
        is_close_touch = near(cl, L)
        is_high_touch = near(hi, L)
        # 上影/高点反应：线落在实体顶附近到最高价区间，算普通共振。
        is_upper_hit = bt * (1 - TOL) <= L <= hi * (1 + TOL)
        is_ordinary_hit = bool(is_upper_hit or is_body_top_touch or is_close_touch or is_high_touch)

        # 切实体：线进入实体内部就是切实体；唯一例外是实顶贴线<=0.5%，算共振，不算切实体。
        is_cut = bool((bb < L < bt) and not is_body_top_touch)
        # 实体接受：整根实体在候选线上方超过容差。接受不否定历史核心线，只扣损耗并标记状态。
        is_accept = bool(bb > L * (1 + TOL))

        if is_ordinary_hit:
            ordinary_hit += 1
            ordinary_score += 1.0
            hit_dates.append(tag)

            if is_upper_hit and not is_body_top_touch:
                upper_hit += 1
            if is_body_top_touch:
                body_touch += 1
            if is_close_touch:
                close_touch += 1
            if is_high_touch:
                high_touch += 1

            vb = volume_event_bonus(r)
            if vb > 0:
                vol_hit += 1
                volume_bonus_score += vb

            ev = dict(base_row)
            ev.update({
                "ordinary_hit": True,
                "upper_shadow_hit": bool(is_upper_hit),
                "close_touch": bool(is_close_touch),
                "body_top_touch": bool(is_body_top_touch),
                "high_touch": bool(is_high_touch),
                "body_cut": bool(is_cut),
                "entity_accept": bool(is_accept),
                "volume_bonus": rd(vb, 3),
            })
            resonance_events.append(ev)
            if evidence_limit > 0 and len(evidence) < evidence_limit:
                evidence.append(ev)

        if is_cut:
            body_cut += 1
            w = entity_loss_weight(r)
            body_cut_penalty += w
            cut_dates.append(tag)
            if w > 0.6:
                volume_body_cut += 1
            ev = dict(base_row)
            ev.update({"entity_loss_weight": rd(w, 3), "reason": "body_cut"})
            body_cut_events.append(ev)

        if is_accept:
            entity_accept += 1
            w = entity_loss_weight(r)
            entity_accept_penalty += w
            accept_dates.append(tag)
            if w > 0.6:
                volume_entity_accept += 1
            ev = dict(base_row)
            ev.update({"entity_loss_weight": rd(w, 3), "reason": "entity_accept"})
            entity_accept_events.append(ev)

    low_vol_window = low_volume_window_for_timeframe(timeframe)
    recent_for_low_vol = k.tail(low_vol_window) if low_vol_window > 0 else pd.DataFrame()
    if not recent_for_low_vol.empty and "volume" in recent_for_low_vol.columns:
        try:
            lv = recent_for_low_vol.loc[recent_for_low_vol["volume"].astype(float).idxmin()]
            stage_low_volume_bar = {
                "date": f"{lv.start}~{lv.end}",
                "high": rd(lv.high),
                "volume": rd(lv.volume),
                "window_bars": low_vol_window,
            }
            if near(lv.high, L):
                stage_low_volume_high_touch = 1
                volume_bonus_score += 0.3
        except Exception:
            stage_low_volume_bar = {}

    entity_loss_score = body_cut_penalty + entity_accept_penalty
    gross_score = ordinary_score + volume_bonus_score
    net_score = gross_score - entity_loss_score

    meaningful = ordinary_hit >= MIN_CORE_HIT_COUNT or (ordinary_hit >= 2 and volume_bonus_score >= 1.2)
    core_candidate = meaningful and net_score >= max(2.0, MIN_CORE_HIT_COUNT - 1.0)

    if core_candidate and ordinary_hit >= 5 and net_score >= 5:
        line_type, level = "core_line", "高置信最大共振核心线"
    elif core_candidate:
        line_type, level = "core_line", "最大共振核心线候选"
    elif meaningful:
        line_type, level = "logic_analysis_line", "共振足够但实体损耗偏重的逻辑线"
    elif ordinary_hit >= 2:
        line_type, level = "logic_analysis_line", "普通共振逻辑线"
    else:
        line_type, level = "non_core", "未成线"

    if volume_entity_accept > 0:
        current_state = "已被放量/倍量实体接受"
    elif entity_accept > 0:
        current_state = "已被普通实体接受"
    else:
        current_state = "未被实体整体接受"

    need_escalate = meaningful and not core_candidate
    clean = body_cut == 0 and entity_accept == 0

    downshift_tests: List[Dict[str, Any]] = []
    if include_downshift and L > 0 and not k.empty:
        for pct in [TOL, TOL * 2]:
            shifted = L * (1 - pct)
            s = score_core_reaction_line(k, shifted, timeframe, include_downshift=False, evidence_limit=0)
            downshift_tests.append({
                "shift_pct": rd(pct * 100, 3),
                "line": rd(shifted),
                "ordinary_resonance_count": s.get("ordinary_resonance_count"),
                "volume_bonus_score": s.get("volume_bonus_score"),
                "body_cut_count": s.get("body_cut_count"),
                "entity_accept_count": s.get("entity_accept_count"),
                "entity_loss_score": s.get("entity_loss_score"),
                "gross_score": s.get("gross_score"),
                "net_score": s.get("net_score"),
                "delta_net_vs_current": rd(sf(s.get("net_score")) - net_score, 3),
            })

    exact_score_formula = (
        "net_score = ordinary_resonance_count*1.0 + volume_bonus_score "
        "- body_cut_penalty - entity_accept_penalty"
    )

    return {
        "line": rd(L),
        "core_line": rd(L),
        "line_type": line_type,
        "level": level,
        "timeframe": timeframe,
        "score": rd(net_score, 3),
        "net_score": rd(net_score, 3),
        "gross_score": rd(gross_score, 3),
        "ordinary_resonance_count": ordinary_hit,
        "resonance_score": rd(ordinary_score, 3),
        "volume_bonus_score": rd(volume_bonus_score, 3),
        "hit_count": ordinary_hit,
        "upper_shadow_hit_count": upper_hit,
        "entity_top_as_shadow_count": body_touch,
        "close_touch_count": close_touch,
        "body_top_touch_count": body_touch,
        "high_touch_count": high_touch,
        "volume_hit_count": vol_hit,
        "stage_low_volume_high_touch_count": stage_low_volume_high_touch,
        "stage_low_volume_bar": stage_low_volume_bar,
        "body_cut_count": body_cut,
        "body_cut_penalty": rd(body_cut_penalty, 3),
        "entity_accept_count": entity_accept,
        "entity_accept_penalty": rd(entity_accept_penalty, 3),
        "entity_loss_score": rd(entity_loss_score, 3),
        "volume_body_cut_count": volume_body_cut,
        "volume_entity_accept_count": volume_entity_accept,
        "current_state": current_state,
        "entity_accept_dates": accept_dates[:80],
        "accepted_invalidated": False,
        "meaningful_candidate": meaningful,
        "need_escalate_timeframe": need_escalate,
        "clean_core_line": clean,
        "tol_pct": rd(TOL * 100, 3),
        "hit_dates": hit_dates[:80],
        "cut_dates": cut_dates[:80],
        "downshift_tests": downshift_tests,
        "evidence": evidence[:evidence_limit] if evidence_limit > 0 else [],
        "resonance_events": resonance_events,
        "body_cut_events": body_cut_events,
        "entity_accept_events": entity_accept_events,
        "exact_score_formula": exact_score_formula,
        "upper_extreme_pressure": rd(k.high.max()) if not k.empty else 0.0,
        "core_band_low": rd(L * (1 - TOL)),
        "core_band_high": rd(L * (1 + TOL)),
        "confirmation_source": "v63_exact_max_resonance_net_score",
    }


# 兼容旧函数名；内部已经升级为 V62 最大共振净分逻辑。
def score_shadow_line(k: pd.DataFrame, line: float, timeframe: str) -> Dict[str, Any]:
    return score_core_reaction_line(k, line, timeframe)


def choose_max_resonance_net_boundary(scored: List[Dict[str, Any]]) -> Dict[str, Any]:
    """同一反应簇内：净分优先，共振和放量作强确认，最后才取更低的有效边界。"""
    if not scored:
        return {}

    return sorted(
        scored,
        key=lambda x: (
            sf(x.get("net_score")),
            int(x.get("ordinary_resonance_count", x.get("hit_count", 0))),
            sf(x.get("volume_bonus_score")),
            -sf(x.get("entity_loss_score")),
            -sf(x.get("line")),  # 净分/共振相近时，取更低的有效边界。
        ),
        reverse=True,
    )[0]


# 兼容旧函数名，避免其他地方调用失败。
def choose_cleanest_lowest_high(scored: List[Dict[str, Any]]) -> Dict[str, Any]:
    return choose_max_resonance_net_boundary(scored)


def find_shadow_coreline(df: pd.DataFrame, window: int, timeframe: str) -> Dict[str, Any]:
    raw_k = aggregate_bars(df, window)
    k, excluded = completed_bars_for_coreline(raw_k)

    if raw_k.empty or len(raw_k) < 6:
        return {
            "level": "数据不足",
            "line": None,
            "line_type": "none",
            "timeframe": timeframe,
            "text": f"{timeframe}聚合K不足，不能硬画核心线。",
        }

    if k.empty or len(k) < 5:
        return {
            "level": "已排除当前未完成K后数据不足",
            "line": None,
            "line_type": "none",
            "timeframe": timeframe,
            "excluded_current_bar": excluded,
            "text": f"{timeframe}已排除最新未完成聚合K后，历史完成K数量不足，不能硬画核心线。",
        }

    raw_candidates = candidate_lines_from_bars(k)
    raw_values = [sf(x.get("line")) for x in raw_candidates if sf(x.get("line")) > 0]
    clusters = line_reaction_clusters(raw_values, TOL)

    scored: List[Dict[str, Any]] = []
    for cluster_id, cluster in enumerate(clusters, 1):
        cluster_scored = [score_core_reaction_line(k, c, timeframe) for c in cluster]
        selected = choose_max_resonance_net_boundary(cluster_scored)
        if selected:
            selected["cluster_id"] = cluster_id
            selected["cluster_prices"] = [rd(x) for x in cluster]
            selected["cluster_highs"] = [rd(x) for x in cluster]
            selected["cluster_size"] = len(cluster)
            selected["cluster_lowest_price"] = rd(min(cluster))
            selected["cluster_lowest_high"] = rd(min(cluster))
            selected["cluster_selection_rule"] = "最大共振净分优先；普通触碰等权；放量共振加权；切实体/实体接受只扣损耗分；同分取更低有效边界"
            selected["candidate_source_summary"] = candidate_source_summary(raw_candidates, selected.get("line"))
            selected["cluster_all_lines"] = sorted(
                cluster_scored,
                key=lambda x: (sf(x.get("net_score")), int(x.get("ordinary_resonance_count", 0)), sf(x.get("volume_bonus_score")), -sf(x.get("line"))),
                reverse=True,
            )[:8]
            scored.append(selected)

    scored = sorted(
        scored,
        key=lambda x: (
            sf(x.get("net_score")),
            int(x.get("ordinary_resonance_count", x.get("hit_count", 0))),
            sf(x.get("volume_bonus_score")),
            -sf(x.get("entity_loss_score")),
            int(x.get("line_type") == "core_line"),
            -sf(x.get("line")),
        ),
        reverse=True,
    )

    best = scored[0] if scored else None

    if not best:
        return {
            "level": "未识别",
            "line": None,
            "line_type": "none",
            "timeframe": timeframe,
            "excluded_current_bar": excluded,
            "text": f"{timeframe}未识别到有效候选。",
        }

    best["all_candidates"] = scored[:10]
    best["high_cluster_count"] = len(clusters)
    best["reaction_cluster_count"] = len(clusters)
    best["excluded_current_bar"] = excluded
    best["excluded_current_bar_note"] = "最新一根聚合K不参与历史成线，但可在报告中作为当前确认观察。"

    if best.get("line_type") == "core_line":
        best["text"] = (
            f"{timeframe}识别核心线：{best['line']}元。"
            f"已排除最新未完成聚合K（{excluded.get('start')}~{excluded.get('end')}）做历史成线。"
            f"普通共振{best.get('ordinary_resonance_count')}根；"
            f"收盘贴线{best['close_touch_count']}次、实体顶贴线{best['body_top_touch_count']}次、"
            f"最高价贴线{best['high_touch_count']}次、上影区打到{best.get('upper_shadow_hit_count')}次；"
            f"放量/倍量共振{best['volume_hit_count']}次，放量加权{best.get('volume_bonus_score')}；"
            f"切实体{best['body_cut_count']}次、切实体损耗{best.get('body_cut_penalty', 0)}；"
            f"实体接受{best.get('entity_accept_count', 0)}次、接受损耗{best.get('entity_accept_penalty', 0)}；"
            f"净分{best.get('net_score')}，状态：{best.get('current_state')}。"
        )
    elif best.get("meaningful_candidate"):
        best["text"] = (
            f"{timeframe}候选线{best.get('line')}元有真实共振："
            f"普通共振{best.get('ordinary_resonance_count')}根、放量加权{best.get('volume_bonus_score')}、"
            f"实体总损耗{best.get('entity_loss_score')}、净分{best.get('net_score')}。"
            f"当前只能作逻辑线，原因是共振收益扣除实体损耗后不足以确认核心。"
        )
    else:
        best["text"] = (
            f"{timeframe}未形成核心线；最强候选为{best.get('line')}元，"
            f"普通共振{best.get('ordinary_resonance_count')}根，放量加权{best.get('volume_bonus_score')}，"
            f"实体总损耗{best.get('entity_loss_score')}，净分{best.get('net_score')}。"
        )

    return best


def is_clean_core(x: Optional[Dict[str, Any]]) -> bool:
    return bool(
        x
        and x.get("line_type") == "core_line"
        and int(x.get("ordinary_resonance_count", x.get("hit_count", 0))) >= MIN_CORE_HIT_COUNT
        and sf(x.get("net_score", x.get("score", 0))) >= max(2.0, MIN_CORE_HIT_COUNT - 1.0)
    )


def should_escalate_timeframe(x: Optional[Dict[str, Any]]) -> bool:
    return bool(x and x.get("need_escalate_timeframe"))


def attach_path(
    selected: Dict[str, Any],
    monthly: Dict[str, Any],
    seasonal: Optional[Dict[str, Any]],
    yearly: Optional[Dict[str, Any]],
    decision: str,
) -> Dict[str, Any]:
    selected["monthly_candidate"] = monthly
    selected["seasonal_candidate"] = seasonal or {}
    selected["yearly_candidate"] = yearly or {}
    selected["timeframe_decision"] = decision
    return selected


def _rank_fallback_coreline(x: Dict[str, Any], priority: int) -> Tuple[float, int, float, float, int]:
    return (
        sf(x.get("net_score", x.get("score", -9999))),
        int(x.get("ordinary_resonance_count", x.get("hit_count", 0))),
        sf(x.get("volume_bonus_score", 0)),
        -sf(x.get("entity_loss_score", 0)),
        -priority,
    )


def core_line(df: pd.DataFrame) -> Dict[str, Any]:
    if len(df) < 80:
        return {"level": "数据不足", "line": None, "line_type": "none", "text": "历史K线不足，不能硬画核心线。"}

    # V62：20/60/250 独立扫描，不再由20日是否“干净”决定是否能看大周期。
    monthly = find_shadow_coreline(df, 20, "20日聚合K")
    seasonal = find_shadow_coreline(df, 60, "60日聚合K")
    yearly = find_shadow_coreline(df, 250, "250日聚合K")

    if is_clean_core(monthly):
        monthly["text"] = monthly.get("text", "") + " 周期决策：20日近端核心线成立；60日/250日已同步扫描作背景校验。"
        return attach_path(monthly, monthly, seasonal, yearly, "20日成立，采用20日近端核心线；60/250同步保留")

    if is_clean_core(seasonal):
        seasonal["text"] = seasonal.get("text", "") + " 周期决策：20日未确认核心，60日中周期核心线成立。"
        return attach_path(seasonal, monthly, seasonal, yearly, "20日未成核心，60日成立，采用60日")

    if is_clean_core(yearly):
        yearly["text"] = yearly.get("text", "") + " 周期决策：20日/60日未确认核心，250日大周期核心线成立。"
        return attach_path(yearly, monthly, seasonal, yearly, "20日和60日未成核心，250日成立，采用250日")

    fallback_pool = [(monthly, 0), (seasonal, 1), (yearly, 2)]
    selected, pri = sorted(fallback_pool, key=lambda xp: _rank_fallback_coreline(xp[0], xp[1]), reverse=True)[0]
    selected["text"] = selected.get("text", "") + " 周期决策：20/60/250均未确认高置信核心线，暂输出净分最高逻辑线，不硬画。"
    return attach_path(selected, monthly, seasonal, yearly, "三周期均未确认，输出净分最高逻辑线")

def _fmt_score(x: Dict[str, Any]) -> str:
    if not x or x.get("line") is None:
        return "无"
    return (
        f"{x.get('line')}｜{x.get('timeframe')}｜净{ x.get('net_score', x.get('score')) }｜"
        f"毛{ x.get('gross_score', '') }｜共振{x.get('ordinary_resonance_count', x.get('hit_count'))}｜"
        f"放量+{x.get('volume_bonus_score', 0)}｜损耗{x.get('entity_loss_score', 0)}｜"
        f"切{x.get('body_cut_count')}｜受{x.get('entity_accept_count')}｜{x.get('current_state', '')}"
    )


def core_line_summary(cl: Dict[str, Any]) -> str:
    if ss(cl.get("line_type")) == "core_line":
        return f"核心线｜{_fmt_score(cl)}｜{cl.get('level')}"

    if ss(cl.get("line_type")) == "logic_analysis_line":
        return f"逻辑线｜{_fmt_score(cl)}｜{cl.get('level')}"

    return f"未识别核心线｜上方极值压力 {cl.get('upper_extreme_pressure')}｜{cl.get('timeframe_decision', '')}"


def safe_jsonable(obj: Any, seen: Optional[set] = None) -> Any:
    if seen is None:
        seen = set()

    if obj is None or isinstance(obj, (str, int, float, bool)):
        return None if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)) else obj

    oid = id(obj)

    if isinstance(obj, (dict, list, tuple, set)):
        if oid in seen:
            return "<circular_ref_removed>"

        seen.add(oid)

        if isinstance(obj, dict):
            out = {str(k): safe_jsonable(v, seen) for k, v in obj.items()}
        else:
            out = [safe_jsonable(x, seen) for x in list(obj)]

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


def candidate_line_brief(x: Dict[str, Any]) -> str:
    return (
        f"线{x.get('line')}｜{x.get('line_type')}｜净分{x.get('net_score', x.get('score'))}｜"
        f"毛分{x.get('gross_score', '')}｜共振{x.get('ordinary_resonance_count', x.get('hit_count'))}｜"
        f"放量+{x.get('volume_bonus_score', 0)}｜损耗{x.get('entity_loss_score', 0)}｜"
        f"切{x.get('body_cut_count')}｜受{x.get('entity_accept_count')}｜"
        f"放切{x.get('volume_body_cut_count')}｜放受{x.get('volume_entity_accept_count')}｜{x.get('current_state', '')}"
    )


def top_candidate_lines(cl: Dict[str, Any], limit: int = 5) -> List[str]:
    out: List[str] = []
    for x in (cl.get("all_candidates") or [])[:limit]:
        out.append(candidate_line_brief(x))
    return out


def timeframe_brief(cl: Dict[str, Any]) -> str:
    parts = []
    for key, name in [("monthly_candidate", "20日"), ("seasonal_candidate", "60日"), ("yearly_candidate", "250日")]:
        x = cl.get(key) or {}
        if x.get("line") is not None:
            parts.append(f"{name}:{_fmt_score(x)}")
    return "；".join(parts) if parts else "无"


def sample_ref_price(sample: Any) -> float:
    if isinstance(sample, pd.Series):
        sample = sample.to_dict()

    if isinstance(sample, dict):
        for key in ["close", "end_close"]:
            v = sf(sample.get(key))
            if v > 0:
                return v

    return 0.0


def three_question_answer_lines(cl: Dict[str, Any], sample: Any) -> List[str]:
    line = sf(cl.get("line"))
    px = sample_ref_price(sample)
    lt = ss(cl.get("line_type"))

    hit = int(sf(cl.get("ordinary_resonance_count", cl.get("hit_count"))))
    close_n = int(sf(cl.get("close_touch_count")))
    body_n = int(sf(cl.get("body_top_touch_count")))
    high_n = int(sf(cl.get("high_touch_count")))
    vol_n = int(sf(cl.get("volume_hit_count")))
    cut_n = int(sf(cl.get("body_cut_count")))
    accept_n = int(sf(cl.get("entity_accept_count")))
    net_score = rd(cl.get("net_score", cl.get("score")))
    gross = rd(cl.get("gross_score"))
    entity_loss = rd(cl.get("entity_loss_score"))
    volume_bonus = rd(cl.get("volume_bonus_score"))
    state = ss(cl.get("current_state"))

    if lt == "core_line":
        a1 = (
            f"答：这条线按最大共振净分逻辑成立。普通共振{hit}根，"
            f"收盘/实体顶/最高价/上影触碰均等权统计，其中收盘贴线{close_n}次、实体顶贴线{body_n}次、最高价贴线{high_n}次；"
            f"放量/倍量共振{vol_n}次，放量加权{volume_bonus}；"
            f"切实体{cut_n}次、实体接受{accept_n}次，实体总损耗{entity_loss}；"
            f"毛分{gross}、净分{net_score}。状态：{state}。周期决策：{cl.get('timeframe_decision', '')}。"
        )
    elif lt == "logic_analysis_line":
        a1 = (
            f"答：这条线有共振但暂未确认核心。普通共振{hit}根、放量加权{volume_bonus}、"
            f"切实体{cut_n}次、实体接受{accept_n}次、实体总损耗{entity_loss}，净分{net_score}。"
            f"说明共振收益扣掉实体损耗后还不够硬。周期决策：{cl.get('timeframe_decision', '')}。"
        )
    else:
        a1 = "答：当前没有识别到合格核心线，不能硬解释。"

    if line > 0 and px > 0:
        diff = (px / line - 1.0) * 100.0

        if abs(diff) <= TOL * 100:
            a2 = f"答：样本价{rd(px)}元贴近核心线{rd(line)}元，正处在核心线反应区。"
        elif diff > 0:
            a2 = f"答：样本价{rd(px)}元高于核心线{rd(line)}元约{rd(diff)}%，后续重点看是否回踩线附近不破、是否形成支撑转换。"
        else:
            a2 = f"答：样本价{rd(px)}元低于核心线{rd(line)}元约{rd(abs(diff))}%，该线暂作上方突破确认位。"
    else:
        a2 = "答：样本价格或核心线缺失，暂时不能判断发动点与核心线的直接关系。"

    if lt == "core_line" and hit >= MIN_CORE_HIT_COUNT:
        a3 = "答：可以沉淀为一号员工结构因子：最大共振核心线被日线高质量突破、接受或回踩不破时，才进入正式候选评分。"
    else:
        a3 = "答：暂时只适合五号员工观察，不能直接沉淀为一号员工正式买入因子。"

    return [
        "1. 这条线为什么有效，还是只是极值压力？",
        a1,
        "2. 为什么在这个时间点发动？",
        a2,
        "3. 能否沉淀成一号员工可提前识别的因子？",
        a3,
    ]

def build_report(hist: Dict[str, pd.DataFrame], stat: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    A, B = pick_samples(hist) if hist else (pd.DataFrame(), pd.DataFrame())

    lines = [
        "# 五号员工：核心线量化报告",
        "",
        f"- 日期：{TARGET}",
        f"- 启动指纹：{BOOT}",
        f"- 数据来源：{stat.get('source')}｜命中：{stat.get('cache_hit', 0)} / 文件数 {stat.get('cache_files', 0)}",
        "- 公式：净分 = 普通共振 + 放量加权 - 切实体损耗 - 实体接受损耗",
        "",
    ]

    results = []

    lines += ["## A组：当日涨停/极强样本"]
    if A.empty:
        lines.append("- 无")
    else:
        for i, r in A.iterrows():
            lines.append(f"{i + 1}. {r.code}｜{r.sample_type}｜涨幅{r.pct_chg}%｜收盘{r.close}")

    lines += ["", "## B组：近20日累计涨幅前三"]
    if B.empty:
        lines.append("- 无")
    else:
        for i, r in B.iterrows():
            lines.append(f"{i + 1}. {r.code}｜{r.gain_20d}%｜{r.start_date}→{r.end_date}")

    lines += ["", "## 核心线结果"]

    merged = (
        pd.concat([A.assign(_group="A组"), B.assign(_group="B组")], ignore_index=True)
        if not (A.empty and B.empty)
        else pd.DataFrame()
    )

    if merged.empty:
        lines.append("- 无有效样本。")
    else:
        for _, r in merged.iterrows():
            c = ss(r.get("code"))
            cl = core_line(load_full_history(c, hist.get(c, pd.DataFrame())))
            lines += [
                f"### {c}｜{r.get('_group')}",
                f"- 采用：{core_line_summary(cl)}",
                f"- 三周期：{timeframe_brief(cl)}",
                "- 候选Top：",
            ]
            tops = top_candidate_lines(cl, 5)
            if tops:
                for t in tops:
                    lines.append(f"  - {t}")
            else:
                lines.append("  - 无")
            lines.append("")

            results.append({
                "group": r.get("_group"),
                "code": c,
                "sample": r.to_dict(),
                "core_line": cl,
                "top_candidates": cl.get("all_candidates", [])[:10],
            })

    payload = {
        "target_date": TARGET,
        "boot_id": BOOT,
        "cache_stats": stat,
        "a_pool": A.to_dict("records") if not A.empty else [],
        "b_pool": B.to_dict("records") if not B.empty else [],
        "results": results,
        "research_only": True,
        "core_line_method": "v63_exact_max_resonance_net_score_compact_report",
        "core_line_tol": TOL,
        "exclude_current_agg_bar": True,
        "entity_accept_hard_filter": False,
        "independent_timeframe_scan": True,
        "report_style": "compact_no_three_questions_no_evidence_lists",
        "score_formula": "net_score = ordinary_resonance_count + volume_bonus_score - body_cut_penalty - entity_accept_penalty",
    }

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
            resp = requests.post(
                url,
                json={"chat_id": CHAT, "text": part, "disable_web_page_preview": True},
                timeout=30,
            )
            print(f"telegram chunk {idx} status={getattr(resp, 'status_code', 'NA')} body={getattr(resp, 'text', '')[:160]}", flush=True)
        except Exception as exc:
            print(f"telegram failed chunk {idx}: {exc}", flush=True)

        time.sleep(0.4)


def write_outputs(md: str, payload: Dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe = safe_jsonable(payload)

    feedback = safe_jsonable({
        "boot_id": BOOT,
        "target_date": TARGET,
        "network_hist_allowed": False,
        "data_source": payload.get("cache_stats", {}).get("source"),
        "core_line_method": payload.get("core_line_method"),
        "core_line_tol": TOL,
        "exclude_current_agg_bar": True,
        "entity_accept_hard_filter": False,
        "independent_timeframe_scan": True,
        "report_style": payload.get("report_style", "compact"),
        "score_formula": payload.get("score_formula", ""),
        "scan_keep_rows": SCAN_KEEP_ROWS,
        "memsafe_scan": True,
        "jsonsafe_payload": True,
    })

    files = {
        "limit_up_research_report.md": md,
        "big_rise_story_report.md": md,
        "left_trace_research_report.md": md,
        "limit_up_research_report.json": json.dumps(safe, ensure_ascii=False, indent=2, allow_nan=False),
        "employee5_runtime_feedback.json": json.dumps(feedback, ensure_ascii=False, indent=2, allow_nan=False),
    }

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
