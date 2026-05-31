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
    if not progress_color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


def stage_style(stage: str) -> Dict[str, Any]:
    # 进度条样式参考五号员工：固定宽度 █/░ 条形进度。
    # 保留三号员工原阶段配色：缓存浅绿、补拉橙色、核心海选紫色、深度筛选金色。
    # GitHub Actions 支持 ANSI 时，整行文字与进度条统一使用同一阶段颜色；关闭颜色时自动退化为纯文本。
    if stage == "cache":
        return {"icon": "🟢", "name": "缓存读取", "color": "92"}
    if stage == "refresh":
        return {"icon": "🟠", "name": "数据补拉", "color": "38;5;208"}
    if stage == "screen":
        return {"icon": "🟣", "name": "核心海选", "color": "95"}
    if stage == "deep":
        return {"icon": "🟡", "name": "深度筛选", "color": "93"}
    return {"icon": "▶", "name": stage, "color": "97"}


def parse_progress_extra(extra: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in ss(extra).split():
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def progress_bar(pct: float, width: int = PROGRESS_WIDTH) -> str:
    width = max(18, min(width, 44))
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100.0))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


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

    bar = progress_bar(pct)
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
    print(ansi(msg, style.get("color", "97")), flush=True)

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


def add_deep_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = normalize_hist(df).copy().reset_index(drop=True)
    if d.empty:
        return d
    close = pd.to_numeric(d["close"], errors="coerce")
    high = pd.to_numeric(d["high"], errors="coerce")
    low = pd.to_numeric(d["low"], errors="coerce")
    open_ = pd.to_numeric(d["open"], errors="coerce")
    volume = pd.to_numeric(d["volume"], errors="coerce").fillna(0.0)
    amount = pd.to_numeric(d["amount"], errors="coerce").fillna(0.0) if "amount" in d.columns else close * volume
    prev_close = close.shift(1)
    d["ma3"] = close.rolling(3, min_periods=2).mean()
    d["ma5"] = close.rolling(5, min_periods=3).mean()
    d["ma6"] = close.rolling(6, min_periods=3).mean()
    d["ma10"] = close.rolling(10, min_periods=5).mean()
    d["ma12"] = close.rolling(12, min_periods=6).mean()
    d["ma20"] = close.rolling(20, min_periods=10).mean()
    d["ma24"] = close.rolling(24, min_periods=12).mean()
    d["ma60"] = close.rolling(60, min_periods=20).mean()
    d["bbi"] = (d["ma3"] + d["ma6"] + d["ma12"] + d["ma24"]) / 4.0
    d["boll_mid"] = d["ma20"]
    d["boll_std"] = close.rolling(20, min_periods=10).std()
    d["boll_upper"] = d["boll_mid"] + 2.0 * d["boll_std"]
    d["boll_lower"] = d["boll_mid"] - 2.0 * d["boll_std"]
    d["vol_ma5"] = volume.rolling(5, min_periods=3).mean()
    d["vol_ma20"] = volume.rolling(20, min_periods=8).mean()
    d["vol_med20"] = volume.rolling(20, min_periods=8).median()
    d["amount_ma20"] = amount.rolling(20, min_periods=8).mean()
    d["range_pct"] = (high - low) / prev_close.replace(0, np.nan)
    d["body_pct"] = (close - open_) / prev_close.replace(0, np.nan)
    d["entity_abs_pct"] = (close - open_).abs() / prev_close.replace(0, np.nan)
    d["close_pos"] = (close - low) / (high - low).replace(0, np.nan)
    d["upper_shadow_ratio"] = (high - pd.concat([open_, close], axis=1).max(axis=1)) / (high - low).replace(0, np.nan)
    d["lower_shadow_ratio"] = (pd.concat([open_, close], axis=1).min(axis=1) - low) / (high - low).replace(0, np.nan)
    d["vr_prev"] = volume / volume.shift(1).replace(0, np.nan)
    d["vr_med20"] = volume / d["vol_med20"].replace(0, np.nan)
    if "pct_chg" not in d.columns or d["pct_chg"].abs().sum() == 0:
        d["pct_chg"] = close.pct_change().fillna(0.0) * 100.0
    return d


def build_monthly_deep_df(df: pd.DataFrame) -> pd.DataFrame:
    d = add_deep_indicators(df)
    if d.empty or len(d) < 120:
        return pd.DataFrame()
    x = d.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    x = x.dropna(subset=["date"]).set_index("date").sort_index()
    m = pd.DataFrame()
    m["open"] = x["open"].resample("ME").first()
    m["high"] = x["high"].resample("ME").max()
    m["low"] = x["low"].resample("ME").min()
    m["close"] = x["close"].resample("ME").last()
    m["volume"] = x["volume"].resample("ME").sum()
    m["amount"] = x["amount"].resample("ME").sum() if "amount" in x.columns else 0.0
    m = m.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index()
    if m.empty:
        return m
    c = pd.to_numeric(m["close"], errors="coerce")
    v = pd.to_numeric(m["volume"], errors="coerce").fillna(0.0)
    m["ma3"] = c.rolling(3, min_periods=2).mean()
    m["ma6"] = c.rolling(6, min_periods=3).mean()
    m["ma12"] = c.rolling(12, min_periods=6).mean()
    m["ma20"] = c.rolling(20, min_periods=10).mean()
    m["ma24"] = c.rolling(24, min_periods=12).mean()
    m["bbi"] = (m["ma3"] + m["ma6"] + m["ma12"] + m["ma24"]) / 4.0
    m["boll_mid"] = m["ma20"]
    m["boll_std"] = c.rolling(20, min_periods=10).std()
    m["boll_upper"] = m["boll_mid"] + 2.0 * m["boll_std"]
    m["boll_lower"] = m["boll_mid"] - 2.0 * m["boll_std"]
    m["boll_width"] = (m["boll_upper"] - m["boll_lower"]) / m["boll_mid"].replace(0, np.nan)
    ma_max = m[["ma3", "ma6", "ma12", "ma24"]].max(axis=1)
    ma_min = m[["ma3", "ma6", "ma12", "ma24"]].min(axis=1)
    m["bbi_dispersion"] = (ma_max - ma_min) / m["bbi"].replace(0, np.nan)
    m["mid"] = m["bbi"].where(m["bbi"].notna(), m["boll_mid"])
    m["body_pct"] = (m["close"] - m["open"]) / m["open"].replace(0, np.nan)
    m["body_ratio"] = (m["close"] - m["open"]).abs() / (m["high"] - m["low"]).replace(0, np.nan)
    m["close_pos"] = (m["close"] - m["low"]) / (m["high"] - m["low"]).replace(0, np.nan)
    m["vol_pct_rank_60"] = v.rolling(60, min_periods=12).apply(lambda a: pd.Series(a).rank(pct=True).iloc[-1], raw=False)
    return m


