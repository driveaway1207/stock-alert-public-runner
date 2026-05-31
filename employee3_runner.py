# -*- coding: utf-8 -*-
from __future__ import annotations

"""三号员工：核心线突破海选器 V1

用途：
1）读取现有日线前复权缓存；
2）全市场逐票计算20日聚合K核心线；
3）筛选最近20个交易日内日K线从下往上高质量突破核心线的股票；
4）推送只输出：股票代码、股票中文名称、核心线价位、高质量突破日期。

约束：
- 不改 workflow 链路，不写入生产凭证，不运行时替换函数；
- 实体接受不淘汰核心线，只输出状态字段；
- 当前版本只做海选，不做深度评分。
"""

import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
import numpy as np

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    import baostock as bs
except Exception:  # pragma: no cover
    bs = None

BOOT = "EMPLOYEE3_CORE_LINE_BREAKOUT_SCREEN_V1_20260531"
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "employee3_reports"
MAIN_CACHE_DIR = ROOT / "kline_cache"
CACHE_DIRS = [
    MAIN_CACHE_DIR,
    ROOT / "employee5_kline_cache",
    ROOT / "data" / "kline_cache",
    ROOT / "cache" / "kline_cache",
    ROOT.parent / "kline_cache",
]

BOT = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
ENABLE_TELEGRAM = os.getenv("ENABLE_TELEGRAM", "0")

TARGET_RAW_KEYS = [
    "EMPLOYEE3_TARGET_DATE",
    "EMPLOYEE5_TARGET_DATE",
    "SELECTION_TRADE_DATE",
    "DATA_GATE_TARGET_DATE",
    "TARGET_TRADE_DATE",
    "LAST_TRADE_DAY_OVERRIDE",
    "REQUIRED_CACHE_DATE",
]

AGG_WINDOW = int(os.getenv("EMPLOYEE3_AGG_WINDOW", "20"))
BREAKOUT_LOOKBACK_DAYS = int(os.getenv("EMPLOYEE3_BREAKOUT_LOOKBACK_DAYS", "20"))
CORE_LINE_TOL = float(os.getenv("EMPLOYEE3_CORE_LINE_TOL", os.getenv("EMPLOYEE5_CORE_LINE_TOL", "0.01")))
CORE_LINE_BAND_TOL = float(os.getenv("EMPLOYEE3_CORE_LINE_BAND_TOL", "0.015"))
MIN_CACHE_ROWS = int(os.getenv("EMPLOYEE3_MIN_CACHE_ROWS", "80"))
MIN_CORE_RESONANCE = int(os.getenv("EMPLOYEE3_MIN_CORE_RESONANCE", "3"))
CACHE_SCAN_PROGRESS_EVERY = int(os.getenv("EMPLOYEE3_CACHE_SCAN_PROGRESS_EVERY", "500"))
SCREEN_PROGRESS_EVERY = int(os.getenv("EMPLOYEE3_SCREEN_PROGRESS_EVERY", "200"))
MAX_STOCKS = int(os.getenv("MAX_STOCKS", os.getenv("EMPLOYEE3_MAX_STOCKS", "0")))
ALLOW_BAOSTOCK_FALLBACK = os.getenv("EMPLOYEE3_ALLOW_BAOSTOCK_FALLBACK", "0") == "1"
RECENT_REFRESH_DAYS = int(os.getenv("EMPLOYEE3_RECENT_REFRESH_DAYS", "10"))
RECENT_REFRESH_BUDGET_MIN = float(os.getenv("EMPLOYEE3_RECENT_REFRESH_BUDGET_MIN", "35"))
QFQ_ADJUSTFLAG = "2"
PROGRESS_COLOR = os.getenv("EMPLOYEE3_PROGRESS_COLOR", "0") == "1" and not os.getenv("NO_COLOR")
PROGRESS_WIDTH = int(os.getenv("EMPLOYEE3_PROGRESS_WIDTH", "34"))
PROGRESS_DIAG_CODE_RAW = os.getenv("EMPLOYEE3_DIAG_CODE", "")
_m_diag_code = re.search(r"(\d{6})", str(PROGRESS_DIAG_CODE_RAW))
PROGRESS_DIAG_CODE = _m_diag_code.group(1) if _m_diag_code else ""
PROGRESS_DIAG = os.getenv("EMPLOYEE3_DIAG", "0") == "1" and bool(PROGRESS_DIAG_CODE)

# 高质量突破阈值：全部是日K可计算字段。
BREAK_PREV_BELOW_PCT = float(os.getenv("EMPLOYEE3_BREAK_PREV_BELOW_PCT", "0.005"))
BREAK_CLOSE_ABOVE_PCT = float(os.getenv("EMPLOYEE3_BREAK_CLOSE_ABOVE_PCT", "0.003"))
BREAK_CLOSE_POS_MIN = float(os.getenv("EMPLOYEE3_BREAK_CLOSE_POS_MIN", "0.65"))
BREAK_UPPER_SHADOW_MAX = float(os.getenv("EMPLOYEE3_BREAK_UPPER_SHADOW_MAX", "0.35"))
BREAK_BODY_RATIO_MIN = float(os.getenv("EMPLOYEE3_BREAK_BODY_RATIO_MIN", "0.25"))
BREAK_MIN_BODY_PCT = float(os.getenv("EMPLOYEE3_BREAK_MIN_BODY_PCT", "0.005"))
BREAK_MIN_PCT_CHG = float(os.getenv("EMPLOYEE3_BREAK_MIN_PCT_CHG", "1.0"))

