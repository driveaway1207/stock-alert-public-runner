# -*- coding: utf-8 -*-
from __future__ import annotations

"""五号员工：涨停核心线报告（V103）

只读公共前复权缓存；只扫样本交易日涨停股；报告保持简洁。
核心线口径：20日聚合K；锚点=最高价/实体顶/阳线放量收盘价；净分=有效共振数。
"""

import json
import math
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

try:
    import requests
except Exception:
    requests = None

try:
    import baostock as bs
except Exception:
    bs = None

BOOT = "EMPLOYEE5_PUBLIC_BOOT_20260530_V103_SIMPLE_CORE"
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "employee5_reports"
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

TOL = float(os.getenv("EMPLOYEE5_CORE_LINE_TOL", "0.01"))
LIMIT_UP_TOL = float(os.getenv("EMPLOYEE5_LIMIT_UP_TOL", "0.15"))
MIN_ROWS = int(os.getenv("EMPLOYEE5_MIN_CACHE_ROWS", "22"))
MIN_CORE_HIT_COUNT = int(os.getenv("EMPLOYEE5_MIN_CORE_HIT_COUNT", "3"))
CORELINE_PROGRESS_EVERY = int(os.getenv("EMPLOYEE5_CORELINE_PROGRESS_EVERY", "10"))
TOP_CANDIDATE_SHOW = int(os.getenv("EMPLOYEE5_TOP_CANDIDATE_SHOW", "3"))
CACHE_SCAN_PROGRESS_EVERY = int(os.getenv("EMPLOYEE5_CACHE_SCAN_PROGRESS_EVERY", "500"))
ALLOW_BAOSTOCK_FALLBACK = os.getenv("EMPLOYEE5_ALLOW_BAOSTOCK_FALLBACK", "0") == "1"
INCREMENTAL_REFRESH = os.getenv("EMPLOYEE5_INCREMENTAL_REFRESH", "1") != "0"
RECENT_REFRESH_DAYS = int(os.getenv("EMPLOYEE5_RECENT_REFRESH_DAYS", "10"))
RECENT_REFRESH_BUDGET_MIN = float(os.getenv("EMPLOYEE5_RECENT_REFRESH_BUDGET_MIN", "35"))
QFQ_ADJUSTFLAG = "2"


def _now_bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _prev_workday(d: datetime) -> datetime:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _target_raw() -> str:
    for key in [
        "EMPLOYEE5_TARGET_DATE",
        "LAST_TRADE_DAY_OVERRIDE",
        "REQUIRED_CACHE_DATE",
        "SELECTION_TRADE_DATE",
        "DATA_GATE_TARGET_DATE",
        "TARGET_TRADE_DATE",
    ]:
        value = os.getenv(key)
        if value:
            return value
    now = _now_bj()
    if now.weekday() >= 5 or (now.hour < 20 or (now.hour == 20 and now.minute < 35)):
        now = _prev_workday(now - timedelta(days=1))
    return now.strftime("%Y%m%d")


TARGET = re.sub(r"\D", "", _target_raw())[:8]
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


def rd(x: Any, n: int = 2) -> float:
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
    return code.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4"))


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


def fmt_seconds(seconds: float) -> str:
    if seconds <= 0 or math.isnan(seconds) or math.isinf(seconds):
        return "0s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}m"


def color_on() -> bool:
    return os.getenv("EMPLOYEE5_PROGRESS_COLOR", "1") != "0" and os.getenv("NO_COLOR", "") == ""


def ansi(text: str, color: str) -> str:
    if not color_on():
        return text
    codes = {"green": "32", "cyan": "36", "yellow": "33", "magenta": "35"}
    return f"\033[1;{codes.get(color, '32')}m{text}\033[0m"


def progress(stage: str, done: int, total: int, start: float, extra: str = "") -> None:
    if total <= 0:
        return
    pct = min(max(done / total, 0.0), 1.0)
    width = 22
    filled = int(round(width * pct))
    bar = "█" * filled + "░" * (width - filled)
    elapsed = time.time() - start
    speed = done / elapsed if elapsed > 0 and done > 0 else 0.0
    eta = (total - done) / speed if speed > 0 else 0.0
    icon, color = {
        "cache": ("🔎", "cyan"),
        "refresh": ("🔄", "yellow"),
        "coreline": ("⚡", "magenta"),
    }.get(stage, ("▶", "green"))
    msg = f"{icon} {stage} {bar} {pct * 100:5.1f}% {done}/{total} elapsed={fmt_seconds(elapsed)} eta={fmt_seconds(eta)} speed={speed:.2f}/s"
    if extra:
        msg += f" | {extra}"
    print(ansi(msg, color), flush=True)


def normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    mp = {
        "日期": "date", "交易日期": "date", "date": "date", "time": "date",
        "代码": "code", "code": "code",
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
    d["date"] = d["date"].map(norm_date)
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
        obj = pd.read_csv(path)
        return normalize_hist(obj)
    except Exception:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            rows = obj.get("rows") or obj.get("data") or obj.get("klines") or []
            return normalize_hist(pd.DataFrame(rows))
        except Exception:
            return pd.DataFrame()


def iter_cache_files() -> List[Path]:
    seen: Dict[str, Path] = {}
    for d in CACHE_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*")):
            if p.suffix.lower() not in {".csv", ".json"}:
                continue
            code = code_of(p)
            if valid_code(code) and code not in seen:
                seen[code] = p
    return list(seen.values())


def load_cache() -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    files = iter_cache_files()
    hist: Dict[str, pd.DataFrame] = {}
    stat = {"source": "public_kline_cache", "cache_files": len(files), "cache_hit": 0, "bad": 0, "short": 0}
    start = time.time()
    progress("cache", 0, len(files), start, "start")
    for i, p in enumerate(files, 1):
        code = code_of(p)
        df = read_cache_file(p)
        if df.empty:
            stat["bad"] += 1
        elif len(df) < MIN_ROWS:
            stat["short"] += 1
        else:
            hist[code] = df
            stat["cache_hit"] += 1
        if i == 1 or i % CACHE_SCAN_PROGRESS_EVERY == 0 or i == len(files):
            progress("cache", i, len(files), start, f"hit={stat['cache_hit']} bad={stat['bad']} short={stat['short']} current={code}")
    return hist, stat


def cache_path(code: str) -> Path:
    MAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return MAIN_CACHE_DIR / f"{code}.csv"


def save_cache(code: str, df: pd.DataFrame) -> bool:
    d = normalize_hist(df)
    if d.empty or len(d) < MIN_ROWS:
        return False
    cols = [c for c in ["date", "code", "open", "high", "low", "close", "volume", "amount", "pct_chg"] if c in d.columns]
    if "code" not in d.columns:
        d["code"] = code
        cols = [c for c in ["date", "code", "open", "high", "low", "close", "volume", "amount", "pct_chg"] if c in d.columns]
    out = cache_path(code)
    tmp = out.with_suffix(".csv.tmp")
    d[cols].to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, out)
    return True


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
    merged = pd.concat([existing, fresh], ignore_index=True)
    return normalize_hist(merged)


def refresh_recent_cache(hist: Dict[str, pd.DataFrame]) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    stat = {"source": "recent_refresh", "processed": 0, "saved": 0, "failed": 0, "skipped": 0}
    if not (ALLOW_BAOSTOCK_FALLBACK and INCREMENTAL_REFRESH and hist and bs is not None):
        stat["skipped_reason"] = "disabled_or_no_cache"
        return hist, stat
    lg = bs.login()
    print(f"baostock login: {getattr(lg, 'error_code', '')} {getattr(lg, 'error_msg', '')}", flush=True)
    start = time.time()
    budget = max(60.0, RECENT_REFRESH_BUDGET_MIN * 60.0)
    items = list(hist.items())
    progress("refresh", 0, len(items), start, "start")
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
        print(f"baostock logout skipped: {exc}", flush=True)
    return hist, stat