def _score_part(value: bool, score: float) -> float:
    return float(score) if bool(value) else 0.0



def get_limit_threshold(code: str) -> float:
    c = code_of(code)
    if c.startswith(("300", "301", "688", "689")):
        return 19.3
    if c.startswith(("8", "4", "920")):
        return 29.0
    return 9.3

def detect_event_tags(d: pd.DataFrame, idx: int) -> Dict[str, Any]:
    tags: List[str] = []
    if d.empty or idx <= 0 or idx >= len(d):
        return {"tags": "", "limit_up": False, "gap_up": False, "bullish_engulf": False, "separation_line": False}
    r = d.iloc[idx]
    p = d.iloc[idx - 1]
    close = sf(r.close); open_ = sf(r.open); high = sf(r.high); low = sf(r.low)
    prev_close = sf(p.close); prev_open = sf(p.open)
    pct = sf(r.pct_chg)
    close_pos = sf(r.get("close_pos", 0.0))
    upper = sf(r.get("upper_shadow_ratio", 0.0))
    body_pct = sf(r.get("entity_abs_pct", 0.0))
    limit_thr = get_limit_threshold(code_of(r.get("code", ""))) if "code" in d.columns and ss(r.get("code", "")) else 9.3
    limit_up = pct >= limit_thr and close_pos >= 0.90
    gap_up = open_ >= prev_close * 1.015 and low >= prev_close * 1.003
    bullish_engulf = close > open_ and prev_close < prev_open and close >= prev_open and open_ <= prev_close * 1.01
    # 分手线近似：前阴后阳，开盘接近前阴开盘，阳线收盘强。
    separation_line = close > open_ and prev_close < prev_open and abs(open_ - prev_open) / max(prev_open, 1e-9) <= 0.012 and close_pos >= 0.70
    long_upper = upper >= 0.45 and close_pos <= 0.60
    full_body = close > open_ and body_pct >= 0.03 and close_pos >= 0.85 and upper <= 0.15
    if limit_up: tags.append("涨停近似")
    if gap_up: tags.append("跳空")
    if bullish_engulf: tags.append("阳包阴")
    if separation_line: tags.append("分手线")
    if long_upper: tags.append("长上影")
    if full_body: tags.append("强实体阳线")
    return {
        "tags": "；".join(tags),
        "limit_up": bool(limit_up),
        "gap_up": bool(gap_up),
        "bullish_engulf": bool(bullish_engulf),
        "separation_line": bool(separation_line),
        "long_upper": bool(long_upper),
        "full_body": bool(full_body),
    }


def evaluate_major_cycle_pricing(d: pd.DataFrame, last_close: float) -> Dict[str, Any]:
    m = build_monthly_deep_df(d)
    if m.empty or len(m) < 18 or last_close <= 0:
        return {"score": 0.0, "type": "无", "detail": "月线样本不足", "anchor_price": 0.0}
    cur = m.iloc[-1]
    mid = sf(cur.get("mid", 0.0))
    score = 0.0
    reasons: List[str] = []
    anchor = 0.0
    if mid > 0:
        if sf(cur.close) >= mid:
            score += 5.0
            reasons.append("月线站回中轨")
            anchor = mid
        elif sf(cur.high) >= mid and sf(cur.close) >= mid * 0.985:
            score += 2.5
            reasons.append("月线贴近中轨")
            anchor = mid
    recent = m.tail(60).copy()
    if len(recent) >= 24 and pd.notna(cur.get("boll_width", np.nan)):
        widths = recent["boll_width"].dropna()
        if len(widths) >= 20:
            pct = float((widths <= sf(cur.get("boll_width"))).sum() / len(widths))
            if pct <= 0.15:
                score += 3.0
                reasons.append(f"月线缩口分位{pct:.0%}")
            elif pct <= 0.30:
                score += 1.5
                reasons.append(f"月线轻缩口{pct:.0%}")
    # 最大量阳K实体中位/实底防守，用最近100个月可落地近似。
    scan = m.tail(100).iloc[:-1].copy()
    if len(scan) >= 12:
        bull = scan[(scan["close"] > scan["open"]) & (scan["body_ratio"] >= 0.35)]
        if not bull.empty:
            mx = bull.loc[bull["volume"].idxmax()]
            body_top = max(sf(mx.open), sf(mx.close))
            body_bottom = min(sf(mx.open), sf(mx.close))
            body_mid = (body_top + body_bottom) / 2.0
            if body_mid > 0 and last_close >= body_mid * 0.995:
                score += 4.0
                reasons.append(f"最大量阳K中位防守{body_mid:.2f}")
                anchor = anchor or body_mid
            if body_top > 0 and last_close >= body_top * 1.003:
                score += 3.0
                reasons.append(f"最大量阳K实体顶修复{body_top:.2f}")
                anchor = body_top
    score = clamp(score, 0, 18)
    if score >= 12:
        typ = "大周期价值重估"
    elif score >= 7:
        typ = "大周期修复"
    elif score > 0:
        typ = "大周期弱修复"
    else:
        typ = "无"
    return {"score": rd(score, 2), "type": typ, "detail": "；".join(reasons) or "无大周期定价证据", "anchor_price": rd(anchor, 3)}


