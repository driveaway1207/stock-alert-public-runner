# -*- coding: utf-8 -*-
from __future__ import annotations

"""三号员工：核心线突破海选器 V1

用途：
1）读取现有日线前复权缓存；
2）全市场逐票计算20日聚合K核心线；
3）筛选最近20个交易日内日K线从下往上高质量突破核心线的股票；
4）对海选命中股票做平铺式深度筛选，输出等级、得分、状态、交易防守位、赔率和归因。

约束：
- 不改 workflow 链路，不写入生产凭证，不运行时替换函数；
- 实体接受不淘汰核心线，只输出状态字段；
- 当前版本在海选后直接做深度筛选；只落地日线缓存可计算字段。
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

BOOT = "EMPLOYEE3_CORE_LINE_BREAKOUT_DEEP_SCREEN_V2_20260531"
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

# 三号员工深度筛选：只使用当前日线缓存可落地字段，不写概念分、不写伪代码。
DEEP_DEFENSE_BUFFER_PCT = float(os.getenv("EMPLOYEE3_DEEP_DEFENSE_BUFFER_PCT", "0.015"))
DEEP_NEAR_LINE_PCT = float(os.getenv("EMPLOYEE3_DEEP_NEAR_LINE_PCT", "0.03"))
DEEP_OVEREXTEND_PCT = float(os.getenv("EMPLOYEE3_DEEP_OVEREXTEND_PCT", "0.18"))
DEEP_RECENT_HOT_20D_PCT = float(os.getenv("EMPLOYEE3_DEEP_RECENT_HOT_20D_PCT", "25"))
DEEP_TOP_REPORT_LIMIT = int(os.getenv("EMPLOYEE3_DEEP_TOP_REPORT_LIMIT", "80"))
DEEP_MIN_FORMAL_SCORE = float(os.getenv("EMPLOYEE3_DEEP_MIN_FORMAL_SCORE", "75"))


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


def progress_color_enabled() -> bool:
    return os.getenv("EMPLOYEE3_PROGRESS_COLOR", "1") != "0" and os.getenv("NO_COLOR", "") == ""


def ansi(text: str, code: str) -> str:
    # GitHub Actions 对 ANSI 颜色支持不稳定；保留开关但进度主体不依赖它。
    if not progress_color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


def stage_style(stage: str) -> Dict[str, Any]:
    # 固定圆点方案：不用方块、不用复杂边框，GitHub Actions 日志最稳定。
    # 缓存浅绿、补拉橙色、核心海选紫色、深度筛选金色。
    if stage == "cache":
        return {"icon": "🟢", "name": "缓存读取", "dot": "🟢"}
    if stage == "refresh":
        return {"icon": "🟠", "name": "数据补拉", "dot": "🟠"}
    if stage == "screen":
        return {"icon": "🟣", "name": "核心海选", "dot": "🟣"}
    if stage == "deep":
        return {"icon": "🟡", "name": "深度筛选", "dot": "🟡"}
    return {"icon": "⚪", "name": stage, "dot": "⚪"}


def parse_progress_extra(extra: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in ss(extra).split():
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def circle_bar(pct: float, stage: str, width: int = PROGRESS_WIDTH) -> str:
    width = max(12, min(width, 36))
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100.0))
    filled = max(0, min(width, filled))
    dot = stage_style(stage)["dot"]
    return dot * filled + "⚪" * (width - filled)


def progress(stage: str, done: int, total: int, start: float, extra: str = "") -> None:
    if total <= 0:
        return
    elapsed = time.time() - start
    speed = done / elapsed if elapsed > 0 and done > 0 else 0.0
    eta = (total - done) / speed if speed > 0 else 0.0
    pct = min(max(done / total, 0.0), 1.0) * 100.0
    info = parse_progress_extra(extra)
    style = stage_style(stage)

    current = info.get("current", "-") or "-"
    hit = info.get("hit", "0")
    bad = info.get("bad", "0")
    short = info.get("short", "0")
    saved = info.get("saved", "0")
    failed = info.get("failed", "0")

    bar = circle_bar(pct, stage)
    if stage == "cache":
        tail = f"命中{hit}｜坏{bad}｜短{short}｜当前{current}"
    elif stage == "refresh":
        tail = f"保存{saved}｜失败{failed}｜当前{current}"
    elif stage in {"screen", "deep"}:
        tail = f"命中{hit}｜当前{current}"
    else:
        tail = extra

    msg = (
        f"{style['icon']} {style['name']} {bar} {pct:5.1f}%｜"
        f"已处理{done:,}/{total:,}｜速度{speed:.2f}只/秒｜已用{fmt_seconds(elapsed)}｜剩余{fmt_seconds(eta)}｜{tail}"
    )
    print(msg, flush=True)

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



def clamp(x: Any, lo: float = 0.0, hi: float = 100.0) -> float:
    v = sf(x)
    if math.isnan(v) or math.isinf(v):
        v = 0.0
    return max(lo, min(hi, v))


def pct_change(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0 if b and b > 0 else 0.0


def kline_features(r: Any, prev_close: float = 0.0, line: float = 0.0) -> Dict[str, float]:
    open_ = sf(getattr(r, "open", 0.0)) if hasattr(r, "open") else sf(r.get("open", 0.0))
    high = sf(getattr(r, "high", 0.0)) if hasattr(r, "high") else sf(r.get("high", 0.0))
    low = sf(getattr(r, "low", 0.0)) if hasattr(r, "low") else sf(r.get("low", 0.0))
    close = sf(getattr(r, "close", 0.0)) if hasattr(r, "close") else sf(r.get("close", 0.0))
    rng = max(high - low, 1e-9)
    body_top = max(open_, close)
    body_bottom = min(open_, close)
    body_abs = abs(close - open_)
    body_ratio = body_abs / rng
    close_pos = (close - low) / rng
    upper_shadow_ratio = (high - body_top) / rng
    lower_shadow_ratio = (body_bottom - low) / rng
    body_pct = body_abs / max(prev_close, 1e-9) if prev_close > 0 else 0.0
    entity_above_line_ratio = 0.0
    if line > 0 and body_abs > 0:
        entity_above_line_ratio = max(0.0, body_top - max(body_bottom, line)) / body_abs
    return {
        "open": rd(open_), "high": rd(high), "low": rd(low), "close": rd(close),
        "body_ratio": rd(body_ratio), "close_pos": rd(close_pos),
        "upper_shadow_ratio": rd(upper_shadow_ratio), "lower_shadow_ratio": rd(lower_shadow_ratio),
        "body_pct": rd(body_pct), "entity_above_line_ratio": rd(entity_above_line_ratio),
    }


def deep_grade(score: float) -> str:
    s = sf(score)
    if s >= 88:
        return "S"
    if s >= 78:
        return "A"
    if s >= 68:
        return "B"
    if s >= 58:
        return "C"
    return "D"


def action_by_grade_state(grade: str, state: str, risk_flags: List[str]) -> str:
    if grade in {"S", "A"} and not any("失败" in x or "过远" in x for x in risk_flags):
        if "回踩确认" in state:
            return "优先深看｜回踩承接型"
        if "接受" in state:
            return "优先深看｜线上接受型"
        return "优先深看"
    if grade == "B":
        return "观察等待｜需要二次确认"
    if "失败" in state:
        return "剔除｜跌回核心线下"
    if "过远" in state:
        return "降级｜突破过远不追"
    return "低优先级观察"


def deep_status_and_score(code: str, name: str, df: pd.DataFrame, line: float, core: Dict[str, Any], br: Dict[str, Any]) -> Dict[str, Any]:
    d = normalize_hist(df)
    L = sf(line)
    empty = {
        "deep_score": 0.0, "deep_grade": "D", "deep_state": "数据不足", "trade_action": "剔除｜数据不足",
        "deep_positive_reasons": "", "deep_negative_reasons": "数据不足", "risk_flags": "数据不足",
    }
    if d.empty or L <= 0 or not br.get("hit"):
        return empty

    d = d.reset_index(drop=True)
    br_date = ss(br.get("date"))
    bidx_list = d.index[d["date"].astype(str) == br_date].tolist()
    if not bidx_list:
        return {**empty, "deep_negative_reasons": "找不到突破日期", "risk_flags": "突破日期缺失"}
    bidx = int(bidx_list[-1])
    if bidx <= 0 or bidx >= len(d):
        return {**empty, "deep_negative_reasons": "突破位置异常", "risk_flags": "突破位置异常"}

    last = d.iloc[-1]
    prev = d.iloc[bidx - 1]
    b = d.iloc[bidx]
    post = d.iloc[bidx:].reset_index(drop=True)
    before = d.iloc[max(0, bidx - 260):bidx].reset_index(drop=True)
    recent20 = d.tail(20).reset_index(drop=True)
    recent10 = d.tail(10).reset_index(drop=True)
    recent5 = d.tail(5).reset_index(drop=True)

    last_close = sf(last.close)
    last_high = sf(last.high)
    last_low = sf(last.low)
    days_since = len(d) - 1 - bidx
    distance_line_pct = pct_change(last_close, L)
    current_above_line = last_close >= L * (1.0 + BREAK_CLOSE_ABOVE_PCT)
    below_close_count = int((post["close"] < L * (1.0 - 0.003)).sum())
    last3_below_count = int((post.tail(3)["close"] < L * (1.0 - 0.003)).sum())
    min_post_close_pct = pct_change(sf(post["close"].min()), L)
    min_post_low_pct = pct_change(sf(post["low"].min()), L)
    max_post_high = sf(post["high"].max())
    drawdown_from_post_high_pct = pct_change(last_close, max_post_high) if max_post_high > 0 else 0.0
    pullback_touched_line = bool((post["low"] <= L * (1.0 + DEEP_NEAR_LINE_PCT)).any())
    pullback_close_not_broken = bool((post["close"] >= L * (1.0 - 0.008)).all())
    near_line_now = abs(distance_line_pct) <= DEEP_NEAR_LINE_PCT * 100.0
    overextended = distance_line_pct >= DEEP_OVEREXTEND_PCT * 100.0

    prev_close = sf(prev.close)
    bfeat = kline_features(b, prev_close=prev_close, line=L)
    last_feat = kline_features(last, prev_close=sf(d.iloc[-2].close) if len(d) >= 2 else 0.0, line=L)
    median_vol_before20 = sf(before.tail(20)["volume"].median()) if not before.empty else 0.0
    breakout_vol = sf(b.volume)
    breakout_volume_ratio = breakout_vol / median_vol_before20 if median_vol_before20 > 0 else 0.0

    recent20_pct = pct_change(last_close, sf(recent20.iloc[0].close)) if len(recent20) >= 2 else 0.0
    recent10_pct = pct_change(last_close, sf(recent10.iloc[0].close)) if len(recent10) >= 2 else 0.0
    recent5_pct = pct_change(last_close, sf(recent5.iloc[0].close)) if len(recent5) >= 2 else 0.0
    recent20_max_close = sf(recent20["close"].max()) if not recent20.empty else last_close
    recent20_drawdown_pct = pct_change(last_close, recent20_max_close) if recent20_max_close > 0 else 0.0

    defense_price = L * (1.0 - DEEP_DEFENSE_BUFFER_PCT)
    risk_pct = pct_change(last_close, defense_price) if defense_price > 0 else 0.0
    prior_high_250 = sf(before.tail(250)["high"].max()) if not before.empty else 0.0
    recent_high_120 = sf(d.tail(120)["high"].max()) if len(d) else 0.0
    if prior_high_250 > last_close * 1.02:
        target_price = prior_high_250
        target_type = "前250日压力"
    elif recent_high_120 > last_close * 1.02:
        target_price = recent_high_120
        target_type = "近120日压力"
    else:
        target_price = last_close * 1.15
        target_type = "无近端压力按15%空间估算"
    space_pct = max(0.0, pct_change(target_price, last_close))
    rr = space_pct / risk_pct if risk_pct > 0 else 0.0

    if last_close < L * (1.0 - 0.012) or last3_below_count >= 2:
        state = "跌回线下/突破失败"
    elif overextended:
        state = "突破过远/追高风险"
    elif pullback_touched_line and pullback_close_not_broken and current_above_line and days_since >= 2:
        state = "回踩确认/线附近承接"
    elif current_above_line and below_close_count == 0 and days_since >= 2:
        state = "突破后接受"
    elif near_line_now and last_close >= L * (1.0 - 0.008):
        state = "线附近震荡"
    elif current_above_line:
        state = "线上观察"
    else:
        state = "突破后未确认"

    pos: List[str] = []
    neg: List[str] = []
    risk_flags: List[str] = []

    core_res = int(sf(core.get("effective_resonance_count")))
    core_vol_res = int(sf(core.get("volume_resonance_count")))
    core_cut = int(sf(core.get("entity_cut_count")))
    core_vol_cut = int(sf(core.get("volume_entity_cut_count")))
    core_entity_accept = int(sf(core.get("entity_accept_count")))
    core_score = clamp(core_res * 2.8 + core_vol_res * 1.6 + min(core_entity_accept, 8) * 0.25 - core_cut * 0.45 - core_vol_cut * 0.9, 0, 18)
    if core_res >= 5:
        pos.append(f"核心线共振{core_res}次")
    if core_vol_res > 0:
        pos.append(f"带量共振{core_vol_res}次")
    if core_cut >= 6:
        neg.append(f"核心线切实体偏多{core_cut}次")
        risk_flags.append("核心线切实体偏多")

    k_score = 0.0
    k_score += 4.0 if sf(bfeat.get("entity_above_line_ratio")) >= 0.70 else 2.5 if sf(bfeat.get("entity_above_line_ratio")) >= 0.45 else 1.0
    k_score += 4.0 if sf(bfeat.get("close_pos")) >= 0.80 else 2.8 if sf(bfeat.get("close_pos")) >= 0.65 else 1.0
    k_score += 3.0 if sf(bfeat.get("upper_shadow_ratio")) <= 0.18 else 2.0 if sf(bfeat.get("upper_shadow_ratio")) <= 0.35 else 0.5
    k_score += 3.0 if sf(bfeat.get("body_ratio")) >= 0.45 else 2.0 if sf(bfeat.get("body_ratio")) >= 0.25 else 0.5
    k_score += 2.0 if sf(bfeat.get("body_pct")) >= 0.015 else 1.0 if sf(bfeat.get("body_pct")) >= 0.005 else 0.0
    k_score += 2.0 if sf(br.get("pct_chg")) >= 3.0 else 1.0 if sf(br.get("pct_chg")) >= 1.0 else 0.0
    k_score = clamp(k_score, 0, 18)
    if sf(bfeat.get("entity_above_line_ratio")) >= 0.5:
        pos.append("突破K实体有效站上核心线")
    if sf(bfeat.get("upper_shadow_ratio")) > 0.35:
        neg.append("突破K上影偏长")
        risk_flags.append("突破上影风险")

    state_score_map = {
        "回踩确认/线附近承接": 22.0,
        "突破后接受": 20.0,
        "线附近震荡": 16.0,
        "线上观察": 14.0,
        "突破后未确认": 8.0,
        "突破过远/追高风险": 7.0,
        "跌回线下/突破失败": 0.0,
    }
    state_score = state_score_map.get(state, 8.0)
    if state in {"回踩确认/线附近承接", "突破后接受", "线附近震荡"}:
        pos.append(state)
    if "失败" in state:
        neg.append("突破后跌回核心线下")
        risk_flags.append("突破失败")
    if "过远" in state:
        neg.append(f"当前距核心线{distance_line_pct:.1f}%偏远")
        risk_flags.append("突破过远")

    position_score = 0.0
    position_score += 5.0 if risk_pct <= 6.0 else 3.5 if risk_pct <= 10.0 else 1.0 if risk_pct <= 15.0 else 0.0
    position_score += 5.0 if rr >= 2.0 else 3.0 if rr >= 1.3 else 1.0 if rr >= 0.8 else 0.0
    position_score += 4.0 if space_pct >= 15.0 else 2.5 if space_pct >= 8.0 else 0.5
    position_score += 4.0 if 0.0 <= distance_line_pct <= 8.0 else 2.0 if -1.0 <= distance_line_pct < 0.0 or 8.0 < distance_line_pct <= 15.0 else 0.5
    position_score = clamp(position_score, 0, 18)
    if risk_pct <= 10.0:
        pos.append(f"距离防守位{risk_pct:.1f}%可控")
    else:
        neg.append(f"距离防守位{risk_pct:.1f}%偏远")
        risk_flags.append("防守距离偏远")
    if rr >= 1.3:
        pos.append(f"估算赔率{rr:.2f}")
    else:
        neg.append(f"估算赔率{rr:.2f}不足")

    volume_heat_score = 0.0
    volume_heat_score += 5.0 if 1.2 <= breakout_volume_ratio <= 3.2 else 3.0 if 0.8 <= breakout_volume_ratio < 1.2 or 3.2 < breakout_volume_ratio <= 5.0 else 1.0
    volume_heat_score += 3.0 if -5.0 <= recent5_pct <= 12.0 else 1.0
    volume_heat_score += 3.0 if recent20_pct <= DEEP_RECENT_HOT_20D_PCT else 0.5
    volume_heat_score += 3.0 if recent20_drawdown_pct >= -12.0 else 1.0
    volume_heat_score = clamp(volume_heat_score, 0, 14)
    if 1.2 <= breakout_volume_ratio <= 3.2:
        pos.append(f"突破量能健康{breakout_volume_ratio:.2f}倍")
    elif breakout_volume_ratio > 5.0:
        neg.append(f"突破量能过大{breakout_volume_ratio:.2f}倍")
        risk_flags.append("爆量风险")
    if recent20_pct > DEEP_RECENT_HOT_20D_PCT:
        neg.append(f"近20日涨幅{recent20_pct:.1f}%偏热")
        risk_flags.append("短线过热")

    risk_score = 10.0
    risk_score -= min(5.0, below_close_count * 1.2)
    risk_score -= 2.0 if min_post_close_pct < -2.0 else 0.0
    risk_score -= 1.5 if sf(last_feat.get("upper_shadow_ratio")) > 0.45 else 0.0
    risk_score -= 1.5 if drawdown_from_post_high_pct < -12.0 else 0.0
    risk_score = clamp(risk_score, 0, 10)
    if below_close_count > 0:
        neg.append(f"突破后收盘跌回线下{below_close_count}次")
    if drawdown_from_post_high_pct < -12.0:
        neg.append(f"突破后回撤{drawdown_from_post_high_pct:.1f}%偏深")
        risk_flags.append("突破后回撤偏深")

    total = clamp(core_score + k_score + state_score + position_score + volume_heat_score + risk_score, 0, 100)
    grade = deep_grade(total)
    action = action_by_grade_state(grade, state, risk_flags)
    if total >= DEEP_MIN_FORMAL_SCORE:
        pool = "深度候选"
    elif total >= 65:
        pool = "观察候选"
    else:
        pool = "低优先级"

    return {
        "deep_score": rd(total, 2),
        "deep_grade": grade,
        "deep_state": state,
        "trade_action": action,
        "deep_pool": pool,
        "deep_positive_reasons": "；".join(pos[:6]) or "无明显加分项",
        "deep_negative_reasons": "；".join(neg[:6]) or "暂无明显扣分项",
        "risk_flags": "；".join(risk_flags[:6]) or "无",
        "days_since_breakout": int(days_since),
        "current_close": rd(last_close, 3),
        "distance_line_pct": rd(distance_line_pct, 2),
        "defense_price": rd(defense_price, 3),
        "defense_distance_pct": rd(risk_pct, 2),
        "target_price": rd(target_price, 3),
        "target_type": target_type,
        "space_pct": rd(space_pct, 2),
        "rr_estimate": rd(rr, 2),
        "breakout_volume_ratio": rd(breakout_volume_ratio, 2),
        "breakout_entity_above_line_ratio": rd(sf(bfeat.get("entity_above_line_ratio")), 3),
        "breakout_close_pos": rd(sf(bfeat.get("close_pos")), 3),
        "breakout_upper_shadow_ratio": rd(sf(bfeat.get("upper_shadow_ratio")), 3),
        "min_post_close_pct": rd(min_post_close_pct, 2),
        "min_post_low_pct": rd(min_post_low_pct, 2),
        "recent5_pct": rd(recent5_pct, 2),
        "recent10_pct": rd(recent10_pct, 2),
        "recent20_pct": rd(recent20_pct, 2),
        "post_drawdown_pct": rd(drawdown_from_post_high_pct, 2),
        "score_core_line": rd(core_score, 2),
        "score_breakout_k": rd(k_score, 2),
        "score_post_state": rd(state_score, 2),
        "score_position_rr": rd(position_score, 2),
        "score_volume_heat": rd(volume_heat_score, 2),
        "score_risk_control": rd(risk_score, 2),
    }


def screen_one_stock(code: str, name: str, df: pd.DataFrame) -> Dict[str, Any]:
    core = choose_core_line(df)
    line = sf(core.get("line")) if core.get("line") is not None else 0.0
    br = daily_breakout_quality(df, line)
    if not br.get("hit"):
        return {}
    deep = deep_status_and_score(code, name, df, line, core, br)
    row = {
        "股票代码": code,
        "股票中文名称": name or code,
        "核心线价位": rd(line, 3),
        "高质量突破日期": br.get("date", ""),
        "深度等级": deep.get("deep_grade", "D"),
        "深度得分": deep.get("deep_score", 0),
        "当前状态": deep.get("deep_state", ""),
        "操作建议": deep.get("trade_action", ""),
        "候选池": deep.get("deep_pool", ""),
        "加分原因": deep.get("deep_positive_reasons", ""),
        "扣分原因": deep.get("deep_negative_reasons", ""),
        "风险标签": deep.get("risk_flags", ""),
        "当前收盘": deep.get("current_close", 0),
        "距核心线%": deep.get("distance_line_pct", 0),
        "交易防守位": deep.get("defense_price", 0),
        "防守距离%": deep.get("defense_distance_pct", 0),
        "目标/压力价": deep.get("target_price", 0),
        "上方空间%": deep.get("space_pct", 0),
        "估算赔率": deep.get("rr_estimate", 0),
        "突破量比": deep.get("breakout_volume_ratio", 0),
        "突破实体在线上比例": deep.get("breakout_entity_above_line_ratio", 0),
        "突破收盘位置": deep.get("breakout_close_pos", 0),
        "突破上影比例": deep.get("breakout_upper_shadow_ratio", 0),
        "突破后天数": deep.get("days_since_breakout", 0),
        "近5日涨幅%": deep.get("recent5_pct", 0),
        "近10日涨幅%": deep.get("recent10_pct", 0),
        "近20日涨幅%": deep.get("recent20_pct", 0),
        # 以下字段进CSV/JSON审计。
        "core_line_score": core.get("net_score", 0),
        "core_line_resonance_count": core.get("effective_resonance_count", 0),
        "core_line_volume_resonance_count": core.get("volume_resonance_count", 0),
        "core_line_entity_cut_count": core.get("entity_cut_count", 0),
        "core_line_entity_accept_count": core.get("entity_accept_count", 0),
        "breakout_quality": br.get("quality", 0),
        "breakout_close": br.get("close", 0),
    }
    for key, val in deep.items():
        if key not in row:
            row[key] = val
    return row


def screen_all(hist: Dict[str, pd.DataFrame], names: Dict[str, str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    items = list(hist.items())
    start = time.time()
    progress("deep", 0, len(items), start, "start")
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
            progress("deep", i, len(items), start, f"hit={len(rows)} current={code}")
    rows = sorted(rows, key=lambda x: (sf(x.get("深度得分")), ss(x.get("高质量突破日期")), sf(x.get("breakout_quality")), sf(x.get("core_line_score"))), reverse=True)
    return rows



def build_report(rows: List[Dict[str, Any]], stat: Dict[str, Any]) -> str:
    if not rows:
        return "无符合条件股票。"
    show_rows = rows[:max(1, DEEP_TOP_REPORT_LIMIT)]
    lines = [
        "| 股票代码 | 股票简称 | 核心线价格 | 最终评级 |",
        "|---|---|---:|---|",
    ]
    for r in show_rows:
        lines.append(
            f"| {r.get('股票代码','')} | {r.get('股票中文名称','')} | {r.get('核心线价位',0)} | {r.get('深度等级','')} |"
        )
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
            "deep_min_formal_score": DEEP_MIN_FORMAL_SCORE,
            "deep_defense_buffer_pct": DEEP_DEFENSE_BUFFER_PCT,
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
        required = ["股票代码", "股票中文名称", "核心线价位", "深度等级"]
        report_has_only_simple_columns = ("| 股票代码 | 股票简称 | 核心线价格 | 最终评级 |" in md and "深度得分" not in md and "当前状态" not in md and "操作建议" not in md and "core_line_score" not in md)
        ok = all(x in row for x in required) and report_has_only_simple_columns
        checks.append({"round": 3, "name": "深度筛选输出字段与报告口径", "ok": bool(ok), "detail": {"row_keys": list(row.keys()), "md_preview": md[:500]}})
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
    print(f"progress_color_enabled={PROGRESS_COLOR} 圆点进度=True", flush=True)
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