OUTPUT_MD = REPORT_DIR / "core_line_breakout_screen.md"
OUTPUT_CSV = REPORT_DIR / "core_line_breakout_screen.csv"
OUTPUT_JSON = REPORT_DIR / "core_line_breakout_screen.json"
SELF_CHECK_JSON = REPORT_DIR / "employee3_self_check.json"


def now_bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def prev_workday(d: datetime) -> datetime:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def target_raw() -> str:
    for key in TARGET_RAW_KEYS:
        value = os.getenv(key)
        if value:
            return value
    now = now_bj()
    if now.weekday() >= 5 or now.hour < 20 or (now.hour == 20 and now.minute < 35):
        now = prev_workday(now - timedelta(days=1))
    return now.strftime("%Y%m%d")


TARGET = re.sub(r"\D", "", target_raw())[:8]
TARGET_DASH = f"{TARGET[:4]}-{TARGET[4:6]}-{TARGET[6:8]}" if len(TARGET) == 8 else ""


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        return float(str(x).replace("%", "").replace(",", ""))
    except Exception:
        return default


def rd(x: Any, n: int = 3) -> float:
    v = sf(x)
    return 0.0 if math.isnan(v) or math.isinf(v) else round(v, n)


def norm_date(x: Any) -> str:
    s = re.sub(r"\D", "", ss(x)[:10])
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else ""


def code_of(x: Any) -> str:
    s = x.stem if isinstance(x, Path) else ss(x)
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else ""


def valid_code(code: str) -> bool:
    c = code_of(code)
    return c.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4"))


def bs_code(code: str) -> str:
    c = code_of(code)
    if c.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh." + c
    if c.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz." + c
    if c.startswith(("920", "8", "4")):
        return "bj." + c
    return c



def fmt_seconds(seconds: float) -> str:
    if seconds <= 0 or math.isnan(seconds) or math.isinf(seconds):
        return "0秒"
    if seconds < 60:
        return f"{seconds:.1f}秒"
    if seconds < 3600:
        return f"{seconds / 60:.1f}分钟"
    return f"{seconds / 3600:.1f}小时"


def stage_label(stage: str) -> str:
    if stage == "cache":
        return "阶段一：缓存读取"
    if stage == "refresh":
        return "阶段二：BaoStock补拉"
    if stage == "screen":
        return "阶段三：核心线海选"
    return stage


def stage_icon(stage: str) -> str:
    if stage == "cache":
        return "📁"
    if stage == "refresh":
        return "☁️"
    if stage == "screen":
        return "🚀"
    return "🟣"


