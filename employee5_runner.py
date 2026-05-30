# -*- coding: utf-8 -*-
from __future__ import annotations

"""五号员工：涨停核心线归因引擎。V85

只做一件事：
扫描最近一个可用交易日的涨停样本，并按原 18.11/V73 60日核心线逻辑输出核心线。

硬约束：
- 核心线识别、评分、锚点校准逻辑不重写、不降级；
- 只使用前复权缓存；
- 报告只输出涨停样本核心线。
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


BOOT = "EMPLOYEE5_PUBLIC_BOOT_20260530_V90_FULL_RESONANCE_QUANT"
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "employee5_reports"

def _now_bj() -> datetime:
    return datetime.utcnow() + timedelta(hours=8)


def _prev_workday(d):
    d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _auto_target_yyyymmdd() -> str:
    """对齐一号员工日期门控：周末回退；交易日20:35前回退到前一工作日。"""
    now = _now_bj()
    today = now.date()
    if today.weekday() >= 5:
        d = today
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d.strftime("%Y%m%d")
    if now.hour > 20 or (now.hour == 20 and now.minute >= 35):
        return today.strftime("%Y%m%d")
    return _prev_workday(today).strftime("%Y%m%d")


TARGET_RAW = (
    os.getenv("EMPLOYEE5_TARGET_DATE")
    or os.getenv("LAST_TRADE_DAY_OVERRIDE")
    or os.getenv("REQUIRED_CACHE_DATE")
    or os.getenv("SELECTION_TRADE_DATE")
    or os.getenv("DATA_GATE_TARGET_DATE")
    or os.getenv("TARGET_TRADE_DATE")
    or _auto_target_yyyymmdd()
)
TARGET_MANUAL = bool(
    os.getenv("EMPLOYEE5_TARGET_DATE")
    or os.getenv("LAST_TRADE_DAY_OVERRIDE")
    or os.getenv("REQUIRED_CACHE_DATE")
    or os.getenv("SELECTION_TRADE_DATE")
    or os.getenv("DATA_GATE_TARGET_DATE")
    or os.getenv("TARGET_TRADE_DATE")
)
TARGET = re.sub(r"\D", "", str(TARGET_RAW))[:8] or _auto_target_yyyymmdd()
TARGET_DASH = f"{TARGET[:4]}-{TARGET[4:6]}-{TARGET[6:8]}"

# 涨停识别容差：只筛涨停板；默认0.15用于兼容pctChg四舍五入，不再把大涨股混入。
LIMIT_UP_TOL = float(os.getenv("EMPLOYEE5_LIMIT_UP_TOL", "0.15"))
MIN_ROWS = int(os.getenv("EMPLOYEE5_MIN_CACHE_ROWS", "22"))
SCAN_KEEP_ROWS = int(os.getenv("EMPLOYEE5_SCAN_KEEP_ROWS", "80"))
TOL = float(os.getenv("EMPLOYEE5_CORE_LINE_TOL", "0.005"))
MIN_CORE_HIT_COUNT = int(os.getenv("EMPLOYEE5_MIN_CORE_HIT_COUNT", "3"))
LOW_VOLUME_WINDOWS = {20: 12, 60: 6, 250: 3}
FIRST_CORE_BAND_TOL = float(os.getenv("EMPLOYEE5_FIRST_CORE_BAND_TOL", "0.015"))
LOWER_LINE_NET_ADVANTAGE = float(os.getenv("EMPLOYEE5_LOWER_LINE_NET_ADVANTAGE", "1.25"))

# 默认允许“补最近5个交易日”的增量修复，但绝不默认全历史重建。
ALLOW_BAOSTOCK_FALLBACK = os.getenv("EMPLOYEE5_ALLOW_BAOSTOCK_FALLBACK", "1") != "0"
BAOSTOCK_LIMIT = int(os.getenv("EMPLOYEE5_BAOSTOCK_FALLBACK_LIMIT", "0"))
PROGRESS_EVERY = max(1, int(os.getenv("EMPLOYEE5_PROGRESS_EVERY", "250")))
CORELINE_PROGRESS_EVERY = max(1, int(os.getenv("EMPLOYEE5_CORELINE_PROGRESS_EVERY", "1")))
INCREMENTAL_REFRESH = os.getenv("EMPLOYEE5_INCREMENTAL_REFRESH", "1") != "0"
REFRESH_LOOKBACK_TRADE_DAYS = int(os.getenv("EMPLOYEE5_REFRESH_LOOKBACK_TRADE_DAYS", "5"))
MIN_CACHE_FILES_REQUIRED = int(os.getenv("EMPLOYEE5_MIN_CACHE_FILES_REQUIRED", "3000"))
ALLOW_FULL_REBUILD = os.getenv("EMPLOYEE5_ALLOW_FULL_REBUILD", "0") == "1"

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

# 一号员工公共缓存可能只有扁平CSV，没有五号自己的_qfq_manifest；默认信任公共flat csv，避免缓存明明存在却被判0命中。
TRUST_PUBLIC_FLAT_CSV = os.getenv("EMPLOYEE5_TRUST_PUBLIC_FLAT_CSV", "1") != "0"

# 五号员工核心线只允许使用前复权缓存。
# BaoStock: adjustflag=2 前复权；adjustflag=3 不复权，不能再用于核心线。
QFQ_ADJUST = "qfq"
QFQ_ADJUSTFLAG = "2"
# 不能启动时全删旧缓存：先拒绝使用，单票前复权覆盖成功后再清掉该票旧缓存。
DELETE_NON_QFQ_CACHE = os.getenv("EMPLOYEE5_DELETE_NON_QFQ_CACHE", "0") == "1"
DELETE_LEGACY_AFTER_QFQ_SAVE = os.getenv("EMPLOYEE5_DELETE_LEGACY_AFTER_QFQ_SAVE", "1") != "0"
BAOSTOCK_START_DATE = os.getenv("EMPLOYEE5_BAOSTOCK_START_DATE", "1990-01-01")
QFQ_REBUILD_TIME_BUDGET_MIN = float(os.getenv("EMPLOYEE5_QFQ_REBUILD_TIME_BUDGET_MIN", "160"))
QFQ_REBUILD_SAFETY_STOP_SEC = float(os.getenv("EMPLOYEE5_QFQ_REBUILD_SAFETY_STOP_SEC", "180"))
QFQ_REBUILD_STATE_FILE = REPORT_DIR / "qfq_rebuild_state.json"
QFQ_REBUILD_FORCE = os.getenv("EMPLOYEE5_QFQ_REBUILD_FORCE", "0") == "1"
QFQ_REBUILD_ALWAYS = os.getenv("EMPLOYEE5_QFQ_REBUILD_ALWAYS", "0") == "1"
QFQ_REBUILD_PROGRESS_EVERY = int(os.getenv("EMPLOYEE5_QFQ_REBUILD_PROGRESS_EVERY", "20"))
QFQ_MANIFEST_FILE = MAIN_CACHE_DIR / "_qfq_manifest.json"
# 正式报告默认不触发全历史重建；该变量只在显式允许全量重建时使用。
QFQ_REBUILD_CODES_RAW = os.getenv("EMPLOYEE5_QFQ_REBUILD_CODES", "002552").strip()
FOCUS_CODES_RAW = os.getenv("EMPLOYEE5_FOCUS_CODES", "").strip()


def parse_code_list(raw: str) -> List[str]:
    if not raw:
        return []
    if raw.strip().upper() in {"ALL", "*", "FULL", "MARKET"}:
        return []
    out: List[str] = []
    seen = set()
    for part in re.split(r"[,;\s]+", raw):
        m = re.search(r"(\d{6})", str(part))
        c = m.group(1) if m else ""
        if c.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4")) and c not in seen:
            out.append(c)
            seen.add(c)
    return out


QFQ_REBUILD_CODES = parse_code_list(QFQ_REBUILD_CODES_RAW)
FOCUS_CODES = parse_code_list(FOCUS_CODES_RAW)


# V72：用户软件截图里的60日捏合K校准样本。
# 注意：这是校准锚点用，不参与交易评分。后续可用环境变量 EMPLOYEE5_60D_ANCHOR_SAMPLES_JSON 覆盖。
# JSON格式示例：{"002552":[{"date":"2020-02-19","open":23.32,"high":31.44,"low":17.17,"close":20.49}]}
DEFAULT_60D_ANCHOR_SAMPLES: Dict[str, List[Dict[str, Any]]] = {
    "002552": [
        {"date": "2020-02-19", "open": 23.32, "high": 31.44, "low": 17.17, "close": 20.49, "source": "user_chart_60d"},
        {"date": "2023-08-16", "open": 18.49, "high": 19.15, "low": 16.50, "close": 17.86, "source": "user_chart_60d"},
    ]
}
try:
    USER_60D_ANCHOR_SAMPLES = json.loads(os.getenv("EMPLOYEE5_60D_ANCHOR_SAMPLES_JSON", "") or "{}")
    if not isinstance(USER_60D_ANCHOR_SAMPLES, dict):
        USER_60D_ANCHOR_SAMPLES = {}
except Exception:
    USER_60D_ANCHOR_SAMPLES = {}

# V73：校准误差足够低时，正式核心线计算也使用该锚点，不再只打印校准结果。
APPLY_60D_ANCHOR = os.getenv("EMPLOYEE5_APPLY_60D_ANCHOR", "1") != "0"
ANCHOR_MAX_TOTAL_ERROR = float(os.getenv("EMPLOYEE5_60D_ANCHOR_MAX_TOTAL_ERROR", "1.0"))


def get_60d_anchor_samples(code: str) -> List[Dict[str, Any]]:
    c = code_of(code)
    samples = USER_60D_ANCHOR_SAMPLES.get(c) or DEFAULT_60D_ANCHOR_SAMPLES.get(c) or []
    out = []
    for x in samples:
        if not isinstance(x, dict):
            continue
        d = norm_date(x.get("date") or x.get("end") or x.get("bar_date"))
        if not d:
            continue
        out.append({
            "date": d,
            "open": rd(x.get("open"), 4),
            "high": rd(x.get("high"), 4),
            "low": rd(x.get("low"), 4),
            "close": rd(x.get("close"), 4),
            "source": ss(x.get("source") or "user_chart_60d"),
        })
    return out


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


def fmt_seconds(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{minutes:.1f}m"
    return f"{minutes / 60.0:.1f}h"


def progress_color_enabled() -> bool:
    raw = ss(os.getenv("EMPLOYEE5_PROGRESS_COLOR", "auto")).lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if os.getenv("NO_COLOR"):
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool(os.getenv("GITHUB_ACTIONS")) or bool(os.getenv("CI"))


def _ansi(text: str, color: str = "", bold: bool = False) -> str:
    if not progress_color_enabled():
        return text
    codes = []
    if bold:
        codes.append("1")
    palette = {
        "cyan": "36",
        "blue": "34",
        "green": "32",
        "yellow": "33",
        "magenta": "35",
        "red": "31",
        "gray": "90",
        "white": "37",
    }
    if color in palette:
        codes.append(palette[color])
    if not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


def _stage_style(stage: str) -> Tuple[str, str]:
    s = ss(stage)
    if "cache" in s:
        return "🔎", "cyan"
    if "refresh" in s or "rebuild" in s:
        return "🔄", "yellow"
    if "core" in s:
        return "⚡", "magenta"
    return "📌", "green"


def _progress_bar(pct: float, width: int = 24, color: str = "green") -> str:
    pct = max(0.0, min(100.0, float(pct or 0.0)))
    filled = int(width * pct / 100.0)
    if pct > 0 and filled == 0:
        filled = 1
    done_part = "█" * filled
    todo_part = "░" * max(width - filled, 0)
    return _ansi(done_part, color, bold=True) + _ansi(todo_part, "gray")


def print_progress(stage: str, done: int, total: int, start_time: float, extra: str = "") -> None:
    total = max(int(total or 0), 0)
    done = max(int(done or 0), 0)
    elapsed = max(time.time() - float(start_time or time.time()), 0.001)
    speed = done / elapsed if done > 0 else 0.0
    icon, color = _stage_style(stage)
    label = _ansi(f"{icon} {stage}", color, bold=True)

    if total > 0:
        pct = min(100.0, done / total * 100.0)
        remain = max(total - done, 0)
        eta = remain / speed if speed > 0 else 0.0
        bar = _progress_bar(pct, width=24, color=color)
        msg = (
            f"{label} {bar} "
            f"{_ansi(f'{pct:5.1f}%', color, bold=True)} "
            f"{done}/{total} "
            f"elapsed={fmt_seconds(elapsed)} "
            f"eta={fmt_seconds(eta)} "
            f"speed={speed:.2f}/s"
        )
    else:
        msg = f"{label} {_ansi('no_items', 'gray')} elapsed={fmt_seconds(elapsed)} eta=0.0s speed=0.00/s"
    if extra:
        msg += f" | {extra}"
    print(msg, flush=True)


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



def load_qfq_manifest() -> Dict[str, Any]:
    try:
        if QFQ_MANIFEST_FILE.exists():
            obj = json.loads(QFQ_MANIFEST_FILE.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}
    return {}


def save_qfq_manifest(manifest: Dict[str, Any]) -> None:
    try:
        MAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = QFQ_MANIFEST_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, QFQ_MANIFEST_FILE)
    except Exception as exc:
        print(f"qfq manifest save failed: {exc}", flush=True)


def update_qfq_manifest_for_code(code: str, df: pd.DataFrame, path: Path, source: str = "baostock") -> None:
    c = code_of(code)
    if not c or df is None or df.empty:
        return
    manifest = load_qfq_manifest()
    manifest.setdefault("__meta__", {})
    manifest["__meta__"].update({
        "adjust": QFQ_ADJUST,
        "adjustflag": QFQ_ADJUSTFLAG,
        "format": "flat_csv",
        "updated_at_bj": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "V70: kline_cache/{code}.csv is trusted only when this manifest marks adjust=qfq and adjustflag=2.",
    })
    manifest[c] = {
        "code": c,
        "file": path.name,
        "path": str(path),
        "adjust": QFQ_ADJUST,
        "adjustflag": QFQ_ADJUSTFLAG,
        "source": source,
        "last_date": ss(df.iloc[-1].get("date", "")),
        "rows": int(len(df)),
        "target_date": TARGET,
        "updated_at_bj": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_qfq_manifest(manifest)


def manifest_entry_valid_for_path(code: str, path: Path, manifest: Optional[Dict[str, Any]] = None, min_rows: int = 0) -> bool:
    c = code_of(code)
    if not c:
        return False
    manifest = manifest if isinstance(manifest, dict) else load_qfq_manifest()
    entry = manifest.get(c)
    if not isinstance(entry, dict):
        return False
    if ss(entry.get("adjust")).lower() != QFQ_ADJUST or ss(entry.get("adjustflag")) != QFQ_ADJUSTFLAG:
        return False
    if min_rows and int(entry.get("rows", 0) or 0) < int(min_rows):
        return False
    # 这里不因日期落后拒绝缓存：五号员工要先读一号员工公共缓存，再判断是否只补最近5个交易日。
    # 日期覆盖由 cache_date_profile / refresh_recent_cache 控制，不能在 manifest 阶段把旧缓存挡掉。
    # V70 允许旧 manifest 只写 file，也允许完整 path；但文件名必须是当前 code.csv。
    if path.suffix.lower() == ".csv" and path.name != f"{c}.csv":
        return False
    return True


def is_qfq_cache_obj(obj: Any) -> bool:
    """只有明确写入前复权元数据的缓存才可信。"""
    if not isinstance(obj, dict):
        return False
    adjust = ss(obj.get("adjust") or obj.get("adjust_type") or obj.get("fq") or obj.get("复权")).lower()
    adjustflag = ss(obj.get("adjustflag") or obj.get("adjust_flag") or obj.get("baostock_adjustflag"))
    return adjust in {"qfq", "前复权", "forward", "forward_adjusted"} or adjustflag == QFQ_ADJUSTFLAG


def reject_untrusted_cache_file(p: Path, reason: str, stat: Optional[Dict[str, Any]] = None) -> None:
    """扫描阶段只拒绝不可信缓存，不批量删除，避免缓存被一次性清空。"""
    if stat is not None:
        stat["non_qfq_rejected"] = stat.get("non_qfq_rejected", 0) + 1
        stat.setdefault("non_qfq_reject_reasons", {})[reason] = stat.setdefault("non_qfq_reject_reasons", {}).get(reason, 0) + 1
    if DELETE_NON_QFQ_CACHE:
        # 兼容紧急人工模式；默认关闭。真正生产推荐使用单票覆盖后清理。
        try:
            p.unlink(missing_ok=True)
            if stat is not None:
                stat["non_qfq_deleted"] = stat.get("non_qfq_deleted", 0) + 1
            print(f"delete non-qfq cache: {p} reason={reason}", flush=True)
        except Exception as exc:
            if stat is not None:
                stat["non_qfq_delete_failed"] = stat.get("non_qfq_delete_failed", 0) + 1
            print(f"delete non-qfq cache failed: {p} reason={reason} err={exc}", flush=True)


def read_cache_file(p: Path, stat: Optional[Dict[str, Any]] = None, require_qfq: bool = True) -> pd.DataFrame:
    try:
        suf = p.suffix.lower()
        c = code_of(p)
        if suf == ".csv":
            if require_qfq and not manifest_entry_valid_for_path(c, p, min_rows=MIN_ROWS):
                # 一号员工公共缓存是生产级kline_cache，可能没有五号自己的_qfq_manifest。
                # 不能因为manifest缺失就把5257个CSV全拒绝，否则样本交易日会变成“无”。
                if not TRUST_PUBLIC_FLAT_CSV:
                    reject_untrusted_cache_file(p, "csv_missing_qfq_manifest", stat)
                    return pd.DataFrame()
                df = normalize_hist(pd.read_csv(p))
                if df.empty or len(df) < MIN_ROWS:
                    reject_untrusted_cache_file(p, "csv_flat_cache_bad_or_short", stat)
                    return pd.DataFrame()
                if stat is not None:
                    stat["trusted_public_flat_csv"] = stat.get("trusted_public_flat_csv", 0) + 1
                return df
            return normalize_hist(pd.read_csv(p))

        # V70 只信任一号员工可直接读取的 flat CSV + manifest。历史 JSON qfq 也不参与正式核心线。
        if suf == ".json":
            if p.name == QFQ_MANIFEST_FILE.name:
                return pd.DataFrame()
            if require_qfq:
                reject_untrusted_cache_file(p, "json_not_flat_csv_cache", stat)
                return pd.DataFrame()
            obj = json.loads(p.read_text(encoding="utf-8"))
            return normalize_hist(pd.DataFrame(rows_from_obj(obj)))

        if require_qfq:
            reject_untrusted_cache_file(p, f"{suf}_no_qfq_metadata", stat)
            return pd.DataFrame()

        if suf in [".txt"]:
            return normalize_hist(pd.read_csv(p))
        if suf in [".pkl", ".pickle"]:
            return normalize_hist(pd.read_pickle(p))
    except Exception as exc:
        if stat is not None:
            stat["cache_read_error"] = stat.get("cache_read_error", 0) + 1
        print(f"read cache failed {p}: {str(exc)[:120]}", flush=True)
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
        "qfq_only": True,
        "adjust": QFQ_ADJUST,
        "adjustflag": QFQ_ADJUSTFLAG,
        "delete_non_qfq_cache": DELETE_NON_QFQ_CACHE,
        "delete_legacy_after_qfq_save": DELETE_LEGACY_AFTER_QFQ_SAVE,
        "qfq_rebuild_state_file": str(QFQ_REBUILD_STATE_FILE),
        "qfq_manifest_file": str(QFQ_MANIFEST_FILE),
        "flat_csv_cache": True,
        "trust_public_flat_csv": TRUST_PUBLIC_FLAT_CSV,
        "rebuild_codes": QFQ_REBUILD_CODES_RAW,
    }

    total_files = len(files)
    cache_scan_start = time.time()
    print_progress("cache_scan", 0, total_files, cache_scan_start, extra="start")

    for i, p in enumerate(files, 1):
        c = code_of(p)
        if not valid_code(c):
            continue

        df = read_cache_file(p, stat=stat, require_qfq=True)
        if df.empty:
            stat["cache_bad"] += 1
            continue

        last_cache_date = ss(df.iloc[-1]["date"]).replace("-", "")
        # 一号员工式缓存链路：先把公共缓存读进来，再由日期门控/最近5日增量决定是否补数据。
        # 这里不能因为缓存日期落后就丢弃，否则五号无法基于一号旧缓存做最近5日补齐。
        if len(df) < MIN_ROWS:
            stat["cache_short"] += 1
            continue
        stat.setdefault("cache_last_date_counts", {})[last_cache_date] = stat.setdefault("cache_last_date_counts", {}).get(last_cache_date, 0) + 1

        CACHE_FILE_MAP[c] = p
        hist[c] = df.tail(max(30, SCAN_KEEP_ROWS)).reset_index(drop=True)
        stat["cache_hit"] += 1

        if i == 1 or i % PROGRESS_EVERY == 0 or i == total_files:
            print_progress(
                "cache_scan",
                i,
                total_files,
                cache_scan_start,
                extra=f"hit={stat['cache_hit']} bad={stat['cache_bad']} short={stat['cache_short']} current={c}",
            )

    if total_files == 0:
        print_progress("cache_scan", 0, 0, cache_scan_start, extra="no_cache_files")

    return hist, stat


def load_full_history(code: str, fallback: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    c = code_of(code)
    p = CACHE_FILE_MAP.get(c)

    if p is not None and Path(p).exists():
        df = read_cache_file(Path(p), require_qfq=True)
        if not df.empty:
            return df

    return fallback if fallback is not None else pd.DataFrame()


def cache_path_for_code(code: str) -> Path:
    """V70：统一写入一号员工可直接读取的扁平 CSV。"""
    return MAIN_CACHE_DIR / f"{code_of(code)}.csv"


def save_rebuild_state(state: Dict[str, Any]) -> None:
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = QFQ_REBUILD_STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, QFQ_REBUILD_STATE_FILE)
    except Exception as exc:
        print(f"qfq state save failed: {exc}", flush=True)


def load_rebuild_state() -> Dict[str, Any]:
    try:
        if QFQ_REBUILD_STATE_FILE.exists():
            obj = json.loads(QFQ_REBUILD_STATE_FILE.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}
    return {}


def is_qfq_cache_file(p: Path) -> bool:
    try:
        return p.suffix.lower() == ".csv" and manifest_entry_valid_for_path(code_of(p), p, min_rows=MIN_ROWS)
    except Exception:
        return False


def delete_legacy_cache_for_code(code: str, keep: Optional[Path] = None) -> Dict[str, int]:
    """单票前复权写入成功后，清理该票旧的不可信缓存；不在全局扫描时批量清空。"""
    stat = {"legacy_deleted": 0, "legacy_keep_qfq": 0, "legacy_delete_failed": 0}
    if not DELETE_LEGACY_AFTER_QFQ_SAVE:
        return stat
    c = code_of(code)
    keep_resolved = None
    try:
        keep_resolved = str(keep.resolve()) if keep else ""
    except Exception:
        keep_resolved = str(keep) if keep else ""
    for p in iter_cache_files():
        if code_of(p) != c:
            continue
        try:
            pr = str(p.resolve())
        except Exception:
            pr = str(p)
        if keep_resolved and pr == keep_resolved:
            continue
        # 只保留明确前复权 JSON；其他 CSV/TXT/PKL/无元数据 JSON 都删除。
        if is_qfq_cache_file(p):
            stat["legacy_keep_qfq"] += 1
            continue
        try:
            p.unlink(missing_ok=True)
            stat["legacy_deleted"] += 1
            print(f"delete legacy non-qfq cache after qfq save: {p}", flush=True)
        except Exception as exc:
            stat["legacy_delete_failed"] += 1
            print(f"delete legacy cache failed {p}: {exc}", flush=True)
    return stat


def save_cache_file(code: str, df: pd.DataFrame) -> bool:
    """V70：原子写入前复权扁平 CSV；成功后更新 manifest，再清理该票旧缓存。"""
    c = code_of(code)
    if not c or df is None or df.empty:
        return False
    try:
        df2 = normalize_hist(df)
        if df2.empty or len(df2) < MIN_ROWS:
            print(f"cache save rejected {c}: normalized rows too short", flush=True)
            return False
        last_save_date = ss(df2.iloc[-1].get("date", "")).replace("-", "")
        # 手动复盘日期要严格覆盖；自动运行允许非交易日/盘中保存到最近交易日。
        if TARGET_MANUAL and last_save_date < TARGET:
            print(f"cache save rejected {c}: last_date={df2.iloc[-1].get('date')} target={TARGET_DASH}", flush=True)
            return False

        MAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        out = cache_path_for_code(c)
        tmp = out.with_suffix(".csv.tmp")
        cols = [x for x in ["date", "open", "close", "high", "low", "volume", "amount", "pct_chg"] if x in df2.columns]
        df2[cols].to_csv(tmp, index=False, encoding="utf-8")

        # 写完临时文件先反读校验，避免坏文件覆盖旧文件。
        check_df = normalize_hist(pd.read_csv(tmp))
        last_check_date = ss(check_df.iloc[-1].get("date", "")).replace("-", "") if not check_df.empty else ""
        if check_df.empty or len(check_df) < MIN_ROWS or (TARGET_MANUAL and last_check_date < TARGET):
            tmp.unlink(missing_ok=True)
            print(f"cache save rejected {c}: csv readback check failed", flush=True)
            return False

        os.replace(tmp, out)
        update_qfq_manifest_for_code(c, check_df, out, source="baostock")
        # 再次校验 manifest 身份证；通过后该 CSV 才算可信。
        if not manifest_entry_valid_for_path(c, out, min_rows=MIN_ROWS):
            print(f"cache save rejected {c}: manifest check failed after replace", flush=True)
            return False
        CACHE_FILE_MAP[c] = out
        cleanup = delete_legacy_cache_for_code(c, keep=out)
        if cleanup.get("legacy_deleted"):
            print(f"qfq csv save {c}: replaced legacy={cleanup.get('legacy_deleted')}", flush=True)
        return True
    except Exception as exc:
        print(f"cache save failed {code}: {exc}", flush=True)
        return False

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

    start = BAOSTOCK_START_DATE
    fields = "date,code,open,high,low,close,volume,amount,pctChg"

    try:
        rs = bs.query_history_k_data_plus(
            bs_code(code),
            fields,
            start_date=start,
            end_date=TARGET_DASH,
            frequency="d",
            adjustflag=QFQ_ADJUSTFLAG,
        )

        data = []
        while rs.error_code == "0" and rs.next():
            data.append(rs.get_row_data())

        return normalize_hist(pd.DataFrame(data, columns=fields.split(","))) if data else pd.DataFrame()
    except Exception as exc:
        print(f"baostock hist failed {code}: {exc}", flush=True)
        return pd.DataFrame()


def cache_date_profile(hist: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    dates: List[str] = []
    for df in hist.values():
        if df is None or df.empty:
            continue
        d = ss(df.iloc[-1].get("date", ""))
        if d:
            dates.append(d)
    counts: Dict[str, int] = {}
    for d in dates:
        counts[d] = counts.get(d, 0) + 1
    majority = ""
    if counts:
        majority = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)[0][0]
    return {
        "cache_count": len(hist),
        "cache_date_count": len(dates),
        "cache_majority_date": majority,
        "cache_date_top": sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)[:10],
        "target_date": TARGET_DASH,
        "accepted_for_report": bool(len(hist) >= MIN_CACHE_FILES_REQUIRED and majority and majority >= TARGET_DASH),
    }


def choose_sample_trade_date(hist: Dict[str, pd.DataFrame]) -> str:
    """按一号员工思路使用缓存众数交易日作为样本日，避免部分增量成功时混入不同日期样本。"""
    profile = cache_date_profile(hist)
    return ss(profile.get("cache_majority_date"))


def _refresh_start_date_from_df(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return TARGET_DASH
    try:
        last = datetime.strptime(ss(df.iloc[-1].get("date", "")), "%Y-%m-%d").date()
    except Exception:
        return TARGET_DASH
    # A股节假日无法从纯日期准确判断，这里按自然日回退留足缓冲；最终仍以交易日数据合并去重。
    start = last - timedelta(days=max(10, REFRESH_LOOKBACK_TRADE_DAYS * 3))
    return start.isoformat()


def baostock_fetch_recent_hist(code: str, existing_df: pd.DataFrame) -> pd.DataFrame:
    """只补最近若干交易日，不做1990以来全历史重拉。"""
    if bs is None:
        return pd.DataFrame()
    start = _refresh_start_date_from_df(existing_df)
    fields = "date,code,open,high,low,close,volume,amount,pctChg"
    try:
        rs = bs.query_history_k_data_plus(
            bs_code(code),
            fields,
            start_date=start,
            end_date=TARGET_DASH,
            frequency="d",
            adjustflag=QFQ_ADJUSTFLAG,
        )
        data = []
        while rs.error_code == "0" and rs.next():
            data.append(rs.get_row_data())
        recent = normalize_hist(pd.DataFrame(data, columns=fields.split(","))) if data else pd.DataFrame()
        if recent.empty:
            return pd.DataFrame()
        base = normalize_hist(existing_df)
        merged = pd.concat([base, recent], ignore_index=True) if not base.empty else recent
        merged = normalize_hist(merged).sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
        return merged
    except Exception as exc:
        print(f"baostock recent refresh failed {code}: {exc}", flush=True)
        return pd.DataFrame()


def refresh_recent_cache(existing_hist: Dict[str, pd.DataFrame]) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    """复用一号员工公共缓存；若缓存日期落后，只补最近5个交易日。"""
    stat = {
        "source": "baostock_qfq_recent_incremental_refresh",
        "target_date": TARGET,
        "target_dash": TARGET_DASH,
        "cache_before": len(existing_hist or {}),
        "lookback_trade_days": REFRESH_LOOKBACK_TRADE_DAYS,
        "full_history_rebuild": False,
        "min_cache_files_required": MIN_CACHE_FILES_REQUIRED,
        "baostock_used": True,
        "adjust": QFQ_ADJUST,
        "adjustflag": QFQ_ADJUSTFLAG,
    }
    hist: Dict[str, pd.DataFrame] = dict(existing_hist or {})
    if not hist or len(hist) < MIN_CACHE_FILES_REQUIRED:
        stat["skipped_reason"] = "公共缓存缺失或数量不足；五号员工不从零全量重建"
        return hist, stat
    if bs is None:
        stat["skipped_reason"] = "baostock package missing"
        return hist, stat

    lg = bs.login()
    print(f"baostock login: {getattr(lg, 'error_code', '')} {getattr(lg, 'error_msg', '')}", flush=True)
    codes = list(hist.keys())
    if BAOSTOCK_LIMIT > 0:
        codes = codes[:BAOSTOCK_LIMIT]
    total = len(codes)
    start_time = time.time()
    print_progress("recent_refresh", 0, total, start_time, extra="start")
    budget_sec = max(60.0, QFQ_REBUILD_TIME_BUDGET_MIN * 60.0)
    processed = refreshed = already_ok = failed = short = 0
    try:
        for i, code in enumerate(codes, 1):
            elapsed = time.time() - start_time
            if elapsed >= max(1.0, budget_sec - QFQ_REBUILD_SAFETY_STOP_SEC):
                stat["stop_reason"] = "time_budget_stop"
                break
            df = hist.get(code, pd.DataFrame())
            last_date = ss(df.iloc[-1].get("date", "")) if df is not None and not df.empty else ""
            if last_date.replace("-", "") >= TARGET:
                already_ok += 1
                processed += 1
                if i == 1 or i % PROGRESS_EVERY == 0 or i == total:
                    print_progress(
                        "recent_refresh",
                        i,
                        total,
                        start_time,
                        extra=f"processed={processed} refreshed={refreshed} already_ok={already_ok} failed={failed} short={short} current={code}",
                    )
                continue
            merged = baostock_fetch_recent_hist(code, load_full_history(code, df))
            processed += 1
            if merged.empty:
                failed += 1
                continue
            last_new = ss(merged.iloc[-1].get("date", "")).replace("-", "")
            if last_new < TARGET:
                short += 1
            if save_cache_file(code, merged):
                hist[code] = merged.tail(max(30, SCAN_KEEP_ROWS)).reset_index(drop=True)
                refreshed += 1
            else:
                failed += 1
            if i == 1 or i % PROGRESS_EVERY == 0 or i == total:
                print_progress(
                    "recent_refresh",
                    i,
                    total,
                    start_time,
                    extra=f"processed={processed} refreshed={refreshed} already_ok={already_ok} failed={failed} short={short} current={code}",
                )
    finally:
        try:
            bs.logout()
        except Exception as exc:
            print(f"baostock logout skipped: {exc}", flush=True)
    stat.update({
        "processed_this_run": processed,
        "refreshed_this_run": refreshed,
        "already_ok_this_run": already_ok,
        "failed_this_run": failed,
        "short_this_run": short,
        "cache_after": len(hist),
        "stop_reason": stat.get("stop_reason", "completed"),
        "cache_profile_after": cache_date_profile(hist),
    })
    return hist, stat


def qfq_existing_codes() -> set:
    out = set()
    manifest = load_qfq_manifest()
    for c, entry in manifest.items():
        if c.startswith("__") or not valid_code(c) or not isinstance(entry, dict):
            continue
        p = MAIN_CACHE_DIR / f"{c}.csv"
        if p.exists() and manifest_entry_valid_for_path(c, p, manifest=manifest, min_rows=MIN_ROWS):
            out.add(c)
    return out


def build_baostock_cache(existing_hist: Optional[Dict[str, pd.DataFrame]] = None) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    """限时、可续跑地重建前复权缓存。不会先清空旧缓存。"""
    stat = {
        "source": "baostock_qfq_incremental_rebuild",
        "cache_files": 0,
        "cache_hit": 0,
        "cache_bad": 0,
        "cache_short": 0,
        "target_date": TARGET,
        "baostock_used": True,
        "adjust": QFQ_ADJUST,
        "adjustflag": QFQ_ADJUSTFLAG,
        "baostock_start_date": BAOSTOCK_START_DATE,
        "time_budget_min": QFQ_REBUILD_TIME_BUDGET_MIN,
        "delete_legacy_after_qfq_save": DELETE_LEGACY_AFTER_QFQ_SAVE,
        "resumable_state_file": str(QFQ_REBUILD_STATE_FILE),
    }
    hist: Dict[str, pd.DataFrame] = dict(existing_hist or {})

    if not ALLOW_BAOSTOCK_FALLBACK:
        stat["baostock_disabled"] = True
        return hist, stat

    if QFQ_REBUILD_CODES:
        if bs is not None:
            lg = bs.login()
            print(f"baostock login: {getattr(lg, 'error_code', '')} {getattr(lg, 'error_msg', '')}", flush=True)
        codes = [(c, c) for c in QFQ_REBUILD_CODES]
        stat["rebuild_mode"] = "focus_codes"
    else:
        codes = baostock_all_codes()
        stat["rebuild_mode"] = "all_market"
    codes = codes[:BAOSTOCK_LIMIT] if BAOSTOCK_LIMIT > 0 else codes
    total = len(codes)
    stat["baostock_universe"] = total
    stat["rebuild_codes_raw"] = QFQ_REBUILD_CODES_RAW

    old_state = load_rebuild_state()
    existing_qfq = qfq_existing_codes()
    stat["existing_qfq_codes"] = len(existing_qfq)

    cursor = int(old_state.get("cursor", 0) or 0)
    if (
        QFQ_REBUILD_FORCE
        or old_state.get("target_date") != TARGET
        or old_state.get("adjustflag") != QFQ_ADJUSTFLAG
        or old_state.get("rebuild_codes_raw") != QFQ_REBUILD_CODES_RAW
    ):
        cursor = 0

    start_time = time.time()
    budget_sec = max(60.0, QFQ_REBUILD_TIME_BUDGET_MIN * 60.0)
    processed = 0
    fetched = 0
    skipped_existing = 0
    failed = int(old_state.get("failed", 0) or 0) if cursor > 0 else 0
    short = 0
    last_code = ""
    stop_reason = "completed"

    for i in range(cursor, total):
        elapsed = time.time() - start_time
        if elapsed >= max(1.0, budget_sec - QFQ_REBUILD_SAFETY_STOP_SEC):
            stop_reason = "time_budget_stop"
            break

        code, _ = codes[i]
        last_code = code

        if code in existing_qfq and not QFQ_REBUILD_FORCE:
            skipped_existing += 1
            # 当前运行需要样本时可按需读取已有 qfq，但避免全量加载长期历史进内存。
            processed += 1
            next_cursor = i + 1
        else:
            df = baostock_fetch_hist(code)
            processed += 1
            next_cursor = i + 1

            if df.empty:
                stat["cache_bad"] += 1
                failed += 1
            elif len(df) < MIN_ROWS or df.iloc[-1]["date"].replace("-", "") < TARGET:
                stat["cache_short"] += 1
                short += 1
            elif save_cache_file(code, df):
                hist[code] = df.tail(max(30, SCAN_KEEP_ROWS)).reset_index(drop=True)
                fetched += 1
                stat["cache_hit"] += 1
            else:
                stat["cache_bad"] += 1
                failed += 1

        elapsed = time.time() - start_time
        speed = processed / max(elapsed, 0.001)
        remaining_budget = max(0.0, budget_sec - elapsed)
        progress_pct = (next_cursor / total * 100.0) if total else 100.0
        state = {
            "target_date": TARGET,
            "adjust": QFQ_ADJUST,
            "adjustflag": QFQ_ADJUSTFLAG,
            "format": "flat_csv",
            "manifest_file": str(QFQ_MANIFEST_FILE),
            "rebuild_codes_raw": QFQ_REBUILD_CODES_RAW,
            "total": total,
            "cursor": next_cursor,
            "done": next_cursor,
            "progress_pct": round(progress_pct, 2),
            "processed_this_run": processed,
            "fetched_this_run": fetched,
            "skipped_existing_this_run": skipped_existing,
            "failed": failed,
            "short_this_run": short,
            "elapsed_sec_this_run": round(elapsed, 1),
            "remaining_budget_sec": round(remaining_budget, 1),
            "speed_codes_per_sec": round(speed, 4),
            "last_code": last_code,
            "stop_reason": stop_reason,
            "complete": next_cursor >= total,
            "state_file": str(QFQ_REBUILD_STATE_FILE),
        }
        save_rebuild_state(state)

        if processed == 1 or processed % QFQ_REBUILD_PROGRESS_EVERY == 0 or next_cursor >= total:
            eta_codes = max(0, total - next_cursor)
            eta_sec = eta_codes / max(speed, 0.0001)
            eta_runs = math.ceil(eta_sec / max(budget_sec - QFQ_REBUILD_SAFETY_STOP_SEC, 1.0)) if eta_codes else 0
            print(
                f"qfq rebuild {next_cursor}/{total} {progress_pct:.2f}% "
                f"run_processed={processed} fetched={fetched} skip_qfq={skipped_existing} "
                f"elapsed={elapsed/60:.1f}min remaining={remaining_budget/60:.1f}min "
                f"speed={speed:.3f}/s eta_runs≈{eta_runs} current={code}",
                flush=True,
            )

    try:
        if bs is not None:
            bs.logout()
    except Exception as exc:
        print(f"baostock logout skipped: {exc}", flush=True)

    # 运行结束状态
    final_state = load_rebuild_state()
    if total and int(final_state.get("cursor", 0) or 0) >= total:
        final_state["complete"] = True
        final_state["stop_reason"] = "completed"
        save_rebuild_state(final_state)

    stat.update({
        "processed_this_run": processed,
        "fetched_this_run": fetched,
        "skipped_existing_this_run": skipped_existing,
        "failed_total_est": failed,
        "short_this_run": short,
        "cursor": int(final_state.get("cursor", cursor) or cursor),
        "progress_pct": final_state.get("progress_pct", 0),
        "remaining_budget_sec": final_state.get("remaining_budget_sec", 0),
        "stop_reason": final_state.get("stop_reason", stop_reason),
        "complete": bool(final_state.get("complete", False)),
        "last_code": final_state.get("last_code", last_code),
    })
    stat["cache_files"] = len(list(MAIN_CACHE_DIR.glob("*.csv"))) if MAIN_CACHE_DIR.exists() else 0
    stat["qfq_manifest_file"] = str(QFQ_MANIFEST_FILE)
    stat["flat_csv_cache"] = True
    # 重新扫描一次 qfq 命中，但只用于报告样本池，不强制全量驻留长期历史。
    return hist, stat


def pick_samples(hist: Dict[str, pd.DataFrame], sample_trade_date: str) -> pd.DataFrame:
    """只筛样本交易日涨停股；不同日期的旧缓存不混入报告。"""
    rows: List[Dict[str, Any]] = []
    sample_trade_date = norm_date(sample_trade_date)

    for i, (code, df) in enumerate(hist.items(), 1):
        if df is None or df.empty:
            continue

        last = df.iloc[-1]
        last_date = norm_date(last.date)
        if sample_trade_date and last_date != sample_trade_date:
            continue

        pct = sf(last.pct_chg)
        lp = limit_pct(code)

        if pct >= lp - LIMIT_UP_TOL:
            rows.append({
                "code": code,
                "name": code,
                "date": last_date,
                "close": rd(last.close),
                "pct_chg": rd(pct),
                "limit_pct": rd(lp),
                "sample_type": "涨停",
            })

        if i % 500 == 0:
            print(f"sample scan {i}/{len(hist)} sample_date={sample_trade_date} limit_up={len(rows)}", flush=True)

    A = pd.DataFrame(rows)
    if not A.empty:
        A = A.sort_values(["pct_chg", "close"], ascending=[False, False]).reset_index(drop=True)
    return A


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
    prev_vol = k.volume.shift(1)
    k["prev_vol_ratio"] = (k.volume / prev_vol.replace(0, pd.NA)).fillna(1.0)
    k["vol_rank_pct"] = k.volume.rolling(8, min_periods=1).rank(pct=True).fillna(0.5)

    return k




def aggregate_bars_with_anchor(df: pd.DataFrame, window: int = 60, mode: str = "backward", offset: int = 0) -> pd.DataFrame:
    """生成可校准锚点的60日捏合K。

    mode=backward: 从目标日/数据末端向左每60根分组；offset表示先忽略末端offset根日K再分组，用来模拟软件锚点差异。
    mode=forward: 从数据起点向右每60根分组；offset表示从第offset根日K开始分组。
    该函数只用于锚点校准，不替代正式核心线，除非校准通过后再显式切换。
    """
    if df is None or df.empty or len(df) < max(22, window * 3):
        return pd.DataFrame()
    offset = int(max(0, min(int(offset), window - 1)))
    d = df.copy().reset_index(drop=True)
    if mode == "forward":
        if offset > 0:
            d = d.iloc[offset:].reset_index(drop=True)
        if len(d) < window:
            return pd.DataFrame()
        d["grp"] = [i // window for i in range(len(d))]
    else:
        if offset > 0:
            d = d.iloc[:-offset].reset_index(drop=True)
        if len(d) < window:
            return pd.DataFrame()
        d["grp"] = [(len(d) - 1 - i) // window for i in range(len(d))]

    bars = []
    for _, g in d.groupby("grp"):
        g = g.sort_index()
        if len(g) <= 0:
            continue
        bars.append({
            "start": g.iloc[0].date,
            "end": g.iloc[-1].date,
            "open": sf(g.iloc[0].open),
            "high": sf(g.high.max()),
            "low": sf(g.low.min()),
            "close": sf(g.iloc[-1].close),
            "volume": sf(g.volume.sum()),
            "bar_days": int(len(g)),
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
    prev_vol = k.volume.shift(1)
    k["prev_vol_ratio"] = (k.volume / prev_vol.replace(0, pd.NA)).fillna(1.0)
    k["vol_rank_pct"] = k.volume.rolling(8, min_periods=1).rank(pct=True).fillna(0.5)
    return k


def _date_gap_days(a: str, b: str) -> int:
    try:
        da = datetime.strptime(norm_date(a), "%Y-%m-%d")
        db = datetime.strptime(norm_date(b), "%Y-%m-%d")
        return abs((da - db).days)
    except Exception:
        return 9999


def _ohlc_error(bar: Dict[str, Any], sample: Dict[str, Any]) -> float:
    err = 0.0
    for f in ["open", "high", "low", "close"]:
        sv = sf(sample.get(f))
        bv = sf(bar.get(f))
        base = max(abs(sv), 0.01)
        err += abs(bv - sv) / base
    return err


def classify_line_on_bar(line: float, bar: Dict[str, Any], tol_pct: float = 0.005) -> str:
    body_top = sf(bar.get("body_top"))
    body_bottom = sf(bar.get("body_bottom"))
    high = sf(bar.get("high"))
    low = sf(bar.get("low"))
    ln = sf(line)
    if body_bottom > ln:
        return "ACCEPT"
    if body_bottom < ln < body_top:
        # 实顶距离线≤0.5%视为实顶贴线，不算切实体。
        if abs(body_top - ln) / max(ln, 0.01) <= tol_pct:
            return "BODY_TOP_TOUCH"
        return "CUT"
    if low <= ln <= high:
        return "RESONANCE"
    return "NONE"


def calibrate_60d_anchor_for_code(code: str, df: pd.DataFrame, window: int = 60) -> Dict[str, Any]:
    c = code_of(code)
    samples = get_60d_anchor_samples(c)
    if not samples or df is None or df.empty:
        return {"code": c, "samples": samples, "status": "no_samples_or_no_data"}

    trials = []
    for mode in ["backward", "forward"]:
        for offset in range(window):
            k = aggregate_bars_with_anchor(df, window=window, mode=mode, offset=offset)
            if k.empty:
                continue
            sample_matches = []
            total_error = 0.0
            for smp in samples:
                sdate = norm_date(smp.get("date"))
                # 先按显示日期/结束日期精确匹配；没有则找end最近的一根。
                exact = k[k["end"].astype(str).map(norm_date) == sdate]
                if not exact.empty:
                    row = exact.iloc[-1]
                    date_gap = 0
                    exact_end_match = True
                else:
                    gaps = k["end"].astype(str).map(lambda x: _date_gap_days(x, sdate))
                    idx = int(gaps.idxmin()) if len(gaps) else 0
                    row = k.loc[idx]
                    date_gap = int(gaps.loc[idx]) if len(gaps) else 9999
                    exact_end_match = False
                bar = {x: row.get(x) for x in ["start", "end", "open", "high", "low", "close", "body_top", "body_bottom", "volume", "bar_days", "rel_vol", "vol_rank_pct"]}
                e = _ohlc_error(bar, smp)
                # 日期不匹配要加罚，但不能压过OHLC本身；这样能看出是锚点偏移还是价格源问题。
                total_error += e + min(date_gap, 30) * 0.02
                sample_matches.append({
                    "sample": smp,
                    "matched_bar": {k2: rd(v, 4) if isinstance(v, (int, float)) else v for k2, v in bar.items()},
                    "exact_end_match": exact_end_match,
                    "date_gap_days": date_gap,
                    "ohlc_error": rd(e, 6),
                    "line_17_25_class": classify_line_on_bar(17.25, bar, TOL),
                    "line_17_09_class": classify_line_on_bar(17.09, bar, TOL),
                })
            trials.append({
                "mode": mode,
                "offset": offset,
                "total_error": rd(total_error, 6),
                "matches": sample_matches,
            })
    trials = sorted(trials, key=lambda x: sf(x.get("total_error")))
    return {
        "code": c,
        "window": window,
        "status": "ok",
        "purpose": "校准前复权日线生成的60日捏合K是否和用户软件截图OHLC一致；不参与评分。",
        "samples": samples,
        "best": trials[0] if trials else {},
        "top10": trials[:10],
        "current_v70_like": [x for x in trials if x.get("mode") == "backward" and int(x.get("offset", -1)) == 0][:1],
        "conclusion_hint": "若best不是backward offset=0，说明V70正式核心线使用的60日锚点与软件截图不一致，应先切换聚合锚点再谈切实体/实体接受/评分。",
    }

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


def bars_audit_records(k: pd.DataFrame) -> List[Dict[str, Any]]:
    if k is None or k.empty:
        return []
    cols = [x for x in ["start", "end", "open", "high", "low", "close", "body_top", "body_bottom", "volume", "rel_vol", "vol_rank_pct"] if x in k.columns]
    out = []
    for _, r in k[cols].iterrows():
        item = {}
        for c in cols:
            v = r.get(c)
            item[c] = ss(v) if c in {"start", "end"} else rd(v, 4)
        out.append(item)
    return out


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
    """放量共振加权：以“当前60日聚合K量 > 前一根60日聚合K量”为主口径。

    只有有效共振K才会调用本函数；突破接受K、切实体K不参与放量共振加分。
    prev_vol_ratio = 当前60日聚合K成交量 / 前一根60日聚合K成交量。
    """
    pvr = sf(r.get("prev_vol_ratio", 1.0), 1.0) if hasattr(r, "get") else 1.0
    if pvr >= 3.0:
        return 1.5
    if pvr >= 1.8:
        return 1.2
    if pvr >= 1.3:
        return 0.8
    if pvr > 1.0:
        return 0.3
    return 0.0


def entity_loss_weight(r: Any) -> float:
    """切实体/接受失败损耗：同样按相较前一根是否放量加权。"""
    pvr = sf(r.get("prev_vol_ratio", 1.0), 1.0) if hasattr(r, "get") else 1.0
    if pvr >= 3.0:
        return 1.5
    if pvr >= 1.8:
        return 1.2
    if pvr >= 1.3:
        return 1.0
    return 0.6

def candidate_lines_from_bars(k: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    V67 第一核心压力线候选只从 high 产生。
    实体顶/上影线只参与“有效共振”打分，不再把候选线拖进实体内部。
    目的：找第一核心上沿/最低有效最高点，而不是箱体内部线。
    """
    out: List[Dict[str, Any]] = []
    if k is None or k.empty:
        return out

    for idx, r in k.iterrows():
        tag = f"{r.start}~{r.end}"
        rv = sf(r.get("rel_vol", 1.0), 1.0) if hasattr(r, "get") else 1.0
        hi = sf(r.high)
        if hi > 0:
            out.append({
                "line": rd(hi),
                "source": "high",
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
    """全盘唯一核心线打分。

    口径锁死：
    1）候选线只来自60日聚合K最高价。
    2）一根60日聚合K只能归一类：有效共振 / 切实体 / 接受状态 / 无关系。
    3）有效共振只看上方反应：最高价贴线、上影线打到、实体顶贴线但未切实体。
    4）最低价、下影线、实体底、收盘价不参与核心线共振计数。
    5）线进实体内部 = 切实体，扣分，不算共振。
    6）实体整体站上线后一直没跌回 = 接受成功，不扣分，也不算共振。
    7）实体站上线后又有效跌回 = 接受失败，扣分。
    8）放量只按当前60日聚合K量相较前一根60日聚合K量，不再按前4根均量主判。
    """
    L = sf(line)

    ordinary_hit = 0
    body_top_touch = 0
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

    accept_positions: List[int] = []
    fallback_after_accept = False
    first_accept_index = -1
    first_accept_date = ""
    try:
        k_reset = k.reset_index(drop=True)
        for _idx, _r0 in k_reset.iterrows():
            _bb0 = sf(_r0.get("body_bottom", 0)) if hasattr(_r0, "get") else sf(_r0.body_bottom)
            _cl0 = sf(_r0.get("close", 0)) if hasattr(_r0, "get") else sf(_r0.close)
            if L > 0 and _bb0 > L:
                accept_positions.append(int(_idx))
            if accept_positions and _cl0 < L * (1 - TOL):
                fallback_after_accept = True
        accepted_and_held = bool(accept_positions and not fallback_after_accept)
        accepted_then_failed = bool(accept_positions and fallback_after_accept)
        if accept_positions:
            first_accept_index = int(accept_positions[0])
            try:
                _fa = k_reset.iloc[first_accept_index]
                first_accept_date = f"{_fa.start}~{_fa.end}"
            except Exception:
                first_accept_date = ""
    except Exception:
        accepted_and_held = False
        accepted_then_failed = False
        first_accept_index = -1
        first_accept_date = ""

    hit_dates: List[str] = []
    cut_dates: List[str] = []
    accept_dates: List[str] = []
    evidence: List[Dict[str, Any]] = []
    body_cut_events: List[Dict[str, Any]] = []
    entity_accept_events: List[Dict[str, Any]] = []
    resonance_events: List[Dict[str, Any]] = []

    for _, r in k.iterrows():
        bt, bb, hi, lo, op, cl = sf(r.body_top), sf(r.body_bottom), sf(r.high), sf(r.low), sf(r.open), sf(r.close)
        if L <= 0 or bt <= 0 or bb <= 0 or hi <= 0 or lo <= 0:
            continue

        tag = f"{r.start}~{r.end}"
        rel_vol = sf(r.get("rel_vol", 1.0), 1.0) if hasattr(r, "get") else 1.0
        prev_vol_ratio = sf(r.get("prev_vol_ratio", 1.0), 1.0) if hasattr(r, "get") else 1.0
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
            "prev_vol_ratio": rd(prev_vol_ratio, 3),
            "vol_rank_pct": rd(vol_rank, 3),
        }

        touch_body_top = near(bt, L)
        touch_high = near(hi, L)

        # 线进入实体内部就是切实体；实体顶0.5%贴线属于上边界反应，不算切实体。
        is_cut = bool((bb < L < bt) and not touch_body_top)
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
            continue

        # 共振只数上方反应：最高价贴线、上影线打到、实体顶贴线。
        # 最低价、下影线、实体底、收盘价不参与核心线共振计数。
        upper_wick_hit = bool(bt <= L <= hi * (1 + TOL))
        is_resonance = bool(touch_high or touch_body_top or upper_wick_hit)

        if is_resonance:
            ordinary_hit += 1
            ordinary_score += 1.0
            hit_dates.append(tag)
            if upper_wick_hit and not touch_body_top:
                upper_hit += 1
            if touch_body_top:
                body_top_touch += 1
            if touch_high:
                high_touch += 1

            vb = volume_event_bonus(r)
            if vb > 0:
                vol_hit += 1
                volume_bonus_score += vb

            ev = dict(base_row)
            ev.update({
                "ordinary_hit": True,
                "upper_shadow_hit": bool(upper_wick_hit),
                "body_top_touch": bool(touch_body_top),
                "high_touch": bool(touch_high),
                "body_cut": False,
                "entity_accept": False,
                "volume_bonus": rd(vb, 3),
            })
            resonance_events.append(ev)
            if evidence_limit > 0 and len(evidence) < evidence_limit:
                evidence.append(ev)
            continue

        # 没有打线/贴线，但实体整体在线上方：只记录接受状态，不算共振。
        is_accept = bool(bb > L)
        if is_accept:
            entity_accept += 1
            raw_w = entity_loss_weight(r)
            w = raw_w if accepted_then_failed else 0.0
            entity_accept_penalty += w
            accept_dates.append(tag)
            if raw_w > 0.6:
                volume_entity_accept += 1
            ev = dict(base_row)
            ev.update({
                "entity_loss_weight": rd(w, 3),
                "raw_entity_loss_weight": rd(raw_w, 3),
                "reason": "entity_accept_held_no_penalty" if accepted_and_held else "entity_accept_failed_penalty" if accepted_then_failed else "entity_accept",
                "accepted_and_held": bool(accepted_and_held),
                "accepted_then_failed": bool(accepted_then_failed),
            })
            entity_accept_events.append(ev)
            continue

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
    core_candidate = meaningful and net_score >= max(1.0, MIN_CORE_HIT_COUNT - 2.0)

    if core_candidate and ordinary_hit >= 5 and net_score >= 4:
        line_type, level = "core_line", "第一核心线候选"
    elif core_candidate:
        line_type, level = "core_line", "核心线候选"
    elif meaningful:
        line_type, level = "logic_analysis_line", "共振不足以确认核心"
    elif ordinary_hit >= 2:
        line_type, level = "logic_analysis_line", "普通反应线"
    else:
        line_type, level = "non_core", "未成线"

    if accepted_and_held and entity_accept > 0:
        current_state = "已被实体突破接受且未跌回"
    elif accepted_then_failed:
        current_state = "突破接受后又有效跌回"
    elif entity_accept > 0:
        current_state = "已被实体整体接受"
    else:
        current_state = "未被实体整体接受"

    clean = body_cut == 0 and entity_accept_penalty == 0
    need_escalate = meaningful and not core_candidate

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
        "净分 = 有效共振数 + 放量共振加分 - 切实体扣分 - 接受失败扣分；"
        "有效共振只数最高价贴线、上影线打到、实体顶贴线；"
        "最低价、下影线、实体底、收盘价不参与核心线共振；"
        "放量按当前60日聚合K成交量/前一根60日聚合K成交量；"
        "突破接受且未跌回不扣分"
    )

    return {
        "line": rd(L),
        "core_line": rd(L),
        "line_type": line_type,
        "level": level,
        "timeframe": timeframe,
        "score": rd(net_score, 3),
        "net_score": rd(net_score, 3),
        "raw_net_score": rd(net_score, 3),
        "gross_score": rd(gross_score, 3),
        "ordinary_resonance_count": ordinary_hit,
        "effective_resonance_count": ordinary_hit,
        "resonance_score": rd(ordinary_score, 3),
        "volume_bonus_score": rd(volume_bonus_score, 3),
        "hit_count": ordinary_hit,
        "upper_shadow_hit_count": upper_hit,
        "entity_top_as_shadow_count": body_top_touch,
        "body_top_touch_count": body_top_touch,
        "high_touch_count": high_touch,
        "volume_hit_count": vol_hit,
        "stage_low_volume_high_touch_count": stage_low_volume_high_touch,
        "stage_low_volume_bar": stage_low_volume_bar,
        "body_cut_count": body_cut,
        "body_cut_penalty": rd(body_cut_penalty, 3),
        "entity_accept_count": entity_accept,
        "entity_accept_penalty": rd(entity_accept_penalty, 3),
        "entity_loss_score": rd(entity_loss_score, 3),
        "accepted_and_held": bool(accepted_and_held),
        "accepted_then_failed": bool(accepted_then_failed),
        "first_accept_index": first_accept_index,
        "first_accept_date": first_accept_date,
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
        "confirmation_source": "v90_full_resonance_unique_coreline",
    }