def evaluate_supply_absorption(d: pd.DataFrame, bidx: int, line: float) -> Dict[str, Any]:
    if d.empty or line <= 0 or bidx < 40:
        return {"score": 0.0, "type": "无", "detail": "供应吸收样本不足"}
    pre = d.iloc[max(0, bidx - 160):bidx].copy()
    if len(pre) < 40:
        return {"score": 0.0, "type": "无", "detail": "供应吸收样本不足"}
    high = pd.to_numeric(pre["high"], errors="coerce")
    close = pd.to_numeric(pre["close"], errors="coerce")
    low = pd.to_numeric(pre["low"], errors="coerce")
    volume = pd.to_numeric(pre["volume"], errors="coerce").fillna(0.0)
    near = ((high >= line * 0.985) & (high <= line * 1.035)) | ((close >= line * 0.985) & (close <= line * 1.025))
    failed = (high >= line * 1.003) & (close < line * 0.995)
    near_count = int(near.sum())
    fail_count = int(failed.sum())
    score = 0.0
    reasons: List[str] = []
    if near_count >= 5:
        score += 5.0; reasons.append(f"压力反复触碰{near_count}次")
    elif near_count >= 3:
        score += 3.0; reasons.append(f"压力触碰{near_count}次")
    if fail_count >= 2:
        score += 3.0; reasons.append(f"假突破/冲高失败{fail_count}次")
    elif fail_count == 1:
        score += 1.2; reasons.append("存在一次假突破记忆")
    # 回撤变浅：最近两次触线后的低点抬高。
    touch_idx = list(np.where(near.values)[0])
    if len(touch_idx) >= 2:
        lows_after = []
        for ti in touch_idx[-4:]:
            seg = pre.iloc[ti:min(len(pre), ti + 12)]
            if len(seg) >= 3:
                lows_after.append(sf(seg["low"].min()))
        if len(lows_after) >= 2 and lows_after[-1] >= lows_after[0] * 0.98:
            score += 2.5; reasons.append("压力下回撤变浅")
    vol_med = sf(volume.median())
    if vol_med > 0 and int((near & (volume >= vol_med * 1.30)).sum()) >= 2:
        score += 2.0; reasons.append("带量冲击压力")
    score = clamp(score, 0, 14)
    typ = "供应吸收后突破" if score >= 8 else "供应吸收观察" if score >= 4 else "无"
    return {"score": rd(score, 2), "type": typ, "detail": "；".join(reasons) or "无明显供应吸收"}


def evaluate_explosion_eve(d: pd.DataFrame, bidx: int) -> Dict[str, Any]:
    if d.empty or bidx < 70:
        return {"score": 0.0, "type": "无", "detail": "爆发前夜样本不足"}
    pre = d.iloc[max(0, bidx - 90):bidx].copy().reset_index(drop=True)
    if len(pre) < 60:
        return {"score": 0.0, "type": "无", "detail": "爆发前夜样本不足"}
    first = pre.iloc[:max(20, len(pre) - 30)]
    last = pre.tail(30)
    close = pd.to_numeric(pre["close"], errors="coerce")
    low = pd.to_numeric(pre["low"], errors="coerce")
    vol = pd.to_numeric(pre["volume"], errors="coerce").fillna(0.0)
    amount = pd.to_numeric(pre["amount"], errors="coerce").fillna(0.0) if "amount" in pre.columns else vol * close
    score = 0.0
    reasons: List[str] = []
    r1 = sf(first["range_pct"].replace([np.inf, -np.inf], np.nan).dropna().mean())
    r2 = sf(last["range_pct"].replace([np.inf, -np.inf], np.nan).dropna().mean())
    if r1 > 0 and r2 > 0:
        ratio = r2 / r1
        if ratio <= 0.65:
            score += 5.0; reasons.append(f"波动压缩{ratio:.2f}")
        elif ratio <= 0.82:
            score += 2.8; reasons.append(f"波动收敛{ratio:.2f}")
    cv1 = sf(first["volume"].std()) / max(sf(first["volume"].mean()), 1e-9)
    cv2 = sf(last["volume"].std()) / max(sf(last["volume"].mean()), 1e-9)
    if cv1 > 0 and cv2 > 0:
        vr = cv2 / cv1
        if vr <= 0.70:
            score += 4.0; reasons.append(f"量能从乱到稳{vr:.2f}")
        elif vr <= 0.88:
            score += 2.0; reasons.append(f"量能稳定改善{vr:.2f}")
    low_first = sf(first.tail(20)["low"].min())
    low_last = sf(last.tail(15)["low"].min())
    if low_first > 0 and low_last >= low_first * 1.03:
        score += 3.0; reasons.append("低点抬高")
    amt1 = sf(first.tail(30)["amount"].mean())
    amt2 = sf(last["amount"].mean())
    if amt1 > 0 and 1.05 <= amt2 / amt1 <= 2.50:
        score += 2.0; reasons.append(f"成交中枢温和抬升{amt2/amt1:.2f}")
    big_down = (pre["close"] < pre["open"]) & (((pre["open"] - pre["close"]) / pre["close"].shift(1).replace(0, np.nan)) >= 0.035) & (pre["volume"] >= pre["vol_ma20"].fillna(pre["volume"].median()) * 1.35)
    first_bd = int(big_down.iloc[:len(first)].sum())
    last_bd = int(big_down.iloc[-30:].sum())
    if last_bd == 0 or last_bd < first_bd:
        score += 2.0; reasons.append(f"放量长阴减少{first_bd}->{last_bd}")
    score = clamp(score, 0, 18)
    typ = "爆发前夜启动" if score >= 12 else "爆发前夜观察" if score >= 7 else "无"
    return {"score": rd(score, 2), "type": typ, "detail": "；".join(reasons) or "无爆发前夜证据"}


