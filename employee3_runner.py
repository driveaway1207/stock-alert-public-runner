
from __future__ import annotations

import json

import math

import os

import re

import time

from datetime import datetime, timedelta, timezone

from pathlib import Path

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

import numpy as np

try:

    import requests

except Exception:                    

    requests = None

try:

    import baostock as bs

except Exception:                    

    bs = None

BOOT = "EMPLOYEE3_DUAL_LINE_BREAKOUT_DEEP_SCREEN_V5_RECENT_IPO_BRANCH_20260607"

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

CORE_LINE_TIMEFRAME = "自然月K"

BREAKOUT_LOOKBACK_DAYS = int(os.getenv("EMPLOYEE3_BREAKOUT_LOOKBACK_DAYS", "20"))

CORE_LINE_TOL = float(os.getenv("EMPLOYEE3_CORE_LINE_TOL", os.getenv("EMPLOYEE5_CORE_LINE_TOL", "0.01")))

CORE_LINE_BAND_TOL = float(os.getenv("EMPLOYEE3_CORE_LINE_BAND_TOL", "0.015"))

MIN_CACHE_ROWS = int(os.getenv("EMPLOYEE3_MIN_CACHE_ROWS", "80"))

MIN_CORE_RESONANCE = int(os.getenv("EMPLOYEE3_MIN_CORE_RESONANCE", "3"))

FIVE_HUNDRED_DAY_LOOKBACK = int(os.getenv("EMPLOYEE3_FIVE_HUNDRED_DAY_LOOKBACK", "500"))

LINE_TOP_CANDIDATE_LIMIT = int(os.getenv("EMPLOYEE3_LINE_TOP_CANDIDATE_LIMIT", "12"))

ASSESSMENT_LINE_MAX_ABOVE_PCT = float(os.getenv("EMPLOYEE3_ASSESSMENT_LINE_MAX_ABOVE_PCT", "0.18"))

ASSESSMENT_LINE_MAX_BELOW_PCT = float(os.getenv("EMPLOYEE3_ASSESSMENT_LINE_MAX_BELOW_PCT", "0.025"))

ASSESSMENT_LINE_MIN_SPACE_PCT = float(os.getenv("EMPLOYEE3_ASSESSMENT_LINE_MIN_SPACE_PCT", "6.0"))

ASSESSMENT_LINE_MIN_RR = float(os.getenv("EMPLOYEE3_ASSESSMENT_LINE_MIN_RR", "1.05"))

# 全历史压力定价：不能只看520日。先分近端/中期/长期/全历史，再决定是历史压力RR还是价格发现。
OVERHEAD_PRESSURE_MIN_ABOVE_PCT = float(os.getenv("EMPLOYEE3_OVERHEAD_PRESSURE_MIN_ABOVE_PCT", "0.025"))
OVERHEAD_PRESSURE_BAND_TOL = float(os.getenv("EMPLOYEE3_OVERHEAD_PRESSURE_BAND_TOL", "0.018"))
OVERHEAD_PRESSURE_NEAR_DAYS = int(os.getenv("EMPLOYEE3_OVERHEAD_PRESSURE_NEAR_DAYS", "520"))
OVERHEAD_PRESSURE_MID_DAYS = int(os.getenv("EMPLOYEE3_OVERHEAD_PRESSURE_MID_DAYS", "1200"))
OVERHEAD_PRESSURE_LONG_DAYS = int(os.getenv("EMPLOYEE3_OVERHEAD_PRESSURE_LONG_DAYS", "2400"))
OVERHEAD_PRESSURE_MIN_RELIABLE_HITS = int(os.getenv("EMPLOYEE3_OVERHEAD_PRESSURE_MIN_RELIABLE_HITS", "2"))
PRICE_DISCOVERY_MAX_RISK_PCT = float(os.getenv("EMPLOYEE3_PRICE_DISCOVERY_MAX_RISK_PCT", "8.5"))
PRICE_DISCOVERY_MAX_DISTANCE_PCT = float(os.getenv("EMPLOYEE3_PRICE_DISCOVERY_MAX_DISTANCE_PCT", "12.0"))

RECENT_IPO_SPECIAL_MAX_DAYS = int(os.getenv("EMPLOYEE3_RECENT_IPO_SPECIAL_MAX_DAYS", "250"))
RECENT_IPO_MIN_FORMAL_DAYS = int(os.getenv("EMPLOYEE3_RECENT_IPO_MIN_FORMAL_DAYS", "60"))
RECENT_IPO_MIN_PLATFORM_DAYS = int(os.getenv("EMPLOYEE3_RECENT_IPO_MIN_PLATFORM_DAYS", "30"))
RECENT_IPO_PLATFORM_MIN_WINDOW = int(os.getenv("EMPLOYEE3_RECENT_IPO_PLATFORM_MIN_WINDOW", "15"))
RECENT_IPO_PLATFORM_MAX_WINDOW = int(os.getenv("EMPLOYEE3_RECENT_IPO_PLATFORM_MAX_WINDOW", "45"))
RECENT_IPO_PLATFORM_BASE_AMP = float(os.getenv("EMPLOYEE3_RECENT_IPO_PLATFORM_BASE_AMP", "0.22"))
RECENT_IPO_PLATFORM_ATR_MULT = float(os.getenv("EMPLOYEE3_RECENT_IPO_PLATFORM_ATR_MULT", "4.0"))
RECENT_IPO_PRICE_DISCOVERY_MAX_RISK_PCT = float(os.getenv("EMPLOYEE3_RECENT_IPO_PRICE_DISCOVERY_MAX_RISK_PCT", "7.5"))
RECENT_IPO_PRICE_DISCOVERY_MAX_DISTANCE_PCT = float(os.getenv("EMPLOYEE3_RECENT_IPO_PRICE_DISCOVERY_MAX_DISTANCE_PCT", "10.0"))

CACHE_SCAN_PROGRESS_EVERY = int(os.getenv("EMPLOYEE3_CACHE_SCAN_PROGRESS_EVERY", "500"))

SCREEN_PROGRESS_EVERY = int(os.getenv("EMPLOYEE3_SCREEN_PROGRESS_EVERY", "200"))

MAX_STOCKS = int(os.getenv("MAX_STOCKS", os.getenv("EMPLOYEE3_MAX_STOCKS", "0")))

ALLOW_BAOSTOCK_FALLBACK = os.getenv("EMPLOYEE3_ALLOW_BAOSTOCK_FALLBACK", "0") == "1"

RECENT_REFRESH_DAYS = int(os.getenv("EMPLOYEE3_RECENT_REFRESH_DAYS", "10"))

RECENT_REFRESH_BUDGET_MIN = float(os.getenv("EMPLOYEE3_RECENT_REFRESH_BUDGET_MIN", "35"))

QFQ_ADJUSTFLAG = "2"

PROGRESS_WIDTH = int(os.getenv("EMPLOYEE3_PROGRESS_WIDTH", "34"))

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

DEEP_DEFENSE_BUFFER_PCT = float(os.getenv("EMPLOYEE3_DEEP_DEFENSE_BUFFER_PCT", "0.015"))

DEEP_RECENT_HOT_20D_PCT = float(os.getenv("EMPLOYEE3_DEEP_RECENT_HOT_20D_PCT", "25"))

DEEP_FINAL_PICK_LIMIT = int(os.getenv("EMPLOYEE3_DEEP_FINAL_PICK_LIMIT", os.getenv("EMPLOYEE3_DEEP_TOP_REPORT_LIMIT", "5")))

DEEP_MIN_FORMAL_SCORE = float(os.getenv("EMPLOYEE3_DEEP_MIN_FORMAL_SCORE", "78"))

DEEP_FORMAL_GRADES = tuple(x.strip() for x in os.getenv("EMPLOYEE3_DEEP_FORMAL_GRADES", "S,A").split(",") if x.strip())
DEEP_BACKUP_MIN_SCORE = float(os.getenv("EMPLOYEE3_DEEP_BACKUP_MIN_SCORE", "58"))
DEEP_BACKUP_ALLOWED_GRADES = tuple(x.strip().upper() for x in os.getenv("EMPLOYEE3_DEEP_BACKUP_ALLOWED_GRADES", "B,C").split(",") if x.strip())
DEEP_BACKUP_MAX_RISK_PENALTY = float(os.getenv("EMPLOYEE3_DEEP_BACKUP_MAX_RISK_PENALTY", "18"))
DEEP_BACKUP_MAX_DISTANCE_LINE_PCT = float(os.getenv("EMPLOYEE3_DEEP_BACKUP_MAX_DISTANCE_LINE_PCT", "18"))
DEEP_BACKUP_MAX_DEFENSE_DISTANCE_PCT = float(os.getenv("EMPLOYEE3_DEEP_BACKUP_MAX_DEFENSE_DISTANCE_PCT", "12"))

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

    if stage == "cache":

        return {"icon": "🟢", "name": "缓存读取", "color": "92"}

    if stage == "refresh":

        return {"icon": "🟠", "name": "数据补拉", "color": "38;5;208"}

    if stage == "screen":

        return {"icon": "🟣", "name": "双线海选", "color": "95"}

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

def valid_stock_display_name(code: Any, name: Any) -> bool:

    c = code_of(code)

    n = ss(name)

    if not n:

        return False

    low = n.lower()

    bad = {"nan", "none", "null", "名称待补", "名称缺失", "--", "-"}

    if n in bad or low in bad:

        return False

    digits = code_of(n)

    if c and digits == c and re.sub(r"\D", "", n) in {c, "0" + c, "1" + c}:

        return False

    if c and n in {c, f"sh.{c}", f"sz.{c}", f"bj.{c}", f"SH.{c}", f"SZ.{c}", f"BJ.{c}"}:

        return False

    return True

def stock_display_name(code: Any, name: Any) -> str:

    c = code_of(code)

    n = ss(name)

    return n if valid_stock_display_name(c, n) else "名称待补"

def add_name_mapping(name_map: Dict[str, str], code: Any, name: Any) -> None:

    c = code_of(code)

    n = ss(name)

    if valid_code(c) and valid_stock_display_name(c, n) and c not in name_map:

        name_map[c] = n

def scan_name_frame(name_map: Dict[str, str], df: pd.DataFrame) -> None:

    if df is None or df.empty:

        return

    code_cols = ["原始代码", "代码", "股票代码", "证券代码", "code", "symbol", "bs_code"]

    name_cols = ["股票中文名称", "名称", "股票名称", "股票简称", "证券简称", "简称", "name", "code_name"]

    usable_code_cols = [c for c in code_cols if c in df.columns]

    usable_name_cols = [c for c in name_cols if c in df.columns]

    if not usable_code_cols or not usable_name_cols:

        return

    for code_col in usable_code_cols:

        for name_col in usable_name_cols:

            pair = df[[code_col, name_col]].dropna(how="all")

            if pair.empty:

                continue

            for _, r in pair.iterrows():

                add_name_mapping(name_map, r.get(code_col, ""), r.get(name_col, ""))

def scan_name_json(name_map: Dict[str, str], path: Path) -> None:

    try:

        obj = json.loads(path.read_text(encoding="utf-8"))

    except Exception:

        return

    if isinstance(obj, dict):

        for k, v in obj.items():

            if isinstance(v, str):

                add_name_mapping(name_map, k, v)

            elif isinstance(v, dict):

                code = v.get("代码") or v.get("股票代码") or v.get("原始代码") or v.get("code") or v.get("symbol") or k

                name = v.get("股票中文名称") or v.get("名称") or v.get("股票名称") or v.get("股票简称") or v.get("证券简称") or v.get("name") or v.get("code_name")

                add_name_mapping(name_map, code, name)

            elif isinstance(v, list) and v:

                add_name_mapping(name_map, k, v[0])

    elif isinstance(obj, list):

        scan_name_frame(name_map, pd.DataFrame(obj))

def load_name_map() -> Dict[str, str]:

    name_map: Dict[str, str] = {}

    explicit = ss(os.getenv("MODEL_UNIVERSE_FILE"))

    paths: List[Path] = []

    if explicit:

        paths.append(Path(explicit))

    search_dirs = [ROOT / "outputs", ROOT, ROOT.parent / "outputs", ROOT / "data", ROOT / "cache", MAIN_CACHE_DIR]

    search_dirs.extend(CACHE_DIRS)

    seen_dirs: List[Path] = []

    for d in search_dirs:

        if d and d not in seen_dirs:

            seen_dirs.append(d)

    for base in seen_dirs:

        if not base.exists():

            continue

        paths.extend(sorted(base.glob("model_usable_universe_*.csv"), key=lambda x: x.stat().st_mtime, reverse=True))

        status = base / "_full_history_status.csv"

        if status.exists():

            paths.append(status)

        for pth in sorted(base.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)[:40]:

            if any(key in pth.name.lower() for key in ["universe", "stock", "usable", "name", "股票", "status"]):

                paths.append(pth)

        for pth in sorted(base.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:30]:

            if any(key in pth.name.lower() for key in ["universe", "stock", "usable", "name", "股票", "status", "map"]):

                paths.append(pth)

    uniq: List[Path] = []

    seen = set()

    for pth in paths:

        try:

            key = str(pth.resolve())

        except Exception:

            key = str(pth)

        if key not in seen:

            uniq.append(pth)

            seen.add(key)

    for pth in uniq:

        if not pth.exists():

            continue

        suffix = pth.suffix.lower()

        if suffix == ".csv":

            try:

                scan_name_frame(name_map, pd.read_csv(pth, dtype=str))

            except Exception:

                continue

        elif suffix == ".json":

            scan_name_json(name_map, pth)

    if os.getenv("EMPLOYEE3_NAME_BAOSTOCK_FALLBACK", "0") == "1" and bs is not None:

        try:

            lg = bs.login()

            if getattr(lg, "error_code", "") == "0":

                day = TARGET_DASH or now_bj().strftime("%Y-%m-%d")

                rs = bs.query_all_stock(day)

                df = rs.get_data()

                if df is not None and not df.empty:

                    scan_name_frame(name_map, df)

        except Exception:

            name_map["__baostock_name_error__"] = "名称待补"

        finally:

            try:

                bs.logout()

            except Exception as exc:

                name_map["__baostock_logout_error__"] = ss(exc)[:60] or "logout_error"

    return {k: v for k, v in name_map.items() if valid_code(k) and valid_stock_display_name(k, v)}

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

                non_empty = [ss(x) for x in df["name"].tolist() if valid_stock_display_name(code, x)]

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

    except Exception as exc:

        stat["logout_error"] = ss(exc)[:120] or "logout_error"

    return stat