# 兼容旧函数名；内部已经升级为 V62 最大共振净分逻辑。
def score_shadow_line(k: pd.DataFrame, line: float, timeframe: str) -> Dict[str, Any]:
    return score_core_reaction_line(k, line, timeframe)


def _score_rank_tuple(x: Dict[str, Any]) -> Tuple[float, int, float, float, float]:
    """最终排序：净分第一；净分接近时再看有效共振、放量共振、实体损耗。"""
    return (
        sf(x.get("net_score", x.get("score", 0))),
        int(x.get("effective_resonance_count", x.get("ordinary_resonance_count", x.get("hit_count", 0))) or 0),
        sf(x.get("volume_bonus_score", 0)),
        -sf(x.get("entity_loss_score", 0)),
        -sf(x.get("line", 0)),
    )


def _group_scored_by_price_band(scored: List[Dict[str, Any]], band_tol: float = FIRST_CORE_BAND_TOL) -> List[List[Dict[str, Any]]]:
    xs = sorted([x for x in scored if sf(x.get("line")) > 0], key=lambda x: sf(x.get("line")))
    groups: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    anchor = 0.0
    for x in xs:
        L = sf(x.get("line"))
        if not cur:
            cur = [x]
            anchor = L
            continue
        if anchor > 0 and abs(L - anchor) / anchor <= band_tol:
            cur.append(x)
        else:
            groups.append(cur)
            cur = [x]
            anchor = L
    if cur:
        groups.append(cur)
    return groups