def evaluate_pullback_acceptance(d: pd.DataFrame, bidx: int, line: float) -> Dict[str, Any]:
    if d.empty or line <= 0 or bidx <= 0 or bidx >= len(d):
        return {"score": 0.0, "type": "无", "detail": "承接样本不足", "support_price": 0.0, "pullback_low": 0.0}
    b = d.iloc[bidx]
    post = d.iloc[bidx:].copy().reset_index(drop=True)
    if post.empty:
        return {"score": 0.0, "type": "无", "detail": "承接样本不足", "support_price": 0.0, "pullback_low": 0.0}
    body_top = max(sf(b.open), sf(b.close))
    body_bottom = min(sf(b.open), sf(b.close))
    body_mid = (body_top + body_bottom) / 2.0
    levels = [
        ("核心线", line),
        ("突破K实体中位", body_mid),
        ("突破K实底", body_bottom),
    ]
    last = d.iloc[-1]
    for nm in ["ma5", "ma10", "bbi", "boll_mid"]:
        v = sf(last.get(nm, 0.0))
        if v > 0:
            levels.append((nm.upper(), v))
    best_name, best_level, best_score = "", 0.0, 0.0
    reasons: List[str] = []
    post_low = sf(post["low"].min())
    post_close_min = sf(post["close"].min())
    post_tail = post.tail(min(8, len(post))).copy()
    for name, lv in levels:
        if lv <= 0:
            continue
        touched = bool((post["low"] <= lv * 1.025).any())
        close_hold = bool((post["close"] >= lv * 0.985).all())
        tail_hold = bool((post_tail["close"] >= lv * 0.992).all()) if not post_tail.empty else False
        s = 0.0
        if touched and close_hold:
            s += 6.0
        elif touched and tail_hold:
            s += 4.0
        elif post_close_min >= lv * 0.995:
            s += 2.0
        if s > best_score:
            best_name, best_level, best_score = name, lv, s
    if best_score > 0:
        reasons.append(f"回踩/守住{best_name}{best_level:.2f}")
    # 缩量、小阴小阳、重新转强。
    bvol = sf(b.volume)
    after = post.iloc[1:].copy()
    if not after.empty and bvol > 0:
        pull = after[(after["low"] <= max(line, best_level) * 1.035) | (after["close"] <= max(line, best_level) * 1.045)]
        if not pull.empty:
            pull_vol_ratio = sf(pull["volume"].median()) / bvol
            small_body_ratio = float(((pull["entity_abs_pct"].abs() <= 0.035).sum()) / max(1, len(pull))) if "entity_abs_pct" in pull.columns else 0.0
            if pull_vol_ratio <= 0.75:
                best_score += 3.0; reasons.append(f"回踩缩量{pull_vol_ratio:.2f}")
            if small_body_ratio >= 0.55:
                best_score += 2.0; reasons.append("回踩小阴小阳")
    if len(post) >= 2:
        last = post.iloc[-1]
        prev = post.iloc[-2]
        if sf(last.close) > sf(prev.close) and sf(last.close_pos) >= 0.65 and sf(last.close) >= max(line, best_level) * 1.003:
            best_score += 3.0; reasons.append("回踩后重新转强")
    score = clamp(best_score, 0, 18)
    typ = "回踩承接二买" if score >= 12 else "突破后接受" if score >= 7 else "未确认承接" if score > 0 else "无"
    return {"score": rd(score, 2), "type": typ, "detail": "；".join(reasons) or "无承接确认", "support_price": rd(best_level, 3), "support_type": best_name, "pullback_low": rd(post_low, 3), "break_body_mid": rd(body_mid, 3), "break_body_bottom": rd(body_bottom, 3)}


def evaluate_fund_behavior(d: pd.DataFrame, bidx: int) -> Dict[str, Any]:
    if d.empty or bidx <= 0 or bidx >= len(d):
        return {"score": 0.0, "type": "无", "detail": "资金样本不足", "volume_ratio": 0.0, "stall": False}
    b = d.iloc[bidx]
    pre = d.iloc[max(0, bidx - 20):bidx]
    med20 = sf(pre["volume"].median()) if not pre.empty else 0.0
    vol_ratio = sf(b.volume) / med20 if med20 > 0 else 0.0
    bullish = sf(b.close) > sf(b.open)
    close_pos = sf(b.get("close_pos", 0.0))
    upper = sf(b.get("upper_shadow_ratio", 0.0))
    pct = sf(b.get("pct_chg", 0.0))
    body_pct = sf(b.get("entity_abs_pct", 0.0))
    score = 0.0
    typ = "无"
    reasons: List[str] = []
    stall = bool(vol_ratio >= 1.8 and (pct < 1.2 or close_pos < 0.55 or upper >= 0.42 or body_pct < 0.008))
    if stall:
        typ = "放量滞涨"
        score = -8.0
        reasons.append(f"高量低效{vol_ratio:.2f}倍")
    elif bullish and 1.8 <= vol_ratio <= 2.5 and close_pos >= 0.68:
        typ = "标准倍量阳K"
        score = 12.0
        reasons.append(f"标准倍量{vol_ratio:.2f}倍")
    elif bullish and 1.2 <= vol_ratio <= 3.2 and close_pos >= 0.62:
        typ = "健康放量"
        score = 9.0
        reasons.append(f"健康放量{vol_ratio:.2f}倍")
    elif bullish and 0.85 <= vol_ratio < 1.2 and close_pos >= 0.72:
        typ = "平量强收"
        score = 6.0
        reasons.append(f"平量强收{vol_ratio:.2f}倍")
    elif vol_ratio > 4.5:
        typ = "爆量分歧"
        score = -4.0
        reasons.append(f"爆量{vol_ratio:.2f}倍")
    else:
        typ = "量能普通"
        score = 2.0
        reasons.append(f"量比{vol_ratio:.2f}")
    # 倍量后平量承接：第二根量差不大，且不破坏。
    if bidx + 1 < len(d) and vol_ratio >= 1.5:
        n1 = d.iloc[bidx + 1]
        diff = abs(sf(n1.volume) / max(sf(b.volume), 1e-9) - 1.0)
        n1_bad = sf(n1.close) < min(sf(b.open), sf(b.close)) * 0.985 and sf(n1.close) < sf(n1.open)
        if diff <= 0.08 and not n1_bad:
            score += 4.0
            reasons.append(f"次日平量承接差{diff:.1%}")
    # 阳量/阴量关系：只给小共振，避免重复。
    last20 = d.iloc[max(0, bidx - 20):bidx + 1].copy()
    up = last20[last20["close"] > last20["open"]]
    down = last20[last20["close"] < last20["open"]]
    down_vol = sf(down["volume"].mean()) if not down.empty else 0.0
    up_vol = sf(up["volume"].mean()) if not up.empty else 0.0
    if down_vol > 0 and up_vol / down_vol >= 1.08:
        score += 2.0
        reasons.append(f"阳量/阴量{up_vol/down_vol:.2f}")
    score = clamp(score, -10, 18)
    return {"score": rd(score, 2), "type": typ, "detail": "；".join(reasons), "volume_ratio": rd(vol_ratio, 2), "stall": bool(stall)}