def parse_progress_extra(extra: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in ss(extra).split():
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def purple(text: str) -> str:
    # GitHub Actions 对 ANSI 色彩支持不稳定；这里不用 ANSI，直接用紫色符号保证可见。
    return text


def stage_skin(stage: str) -> Dict[str, str]:
    if stage == "cache":
        return {"fill": "🔷", "empty": "▫️", "pulse": "🔹", "title": "🔷 缓存读取雷达"}
    if stage == "refresh":
        return {"fill": "🟧", "empty": "▫️", "pulse": "🟠", "title": "🟧 BaoStock补拉"}
    if stage == "screen":
        return {"fill": "🟪", "empty": "▫️", "pulse": "🟣", "title": "🟪 核心线海选引擎"}
    return {"fill": "🟣", "empty": "▫️", "pulse": "🟣", "title": stage_label(stage)}


def progress_bar(pct: float, width: int = PROGRESS_WIDTH, fill: str = "🟪", empty: str = "▫️") -> str:
    width = max(16, min(width, 42))
    filled = int(round(width * max(0.0, min(100.0, pct)) / 100.0))
    filled = max(0, min(width, filled))
    return fill * filled + empty * (width - filled)


def mini_bar(pct: float, width: int = 12, pulse: str = "🟣") -> str:
    filled = int(round(width * max(0.0, min(100.0, pct)) / 100.0))
    return pulse * filled + "·" * (width - filled)


def progress(stage: str, done: int, total: int, start: float, extra: str = "") -> None:
    if total <= 0:
        return
    elapsed = time.time() - start
    speed = done / elapsed if elapsed > 0 and done > 0 else 0.0
    eta = (total - done) / speed if speed > 0 else 0.0
    pct = min(max(done / total, 0.0), 1.0) * 100.0
    info = parse_progress_extra(extra)
    skin = stage_skin(stage)

    current = info.get("current", "") or "-"
    hit = info.get("hit", "0")
    bad = info.get("bad", "0")
    short = info.get("short", "0")
    saved = info.get("saved", "0")
    failed = info.get("failed", "0")

    bar = progress_bar(pct, fill=skin["fill"], empty=skin["empty"])
    pulse = mini_bar(pct, pulse=skin["pulse"])
    title = skin["title"]

    lines = [
        "╭" + "─" * 72 + "╮",
        f"│ 🚀 三号员工 · 核心线突破海选 V1  ｜ {title} ｜ {pct:6.2f}%".ljust(73) + "│",
        f"│ {bar}".ljust(73) + "│",
        f"│ 进度脉冲：{pulse}".ljust(73) + "│",
        "├" + "─" * 72 + "┤",
        f"│ 📊 已处理：{done:,}/{total:,}只  ｜ ⚡处理速度：{speed:.2f}只/秒  ｜ ⏱已用：{fmt_seconds(elapsed)}".ljust(73) + "│",
        f"│ ⌛剩余：{fmt_seconds(eta)}  ｜ 🎯当前股票：{current}".ljust(73) + "│",
    ]

    if stage == "cache":
        lines.append(f"│ 💾命中缓存：{hit}  ｜ 🧯坏文件：{bad}  ｜ 📉数据过短：{short}".ljust(73) + "│")
    elif stage == "refresh":
        lines.append(f"│ ☁️已保存：{saved}  ｜ ⚠️失败：{failed}".ljust(73) + "│")
    elif stage == "screen":
        lines.append(f"│ 🎯命中股票：{hit}  ｜ 🔍状态：正在扫描核心线突破候选".ljust(73) + "│")
    elif extra:
        lines.append(f"│ 📝备注：{extra}".ljust(73) + "│")

    lines.append("╰" + "─" * 72 + "╯")
    print("\n".join(lines), flush=True)

def normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    mp = {
        "日期": "date", "交易日期": "date", "date": "date", "time": "date",
        "代码": "code", "code": "code", "symbol": "code",
        "名称": "name", "股票名称": "name", "name": "name",
        "开盘": "open", "open": "open", "开盘价": "open",
        "最高": "high", "high": "high", "最高价": "high",
        "最低": "low", "low": "low", "最低价": "low",
        "收盘": "close", "close": "close", "收盘价": "close",
        "成交量": "volume", "volume": "volume", "vol": "volume",
        "成交额": "amount", "amount": "amount",
        "涨跌幅": "pct_chg", "pct_chg": "pct_chg", "pctChg": "pct_chg", "涨幅": "pct_chg",
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
    if "name" not in d.columns:
        d["name"] = ""
    if "code" not in d.columns:
        d["code"] = ""
    d["date"] = d["date"].map(norm_date)
    d["code"] = d["code"].map(code_of)
    d["name"] = d["name"].map(ss)
    d = d[(d.date != "") & (d.open > 0) & (d.high > 0) & (d.low > 0) & (d.close > 0)]
    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if TARGET_DASH:
        d = d[d.date <= TARGET_DASH].reset_index(drop=True)
    if "pct_chg" not in d.columns or d["pct_chg"].abs().sum() == 0:
        prev = d.close.shift(1)
        d["pct_chg"] = (d.close / prev - 1.0) * 100.0
        d.loc[prev <= 0, "pct_chg"] = 0.0
    return d


def read_cache_file(path: Path) -> pd.DataFrame:
    try:
        return normalize_hist(pd.read_csv(path))
    except Exception:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            rows = obj.get("rows") or obj.get("data") or obj.get("klines") or []
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
            code = code_of(p)
            if valid_code(code) and code not in seen:
                seen[code] = p
    files = list(seen.values())
    if MAX_STOCKS > 0:
        files = files[:MAX_STOCKS]
    return files


def load_name_map() -> Dict[str, str]:
    name_map: Dict[str, str] = {}
    candidates = [
        ROOT / "outputs",
        ROOT,
        ROOT.parent / "outputs",
    ]
    for base in candidates:
        if not base.exists():
            continue
        for p in sorted(base.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
            if not any(key in p.name for key in ["universe", "stock", "股票", "usable"]):
                continue
            try:
                df = pd.read_csv(p, dtype=str)
            except Exception:
                continue
            code_col = next((c for c in ["代码", "股票代码", "原始代码", "code", "symbol"] if c in df.columns), None)
            name_col = next((c for c in ["名称", "股票名称", "name", "股票简称"] if c in df.columns), None)
            if not code_col or not name_col:
                continue
            for _, r in df[[code_col, name_col]].dropna(how="all").iterrows():
                code = code_of(r.get(code_col, ""))
                name = ss(r.get(name_col, ""))
                if valid_code(code) and name and code not in name_map:
                    name_map[code] = name
    return name_map


def load_cache() -> Tuple[Dict[str, pd.DataFrame], Dict[str, str], Dict[str, Any]]:
    files = iter_cache_files()
    hist: Dict[str, pd.DataFrame] = {}
    names = load_name_map()
    stat = {"source": "public_kline_cache", "cache_files": len(files), "cache_hit": 0, "bad": 0, "short": 0}
    start = time.time()
    progress("cache", 0, len(files), start, "start")
    for i, p in enumerate(files, 1):
        code = code_of(p)
        df = read_cache_file(p)
        if df.empty:
            stat["bad"] += 1
        elif len(df) < MIN_CACHE_ROWS:
            stat["short"] += 1
        else:
            if code and "code" in df.columns:
                df.loc[df["code"].astype(str).str.len() == 0, "code"] = code
            if code and code not in names and "name" in df.columns:
                non_empty = [ss(x) for x in df["name"].tolist() if ss(x)]
                if non_empty:
                    names[code] = non_empty[-1]
            hist[code] = df
            stat["cache_hit"] += 1
        if i == 1 or i % CACHE_SCAN_PROGRESS_EVERY == 0 or i == len(files):
            progress("cache", i, len(files), start, f"hit={stat['cache_hit']} bad={stat['bad']} short={stat['short']} current={code}")
    return hist, names, stat


def refresh_start_date(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return (datetime.strptime(TARGET_DASH, "%Y-%m-%d") - timedelta(days=RECENT_REFRESH_DAYS)).strftime("%Y-%m-%d")
    last = norm_date(df.iloc[-1].get("date", ""))
    if not last:
        return (datetime.strptime(TARGET_DASH, "%Y-%m-%d") - timedelta(days=RECENT_REFRESH_DAYS)).strftime("%Y-%m-%d")
    return (datetime.strptime(last, "%Y-%m-%d") - timedelta(days=RECENT_REFRESH_DAYS)).strftime("%Y-%m-%d")


def fetch_recent(code: str, existing: pd.DataFrame) -> pd.DataFrame:
    if bs is None or not TARGET_DASH:
        return existing
    rs = bs.query_history_k_data_plus(
        bs_code(code),
        "date,code,open,high,low,close,volume,amount,pctChg",
        start_date=refresh_start_date(existing),
        end_date=TARGET_DASH,
        frequency="d",
        adjustflag=QFQ_ADJUSTFLAG,
    )
    rows: List[List[str]] = []
    while getattr(rs, "error_code", "0") == "0" and rs.next():
        rows.append(rs.get_row_data())
    fresh = normalize_hist(pd.DataFrame(rows, columns=rs.fields if rows else []))
    if fresh.empty:
        return existing
    return normalize_hist(pd.concat([existing, fresh], ignore_index=True))


def save_cache(code: str, df: pd.DataFrame) -> bool:
    d = normalize_hist(df)
    if d.empty or len(d) < MIN_CACHE_ROWS:
        return False
    MAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if "code" not in d.columns or d["code"].astype(str).str.len().sum() == 0:
        d["code"] = code
    cols = [c for c in ["date", "code", "name", "open", "high", "low", "close", "volume", "amount", "pct_chg"] if c in d.columns]
    out = MAIN_CACHE_DIR / f"{code}.csv"
    tmp = out.with_suffix(".csv.tmp")
    d[cols].to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, out)
    return True


def refresh_recent_cache(hist: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    stat = {"source": "recent_refresh", "processed": 0, "saved": 0, "failed": 0, "skipped": 0}
    if not (ALLOW_BAOSTOCK_FALLBACK and hist and bs is not None):
        stat["skipped_reason"] = "disabled_or_no_cache"
        return stat
    lg = bs.login()
    print(f"baostock login: {getattr(lg, 'error_code', '')} {getattr(lg, 'error_msg', '')}", flush=True)
    start = time.time()
    budget = max(60.0, RECENT_REFRESH_BUDGET_MIN * 60.0)
    items = list(hist.items())
    for i, (code, df) in enumerate(items, 1):
        if time.time() - start >= budget:
            stat["stop_reason"] = "time_budget"
            break
        try:
            merged = fetch_recent(code, df)
            if len(merged) >= len(df) and save_cache(code, merged):
                hist[code] = merged
                stat["saved"] += 1
            else:
                stat["failed"] += 1
        except Exception:
            stat["failed"] += 1
        stat["processed"] += 1
        if i == 1 or i % 500 == 0 or i == len(items):
            progress("refresh", i, len(items), start, f"saved={stat['saved']} failed={stat['failed']} current={code}")
    try:
        bs.logout()
    except Exception:
        pass
    return stat


def aggregate_bars(df: pd.DataFrame, window: int = AGG_WINDOW) -> pd.DataFrame:
    d = normalize_hist(df)
    if d.empty or len(d) < max(22, window * 3):
        return pd.DataFrame()
    d = d.reset_index(drop=True)
    d["grp"] = [(len(d) - 1 - i) // window for i in range(len(d))]
    bars: List[Dict[str, Any]] = []
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
    k["body_top"] = k[["open", "close"]].max(axis=1)
    k["body_bottom"] = k[["open", "close"]].min(axis=1)
    k["body_mid"] = (k["body_top"] + k["body_bottom"]) / 2.0
    k["range"] = (k["high"] - k["low"]).replace(0, math.nan)
    k["body_ratio"] = ((k["close"] - k["open"]).abs() / k["range"]).fillna(0.0)
    return k


def near(a: float, b: float, tol: float = CORE_LINE_TOL) -> bool:
    return b > 0 and abs(a - b) / b <= tol


def line_candidate_sources(k: pd.DataFrame) -> Dict[float, str]:
    sources: Dict[float, set] = {}

    def add(price: float, source: str) -> None:
        price = sf(price)
        if price > 0:
            sources.setdefault(rd(price, 3), set()).add(source)

    for _, r in k.iterrows():
        add(r.high, "最高价")
        add(r.body_top, "实体顶")

    prev_volume = k.volume.shift(1) if "volume" in k.columns else pd.Series([0.0] * len(k))
    for idx, r in k.iterrows():
        close = sf(r.close)
        open_ = sf(r.open)
        volume = sf(r.volume)
        pv = sf(prev_volume.iloc[idx] if idx < len(prev_volume) else 0.0)
        if close > open_ and volume > pv and close > 0:
            add(close, "阳线放量收盘价")
    return {line: "+".join(sorted(srcs)) for line, srcs in sources.items()}


def score_line(k: pd.DataFrame, line: float) -> Dict[str, Any]:
    L = sf(line)
    if k.empty or L <= 0:
        return {"line": rd(L), "net_score": 0.0, "effective_resonance_count": 0, "line_type": "non_core"}

    need_cols = ["high", "body_top", "body_bottom", "close", "volume"]
    if not all(c in k.columns for c in need_cols):
        return {"line": rd(L), "net_score": 0.0, "effective_resonance_count": 0, "line_type": "non_core"}

    hi = pd.to_numeric(k["high"], errors="coerce").fillna(0.0)
    bt = pd.to_numeric(k["body_top"], errors="coerce").fillna(0.0)
    bb = pd.to_numeric(k["body_bottom"], errors="coerce").fillna(0.0)
    cl = pd.to_numeric(k["close"], errors="coerce").fillna(0.0)
    vol = pd.to_numeric(k["volume"], errors="coerce").fillna(0.0)

    valid = (hi > 0) & (bt > 0) & (bb > 0)
    if not bool(valid.any()):
        return {"line": rd(L), "net_score": 0.0, "effective_resonance_count": 0, "line_type": "non_core"}

    vol_med = sf(vol[valid].median())
    is_volume_bar = (vol_med > 0) & (vol >= vol_med * 1.30)

    entity_accept = valid & (bb > L)
    entity_cut = valid & (bb < L) & (L < bt)
    normal_zone = valid & ~entity_accept & ~entity_cut

    denom = max(L, 1e-9)
    is_high = normal_zone & (hi.sub(L).abs() / denom <= CORE_LINE_TOL)
    is_upper = normal_zone & (bt <= L) & (L <= hi)
    is_body_top = normal_zone & (bt.sub(L).abs() / denom <= CORE_LINE_TOL)
    is_close = normal_zone & (cl.sub(L).abs() / denom <= CORE_LINE_TOL)
    touch = is_high | is_upper | is_body_top | is_close

    hit = int(touch.sum())
    high_touch = int(is_high.sum())
    upper_hit = int((is_upper & ~is_body_top).sum())
    body_top_touch = int(is_body_top.sum())
    close_touch = int(is_close.sum())
    entity_cut_count = int(entity_cut.sum())
    entity_accept_count = int(entity_accept.sum())
    volume_resonance_count = int((touch & is_volume_bar).sum())
    volume_entity_cut_count = int((entity_cut & is_volume_bar).sum())
    volume_entity_accept_count = int((entity_accept & is_volume_bar).sum())

    net = hit + volume_resonance_count * 0.50 - entity_cut_count * 0.35 - volume_entity_cut_count * 0.75
    level = "核心线候选" if hit >= MIN_CORE_RESONANCE and net > 0 else "未成线"
    return {
        "line": rd(L, 3),
        "score": rd(hit, 3),
        "net_score": rd(net, 3),
        "effective_resonance_count": int(hit),
        "volume_resonance_count": int(volume_resonance_count),
        "high_touch_count": int(high_touch),
        "upper_shadow_hit_count": int(upper_hit),
        "body_top_touch_count": int(body_top_touch),
        "close_touch_count": int(close_touch),
        "entity_cut_count": int(entity_cut_count),
        "volume_entity_cut_count": int(volume_entity_cut_count),
        "entity_accept_count": int(entity_accept_count),
        "volume_entity_accept_count": int(volume_entity_accept_count),
        "level": level,
        "line_type": "core_line" if level == "核心线候选" else "non_core",
        "timeframe": f"{AGG_WINDOW}日聚合K",
        "current_state": "存在实体接受记录" if entity_accept_count else "暂无实体接受记录",
    }



def batch_score_lines(k: pd.DataFrame, sources: Dict[float, str], chunk_size: int = 768) -> List[Dict[str, Any]]:
    """批量计算核心线分数：口径保持 score_line 一致，但用矩阵一次性计算，避免逐线慢循环。"""
    if k.empty or not sources:
        return []
    need_cols = ["high", "body_top", "body_bottom", "close", "volume"]
    if not all(c in k.columns for c in need_cols):
        return []

    hi = pd.to_numeric(k["high"], errors="coerce").fillna(0.0).to_numpy(dtype=float)[:, None]
    bt = pd.to_numeric(k["body_top"], errors="coerce").fillna(0.0).to_numpy(dtype=float)[:, None]
    bb = pd.to_numeric(k["body_bottom"], errors="coerce").fillna(0.0).to_numpy(dtype=float)[:, None]
    cl = pd.to_numeric(k["close"], errors="coerce").fillna(0.0).to_numpy(dtype=float)[:, None]
    vol = pd.to_numeric(k["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)[:, None]

    valid = (hi > 0) & (bt > 0) & (bb > 0)
    if not bool(valid.any()):
        return []
    valid_flat = valid[:, 0]
    vol_flat = vol[:, 0]
    vol_med = sf(np.median(vol_flat[valid_flat])) if bool(valid_flat.any()) else 0.0
    is_volume_bar = (vol_med > 0) & (vol >= vol_med * 1.30)

    sorted_lines = sorted([sf(x) for x in sources.keys() if sf(x) > 0])
    out: List[Dict[str, Any]] = []

    for start_idx in range(0, len(sorted_lines), max(32, chunk_size)):
        part = sorted_lines[start_idx:start_idx + max(32, chunk_size)]
        L = np.array(part, dtype=float)[None, :]
        denom = np.maximum(L, 1e-9)

        entity_accept = valid & (bb > L)
        entity_cut = valid & (bb < L) & (L < bt)
        normal_zone = valid & (~entity_accept) & (~entity_cut)

        is_high = normal_zone & (np.abs(hi - L) / denom <= CORE_LINE_TOL)
        is_upper = normal_zone & (bt <= L) & (L <= hi)
        is_body_top = normal_zone & (np.abs(bt - L) / denom <= CORE_LINE_TOL)
        is_close = normal_zone & (np.abs(cl - L) / denom <= CORE_LINE_TOL)
        touch = is_high | is_upper | is_body_top | is_close

        hit_arr = touch.sum(axis=0).astype(int)
        high_touch_arr = is_high.sum(axis=0).astype(int)
        upper_hit_arr = (is_upper & (~is_body_top)).sum(axis=0).astype(int)
        body_top_touch_arr = is_body_top.sum(axis=0).astype(int)
        close_touch_arr = is_close.sum(axis=0).astype(int)
        entity_cut_arr = entity_cut.sum(axis=0).astype(int)
        entity_accept_arr = entity_accept.sum(axis=0).astype(int)
        volume_res_arr = (touch & is_volume_bar).sum(axis=0).astype(int)
        volume_cut_arr = (entity_cut & is_volume_bar).sum(axis=0).astype(int)
        volume_accept_arr = (entity_accept & is_volume_bar).sum(axis=0).astype(int)
        net_arr = hit_arr + volume_res_arr * 0.50 - entity_cut_arr * 0.35 - volume_cut_arr * 0.75

        for j, line in enumerate(part):
            hit = int(hit_arr[j])
            net = float(net_arr[j])
            level = "核心线候选" if hit >= MIN_CORE_RESONANCE and net > 0 else "未成线"
            out.append({
                "line": rd(line, 3),
                "score": rd(hit, 3),
                "net_score": rd(net, 3),
                "effective_resonance_count": hit,
                "volume_resonance_count": int(volume_res_arr[j]),
                "high_touch_count": int(high_touch_arr[j]),
                "upper_shadow_hit_count": int(upper_hit_arr[j]),
                "body_top_touch_count": int(body_top_touch_arr[j]),
                "close_touch_count": int(close_touch_arr[j]),
                "entity_cut_count": int(entity_cut_arr[j]),
                "volume_entity_cut_count": int(volume_cut_arr[j]),
                "entity_accept_count": int(entity_accept_arr[j]),
                "volume_entity_accept_count": int(volume_accept_arr[j]),
                "level": level,
                "line_type": "core_line" if level == "核心线候选" else "non_core",
                "timeframe": f"{AGG_WINDOW}日聚合K",
                "current_state": "存在实体接受记录" if int(entity_accept_arr[j]) else "暂无实体接受记录",
                "source": sources.get(rd(line, 3), sources.get(line, "")),
            })
    return out

def group_by_band(scored: List[Dict[str, Any]], tol: float = CORE_LINE_BAND_TOL) -> List[List[Dict[str, Any]]]:
    xs = sorted([x for x in scored if sf(x.get("line")) > 0], key=lambda x: sf(x.get("line")))
    groups: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    base = 0.0
    for x in xs:
        L = sf(x.get("line"))
        if not cur or (base > 0 and abs(L - base) / base <= tol):
            cur.append(x)
            base = base or L
        else:
            groups.append(cur)
            cur = [x]
            base = L
    if cur:
        groups.append(cur)
    return groups


def rank_key(x: Dict[str, Any]) -> Tuple[float, int, int, float]:
    return (sf(x.get("net_score")), int(sf(x.get("effective_resonance_count"))), int(sf(x.get("volume_resonance_count"))), -sf(x.get("line")))


def choose_core_line(df: pd.DataFrame) -> Dict[str, Any]:
    raw_k = aggregate_bars(df, AGG_WINDOW)
    if raw_k.empty or len(raw_k) < 3:
        return {"line": None, "level": "数据不足", "reason": "历史K线不足"}
    completed = raw_k.iloc[:-1].reset_index(drop=True)
    if completed.empty:
        return {"line": None, "level": "数据不足", "reason": "无已完成聚合K"}

    sources = line_candidate_sources(completed)
    if not sources:
        return {"line": None, "level": "未识别", "reason": "未识别到候选核心线"}

    # 核心优化：不改变核心线口径，不硬砍候选线。
    # 仍然对所有候选线评分，但用矩阵批量计算；再按价格带合并，选每个价格带最优代表。
    scored_all = batch_score_lines(completed, sources)
    scored = [x for x in scored_all if sf(x.get("net_score")) > 0]
    if not scored:
        return {"line": None, "level": "未识别", "reason": "未识别到有效核心线"}

    band_winners = [max(g, key=rank_key) for g in group_by_band(scored)]
    ranked = sorted(band_winners, key=rank_key, reverse=True)
    best = dict(ranked[0])
    top_candidates = []
    for item in ranked[:5]:
        top_candidates.append({k: v for k, v in item.items() if k not in {"top_candidates", "excluded_current_bar"}})
    best["top_candidates"] = top_candidates
    best["all_candidates_count"] = len(sources)
    best["positive_candidates_count"] = len(scored)
    best["band_candidates_count"] = len(band_winners)
    best["excluded_current_bar"] = {k: (rd(v, 3) if isinstance(v, (int, float)) else v) for k, v in raw_k.iloc[-1].to_dict().items()}
    return best
def daily_breakout_quality(df: pd.DataFrame, line: float) -> Dict[str, Any]:
    d = normalize_hist(df)
    L = sf(line)
    empty = {"hit": False, "date": "", "quality": 0.0, "reason": ""}
    if d.empty or L <= 0 or len(d) < BREAKOUT_LOOKBACK_DAYS + 1:
        empty["reason"] = "日线样本不足或核心线无效"
        return empty
    recent = d.tail(BREAKOUT_LOOKBACK_DAYS + 1).reset_index(drop=True)
    best: Dict[str, Any] = dict(empty)
    for i in range(1, len(recent)):
        prev = recent.iloc[i - 1]
        r = recent.iloc[i]
        prev_close = sf(prev.close)
        open_ = sf(r.open)
        high = sf(r.high)
        low = sf(r.low)
        close = sf(r.close)
        pct_chg = sf(r.pct_chg)
        rng = max(high - low, 1e-9)
        body_abs = abs(close - open_)
        body_ratio = body_abs / rng
        close_pos = (close - low) / rng
        upper_shadow_ratio = (high - max(open_, close)) / rng
        body_pct = body_abs / max(prev_close, 1e-9)
        crossed_from_below = prev_close < L * (1.0 - BREAK_PREV_BELOW_PCT)
        close_confirm = close >= L * (1.0 + BREAK_CLOSE_ABOVE_PCT)
        intraday_not_fake = high >= L and close >= L
        k_quality = (
            close_pos >= BREAK_CLOSE_POS_MIN
            and upper_shadow_ratio <= BREAK_UPPER_SHADOW_MAX
            and body_ratio >= BREAK_BODY_RATIO_MIN
            and body_pct >= BREAK_MIN_BODY_PCT
            and pct_chg >= BREAK_MIN_PCT_CHG
        )
        if crossed_from_below and close_confirm and intraday_not_fake and k_quality:
            quality = 0.0
            quality += 2.0 if close >= L * 1.01 else 1.4
            quality += 1.2 if close_pos >= 0.80 else 0.8
            quality += 1.0 if upper_shadow_ratio <= 0.20 else 0.5
            quality += 0.8 if body_ratio >= 0.45 else 0.4
            quality += 0.6 if close > open_ else 0.2
            quality = rd(quality, 3)
            if quality >= sf(best.get("quality")):
                best = {
                    "hit": True,
                    "date": ss(r.date),
                    "quality": quality,
                    "reason": "close_break_confirmed",
                    "prev_close": rd(prev_close),
                    "open": rd(open_),
                    "high": rd(high),
                    "low": rd(low),
                    "close": rd(close),
                    "pct_chg": rd(pct_chg),
                    "close_pos": rd(close_pos),
                    "upper_shadow_ratio": rd(upper_shadow_ratio),
                    "body_ratio": rd(body_ratio),
                }
    return best


def screen_one_stock(code: str, name: str, df: pd.DataFrame) -> Dict[str, Any]:
    core = choose_core_line(df)
    line = sf(core.get("line")) if core.get("line") is not None else 0.0
    br = daily_breakout_quality(df, line)
    if not br.get("hit"):
        return {}
    return {
        "股票代码": code,
        "股票中文名称": name or code,
        "核心线价位": rd(line, 3),
        "高质量突破日期": br.get("date", ""),
        # 以下字段只进CSV/JSON审计，不进Telegram正文。
        "core_line_score": core.get("net_score", 0),
        "core_line_resonance_count": core.get("effective_resonance_count", 0),
        "core_line_volume_resonance_count": core.get("volume_resonance_count", 0),
        "core_line_entity_cut_count": core.get("entity_cut_count", 0),
        "core_line_entity_accept_count": core.get("entity_accept_count", 0),
        "breakout_quality": br.get("quality", 0),
        "breakout_close": br.get("close", 0),
    }


def screen_all(hist: Dict[str, pd.DataFrame], names: Dict[str, str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    items = list(hist.items())
    start = time.time()
    progress("screen", 0, len(items), start, "start")
    for i, (code, df) in enumerate(items, 1):
        name = names.get(code, "")
        if not name and df is not None and not df.empty and "name" in df.columns:
            vals = [ss(x) for x in df["name"].tolist() if ss(x)]
            name = vals[-1] if vals else code
        try:
            row = screen_one_stock(code, name or code, df)
            if row:
                rows.append(row)
        except Exception as exc:
            print(f"screen failed code={code} err={str(exc)[:120]}", flush=True)
        if i == 1 or i % SCREEN_PROGRESS_EVERY == 0 or i == len(items):
            progress("screen", i, len(items), start, f"hit={len(rows)} current={code}")
    rows = sorted(rows, key=lambda x: (ss(x.get("高质量突破日期")), sf(x.get("breakout_quality")), sf(x.get("core_line_score"))), reverse=True)
    return rows


def build_report(rows: List[Dict[str, Any]], stat: Dict[str, Any]) -> str:
    lines = [
        "# 三号员工：核心线突破海选",
        f"- 运行日期：{TARGET}",
        f"- 使用K线截止日：{TARGET_DASH or '未知'}",
        f"- 缓存命中：{stat.get('cache_hit', 0)} / 文件数 {stat.get('cache_files', 0)}",
        f"- 海选口径：{AGG_WINDOW}日聚合K核心线；最近{BREAKOUT_LOOKBACK_DAYS}个交易日内日K收盘高质量突破核心线",
        "",
    ]
    if not rows:
        lines.append("无符合条件股票。")
        return "\n".join(lines)
    lines.append("| 股票代码 | 股票中文名称 | 核心线价位 | 高质量突破日期 |")
    lines.append("|---|---|---:|---|")
    for r in rows:
        lines.append(f"| {r.get('股票代码','')} | {r.get('股票中文名称','')} | {r.get('核心线价位',0)} | {r.get('高质量突破日期','')} |")
    return "\n".join(lines)


def write_outputs(rows: List[Dict[str, Any]], md: str, stat: Dict[str, Any], self_check: Dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(md, encoding="utf-8")
    pd.DataFrame(rows).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    payload = {
        "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "target": TARGET,
        "target_dash": TARGET_DASH,
        "config": {
            "agg_window": AGG_WINDOW,
            "breakout_lookback_days": BREAKOUT_LOOKBACK_DAYS,
            "core_line_tol": CORE_LINE_TOL,
            "min_core_resonance": MIN_CORE_RESONANCE,
        },
        "stat": stat,
        "self_check": self_check,
        "rows": rows,
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    SELF_CHECK_JSON.write_text(json.dumps(self_check, ensure_ascii=False, indent=2), encoding="utf-8")


def send_report(md: str) -> None:
    print(f"telegram_env_present enable={ENABLE_TELEGRAM} token={bool(BOT)} chat={bool(CHAT)} requests={requests is not None}", flush=True)
    if ENABLE_TELEGRAM != "1" or not BOT or not CHAT or requests is None:
        print("telegram skipped; report preview below:", flush=True)
        print(md[:2400], flush=True)
        return
    url = f"https://api.telegram.org/bot{BOT}/sendMessage"
    chunks = [md[i:i + 3600] for i in range(0, len(md), 3600)] or [md]
    for idx, part in enumerate(chunks, 1):
        try:
            resp = requests.post(url, json={"chat_id": CHAT, "text": part, "disable_web_page_preview": True}, timeout=30)
            print(f"telegram chunk {idx} status={getattr(resp, 'status_code', 'NA')} body={getattr(resp, 'text', '')[:120]}", flush=True)
        except Exception as exc:
            print(f"telegram failed chunk {idx}: {exc}", flush=True)
        time.sleep(0.4)


def synthetic_coreline_df() -> pd.DataFrame:
    rows = []
    start = datetime(2024, 1, 1)
    price = 10.0
    for i in range(140):
        dt = start + timedelta(days=i)
        if dt.weekday() >= 5:
            continue
        if len(rows) < 100:
            open_ = 9.6 + (len(rows) % 7) * 0.03
            close = 9.7 + (len(rows) % 5) * 0.04
            high = 10.0 if len(rows) % 6 in {0, 1, 2} else max(open_, close) + 0.15
            low = min(open_, close) - 0.2
            vol = 1000 + (len(rows) % 9) * 50
        else:
            open_ = price
            close = price * 1.005
            high = max(open_, close) * 1.01
            low = min(open_, close) * 0.99
            vol = 1100
        rows.append({"date": dt.strftime("%Y-%m-%d"), "code": "000001", "name": "测试股", "open": open_, "high": high, "low": low, "close": close, "volume": vol, "amount": vol * close})
        price = close
    d = normalize_hist(pd.DataFrame(rows))
    # 末端制造有效突破：前一日在线下，当日收盘在线上。
    if len(d) >= 2:
        d.loc[d.index[-2], ["open", "high", "low", "close", "volume"]] = [9.72, 9.85, 9.62, 9.78, 1400]
        d.loc[d.index[-1], ["open", "high", "low", "close", "volume"]] = [9.85, 10.35, 9.80, 10.25, 2200]
        prev = d.close.shift(1)
        d["pct_chg"] = (d.close / prev - 1.0) * 100.0
        d.loc[prev <= 0, "pct_chg"] = 0.0
    return d


def run_self_check() -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    ok_all = True

    # 第1遍：核心线实体接受不淘汰、能输出线。
    try:
        df = synthetic_coreline_df()
        core = choose_core_line(df)
        ok = core.get("line") is not None and sf(core.get("effective_resonance_count")) >= MIN_CORE_RESONANCE
        checks.append({"round": 1, "name": "核心线识别与实体接受不淘汰", "ok": bool(ok), "detail": core})
        ok_all = ok_all and bool(ok)
    except Exception as exc:
        checks.append({"round": 1, "name": "核心线识别与实体接受不淘汰", "ok": False, "error": str(exc)})
        ok_all = False

    # 第2遍：最近20日高质量突破必须来自下往上，且以收盘确认。
    try:
        df = synthetic_coreline_df()
        core = choose_core_line(df)
        br = daily_breakout_quality(df, sf(core.get("line")))
        ok = bool(br.get("hit")) and ss(br.get("date")) == ss(df.iloc[-1].date)
        checks.append({"round": 2, "name": "20日内日K高质量突破确认", "ok": bool(ok), "detail": br})
        ok_all = ok_all and bool(ok)
    except Exception as exc:
        checks.append({"round": 2, "name": "20日内日K高质量突破确认", "ok": False, "error": str(exc)})
        ok_all = False

    # 第3遍：推送字段必须只有四列，审计字段只能进CSV/JSON。
    try:
        row = screen_one_stock("000001", "测试股", synthetic_coreline_df())
        md = build_report([row] if row else [], {"cache_hit": 1, "cache_files": 1})
        required = ["股票代码", "股票中文名称", "核心线价位", "高质量突破日期"]
        ok = all(x in row for x in required) and "core_line_score" not in md and "breakout_quality" not in md
        checks.append({"round": 3, "name": "输出字段与报告口径", "ok": bool(ok), "detail": {"row_keys": list(row.keys()), "md_preview": md[:300]}})
        ok_all = ok_all and bool(ok)
    except Exception as exc:
        checks.append({"round": 3, "name": "输出字段与报告口径", "ok": False, "error": str(exc)})
        ok_all = False

    return {
        "overall_ok": bool(ok_all),
        "checked_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),
        "checks": checks,
    }


def main() -> None:
    print(BOOT, flush=True)
    print(f"file={Path(__file__).resolve()}", flush=True)
    print(f"target={TARGET} target_dash={TARGET_DASH}", flush=True)
    print(f"progress_color_enabled={PROGRESS_COLOR} 紫色赛博仪表盘=True", flush=True)
    print("cache_dirs=" + " | ".join(str(x) for x in CACHE_DIRS), flush=True)
    self_check = run_self_check()
    print(f"self_check_overall_ok={self_check.get('overall_ok')}", flush=True)
    hist, names, stat = load_cache()
    if hist and ALLOW_BAOSTOCK_FALLBACK:
        stat["recent_refresh"] = refresh_recent_cache(hist)
    elif not hist:
        print("公共缓存为空：输出空报告。", flush=True)
    rows = screen_all(hist, names) if hist else []
    md = build_report(rows, stat)
    write_outputs(rows, md, stat, self_check)
    send_report(md[:9000])
    print(f"Employee3 done. Report: {OUTPUT_MD}", flush=True)
    print(f"CSV: {OUTPUT_CSV}", flush=True)
    print(f"JSON: {OUTPUT_JSON}", flush=True)


if __name__ == "__main__":
    main()