def choose_first_core_boundary_in_band(band: List[Dict[str, Any]]) -> Dict[str, Any]:
    """同一价格反应带内，先剔除净分非正候选，再按净分选代表线。"""
    if not band:
        return {}
    eligible = [x for x in band if sf(x.get("net_score", x.get("score", 0))) > 0]
    if not eligible:
        return {}
    ordered = sorted(eligible, key=_score_rank_tuple, reverse=True)
    chosen = ordered[0]
    chosen["same_band_boundary_comparisons"] = [
        {
            "line": rd(x.get("line")),
            "effective_resonance_count": x.get("effective_resonance_count"),
            "volume_bonus_score": x.get("volume_bonus_score"),
            "entity_loss_score": x.get("entity_loss_score"),
            "net_score": x.get("net_score"),
        }
        for x in ordered[:8]
    ]
    chosen["same_band_selection_rule"] = "同一反应带内先剔除净分非正候选；再按净分优先，净分接近时看有效共振、放量共振和实体损耗。"
    return chosen


def choose_max_resonance_net_boundary(scored: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not scored:
        return {}
    scored = [x for x in scored if sf(x.get("net_score", x.get("score", 0))) > 0]
    if not scored:
        return {}
    winners = [choose_first_core_boundary_in_band(g) for g in _group_scored_by_price_band(scored, FIRST_CORE_BAND_TOL)]
    winners = [x for x in winners if x and sf(x.get("net_score", x.get("score", 0))) > 0]
    if not winners:
        return {}
    chosen = sorted(winners, key=_score_rank_tuple, reverse=True)[0]
    chosen["core_selection_rule"] = "net_score_first_high_candidate_upper_resonance_only"
    chosen["core_selection_note"] = "从全盘60日聚合K最高价候选里选唯一核心线；先剔除净分非正候选；最终按净分优先，净分接近时再看有效共振、放量共振、实体损耗；有效共振只数最高价/上影线/实体顶；放量按当前量/前一根量；切实体扣分；突破接受且未跌回不扣分。"
    return chosen


# 兼容旧函数名，避免其他地方调用失败。
def choose_cleanest_lowest_high(scored: List[Dict[str, Any]]) -> Dict[str, Any]:
    return choose_max_resonance_net_boundary(scored)


def find_shadow_coreline(df: pd.DataFrame, window: int, timeframe: str, raw_k_override: Optional[pd.DataFrame] = None, anchor_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raw_k = raw_k_override.copy() if isinstance(raw_k_override, pd.DataFrame) and not raw_k_override.empty else aggregate_bars(df, window)
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
            selected["cluster_selection_rule"] = "60日第一核心线：先精确审计有效共振/切实体/实体接受；净分非正直接淘汰；同一反应带按净分优先"
            selected["candidate_source_summary"] = candidate_source_summary(raw_candidates, selected.get("line"))
            selected["cluster_all_lines"] = sorted(
                cluster_scored,
                key=lambda x: (sf(x.get("net_score")), int(x.get("effective_resonance_count", x.get("ordinary_resonance_count", 0))), sf(x.get("volume_bonus_score")), -sf(x.get("entity_loss_score")), -sf(x.get("line"))),
                reverse=True,
            )[:8]
            scored.append(selected)

    scored = sorted(
        scored,
        key=lambda x: (
            sf(x.get("net_score")),
            int(x.get("effective_resonance_count", x.get("ordinary_resonance_count", x.get("hit_count", 0)))),
            sf(x.get("volume_bonus_score")),
            -sf(x.get("entity_loss_score")),
            -sf(x.get("line")),
        ),
        reverse=True,
    )

    best = choose_max_resonance_net_boundary(scored) if scored else None

    if not best:
        return {
            "level": "未识别",
            "line": None,
            "line_type": "none",
            "timeframe": timeframe,
            "excluded_current_bar": excluded,
            "text": f"{timeframe}未识别到有效候选。",
        }

    best["raw_60d_bars"] = bars_audit_records(raw_k)
    best["completed_60d_bars_for_scoring"] = bars_audit_records(k)
    best["all_candidates"] = scored[:10]
    best["high_cluster_count"] = len(clusters)
    best["reaction_cluster_count"] = len(clusters)
    best["first_core_band_tol_pct"] = rd(FIRST_CORE_BAND_TOL * 100, 3)
    best["lower_line_required_net_advantage"] = rd(LOWER_LINE_NET_ADVANTAGE, 3)
    best["excluded_current_bar"] = excluded
    best["excluded_current_bar_note"] = "最新一根聚合K不参与历史成线，但可在报告中作为当前确认观察。"
    if anchor_info:
        best["applied_60d_anchor"] = anchor_info
        try:
            best["current_excluded_bar_relation_to_line"] = classify_line_on_bar(sf(best.get("line")), excluded, TOL) if excluded else "NONE"
        except Exception:
            best["current_excluded_bar_relation_to_line"] = "UNKNOWN"

    if best.get("line_type") == "core_line":
        best["text"] = (
            f"{timeframe}识别核心线：{best['line']}元。"
            f"已排除最新未完成聚合K（{excluded.get('start')}~{excluded.get('end')}）做历史成线。"
            f"有效共振{best.get('ordinary_resonance_count')}根；"
            f"实体顶贴线{best['body_top_touch_count']}次、"
            f"最高价贴线{best['high_touch_count']}次、上影线打到{best.get('upper_shadow_hit_count')}次；"
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
        and int(x.get("effective_resonance_count", x.get("ordinary_resonance_count", x.get("hit_count", 0)))) >= MIN_CORE_HIT_COUNT
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
        int(x.get("effective_resonance_count", x.get("ordinary_resonance_count", x.get("hit_count", 0)))),
        sf(x.get("volume_bonus_score", 0)),
        -sf(x.get("entity_loss_score", 0)),
        -priority,
    )


def core_line(df: pd.DataFrame, code: str = "", anchor_calibration: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """V73：只做60日聚合K第一核心线；若锚点校准成功，则用校准后的60日K正式评分。"""
    if len(df) < 80:
        return {"level": "数据不足", "line": None, "line_type": "none", "text": "历史K线不足，不能硬画60日核心线。"}

    anchor_info: Dict[str, Any] = {"applied": False, "reason": "no_anchor_applied"}
    raw_k_override: Optional[pd.DataFrame] = None
    cal = anchor_calibration if isinstance(anchor_calibration, dict) else (calibrate_60d_anchor_for_code(code, df, 60) if code else {})
    best_anchor = cal.get("best", {}) if isinstance(cal, dict) else {}
    if APPLY_60D_ANCHOR and best_anchor and sf(best_anchor.get("total_error"), 9999) <= ANCHOR_MAX_TOTAL_ERROR:
        mode = ss(best_anchor.get("mode")) or "backward"
        offset = int(sf(best_anchor.get("offset"), 0))
        raw_k_override = aggregate_bars_with_anchor(df, window=60, mode=mode, offset=offset)
        anchor_info = {
            "applied": True,
            "mode": mode,
            "offset": offset,
            "total_error": rd(best_anchor.get("total_error"), 6),
            "max_allowed_error": ANCHOR_MAX_TOTAL_ERROR,
            "source": "v73_user_chart_anchor_calibration",
            "samples": get_60d_anchor_samples(code),
        }
    elif best_anchor:
        anchor_info = {
            "applied": False,
            "reason": "anchor_error_too_large_or_disabled",
            "best_mode": best_anchor.get("mode"),
            "best_offset": best_anchor.get("offset"),
            "best_total_error": best_anchor.get("total_error"),
            "max_allowed_error": ANCHOR_MAX_TOTAL_ERROR,
            "apply_enabled": APPLY_60D_ANCHOR,
        }

    seasonal = find_shadow_coreline(df, 60, "60日聚合K", raw_k_override=raw_k_override, anchor_info=anchor_info)
    seasonal["seasonal_candidate"] = seasonal
    seasonal["monthly_candidate"] = {}
    seasonal["yearly_candidate"] = {}
    seasonal["timeframe_decision"] = "只采用60日聚合K"
    seasonal["anchor_calibration_60d"] = cal
    seasonal["text"] = seasonal.get("text", "") + " 周期决策：V73只看前复权60日聚合K第一核心线；若锚点校准通过，则正式应用校准锚点。"
    return seasonal

def _fmt_score(x: Dict[str, Any]) -> str:
    if not x or x.get("line") is None:
        return "无"
    return (
        f"{x.get('line')}｜{x.get('timeframe')}｜净{x.get('net_score', x.get('score'))}"
        f"｜毛{x.get('gross_score', '')}｜有效共振{x.get('effective_resonance_count', x.get('ordinary_resonance_count', x.get('hit_count')))}"
        f"｜放量+{x.get('volume_bonus_score', 0)}｜损耗{x.get('entity_loss_score', 0)}"
        f"｜切{x.get('body_cut_count')}｜受{x.get('entity_accept_count')}｜{x.get('current_state', '')}"
    )


def core_line_summary(cl: Dict[str, Any]) -> str:
    if ss(cl.get("line_type")) == "core_line":
        return f"核心线｜{_fmt_score(cl)}｜{cl.get('level')}"

    if ss(cl.get("line_type")) == "logic_analysis_line":
        return f"逻辑线｜{_fmt_score(cl)}｜{cl.get('level')}"

    return f"未识别核心线｜上方极值压力 {cl.get('upper_extreme_pressure')}｜{cl.get('timeframe_decision', '')}"


def _event_relation_cn(e: Dict[str, Any]) -> str:
    """把一根聚合K与核心线的关系翻成人能看懂的中文。"""
    parts: List[str] = []
    if e.get("upper_shadow_hit"):
        parts.append("上影线打到")
    if e.get("high_touch"):
        parts.append("最高价贴线")
    if e.get("body_top_touch"):
        parts.append("实体顶贴线")
    if e.get("body_cut"):
        parts.append("切实体")
    if e.get("entity_accept") or ss(e.get("reason", "")).startswith("entity_accept"):
        if e.get("accepted_and_held"):
            parts.append("突破接受且未跌回")
        elif e.get("accepted_then_failed"):
            parts.append("突破接受后跌回")
        else:
            parts.append("实体站上")
    return "+".join(parts) if parts else ss(e.get("reason")) or "反应"


def _fmt_event_line(i: int, e: Dict[str, Any]) -> str:
    return (
        f"  {i}. {e.get('date')}｜{_event_relation_cn(e)}｜"
        f"开{e.get('open')} 高{e.get('high')} 低{e.get('low')} 收{e.get('close')}｜"
        f"实体{e.get('body_bottom')}~{e.get('body_top')}｜量比{e.get('rel_vol')}｜放量+{e.get('volume_bonus', 0)}"
    )


def core_line_audit_lines(cl: Dict[str, Any], limit: int = 20) -> List[str]:
    """输出当前核心线的逐条证据，避免只给一个“共振N”的黑箱数字。"""
    out: List[str] = []
    if not isinstance(cl, dict) or cl.get("line") is None:
        return out

    resonance = cl.get("resonance_events") or []
    cuts = cl.get("body_cut_events") or []
    accepts = cl.get("entity_accept_events") or []

    out.append(f"  共振明细（{len(resonance)}）：")
    if resonance:
        for i, e in enumerate(resonance[:limit], 1):
            out.append(_fmt_event_line(i, e))
        if len(resonance) > limit:
            out.append(f"  ... 还有{len(resonance) - limit}条共振明细未展开")
    else:
        out.append("  无")

    out.append(f"  切实体明细（{len(cuts)}）：")
    if cuts:
        for i, e in enumerate(cuts[:limit], 1):
            out.append(
                f"  {i}. {e.get('date')}｜切实体｜开{e.get('open')} 高{e.get('high')} 低{e.get('low')} 收{e.get('close')}｜"
                f"实体{e.get('body_bottom')}~{e.get('body_top')}｜量比{e.get('rel_vol')}｜扣{e.get('entity_loss_weight')}"
            )
        if len(cuts) > limit:
            out.append(f"  ... 还有{len(cuts) - limit}条切实体明细未展开")
    else:
        out.append("  无")

    out.append(f"  接受/站上明细（{len(accepts)}）：")
    if accepts:
        for i, e in enumerate(accepts[:limit], 1):
            out.append(
                f"  {i}. {e.get('date')}｜{_event_relation_cn(e)}｜开{e.get('open')} 高{e.get('high')} 低{e.get('low')} 收{e.get('close')}｜"
                f"实体{e.get('body_bottom')}~{e.get('body_top')}｜量比{e.get('rel_vol')}｜扣{e.get('entity_loss_weight', 0)}"
            )
        if len(accepts) > limit:
            out.append(f"  ... 还有{len(accepts) - limit}条接受明细未展开")
    else:
        out.append("  无")
    return out


def compact_core_line_for_output(cl: Dict[str, Any]) -> Dict[str, Any]:
    """报告/JSON只保留核心线必要字段，不落大段候选证据。"""
    if not isinstance(cl, dict):
        return {}
    keys = [
        "line", "core_line", "line_type", "level", "timeframe",
        "score", "net_score", "gross_score",
        "effective_resonance_count", "ordinary_resonance_count", "hit_count",
        "volume_bonus_score", "volume_hit_count",
        "entity_loss_score", "body_cut_count", "body_cut_penalty",
        "entity_accept_count", "entity_accept_penalty",
        "accepted_and_held", "accepted_then_failed", "first_accept_index", "first_accept_date",
        "volume_body_cut_count", "volume_entity_accept_count",
        "current_state", "clean_core_line", "tol_pct",
        "core_band_low", "core_band_high", "upper_extreme_pressure",
        "timeframe_decision", "confirmation_source",
    ]
    return {k: cl.get(k) for k in keys if k in cl}


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
        obj_is_not_pandas_scalar = True

    if hasattr(obj, "item"):
        try:
            return safe_jsonable(obj.item(), seen)
        except Exception:
            return ss(obj)

    return ss(obj)


def build_report(hist: Dict[str, pd.DataFrame], stat: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """只输出样本交易日涨停股核心线。"""
    sample_trade_date = choose_sample_trade_date(hist) if hist else ""
    A = pick_samples(hist, sample_trade_date) if hist and sample_trade_date else pd.DataFrame()

    sample_dates = [sample_trade_date] if sample_trade_date else []
    sample_date_text = sample_trade_date or "无"

    lines = [
        "# 五号员工：涨停核心线",
        f"- 运行日期：{TARGET}",
        f"- 样本交易日：{sample_date_text}",
        "",
    ]

    results = []

    if A.empty:
        lines.append("- 无涨停样本。")
    else:
        coreline_total = len(A)
        coreline_start = time.time()
        print_progress("coreline", 0, coreline_total, coreline_start, extra="start")
        for pos, (_, r) in enumerate(A.iterrows(), 1):
            c = ss(r.get("code"))
            full_df = load_full_history(c, hist.get(c, pd.DataFrame()))
            anchor_cal = calibrate_60d_anchor_for_code(c, full_df, 60)
            cl = core_line(full_df, code=c, anchor_calibration=anchor_cal)

            line = cl.get("line")
            state = cl.get("current_state", "")
            level = cl.get("level", "")
            net = cl.get("net_score", cl.get("score", ""))
            hit = cl.get("effective_resonance_count", cl.get("ordinary_resonance_count", cl.get("hit_count", "")))
            vol_bonus = cl.get("volume_bonus_score", 0)
            loss = cl.get("entity_loss_score", 0)

            if line is None:
                lines.append(f"{pos}. {c}｜核心线：无｜{level}")
            else:
                lines.append(
                    f"{pos}. {c}｜核心线：{line}｜净分{net}｜共振{hit}｜放量+{vol_bonus}｜损耗{loss}｜{state}"
                )
                lines.extend(core_line_audit_lines(cl))

            results.append({
                "group": "涨停样本",
                "code": c,
                "sample": r.to_dict(),
                "core_line": compact_core_line_for_output(cl),
            })

            if pos == 1 or pos % CORELINE_PROGRESS_EVERY == 0 or pos == coreline_total:
                print_progress(
                    "coreline",
                    pos,
                    coreline_total,
                    coreline_start,
                    extra=f"current={c} line={line}",
                )

    payload = {
        "target_date": TARGET,
        "target_manual": TARGET_MANUAL,
        "sample_trade_dates": sample_dates,
        "cache_date_profile": cache_date_profile(hist),
        "boot_id": BOOT,
        "cache_stats": stat,
        "a_pool": A.to_dict("records") if not A.empty else [],
        "results": results,
        "research_only": True,
        "core_line_method": "v73_qfq_apply_60d_anchor",
        "core_line_tol": TOL,
        "limit_up_tol": LIMIT_UP_TOL,
        "exclude_current_agg_bar": True,
        "entity_accept_hard_filter": False,
        "seasonal_60d_only": True,
        "qfq_only": True,
        "adjust": QFQ_ADJUST,
        "adjustflag": QFQ_ADJUSTFLAG,
        "delete_non_qfq_cache": DELETE_NON_QFQ_CACHE,
        "delete_legacy_after_qfq_save": DELETE_LEGACY_AFTER_QFQ_SAVE,
        "qfq_rebuild_state_file": str(QFQ_REBUILD_STATE_FILE),
        "qfq_manifest_file": str(QFQ_MANIFEST_FILE),
        "flat_csv_cache": True,
        "trust_public_flat_csv": TRUST_PUBLIC_FLAT_CSV,
        "rebuild_codes": QFQ_REBUILD_CODES_RAW,
        "report_style": "limit_up_core_lines_only_clean",
        "score_formula": "net_score = effective_resonance_count + volume_bonus_score - body_cut_penalty - failed_entity_accept_penalty; final selection requires net_score > 0 and ranks by net_score first; accepted-and-held breakthrough does not deduct score; line inside entity is CUT, line in upper wick is RESONANCE; candidate lines only from 60d high",
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
        "network_hist_allowed": bool(ALLOW_BAOSTOCK_FALLBACK),
        "data_source": payload.get("cache_stats", {}).get("source"),
        "core_line_method": payload.get("core_line_method"),
        "core_line_tol": TOL,
        "exclude_current_agg_bar": True,
        "entity_accept_hard_filter": False,
        "seasonal_60d_only": True,
        "qfq_only": True,
        "adjust": QFQ_ADJUST,
        "adjustflag": QFQ_ADJUSTFLAG,
        "delete_non_qfq_cache": DELETE_NON_QFQ_CACHE,
        "delete_legacy_after_qfq_save": DELETE_LEGACY_AFTER_QFQ_SAVE,
        "qfq_rebuild_state_file": str(QFQ_REBUILD_STATE_FILE),
        "qfq_manifest_file": str(QFQ_MANIFEST_FILE),
        "flat_csv_cache": True,
        "rebuild_codes": QFQ_REBUILD_CODES_RAW,
        "report_style": payload.get("report_style", "compact"),
        "score_formula": payload.get("score_formula", ""),
        "scan_keep_rows": SCAN_KEEP_ROWS,
        "memsafe_scan": True,
        "jsonsafe_payload": True,
    })

    # V89：只落一个可直接下载/查看的 Markdown 文件；不再落大 JSON，避免 artifact 过大。
    files = {
        "limit_up_research_report.md": md,
    }

    for name, content in files.items():
        (REPORT_DIR / name).write_text(content, encoding="utf-8")


def main() -> None:
    print(BOOT, flush=True)
    print(f"file={Path(__file__).resolve()}", flush=True)
    print(f"target_date={TARGET} target_dash={TARGET_DASH} recent_refresh={INCREMENTAL_REFRESH} baostock_allowed={ALLOW_BAOSTOCK_FALLBACK} full_rebuild_allowed={ALLOW_FULL_REBUILD}", flush=True)
    print("cache_dirs=" + " | ".join(str(x) for x in CACHE_DIRS), flush=True)

    hist, stat = load_cache()
    print(f"cache_stats={stat}", flush=True)

    profile = cache_date_profile(hist)
    print(f"cache_date_profile={profile}", flush=True)

    if hist and profile.get("cache_majority_date", "") < TARGET_DASH and ALLOW_BAOSTOCK_FALLBACK and INCREMENTAL_REFRESH:
        print("公共缓存日期落后：按一号员工缓存链路只补最近5个交易日，不做全历史重建", flush=True)
        hist, stat = refresh_recent_cache(hist)
        print(f"recent_qfq_refresh_stats={stat}", flush=True)
    elif not hist:
        print("qfq cache empty; 五号员工不从零全市场重建，请先恢复/运行一号员工公共缓存", flush=True)

    if ALLOW_FULL_REBUILD and ALLOW_BAOSTOCK_FALLBACK and (QFQ_REBUILD_ALWAYS or (not hist and QFQ_REBUILD_FORCE)):
        print("显式允许全历史重建：开始 baostock qfq rebuild / resume", flush=True)
        hist, stat = build_baostock_cache(existing_hist=hist)
        print(f"baostock_qfq_rebuild_stats={stat}", flush=True)

    md, payload = build_report(hist, stat)
    write_outputs(md, payload)
    send_report(md[:9000])
    print(f"Employee5 done. Reports: {REPORT_DIR}", flush=True)


if __name__ == "__main__":
    main()