def evaluate_activity(d: pd.DataFrame, code: str) -> Dict[str, Any]:
    if d.empty or len(d) < 60:
        return {"score": 0.0, "type": "无", "detail": "股性样本不足"}
    w = d.tail(100).copy()
    limit_thr = get_limit_threshold(code)
    limit_count = int((w["pct_chg"] >= limit_thr).sum())
    big_up_count = int((w["pct_chg"] >= 5.0).sum())
    gap_count = int((w["open"] >= w["close"].shift(1) * 1.015).sum())
    atr = sf(w["range_pct"].replace([np.inf, -np.inf], np.nan).dropna().tail(60).mean())
    body_med = sf(w["entity_abs_pct"].replace([np.inf, -np.inf], np.nan).dropna().tail(60).median())
    amount20 = sf(w["amount"].tail(20).mean()) if "amount" in w.columns else 0.0
    score = 0.0
    reasons: List[str] = []
    if limit_count >= 4:
        score += 6.0; reasons.append(f"100日涨停{limit_count}次")
    elif limit_count >= 2:
        score += 3.5; reasons.append(f"100日涨停{limit_count}次")
    elif limit_count == 0:
        score -= 2.0; reasons.append("100日无涨停")
    if big_up_count >= 6:
        score += 4.0; reasons.append(f"大阳{big_up_count}次")
    elif big_up_count >= 3:
        score += 2.0; reasons.append(f"大阳{big_up_count}次")
    if gap_count >= 3:
        score += 2.0; reasons.append(f"跳空{gap_count}次")
    if atr >= 0.035:
        score += 3.0; reasons.append(f"ATR弹性{atr:.1%}")
    elif atr < 0.018:
        score -= 2.0; reasons.append(f"波动黏密{atr:.1%}")
    if body_med < 0.008:
        score -= 1.5; reasons.append("实体黏密")
    if amount20 > 0 and amount20 < 5e7:
        score -= 3.0; reasons.append(f"成交额不足{amount20/1e8:.2f}亿")
    score = clamp(score, -8, 14)
    typ = "高活跃" if score >= 8 else "中活跃" if score >= 4 else "低活跃" if score < 0 else "普通"
    return {"score": rd(score, 2), "type": typ, "detail": "；".join(reasons), "limitup_100d_count": limit_count, "big_up_100d_count": big_up_count, "gap_100d_count": gap_count, "atr60": rd(atr, 4), "amount20": rd(amount20, 2)}


def evaluate_time_maturity(d: pd.DataFrame, bidx: int) -> Dict[str, Any]:
    if d.empty or bidx < 30:
        return {"score": 0.0, "type": "无", "detail": "时间样本不足"}
    days_since = len(d) - 1 - bidx
    pre = d.iloc[max(0, bidx - 80):bidx].copy()
    score = 0.0
    reasons: List[str] = []
    if 2 <= days_since <= 8:
        score += 4.0; reasons.append(f"突破后{days_since}日处于确认窗口")
    elif days_since <= 1:
        score += 1.0; reasons.append("突破初期需确认")
    elif 9 <= days_since <= 20:
        score += 2.0; reasons.append(f"突破后{days_since}日仍在观察窗口")
    close_amp = 0.0
    if len(pre) >= 30:
        close_mid = sf(pre["close"].median())
        close_amp = (sf(pre["close"].quantile(0.90)) - sf(pre["close"].quantile(0.10))) / close_mid if close_mid > 0 else 0.0
        if close_amp <= 0.14:
            score += 3.0; reasons.append(f"平台蓄势收敛{close_amp:.1%}")
        elif close_amp <= 0.22:
            score += 1.5; reasons.append(f"平台蓄势一般{close_amp:.1%}")
    score = clamp(score, 0, 10)
    typ = "时间成熟" if score >= 6 else "时间观察" if score > 0 else "无"
    return {"score": rd(score, 2), "type": typ, "detail": "；".join(reasons), "days_since_breakout": int(days_since), "pre_close_amp": rd(close_amp, 4)}