def aggregate_monthly_bars(df: pd.DataFrame) -> pd.DataFrame:

    d = normalize_hist(df)

    if d.empty or len(d) < 60:

        return pd.DataFrame()

    d = d.reset_index(drop=True).copy()

    dt = pd.to_datetime(d["date"], errors="coerce")

    d = d[dt.notna()].copy()

    if d.empty:

        return pd.DataFrame()

    d["_month"] = pd.to_datetime(d["date"], errors="coerce").dt.to_period("M").astype(str)

    bars: List[Dict[str, Any]] = []

    for month, g in d.groupby("_month", sort=True):

        g = g.sort_values("date").reset_index(drop=True)

        if g.empty:

            continue

        bars.append({

            "month": ss(month),

            "start": ss(g.iloc[0].date),

            "end": ss(g.iloc[-1].date),

            "open": sf(g.iloc[0].open),

            "high": sf(g.high.max()),

            "low": sf(g.low.min()),

            "close": sf(g.iloc[-1].close),

            "volume": sf(g.volume.sum()) if "volume" in g.columns else 0.0,

            "amount": sf(g.amount.sum()) if "amount" in g.columns else 0.0,

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


def line_candidate_sources(k: pd.DataFrame) -> Dict[float, str]:

    sources: Dict[float, set] = {}

    def add(price: Any, source: str) -> None:

        price_value = sf(price)

        if price_value > 0:

            sources.setdefault(rd(price_value, 3), set()).add(source)

    for _, r in k.iterrows():

        add(r.get("high", 0.0), "最高价")

        add(r.get("body_top", 0.0), "实体顶")

        add(r.get("close", 0.0), "收盘价")

    prev_volume = k.volume.shift(1) if "volume" in k.columns else pd.Series([0.0] * len(k))

    for idx, r in k.iterrows():

        close = sf(r.get("close", 0.0))

        open_value = sf(r.get("open", 0.0))

        volume = sf(r.get("volume", 0.0))

        pv = sf(prev_volume.iloc[idx] if idx < len(prev_volume) else 0.0)

        if close > open_value and volume > pv and close > 0:

            add(close, "阳线放量收盘价")

    return {line: "+".join(sorted(srcs)) for line, srcs in sources.items()}

def batch_score_lines(k: pd.DataFrame, sources: Dict[float, str], chunk_size: int = 768) -> List[Dict[str, Any]]:

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

                "timeframe": CORE_LINE_TIMEFRAME,

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

def rank_key_historical_core_resonance_line(x: Dict[str, Any]) -> Tuple[float, int, int, float]:

    return (sf(x.get("net_score")), int(sf(x.get("effective_resonance_count"))), int(sf(x.get("volume_resonance_count"))), -sf(x.get("line")))

def rank_key_five_hundred_day_resonance_trigger_line(x: Dict[str, Any]) -> Tuple[int, int, float, float]:

    return (int(sf(x.get("effective_resonance_count"))), int(sf(x.get("volume_resonance_count"))), sf(x.get("net_score")), -sf(x.get("line")))

def choose_resonance_line(df: pd.DataFrame, line_label: str, lookback_days: int = 0, rank_mode: str = "historical") -> Dict[str, Any]:

    d = normalize_hist(df)

    if lookback_days > 0 and len(d) > lookback_days:

        d = d.tail(lookback_days).reset_index(drop=True)

    raw_k = aggregate_monthly_bars(d)

    if raw_k.empty or len(raw_k) < 3:

        return {"line": None, "level": "数据不足", "reason": f"{line_label}自然月K线不足", "line_label": line_label}

    completed = raw_k.iloc[:-1].reset_index(drop=True)

    if completed.empty:

        return {"line": None, "level": "数据不足", "reason": f"{line_label}无已完成自然月K", "line_label": line_label}

    sources = line_candidate_sources(completed)

    if not sources:

        return {"line": None, "level": "未识别", "reason": f"{line_label}未识别到候选价位", "line_label": line_label}

    scored_all = batch_score_lines(completed, sources)

    scored = [x for x in scored_all if ss(x.get("line_type")) == "core_line"]

    if not scored:

        return {"line": None, "level": "未识别", "reason": f"{line_label}未识别到有效共振线", "line_label": line_label}

    rank_func = rank_key_five_hundred_day_resonance_trigger_line if rank_mode == "five_hundred_day" else rank_key_historical_core_resonance_line

    band_winners = [max(g, key=rank_func) for g in group_by_band(scored)]

    ranked = sorted(band_winners, key=rank_func, reverse=True)

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

def choose_five_hundred_day_resonance_trigger_line(df: pd.DataFrame) -> Dict[str, Any]:

    return choose_resonance_line(df, "五百日共振触发线", lookback_days=FIVE_HUNDRED_DAY_LOOKBACK, rank_mode="five_hundred_day")


def recent_ipo_empty_context(d: pd.DataFrame = None, detail: str = "") -> Dict[str, Any]:
    x = normalize_hist(d) if d is not None else pd.DataFrame()
    first_date = ss(x.iloc[0].get("date", "")) if not x.empty else ""
    return {
        "is_recent_ipo": False,
        "recent_ipo_flag": False,
        "listing_age_days": int(len(x)) if x is not None else 0,
        "first_trade_date": first_date,
        "recent_ipo_stage": "普通股/非次新",
        "recent_ipo_maturity_score": 0.0,
        "recent_ipo_platform_valid": False,
        "recent_ipo_platform_score": 0.0,
        "recent_ipo_platform_upper": 0.0,
        "recent_ipo_platform_lower": 0.0,
        "recent_ipo_platform_window": 0,
        "recent_ipo_platform_amp_pct": 0.0,
        "recent_ipo_max_amount_high": 0.0,
        "recent_ipo_max_amount_body_top": 0.0,
        "recent_ipo_max_amount_body_bottom": 0.0,
        "recent_ipo_ipo_day_high": 0.0,
        "recent_ipo_ipo_day_close": 0.0,
        "recent_ipo_post_high": 0.0,
        "recent_ipo_post_high_date": "",
        "recent_ipo_action": "NORMAL",
        "recent_ipo_detail": detail or "非次新股，不启用次新专属分支",
    }


def evaluate_recent_ipo_platform(d: pd.DataFrame) -> Dict[str, Any]:
    x = add_deep_indicators(d)
    if x.empty or len(x) < RECENT_IPO_MIN_PLATFORM_DAYS:
        return {
            "valid": False, "score": 0.0, "detail": "上市后平台样本不足",
            "upper": 0.0, "lower": 0.0, "window": 0, "amp_pct": 0.0,
            "vol_cv": 0.0, "touch_upper": 0, "touch_lower": 0, "long_bear_count": 0,
        }
    completed = x.iloc[:-1].copy().reset_index(drop=True) if len(x) > 1 else x.copy().reset_index(drop=True)
    if len(completed) < RECENT_IPO_MIN_PLATFORM_DAYS:
        return {
            "valid": False, "score": 0.0, "detail": "上市后平台未完成，需更多交易日",
            "upper": 0.0, "lower": 0.0, "window": 0, "amp_pct": 0.0,
            "vol_cv": 0.0, "touch_upper": 0, "touch_lower": 0, "long_bear_count": 0,
        }
    max_window = min(RECENT_IPO_PLATFORM_MAX_WINDOW, len(completed))
    min_window = min(max(RECENT_IPO_PLATFORM_MIN_WINDOW, 12), max_window)
    best: Dict[str, Any] = {}
    for win in range(min_window, max_window + 1):
        seg = completed.tail(win).copy().reset_index(drop=True)
        close_mid = sf(seg["close"].median())
        if close_mid <= 0:
            continue
        high_max = sf(seg["high"].max())
        low_min = sf(seg["low"].min())
        amp = (high_max - low_min) / max(close_mid, 1e-9)
        atr = sf(seg["range_pct"].replace([np.inf, -np.inf], np.nan).dropna().median()) if "range_pct" in seg.columns else 0.0
        allowed_amp = max(RECENT_IPO_PLATFORM_BASE_AMP, atr * RECENT_IPO_PLATFORM_ATR_MULT)
        allowed_amp = min(max(allowed_amp, 0.18), 0.36)
        volume_mean = sf(seg["volume"].mean())
        vol_cv = sf(seg["volume"].std()) / max(volume_mean, 1e-9) if volume_mean > 0 else 9.99
        upper = max(sf(seg["body_top"].quantile(0.88)) if "body_top" in seg.columns else 0.0, sf(seg["close"].quantile(0.90)))
        upper = max(upper, sf(seg["high"].quantile(0.78)))
        lower = min(sf(seg["body_bottom"].quantile(0.12)) if "body_bottom" in seg.columns else low_min, sf(seg["close"].quantile(0.10)))
        lower = min(lower, sf(seg["low"].quantile(0.22)))
        if upper <= 0 or lower <= 0 or upper <= lower:
            continue
        touch_upper = int(((seg["high"] >= upper * 0.985) | (seg["close"] >= upper * 0.990)).sum())
        touch_lower = int(((seg["low"] <= lower * 1.015) | (seg["close"] <= lower * 1.010)).sum())
        long_bear = (seg["close"] < seg["open"]) & (((seg["open"] - seg["close"]) / seg["close"].shift(1).replace(0, np.nan)) >= 0.035) & (seg["volume"] >= seg["vol_ma20"].fillna(seg["volume"].median()) * 1.35)
        long_bear_count = int(long_bear.sum())
        close_mad = sf((seg["close"] - close_mid).abs().median()) / max(close_mid, 1e-9)
        score = 0.0
        reasons: List[str] = []
        if amp <= allowed_amp:
            score += 4.0; reasons.append(f"振幅{amp:.1%}<=阈值{allowed_amp:.1%}")
        elif amp <= allowed_amp * 1.20:
            score += 1.5; reasons.append(f"振幅略宽{amp:.1%}")
        else:
            reasons.append(f"振幅过宽{amp:.1%}")
        if vol_cv <= 0.85:
            score += 2.5; reasons.append(f"量能趋稳CV{vol_cv:.2f}")
        elif vol_cv <= 1.20:
            score += 1.0; reasons.append(f"量能波动可接受CV{vol_cv:.2f}")
        if touch_upper >= 2:
            score += 1.8; reasons.append(f"上沿触碰{touch_upper}次")
        if touch_lower >= 2:
            score += 1.6; reasons.append(f"下沿承接{touch_lower}次")
        if close_mad <= 0.075:
            score += 1.4; reasons.append(f"收盘集中{close_mad:.1%}")
        if long_bear_count == 0:
            score += 1.5; reasons.append("无放量长阴破坏")
        elif long_bear_count == 1:
            score -= 1.0; reasons.append("有1次放量长阴")
        else:
            score -= 4.0; reasons.append(f"放量长阴{long_bear_count}次")
        valid = bool(score >= 7.0 and amp <= allowed_amp * 1.20 and touch_upper >= 2 and touch_lower >= 1 and long_bear_count <= 1)
        item = {
            "valid": valid, "score": rd(score, 2), "detail": "；".join(reasons),
            "upper": rd(upper, 3), "lower": rd(lower, 3), "window": int(win),
            "amp_pct": rd(amp * 100.0, 2), "vol_cv": rd(vol_cv, 3),
            "touch_upper": int(touch_upper), "touch_lower": int(touch_lower),
            "long_bear_count": int(long_bear_count),
        }
        if not best or (item["valid"], item["score"], -item["amp_pct"]) > (best.get("valid", False), sf(best.get("score")), -sf(best.get("amp_pct"))):
            best = item
    if not best:
        return {
            "valid": False, "score": 0.0, "detail": "未识别到可用上市后平台",
            "upper": 0.0, "lower": 0.0, "window": 0, "amp_pct": 0.0,
            "vol_cv": 0.0, "touch_upper": 0, "touch_lower": 0, "long_bear_count": 0,
        }
    return best


def evaluate_recent_ipo_context(d: pd.DataFrame) -> Dict[str, Any]:
    x = add_deep_indicators(d)
    if x.empty:
        return recent_ipo_empty_context(x, "数据为空")
    listing_age = int(len(x))
    first = x.iloc[0]
    first_date = ss(first.get("date", ""))
    if listing_age > RECENT_IPO_SPECIAL_MAX_DAYS:
        return recent_ipo_empty_context(x, "上市时间已超过次新专属窗口，使用普通股逻辑")
    completed = x.iloc[:-1].copy().reset_index(drop=True) if len(x) > 1 else x.copy().reset_index(drop=True)
    platform = evaluate_recent_ipo_platform(x)
    amount_series = pd.to_numeric(completed.get("amount", pd.Series(dtype=float)), errors="coerce").fillna(0.0) if not completed.empty else pd.Series(dtype=float)
    if not completed.empty and len(amount_series) == len(completed) and float(amount_series.max() if len(amount_series) else 0.0) > 0:
        max_amount_idx = int(amount_series.idxmax())
        max_amount_row = completed.iloc[max_amount_idx]
    elif not completed.empty:
        volume_series = pd.to_numeric(completed.get("volume", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
        max_amount_idx = int(volume_series.idxmax()) if len(volume_series) else 0
        max_amount_row = completed.iloc[max_amount_idx]
    else:
        max_amount_row = first
    post_high = sf(completed["high"].max()) if not completed.empty else sf(first.high)
    post_high_date = ""
    try:
        hi_idx = pd.to_numeric(completed["high"], errors="coerce").idxmax() if not completed.empty else 0
        post_high_date = ss(completed.loc[hi_idx].get("date", "")) if not completed.empty else first_date
    except Exception:
        post_high_date = ""
    maturity = 0.0
    reasons: List[str] = [f"上市{listing_age}个交易日"]
    if listing_age >= RECENT_IPO_MIN_FORMAL_DAYS:
        maturity += 4.0; reasons.append("样本达到正式评估下限")
    elif listing_age >= RECENT_IPO_MIN_PLATFORM_DAYS:
        maturity += 2.0; reasons.append("样本仅够观察")
    else:
        reasons.append("样本不足，筹码结构未沉淀")
    if bool(platform.get("valid")):
        maturity += min(4.0, sf(platform.get("score")) * 0.45); reasons.append(f"上市后平台有效：{platform.get('detail')}")
    else:
        reasons.append(f"上市后平台未确认：{platform.get('detail')}")
    max_amount_high = sf(max_amount_row.get("high", 0.0))
    max_amount_body_top = max(sf(max_amount_row.get("open", 0.0)), sf(max_amount_row.get("close", 0.0)))
    max_amount_body_bottom = min(sf(max_amount_row.get("open", 0.0)), sf(max_amount_row.get("close", 0.0)))
    maturity = clamp(maturity, 0, 10)
    if listing_age < RECENT_IPO_MIN_PLATFORM_DAYS:
        action = "HARD_REJECT"
        stage = "次新样本不足"
    elif listing_age < RECENT_IPO_MIN_FORMAL_DAYS:
        action = "OBSERVE_ONLY"
        stage = "次新观察期"
    elif not bool(platform.get("valid")):
        action = "OBSERVE_ONLY"
        stage = "次新平台未确认"
    else:
        action = "ALLOW_FORMAL_IF_CONFIRMED"
        stage = "次新平台成熟"
    return {
        "is_recent_ipo": True,
        "recent_ipo_flag": True,
        "listing_age_days": listing_age,
        "first_trade_date": first_date,
        "recent_ipo_stage": stage,
        "recent_ipo_maturity_score": rd(maturity, 2),
        "recent_ipo_platform_valid": bool(platform.get("valid")),
        "recent_ipo_platform_score": platform.get("score", 0),
        "recent_ipo_platform_upper": platform.get("upper", 0),
        "recent_ipo_platform_lower": platform.get("lower", 0),
        "recent_ipo_platform_window": platform.get("window", 0),
        "recent_ipo_platform_amp_pct": platform.get("amp_pct", 0),
        "recent_ipo_max_amount_high": rd(max_amount_high, 3),
        "recent_ipo_max_amount_body_top": rd(max_amount_body_top, 3),
        "recent_ipo_max_amount_body_bottom": rd(max_amount_body_bottom, 3),
        "recent_ipo_ipo_day_high": rd(sf(first.get("high", 0.0)), 3),
        "recent_ipo_ipo_day_close": rd(sf(first.get("close", 0.0)), 3),
        "recent_ipo_post_high": rd(post_high, 3),
        "recent_ipo_post_high_date": post_high_date,
        "recent_ipo_action": action,
        "recent_ipo_detail": "；".join(reasons),
    }


def _score_recent_ipo_line(d: pd.DataFrame, line: float, source: str) -> Dict[str, Any]:
    x = normalize_hist(d)
    L = sf(line)
    if x.empty or L <= 0:
        return {"line": None, "level": "未识别", "reason": "次新筹码线无效", "line_label": "次新上市后筹码线"}
    completed = x.iloc[:-1].copy().reset_index(drop=True) if len(x) > 1 else x.copy().reset_index(drop=True)
    if completed.empty:
        return {"line": None, "level": "未识别", "reason": "次新筹码线样本不足", "line_label": "次新上市后筹码线"}
    body_top = completed[["open", "close"]].max(axis=1)
    body_bottom = completed[["open", "close"]].min(axis=1)
    vol = pd.to_numeric(completed.get("volume", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    vol_med = sf(vol.median())
    tol = max(CORE_LINE_TOL, 0.012)
    high_touch = (abs(completed["high"] - L) / max(L, 1e-9) <= tol)
    body_touch = (abs(body_top - L) / max(L, 1e-9) <= tol)
    close_touch = (abs(completed["close"] - L) / max(L, 1e-9) <= tol)
    upper_shadow = (body_top <= L) & (L <= completed["high"])
    touch = high_touch | body_touch | close_touch | upper_shadow
    volume_touch = touch & (vol >= vol_med * 1.25) if vol_med > 0 else touch & False
    entity_cut = (body_bottom < L) & (L < body_top)
    hit = int(touch.sum())
    vol_hit = int(volume_touch.sum())
    cut = int(entity_cut.sum())
    net = hit + vol_hit * 0.8 - cut * 0.35
    level = "核心线候选" if hit >= 2 and net > 0 else "未成线"
    return {
        "line": rd(L, 3),
        "score": rd(hit, 3),
        "net_score": rd(net, 3),
        "effective_resonance_count": hit,
        "volume_resonance_count": vol_hit,
        "entity_cut_count": cut,
        "level": level,
        "line_type": "recent_ipo_core_line" if level == "核心线候选" else "non_core",
        "timeframe": "上市后日K",
        "current_state": "次新上市后筹码交换线",
        "source": source,
        "line_label": "次新上市后筹码线",
        "lookback_days": int(len(completed)),
        "rank_mode": "recent_ipo",
    }


def choose_recent_ipo_core_line(df: pd.DataFrame) -> Dict[str, Any]:
    x = normalize_hist(df)
    ctx = evaluate_recent_ipo_context(x)
    if not bool(ctx.get("is_recent_ipo")):
        return {"line": None, "level": "非次新", "reason": "非次新股，不启用次新筹码线", "line_label": "次新上市后筹码线", "recent_ipo_context": ctx}
    if int(sf(ctx.get("listing_age_days"))) < RECENT_IPO_MIN_PLATFORM_DAYS:
        return {"line": None, "level": "样本不足", "reason": "次新上市后交易日不足，不生成正式筹码线", "line_label": "次新上市后筹码线", "recent_ipo_context": ctx}
    raw_candidates: List[Tuple[float, str, float]] = []
    def add(price: Any, source: str, weight: float) -> None:
        v = sf(price)
        if v > 0:
            raw_candidates.append((v, source, weight))
    add(ctx.get("recent_ipo_ipo_day_high"), "上市首日高点", 3.0)
    add(ctx.get("recent_ipo_ipo_day_close"), "上市首日收盘", 0.5)
    add(ctx.get("recent_ipo_max_amount_high"), "上市后最大成交额K高点", 4.0)
    add(ctx.get("recent_ipo_max_amount_body_top"), "上市后最大成交额K实体顶", 3.5)
    add(ctx.get("recent_ipo_post_high"), "上市以来高点", 3.0)
    if bool(ctx.get("recent_ipo_platform_valid")):
        add(ctx.get("recent_ipo_platform_upper"), "上市后平台上沿", 10.0)
    if not raw_candidates:
        return {"line": None, "level": "未识别", "reason": "次新未识别上市后筹码候选线", "line_label": "次新上市后筹码线", "recent_ipo_context": ctx}
    scored: List[Dict[str, Any]] = []
    for price, source, weight in raw_candidates:
        item = _score_recent_ipo_line(x, price, source)
        item["recent_ipo_source_weight"] = rd(weight, 2)
        item["recent_ipo_context"] = ctx
        last_close_for_rank = sf(x.iloc[-1].get("close", 0.0)) if not x.empty else 0.0
        line_distance_pct = pct_change(last_close_for_rank, sf(item.get("line"))) if last_close_for_rank > 0 and sf(item.get("line")) > 0 else 0.0
        distance_bonus = 0.0
        if 0.0 <= line_distance_pct <= 10.0:
            distance_bonus = 3.0
        elif 10.0 < line_distance_pct <= 15.0:
            distance_bonus = 1.0
        elif line_distance_pct > 15.0:
            distance_bonus = -min(8.0, (line_distance_pct - 15.0) * 0.65)
        platform_bonus = 5.0 if bool(ctx.get("recent_ipo_platform_valid")) and "平台上沿" in source else 0.0
        item["recent_ipo_line_distance_pct"] = rd(line_distance_pct, 2)
        item["recent_ipo_line_rank_score"] = rd(sf(item.get("net_score")) * 0.55 + weight + platform_bonus + distance_bonus + sf(item.get("volume_resonance_count")) * 0.6, 3)
        if line_distance_pct > 18.0:
            item["level"] = "过远低线"
            item["line_type"] = "non_core"
        if ss(item.get("line_type")) == "recent_ipo_core_line":
            scored.append(item)
    if not scored:
        fallback = max((_score_recent_ipo_line(x, price, source) for price, source, _ in raw_candidates), key=lambda z: sf(z.get("net_score")))
        fallback["recent_ipo_context"] = ctx
        fallback["reason"] = "次新候选线共振不足，仅作观察"
        return fallback
    best = max(scored, key=lambda z: (sf(z.get("recent_ipo_line_rank_score")), sf(z.get("volume_resonance_count")), sf(z.get("effective_resonance_count"))))
    best["top_candidates"] = sorted(scored, key=lambda z: sf(z.get("recent_ipo_line_rank_score")), reverse=True)[:6]
    return best


def build_recent_ipo_pressure_profile(d: pd.DataFrame, bidx: int, last_close: float, end_idx: Any = None) -> Dict[str, Any]:
    x = normalize_hist(d)
    ctx = evaluate_recent_ipo_context(x)
    listing_age = int(sf(ctx.get("listing_age_days")))
    if x.empty or last_close <= 0:
        return {"pressure_found": False, "target_reliable": False, "target_price": 0.0, "space_pct": 0.0, "pricing_mode": "次新数据不足", "pressure_horizon": "次新", "target_type": "次新压力样本不足", "pressure_audit_detail": "次新压力样本不足", **ctx}
    if end_idx is None:
        end_idx = len(x) - 1
    end_idx = max(1, min(int(end_idx), len(x)))
    completed = x.iloc[:end_idx].copy().reset_index(drop=True)
    levels: List[Tuple[float, str, bool, float]] = []
    def add_level(price: Any, label: str, reliable: bool, weight: float) -> None:
        v = sf(price)
        if v > 0 and v >= last_close * (1.0 + OVERHEAD_PRESSURE_MIN_ABOVE_PCT):
            levels.append((v, label, reliable, weight))
    add_level(ctx.get("recent_ipo_post_high"), "上市以来高点", True, 5.0)
    add_level(ctx.get("recent_ipo_ipo_day_high"), "上市首日高点", True, 4.0)
    add_level(ctx.get("recent_ipo_max_amount_high"), "最大成交额K高点", True, 4.5)
    add_level(ctx.get("recent_ipo_max_amount_body_top"), "最大成交额K实体顶", True, 3.5)
    if bool(ctx.get("recent_ipo_platform_valid")):
        add_level(ctx.get("recent_ipo_platform_upper"), "上市后平台上沿", True, 5.0)
    if levels:
        price, label, reliable, weight = sorted(levels, key=lambda z: (z[0], -z[3]))[0]
        space = pct_change(price, last_close)
        audit = f"次新上市后压力:{label}{price:.2f}；上市{listing_age}日；平台有效={bool(ctx.get('recent_ipo_platform_valid'))}；最大成交额K高点{ctx.get('recent_ipo_max_amount_high')}；上市以来高点{ctx.get('recent_ipo_post_high')}"
        return {
            "pressure_found": True,
            "target_reliable": bool(reliable),
            "target_price": rd(price, 3),
            "space_pct": rd(space, 2),
            "target_quality": "valid" if reliable else "weak",
            "pricing_mode": "次新上市后筹码压力定价",
            "pressure_horizon": "次新上市后",
            "target_type": f"次新{label}",
            "near_pressure_price": 0.0,
            "mid_pressure_price": 0.0,
            "long_pressure_price": 0.0,
            "full_pressure_price": rd(price, 3),
            "near_pressure_quality": "recent_ipo",
            "mid_pressure_quality": "recent_ipo",
            "long_pressure_quality": "recent_ipo",
            "full_pressure_quality": "valid" if reliable else "weak",
            "full_history_high": ctx.get("recent_ipo_post_high", 0),
            "full_history_high_date": ctx.get("recent_ipo_post_high_date", ""),
            "pressure_audit_detail": audit,
            "pressure_scan_sample_days": int(len(completed)),
            "recent_ipo_price_discovery_ok": False,
            **ctx,
        }
    platform_ok = bool(ctx.get("recent_ipo_platform_valid"))
    price_discovery_ok = bool(listing_age >= RECENT_IPO_MIN_FORMAL_DAYS and platform_ok)
    pricing_mode = "次新上市后价格发现" if price_discovery_ok else "次新历史压力不足"
    target_type = "次新突破上市后筹码区进入价格发现" if price_discovery_ok else "次新样本短/平台不足，不按价格发现加分"
    return {
        "pressure_found": False,
        "target_reliable": False,
        "target_price": 0.0,
        "space_pct": 0.0,
        "target_quality": "none",
        "pricing_mode": pricing_mode,
        "pressure_horizon": "次新上市后",
        "target_type": target_type,
        "near_pressure_price": 0.0,
        "mid_pressure_price": 0.0,
        "long_pressure_price": 0.0,
        "full_pressure_price": 0.0,
        "near_pressure_quality": "recent_ipo",
        "mid_pressure_quality": "recent_ipo",
        "long_pressure_quality": "recent_ipo",
        "full_pressure_quality": "none",
        "full_history_high": ctx.get("recent_ipo_post_high", 0),
        "full_history_high_date": ctx.get("recent_ipo_post_high_date", ""),
        "pressure_audit_detail": f"次新无上方筹码压力；上市{listing_age}日；平台有效={platform_ok}；上市以来高点{ctx.get('recent_ipo_post_high')}；不套用老股全历史价格发现",
        "pressure_scan_sample_days": int(len(completed)),
        "recent_ipo_price_discovery_ok": price_discovery_ok,
        **ctx,
    }

def _pressure_empty_scan(horizon_key: str, horizon_label: str, lookback_days: int, sample_days: int, reason: str) -> Dict[str, Any]:

    return {

        "horizon_key": horizon_key,

        "horizon_label": horizon_label,

        "lookback_days": int(lookback_days),

        "sample_days": int(sample_days),

        "pressure_found": False,

        "target_reliable": False,

        "target_price": 0.0,

        "space_pct": 0.0,

        "target_quality": "none",

        "hit_count": 0,

        "volume_hit_count": 0,

        "band_low": 0.0,

        "band_high": 0.0,

        "full_high": 0.0,

        "full_high_date": "",

        "detail": reason,

    }


def _price_event_rows(window: pd.DataFrame, last_close: float) -> List[Dict[str, Any]]:

    if window.empty or last_close <= 0:

        return []

    threshold = last_close * (1.0 + OVERHEAD_PRESSURE_MIN_ABOVE_PCT)

    events: List[Dict[str, Any]] = []

    for pos, (_, r) in enumerate(window.iterrows()):

        open_ = sf(r.get("open", 0.0))

        high = sf(r.get("high", 0.0))

        close = sf(r.get("close", 0.0))

        volume = sf(r.get("volume", 0.0))

        if high <= 0 or close <= 0 or open_ <= 0:

            continue

        body_top = max(open_, close)

        for price, source in [(high, "最高价"), (body_top, "实体顶"), (close, "收盘价")]:

            if price >= threshold:

                events.append({

                    "price": float(price),

                    "row_pos": int(pos),

                    "date": ss(r.get("date", "")),

                    "volume": float(volume),

                    "source": source,

                })

    return sorted(events, key=lambda x: sf(x.get("price")))


def _score_pressure_group(group: List[Dict[str, Any]], vol_med: float, last_close: float) -> Dict[str, Any]:

    prices = [sf(x.get("price")) for x in group if sf(x.get("price")) > 0]

    if not prices:

        return {}

    row_ids = set(int(sf(x.get("row_pos"))) for x in group)

    unique_hits = len(row_ids)

    volume_rows = set(

        int(sf(x.get("row_pos")))

        for x in group

        if vol_med > 0 and sf(x.get("volume")) >= vol_med * 1.30

    )

    volume_hits = len(volume_rows)

    body_or_close_events = sum(1 for x in group if ss(x.get("source")) in {"实体顶", "收盘价"})

    pressure_score = unique_hits + volume_hits * 0.70 + body_or_close_events * 0.15

    if unique_hits >= max(4, OVERHEAD_PRESSURE_MIN_RELIABLE_HITS + 2) or volume_hits >= 2 or pressure_score >= 5.0:

        quality = "strong"

    elif unique_hits >= OVERHEAD_PRESSURE_MIN_RELIABLE_HITS or volume_hits >= 1:

        quality = "valid"

    else:

        quality = "weak"

    band_low = min(prices)

    band_high = max(prices)

    pressure_price = max(last_close * (1.0 + OVERHEAD_PRESSURE_MIN_ABOVE_PCT), band_low)

    return {

        "pressure_found": True,

        "target_reliable": quality in {"strong", "valid"},

        "target_price": rd(pressure_price, 3),

        "target_quality": quality,

        "hit_count": int(unique_hits),

        "volume_hit_count": int(volume_hits),

        "band_low": rd(band_low, 3),

        "band_high": rd(band_high, 3),

        "space_pct": rd(pct_change(pressure_price, last_close), 2),

        "pressure_score": rd(pressure_score, 2),

        "sample_dates": ",".join([ss(x.get("date")) for x in group[:3] if ss(x.get("date"))]),

    }


def scan_overhead_pressure_window(window: pd.DataFrame, last_close: float, horizon_key: str, horizon_label: str, lookback_days: int) -> Dict[str, Any]:

    w = normalize_hist(window)

    sample_days = int(len(w))

    if w.empty or last_close <= 0:

        return _pressure_empty_scan(horizon_key, horizon_label, lookback_days, sample_days, f"{horizon_label}样本不足")

    high_series = pd.to_numeric(w.get("high", pd.Series(dtype=float)), errors="coerce").dropna()

    if high_series.empty:

        return _pressure_empty_scan(horizon_key, horizon_label, lookback_days, sample_days, f"{horizon_label}高点数据无效")

    full_high = sf(high_series.max())

    full_high_date = ""

    try:

        full_high_idx = pd.to_numeric(w["high"], errors="coerce").idxmax()

        full_high_date = ss(w.loc[full_high_idx].get("date", ""))

    except Exception:

        full_high_date = ""

    threshold = last_close * (1.0 + OVERHEAD_PRESSURE_MIN_ABOVE_PCT)

    if full_high < threshold:

        out = _pressure_empty_scan(horizon_key, horizon_label, lookback_days, sample_days, f"{horizon_label}无高于当前{OVERHEAD_PRESSURE_MIN_ABOVE_PCT:.1%}的历史压力")

        out.update({"full_high": rd(full_high, 3), "full_high_date": full_high_date})

        return out

    events = _price_event_rows(w, last_close)

    if not events:

        out = _pressure_empty_scan(horizon_key, horizon_label, lookback_days, sample_days, f"{horizon_label}无有效压力事件")

        out.update({"full_high": rd(full_high, 3), "full_high_date": full_high_date})

        return out

    vol_med = sf(pd.to_numeric(w.get("volume", pd.Series(dtype=float)), errors="coerce").dropna().median())

    groups: List[List[Dict[str, Any]]] = []

    cur: List[Dict[str, Any]] = []

    base = 0.0

    for ev in events:

        price = sf(ev.get("price"))

        if price <= 0:

            continue

        if not cur:

            cur = [ev]

            base = price

        elif base > 0 and abs(price - base) / base <= OVERHEAD_PRESSURE_BAND_TOL:

            cur.append(ev)

        else:

            groups.append(cur)

            cur = [ev]

            base = price

    if cur:

        groups.append(cur)

    scored = []

    for group in groups:

        item = _score_pressure_group(group, vol_med, last_close)

        if item:

            scored.append(item)

    if not scored:

        out = _pressure_empty_scan(horizon_key, horizon_label, lookback_days, sample_days, f"{horizon_label}压力分组无效")

        out.update({"full_high": rd(full_high, 3), "full_high_date": full_high_date})

        return out

    reliable = [x for x in scored if bool(x.get("target_reliable"))]

    picked = min(reliable, key=lambda x: sf(x.get("target_price"))) if reliable else min(scored, key=lambda x: sf(x.get("target_price")))

    picked.update({

        "horizon_key": horizon_key,

        "horizon_label": horizon_label,

        "lookback_days": int(lookback_days),

        "sample_days": sample_days,

        "full_high": rd(full_high, 3),

        "full_high_date": full_high_date,

        "detail": f"{horizon_label}压力{picked.get('target_price')}，质量{picked.get('target_quality')}，共振{picked.get('hit_count')}次/带量{picked.get('volume_hit_count')}次，区间{picked.get('band_low')}-{picked.get('band_high')}",

    })

    return picked


def build_overhead_pressure_profile(d: pd.DataFrame, bidx: int, last_close: float, end_idx: Any = None) -> Dict[str, Any]:

    x = normalize_hist(d)

    if x.empty or last_close <= 0:

        return {

            "pressure_found": False,

            "target_reliable": False,

            "target_price": 0.0,

            "space_pct": 0.0,

            "pricing_mode": "数据不足",

            "pressure_horizon": "无",

            "target_type": "压力样本不足",

            "pressure_audit_detail": "压力样本不足",

        }

    if end_idx is None:

        end_idx = int(bidx)

    end_idx = max(1, min(int(end_idx), len(x)))

    pre_all = x.iloc[:end_idx].copy().reset_index(drop=True)

    if pre_all.empty:

        return {

            "pressure_found": False,

            "target_reliable": False,

            "target_price": 0.0,

            "space_pct": 0.0,

            "pricing_mode": "数据不足",

            "pressure_horizon": "无",

            "target_type": "压力样本不足",

            "pressure_audit_detail": "压力样本不足",

        }

    horizons = [

        ("near", "近520日", OVERHEAD_PRESSURE_NEAR_DAYS),

        ("mid", "近1200日", OVERHEAD_PRESSURE_MID_DAYS),

        ("long", "近2400日", OVERHEAD_PRESSURE_LONG_DAYS),

        ("full", "全历史", 0),

    ]

    scans: Dict[str, Dict[str, Any]] = {}

    for key, label, days in horizons:

        win = pre_all if days <= 0 or len(pre_all) <= days else pre_all.tail(days)

        scans[key] = scan_overhead_pressure_window(win, last_close, key, label, days)

    chosen: Dict[str, Any] = {}

    for key in ["near", "mid", "long", "full"]:

        item = scans.get(key, {})

        if bool(item.get("pressure_found")) and bool(item.get("target_reliable")):

            chosen = dict(item)

            break

    if not chosen:

        weak_items = [scans[k] for k in ["near", "mid", "long", "full"] if bool(scans.get(k, {}).get("pressure_found"))]

        if weak_items:

            chosen = min(weak_items, key=lambda x: sf(x.get("target_price")))

    full_scan = scans.get("full", {})

    if chosen:

        hkey = ss(chosen.get("horizon_key"))

        reliable = bool(chosen.get("target_reliable"))

        if not reliable:

            pricing_mode = "弱压力参考"

            target_type = f"{chosen.get('horizon_label', '')}弱压力参考"

        elif hkey == "near":

            pricing_mode = "近端历史压力定价"

            target_type = "近端历史压力"

        elif hkey in {"mid", "long"}:

            pricing_mode = "近端真空｜中远期压力定价"

            target_type = f"{chosen.get('horizon_label', '')}历史压力"

        else:

            pricing_mode = "近端真空｜全历史压力定价"

            target_type = "全历史远端压力"

        target_price = sf(chosen.get("target_price"))

        space_pct = pct_change(target_price, last_close) if target_price > 0 else 0.0

        out = dict(chosen)

        out.update({

            "pressure_found": bool(target_price > 0),

            "target_reliable": reliable,

            "target_price": rd(target_price, 3),

            "space_pct": rd(space_pct, 2),

            "pricing_mode": pricing_mode,

            "pressure_horizon": chosen.get("horizon_label", ""),

            "target_type": target_type,

        })

    else:

        pricing_mode = "全历史价格发现" if sf(full_scan.get("full_high")) < last_close * (1.0 + OVERHEAD_PRESSURE_MIN_ABOVE_PCT) else "压力未成线"

        target_type = "全历史上方无有效历史压力" if pricing_mode == "全历史价格发现" else "存在上影/毛刺但未成可靠压力"

        out = {

            "pressure_found": False,

            "target_reliable": False,

            "target_price": 0.0,

            "space_pct": 0.0,

            "target_quality": "none",

            "pricing_mode": pricing_mode,

            "pressure_horizon": "全历史",

            "target_type": target_type,

            "full_high": full_scan.get("full_high", 0.0),

            "full_high_date": full_scan.get("full_high_date", ""),

        }

    def p(key: str, field: str = "target_price") -> float:

        return rd(scans.get(key, {}).get(field, 0.0), 3)

    audit_parts = []

    for key in ["near", "mid", "long", "full"]:

        item = scans.get(key, {})

        if bool(item.get("pressure_found")):

            audit_parts.append(f"{item.get('horizon_label')}:{item.get('target_price')}({item.get('target_quality')},共振{item.get('hit_count')})")

        else:

            audit_parts.append(f"{item.get('horizon_label')}:{item.get('detail')}")

    out.update({

        "near_pressure_price": p("near"),

        "mid_pressure_price": p("mid"),

        "long_pressure_price": p("long"),

        "full_pressure_price": p("full"),

        "near_pressure_quality": scans.get("near", {}).get("target_quality", "none"),

        "mid_pressure_quality": scans.get("mid", {}).get("target_quality", "none"),

        "long_pressure_quality": scans.get("long", {}).get("target_quality", "none"),

        "full_pressure_quality": scans.get("full", {}).get("target_quality", "none"),

        "full_history_high": scans.get("full", {}).get("full_high", 0.0),

        "full_history_high_date": scans.get("full", {}).get("full_high_date", ""),

        "pressure_audit_detail": "；".join(audit_parts),

        "pressure_scan_sample_days": int(len(pre_all)),

    })

    return out


def first_real_pressure_before_breakout(d: pd.DataFrame, bidx: int, last_close: float) -> Dict[str, Any]:

    return build_overhead_pressure_profile(d, bidx, last_close, end_idx=bidx)


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

    if bool(pressure.get("pressure_found")) and bool(pressure.get("target_reliable", True)):

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

        reasons.append(ss(pressure.get("target_type")) or "无可靠历史上方压力，赔率不虚构")

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

def _score_by_thresholds(value: float, steps: List[Tuple[float, float]], default: float = 0.0) -> float:
    v = sf(value)
    score = default
    for threshold, step_score in steps:
        if v >= threshold:
            score = step_score
    return score

def _line_resonance_bonus(line_info: Dict[str, Any], max_bonus: float) -> float:
    hit = int(sf(line_info.get("effective_resonance_count")))
    vol_hit = int(sf(line_info.get("volume_resonance_count")))
    net = sf(line_info.get("net_score"))
    raw = max(0.0, (hit - 3) * 0.35 + vol_hit * 0.65 + max(0.0, net) * 0.025)
    return rd(min(max_bonus, raw), 2)

def _line_pair_distance_pct(a: float, b: float) -> float:
    a = sf(a); b = sf(b)
    if a <= 0 or b <= 0:
        return 999.0
    return abs(a - b) / max(a, b) * 100.0

def score_core_line_level(line_type: str, line_info: Dict[str, Any], hit_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ctx = hit_context or {}
    typ = ss(line_type)
    historical_hit = bool(ctx.get("historical_hit"))
    five_hit = bool(ctx.get("five_hundred_hit"))
    recent_ipo_hit = bool(ctx.get("recent_ipo_hit"))
    historical_price = sf(ctx.get("historical_price"))
    five_price = sf(ctx.get("five_hundred_price"))
    historical_date = ss(ctx.get("historical_breakout_date"))
    five_date = ss(ctx.get("five_hundred_breakout_date"))
    pair_dist = _line_pair_distance_pct(historical_price, five_price)
    reasons: List[str] = []

    if historical_hit and five_hit and pair_dist <= 5.0:
        base = 22.0
        if pair_dist <= 3.0:
            base += 1.0
        if pair_dist <= 1.5:
            base += 1.0
        if historical_date and historical_date == five_date:
            base += 0.7
        bonus = min(1.3, _line_resonance_bonus(ctx.get("historical_line", {}) or {}, 0.7) + _line_resonance_bonus(ctx.get("five_hundred_line", {}) or {}, 0.7))
        score = clamp(base + bonus, 22.0, 25.0)
        reasons.append(f"历史线+500日线双线共振，距离{pair_dist:.1f}%")
        return {"score": rd(score, 2), "detail": "；".join(reasons), "line_level_type": "双线共振突破", "dual_line_distance_pct": rd(pair_dist, 2)}

    if "历史" in typ:
        score = 18.0 + _line_resonance_bonus(line_info, 2.0)
        score = clamp(score, 18.0, 20.0)
        reasons.append(f"历史自然月核心线，共振{int(sf(line_info.get('effective_resonance_count')))}次/带量{int(sf(line_info.get('volume_resonance_count')))}次")
        return {"score": rd(score, 2), "detail": "；".join(reasons), "line_level_type": "历史核心线突破", "dual_line_distance_pct": rd(pair_dist, 2)}

    if "五百" in typ or "500" in typ:
        score = 15.0 + _line_resonance_bonus(line_info, 3.0)
        score = clamp(score, 15.0, 18.0)
        reasons.append(f"近500日辅助核心线，共振{int(sf(line_info.get('effective_resonance_count')))}次/带量{int(sf(line_info.get('volume_resonance_count')))}次")
        return {"score": rd(score, 2), "detail": "；".join(reasons), "line_level_type": "500日辅助线突破", "dual_line_distance_pct": rd(pair_dist, 2)}

    if "次新" in typ or recent_ipo_hit:
        score = 15.0 + _line_resonance_bonus(line_info, 3.0)
        score = clamp(score, 15.0, 18.0)
        reasons.append("次新上市后筹码线，按辅助核心线处理")
        return {"score": rd(score, 2), "detail": "；".join(reasons), "line_level_type": "次新筹码线突破", "dual_line_distance_pct": rd(pair_dist, 2)}

    score = clamp(12.0 + _line_resonance_bonus(line_info, 3.0), 0.0, 18.0)
    reasons.append("未知线型，保守给分")
    return {"score": rd(score, 2), "detail": "；".join(reasons), "line_level_type": typ or "未知线型", "dual_line_distance_pct": rd(pair_dist, 2)}

def score_breakout_k_quality(d: pd.DataFrame, bidx: int, line: float, fund: Dict[str, Any], events: Dict[str, Any]) -> Dict[str, Any]:
    if d.empty or bidx <= 0 or bidx >= len(d) or line <= 0:
        return {"score": 0.0, "detail": "突破K样本不足", "entity_score": 0, "direction_score": 0, "body_score": 0, "close_control_score": 0, "volume_score": 0, "pattern_score": 0}
    b = d.iloc[bidx]
    prev = d.iloc[bidx - 1]
    prev_close = sf(prev.close)
    open_ = sf(b.open); high = sf(b.high); low = sf(b.low); close = sf(b.close)
    feat = kline_features(b, prev_close=prev_close, line=line)
    entity_ratio = sf(feat.get("entity_above_line_ratio"))
    body_ratio = sf(feat.get("body_ratio"))
    close_pos = sf(feat.get("close_pos"))
    vol_ratio = sf(fund.get("volume_ratio"))
    is_true_yang = close > open_
    is_fake_yin_true_yang = close < open_ and close > prev_close

    entity_score = 0.0
    if entity_ratio >= 0.80:
        entity_score = 10.0
    elif entity_ratio >= 0.60:
        entity_score = 8.0
    elif entity_ratio >= 0.40:
        entity_score = 6.0
    elif entity_ratio >= 0.20:
        entity_score = 4.0
    elif close >= line:
        entity_score = 2.0

    if is_true_yang:
        direction_score = 5.0 if close > prev_close else 3.0
    elif is_fake_yin_true_yang:
        direction_score = 3.0
    elif close > prev_close:
        direction_score = 1.0
    else:
        direction_score = 0.0

    if body_ratio >= 0.80:
        body_score = 5.0
    elif body_ratio >= 0.65:
        body_score = 4.0
    elif body_ratio >= 0.45:
        body_score = 3.0
    elif body_ratio >= 0.25:
        body_score = 2.0
    else:
        body_score = 1.0 if body_ratio > 0 else 0.0

    if close_pos >= 0.95:
        close_control_score = 5.0
    elif close_pos >= 0.85:
        close_control_score = 4.0
    elif close_pos >= 0.70:
        close_control_score = 3.0
    elif close_pos >= 0.50:
        close_control_score = 1.0
    else:
        close_control_score = 0.0

    if bool(fund.get("stall")):
        volume_score = 0.0
    elif bool(events.get("limit_up")) and close_pos >= 0.88:
        # 涨停板日成交量可能因早封板失真，不能简单因不足标准倍量重扣。
        if vol_ratio >= 1.2:
            volume_score = 9.0
        elif vol_ratio >= 0.8:
            volume_score = 7.0
        else:
            volume_score = 5.0
    elif 1.8 <= vol_ratio <= 2.5 and is_true_yang:
        volume_score = 10.0
    elif 1.5 <= vol_ratio < 1.8 and is_true_yang:
        volume_score = 8.0
    elif 1.2 <= vol_ratio <= 3.2 and is_true_yang:
        volume_score = 6.0
    elif 0.85 <= vol_ratio < 1.2 and close_pos >= 0.72:
        volume_score = 3.0
    elif vol_ratio > 3.2 and is_true_yang and close_pos >= 0.75:
        volume_score = 4.0
    else:
        volume_score = 1.0 if vol_ratio > 0 else 0.0

    pattern_raw = 0.0
    pattern_reasons: List[str] = []
    if bool(events.get("bullish_engulf")):
        pattern_raw += 2.0; pattern_reasons.append("阳包阴")
    if bool(events.get("separation_line")):
        pattern_raw += 2.0; pattern_reasons.append("分手线")
    if bool(events.get("gap_up")):
        pattern_raw += 2.0; pattern_reasons.append("跳空")
    if bool(events.get("limit_up")):
        pattern_raw += 4.0; pattern_reasons.append("涨停近似")
    if bool(events.get("full_body")):
        pattern_raw += 1.0; pattern_reasons.append("强实体阳线")
    if bool(events.get("gap_up")) and bool(events.get("bullish_engulf")):
        pattern_raw += 1.0
    if bool(events.get("bullish_engulf")) and 1.8 <= vol_ratio <= 2.5:
        pattern_raw += 1.0
    pattern_score = min(5.0, pattern_raw)

    # 高质量突破是三号员工的主事件：核心线被漂亮打穿时，应有足够权重，
    # 否则会被后续多个同源风险扣分项过度压制。这里不盲目给所有突破加分，
    # 只奖励“实体站线 + 收盘控制 + 健康量能 + 非滞涨”的高质量突破。
    high_quality_bonus = 0.0
    high_quality_reasons: List[str] = []
    volume_healthy_for_breakout = (
        (1.5 <= vol_ratio <= 3.2 and is_true_yang)
        or (bool(events.get("limit_up")) and vol_ratio >= 0.8 and close_pos >= 0.88)
    )
    if (not bool(fund.get("stall"))) and entity_ratio >= 0.60 and close_pos >= 0.85 and volume_healthy_for_breakout:
        high_quality_bonus += 4.0
        high_quality_reasons.append("高质量实体突破")
        if entity_ratio >= 0.80:
            high_quality_bonus += 1.5
            high_quality_reasons.append("实体大部在线上")
        if body_ratio >= 0.65:
            high_quality_bonus += 1.0
            high_quality_reasons.append("实体饱满")
        if close_pos >= 0.95:
            high_quality_bonus += 1.0
            high_quality_reasons.append("收盘强控盘")
        if 1.8 <= vol_ratio <= 2.5 and is_true_yang:
            high_quality_bonus += 0.5
            high_quality_reasons.append("标准倍量")
    high_quality_bonus = clamp(high_quality_bonus, 0.0, 8.0)

    total = clamp(entity_score + direction_score + body_score + close_control_score + volume_score + pattern_score + high_quality_bonus, 0.0, 48.0)
    detail = (
        f"实体上线{entity_ratio:.0%}/{entity_score:.1f}分；"
        f"方向{'真阳' if is_true_yang else '假阴真阳' if is_fake_yin_true_yang else '非阳'}{direction_score:.1f}分；"
        f"实体效率{body_ratio:.0%}/{body_score:.1f}分；"
        f"收盘位置{close_pos:.0%}/{close_control_score:.1f}分；"
        f"量比{vol_ratio:.2f}/{volume_score:.1f}分；"
        f"形态{'+'.join(pattern_reasons) if pattern_reasons else '无'}/{pattern_score:.1f}分；"
        f"高质量突破加成{'+'.join(high_quality_reasons) if high_quality_reasons else '无'}/{high_quality_bonus:.1f}分"
    )
    return {
        "score": rd(total, 2),
        "detail": detail,
        "entity_score": rd(entity_score, 2),
        "direction_score": rd(direction_score, 2),
        "body_score": rd(body_score, 2),
        "close_control_score": rd(close_control_score, 2),
        "volume_score": rd(volume_score, 2),
        "pattern_score": rd(pattern_score, 2),
        "high_quality_breakout_bonus": rd(high_quality_bonus, 2),
        "is_true_yang": bool(is_true_yang),
        "is_fake_yin_true_yang": bool(is_fake_yin_true_yang),
        "entity_above_line_ratio": rd(entity_ratio, 3),
        "body_ratio": rd(body_ratio, 3),
        "close_pos": rd(close_pos, 3),
        "volume_ratio": rd(vol_ratio, 2),
    }

def score_acceptance_15(pullback: Dict[str, Any], d: pd.DataFrame, bidx: int, line: float) -> Dict[str, Any]:
    if d.empty or bidx <= 0 or bidx >= len(d) or line <= 0:
        return {"score": 0.0, "detail": "承接样本不足"}
    post = d.iloc[bidx:].copy().reset_index(drop=True)
    if len(post) <= 1:
        return {"score": 8.0, "detail": "突破当天为最新日，承接暂按中性8分"}
    below_count = int((post["close"] < line * 0.992).sum())
    last3_below = int((post.tail(3)["close"] < line * 0.992).sum())
    raw = sf(pullback.get("score"))
    if last3_below >= 2 or sf(post.iloc[-1].close) < line * 0.988:
        score = 2.0
        detail = "突破后快速跌回线下"
    elif below_count > 0:
        score = max(4.0, min(7.0, 8.0 - below_count * 1.2))
        detail = f"突破后曾跌回线下{below_count}次"
    else:
        if raw >= 12.0:
            score = 15.0
        elif raw >= 7.0:
            score = 11.0
        else:
            score = 8.0
        detail = ss(pullback.get("detail")) or "突破后收盘未有效跌回线下"
    return {"score": rd(clamp(score, 0.0, 15.0), 2), "detail": detail}

def score_space_odds_12(trade: Dict[str, Any]) -> Dict[str, Any]:
    space = sf(trade.get("space_pct"))
    rr = sf(trade.get("rr"))
    target_price = sf(trade.get("target_price"))
    target_reliable = bool(trade.get("target_reliable"))
    pricing_mode = ss(trade.get("pricing_mode"))
    risk_pct = sf(trade.get("defense_distance_pct"))
    reasons: List[str] = []

    if target_price > 0 and target_reliable:
        if space >= 18.0:
            score = 12.0
        elif space >= 10.0:
            score = 10.0
        elif space >= 6.0:
            score = 8.0
        elif space >= 3.0:
            score = 5.0
        else:
            score = 2.0
        if rr >= 2.0:
            score += 1.0
        elif rr < 1.05 and score > 3.0:
            score -= 2.0
        reasons.append(f"上方空间{space:.1f}%，RR={rr:.2f}")
    elif pricing_mode in {"全历史价格发现", "次新上市后价格发现"}:
        score = 9.0 if risk_pct <= 8.5 else 7.0
        reasons.append(f"{pricing_mode}，无可靠固定压力，不虚构满分")
    else:
        score = 6.0
        reasons.append("压力不可靠，按中性偏低处理")
    return {"score": rd(clamp(score, 0.0, 12.0), 2), "detail": "；".join(reasons)}

def score_technical_risk_8(risk: Dict[str, Any], fund: Dict[str, Any], trade: Dict[str, Any], data_fresh: bool, recent_ipo: Dict[str, Any]) -> Dict[str, Any]:
    penalty = sf(risk.get("penalty"))
    score = 8.0
    reasons: List[str] = []
    if penalty >= 35.0 or bool(risk.get("block")):
        score = 0.0
        reasons.append(ss(risk.get("detail")) or "硬风险")
    elif penalty >= 25.0:
        score = 1.5; reasons.append(ss(risk.get("detail")))
    elif penalty >= 10.0:
        score = 4.0; reasons.append(ss(risk.get("detail")))
    elif penalty > 0:
        score = 6.0; reasons.append(ss(risk.get("detail")))
    if bool(fund.get("stall")):
        score = min(score, 2.0); reasons.append("放量滞涨")
    if sf(trade.get("defense_distance_pct")) > 12.0:
        score = min(score, 4.0); reasons.append("防守距离过远")
    if not data_fresh:
        score = 0.0; reasons.append("数据日期未对齐")
    if bool(recent_ipo.get("is_recent_ipo")) and ss(recent_ipo.get("recent_ipo_action")) == "HARD_REJECT":
        score = 0.0; reasons.append("次新样本不足")
    return {"score": rd(clamp(score, 0.0, 8.0), 2), "detail": "；".join([x for x in reasons if ss(x)]) or "无明显可落地技术风险"}


def _weighted_component_score(value: Any, raw_max: float, target_weight: float) -> float:
    """把已有底层评分压缩到正式100分权重里，避免各模块原始满分相加超过100。"""
    if raw_max <= 0 or target_weight <= 0:
        return 0.0
    return rd(clamp(sf(value), 0.0, raw_max) / raw_max * target_weight, 2)

def score_context_13(major: Dict[str, Any], supply: Dict[str, Any], eve: Dict[str, Any], activity: Dict[str, Any], timing: Dict[str, Any]) -> Dict[str, Any]:
    """13分独立上下文分：回答“这个突破发生的土壤好不好”，而不是重复奖励突破K本身。"""
    major_part = _weighted_component_score(major.get("score"), 16.0, 3.0)
    supply_part = _weighted_component_score(supply.get("score"), 14.0, 3.0)
    eve_part = _weighted_component_score(eve.get("score"), 18.0, 3.0)
    activity_part = _weighted_component_score(max(0.0, sf(activity.get("score"))), 16.0, 2.0)
    timing_part = _weighted_component_score(timing.get("score"), 10.0, 2.0)
    total = rd(clamp(major_part + supply_part + eve_part + activity_part + timing_part, 0.0, 13.0), 2)

    parts = [
        f"大周期{major_part:.2f}/3：{ss(major.get('type')) or '无'}",
        f"供应吸收{supply_part:.2f}/3：{ss(supply.get('type')) or '无'}",
        f"爆发前夜{eve_part:.2f}/3：{ss(eve.get('type')) or '无'}",
        f"股性活跃{activity_part:.2f}/2：{ss(activity.get('type')) or '无'}",
        f"时间成熟{timing_part:.2f}/2：{ss(timing.get('type')) or '无'}",
    ]
    return {
        "score": total,
        "detail": "；".join(parts),
        "major_component": major_part,
        "supply_component": supply_part,
        "explosion_eve_component": eve_part,
        "activity_component": activity_part,
        "timing_component": timing_part,
    }

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

    d["ma20_mid"] = d["ma20"]

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

    k["ma20_mid"] = k["ma20"]

    k["mid"] = k["bbi"].where(k["bbi"].notna(), k["ma20_mid"])

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

        return {"cycle": "quarter", "label": "季线", "unit": "个季度", "lookback": 20, "min_len": 8, "min_valid": 7, "support_min": 2, "support_max": 4, "close_above": 1.005, "soft_above": 1.001, "hold_close": 0.992, "breakdown": 0.985, "touch": 1.018, "score_cap": 10.0, "max_actionable_above": 1.20}

    if c == "year":

        return {"cycle": "year", "label": "年线", "unit": "年", "lookback": 8, "min_len": 6, "min_valid": 5, "support_min": 1, "support_max": 2, "close_above": 1.005, "soft_above": 1.001, "hold_close": 0.990, "breakdown": 0.982, "touch": 1.020, "score_cap": 10.0, "max_actionable_above": 1.35}

    return {"cycle": "month", "label": "月线", "unit": "个月", "lookback": 36, "min_len": 24, "min_valid": 18, "support_min": 3, "support_max": 5, "close_above": 1.005, "soft_above": 1.002, "hold_close": 0.992, "breakdown": 0.985, "touch": 1.018, "score_cap": 10.0, "max_actionable_above": 1.12}

def evaluate_cycle_mid_repair(k: pd.DataFrame, cycle: str = "month") -> Dict[str, Any]:

    params = cycle_mid_repair_params(cycle)

    label = params["label"]

    unit = ss(params.get("unit")) or "根"

    empty = {

        "score": 0.0, "type": "无", "detail": f"{label}中轨位置样本不足", "anchor_price": 0.0,

        "repair_stage": "NO_SAMPLE", "cycle": params["cycle"], "label": label, "sample_count": 0,

        "available": False, "support_periods": 0, "breakdown_date": "", "current_mid": 0.0,

        "current_close": 0.0, "close_mid_ratio": 0.0, "repair_volume_ratio": 0.0,

        "overextended": False, "overextension_detail": "",

    }

    if k is None or k.empty or len(k) < int(params["min_len"]):

        out = dict(empty)

        out["sample_count"] = 0 if k is None or k.empty else int(len(k))

        out["detail"] = f"{label}中轨位置样本不足：仅{out['sample_count']}根，至少需要{int(params['min_len'])}根"

        return out

    x = k.copy().reset_index(drop=True)

    if "mid" not in x.columns:

        bbi = x["bbi"] if "bbi" in x.columns else pd.Series(np.nan, index=x.index)

        mid20 = x["ma20_mid"] if "ma20_mid" in x.columns else (x["ma20"] if "ma20" in x.columns else pd.Series(np.nan, index=x.index))

        x["mid"] = bbi.where(pd.notna(bbi), mid20)

    for col in ["open", "high", "low", "close", "volume", "mid", "body_pct", "body_ratio", "close_pos", "vol_ma6", "vol_ma12"]:

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

    recent = x.tail(lookback).copy().reset_index(drop=True)

    valid = recent[(recent["mid"] > 0) & recent["close"].notna()].copy()

    if len(valid) < int(params["min_valid"]):

        out = dict(empty)

        out.update({"detail": f"可用{label}中轨位置样本不足", "anchor_price": rd(cur_mid, 3), "current_mid": rd(cur_mid, 3), "current_close": rd(cur_close, 3), "close_mid_ratio": rd(close_mid_ratio, 4), "sample_count": int(len(x))})

        return out

    max_actionable_above = float(params.get("max_actionable_above", 1.25))

    if close_mid_ratio > max_actionable_above:

        detail = f"当前收盘高于{label}中轨{close_mid_ratio - 1.0:.1%}，已明显远离中轨；不按{label}中轨修复买点加分"

        return {

            "score": 0.0, "type": f"{label}中轨远离无效", "detail": detail,

            "anchor_price": rd(cur_mid, 3), "repair_stage": "MID_OVEREXTENDED", "cycle": params["cycle"],

            "support_periods": 0, "breakdown_date": "", "current_mid": rd(cur_mid, 3),

            "current_close": rd(cur_close, 3), "close_mid_ratio": rd(close_mid_ratio, 4),

            "repair_volume_ratio": 0.0, "sample_count": int(len(x)), "available": True,

            "overextended": True, "overextension_detail": detail,

        }

    score = 0.0

    reasons: List[str] = []

    stage = "MID_POSITION_OBSERVE"

    valid["above_mid"] = valid["close"] >= valid["mid"] * float(params["soft_above"])

    valid["hard_above_mid"] = valid["close"] >= valid["mid"] * float(params["close_above"])

    valid["below_mid"] = valid["close"] < valid["mid"] * float(params["breakdown"])

    valid["touch_mid"] = valid["low"] <= valid["mid"] * float(params["touch"])

    support_periods = 0

    tail_max = min(int(params["support_max"]), len(valid))

    for n in range(1, tail_max + 1):

        seg = valid.tail(n)

        if bool((seg["close"] >= seg["mid"] * float(params["hold_close"])).all()):

            support_periods = n

        else:

            break

    if cur_close >= cur_mid * float(params["close_above"]):

        score += 2.4

        stage = "CURRENT_ABOVE_MID"

        reasons.append(f"当前站上{label}中轨")

    elif cur_close >= cur_mid * float(params["soft_above"]):

        score += 1.0

        stage = "CURRENT_NEAR_MID"

        reasons.append(f"当前贴近{label}中轨")

    elif sf(cur.high) >= cur_mid and cur_close >= cur_mid * 0.985:

        score += 0.6

        reasons.append(f"当前触及{label}中轨但收盘未强站稳")

    if support_periods >= int(params["support_min"]):

        add = min(3.0, 1.2 + 0.45 * support_periods)

        score += add

        stage = "MID_SUPPORT_CONFIRMED" if score >= 3.0 else stage

        reasons.append(f"最近{support_periods}{unit}收盘守住中轨")

    prior = valid.iloc[:-1].copy()

    breakdown_date = ""

    if not prior.empty:

        bd = prior[prior["below_mid"]]

        if not bd.empty:

            breakdown_date = ss(bd.iloc[-1].get("date", ""))[:10]

            bars_since = len(valid) - 1 - int(bd.index[-1])

            if cur_close >= cur_mid * float(params["close_above"]) and bars_since <= max(6, int(params["lookback"]) // 2):

                score += 2.4

                stage = "MID_REPAIR_CONFIRMED"

                reasons.append(f"曾跌破{label}中轨后当前重新站回")

            elif cur_close >= cur_mid * float(params["soft_above"]):

                score += 0.8

                reasons.append(f"曾跌破{label}中轨后当前贴近修复")

    touch_count = int(valid.tail(min(len(valid), int(params["support_max"]) + 2))["touch_mid"].sum())

    if touch_count >= 1 and support_periods >= 1:

        score += min(1.0, touch_count * 0.35)

        reasons.append(f"近期影线回踩中轨{touch_count}次")

    if close_mid_ratio >= 1.025:

        score += 0.8

        reasons.append(f"当前收盘高于中轨{close_mid_ratio - 1.0:.1%}")

    elif close_mid_ratio >= 1.010:

        score += 0.4

        reasons.append(f"当前收盘高于中轨{close_mid_ratio - 1.0:.1%}")

    vol_ref = sf(x.iloc[-7:-1]["volume"].mean()) if len(x) >= 8 else sf(x.iloc[:-1]["volume"].mean())

    repair_volume_ratio = sf(cur.volume) / max(vol_ref, 1e-9) if vol_ref > 0 else 0.0

    if cur_close >= cur_mid * float(params["soft_above"]):

        if 0.75 <= repair_volume_ratio <= 2.50:

            score += 0.5

            reasons.append(f"当前量能配合{repair_volume_ratio:.2f}倍")

        elif repair_volume_ratio > 3.50:

            score -= 0.4

            reasons.append(f"当前量能过猛{repair_volume_ratio:.2f}倍")

    score = clamp(score, 0, float(params["score_cap"]))

    if score >= 7.5:

        typ = f"{label}中轨位置修复"

    elif score >= 4.5:

        typ = f"{label}中轨位置观察"

    elif score > 0:

        typ = f"{label}中轨弱修复"

    else:

        typ = "无"

    return {

        "score": rd(score, 2), "type": typ, "detail": "；".join(reasons) or f"无{label}中轨位置证据",

        "anchor_price": rd(cur_mid, 3), "repair_stage": stage, "cycle": params["cycle"],

        "support_periods": int(support_periods), "breakdown_date": breakdown_date,

        "current_mid": rd(cur_mid, 3), "current_close": rd(cur_close, 3), "close_mid_ratio": rd(close_mid_ratio, 4),

        "repair_volume_ratio": rd(repair_volume_ratio, 3), "sample_count": int(len(x)), "available": True,

        "overextended": False, "overextension_detail": "",

    }

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

    repair_count = sum(1 for k in available_keys if sf(repairs[k].get("score")) >= 6.5)

    if repair_count >= 2:

        weighted += 0.8

    score = clamp(weighted, 0, 15)

    if available_keys:

        best_key, best = max(((k, repairs[k]) for k in available_keys), key=lambda kv: sf(kv[1].get("score")))

    else:

        best_key, best = max(repairs.items(), key=lambda kv: sf(kv[1].get("sample_count")))

    details = []

    unavailable = []

    distance_warnings = []

    for key, r in [("month", month), ("quarter", quarter), ("year", year)]:

        if bool(r.get("overextended")) and ss(r.get("overextension_detail")):

            distance_warnings.append(ss(r.get("overextension_detail")))

        if sf(r.get("score")) > 0 and ss(r.get("detail")):

            details.append(f"{ss(r.get('type'))}:{ss(r.get('detail'))}")

        elif not bool(r.get("available")):

            unavailable.append(f"{ss(r.get('label') or key)}不可用:{ss(r.get('detail'))}")

    if len(available_keys) >= 2 and score >= 9:

        typ = "多周期大周期位置修复"

    elif score >= 7:

        typ = "高级别大周期位置修复"

    elif score >= 4:

        typ = "高级别位置观察"

    elif score > 0:

        typ = "高级别弱修复"

    else:

        typ = "无"

    return {

        "score": rd(score, 2), "type": typ, "detail": "；".join(details[:4]) or "无高级别位置修复证据",

        "unavailable_detail": "；".join(unavailable[:3]), "available_cycles": ",".join(available_keys),

        "distance_warning_detail": "；".join(distance_warnings[:3]),

        "available_cycle_count": int(len(available_keys)), "anchor_price": best.get("anchor_price", 0),

        "best_cycle": best_key, "repair_stage": best.get("repair_stage", ""),

        "month": month, "quarter": quarter, "year": year,

        "monthly_mid_repair_score": month.get("score", 0), "monthly_mid_repair_type": month.get("type", ""), "monthly_mid_repair_stage": month.get("repair_stage", ""), "monthly_mid_sample_count": month.get("sample_count", 0),

        "quarter_mid_repair_score": quarter.get("score", 0), "quarter_mid_repair_type": quarter.get("type", ""), "quarter_mid_repair_stage": quarter.get("repair_stage", ""), "quarter_mid_sample_count": quarter.get("sample_count", 0),

        "year_mid_repair_score": year.get("score", 0), "year_mid_repair_type": year.get("type", ""), "year_mid_repair_stage": year.get("repair_stage", ""), "year_mid_sample_count": year.get("sample_count", 0),

    }

def evaluate_major_cycle_pricing(d: pd.DataFrame, last_close: float) -> Dict[str, Any]:

    if d.empty or last_close <= 0:

        return {"score": 0.0, "type": "无", "detail": "高级别样本不足", "anchor_price": 0.0}

    mid_repair = evaluate_multi_cycle_mid_repair(d)

    score = sf(mid_repair.get("score"))

    reasons: List[str] = []

    warnings: List[str] = []

    if ss(mid_repair.get("detail")) and ss(mid_repair.get("type")) != "无":

        reasons.append(ss(mid_repair.get("detail")))

    if ss(mid_repair.get("distance_warning_detail")):

        warnings.append(ss(mid_repair.get("distance_warning_detail")))

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

                    mid_dist = pct_change(last_close, body_mid)

                    if mid_dist <= 15.0:

                        score += 2.5

                        reasons.append(f"月线最大量阳K中位修复{body_mid:.2f}")

                        anchor = anchor or body_mid

                    else:

                        warnings.append(f"当前价高于月线最大量阳K中位{mid_dist:.1f}%，已远离该修复位，不按买点加分")

                if body_top > 0 and last_close >= body_top * 1.003:

                    top_dist = pct_change(last_close, body_top)

                    if top_dist <= 12.0:

                        score += 1.5

                        reasons.append(f"月线最大量阳K实体顶修复{body_top:.2f}")

                        anchor = body_top

                    else:

                        warnings.append(f"当前价高于月线最大量阳K实体顶{top_dist:.1f}%，已远离该修复位，不按买点加分")

    score = clamp(score, 0, 16)

    if score >= 11:

        typ = "高级别大周期修复"

    elif score >= 7:

        typ = "高级别修复观察"

    elif score > 0:

        typ = "高级别弱修复"

    else:

        typ = "无"

    return {

        "score": rd(score, 2), "type": typ, "detail": "；".join(reasons) or "无高级别修复证据",

        "anchor_price": rd(anchor, 3), "major_mid_best_cycle": mid_repair.get("best_cycle", ""),

        "major_mid_repair_stage": mid_repair.get("repair_stage", ""),

        "monthly_mid_repair_score": mid_repair.get("monthly_mid_repair_score", 0), "monthly_mid_repair_type": mid_repair.get("monthly_mid_repair_type", ""), "monthly_mid_repair_stage": mid_repair.get("monthly_mid_repair_stage", ""),

        "quarter_mid_repair_score": mid_repair.get("quarter_mid_repair_score", 0), "quarter_mid_repair_type": mid_repair.get("quarter_mid_repair_type", ""), "quarter_mid_repair_stage": mid_repair.get("quarter_mid_repair_stage", ""),

        "year_mid_repair_score": mid_repair.get("year_mid_repair_score", 0), "year_mid_repair_type": mid_repair.get("year_mid_repair_type", ""), "year_mid_repair_stage": mid_repair.get("year_mid_repair_stage", ""),

        "major_mid_available_cycles": mid_repair.get("available_cycles", ""), "major_mid_available_cycle_count": mid_repair.get("available_cycle_count", 0),

        "monthly_mid_sample_count": mid_repair.get("monthly_mid_sample_count", 0), "quarter_mid_sample_count": mid_repair.get("quarter_mid_sample_count", 0), "year_mid_sample_count": mid_repair.get("year_mid_sample_count", 0),

        "major_mid_unavailable_detail": mid_repair.get("unavailable_detail", ""),

        "major_cycle_distance_warning": "；".join(warnings[:4]),

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

    for nm in ["ma5", "ma10", "bbi", "ma20_mid"]:

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

    if bidx + 1 < len(d) and vol_ratio >= 1.5:

        n1 = d.iloc[bidx + 1]

        diff = abs(sf(n1.volume) / max(sf(b.volume), 1e-9) - 1.0)

        n1_bad = sf(n1.close) < min(sf(b.open), sf(b.close)) * 0.985 and sf(n1.close) < sf(n1.open)

        if diff <= 0.08 and not n1_bad:

            score += 4.0

            reasons.append(f"次日平量承接差{diff:.1%}")

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

        return {"score": 0.0, "ok": False, "detail": "交易定价样本不足", "defense_price": 0.0, "target_price": 0.0, "rr": 0.0, "pricing_mode": "数据不足", "pressure_audit_detail": "交易定价样本不足"}

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

        defense_price, defense_type, structure_key = max(candidates, key=lambda x: x[0])

    else:

        defense_price, defense_type, structure_key = line * 0.985, "核心线缓冲", line

    risk_pct = pct_change(last_close, defense_price) if defense_price > 0 else 0.0

    # 当前交易定价必须使用“截至当前日前”的全部已知历史；次新股单独走上市后筹码分支，不能套老股全历史压力。
    recent_ipo_pricing_context = evaluate_recent_ipo_context(d)
    if bool(recent_ipo_pricing_context.get("is_recent_ipo")):
        pressure = build_recent_ipo_pressure_profile(d, bidx, last_close, end_idx=max(1, len(d) - 1))
    else:
        pressure = build_overhead_pressure_profile(d, bidx, last_close, end_idx=max(1, len(d) - 1))

    target_price = sf(pressure.get("target_price"))

    target_reliable = bool(pressure.get("target_reliable"))

    pricing_mode = ss(pressure.get("pricing_mode")) or "压力未识别"

    target_type = ss(pressure.get("target_type")) or pricing_mode

    pressure_horizon = ss(pressure.get("pressure_horizon"))

    space_pct = pct_change(target_price, last_close) if target_price > 0 else 0.0

    rr = space_pct / risk_pct if risk_pct > 0 and target_price > 0 and target_reliable else 0.0

    distance_line_pct = pct_change(last_close, line)

    score = 0.0

    reasons: List[str] = []

    if risk_pct <= 6.0:

        score += 7.0; reasons.append(f"防守距离{risk_pct:.1f}%")

    elif risk_pct <= 10.5:

        score += 4.0; reasons.append(f"防守距离{risk_pct:.1f}%")

    else:

        reasons.append(f"防守距离{risk_pct:.1f}%偏远")

    if target_price > 0 and target_reliable:

        if space_pct >= 18.0:

            score += 5.0; reasons.append(f"{target_type}空间{space_pct:.1f}%")

        elif space_pct >= 10.0:

            score += 3.0; reasons.append(f"{target_type}空间{space_pct:.1f}%")

        else:

            reasons.append(f"第一压力近{space_pct:.1f}%")

        if rr >= 2.0:

            score += 6.0; reasons.append(f"历史压力RR={rr:.2f}")

        elif rr >= 1.35:

            score += 3.5; reasons.append(f"历史压力RR={rr:.2f}")

        else:

            reasons.append(f"历史压力RR={rr:.2f}不足")

    elif target_price > 0 and not target_reliable:

        score -= 1.0

        reasons.append(f"上方仅弱压力参考{target_price:.2f}，不按固定RR重仓")

    elif pricing_mode in {"全历史价格发现", "次新上市后价格发现"}:

        if pricing_mode == "次新上市后价格发现":

            if bool(pressure.get("recent_ipo_price_discovery_ok")) and risk_pct <= RECENT_IPO_PRICE_DISCOVERY_MAX_RISK_PCT and distance_line_pct <= RECENT_IPO_PRICE_DISCOVERY_MAX_DISTANCE_PCT:

                score += 4.5

                reasons.append("次新突破上市后筹码区进入价格发现；不设固定目标，用防守位和移动止盈管理")

            else:

                reasons.append("次新价格发现条件不足，需平台成熟、距线合适且防守位清楚")

        elif risk_pct <= PRICE_DISCOVERY_MAX_RISK_PCT and distance_line_pct <= PRICE_DISCOVERY_MAX_DISTANCE_PCT:

            score += 5.0

            reasons.append("全历史上方无有效压力，价格发现模式；不设固定目标，用防守位和移动止盈管理")

        else:

            reasons.append("全历史价格发现但当前距线/防守偏远，只能等待回踩")

    else:

        reasons.append(f"{target_type}，赔率不按固定目标虚构")

    if 0.0 <= distance_line_pct <= 8.0:

        score += 2.0; reasons.append(f"距核心线{distance_line_pct:.1f}%")

    elif 8.0 < distance_line_pct <= 12.0:

        score += 0.5; reasons.append(f"距核心线{distance_line_pct:.1f}%略远")

    elif distance_line_pct > 18.0:

        score -= 4.0; reasons.append(f"距核心线{distance_line_pct:.1f}%过远")

    historical_ok = bool(target_price > 0 and target_reliable and risk_pct <= 10.5 and rr >= 1.35 and space_pct >= 8.0 and distance_line_pct <= 18.0)

    old_stock_price_discovery_ok = bool(target_price <= 0 and pricing_mode == "全历史价格发现" and risk_pct <= PRICE_DISCOVERY_MAX_RISK_PCT and distance_line_pct <= PRICE_DISCOVERY_MAX_DISTANCE_PCT)

    recent_ipo_price_discovery_ok = bool(target_price <= 0 and pricing_mode == "次新上市后价格发现" and bool(pressure.get("recent_ipo_price_discovery_ok")) and risk_pct <= RECENT_IPO_PRICE_DISCOVERY_MAX_RISK_PCT and distance_line_pct <= RECENT_IPO_PRICE_DISCOVERY_MAX_DISTANCE_PCT)

    price_discovery_ok = old_stock_price_discovery_ok or recent_ipo_price_discovery_ok

    ok = historical_ok or price_discovery_ok

    score = clamp(score, -8, 20)

    trigger_price = max([x for x in [line, support_price] if sf(x) > 0] or [line])

    recent_tail = d.tail(min(20, len(d))).copy()

    recent_high = sf(recent_tail["high"].max()) if not recent_tail.empty and "high" in recent_tail.columns else sf(last.get("high", last_close))

    if last_close >= max(trigger_price, defense_price) * 1.003:

        if target_price > 0 and target_reliable:

            if distance_line_pct > 12.0:

                confirm = f"已站上主评测线{line:.2f}且距线{distance_line_pct:.1f}%；不追涨，后续只看能否守住交易防守位{defense_price:.2f}并向{pressure_horizon}压力{target_price:.2f}推进"

            else:

                confirm = f"已站上主评测线{line:.2f}；后续看能否守住交易防守位{defense_price:.2f}，并向{pressure_horizon}压力{target_price:.2f}推进"

        elif target_price > 0 and not target_reliable:

            confirm = f"已站上主评测线{line:.2f}；上方仅弱压力参考{target_price:.2f}，不按固定RR重仓，只看回踩守住{defense_price:.2f}后能否继续放量拓展"

        elif pricing_mode in {"全历史价格发现", "次新上市后价格发现"}:

            if pricing_mode == "次新上市后价格发现":

                if distance_line_pct > RECENT_IPO_PRICE_DISCOVERY_MAX_DISTANCE_PCT:

                    confirm = f"次新价格发现但距主评测线{distance_line_pct:.1f}%偏远；不追涨，只等待缩量/正常回踩不破交易防守位{defense_price:.2f}"

                else:

                    confirm = f"次新上市后筹码区价格发现；不设固定目标，后续核心是守住交易防守位{defense_price:.2f}，沿MA10/BBI或移动止盈管理"

            elif distance_line_pct > PRICE_DISCOVERY_MAX_DISTANCE_PCT:

                confirm = f"全历史价格发现但距主评测线{distance_line_pct:.1f}%偏远；不追涨，只等待缩量/正常回踩不破交易防守位{defense_price:.2f}"

            else:

                confirm = f"全历史价格发现；不设固定目标，后续核心是守住交易防守位{defense_price:.2f}，沿MA10/BBI或移动止盈管理"

        elif recent_high > last_close * 1.003:

            confirm = f"已站上主评测线{line:.2f}；历史压力未成可靠线，后续看守住{defense_price:.2f}并放量收盘突破近20日高点{recent_high:.2f}"

        else:

            confirm = f"已站上主评测线{line:.2f}；后续核心是缩量/正常回踩不破交易防守位{defense_price:.2f}，不再要求重复确认{trigger_price:.2f}"

    else:

        confirm = f"放量收盘站稳{max(trigger_price, defense_price):.2f}，且回踩不有效跌破交易防守位{defense_price:.2f}"

    giveup = f"收盘跌破交易防守位{defense_price:.2f}，或放量长阴跌回主评测线{line:.2f}下方"

    return {

        "score": rd(score, 2),

        "ok": ok,

        "detail": "；".join(reasons),

        "defense_price": rd(defense_price, 3),

        "defense_type": defense_type,

        "structure_key_price": rd(structure_key, 3),

        "defense_distance_pct": rd(risk_pct, 2),

        "target_price": rd(target_price, 3),

        "target_type": target_type,

        "space_pct": rd(space_pct, 2),

        "rr": rd(rr, 2),

        "distance_line_pct": rd(distance_line_pct, 2),

        "confirm_condition": confirm,

        "giveup_condition": giveup,

        "trigger_price": rd(trigger_price, 3),

        "recent_high_20d": rd(recent_high, 3),

        "pricing_mode": pricing_mode,

        "pressure_horizon": pressure_horizon,

        "target_reliable": bool(target_reliable),

        "pressure_quality": pressure.get("target_quality", "none"),

        "near_pressure_price": pressure.get("near_pressure_price", 0),

        "mid_pressure_price": pressure.get("mid_pressure_price", 0),

        "long_pressure_price": pressure.get("long_pressure_price", 0),

        "full_pressure_price": pressure.get("full_pressure_price", 0),

        "near_pressure_quality": pressure.get("near_pressure_quality", "none"),

        "mid_pressure_quality": pressure.get("mid_pressure_quality", "none"),

        "long_pressure_quality": pressure.get("long_pressure_quality", "none"),

        "full_pressure_quality": pressure.get("full_pressure_quality", "none"),

        "full_history_high": pressure.get("full_history_high", 0),

        "full_history_high_date": pressure.get("full_history_high_date", ""),

        "pressure_audit_detail": pressure.get("pressure_audit_detail", ""),

        "pressure_scan_sample_days": pressure.get("pressure_scan_sample_days", 0),
        "recent_ipo_flag": bool(pressure.get("recent_ipo_flag", False)),
        "listing_age_days": pressure.get("listing_age_days", 0),
        "first_trade_date": pressure.get("first_trade_date", ""),
        "recent_ipo_stage": pressure.get("recent_ipo_stage", ""),
        "recent_ipo_action": pressure.get("recent_ipo_action", ""),
        "recent_ipo_maturity_score": pressure.get("recent_ipo_maturity_score", 0),
        "recent_ipo_platform_valid": bool(pressure.get("recent_ipo_platform_valid", False)),
        "recent_ipo_platform_score": pressure.get("recent_ipo_platform_score", 0),
        "recent_ipo_platform_upper": pressure.get("recent_ipo_platform_upper", 0),
        "recent_ipo_platform_lower": pressure.get("recent_ipo_platform_lower", 0),
        "recent_ipo_platform_window": pressure.get("recent_ipo_platform_window", 0),
        "recent_ipo_platform_amp_pct": pressure.get("recent_ipo_platform_amp_pct", 0),
        "recent_ipo_max_amount_high": pressure.get("recent_ipo_max_amount_high", 0),
        "recent_ipo_max_amount_body_top": pressure.get("recent_ipo_max_amount_body_top", 0),
        "recent_ipo_max_amount_body_bottom": pressure.get("recent_ipo_max_amount_body_bottom", 0),
        "recent_ipo_ipo_day_high": pressure.get("recent_ipo_ipo_day_high", 0),
        "recent_ipo_ipo_day_close": pressure.get("recent_ipo_ipo_day_close", 0),
        "recent_ipo_post_high": pressure.get("recent_ipo_post_high", 0),
        "recent_ipo_post_high_date": pressure.get("recent_ipo_post_high_date", ""),
        "recent_ipo_detail": pressure.get("recent_ipo_detail", ""),
        "recent_ipo_price_discovery_ok": bool(pressure.get("recent_ipo_price_discovery_ok", False)),

    }


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

def deep_status_and_score(code: str, name: str, df: pd.DataFrame, line: float, line_info: Dict[str, Any], br: Dict[str, Any], line_type: str = "", hit_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:

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

    # 底层评估分两类：主事件直接进分；大周期/供应/爆发前夜/股性/时间汇总为独立13分上下文。
    major = evaluate_major_cycle_pricing(d, last_close)
    supply = evaluate_supply_absorption(d, bidx, L)
    eve = evaluate_explosion_eve(d, bidx)
    pullback = evaluate_pullback_acceptance(d, bidx, L)
    fund = evaluate_fund_behavior(d, bidx)
    activity = evaluate_activity(d, code, L, bidx)
    timing = evaluate_time_maturity(d, bidx)
    trade = evaluate_trade_pricing(d, bidx, L, sf(pullback.get("support_price")))
    recent_ipo = evaluate_recent_ipo_context(d)
    risk = evaluate_risk_counterevidence(d, bidx, L, fund, trade)

    data_last_date = ss(last.get("date", ""))

    data_fresh = bool((not TARGET_DASH) or data_last_date == TARGET_DASH)

    line_score = score_core_line_level(line_type, line_info, hit_context)
    k_score = score_breakout_k_quality(d, bidx, L, fund, events)
    accept_score = score_acceptance_15(pullback, d, bidx, L)
    space_score = score_space_odds_12(trade)
    risk_score = score_technical_risk_8(risk, fund, trade, data_fresh, recent_ipo)
    context_score = score_context_13(major, supply, eve, activity, timing)

    # 正式100分结构：核心线25 + 突破K27 + 承接15 + 空间12 + 风险8 + 上下文13 = 100。
    # k_score内部仍保留48分原始细分，进入总分前压缩为27分，避免原始细分把总分顶穿。
    line_component = rd(clamp(sf(line_score.get("score")), 0.0, 25.0), 2)
    breakout_component = _weighted_component_score(k_score.get("score"), 48.0, 27.0)
    accept_component = rd(clamp(sf(accept_score.get("score")), 0.0, 15.0), 2)
    space_component = rd(clamp(sf(space_score.get("score")), 0.0, 12.0), 2)
    risk_component = rd(clamp(sf(risk_score.get("score")), 0.0, 8.0), 2)
    context_component = rd(clamp(sf(context_score.get("score")), 0.0, 13.0), 2)

    raw_total = (
        line_component
        + breakout_component
        + accept_component
        + space_component
        + risk_component
        + context_component
    )

    hard_reject = False
    reject_reasons: List[str] = []

    if not data_fresh:
        hard_reject = True
        reject_reasons.append(f"数据日期未对齐：目标{TARGET_DASH}，实际{data_last_date or '未知'}")

    if bool(risk.get("block")):
        hard_reject = True
        reject_reasons.append(ss(risk.get("detail")) or "硬风险")

    if bool(recent_ipo.get("is_recent_ipo")) and ss(recent_ipo.get("recent_ipo_action")) == "HARD_REJECT":
        hard_reject = True
        reject_reasons.append("次新样本不足")

    deep_score_value = rd(clamp(raw_total, 0.0, 100.0), 2)

    if hard_reject:
        deep_score_value = min(deep_score_value, 49.0)
        deep_grade_value = "D"
        deep_state_value = "剔除｜" + "；".join([x for x in reject_reasons if ss(x)])
        trade_action_value = deep_state_value
        deep_pool_value = "剔除"
    else:
        deep_grade_value = deep_grade(deep_score_value)
        if deep_score_value >= 85:
            deep_state_value = "强推｜核心线突破质量高"
            trade_action_value = "强推候选｜等待分时/次日承接确认"
            deep_pool_value = "正式候选"
        elif deep_score_value >= 78:
            deep_state_value = "正式候选｜核心线突破有效"
            trade_action_value = "正式候选｜按防守位管理"
            deep_pool_value = "正式候选"
        elif deep_score_value >= 70:
            deep_state_value = "观察｜突破成立但质量未满"
            trade_action_value = "观察等待｜看回踩承接或二次放量"
            deep_pool_value = "观察候选"
        else:
            deep_state_value = "不推｜突破质量不足"
            trade_action_value = "不推｜等待更高质量确认"
            deep_pool_value = "不推"

    pos = [
        "核心线级别：" + ss(line_score.get("detail")),
        "突破K质量：" + ss(k_score.get("detail")),
        "突破后接受：" + ss(accept_score.get("detail")),
        "空间赔率：" + ss(space_score.get("detail")),
        "可落地风险：" + ss(risk_score.get("detail")),
        "独立上下文13分：" + ss(context_score.get("detail")),
    ]

    if bool(recent_ipo.get("is_recent_ipo")) and ss(recent_ipo.get("recent_ipo_detail")):
        pos.append("次新股专项：" + ss(recent_ipo.get("recent_ipo_detail")))

    neg: List[str] = []

    if not bool(trade.get("ok")):
        neg.append("交易定价未过闸：" + ss(trade.get("detail")))

    if sf(risk.get("penalty")) > 0:
        neg.append("风险反证：" + ss(risk.get("detail")))

    if bool(fund.get("stall")):
        neg.append("资金行为为放量滞涨")

    if not data_fresh:
        neg.append(f"数据日期未对齐：目标{TARGET_DASH}，实际{data_last_date or '未知'}")

    if bool(recent_ipo.get("is_recent_ipo")) and ss(recent_ipo.get("recent_ipo_action")) in {"HARD_REJECT", "OBSERVE_ONLY"}:
        neg.append("次新股专项约束：" + ss(recent_ipo.get("recent_ipo_stage")) + "；" + ss(recent_ipo.get("recent_ipo_detail")))

    risk_flag_parts: List[str] = []

    if sf(risk.get("penalty")) > 0:
        risk_flag_parts.append(ss(risk.get("detail")))

    if not data_fresh:
        risk_flag_parts.append("数据日期未对齐")

    if bool(recent_ipo.get("is_recent_ipo")) and ss(recent_ipo.get("recent_ipo_action")) in {"HARD_REJECT", "OBSERVE_ONLY"}:
        risk_flag_parts.append("次新专项约束")

    risk_flag_text = "；".join([x for x in risk_flag_parts if ss(x)]) or "无"

    return {

        "deep_score": deep_score_value,

        "deep_grade": deep_grade_value,

        "deep_state": deep_state_value,

        "trade_action": trade_action_value,

        "deep_pool": deep_pool_value,

        "deep_positive_reasons": "；".join([x for x in pos if ss(x)]),

        "deep_negative_reasons": "；".join(neg[:8]) or "暂无明显扣分项",

        "risk_flags": risk_flag_text,

        "current_close": rd(last_close, 3),

        "data_last_date": ss(last.get("date", "")),

        "target_trade_date": TARGET_DASH,

        "data_is_target_fresh": bool((not TARGET_DASH) or ss(last.get("date", "")) == TARGET_DASH),

        "data_freshness_detail": f"数据截至{ss(last.get('date', '')) or '未知'}，目标交易日{TARGET_DASH or '未指定'}",

        "score_core_line_level": line_component,
        "score_core_line_level_raw": line_score.get("score", 0),
        "core_line_level_type": line_score.get("line_level_type", ""),
        "core_line_level_detail": line_score.get("detail", ""),
        "dual_line_distance_pct": line_score.get("dual_line_distance_pct", 0),
        "score_breakout_k_quality": breakout_component,
        "score_breakout_k_quality_raw_48": k_score.get("score", 0),
        "breakout_k_quality_detail": k_score.get("detail", ""),
        "breakout_entity_score": k_score.get("entity_score", 0),
        "breakout_direction_score": k_score.get("direction_score", 0),
        "breakout_body_score": k_score.get("body_score", 0),
        "breakout_close_control_score": k_score.get("close_control_score", 0),
        "breakout_volume_score": k_score.get("volume_score", 0),
        "breakout_pattern_score": k_score.get("pattern_score", 0),
        "breakout_high_quality_bonus": k_score.get("high_quality_breakout_bonus", 0),
        "breakout_is_true_yang": bool(k_score.get("is_true_yang")),
        "breakout_is_fake_yin_true_yang": bool(k_score.get("is_fake_yin_true_yang")),
        "score_acceptance_15": accept_component,
        "score_acceptance_15_raw": accept_score.get("score", 0),
        "acceptance_detail": accept_score.get("detail", ""),
        "score_space_odds_12": space_component,
        "score_space_odds_12_raw": space_score.get("score", 0),
        "space_odds_detail": space_score.get("detail", ""),
        "score_technical_risk_8": risk_component,
        "score_technical_risk_8_raw": risk_score.get("score", 0),
        "technical_risk_detail": risk_score.get("detail", ""),
        "score_context_13": context_component,
        "context_13_detail": context_score.get("detail", ""),
        "context_major_component": context_score.get("major_component", 0),
        "context_supply_component": context_score.get("supply_component", 0),
        "context_explosion_eve_component": context_score.get("explosion_eve_component", 0),
        "context_activity_component": context_score.get("activity_component", 0),
        "context_timing_component": context_score.get("timing_component", 0),

        "distance_line_pct": trade.get("distance_line_pct", 0),

        "defense_price": trade.get("defense_price", 0),

        "defense_type": trade.get("defense_type", ""),

        "defense_distance_pct": trade.get("defense_distance_pct", 0),

        "target_price": trade.get("target_price", 0),

        "target_type": trade.get("target_type", ""),

        "pricing_mode": trade.get("pricing_mode", ""),

        "pressure_horizon": trade.get("pressure_horizon", ""),

        "target_reliable": bool(trade.get("target_reliable")),

        "pressure_quality": trade.get("pressure_quality", ""),

        "near_pressure_price": trade.get("near_pressure_price", 0),

        "mid_pressure_price": trade.get("mid_pressure_price", 0),

        "long_pressure_price": trade.get("long_pressure_price", 0),

        "full_pressure_price": trade.get("full_pressure_price", 0),

        "near_pressure_quality": trade.get("near_pressure_quality", ""),

        "mid_pressure_quality": trade.get("mid_pressure_quality", ""),

        "long_pressure_quality": trade.get("long_pressure_quality", ""),

        "full_pressure_quality": trade.get("full_pressure_quality", ""),

        "full_history_high": trade.get("full_history_high", 0),

        "full_history_high_date": trade.get("full_history_high_date", ""),

        "pressure_audit_detail": trade.get("pressure_audit_detail", ""),

        "pressure_scan_sample_days": trade.get("pressure_scan_sample_days", 0),
        "recent_ipo_flag": bool(recent_ipo.get("recent_ipo_flag", False)),
        "listing_age_days": recent_ipo.get("listing_age_days", 0),
        "first_trade_date": recent_ipo.get("first_trade_date", ""),
        "recent_ipo_stage": recent_ipo.get("recent_ipo_stage", ""),
        "recent_ipo_action": recent_ipo.get("recent_ipo_action", ""),
        "recent_ipo_maturity_score": recent_ipo.get("recent_ipo_maturity_score", 0),
        "recent_ipo_platform_valid": bool(recent_ipo.get("recent_ipo_platform_valid", False)),
        "recent_ipo_platform_score": recent_ipo.get("recent_ipo_platform_score", 0),
        "recent_ipo_platform_upper": recent_ipo.get("recent_ipo_platform_upper", 0),
        "recent_ipo_platform_lower": recent_ipo.get("recent_ipo_platform_lower", 0),
        "recent_ipo_platform_window": recent_ipo.get("recent_ipo_platform_window", 0),
        "recent_ipo_platform_amp_pct": recent_ipo.get("recent_ipo_platform_amp_pct", 0),
        "recent_ipo_max_amount_high": recent_ipo.get("recent_ipo_max_amount_high", 0),
        "recent_ipo_max_amount_body_top": recent_ipo.get("recent_ipo_max_amount_body_top", 0),
        "recent_ipo_max_amount_body_bottom": recent_ipo.get("recent_ipo_max_amount_body_bottom", 0),
        "recent_ipo_ipo_day_high": recent_ipo.get("recent_ipo_ipo_day_high", 0),
        "recent_ipo_ipo_day_close": recent_ipo.get("recent_ipo_ipo_day_close", 0),
        "recent_ipo_post_high": recent_ipo.get("recent_ipo_post_high", 0),
        "recent_ipo_post_high_date": recent_ipo.get("recent_ipo_post_high_date", ""),
        "recent_ipo_detail": recent_ipo.get("recent_ipo_detail", ""),

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

        "score_major_cycle": major.get("score", 0),

        "major_cycle_type": major.get("type", ""),

        "major_cycle_detail": major.get("detail", ""),

        "major_cycle_anchor_price": major.get("anchor_price", 0),

        "major_cycle_distance_warning": major.get("major_cycle_distance_warning", ""),

        "major_mid_best_cycle": major.get("major_mid_best_cycle", ""),

        "major_mid_repair_stage": major.get("major_mid_repair_stage", ""),

        "monthly_mid_repair_score": major.get("monthly_mid_repair_score", 0),

        "monthly_mid_repair_type": major.get("monthly_mid_repair_type", ""),

        "monthly_mid_repair_stage": major.get("monthly_mid_repair_stage", ""),

        "quarter_mid_repair_score": major.get("quarter_mid_repair_score", 0),

        "quarter_mid_repair_type": major.get("quarter_mid_repair_type", ""),

        "quarter_mid_repair_stage": major.get("quarter_mid_repair_stage", ""),

        "year_mid_repair_score": major.get("year_mid_repair_score", 0),

        "year_mid_repair_type": major.get("year_mid_repair_type", ""),

        "year_mid_repair_stage": major.get("year_mid_repair_stage", ""),

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


def build_dual_line_hit_candidate(code: str, name: str, df: pd.DataFrame) -> Dict[str, Any]:

    historical_line = choose_historical_core_resonance_line(df)

    five_hundred_line = choose_five_hundred_day_resonance_trigger_line(df)

    recent_ipo_line = choose_recent_ipo_core_line(df)

    historical_price = sf(historical_line.get("line")) if historical_line.get("line") is not None else 0.0

    five_hundred_price = sf(five_hundred_line.get("line")) if five_hundred_line.get("line") is not None else 0.0

    recent_ipo_price = sf(recent_ipo_line.get("line")) if recent_ipo_line.get("line") is not None else 0.0

    historical_breakout = daily_breakout_quality(df, historical_price)

    five_hundred_breakout = daily_breakout_quality(df, five_hundred_price)

    recent_ipo_breakout = daily_breakout_quality(df, recent_ipo_price)

    historical_hit = bool(historical_breakout.get("hit"))

    five_hundred_hit = bool(five_hundred_breakout.get("hit"))

    recent_ipo_hit = bool(recent_ipo_breakout.get("hit")) and ss(recent_ipo_line.get("line_type")) == "recent_ipo_core_line"

    recent_ipo_context = evaluate_recent_ipo_context(df)

    is_recent_ipo = bool(recent_ipo_context.get("is_recent_ipo"))

    # 次新股不允许套用普通历史/五百日共振线作为主评测线；只能由上市后筹码线触发。
    option_historical_hit = bool(historical_hit and not is_recent_ipo)

    option_five_hundred_hit = bool(five_hundred_hit and not is_recent_ipo)

    option_recent_ipo_hit = bool(recent_ipo_hit and is_recent_ipo)

    if not (option_historical_hit or option_five_hundred_hit or option_recent_ipo_hit):

        return {}

    options: List[Dict[str, Any]] = []

    if option_historical_hit:

        options.append({"line_type": "历史核心共振线", "line_info": historical_line, "breakout": historical_breakout})

    if option_five_hundred_hit:

        options.append({"line_type": "五百日共振触发线", "line_info": five_hundred_line, "breakout": five_hundred_breakout})

    if option_recent_ipo_hit:

        options.append({"line_type": "次新上市后筹码线", "line_info": recent_ipo_line, "breakout": recent_ipo_breakout})

    primary = select_primary_assessment_line(df, options)

    if not primary:

        return {}

    hit_sources: List[str] = []

    if option_historical_hit:

        hit_sources.append("历史核心共振线")

    if option_five_hundred_hit:

        hit_sources.append("五百日共振触发线")

    if option_recent_ipo_hit:

        hit_sources.append("次新上市后筹码线")

    source = " + ".join(hit_sources) if hit_sources else "未识别"

    return {

        "股票代码": code,

        "股票中文名称": stock_display_name(code, name),

        "海选命中来源": source,

        "historical_line": historical_line,

        "five_hundred_line": five_hundred_line,

        "recent_ipo_line": recent_ipo_line,

        "historical_breakout": historical_breakout,

        "five_hundred_breakout": five_hundred_breakout,

        "recent_ipo_breakout": recent_ipo_breakout,

        "historical_hit": historical_hit,

        "five_hundred_hit": five_hundred_hit,

        "recent_ipo_hit": recent_ipo_hit,

        "primary": primary,

        "df": df,

    }

DEEP_CN_ALIAS_FIELDS: Tuple[Tuple[str, str, Any], ...] = (
    ("深度等级", "deep_grade", "D"),
    ("深度得分", "deep_score", 0),
    ("当前状态", "deep_state", ""),
    ("操作建议", "trade_action", ""),
    ("候选池", "deep_pool", ""),
    ("加分原因", "deep_positive_reasons", ""),
    ("扣分原因", "deep_negative_reasons", ""),
    ("风险标签", "risk_flags", ""),
    ("核心线级别分", "score_core_line_level", 0),
    ("突破K质量分", "score_breakout_k_quality", 0),
    ("突破后接受分", "score_acceptance_15", 0),
    ("空间赔率分", "score_space_odds_12", 0),
    ("可落地风险分", "score_technical_risk_8", 0),
    ("独立上下文13分", "score_context_13", 0),
    ("上下文13分说明", "context_13_detail", ""),
    ("核心线级别说明", "core_line_level_detail", ""),
    ("突破K质量说明", "breakout_k_quality_detail", ""),
    ("突破后接受说明", "acceptance_detail", ""),
    ("空间赔率说明", "space_odds_detail", ""),
    ("可落地风险说明", "technical_risk_detail", ""),
    ("当前收盘", "current_close", 0),
    ("是否次新股", "recent_ipo_flag", False),
    ("上市交易日", "listing_age_days", 0),
    ("首日日期", "first_trade_date", ""),
    ("次新阶段", "recent_ipo_stage", ""),
    ("次新动作", "recent_ipo_action", ""),
    ("次新平台有效", "recent_ipo_platform_valid", False),
    ("次新平台上沿", "recent_ipo_platform_upper", 0),
    ("次新平台下沿", "recent_ipo_platform_lower", 0),
    ("次新平台窗口", "recent_ipo_platform_window", 0),
    ("次新最大成交额K高点", "recent_ipo_max_amount_high", 0),
    ("次新上市以来高点", "recent_ipo_post_high", 0),
    ("次新审计", "recent_ipo_detail", ""),
    ("大周期远离警告", "major_cycle_distance_warning", ""),
    ("数据截至", "data_last_date", ""),
    ("目标交易日", "target_trade_date", ""),
    ("距主评测线%", "distance_line_pct", 0),
    ("交易防守位", "defense_price", 0),
    ("防守距离%", "defense_distance_pct", 0),
    ("目标/压力价", "target_price", 0),
    ("定价模式", "pricing_mode", ""),
    ("压力层级", "pressure_horizon", ""),
    ("近端压力价", "near_pressure_price", 0),
    ("中期压力价", "mid_pressure_price", 0),
    ("长期压力价", "long_pressure_price", 0),
    ("全历史压力价", "full_pressure_price", 0),
    ("全历史最高价", "full_history_high", 0),
    ("压力审计", "pressure_audit_detail", ""),
    ("上方空间%", "space_pct", 0),
    ("估算赔率", "rr_estimate", 0),
    ("突破量比", "breakout_volume_ratio", 0),
    ("突破实体在线上比例", "breakout_entity_above_line_ratio", 0),
    ("突破收盘位置", "breakout_close_pos", 0),
    ("突破上影比例", "breakout_upper_shadow_ratio", 0),
    ("突破后天数", "days_since_breakout", 0),
    ("近5日涨幅%", "recent5_pct", 0),
    ("近10日涨幅%", "recent10_pct", 0),
    ("近20日涨幅%", "recent20_pct", 0),
)

def apply_deep_cn_alias_fields(row: Dict[str, Any], deep: Dict[str, Any]) -> None:
    for cn_key, deep_key, default in DEEP_CN_ALIAS_FIELDS:
        row[cn_key] = deep.get(deep_key, default)

def append_deep_audit_fields(row: Dict[str, Any], deep: Dict[str, Any]) -> None:
    for key, val in deep.items():
        if key not in row:
            row[key] = val

def deep_screen_dual_line_hit(candidate: Dict[str, Any]) -> Dict[str, Any]:

    code = ss(candidate.get("股票代码"))

    name = ss(candidate.get("股票中文名称"))

    df = candidate.get("df")

    historical_line = candidate.get("historical_line", {}) or {}

    five_hundred_line = candidate.get("five_hundred_line", {}) or {}

    historical_breakout = candidate.get("historical_breakout", {}) or {}

    five_hundred_breakout = candidate.get("five_hundred_breakout", {}) or {}

    historical_hit = bool(candidate.get("historical_hit"))

    five_hundred_hit = bool(candidate.get("five_hundred_hit"))

    primary = candidate.get("primary", {}) or {}

    primary_type = ss(primary.get("line_type"))

    primary_line_info = primary.get("line_info", {}) or {}

    primary_breakout = primary.get("breakout", {}) or {}

    primary_price = sf(primary.get("line"))

    historical_price = sf(historical_line.get("line")) if historical_line.get("line") is not None else 0.0

    five_hundred_price = sf(five_hundred_line.get("line")) if five_hundred_line.get("line") is not None else 0.0

    hit_context = {
        "historical_hit": historical_hit,
        "five_hundred_hit": five_hundred_hit,
        "recent_ipo_hit": bool(candidate.get("recent_ipo_hit")),
        "historical_price": historical_price,
        "five_hundred_price": five_hundred_price,
        "historical_breakout_date": historical_breakout.get("date", ""),
        "five_hundred_breakout_date": five_hundred_breakout.get("date", ""),
        "historical_line": historical_line,
        "five_hundred_line": five_hundred_line,
    }

    deep = deep_status_and_score(code, name, df, primary_price, primary_line_info, primary_breakout, primary_type, hit_context)


    row = {

        "股票代码": code,

        "股票中文名称": stock_display_name(code, name),

        "海选命中来源": candidate.get("海选命中来源", ""),

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

        "五百日共振触发线价位": rd(five_hundred_price, 3),

        "五百日共振触发线突破日期": five_hundred_breakout.get("date", "") if five_hundred_hit else "",

        "五百日共振触发线是否命中": five_hundred_hit,

        "五百日共振触发线共振次数": five_hundred_line.get("effective_resonance_count", 0),

        "五百日共振触发线带量共振次数": five_hundred_line.get("volume_resonance_count", 0),

        "五百日共振触发线净分": five_hundred_line.get("net_score", 0),
        "次新上市后筹码线价位": rd(sf((candidate.get("recent_ipo_line", {}) or {}).get("line")) if (candidate.get("recent_ipo_line", {}) or {}).get("line") is not None else 0.0, 3),
        "次新上市后筹码线突破日期": (candidate.get("recent_ipo_breakout", {}) or {}).get("date", "") if bool(candidate.get("recent_ipo_hit")) else "",
        "次新上市后筹码线是否命中": bool(candidate.get("recent_ipo_hit")),
        "次新上市后筹码线共振次数": (candidate.get("recent_ipo_line", {}) or {}).get("effective_resonance_count", 0),
        "次新上市后筹码线带量共振次数": (candidate.get("recent_ipo_line", {}) or {}).get("volume_resonance_count", 0),
        "次新上市后筹码线净分": (candidate.get("recent_ipo_line", {}) or {}).get("net_score", 0),

    }

    apply_deep_cn_alias_fields(row, deep)

    row["breakout_quality"] = primary_breakout.get("quality", 0)

    row["breakout_close"] = primary_breakout.get("close", 0)

    append_deep_audit_fields(row, deep)

    return row

# EMPLOYEE3_TARGET_DATE_PREFILTER_V1
LAST_SCREEN_PREFILTER_STAT: Dict[str, Any] = {}

def get_df_last_trade_date_for_prefilter(df: pd.DataFrame) -> str:
    # 读取单只股票缓存里的最后一根K线日期，用于三号员工源头日期预过滤。
    try:
        d = normalize_hist(df)
        if d.empty or "date" not in d.columns:
            return ""
        return ss(d.iloc[-1].get("date", ""))
    except Exception:
        return ""

def _format_prefilter_date_counts(counts: Dict[str, int], limit: int = 8) -> str:
    if not counts:
        return "无"
    items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return "；".join(f"{k}:{v}" for k, v in items)


def screen_all(hist: Dict[str, pd.DataFrame], names: Dict[str, str]) -> List[Dict[str, Any]]:

    items = list(hist.items())

    candidates: List[Dict[str, Any]] = []

    stale_date_counts: Dict[str, int] = {}
    stale_skipped = 0
    target_fresh_checked = 0
    invalid_date_skipped = 0

    start = time.time()

    progress("screen", 0, len(items), start, "start")

    for i, (code, df) in enumerate(items, 1):

        name = names.get(code, "")

        if not name and df is not None and not df.empty and "name" in df.columns:

            vals = [ss(x) for x in df["name"].tolist() if ss(x)]

            name = vals[-1] if vals else ""

        try:

            data_last_date = get_df_last_trade_date_for_prefilter(df)

            if TARGET_DASH:
                if not data_last_date:
                    invalid_date_skipped += 1
                    stale_date_counts["未知"] = stale_date_counts.get("未知", 0) + 1
                    if i == 1 or i % SCREEN_PROGRESS_EVERY == 0 or i == len(items):
                        progress("screen", i, len(items), start, f"hit={len(candidates)} stale_skip={stale_skipped} invalid_date={invalid_date_skipped} current={code}")
                    continue

                if data_last_date != TARGET_DASH:
                    stale_skipped += 1
                    stale_date_counts[data_last_date] = stale_date_counts.get(data_last_date, 0) + 1
                    if i == 1 or i % SCREEN_PROGRESS_EVERY == 0 or i == len(items):
                        progress("screen", i, len(items), start, f"hit={len(candidates)} stale_skip={stale_skipped} current={code}")
                    continue

            target_fresh_checked += 1

            candidate = build_dual_line_hit_candidate(code, stock_display_name(code, name), df)

            if candidate:

                candidate["data_last_date_prefilter"] = data_last_date
                candidate["target_trade_date_prefilter"] = TARGET_DASH

                candidates.append(candidate)

        except Exception as exc:

            print(f"screen failed code={code} err={str(exc)[:120]}", flush=True)

        if i == 1 or i % SCREEN_PROGRESS_EVERY == 0 or i == len(items):

            progress("screen", i, len(items), start, f"hit={len(candidates)} stale_skip={stale_skipped} current={code}")

    globals()["LAST_SCREEN_PREFILTER_STAT"] = {
        "prefilter_total_cache_stocks": len(items),
        "prefilter_target_date": TARGET_DASH,
        "prefilter_target_fresh_checked": target_fresh_checked,
        "prefilter_stale_skipped": stale_skipped,
        "prefilter_invalid_date_skipped": invalid_date_skipped,
        "prefilter_stale_date_counts": dict(sorted(stale_date_counts.items(), key=lambda kv: kv[1], reverse=True)),
        "prefilter_stale_date_counts_text": _format_prefilter_date_counts(stale_date_counts),
        "prefilter_raw_scan_hits_after_date_gate": len(candidates),
    }

    print(
        "三号员工日期预检："
        f"缓存股票{len(items)}只｜"
        f"目标日通过{target_fresh_checked}只｜"
        f"旧日期跳过{stale_skipped}只｜"
        f"日期未知跳过{invalid_date_skipped}只｜"
        f"进入核心线扫描命中{len(candidates)}只｜"
        f"旧日期分布:{_format_prefilter_date_counts(stale_date_counts)}",
        flush=True,
    )

    rows: List[Dict[str, Any]] = []

    deep_start = time.time()

    progress("deep", 0, len(candidates), deep_start, "start")

    for i, candidate in enumerate(candidates, 1):

        code = ss(candidate.get("股票代码"))

        try:

            row = deep_screen_dual_line_hit(candidate)

            if row:

                rows.append(row)

        except Exception as exc:

            print(f"deep failed code={code} err={str(exc)[:120]}", flush=True)

        if i == 1 or i % SCREEN_PROGRESS_EVERY == 0 or i == len(candidates):

            progress("deep", i, len(candidates), deep_start, f"hit={len(rows)} current={code}")

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

    if r.get("data_is_target_fresh") is False:

        return True

    if "数据日期未对齐" in action:

        return True

    if "突破失败" in action or "硬风险" in action:

        return True

    return False

def is_backup_observation_row(r: Dict[str, Any]) -> bool:

    if is_hard_rejected_row(r):

        return False

    grade = ss(r.get("深度等级") or r.get("deep_grade")).upper()

    score = sf(r.get("深度得分") or r.get("deep_score"))

    risk_penalty = sf(r.get("risk_penalty"))

    distance_line = sf(r.get("距主评测线%") or r.get("distance_line_pct"))

    defense_distance = sf(r.get("防守距离%") or r.get("defense_distance_pct"))

    if grade not in DEEP_BACKUP_ALLOWED_GRADES:

        return False

    if score < DEEP_BACKUP_MIN_SCORE:

        return False

    if risk_penalty > DEEP_BACKUP_MAX_RISK_PENALTY:

        return False

    if distance_line > DEEP_BACKUP_MAX_DISTANCE_LINE_PCT:

        return False

    if defense_distance > DEEP_BACKUP_MAX_DEFENSE_DISTANCE_PCT:

        return False

    if bool(r.get("是否次新股") or r.get("recent_ipo_flag")):

        ipo_action = ss(r.get("次新动作") or r.get("recent_ipo_action"))

        platform_valid = bool(r.get("次新平台有效") or r.get("recent_ipo_platform_valid"))

        if ipo_action == "HARD_REJECT":

            return False

        if ipo_action == "OBSERVE_ONLY" and not platform_valid:

            return False

    return True

def featured_sort_key(r: Dict[str, Any]) -> Tuple[int, float, int, float, float]:

    grade = ss(r.get("深度等级") or r.get("deep_grade")).upper()

    score = sf(r.get("深度得分") or r.get("deep_score"))

    trade_ok = 1 if bool(r.get("trade_pricing_ok")) else 0

    rr = sf(r.get("估算赔率") or r.get("rr_estimate"))

    risk_penalty = sf(r.get("risk_penalty"))

    return (grade_rank(grade), score, trade_ok, rr, -risk_penalty)

def select_featured_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:

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

            return selected

    backup = [r for r in usable if is_backup_observation_row(r)]

    for r in backup:

        code = ss(r.get("股票代码"))

        if code and code not in seen:

            r["最终入选性质"] = "非正式观察｜A级不足补位"

            selected.append(r)

            seen.add(code)

        if len(selected) >= limit:

            break

    return selected

def selected_quality_note(selected: List[Dict[str, Any]]) -> str:

    if not selected:

        return "今日无合格输出：海选命中票未达到正式A级/S级或非正式观察底线，宁可空仓/不推送。"

    formal_count = sum(

        1 for r in selected

        if ss(r.get("最终入选性质")) == "A级/S级深度精选"

    )

    if formal_count == len(selected):

        return "今日精选质量：A级/S级满足数量，直接输出深度精选。"

    if formal_count > 0:

        return f"今日精选质量：A级/S级仅{formal_count}只，其余仅为非正式观察补位，不构成买入池。"

    return f"今日精选质量：无A级/S级，仅输出达到观察底线的非正式跟踪票；不构成买入池。"

def short_reason(text: Any, max_len: int = 90) -> str:

    s = ss(text).replace("\n", "；")

    return s if len(s) <= max_len else s[:max_len - 1] + "…"

def build_report(rows: List[Dict[str, Any]], stat: Dict[str, Any]) -> str:

    if not rows:

        return f"三号员工Top5｜{TARGET_DASH or TARGET}\n今日无双线突破海选命中。"

    selected = select_featured_rows(rows)[:5]

    hard_rejected = len([r for r in rows if is_hard_rejected_row(r)])

    formal_selected_count = sum(1 for r in selected if ss(r.get("最终入选性质")) == "A级/S级深度精选")

    report_title = "三号员工Top5深度精选" if formal_selected_count > 0 else "三号员工Top5观察池｜无A级/S级"

    lines = [

        f"{report_title}｜{TARGET_DASH or TARGET}",

        f"海选{len(rows)}只｜硬剔除{hard_rejected}只｜输出{len(selected)}只｜正式A/S {formal_selected_count}只",

        selected_quality_note(selected),

        "Telegram仅保留一页Top5；全量明细见CSV/JSON。",

        "",

    ]

    if selected:

        for idx, r in enumerate(selected, 1):

            code = r.get('股票代码', '')

            name = r.get('股票中文名称', '')

            grade = r.get('深度等级', '')

            score = r.get('深度得分', 0)

            nature = ss(r.get('最终入选性质')) or '深度精选'

            line_price = r.get('主评测线价位', 0)

            breakout_date = r.get('主评测线突破日期', '')

            close_price = r.get('当前收盘', r.get('current_close', 0))

            distance_line = r.get('距主评测线%', r.get('distance_line_pct', 0))

            defense = r.get('交易防守位', r.get('defense_price', 0))

            rr = r.get('估算赔率', r.get('rr_estimate', 0))

            action = short_reason(r.get('操作建议', ''), 38)

            reason = short_reason(r.get('加分原因', ''), 82)

            risk = short_reason(r.get('扣分原因', ''), 72)

            confirm = short_reason(r.get('confirm_condition', ''), 60)

            giveup = short_reason(r.get('giveup_condition', ''), 60)

            lines.extend([

                f"{idx}. {code} {name}｜{grade} {score}分｜{nature}",

                f"   线:{line_price}｜破:{breakout_date}｜收:{close_price}｜距线:{distance_line}%｜防:{defense}｜RR:{rr}",

                f"   操作:{action}",

                f"   亮点:{reason}",

                f"   风险:{risk}",

                f"   确认:{confirm}｜放弃:{giveup}",

            ])

    else:

        lines.append("今日无三号员工合格输出：海选票未达到正式买入池或非正式观察底线。")

    report = "\n".join(lines)

    if len(report) > 3900:

        report = report[:3850].rstrip() + "\n……\n报告过长，Telegram已压缩为一页；完整明细见CSV/JSON。"

    return report

def write_outputs(rows: List[Dict[str, Any]], md: str, stat: Dict[str, Any], self_check: Dict[str, Any]) -> None:

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    OUTPUT_MD.write_text(md, encoding="utf-8")

    pd.DataFrame(rows).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    payload = {

        "generated_at_bj": now_bj().strftime("%Y-%m-%d %H:%M:%S"),

        "target": TARGET,

        "target_dash": TARGET_DASH,

        "config": {

            "core_line_timeframe": CORE_LINE_TIMEFRAME,

            "breakout_lookback_days": BREAKOUT_LOOKBACK_DAYS,

            "five_hundred_day_lookback": FIVE_HUNDRED_DAY_LOOKBACK,

            "core_line_tol": CORE_LINE_TOL,

            "min_core_resonance": MIN_CORE_RESONANCE,

            "deep_min_formal_score": DEEP_MIN_FORMAL_SCORE,

            "deep_defense_buffer_pct": DEEP_DEFENSE_BUFFER_PCT,

            "deep_backup_min_score": DEEP_BACKUP_MIN_SCORE,

            "deep_backup_allowed_grades": ",".join(DEEP_BACKUP_ALLOWED_GRADES),

        },

        "stat": stat,

        "self_check": self_check,

        "rows": rows,

    }

    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    SELF_CHECK_JSON.write_text(json.dumps(self_check, ensure_ascii=False, indent=2), encoding="utf-8")


class SelfCheckError(RuntimeError):

    pass

def _selfcheck_item(name: str, ok: bool, level: str, detail: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:

    item: Dict[str, Any] = {
        "name": name,
        "ok": bool(ok),
        "level": level,
        "detail": detail,
    }

    if extra:
        item.update(extra)

    return item

def _validate_target_date() -> Tuple[bool, str]:

    if not re.fullmatch(r"\d{8}", TARGET or ""):
        return False, f"TARGET无效: {TARGET!r}"

    try:
        datetime.strptime(TARGET, "%Y%m%d")
    except Exception as exc:
        return False, f"TARGET日期不可解析: {TARGET!r}, err={exc}"

    return True, f"TARGET={TARGET} TARGET_DASH={TARGET_DASH}"

def _validate_numeric_config() -> List[Dict[str, Any]]:

    checks: List[Tuple[str, Any, float, float]] = [
        ("BREAKOUT_LOOKBACK_DAYS", BREAKOUT_LOOKBACK_DAYS, 1, 250),
        ("CORE_LINE_TOL", CORE_LINE_TOL, 0.001, 0.08),
        ("CORE_LINE_BAND_TOL", CORE_LINE_BAND_TOL, 0.001, 0.10),
        ("MIN_CACHE_ROWS", MIN_CACHE_ROWS, 30, 5000),
        ("MIN_CORE_RESONANCE", MIN_CORE_RESONANCE, 1, 20),
        ("FIVE_HUNDRED_DAY_LOOKBACK", FIVE_HUNDRED_DAY_LOOKBACK, 80, 3000),
        ("LINE_TOP_CANDIDATE_LIMIT", LINE_TOP_CANDIDATE_LIMIT, 3, 80),
        ("DEEP_FINAL_PICK_LIMIT", DEEP_FINAL_PICK_LIMIT, 1, 20),
        ("DEEP_MIN_FORMAL_SCORE", DEEP_MIN_FORMAL_SCORE, 0, 100),
        ("DEEP_BACKUP_MIN_SCORE", DEEP_BACKUP_MIN_SCORE, 0, 100),
        ("DEEP_DEFENSE_BUFFER_PCT", DEEP_DEFENSE_BUFFER_PCT, 0.0, 0.20),
    ]

    out: List[Dict[str, Any]] = []

    for name, value, low, high in checks:
        v = sf(value, float("nan"))
        ok = not math.isnan(v) and low <= v <= high
        out.append(_selfcheck_item(
            f"config::{name}",
            ok,
            "hard",
            f"{name}={value}，允许范围[{low}, {high}]" if ok else f"{name}={value} 超出允许范围[{low}, {high}]",
            {"value": value, "min": low, "max": high},
        ))

    grade_ok = bool(DEEP_FORMAL_GRADES) and all(ss(x).upper() in {"S", "A", "B", "C", "D"} for x in DEEP_FORMAL_GRADES)
    out.append(_selfcheck_item(
        "config::DEEP_FORMAL_GRADES",
        grade_ok,
        "hard",
        f"DEEP_FORMAL_GRADES={DEEP_FORMAL_GRADES}" if grade_ok else f"DEEP_FORMAL_GRADES非法: {DEEP_FORMAL_GRADES}",
    ))

    backup_ok = all(ss(x).upper() in {"S", "A", "B", "C", "D"} for x in DEEP_BACKUP_ALLOWED_GRADES)
    out.append(_selfcheck_item(
        "config::DEEP_BACKUP_ALLOWED_GRADES",
        backup_ok,
        "hard",
        f"DEEP_BACKUP_ALLOWED_GRADES={DEEP_BACKUP_ALLOWED_GRADES}" if backup_ok else f"DEEP_BACKUP_ALLOWED_GRADES非法: {DEEP_BACKUP_ALLOWED_GRADES}",
    ))

    return out

def _check_output_writable() -> Dict[str, Any]:

    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        test_path = REPORT_DIR / ".employee3_write_test.tmp"
        test_path.write_text("ok", encoding="utf-8")
        if test_path.read_text(encoding="utf-8") != "ok":
            return _selfcheck_item("output::writable", False, "hard", f"报告目录写入后读取异常: {REPORT_DIR}")
        test_path.unlink(missing_ok=True)
        return _selfcheck_item("output::writable", True, "hard", f"报告目录可写: {REPORT_DIR}")
    except Exception as exc:
        return _selfcheck_item("output::writable", False, "hard", f"报告目录不可写: {REPORT_DIR}, err={exc}")

def _check_cache_files() -> List[Dict[str, Any]]:

    items: List[Dict[str, Any]] = []
    existing_dirs = [str(x) for x in CACHE_DIRS if x.exists()]
    require_cache = os.getenv("EMPLOYEE3_SELF_CHECK_REQUIRE_CACHE", "1") == "1"
    dir_ok = bool(existing_dirs) or not require_cache
    if existing_dirs:
        dir_detail = "存在缓存目录: " + " | ".join(existing_dirs)
    elif require_cache:
        dir_detail = "没有任何缓存目录存在"
    else:
        dir_detail = "没有任何缓存目录存在；当前已用 EMPLOYEE3_SELF_CHECK_REQUIRE_CACHE=0 临时降级"
    items.append(_selfcheck_item(
        "cache::dirs_exist",
        dir_ok,
        "hard" if require_cache else "warn",
        dir_detail,
        {"existing_dirs": existing_dirs, "configured_dirs": [str(x) for x in CACHE_DIRS], "require_cache": require_cache},
    ))

    files = iter_cache_files()
    level = "hard" if require_cache else "warn"
    ok = bool(files) or not require_cache
    items.append(_selfcheck_item(
        "cache::files_found",
        ok,
        level,
        f"发现有效代码缓存文件 {len(files)} 个" if files else "未发现有效代码缓存文件；默认视为硬错误，可用 EMPLOYEE3_SELF_CHECK_REQUIRE_CACHE=0 临时降级",
        {"cache_files": len(files), "require_cache": require_cache},
    ))

    sample_files = files[: min(8, len(files))]
    required_cols = {"date", "open", "high", "low", "close"}
    bad_samples: List[str] = []
    short_samples: List[str] = []

    for path in sample_files:
        try:
            df = read_cache_file(path)
            missing = sorted(required_cols - set(df.columns)) if not df.empty else sorted(required_cols)
            if df.empty or missing:
                bad_samples.append(f"{path.name}: empty/missing={missing}")
            elif len(df) < MIN_CACHE_ROWS:
                short_samples.append(f"{path.name}: rows={len(df)}")
        except Exception as exc:
            bad_samples.append(f"{path.name}: err={str(exc)[:80]}")

    sample_ok = not bad_samples and not short_samples
    items.append(_selfcheck_item(
        "cache::sample_schema",
        sample_ok,
        "hard" if bad_samples else "warn",
        "缓存样本字段/行数正常" if sample_ok else "缓存样本存在问题: " + "；".join((bad_samples + short_samples)[:5]),
        {"sample_checked": len(sample_files), "bad_samples": bad_samples, "short_samples": short_samples},
    ))

    return items

def _check_runtime_switches() -> List[Dict[str, Any]]:

    items: List[Dict[str, Any]] = []

    tg_ok = True
    tg_detail = "Telegram未启用"
    if ENABLE_TELEGRAM == "1":
        tg_ok = bool(BOT) and bool(CHAT) and requests is not None
        tg_detail = f"ENABLE_TELEGRAM=1 token={bool(BOT)} chat={bool(CHAT)} requests={requests is not None}"

    items.append(_selfcheck_item("runtime::telegram", tg_ok, "hard", tg_detail))

    if ALLOW_BAOSTOCK_FALLBACK:
        bs_ok = bs is not None
        bs_detail = "BaoStock补拉已启用且包可导入" if bs_ok else "BaoStock补拉已启用但 baostock 包不可导入"
        items.append(_selfcheck_item("runtime::baostock_refresh", bs_ok, "hard", bs_detail))
    else:
        items.append(_selfcheck_item("runtime::baostock_refresh", True, "info", "BaoStock补拉未启用，生产主流程不主动补拉"))

    name_fallback_enabled = os.getenv("EMPLOYEE3_NAME_BAOSTOCK_FALLBACK", "0") == "1"
    if name_fallback_enabled:
        ok = bs is not None
        detail = "名称BaoStock回填已启用" if ok else "名称BaoStock回填已启用但 baostock 包不可导入"
        items.append(_selfcheck_item("runtime::baostock_name_fallback", ok, "hard", detail))
    else:
        items.append(_selfcheck_item("runtime::baostock_name_fallback", True, "info", "名称BaoStock回填默认关闭，避免名称映射阶段隐式联网"))

    return items

def run_startup_self_check() -> Dict[str, Any]:

    started = now_bj()
    checks: List[Dict[str, Any]] = []

    ok, detail = _validate_target_date()
    checks.append(_selfcheck_item("target::date", ok, "hard", detail))
    checks.extend(_validate_numeric_config())
    checks.append(_check_output_writable())
    checks.extend(_check_cache_files())
    checks.extend(_check_runtime_switches())

    hard_errors = [x for x in checks if x.get("level") == "hard" and not bool(x.get("ok"))]
    warnings = [x for x in checks if x.get("level") == "warn" and not bool(x.get("ok"))]

    result = {
        "enabled": True,
        "status": "PASS" if not hard_errors else "FAIL",
        "generated_at_bj": started.strftime("%Y-%m-%d %H:%M:%S"),
        "target": TARGET,
        "target_dash": TARGET_DASH,
        "boot": BOOT,
        "hard_error_count": len(hard_errors),
        "warning_count": len(warnings),
        "hard_errors": hard_errors,
        "warnings": warnings,
        "checks": checks,
        "policy": {
            "fail_fast": True,
            "require_cache": os.getenv("EMPLOYEE3_SELF_CHECK_REQUIRE_CACHE", "1") == "1",
            "allow_baostock_refresh": ALLOW_BAOSTOCK_FALLBACK,
            "name_baostock_fallback": os.getenv("EMPLOYEE3_NAME_BAOSTOCK_FALLBACK", "0") == "1",
        },
    }

    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        SELF_CHECK_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        result["status"] = "FAIL"
        result.setdefault("hard_errors", []).append(_selfcheck_item("selfcheck::write_json", False, "hard", f"自检JSON写入失败: {exc}"))
        result["hard_error_count"] = len(result.get("hard_errors", []))

    print(f"self_check status={result['status']} hard_errors={result['hard_error_count']} warnings={result['warning_count']}", flush=True)
    for item in hard_errors[:10]:
        print(f"SELF_CHECK_ERROR {item.get('name')}: {item.get('detail')}", flush=True)
    for item in warnings[:10]:
        print(f"SELF_CHECK_WARN {item.get('name')}: {item.get('detail')}", flush=True)

    if result["status"] != "PASS":
        raise SelfCheckError("启动自检失败，已停止筛选；详见 employee3_reports/employee3_self_check.json")

    return result

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

def main() -> None:

    print(BOOT, flush=True)

    print(f"file={Path(__file__).resolve()}", flush=True)

    print(f"target={TARGET} target_dash={TARGET_DASH}", flush=True)

    print(f"progress_color_enabled={progress_color_enabled()} 条形进度=True", flush=True)

    print("cache_dirs=" + " | ".join(str(x) for x in CACHE_DIRS), flush=True)

    self_check = run_startup_self_check()

    hist, names, stat = load_cache()

    if hist and ALLOW_BAOSTOCK_FALLBACK:

        stat["recent_refresh"] = refresh_recent_cache(hist)

    elif not hist:

        print("公共缓存为空：输出空报告。", flush=True)

    rows = screen_all(hist, names) if hist else []

    if isinstance(stat, dict):
        stat.update(globals().get("LAST_SCREEN_PREFILTER_STAT", {}))

    md = build_report(rows, stat)

    write_outputs(rows, md, stat, self_check)

    send_report(md)

    print(f"Employee3 done. Report: {OUTPUT_MD}", flush=True)

    print(f"CSV: {OUTPUT_CSV}", flush=True)

    print(f"JSON: {OUTPUT_JSON}", flush=True)

if __name__ == "__main__":

    main()