def cache_date_profile(hist: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    dates = []
    for df in hist.values():
        if df is not None and not df.empty:
            dates.append(ss(df.iloc[-1].get("date", "")))
    cnt = Counter(d for d in dates if d)
    common = cnt.most_common(1)[0][0] if cnt else ""
    return {"count": len(dates), "latest_date_mode": common, "latest_date_mode_count": cnt.get(common, 0) if common else 0}


def sample_trade_date(hist: Dict[str, pd.DataFrame]) -> str:
    return ss(cache_date_profile(hist).get("latest_date_mode", ""))


def pick_limit_up(hist: Dict[str, pd.DataFrame], trade_date: str) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for code, df in hist.items():
        if df is None or df.empty:
            continue
        d = df[df.date == trade_date]
        if d.empty:
            continue
        r = d.iloc[-1]
        name = ss(r.get("name", code))
        pct = sf(r.get("pct_chg"))
        if pct >= limit_pct(code, name) - LIMIT_UP_TOL:
            rows.append({"code": code, "name": name, "date": trade_date, "pct_chg": rd(pct), "close": rd(r.close)})
    return pd.DataFrame(rows).sort_values(["pct_chg", "code"], ascending=[False, True]).reset_index(drop=True) if rows else pd.DataFrame()


def aggregate_bars(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    d = normalize_hist(df)
    if d.empty or len(d) < max(22, window * 3):
        return pd.DataFrame()
    d = d.reset_index(drop=True)
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
    k["body_top"] = k[["open", "close"]].max(axis=1)
    k["body_bottom"] = k[["open", "close"]].min(axis=1)
    return k


def near(a: float, b: float, tol: float = TOL) -> bool:
    return b > 0 and abs(a - b) / b <= tol


def line_candidate_sources(k: pd.DataFrame) -> Dict[float, str]:
    sources: Dict[float, set] = {}

    def add(price: float, source: str) -> None:
        price = sf(price)
        if price > 0:
            sources.setdefault(rd(price), set()).add(source)

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
    hit = high_touch = upper_hit = body_top_touch = close_touch = inside_neutral = accept_count = 0
    for _, r in k.iterrows():
        hi, bt, bb, cl = sf(r.high), sf(r.body_top), sf(r.body_bottom), sf(r.close)
        if L <= 0 or hi <= 0 or bt <= 0 or bb <= 0:
            continue
        if bb > L:
            accept_count += 1
            continue
        if bb < L < bt:
            inside_neutral += 1
            continue
        is_high = near(hi, L)
        is_upper = bool(bt <= L <= hi)
        is_body_top = near(bt, L)
        is_close = near(cl, L)
        if is_high or is_upper or is_body_top or is_close:
            hit += 1
            high_touch += int(is_high)
            upper_hit += int(is_upper and not is_body_top)
            body_top_touch += int(is_body_top)
            close_touch += int(is_close)
    level = "核心线候选" if hit >= MIN_CORE_HIT_COUNT else "未成线"
    return {
        "line": rd(L),
        "score": hit,
        "net_score": hit,
        "effective_resonance_count": hit,
        "high_touch_count": high_touch,
        "upper_shadow_hit_count": upper_hit,
        "body_top_touch_count": body_top_touch,
        "close_touch_count": close_touch,
        "entity_inside_neutral_count": inside_neutral,
        "entity_accept_count": accept_count,
        "level": level,
        "line_type": "core_line" if hit >= MIN_CORE_HIT_COUNT else "non_core",
        "timeframe": "20日聚合K",
        "current_state": "实体接受记录" if accept_count else "未被实体整体接受",
    }


def group_by_band(scored: List[Dict[str, Any]], tol: float = 0.015) -> List[List[Dict[str, Any]]]:
    xs = sorted([x for x in scored if sf(x.get("line")) > 0], key=lambda x: sf(x.get("line")))
    groups: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    band_base = 0.0
    for x in xs:
        L = sf(x.get("line"))
        if not cur or (band_base > 0 and abs(L - band_base) / band_base <= tol):
            cur.append(x)
            band_base = band_base or L
        else:
            groups.append(cur)
            cur = [x]
            band_base = L
    if cur:
        groups.append(cur)
    return groups


def rank_key(x: Dict[str, Any]) -> Tuple[float, int, float]:
    return (sf(x.get("net_score")), int(sf(x.get("effective_resonance_count"))), -sf(x.get("line")))


def choose_core_line(df: pd.DataFrame) -> Dict[str, Any]:
    raw_k = aggregate_bars(df, 20)
    if raw_k.empty or len(raw_k) < 3:
        return {"line": None, "level": "数据不足", "text": "历史K线不足。"}
    completed = raw_k.iloc[:-1].reset_index(drop=True)
    if completed.empty:
        return {"line": None, "level": "数据不足", "text": "无已完成20日聚合K。"}
    sources = line_candidate_sources(completed)
    scored = []
    for L in sorted(sources):
        x = score_line(completed, L)
        x["source"] = sources.get(L, "")
        scored.append(x)
    scored = [x for x in scored if sf(x.get("net_score")) > 0]
    if not scored:
        return {"line": None, "level": "未识别", "text": "未识别到有效核心线。"}
    band_winners = [max(g, key=rank_key) for g in group_by_band(scored)]
    ranked = sorted(band_winners, key=rank_key, reverse=True)
    best = ranked[0]
    best["top_candidates"] = ranked[:TOP_CANDIDATE_SHOW]
    best["all_candidates_count"] = len(scored)
    best["excluded_current_bar"] = raw_k.iloc[-1].to_dict()
    return best


def top_candidate_text(cands: List[Dict[str, Any]]) -> str:
    parts = []
    for x in cands[:TOP_CANDIDATE_SHOW]:
        parts.append(f"{x.get('line')}/{x.get('effective_resonance_count')}/{x.get('source', '')}")
    return "；".join(parts)


def build_report(hist: Dict[str, pd.DataFrame], stat: Dict[str, Any]) -> str:
    trade_date = sample_trade_date(hist) if hist else ""
    samples = pick_limit_up(hist, trade_date) if trade_date else pd.DataFrame()
    lines = [
        "# 五号员工：涨停核心线",
        f"- 运行日期：{TARGET}",
        f"- 样本交易日：{trade_date or '无'}",
        f"- 缓存命中：{stat.get('cache_hit', 0)} / 文件数 {stat.get('cache_files', 0)}",
        "- 口径：20日聚合K｜锚点：最高价/实体顶/阳线放量收盘价｜共振：最高价/上影线/实体顶/收盘±1%",
        "",
    ]
    if samples.empty:
        lines.append("- 无涨停样本。")
        return "\n".join(lines)
    start = time.time()
    progress("coreline", 0, len(samples), start, "start")
    for pos, (_, row) in enumerate(samples.iterrows(), 1):
        code = ss(row.get("code"))
        cl = choose_core_line(hist.get(code, pd.DataFrame()))
        if cl.get("line") is None:
            lines.append(f"{pos}. {code}｜核心线：无｜{cl.get('level')}")
        else:
            top = top_candidate_text(cl.get("top_candidates") or [])
            suffix = f"｜候选：{top}" if top else ""
            lines.append(
                f"{pos}. {code}｜核心线：{cl.get('line')}｜共振{cl.get('effective_resonance_count')}｜来源{cl.get('source', '')}{suffix}"
            )
        if pos == 1 or pos % CORELINE_PROGRESS_EVERY == 0 or pos == len(samples):
            progress("coreline", pos, len(samples), start, f"current={code} line={cl.get('line')}")
    return "\n".join(lines)


def write_report(md: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "limit_up_research_report.md").write_text(md, encoding="utf-8")


def send_report(md: str) -> None:
    print(f"telegram_env_present token={bool(BOT)} chat={bool(CHAT)} requests={requests is not None}", flush=True)
    if not BOT or not CHAT or requests is None:
        print("telegram skipped; report preview below:", flush=True)
        print(md[:1800], flush=True)
        return
    url = f"https://api.telegram.org/bot{BOT}/sendMessage"
    for idx, part in enumerate([md[i:i + 3600] for i in range(0, len(md), 3600)], 1):
        try:
            resp = requests.post(url, json={"chat_id": CHAT, "text": part, "disable_web_page_preview": True}, timeout=30)
            print(f"telegram chunk {idx} status={getattr(resp, 'status_code', 'NA')} body={getattr(resp, 'text', '')[:120]}", flush=True)
        except Exception as exc:
            print(f"telegram failed chunk {idx}: {exc}", flush=True)
        time.sleep(0.4)


def main() -> None:
    print(BOOT, flush=True)
    print(f"file={Path(__file__).resolve()}", flush=True)
    print(f"target_date={TARGET} target_dash={TARGET_DASH} recent_refresh={INCREMENTAL_REFRESH} baostock_allowed={ALLOW_BAOSTOCK_FALLBACK}", flush=True)
    print("cache_dirs=" + " | ".join(str(x) for x in CACHE_DIRS), flush=True)
    hist, stat = load_cache()
    if hist and ALLOW_BAOSTOCK_FALLBACK and INCREMENTAL_REFRESH:
        hist, refresh_stat = refresh_recent_cache(hist)
        stat["recent_refresh"] = refresh_stat
    elif not hist:
        print("公共缓存为空：不做全市场历史重建，直接输出空报告。", flush=True)
    md = build_report(hist, stat)
    write_report(md)
    send_report(md[:9000])
    print(f"Employee5 done. Report: {REPORT_DIR / 'limit_up_research_report.md'}", flush=True)


if __name__ == "__main__":
    main()