def evaluate_trade_pricing(d: pd.DataFrame, bidx: int, line: float, support_price: float) -> Dict[str, Any]:
    if d.empty or bidx <= 0 or bidx >= len(d) or line <= 0:
        return {"score": 0.0, "ok": False, "detail": "交易定价样本不足", "defense_price": 0.0, "target_price": 0.0, "rr": 0.0}
    last = d.iloc[-1]
    b = d.iloc[bidx]
    last_close = sf(last.close)
    body_bottom = min(sf(b.open), sf(b.close))
    body_mid = (max(sf(b.open), sf(b.close)) + body_bottom) / 2.0
    candidates = []
    for name, price, buffer in [
        ("核心线缓冲", line, 0.015),
        ("突破K实底", body_bottom, 0.012),
        ("突破K实体中位", body_mid, 0.018),
        ("承接位", support_price, 0.015),
        ("MA10", sf(last.get("ma10", 0.0)), 0.018),
        ("BBI", sf(last.get("bbi", 0.0)), 0.018),
    ]:
        if price > 0 and price < last_close:
            candidates.append((price * (1.0 - buffer), name, price))
    if candidates:
        # 选离现价最近但仍有结构来源的防守位，避免过宽假防守。
        defense_price, defense_type, structure_key = max(candidates, key=lambda x: x[0])
    else:
        defense_price, defense_type, structure_key = line * 0.985, "核心线缓冲", line
    risk_pct = pct_change(last_close, defense_price) if defense_price > 0 else 0.0
    pre = d.iloc[max(0, bidx - 520):bidx].copy()
    highs = pd.to_numeric(pre["high"], errors="coerce").dropna()
    pressures = sorted([float(x) for x in highs.tolist() if float(x) >= last_close * 1.025])
    target_price = pressures[0] if pressures else 0.0
    target_type = "历史真实压力" if target_price > 0 else "无真实上方压力"
    space_pct = pct_change(target_price, last_close) if target_price > 0 else 0.0
    rr = space_pct / risk_pct if risk_pct > 0 and target_price > 0 else 0.0
    score = 0.0
    reasons: List[str] = []
    if risk_pct <= 6.0:
        score += 7.0; reasons.append(f"防守距离{risk_pct:.1f}%")
    elif risk_pct <= 10.5:
        score += 4.0; reasons.append(f"防守距离{risk_pct:.1f}%")
    else:
        reasons.append(f"防守距离{risk_pct:.1f}%偏远")
    if target_price > 0:
        if space_pct >= 18.0:
            score += 5.0; reasons.append(f"真实空间{space_pct:.1f}%")
        elif space_pct >= 10.0:
            score += 3.0; reasons.append(f"真实空间{space_pct:.1f}%")
        else:
            reasons.append(f"第一压力近{space_pct:.1f}%")
        if rr >= 2.0:
            score += 6.0; reasons.append(f"RR={rr:.2f}")
        elif rr >= 1.35:
            score += 3.5; reasons.append(f"RR={rr:.2f}")
        else:
            reasons.append(f"RR={rr:.2f}不足")
    else:
        reasons.append("无真实压力不虚构15%目标")
    distance_line_pct = pct_change(last_close, line)
    if 0.0 <= distance_line_pct <= 8.0:
        score += 2.0; reasons.append(f"距核心线{distance_line_pct:.1f}%")
    elif distance_line_pct > 18.0:
        score -= 4.0; reasons.append(f"距核心线{distance_line_pct:.1f}%过远")
    ok = bool(risk_pct <= 10.5 and target_price > 0 and rr >= 1.35 and space_pct >= 8.0 and distance_line_pct <= 18.0)
    score = clamp(score, -8, 20)
    confirm = f"放量收盘站稳{max(line, support_price):.2f}且不跌破{defense_price:.2f}"
    giveup = f"收盘跌破{defense_price:.2f}或放量长阴跌回核心线{line:.2f}下方"
    return {"score": rd(score, 2), "ok": ok, "detail": "；".join(reasons), "defense_price": rd(defense_price, 3), "defense_type": defense_type, "structure_key_price": rd(structure_key, 3), "defense_distance_pct": rd(risk_pct, 2), "target_price": rd(target_price, 3), "target_type": target_type, "space_pct": rd(space_pct, 2), "rr": rd(rr, 2), "distance_line_pct": rd(distance_line_pct, 2), "confirm_condition": confirm, "giveup_condition": giveup}


def evaluate_risk_counterevidence(d: pd.DataFrame, bidx: int, line: float, fund: Dict[str, Any], trade: Dict[str, Any]) -> Dict[str, Any]:
    if d.empty or bidx <= 0 or line <= 0:
        return {"penalty": 40.0, "block": True, "level": "高", "detail": "风险样本不足"}
    post = d.iloc[bidx:].copy().reset_index(drop=True)
    last = d.iloc[-1]
    penalty = 0.0
    block = False
    reasons: List[str] = []
    below_count = int((post["close"] < line * 0.992).sum())
    last3_below = int((post.tail(3)["close"] < line * 0.992).sum())
    if sf(last.close) < line * 0.988 or last3_below >= 2:
        penalty += 35.0; block = True; reasons.append("突破失败跌回线下")
    elif below_count > 0:
        penalty += min(12.0, below_count * 3.0); reasons.append(f"突破后跌回线下{below_count}次")
    last20 = d.tail(20).copy()
    long_bear = (last20["close"] < last20["open"]) & (((last20["open"] - last20["close"]) / last20["close"].shift(1).replace(0, np.nan)) >= 0.035) & (last20["volume"] >= last20["vol_ma20"].fillna(last20["volume"].median()) * 1.35)
    lb_cnt = int(long_bear.sum())
    if lb_cnt >= 2:
        penalty += 12.0; reasons.append(f"近20日放量长阴{lb_cnt}次")
    elif lb_cnt == 1:
        penalty += 5.0; reasons.append("近20日有放量长阴")
    if bool(fund.get("stall")):
        penalty += 18.0; reasons.append("放量滞涨")
    recent20_pct = pct_change(sf(d.iloc[-1].close), sf(last20.iloc[0].close)) if len(last20) >= 2 else 0.0
    if recent20_pct > DEEP_RECENT_HOT_20D_PCT:
        penalty += 8.0; reasons.append(f"近20日涨幅{recent20_pct:.1f}%过热")
    max_post_high = sf(post["high"].max())
    drawdown = pct_change(sf(last.close), max_post_high) if max_post_high > 0 else 0.0
    if drawdown < -12.0:
        penalty += 7.0; reasons.append(f"突破后回撤{drawdown:.1f}%")
    if sf(trade.get("defense_distance_pct")) > 12.0:
        penalty += 8.0; reasons.append("防守距离过远")
    level = "高" if block or penalty >= 25 else "中" if penalty >= 10 else "低" if penalty > 0 else "无"
    return {"penalty": rd(clamp(penalty, 0, 45), 2), "block": bool(block), "level": level, "detail": "；".join(reasons) or "无明显风险反证", "recent20_pct": rd(recent20_pct, 2), "post_drawdown_pct": rd(drawdown, 2), "below_line_count": below_count}


