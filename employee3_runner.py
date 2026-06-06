# -*- coding: utf-8 -*-
from __future__ import annotations

"""三号员工：双线突破海选器 V2

用途：
1）读取现有日线前复权缓存；
2）全市场逐票计算历史核心共振线；
3）全市场逐票计算最近1000个交易日内的千日共振触发线；
4）筛选最近20个交易日内日K线从下往上高质量突破任意一条线的股票；
5）对所有海选命中股票直接做完整深度筛选，最终只推送深度质量最好的前5只。

约束：
- 不改 workflow 链路，不写入生产凭证，不运行时替换函数；
- 实体接受不淘汰历史核心共振线或千日共振触发线，只输出状态字段；
- 不设置300只预选池，海选命中后直接完整深度评测。
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

BOOT = "EMPLOYEE3_DUAL_LINE_BREAKOUT_DEEP_SCREEN_V3_20260606"
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
# 三号员工只允许两种海选线名：历史核心共振线、千日共振触发线。
THOUSAND_DAY_LOOKBACK = int(os.getenv("EMPLOYEE3_THOUSAND_DAY_LOOKBACK", "1000"))
LINE_TOP_CANDIDATE_LIMIT = int(os.getenv("EMPLOYEE3_LINE_TOP_CANDIDATE_LIMIT", "12"))
ASSESSMENT_LINE_MAX_ABOVE_PCT = float(os.getenv("EMPLOYEE3_ASSESSMENT_LINE_MAX_ABOVE_PCT", "0.18"))
ASSESSMENT_LINE_MAX_BELOW_PCT = float(os.getenv("EMPLOYEE3_ASSESSMENT_LINE_MAX_BELOW_PCT", "0.025"))
ASSESSMENT_LINE_MIN_SPACE_PCT = float(os.getenv("EMPLOYEE3_ASSESSMENT_LINE_MIN_SPACE_PCT", "6.0"))
ASSESSMENT_LINE_MIN_RR = float(os.getenv("EMPLOYEE3_ASSESSMENT_LINE_MIN_RR", "1.05"))
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

# 正式报告口径：海选层只负责召回，不输出评级；所有海选命中票全部完成深度评分。
# Telegram/Markdown 只输出最终深度精选 TopN；A级/S级不足时，用未硬剔除的最高分票补足并明示。
# 全量海选与全量深度字段继续写入 CSV/JSON，便于审计和复盘。
DEEP_FINAL_PICK_LIMIT = int(os.getenv("EMPLOYEE3_DEEP_FINAL_PICK_LIMIT", os.getenv("EMPLOYEE3_DEEP_TOP_REPORT_LIMIT", "5")))
DEEP_WATCH_REPORT_LIMIT = int(os.getenv("EMPLOYEE3_DEEP_WATCH_REPORT_LIMIT", "3"))
DEEP_MIN_FORMAL_SCORE = float(os.getenv("EMPLOYEE3_DEEP_MIN_FORMAL_SCORE", "78"))
DEEP_FORMAL_GRADES = tuple(x.strip() for x in os.getenv("EMPLOYEE3_DEEP_FORMAL_GRADES", "S,A").split(",") if x.strip())


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



def rank_key_historical_core_resonance_line(x: Dict[str, Any]) -> Tuple[float, int, int, float]:
    return (sf(x.get("net_score")), int(sf(x.get("effective_resonance_count"))), int(sf(x.get("volume_resonance_count"))), -sf(x.get("line")))


def rank_key_thousand_day_resonance_trigger_line(x: Dict[str, Any]) -> Tuple[int, int, float, float]:
    return (int(sf(x.get("effective_resonance_count"))), int(sf(x.get("volume_resonance_count"))), sf(x.get("net_score")), -sf(x.get("line")))


def choose_resonance_line(df: pd.DataFrame, line_label: str, lookback_days: int = 0, rank_mode: str = "historical") -> Dict[str, Any]:
    d = normalize_hist(df)
    if lookback_days > 0 and len(d) > lookback_days:
        d = d.tail(lookback_days).reset_index(drop=True)
    raw_k = aggregate_bars(d, AGG_WINDOW)
    if raw_k.empty or len(raw_k) < 3:
        return {"line": None, "level": "数据不足", "reason": f"{line_label}历史K线不足", "line_label": line_label}
    completed = raw_k.iloc[:-1].reset_index(drop=True)
    if completed.empty:
        return {"line": None, "level": "数据不足", "reason": f"{line_label}无已完成聚合K", "line_label": line_label}
    sources = line_candidate_sources(completed)
    if not sources:
        return {"line": None, "level": "未识别", "reason": f"{line_label}未识别到候选价位", "line_label": line_label}
    scored_all = batch_score_lines(completed, sources)
    scored = [x for x in scored_all if ss(x.get("line_type")) == "core_line"]
    if not scored:
        return {"line": None, "level": "未识别", "reason": f"{line_label}未识别到有效共振线", "line_label": line_label}
    band_winners = [max(g, key=rank_key_historical_core_resonance_line) for g in group_by_band(scored)]
    ranked = sorted(
        band_winners,
        key=rank_key_thousand_day_resonance_trigger_line if rank_mode == "thousand_day" else rank_key_historical_core_resonance_line,
        reverse=True,
    )
    best = dict(ranked[0])
    best["line_label"] = line_label
    best["lookback_days"] = int(lookback_days) if lookback_days > 0 else 0
    best["rank_mode"] = rank_mode
    top_candidates = []
    for item in ranked[:max(3, LINE_TOP_CANDIDATE_LIMIT)]:
        obj = {k: v for k, v in item.items() if k not in {"top_candidates", "excluded_current_bar"}}
        obj["line_label"] = line_label
        obj["lookback_days"] = int(lookback_days) if lookback_days > 0 else 0
        obj["rank_mode"] = rank_mode
        top_candidates.append(obj)
    best["top_candidates"] = top_candidates
    best["all_candidates_count"] = len(sources)
    best["effective_candidates_count"] = len(scored)
    best["band_candidates_count"] = len(band_winners)
    best["excluded_current_bar"] = {k: (rd(v, 3) if isinstance(v, (int, float)) else v) for k, v in raw_k.iloc[-1].to_dict().items()}
    return best


def choose_historical_core_resonance_line(df: pd.DataFrame) -> Dict[str, Any]:
    return choose_resonance_line(df, "历史核心共振线", lookback_days=0, rank_mode="historical")


def choose_thousand_day_resonance_trigger_line(df: pd.DataFrame) -> Dict[str, Any]:
    return choose_resonance_line(df, "千日共振触发线", lookback_days=THOUSAND_DAY_LOOKBACK, rank_mode="thousand_day")


def first_real_pressure_before_breakout(d: pd.DataFrame, bidx: int, last_close: float) -> Dict[str, Any]:
    if d.empty or bidx <= 0 or last_close <= 0:
        return {"target_price": 0.0, "space_pct": 0.0, "pressure_found": False}
    pre = d.iloc[max(0, bidx - 520):bidx].copy()
    highs = pd.to_numeric(pre.get("high", pd.Series(dtype=float)), errors="coerce").dropna()
    pressures = sorted([float(x) for x in highs.tolist() if float(x) >= last_close * 1.025])
    target = pressures[0] if pressures else 0.0
    space = pct_change(target, last_close) if target > 0 else 0.0
    return {"target_price": rd(target, 3), "space_pct": rd(space, 2), "pressure_found": bool(target > 0)}


def score_assessment_line_option(df: pd.DataFrame, line_info: Dict[str, Any], breakout: Dict[str, Any], line_type: str) -> Dict[str, Any]:
    d = add_deep_indicators(df)
    L = sf(line_info.get("line"))
    out = {
        "line_type": line_type,
        "line": rd(L, 3),
        "line_info": line_info,
        "breakout": breakout,
        "assessment_score": -999.0,
        "assessment_reason": "未命中高质量突破",
        "assessment_distance_pct": 0.0,
        "assessment_risk_pct": 0.0,
        "assessment_space_pct": 0.0,
        "assessment_rr": 0.0,
        "assessment_defense_price": 0.0,
    }
    if d.empty or L <= 0 or not bool(breakout.get("hit")):
        return out
    br_date = ss(breakout.get("date"))
    idxs = d.index[d["date"].astype(str) == br_date].tolist()
    if not idxs:
        out["assessment_reason"] = "突破日期定位失败"
        return out
    bidx = int(idxs[-1])
    if bidx <= 0 or bidx >= len(d):
        out["assessment_reason"] = "突破位置异常"
        return out
    b = d.iloc[bidx]
    last = d.iloc[-1]
    last_close = sf(last.close)
    if last_close <= 0:
        out["assessment_reason"] = "当前收盘无效"
        return out
    distance = pct_change(last_close, L)
    post = d.iloc[bidx:].copy().reset_index(drop=True)
    below_after = int((post["close"] < L * 0.992).sum())
    last3_below = int((post.tail(3)["close"] < L * 0.992).sum())
    body_bottom = min(sf(b.open), sf(b.close))
    support_floor = max(0.0, min(x for x in [L, body_bottom] if x > 0))
    defense = support_floor * (1.0 - DEEP_DEFENSE_BUFFER_PCT) if support_floor > 0 else L * 0.985
    risk_pct = pct_change(last_close, defense) if defense > 0 else 0.0
    pressure = first_real_pressure_before_breakout(d, bidx, last_close)
    space_pct = sf(pressure.get("space_pct"))
    rr = space_pct / risk_pct if risk_pct > 0 and space_pct > 0 else 0.0
    score = 0.0
    reasons: List[str] = []
    bq = sf(breakout.get("quality"))
    score += min(18.0, bq * 3.0)
    reasons.append(f"突破质量{bq:.2f}")
    hit = int(sf(line_info.get("effective_resonance_count")))
    vol_hit = int(sf(line_info.get("volume_resonance_count")))
    net = sf(line_info.get("net_score"))
    score += min(16.0, hit * 1.8 + vol_hit * 1.2 + max(0.0, net) * 0.25)
    reasons.append(f"{line_type}共振{hit}次/带量{vol_hit}次")
    if distance < -ASSESSMENT_LINE_MAX_BELOW_PCT * 100.0 or last3_below >= 2:
        score -= 35.0
        reasons.append("突破后重新跌回线下")
    elif distance <= 6.0:
        score += 14.0
        reasons.append(f"距线{distance:.1f}%")
    elif distance <= 12.0:
        score += 8.0
        reasons.append(f"距线{distance:.1f}%")
    elif distance <= ASSESSMENT_LINE_MAX_ABOVE_PCT * 100.0:
        score += 2.0
        reasons.append(f"距线{distance:.1f}%偏远")
    else:
        score -= 12.0
        reasons.append(f"距线{distance:.1f}%过远")
    if risk_pct <= 6.0:
        score += 8.0
        reasons.append(f"防守距离{risk_pct:.1f}%")
    elif risk_pct <= 10.5:
        score += 4.0
        reasons.append(f"防守距离{risk_pct:.1f}%")
    else:
        score -= 6.0
        reasons.append(f"防守距离{risk_pct:.1f}%偏远")
    if bool(pressure.get("pressure_found")):
        if space_pct >= 18.0:
            score += 8.0
            reasons.append(f"上方空间{space_pct:.1f}%")
        elif space_pct >= ASSESSMENT_LINE_MIN_SPACE_PCT:
            score += 5.0
            reasons.append(f"上方空间{space_pct:.1f}%")
        else:
            score -= 7.0
            reasons.append(f"第一压力太近{space_pct:.1f}%")
        if rr >= 2.0:
            score += 8.0
            reasons.append(f"RR={rr:.2f}")
        elif rr >= ASSESSMENT_LINE_MIN_RR:
            score += 4.0
            reasons.append(f"RR={rr:.2f}")
        else:
            score -= 6.0
            reasons.append(f"RR={rr:.2f}不足")
    else:
        reasons.append("无真实上方压力，不虚构空间")
    if below_after > 0:
        score -= min(10.0, below_after * 3.0)
        reasons.append(f"突破后跌回线下{below_after}次")
    out.update({
        "assessment_score": rd(score, 3),
        "assessment_reason": "；".join(reasons),
        "assessment_distance_pct": rd(distance, 2),
        "assessment_risk_pct": rd(risk_pct, 2),
        "assessment_space_pct": rd(space_pct, 2),
        "assessment_rr": rd(rr, 2),
        "assessment_defense_price": rd(defense, 3),
    })
    return out


def select_primary_assessment_line(df: pd.DataFrame, options: List[Dict[str, Any]]) -> Dict[str, Any]:
    scored = [score_assessment_line_option(df, x["line_info"], x["breakout"], x["line_type"]) for x in options]
    if not scored:
        return {}
    return max(scored, key=lambda x: (sf(x.get("assessment_score")), sf(x.get("breakout", {}).get("quality")), -abs(sf(x.get("assessment_distance_pct")))))

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



def build_cycle_deep_df(df: pd.DataFrame, freq: str, min_daily_rows: int = 120) -> pd.DataFrame:
    d = add_deep_indicators(df)
    if d.empty or len(d) < min_daily_rows:
        return pd.DataFrame()
    x = d.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce")
    x = x.dropna(subset=["date"]).set_index("date").sort_index()
    if x.empty:
        return pd.DataFrame()
    k = pd.DataFrame()
    k["open"] = x["open"].resample(freq).first()
    k["high"] = x["high"].resample(freq).max()
    k["low"] = x["low"].resample(freq).min()
    k["close"] = x["close"].resample(freq).last()
    k["volume"] = x["volume"].resample(freq).sum()
    k["amount"] = x["amount"].resample(freq).sum() if "amount" in x.columns else 0.0
    k = k.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index()
    if k.empty:
        return k
    c = pd.to_numeric(k["close"], errors="coerce")
    v = pd.to_numeric(k["volume"], errors="coerce").fillna(0.0)
    k["ma3"] = c.rolling(3, min_periods=2).mean()
    k["ma6"] = c.rolling(6, min_periods=3).mean()
    k["ma12"] = c.rolling(12, min_periods=6).mean()
    k["ma20"] = c.rolling(20, min_periods=10).mean()
    k["ma24"] = c.rolling(24, min_periods=12).mean()
    ma_pack = k[["ma3", "ma6", "ma12", "ma24"]]
    ma_valid_count = ma_pack.notna().sum(axis=1)
    k["bbi"] = ma_pack.mean(axis=1, skipna=True)
    k.loc[ma_valid_count < 2, "bbi"] = np.nan
    k["boll_mid"] = k["ma20"]
    k["boll_std"] = c.rolling(20, min_periods=10).std()
    k["boll_upper"] = k["boll_mid"] + 2.0 * k["boll_std"]
    k["boll_lower"] = k["boll_mid"] - 2.0 * k["boll_std"]
    k["boll_width"] = (k["boll_upper"] - k["boll_lower"]) / k["boll_mid"].replace(0, np.nan)
    ma_max = k[["ma3", "ma6", "ma12", "ma24"]].max(axis=1)
    ma_min = k[["ma3", "ma6", "ma12", "ma24"]].min(axis=1)
    k["bbi_dispersion"] = (ma_max - ma_min) / k["bbi"].replace(0, np.nan)
    k["mid"] = k["bbi"].where(k["bbi"].notna(), k["boll_mid"])
    k["body_pct"] = (k["close"] - k["open"]) / k["open"].replace(0, np.nan)
    k["body_ratio"] = (k["close"] - k["open"]).abs() / (k["high"] - k["low"]).replace(0, np.nan)
    k["close_pos"] = (k["close"] - k["low"]) / (k["high"] - k["low"]).replace(0, np.nan)
    k["vol_ma6"] = v.rolling(6, min_periods=3).mean()
    k["vol_ma12"] = v.rolling(12, min_periods=4).mean()
    k["vol_pct_rank_60"] = v.rolling(60, min_periods=12).apply(lambda a: pd.Series(a).rank(pct=True).iloc[-1], raw=False)
    return k


def build_monthly_deep_df(df: pd.DataFrame) -> pd.DataFrame:
    return build_cycle_deep_df(df, "ME", 120)


def build_quarterly_deep_df(df: pd.DataFrame) -> pd.DataFrame:
    return build_cycle_deep_df(df, "QE-DEC", 360)


def build_yearly_deep_df(df: pd.DataFrame) -> pd.DataFrame:
    return build_cycle_deep_df(df, "YE-DEC", 720)

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




def cycle_mid_repair_params(cycle: str) -> Dict[str, Any]:
    c = ss(cycle).lower()
    if c == "quarter":
        return {
            "cycle": "quarter", "label": "季线", "lookback": 20, "min_len": 8, "min_valid": 7,
            "stand_max_periods": 4, "support_min": 2, "support_max": 4,
            "close_above": 1.005, "soft_above": 1.001, "hold_close": 0.992,
            "breakdown": 0.985, "touch": 1.018, "score_cap": 12.0,
        }
    if c == "year":
        return {
            "cycle": "year", "label": "年线", "lookback": 8, "min_len": 6, "min_valid": 5,
            "stand_max_periods": 2, "support_min": 1, "support_max": 2,
            "close_above": 1.005, "soft_above": 1.001, "hold_close": 0.990,
            "breakdown": 0.982, "touch": 1.020, "score_cap": 12.0,
        }
    return {
        "cycle": "month", "label": "月线", "lookback": 36, "min_len": 24, "min_valid": 18,
        "stand_max_periods": 6, "support_min": 3, "support_max": 5,
        "close_above": 1.005, "soft_above": 1.002, "hold_close": 0.992,
        "breakdown": 0.985, "touch": 1.018, "score_cap": 12.0,
    }

def evaluate_cycle_mid_repair(k: pd.DataFrame, cycle: str = "month") -> Dict[str, Any]:
    params = cycle_mid_repair_params(cycle)
    label = params["label"]
    empty = {
        "score": 0.0,
        "type": "无",
        "detail": f"{label}中轨修复样本不足",
        "anchor_price": 0.0,
        "tight_date": "",
        "tight_idx": -1,
        "repair_stage": "NO_SAMPLE",
        "cycle": params["cycle"],
        "label": label,
        "sample_count": 0,
        "available": False,
        "first_above_date": "",
        "first_above_periods_after_tight": 0,
        "support_periods": 0,
        "breakdown_date": "",
        "current_mid": 0.0,
        "current_close": 0.0,
        "close_mid_ratio": 0.0,
        "repair_volume_ratio": 0.0,
    }
    if k is None or k.empty or len(k) < int(params["min_len"]):
        out = dict(empty)
        out["sample_count"] = 0 if k is None or k.empty else int(len(k))
        out["detail"] = f"{label}中轨修复样本不足：仅{out['sample_count']}根，至少需要{int(params['min_len'])}根"
        return out

    x = k.copy().reset_index(drop=True)
    if "mid" not in x.columns:
        bbi = x["bbi"] if "bbi" in x.columns else pd.Series(np.nan, index=x.index)
        boll_mid = x["boll_mid"] if "boll_mid" in x.columns else pd.Series(np.nan, index=x.index)
        x["mid"] = bbi.where(pd.notna(bbi), boll_mid)
    for col in ["open", "high", "low", "close", "volume", "mid", "boll_width", "bbi_dispersion", "body_pct", "body_ratio", "close_pos", "vol_ma6", "vol_ma12"]:
        if col not in x.columns:
            x[col] = np.nan
        x[col] = pd.to_numeric(x[col], errors="coerce")

    cur = x.iloc[-1]
    cur_mid = sf(cur.get("mid", 0.0))
    cur_close = sf(cur.get("close", 0.0))
    close_mid_ratio = cur_close / cur_mid if cur_mid > 0 else 0.0
    if cur_mid <= 0 or cur_close <= 0:
        out = dict(empty)
        out["detail"] = f"当前{label}中轨无效"
        return out

    lookback = min(int(params["lookback"]), len(x))
    recent = x.tail(lookback).copy()
    valid = recent[(recent["mid"] > 0) & recent["close"].notna()].copy()
    min_valid = int(params.get("min_valid", max(8, min(int(params["min_len"]) - 4, int(params["lookback"]) // 2))))
    if len(valid) < min_valid:
        out = dict(empty)
        out["detail"] = f"可用{label}中轨样本不足"
        out["anchor_price"] = rd(cur_mid, 3)
        out["current_mid"] = rd(cur_mid, 3)
        out["current_close"] = rd(cur_close, 3)
        out["close_mid_ratio"] = rd(close_mid_ratio, 4)
        out["sample_count"] = int(len(x))
        return out

    def pct_rank_series(sv: pd.Series) -> pd.Series:
        sv = pd.to_numeric(sv, errors="coerce")
        if sv.notna().sum() < 6:
            return pd.Series(np.nan, index=sv.index)
        return sv.rank(pct=True, method="average")

    bw_rank = pct_rank_series(valid["boll_width"])
    disp_rank = pct_rank_series(valid["bbi_dispersion"])
    if bw_rank.notna().any() and disp_rank.notna().any():
        tight_metric = bw_rank.fillna(1.0) * 0.65 + disp_rank.fillna(1.0) * 0.35
    elif bw_rank.notna().any():
        tight_metric = bw_rank.fillna(1.0)
    elif disp_rank.notna().any():
        tight_metric = disp_rank.fillna(1.0)
    else:
        out = dict(empty)
        out["detail"] = f"{label}缩口指标不可用"
        out["anchor_price"] = rd(cur_mid, 3)
        out["current_mid"] = rd(cur_mid, 3)
        out["current_close"] = rd(cur_close, 3)
        out["close_mid_ratio"] = rd(close_mid_ratio, 4)
        out["sample_count"] = int(len(x))
        return out

    tight_local_idx = int(tight_metric.idxmin())
    tight = x.loc[tight_local_idx]
    tight_date = ss(tight.get("date", ""))[:10]
    post = x.iloc[tight_local_idx + 1:].copy().reset_index(drop=False).rename(columns={"index": "orig_idx"})

    score = 0.0
    reasons: List[str] = []
    stage = "TIGHT_ONLY"
    tight_pct = sf(tight_metric.loc[tight_local_idx])
    if tight_pct <= 0.15:
        score += 2.0
        reasons.append(f"{label}{lookback}期内最紧缩口{tight_date}，缩口分位{tight_pct:.0%}")
    elif tight_pct <= 0.30:
        score += 1.0
        reasons.append(f"{label}{lookback}期内相对缩口{tight_date}，缩口分位{tight_pct:.0%}")
    else:
        reasons.append(f"{label}{lookback}期内缩口不极致{tight_date}，分位{tight_pct:.0%}")

    first_above_date = ""
    first_periods_after_tight = 0
    support_periods = 0
    breakdown_date = ""
    repair_volume_ratio = 0.0

    if post.empty or len(post) < 2:
        typ = f"{label}缩口观察" if score > 0 else "无"
        return {
            **dict(empty), "score": rd(clamp(score, 0, params["score_cap"]), 2), "type": typ,
            "detail": "；".join(reasons), "anchor_price": rd(cur_mid, 3), "tight_date": tight_date,
            "tight_idx": tight_local_idx, "repair_stage": stage, "current_mid": rd(cur_mid, 3),
            "current_close": rd(cur_close, 3), "close_mid_ratio": rd(close_mid_ratio, 4),
            "sample_count": int(len(x)), "available": True,
        }

    post["hard_above_mid"] = post["close"] >= post["mid"] * float(params["close_above"])
    post["soft_above_mid"] = post["close"] >= post["mid"] * float(params["soft_above"])
    post["below_mid"] = post["close"] < post["mid"] * float(params["breakdown"])
    post["touch_mid"] = post["low"] <= post["mid"] * float(params["touch"])
    post["bull_break_mid"] = (
        (post["close"] > post["open"])
        & post["hard_above_mid"]
        & (post["close_pos"].fillna(0) >= 0.55)
    )

    break_rows = post[post["bull_break_mid"]]
    start_pos = -1
    if not break_rows.empty:
        br0 = break_rows.iloc[0]
        start_pos = int(post.index[post["orig_idx"] == br0["orig_idx"]][0])
        first_periods_after_tight = start_pos + 1
        first_above_date = ss(br0.get("date", ""))[:10]
        if first_periods_after_tight <= int(params["stand_max_periods"]):
            score += 2.0
            reasons.append(f"缩口后{first_periods_after_tight}{label[-1]}内有效站上中轨")
        else:
            score += 0.8
            reasons.append(f"缩口后{first_periods_after_tight}{label[-1]}才站上中轨，时间偏慢")
        close_strength = sf(br0.close) / max(sf(br0.mid), 1e-9) - 1.0
        if close_strength >= 0.018:
            score += 0.8
            reasons.append(f"首次站上收盘强度{close_strength:.1%}")
        elif close_strength >= 0.008:
            score += 0.4
            reasons.append(f"首次站上收盘强度{close_strength:.1%}")
        vol_base = sf(post.iloc[:start_pos]["volume"].tail(6).mean()) if start_pos > 0 else sf(x.iloc[max(0, tight_local_idx-6):tight_local_idx]["volume"].mean())
        first_vol_ratio = sf(br0.volume) / max(vol_base, 1e-9) if vol_base > 0 else 0.0
        if 1.05 <= first_vol_ratio <= 2.80:
            score += 0.6
            reasons.append(f"首次站上量能健康{first_vol_ratio:.2f}倍")
        elif first_vol_ratio > 3.50:
            score -= 0.4
            reasons.append(f"首次站上量能过猛{first_vol_ratio:.2f}倍")
        stage = "BREAK_MID"
    else:
        if cur_close >= cur_mid * float(params["close_above"]):
            score += 0.6
            reasons.append(f"当前站上{label}中轨，但缺少缩口后首次站上记录")
            stage = "CURRENT_ABOVE_ONLY"

    support_score = 0.0
    support_touched = 0
    if start_pos >= 0 and len(post) - start_pos >= int(params["support_min"]):
        for n in range(int(params["support_min"]), min(int(params["support_max"]), len(post) - start_pos) + 1):
            seg = post.iloc[start_pos:start_pos + n]
            close_hold = bool((seg["close"] >= seg["mid"] * float(params["hold_close"])).all())
            touch_count = int((seg["low"] <= seg["mid"] * float(params["touch"])).sum())
            if close_hold:
                s = 2.0
                if touch_count >= 1:
                    s += 0.7
                if n >= int(params["support_max"]):
                    s += 0.4
                if s > support_score:
                    support_score = s
                    support_periods = n
                    support_touched = touch_count
        if support_score > 0:
            score += support_score
            reasons.append(f"站上后{support_periods}{label[-1]}收盘不破中轨，影线回踩{support_touched}次")
            stage = "MID_SUPPORT_CONFIRMED"

    breakdown_pos = -1
    if support_periods > 0:
        after_support = post.iloc[start_pos + support_periods:].copy()
        bd = after_support[after_support["below_mid"]]
        if not bd.empty:
            breakdown_pos = int(post.index[post["orig_idx"] == bd.iloc[0]["orig_idx"]][0])
            breakdown_date = ss(bd.iloc[0].get("date", ""))[:10]
            score += 2.0
            reasons.append(f"站稳后曾有效跌破{label}中轨")
            stage = "BREAKDOWN_AFTER_SUPPORT"

    prev_close = sf(x.iloc[-2].close) if len(x) >= 2 else 0.0
    prev_mid = sf(x.iloc[-2].mid) if len(x) >= 2 else 0.0
    current_repair = bool(cur_close >= cur_mid * float(params["close_above"]))
    current_soft_repair = bool(cur_close >= cur_mid * float(params["soft_above"]))
    from_below = bool(prev_mid > 0 and prev_close < prev_mid * 0.995 and current_repair)

    vol_ref = sf(x.iloc[-7:-1]["volume"].mean()) if len(x) >= 8 else sf(x.iloc[:-1]["volume"].mean())
    repair_volume_ratio = sf(cur.volume) / max(vol_ref, 1e-9) if vol_ref > 0 else 0.0
    if current_repair:
        if breakdown_pos >= 0:
            score += 3.0
            stage = "FULL_MID_REPAIR"
            if from_below:
                score += 0.8
                reasons.append(f"当前{label}从中轨下方重新站回，闭环修复确认")
            else:
                reasons.append(f"当前{label}重新站回中轨，闭环修复成立")
        elif stage in {"MID_SUPPORT_CONFIRMED", "BREAK_MID"}:
            score += 1.0
            reasons.append(f"当前仍站在{label}中轨上方")
        elif from_below:
            score += 1.2
            reasons.append(f"当前{label}从中轨下方修复回中轨")
            stage = "SIMPLE_REPAIR"
        if close_mid_ratio >= 1.025:
            score += 0.8
            reasons.append(f"当前收盘高于中轨{close_mid_ratio - 1.0:.1%}")
        elif close_mid_ratio >= 1.010:
            score += 0.4
            reasons.append(f"当前收盘高于中轨{close_mid_ratio - 1.0:.1%}")
        if 0.90 <= repair_volume_ratio <= 2.50:
            score += 0.6
            reasons.append(f"回站量能健康{repair_volume_ratio:.2f}倍")
        elif repair_volume_ratio > 3.50:
            score -= 0.4
            reasons.append(f"回站量能过猛{repair_volume_ratio:.2f}倍")
    elif current_soft_repair:
        score += 0.5
        reasons.append(f"当前贴近/略站{label}中轨但未达到强确认")
    elif sf(cur.high) >= cur_mid and cur_close >= cur_mid * 0.985:
        score += 0.4
        reasons.append(f"当前触及{label}中轨但收盘未有效站稳")

    if breakdown_pos >= 0 and not current_repair:
        score = min(score, 6.0)
        reasons.append("跌破后尚未强势回站，封顶为观察")

    score = clamp(score, 0, float(params["score_cap"]))
    if stage == "FULL_MID_REPAIR" and score >= 9:
        typ = f"{label}中轨闭环修复"
    elif score >= 7:
        typ = f"{label}中轨修复"
    elif score >= 4:
        typ = f"{label}中轨观察"
    elif score > 0:
        typ = f"{label}弱修复"
    else:
        typ = "无"

    return {
        "score": rd(score, 2),
        "type": typ,
        "detail": "；".join(reasons) or f"无{label}中轨修复证据",
        "anchor_price": rd(cur_mid, 3),
        "tight_date": tight_date,
        "tight_idx": tight_local_idx,
        "repair_stage": stage,
        "cycle": params["cycle"],
        "first_above_date": first_above_date,
        "first_above_periods_after_tight": int(first_periods_after_tight),
        "support_periods": int(support_periods),
        "breakdown_date": breakdown_date,
        "current_mid": rd(cur_mid, 3),
        "current_close": rd(cur_close, 3),
        "close_mid_ratio": rd(close_mid_ratio, 4),
        "repair_volume_ratio": rd(repair_volume_ratio, 3),
        "sample_count": int(len(x)),
        "available": True,
    }


def evaluate_monthly_mid_repair(m: pd.DataFrame) -> Dict[str, Any]:
    return evaluate_cycle_mid_repair(m, "month")


def evaluate_multi_cycle_mid_repair(d: pd.DataFrame) -> Dict[str, Any]:
    month = evaluate_cycle_mid_repair(build_monthly_deep_df(d), "month")
    quarter = evaluate_cycle_mid_repair(build_quarterly_deep_df(d), "quarter")
    year = evaluate_cycle_mid_repair(build_yearly_deep_df(d), "year")
    repairs = {"month": month, "quarter": quarter, "year": year}

    base_weights = {"month": 0.55, "quarter": 0.32, "year": 0.18}
    available_keys = [k for k, r in repairs.items() if bool(r.get("available")) and sf(r.get("anchor_price")) > 0]
    if available_keys:
        total_weight = sum(base_weights[k] for k in available_keys)
        weighted = sum(sf(repairs[k].get("score")) * base_weights[k] for k in available_keys) / max(total_weight, 1e-9)
    else:
        weighted = 0.0

    full_count = sum(1 for k in available_keys if ss(repairs[k].get("repair_stage")) == "FULL_MID_REPAIR")
    repair_count = sum(1 for k in available_keys if sf(repairs[k].get("score")) >= 7)
    if full_count >= 2:
        weighted += 1.5
    elif repair_count >= 2:
        weighted += 0.8
    score = clamp(weighted, 0, 18)

    if available_keys:
        best_key, best = max(((k, repairs[k]) for k in available_keys), key=lambda kv: sf(kv[1].get("score")))
    else:
        best_key, best = max(repairs.items(), key=lambda kv: sf(kv[1].get("sample_count")))

    details = []
    unavailable = []
    for key, r in [("month", month), ("quarter", quarter), ("year", year)]:
        if sf(r.get("score")) > 0 and ss(r.get("detail")):
            details.append(f"{ss(r.get('type'))}:{ss(r.get('detail'))}")
        elif not bool(r.get("available")):
            unavailable.append(f"{ss(r.get('label') or key)}不可用:{ss(r.get('detail'))}")

    available_count = len(available_keys)
    if available_count >= 2 and full_count >= 2 and score >= 12:
        typ = "多周期中轨闭环修复"
    elif available_count >= 2 and score >= 9:
        typ = "多周期高级别中轨修复"
    elif score >= 10:
        typ = f"{ss(best.get('type')) or '高级别中轨修复'}"
    elif score >= 6:
        typ = "高级别中轨观察"
    elif score > 0:
        typ = "高级别弱修复"
    else:
        typ = "无"
    return {
        "score": rd(score, 2),
        "type": typ,
        "detail": "；".join(details[:4]) or "无高级别中轨修复证据",
        "unavailable_detail": "；".join(unavailable[:3]),
        "available_cycles": ",".join(available_keys),
        "available_cycle_count": int(available_count),
        "anchor_price": best.get("anchor_price", 0),
        "best_cycle": best_key,
        "repair_stage": best.get("repair_stage", ""),
        "tight_date": best.get("tight_date", ""),
        "month": month,
        "quarter": quarter,
        "year": year,
        "monthly_mid_repair_score": month.get("score", 0),
        "monthly_mid_repair_type": month.get("type", ""),
        "monthly_mid_repair_stage": month.get("repair_stage", ""),
        "monthly_mid_tight_date": month.get("tight_date", ""),
        "monthly_mid_sample_count": month.get("sample_count", 0),
        "quarter_mid_repair_score": quarter.get("score", 0),
        "quarter_mid_repair_type": quarter.get("type", ""),
        "quarter_mid_repair_stage": quarter.get("repair_stage", ""),
        "quarter_mid_tight_date": quarter.get("tight_date", ""),
        "quarter_mid_sample_count": quarter.get("sample_count", 0),
        "year_mid_repair_score": year.get("score", 0),
        "year_mid_repair_type": year.get("type", ""),
        "year_mid_repair_stage": year.get("repair_stage", ""),
        "year_mid_tight_date": year.get("tight_date", ""),
        "year_mid_sample_count": year.get("sample_count", 0),
    }


def evaluate_major_cycle_pricing(d: pd.DataFrame, last_close: float) -> Dict[str, Any]:
    if d.empty or last_close <= 0:
        return {"score": 0.0, "type": "无", "detail": "高级别样本不足", "anchor_price": 0.0}

    mid_repair = evaluate_multi_cycle_mid_repair(d)
    score = sf(mid_repair.get("score"))
    reasons: List[str] = []
    if ss(mid_repair.get("detail")) and ss(mid_repair.get("type")) != "无":
        reasons.append(ss(mid_repair.get("detail")))
    anchor = sf(mid_repair.get("anchor_price"))

    m = build_monthly_deep_df(d)
    if not m.empty:
        scan = m.tail(100).iloc[:-1].copy()
        if len(scan) >= 12:
            bull = scan[(scan["close"] > scan["open"]) & (scan["body_ratio"] >= 0.35)]
            if not bull.empty:
                mx = bull.loc[bull["volume"].idxmax()]
                body_top = max(sf(mx.open), sf(mx.close))
                body_bottom = min(sf(mx.open), sf(mx.close))
                body_mid = (body_top + body_bottom) / 2.0
                if body_mid > 0 and last_close >= body_mid * 0.995:
                    score += 2.5
                    reasons.append(f"月线最大量阳K中位修复{body_mid:.2f}")
                    anchor = anchor or body_mid
                if body_top > 0 and last_close >= body_top * 1.003:
                    score += 1.5
                    reasons.append(f"月线最大量阳K实体顶修复{body_top:.2f}")
                    anchor = body_top

    score = clamp(score, 0, 18)
    if score >= 14 and ss(mid_repair.get("type")) == "多周期中轨闭环修复":
        typ = "多周期中轨闭环修复+历史量峰修复"
    elif score >= 11:
        typ = "高级别大周期修复"
    elif score >= 7:
        typ = "高级别修复观察"
    elif score > 0:
        typ = "高级别弱修复"
    else:
        typ = "无"
    return {
        "score": rd(score, 2),
        "type": typ,
        "detail": "；".join(reasons) or "无高级别修复证据",
        "anchor_price": rd(anchor, 3),
        "major_mid_best_cycle": mid_repair.get("best_cycle", ""),
        "major_mid_repair_stage": mid_repair.get("repair_stage", ""),
        "major_mid_tight_date": mid_repair.get("tight_date", ""),
        "monthly_mid_repair_score": mid_repair.get("monthly_mid_repair_score", 0),
        "monthly_mid_repair_type": mid_repair.get("monthly_mid_repair_type", ""),
        "monthly_mid_repair_stage": mid_repair.get("monthly_mid_repair_stage", ""),
        "monthly_mid_tight_date": mid_repair.get("monthly_mid_tight_date", ""),
        "quarter_mid_repair_score": mid_repair.get("quarter_mid_repair_score", 0),
        "quarter_mid_repair_type": mid_repair.get("quarter_mid_repair_type", ""),
        "quarter_mid_repair_stage": mid_repair.get("quarter_mid_repair_stage", ""),
        "quarter_mid_tight_date": mid_repair.get("quarter_mid_tight_date", ""),
        "year_mid_repair_score": mid_repair.get("year_mid_repair_score", 0),
        "year_mid_repair_type": mid_repair.get("year_mid_repair_type", ""),
        "year_mid_repair_stage": mid_repair.get("year_mid_repair_stage", ""),
        "year_mid_tight_date": mid_repair.get("year_mid_tight_date", ""),
        "major_mid_available_cycles": mid_repair.get("available_cycles", ""),
        "major_mid_available_cycle_count": mid_repair.get("available_cycle_count", 0),
        "monthly_mid_sample_count": mid_repair.get("monthly_mid_sample_count", 0),
        "quarter_mid_sample_count": mid_repair.get("quarter_mid_sample_count", 0),
        "year_mid_sample_count": mid_repair.get("year_mid_sample_count", 0),
        "major_mid_unavailable_detail": mid_repair.get("unavailable_detail", ""),
    }

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



def evaluate_sticky_structure(d: pd.DataFrame, line: float = 0.0, bidx: int = -1, window: int = 30) -> Dict[str, Any]:
    """核心线附近K线粘合度风险识别。

    设计口径：
    1）只看突破前窗口，优先 bidx 前 window 根日K；
    2）只保留四个核心指标：实体重叠率、收盘聚集度、错位率、大实体比率；
    3）只有粘合发生在核心线附近才扣分，远离核心线只输出观察，不扣分；
    4）粘合度是负向风险因子，不作为爆发前夜加分项。
    """
    empty = {
        "sticky_state": "NO_SAMPLE",
        "sticky_raw_score": 0.0,
        "sticky_penalty": 0.0,
        "sticky_core_distance_pct": 0.0,
        "sticky_core_context_factor": 0.0,
        "sticky_body_overlap_ratio": 0.0,
        "sticky_body_overlap_strength": 0.0,
        "sticky_range_overlap_ratio": 0.0,
        "sticky_close_mad_pct": 0.0,
        "sticky_body_mid_mad_pct": 0.0,
        "sticky_dislocation_ratio": 0.0,
        "sticky_large_body_ratio": 0.0,
        "sticky_detail": "粘合样本不足",
    }

    if d.empty or len(d) < 12:
        return dict(empty)

    core_line = sf(line)
    if core_line <= 0:
        out = dict(empty)
        out["sticky_state"] = "NO_CORE_LINE"
        out["sticky_detail"] = "核心线无效，不计算关键位粘合风险"
        return out

    if bidx is not None and int(bidx) > 0:
        sticky_window = d.iloc[max(0, int(bidx) - window):int(bidx)].copy()
    else:
        sticky_window = d.tail(window).copy()

    if sticky_window.empty or len(sticky_window) < 12:
        return dict(empty)

    sw = sticky_window.copy().reset_index(drop=True)
    o = pd.to_numeric(sw["open"], errors="coerce").to_numpy(dtype=float)
    h = pd.to_numeric(sw["high"], errors="coerce").to_numpy(dtype=float)
    l = pd.to_numeric(sw["low"], errors="coerce").to_numpy(dtype=float)
    c = pd.to_numeric(sw["close"], errors="coerce").to_numpy(dtype=float)

    valid = np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c) & (o > 0) & (h > 0) & (l > 0) & (c > 0)
    o, h, l, c = o[valid], h[valid], l[valid], c[valid]
    if len(c) < 12:
        return dict(empty)

    center = float(np.nanmedian(c))
    if center <= 0:
        return dict(empty)

    body_low = np.minimum(o, c)
    body_high = np.maximum(o, c)
    body_mid = (body_low + body_high) / 2.0
    body = np.abs(c - o)
    prev_close_arr = np.r_[c[0], c[:-1]]

    body_hits: List[bool] = []
    body_strengths: List[float] = []
    dislocated_flags: List[bool] = []
    for i in range(1, len(c)):
        inter = max(0.0, min(body_high[i], body_high[i - 1]) - max(body_low[i], body_low[i - 1]))
        base = max(min(body_high[i] - body_low[i], body_high[i - 1] - body_low[i - 1]), center * 0.003)
        body_strength = inter / base
        body_hits.append(inter > center * 0.001)
        body_strengths.append(float(min(max(body_strength, 0.0), 2.0)))

        mid_far = abs(body_mid[i] - body_mid[i - 1]) / max(center, 1e-9) > 0.050
        dislocated_flags.append((inter <= center * 0.001) and mid_far)

    body_overlap_ratio = float(np.mean(body_hits)) if body_hits else 0.0
    body_overlap_strength = float(np.mean(body_strengths)) if body_strengths else 0.0
    dislocation_ratio = float(np.mean(dislocated_flags)) if dislocated_flags else 0.0

    close_median = float(np.nanmedian(c))
    close_mad_pct = float(np.nanmedian(np.abs(c - close_median)) / max(close_median, 1e-9))
    body_mid_median = float(np.nanmedian(body_mid))
    body_mid_mad_pct = float(np.nanmedian(np.abs(body_mid - body_mid_median)) / max(body_mid_median, 1e-9))

    body_pct_arr = body / np.maximum(prev_close_arr, center * 0.01)
    large_body_ratio = float(np.mean(body_pct_arr >= 0.020))

    median_body_mid = float(np.nanmedian(body_mid))
    median_close = float(np.nanmedian(c))
    core_distance = min(
        abs(median_body_mid - core_line) / max(core_line, 1e-9),
        abs(median_close - core_line) / max(core_line, 1e-9),
    )

    if core_distance <= 0.05:
        context_factor = 1.00
    elif core_distance <= 0.08:
        context_factor = 0.60
    else:
        context_factor = 0.0

    # 四因子粘合原始分：实体重叠 + 收盘聚集 - 错位 - 大实体推进。
    sticky_raw = (
        42.0 * body_overlap_ratio
        + 28.0 * max(0.0, 1.0 - close_mad_pct / 0.050)
        - 22.0 * dislocation_ratio
        - 20.0 * large_body_ratio
    )
    sticky_raw = clamp(sticky_raw, 0.0, 100.0)

    if sticky_raw >= 70 and body_overlap_ratio >= 0.68 and close_mad_pct <= 0.030 and dislocation_ratio <= 0.22 and large_body_ratio <= 0.24:
        sticky_state = "OVER_STICKY"
    elif sticky_raw >= 52 and body_overlap_ratio >= 0.55 and close_mad_pct <= 0.045 and dislocation_ratio <= 0.35:
        sticky_state = "STICKY"
    elif sticky_raw >= 35:
        sticky_state = "MILD_STICKY"
    else:
        sticky_state = "NOT_STICKY"

    if context_factor <= 0.0:
        penalty = 0.0
        detail = f"粘合距核心线{core_distance:.1%}，超出关键位范围，不扣分"
    elif sticky_state == "OVER_STICKY":
        penalty = 6.0 * context_factor
        detail = f"核心线附近过度粘合：距线{core_distance:.1%}，实体重叠{body_overlap_ratio:.0%}，收盘离散{close_mad_pct:.1%}，扣{penalty:.1f}"
    elif sticky_state == "STICKY":
        penalty = 3.5 * context_factor
        detail = f"核心线附近偏粘合：距线{core_distance:.1%}，实体重叠{body_overlap_ratio:.0%}，扣{penalty:.1f}"
    elif sticky_state == "MILD_STICKY":
        penalty = 1.5 * context_factor
        detail = f"核心线附近轻微粘合：距线{core_distance:.1%}，扣{penalty:.1f}"
    else:
        penalty = 0.0
        detail = f"核心线附近不粘合：距线{core_distance:.1%}，攻击弹性尚可"

    return {
        "sticky_state": sticky_state,
        "sticky_raw_score": rd(sticky_raw, 2),
        "sticky_penalty": rd(penalty, 2),
        "sticky_core_distance_pct": rd(core_distance * 100.0, 2),
        "sticky_core_context_factor": rd(context_factor, 2),
        "sticky_body_overlap_ratio": rd(body_overlap_ratio, 3),
        "sticky_body_overlap_strength": rd(body_overlap_strength, 3),
        "sticky_range_overlap_ratio": 0.0,
        "sticky_close_mad_pct": rd(close_mad_pct, 4),
        "sticky_body_mid_mad_pct": rd(body_mid_mad_pct, 4),
        "sticky_dislocation_ratio": rd(dislocation_ratio, 3),
        "sticky_large_body_ratio": rd(large_body_ratio, 3),
        "sticky_detail": detail,
    }


def evaluate_activity(d: pd.DataFrame, code: str, line: float = 0.0, bidx: int = -1) -> Dict[str, Any]:
    if d.empty or len(d) < 60:
        return {
            "score": 0.0,
            "type": "无",
            "detail": "股性样本不足",
            "limitup_100d_count": 0,
            "big_up_100d_count": 0,
            "gap_100d_count": 0,
            "atr60": 0.0,
            "amount20": 0.0,
            "volume_ratio_20_60": 0.0,
            "amount_ratio_20_60": 0.0,
            **evaluate_sticky_structure(pd.DataFrame(), line, bidx),
        }

    w = d.tail(100).copy()
    limit_thr = get_limit_threshold(code)
    limit_count = int((w["pct_chg"] >= limit_thr).sum())
    big_up_count = int((w["pct_chg"] >= 5.0).sum())
    gap_count = int((w["open"] >= w["close"].shift(1) * 1.015).sum())
    atr = sf(w["range_pct"].replace([np.inf, -np.inf], np.nan).dropna().tail(60).mean())
    amount20 = sf(w["amount"].tail(20).mean()) if "amount" in w.columns else 0.0
    volume20 = sf(w["volume"].tail(20).mean()) if "volume" in w.columns else 0.0
    volume60 = sf(w["volume"].tail(60).mean()) if "volume" in w.columns else 0.0
    volume_ratio_20_60 = volume20 / max(volume60, 1e-9) if volume60 > 0 else 0.0
    amount_ratio_20_60 = (
        sf(w["amount"].tail(20).mean()) / max(sf(w["amount"].tail(60).mean()), 1e-9)
        if "amount" in w.columns and sf(w["amount"].tail(60).mean()) > 0 else 0.0
    )

    score = 0.0
    reasons: List[str] = []

    if limit_count >= 5:
        score += 7.0; reasons.append(f"100日涨停{limit_count}次")
    elif limit_count >= 3:
        score += 5.0; reasons.append(f"100日涨停{limit_count}次")
    elif limit_count >= 1:
        score += 1.5; reasons.append(f"100日涨停{limit_count}次")
    else:
        score -= 2.0; reasons.append("100日无涨停")

    if big_up_count >= 8:
        score += 4.0; reasons.append(f"大阳{big_up_count}次")
    elif big_up_count >= 4:
        score += 2.5; reasons.append(f"大阳{big_up_count}次")
    elif big_up_count >= 2:
        score += 1.0; reasons.append(f"大阳{big_up_count}次")

    if gap_count >= 4:
        score += 2.5; reasons.append(f"跳空{gap_count}次")
    elif gap_count >= 2:
        score += 1.2; reasons.append(f"跳空{gap_count}次")

    if atr >= 0.055:
        score += 4.0; reasons.append(f"ATR弹性{atr:.1%}")
    elif atr >= 0.035:
        score += 2.5; reasons.append(f"ATR弹性{atr:.1%}")
    elif atr < 0.018:
        score -= 2.0; reasons.append(f"波动过窄{atr:.1%}")

    if volume_ratio_20_60 >= 1.20:
        score += 1.5; reasons.append(f"量能中枢抬升{volume_ratio_20_60:.2f}")
    elif 0 < volume_ratio_20_60 <= 0.65:
        score -= 1.0; reasons.append(f"量能中枢收缩{volume_ratio_20_60:.2f}")

    if amount20 > 0 and amount20 < 5e7:
        score -= 3.0; reasons.append(f"成交额不足{amount20/1e8:.2f}亿")

    sticky = evaluate_sticky_structure(d, line=line, bidx=bidx, window=30)
    sticky_penalty = sf(sticky.get("sticky_penalty"))
    if sticky_penalty > 0:
        score -= sticky_penalty
    reasons.append(ss(sticky.get("sticky_detail")))

    score = clamp(score, -12, 16)
    typ = "高活跃" if score >= 8 else "中活跃" if score >= 4 else "低活跃" if score < 0 else "普通"

    return {
        "score": rd(score, 2),
        "type": typ,
        "detail": "；".join([x for x in reasons if ss(x)]),
        "limitup_100d_count": limit_count,
        "big_up_100d_count": big_up_count,
        "gap_100d_count": gap_count,
        "atr60": rd(atr, 4),
        "amount20": rd(amount20, 2),
        "volume_ratio_20_60": rd(volume_ratio_20_60, 3),
        "amount_ratio_20_60": rd(amount_ratio_20_60, 3),
        **sticky,
    }

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
    hypotheses = []
    hypotheses.append(("高级别中轨修复", sf(major.get("score")) * 2.6 + sf(pullback.get("score")) * 0.9 + sf(fund.get("score")) * 0.45))
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


def deep_status_and_score(code: str, name: str, df: pd.DataFrame, line: float, line_info: Dict[str, Any], br: Dict[str, Any]) -> Dict[str, Any]:
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
    activity = evaluate_activity(d, code, L, bidx)
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
        "major_cycle_anchor_price": major.get("anchor_price", 0),
        "major_mid_best_cycle": major.get("major_mid_best_cycle", ""),
        "major_mid_repair_stage": major.get("major_mid_repair_stage", ""),
        "major_mid_tight_date": major.get("major_mid_tight_date", ""),
        "monthly_mid_repair_score": major.get("monthly_mid_repair_score", 0),
        "monthly_mid_repair_type": major.get("monthly_mid_repair_type", ""),
        "monthly_mid_repair_stage": major.get("monthly_mid_repair_stage", ""),
        "monthly_mid_tight_date": major.get("monthly_mid_tight_date", ""),
        "quarter_mid_repair_score": major.get("quarter_mid_repair_score", 0),
        "quarter_mid_repair_type": major.get("quarter_mid_repair_type", ""),
        "quarter_mid_repair_stage": major.get("quarter_mid_repair_stage", ""),
        "quarter_mid_tight_date": major.get("quarter_mid_tight_date", ""),
        "year_mid_repair_score": major.get("year_mid_repair_score", 0),
        "year_mid_repair_type": major.get("year_mid_repair_type", ""),
        "year_mid_repair_stage": major.get("year_mid_repair_stage", ""),
        "year_mid_tight_date": major.get("year_mid_tight_date", ""),
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
        "sticky_state": activity.get("sticky_state", ""),
        "sticky_raw_score": activity.get("sticky_raw_score", 0),
        "sticky_penalty": activity.get("sticky_penalty", 0),
        "sticky_core_distance_pct": activity.get("sticky_core_distance_pct", 0),
        "sticky_core_context_factor": activity.get("sticky_core_context_factor", 0),
        "sticky_body_overlap_ratio": activity.get("sticky_body_overlap_ratio", 0),
        "sticky_body_overlap_strength": activity.get("sticky_body_overlap_strength", 0),
        "sticky_range_overlap_ratio": activity.get("sticky_range_overlap_ratio", 0),
        "sticky_close_mad_pct": activity.get("sticky_close_mad_pct", 0),
        "sticky_body_mid_mad_pct": activity.get("sticky_body_mid_mad_pct", 0),
        "sticky_dislocation_ratio": activity.get("sticky_dislocation_ratio", 0),
        "sticky_large_body_ratio": activity.get("sticky_large_body_ratio", 0),
        "sticky_detail": activity.get("sticky_detail", ""),
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
    }


def screen_one_stock(code: str, name: str, df: pd.DataFrame) -> Dict[str, Any]:
    historical_line = choose_historical_core_resonance_line(df)
    thousand_line = choose_thousand_day_resonance_trigger_line(df)
    historical_price = sf(historical_line.get("line")) if historical_line.get("line") is not None else 0.0
    thousand_price = sf(thousand_line.get("line")) if thousand_line.get("line") is not None else 0.0
    historical_breakout = daily_breakout_quality(df, historical_price)
    thousand_breakout = daily_breakout_quality(df, thousand_price)
    historical_hit = bool(historical_breakout.get("hit"))
    thousand_hit = bool(thousand_breakout.get("hit"))
    if not (historical_hit or thousand_hit):
        return {}
    options: List[Dict[str, Any]] = []
    if historical_hit:
        options.append({"line_type": "历史核心共振线", "line_info": historical_line, "breakout": historical_breakout})
    if thousand_hit:
        options.append({"line_type": "千日共振触发线", "line_info": thousand_line, "breakout": thousand_breakout})
    primary = select_primary_assessment_line(df, options)
    if not primary:
        return {}
    primary_type = ss(primary.get("line_type"))
    primary_line_info = primary.get("line_info", {}) or {}
    primary_breakout = primary.get("breakout", {}) or {}
    primary_price = sf(primary.get("line"))
    deep = deep_status_and_score(code, name, df, primary_price, primary_line_info, primary_breakout)
    if historical_hit and thousand_hit:
        source = "双线共振"
    elif historical_hit:
        source = "历史核心共振线"
    else:
        source = "千日共振触发线"
    row = {
        "股票代码": code,
        "股票中文名称": name or code,
        "海选命中来源": source,
        "主评测线类型": primary_type,
        "主评测线价位": rd(primary_price, 3),
        "主评测线突破日期": primary_breakout.get("date", ""),
        "主评测线突破质量": primary_breakout.get("quality", 0),
        "主评测线共振次数": primary_line_info.get("effective_resonance_count", 0),
        "主评测线带量共振次数": primary_line_info.get("volume_resonance_count", 0),
        "主评测线净分": primary_line_info.get("net_score", 0),
        "主评测线选择分": primary.get("assessment_score", 0),
        "主评测线选择原因": primary.get("assessment_reason", ""),
        "历史核心共振线价位": rd(historical_price, 3),
        "历史核心共振线突破日期": historical_breakout.get("date", "") if historical_hit else "",
        "历史核心共振线是否命中": historical_hit,
        "历史核心共振线共振次数": historical_line.get("effective_resonance_count", 0),
        "历史核心共振线带量共振次数": historical_line.get("volume_resonance_count", 0),
        "历史核心共振线净分": historical_line.get("net_score", 0),
        "千日共振触发线价位": rd(thousand_price, 3),
        "千日共振触发线突破日期": thousand_breakout.get("date", "") if thousand_hit else "",
        "千日共振触发线是否命中": thousand_hit,
        "千日共振触发线共振次数": thousand_line.get("effective_resonance_count", 0),
        "千日共振触发线带量共振次数": thousand_line.get("volume_resonance_count", 0),
        "千日共振触发线净分": thousand_line.get("net_score", 0),
        "深度等级": deep.get("deep_grade", "D"),
        "深度得分": deep.get("deep_score", 0),
        "当前状态": deep.get("deep_state", ""),
        "操作建议": deep.get("trade_action", ""),
        "候选池": deep.get("deep_pool", ""),
        "加分原因": deep.get("deep_positive_reasons", ""),
        "扣分原因": deep.get("deep_negative_reasons", ""),
        "风险标签": deep.get("risk_flags", ""),
        "当前收盘": deep.get("current_close", 0),
        "距主评测线%": deep.get("distance_line_pct", 0),
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
        "breakout_quality": primary_breakout.get("quality", 0),
        "breakout_close": primary_breakout.get("close", 0),
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
    rows = sorted(rows, key=lambda x: (sf(x.get("深度得分")), ss(x.get("主评测线突破日期")), sf(x.get("breakout_quality")), sf(x.get("主评测线净分"))), reverse=True)
    return rows



def grade_rank(grade: Any) -> int:
    order = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
    return order.get(ss(grade).upper(), 0)


def is_hard_rejected_row(r: Dict[str, Any]) -> bool:
    action = ss(r.get("操作建议") or r.get("trade_action"))
    pool = ss(r.get("候选池") or r.get("deep_pool"))
    if "剔除" in action or pool == "剔除":
        return True
    if bool(r.get("risk_block")):
        return True
    if "突破失败" in action or "硬风险" in action:
        return True
    return False


def featured_sort_key(r: Dict[str, Any]) -> Tuple[int, float, int, float, float, float]:
    grade = ss(r.get("深度等级") or r.get("deep_grade")).upper()
    score = sf(r.get("深度得分") or r.get("deep_score"))
    trade_ok = 1 if bool(r.get("trade_pricing_ok")) else 0
    primary = sf(r.get("primary_setup_score"))
    rr = sf(r.get("估算赔率") or r.get("rr_estimate"))
    risk_penalty = sf(r.get("risk_penalty"))
    return (grade_rank(grade), score, trade_ok, primary, rr, -risk_penalty)


def select_featured_rows(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """海选只召回；深度层统一评级；最终报告最多输出TopN。

    规则：
    1）所有海选命中票已经在 screen_one_stock 内完成深度评分；
    2）正式精选优先取 S/A 且分数达到 DEEP_MIN_FORMAL_SCORE 的票；
    3）S/A 不足 TopN 时，不再空着，也不再推海选大表，而是从未硬剔除票里按深度质量补足；
    4）硬剔除票包括突破失败跌回线下、风险阻断、交易动作明确剔除等，不允许补位。
    """
    limit = max(1, DEEP_FINAL_PICK_LIMIT)
    usable = sorted([r for r in rows if not is_hard_rejected_row(r)], key=featured_sort_key, reverse=True)

    selected: List[Dict[str, Any]] = []
    seen = set()

    formal = [
        r for r in usable
        if ss(r.get("深度等级") or r.get("deep_grade")).upper() in DEEP_FORMAL_GRADES
        and sf(r.get("深度得分") or r.get("deep_score")) >= DEEP_MIN_FORMAL_SCORE
    ]
    for r in formal:
        code = ss(r.get("股票代码"))
        if code and code not in seen:
            r["最终入选性质"] = "A级/S级深度精选"
            selected.append(r)
            seen.add(code)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        for r in usable:
            code = ss(r.get("股票代码"))
            if code and code not in seen:
                r["最终入选性质"] = "A级不足补位｜相对最优观察"
                selected.append(r)
                seen.add(code)
            if len(selected) >= limit:
                break

    watch = [r for r in usable if ss(r.get("股票代码")) not in seen]
    watch = watch[:max(0, DEEP_WATCH_REPORT_LIMIT)]
    return selected, watch


def selected_quality_note(selected: List[Dict[str, Any]]) -> str:
    if not selected:
        return "今日无可用精选：海选命中票全部被硬风险或突破失败剔除。"
    formal_count = sum(
        1 for r in selected
        if ss(r.get("最终入选性质")) == "A级/S级深度精选"
    )
    if formal_count == len(selected):
        return "今日精选质量：A级/S级满足数量，直接输出深度精选。"
    if formal_count > 0:
        return f"今日精选质量：A级/S级仅{formal_count}只，其余按深度得分补入相对最优观察，不伪装成A级。"
    return f"今日精选质量：无A级/S级，输出未硬剔除票中的相对最优Top{DEEP_FINAL_PICK_LIMIT}，只能按观察处理。"

def short_reason(text: Any, max_len: int = 90) -> str:
    s = ss(text).replace("\n", "；")
    return s if len(s) <= max_len else s[:max_len - 1] + "…"


def build_report(rows: List[Dict[str, Any]], stat: Dict[str, Any]) -> str:
    if not rows:
        return "三号员工：今日无核心线突破海选命中。"

    selected, watch = select_featured_rows(rows)
    hard_rejected = len([r for r in rows if is_hard_rejected_row(r)])
    lines = [
        f"三号员工最终深度精选｜{TARGET_DASH or TARGET}",
        f"海选召回：{len(rows)}只；完成深度评分：{len(rows)}只；硬剔除：{hard_rejected}只；最终输出：{len(selected)}只。",
        selected_quality_note(selected),
        "全量海选/深度评分明细已写入 CSV/JSON 审计，Telegram 只推最终精选，不再推海选大表。",
        "",
    ]

    if selected:
        lines.extend([
            "| 排名 | 股票代码 | 股票简称 | 海选来源 | 主评测线类型 | 主评测线 | 突破日 | 深度评级 | 得分 | 入选性质 | 状态 | 防守位 | RR | 操作 |",
            "|---:|---|---|---|---|---:|---|---|---:|---|---|---:|---:|---|",
        ])
        for idx, r in enumerate(selected, 1):
            lines.append(
                f"| {idx} | {r.get('股票代码','')} | {r.get('股票中文名称','')} | {short_reason(r.get('海选命中来源',''), 12)} | "
                f"{short_reason(r.get('主评测线类型',''), 12)} | {r.get('主评测线价位',0)} | {r.get('主评测线突破日期','')} | "
                f"{r.get('深度等级','')} | {r.get('深度得分',0)} | {short_reason(r.get('最终入选性质',''), 22)} | "
                f"{short_reason(r.get('当前状态',''), 18)} | {r.get('交易防守位',0)} | {r.get('估算赔率',0)} | "
                f"{short_reason(r.get('操作建议',''), 24)} |"
            )
        lines.append("")
        for idx, r in enumerate(selected, 1):
            lines.extend([
                f"{idx}）{r.get('股票代码','')} {r.get('股票中文名称','')}",
                f"- 入选性质：{ss(r.get('最终入选性质')) or '深度精选'}",
                f"- 选中原因：{short_reason(r.get('加分原因',''), 180)}",
                f"- 扣分/风险：{short_reason(r.get('扣分原因',''), 160)}",
                f"- 确认条件：{short_reason(r.get('confirm_condition',''), 120)}",
                f"- 放弃条件：{short_reason(r.get('giveup_condition',''), 120)}",
            ])
    else:
        lines.append("今日无三号员工最终精选：所有海选票均被硬风险、突破失败或数据异常剔除。")

    # 未入最终精选的深度候选不进入 Telegram/Markdown 正文，避免把海选/观察池误当精选。
    # 全量 rows 已写入 CSV/JSON，复盘时看审计文件。


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
            "thousand_day_lookback": THOUSAND_DAY_LOOKBACK,
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



# 自检与模拟样本已从生产主文件拆出到 employee3_coreline_selfcheck.py。
# 生产运行只负责：读取缓存 -> 海选/深评 -> 写入报告 -> Telegram 推送。
# 如需开发验证，请单独运行：python employee3_coreline_selfcheck.py

def main() -> None:
    print(BOOT, flush=True)
    print(f"file={Path(__file__).resolve()}", flush=True)
    print(f"target={TARGET} target_dash={TARGET_DASH}", flush=True)
    print(f"progress_color_enabled={progress_color_enabled()} 条形进度=True", flush=True)
    print("cache_dirs=" + " | ".join(str(x) for x in CACHE_DIRS), flush=True)

    # 生产主流程不再混入模拟自检。
    # 自检结果字段保留在 JSON 中，只作为审计占位，避免破坏既有输出契约。
    self_check = {
        "enabled": False,
        "status": "moved_to_employee3_coreline_selfcheck.py",
        "note": "生产主流程不执行模拟自检；开发验证请单独运行 selfcheck 文件。",
    }

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