def arbitrate_hypotheses(major: Dict[str, Any], supply: Dict[str, Any], eve: Dict[str, Any], pullback: Dict[str, Any], fund: Dict[str, Any], activity: Dict[str, Any], timing: Dict[str, Any], trade: Dict[str, Any], risk: Dict[str, Any]) -> Dict[str, Any]:
    # 母机会分只取主假设，其他证据进入封顶共振，杜绝同源线性堆分。
    hypotheses = []
    hypotheses.append(("大周期价值重估", sf(major.get("score")) * 2.6 + sf(pullback.get("score")) * 0.9 + sf(fund.get("score")) * 0.45))
    hypotheses.append(("供应吸收后突破", sf(supply.get("score")) * 3.1 + sf(fund.get("score")) * 0.75 + sf(timing.get("score")) * 0.65))
    hypotheses.append(("爆发前夜启动", sf(eve.get("score")) * 3.0 + sf(timing.get("score")) * 0.90 + sf(activity.get("score")) * 0.45))
    hypotheses.append(("回踩承接二买", sf(pullback.get("score")) * 3.2 + sf(fund.get("score")) * 0.65 + sf(trade.get("score")) * 0.45))
    hypotheses.append(("资金二次确认", max(0.0, sf(fund.get("score"))) * 3.0 + sf(pullback.get("score")) * 0.75 + sf(supply.get("score")) * 0.45))
    hypotheses = [(name, clamp(score, 0, 60)) for name, score in hypotheses]
    hypotheses_sorted = sorted(hypotheses, key=lambda x: x[1], reverse=True)
    primary_type, primary_score = hypotheses_sorted[0]
    second_score = hypotheses_sorted[1][1] if len(hypotheses_sorted) > 1 else 0.0
    dominance = primary_score - second_score
    evidence_scores = [sf(major.get("score")), sf(supply.get("score")), sf(eve.get("score")), sf(pullback.get("score")), max(0.0, sf(fund.get("score"))), max(0.0, sf(activity.get("score"))), sf(timing.get("score"))]
    # 去掉主假设中的主来源后仍只给封顶共振。
    resonance_score = clamp(sum(sorted(evidence_scores, reverse=True)[1:4]) * 0.55, 0, 20)
    trade_score = clamp(sf(trade.get("score")), 0, 20)
    raw = primary_score + resonance_score + trade_score - sf(risk.get("penalty"))
    if primary_score < 30:
        raw = min(raw, 64.0)
    if dominance < 4.0:
        raw = min(raw, 76.0)
    if not bool(trade.get("ok")):
        raw = min(raw, 74.0)
    if bool(risk.get("block")):
        raw = min(raw, 49.0)
    final_score = clamp(raw, 0, 100)
    grade = deep_grade(final_score)
    if bool(risk.get("block")):
        action = "剔除｜突破失败或硬风险反证"
        pool = "剔除"
    elif final_score >= 88 and bool(trade.get("ok")) and primary_score >= 42:
        action = "正式买入池｜标准仓位候选"
        pool = "正式候选"
    elif final_score >= 78 and bool(trade.get("ok")) and primary_score >= 38:
        action = "正式买入池｜轻仓/确认候选"
        pool = "正式候选"
    elif "回踩" in str(pullback.get("type")) and not bool(trade.get("ok")):
        action = "观察等待｜交易定价未过闸"
        pool = "观察候选"
    elif final_score >= 68:
        action = "观察等待｜需要二次确认"
        pool = "观察候选"
    elif sf(trade.get("distance_line_pct")) > 18:
        action = "降级｜突破过远不追"
        pool = "低优先级"
    else:
        action = "低优先级观察"
        pool = "低优先级"
    return {"primary_setup_type": primary_type, "primary_setup_score": rd(primary_score, 2), "second_setup_score": rd(second_score, 2), "primary_dominance": rd(dominance, 2), "resonance_score": rd(resonance_score, 2), "trade_score": rd(trade_score, 2), "final_score": rd(final_score, 2), "grade": grade, "action": action, "pool": pool, "hypothesis_scores": ";".join([f"{n}:{rd(v,1)}" for n, v in hypotheses_sorted])}


def deep_status_and_score(code: str, name: str, df: pd.DataFrame, line: float, core: Dict[str, Any], br: Dict[str, Any]) -> Dict[str, Any]:
    d = add_deep_indicators(df)
    L = sf(line)
    empty = {
        "deep_score": 0.0, "deep_grade": "D", "deep_state": "数据不足", "trade_action": "剔除｜数据不足",
        "deep_positive_reasons": "", "deep_negative_reasons": "数据不足", "risk_flags": "数据不足",
    }
    if d.empty or L <= 0 or not br.get("hit"):
        return empty
    br_date = ss(br.get("date"))
    idxs = d.index[d["date"].astype(str) == br_date].tolist()
    if not idxs:
        return {**empty, "deep_negative_reasons": "找不到突破日期", "risk_flags": "突破日期缺失"}
    bidx = int(idxs[-1])
    if bidx <= 0 or bidx >= len(d):
        return {**empty, "deep_negative_reasons": "突破位置异常", "risk_flags": "突破位置异常"}
    last = d.iloc[-1]
    b = d.iloc[bidx]
    prev = d.iloc[bidx - 1]
    last_close = sf(last.close)
    bfeat = kline_features(b, prev_close=sf(prev.close), line=L)
    events = detect_event_tags(d, bidx)
    major = evaluate_major_cycle_pricing(d, last_close)
    supply = evaluate_supply_absorption(d, bidx, L)
    eve = evaluate_explosion_eve(d, bidx)
    pullback = evaluate_pullback_acceptance(d, bidx, L)
    fund = evaluate_fund_behavior(d, bidx)
    activity = evaluate_activity(d, code)
    timing = evaluate_time_maturity(d, bidx)
    trade = evaluate_trade_pricing(d, bidx, L, sf(pullback.get("support_price")))
    risk = evaluate_risk_counterevidence(d, bidx, L, fund, trade)
    arb = arbitrate_hypotheses(major, supply, eve, pullback, fund, activity, timing, trade, risk)

    pos = []
    for item in [major, supply, eve, pullback, fund, activity, timing]:
        if sf(item.get("score")) > 0 and ss(item.get("detail")):
            pos.append(ss(item.get("detail")))
    neg = []
    if not bool(trade.get("ok")):
        neg.append("交易定价未过闸：" + ss(trade.get("detail")))
    if sf(risk.get("penalty")) > 0:
        neg.append("风险反证：" + ss(risk.get("detail")))
    if bool(fund.get("stall")):
        neg.append("资金行为为放量滞涨")

    return {
        "deep_score": arb["final_score"],
        "deep_grade": arb["grade"],
        "deep_state": arb["primary_setup_type"],
        "trade_action": arb["action"],
        "deep_pool": arb["pool"],
        "deep_positive_reasons": "；".join(pos[:8]) or "无有效母机会证据",
        "deep_negative_reasons": "；".join(neg[:6]) or "暂无明显扣分项",
        "risk_flags": ss(risk.get("detail")) if sf(risk.get("penalty")) > 0 else "无",
        "current_close": rd(last_close, 3),
        "distance_line_pct": trade.get("distance_line_pct", 0),
        "defense_price": trade.get("defense_price", 0),
        "defense_type": trade.get("defense_type", ""),
        "defense_distance_pct": trade.get("defense_distance_pct", 0),
        "target_price": trade.get("target_price", 0),
        "target_type": trade.get("target_type", ""),
        "space_pct": trade.get("space_pct", 0),
        "rr_estimate": trade.get("rr", 0),
        "breakout_volume_ratio": fund.get("volume_ratio", 0),
        "breakout_entity_above_line_ratio": rd(sf(bfeat.get("entity_above_line_ratio")), 3),
        "breakout_close_pos": rd(sf(bfeat.get("close_pos")), 3),
        "breakout_upper_shadow_ratio": rd(sf(bfeat.get("upper_shadow_ratio")), 3),
        "days_since_breakout": timing.get("days_since_breakout", 0),
        "recent5_pct": pct_change(last_close, sf(d.tail(5).iloc[0].close)) if len(d.tail(5)) >= 2 else 0.0,
        "recent10_pct": pct_change(last_close, sf(d.tail(10).iloc[0].close)) if len(d.tail(10)) >= 2 else 0.0,
        "recent20_pct": risk.get("recent20_pct", 0),
        "post_drawdown_pct": risk.get("post_drawdown_pct", 0),
        "primary_setup_type": arb["primary_setup_type"],
        "primary_setup_score": arb["primary_setup_score"],
        "primary_dominance": arb["primary_dominance"],
        "hypothesis_scores": arb["hypothesis_scores"],
        "score_major_cycle": major.get("score", 0),
        "major_cycle_type": major.get("type", ""),
        "major_cycle_detail": major.get("detail", ""),
        "score_supply_absorption": supply.get("score", 0),
        "supply_absorption_type": supply.get("type", ""),
        "supply_absorption_detail": supply.get("detail", ""),
        "score_explosion_eve": eve.get("score", 0),
        "explosion_eve_type": eve.get("type", ""),
        "explosion_eve_detail": eve.get("detail", ""),
        "score_pullback_acceptance": pullback.get("score", 0),
        "pullback_acceptance_type": pullback.get("type", ""),
        "pullback_acceptance_detail": pullback.get("detail", ""),
        "support_type": pullback.get("support_type", ""),
        "support_price": pullback.get("support_price", 0),
        "break_body_mid": pullback.get("break_body_mid", 0),
        "break_body_bottom": pullback.get("break_body_bottom", 0),
        "score_fund_behavior": fund.get("score", 0),
        "fund_behavior_type": fund.get("type", ""),
        "fund_behavior_detail": fund.get("detail", ""),
        "score_activity": activity.get("score", 0),
        "activity_type": activity.get("type", ""),
        "activity_detail": activity.get("detail", ""),
        "limitup_100d_count": activity.get("limitup_100d_count", 0),
        "big_up_100d_count": activity.get("big_up_100d_count", 0),
        "gap_100d_count": activity.get("gap_100d_count", 0),
        "score_time_maturity": timing.get("score", 0),
        "time_maturity_type": timing.get("type", ""),
        "time_maturity_detail": timing.get("detail", ""),
        "score_trade_pricing": trade.get("score", 0),
        "trade_pricing_ok": bool(trade.get("ok")),
        "trade_pricing_detail": trade.get("detail", ""),
        "risk_penalty": risk.get("penalty", 0),
        "risk_level": risk.get("level", ""),
        "risk_block": bool(risk.get("block")),
        "event_tags": events.get("tags", ""),
        "confirm_condition": trade.get("confirm_condition", ""),
        "giveup_condition": trade.get("giveup_condition", ""),
        # 旧字段置零：核心线已经是海选门票，不再参与深度总分。
        "score_core_line": 0.0,
        "score_breakout_k": rd(sf(br.get("quality")), 2),
        "score_post_state": pullback.get("score", 0),
        "score_position_rr": trade.get("score", 0),
        "score_volume_heat": fund.get("score", 0),
        "score_risk_control": max(0.0, 10.0 - sf(risk.get("penalty")) / 3.0),
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
    print(f"progress_color_enabled={progress_color_enabled()} 条形进度=True", flush=True)
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
