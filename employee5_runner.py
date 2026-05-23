# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import signal
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import akshare as ak
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "employee5_reports"
TARGET_DATE_ENV = os.getenv("EMPLOYEE5_TARGET_DATE", "").strip()
MAX_DEEP_ANALYZE = int(os.getenv("EMPLOYEE5_MAX_STOCKS", "300"))
AK_TIMEOUT_SECONDS = int(os.getenv("EMPLOYEE5_AK_TIMEOUT_SECONDS", "18"))
REQUEST_SLEEP = float(os.getenv("EMPLOYEE5_REQUEST_SLEEP", "0.12"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MA_PERIODS = [5, 10, 20, 30, 60, 100, 250]


class AkTimeout(Exception):
    pass


@contextmanager
def timeout_guard(seconds: int, label: str):
    def handler(signum, frame):
        raise AkTimeout(f"{label} timeout {seconds}s")
    old = signal.signal(signal.SIGALRM, handler)
    signal.alarm(max(1, int(seconds)))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        return float(str(x).replace("%", "").replace(",", ""))
    except Exception:
        return default


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def first_col(df: pd.DataFrame, cols: Iterable[str]) -> Optional[str]:
    for c in cols:
        if c in df.columns:
            return c
    return None


def latest_weekday(today: datetime) -> str:
    d = today
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def latest_trade_date() -> str:
    if TARGET_DATE_ENV:
        return TARGET_DATE_ENV.replace("-", "")
    today = datetime.now()
    today_ymd = today.strftime("%Y-%m-%d")
    try:
        df = ak.tool_trade_date_hist_sina()
        if df is not None and not df.empty and "trade_date" in df.columns:
            values = []
            for x in df["trade_date"].tolist():
                s = str(x)[:10]
                if s <= today_ymd:
                    values.append(s)
            if values:
                return max(values).replace("-", "")
    except Exception as e:
        print(f"trade calendar failed: {e}")
    return latest_weekday(today)


def board_limit(code: str, name: str) -> Tuple[str, float]:
    code, name = ss(code).zfill(6), ss(name).upper()
    if "ST" in name:
        return "ST", 5.0
    if code.startswith(("688", "689")):
        return "科创板", 20.0
    if code.startswith(("300", "301")):
        return "创业板", 20.0
    if code.startswith(("920", "8", "4")):
        return "北交所", 30.0
    if code.startswith("002"):
        return "中小板", 10.0
    return "主板", 10.0


def is_limit_up(pct: float, limit_pct: float) -> bool:
    if limit_pct <= 5:
        return pct >= 4.75
    if limit_pct <= 10:
        return pct >= 9.65
    if limit_pct <= 20:
        return pct >= 19.2
    return pct >= 28.8


def send_tg(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram missing; skip")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text[:3900], "disable_web_page_preview": True}
        r = requests.post(url, json=payload, timeout=30)
        print("Telegram status:", r.status_code)
        print("Telegram body:", r.text[:500])
    except Exception as e:
        print("telegram failed:", e)


def normalize_pool(df: pd.DataFrame, source: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    code_col = first_col(df, ["代码", "股票代码"])
    name_col = first_col(df, ["名称", "股票简称"])
    pct_col = first_col(df, ["涨跌幅", "涨幅"])
    price_col = first_col(df, ["最新价", "收盘价"])
    if not code_col or not name_col:
        return pd.DataFrame()
    out = pd.DataFrame()
    out["code"] = df[code_col].astype(str).str.extract(r"(\d+)", expand=False).fillna("").str.zfill(6)
    out["name"] = df[name_col].astype(str)
    out["pct_chg"] = df[pct_col].apply(sf) if pct_col else 0.0
    out["close"] = df[price_col].apply(sf) if price_col else 0.0
    out["source"] = source
    out = out[out["code"].str.len() == 6]
    return out


def safe_source_call(fn_name: str, *args, **kwargs) -> pd.DataFrame:
    try:
        fn = getattr(ak, fn_name)
    except Exception:
        print(f"source not available: {fn_name}")
        return pd.DataFrame()
    try:
        with timeout_guard(AK_TIMEOUT_SECONDS, fn_name):
            return fn(*args, **kwargs)
    except TypeError:
        try:
            with timeout_guard(AK_TIMEOUT_SECONDS, fn_name):
                return fn()
        except Exception as e:
            print(f"{fn_name} failed: {e}")
            return pd.DataFrame()
    except Exception as e:
        print(f"{fn_name} failed: {e}")
        return pd.DataFrame()


def fetch_raw_pools(target_date: str) -> Tuple[pd.DataFrame, Dict[str, int]]:
    parts: List[pd.DataFrame] = []
    source_counts: Dict[str, int] = {}
    source_defs = [
        ("zt_pool", "stock_zt_pool_em", {"date": target_date}),
        ("zt_st_pool", "stock_zt_pool_st_em", {"date": target_date}),
        ("zt_previous_pool", "stock_zt_pool_previous_em", {"date": target_date}),
        ("a_spot", "stock_zh_a_spot_em", {}),
        ("bj_spot", "stock_bj_a_spot_em", {}),
    ]
    for source, fn_name, kwargs in source_defs:
        raw = safe_source_call(fn_name, **kwargs)
        norm = normalize_pool(raw, source)
        source_counts[source] = 0 if norm is None or norm.empty else len(norm)
        if norm is not None and not norm.empty:
            parts.append(norm)
    if not parts:
        return pd.DataFrame(columns=["code", "name", "pct_chg", "close", "source"]), source_counts
    raw_pool = pd.concat(parts, ignore_index=True)
    raw_pool["source_rank"] = raw_pool["source"].map({"zt_pool": 1, "zt_st_pool": 2, "zt_previous_pool": 3, "bj_spot": 4, "a_spot": 5}).fillna(9)
    raw_pool = raw_pool.sort_values(["source_rank", "pct_chg"], ascending=[True, False])
    raw_pool = raw_pool.drop_duplicates("code", keep="first").drop(columns=["source_rank"]).reset_index(drop=True)
    return raw_pool, source_counts


def fetch_limit_pool(target_date: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    raw_pool, source_counts = fetch_raw_pools(target_date)
    if raw_pool.empty:
        return pd.DataFrame(columns=["code", "name", "pct_chg", "close", "board", "limit_pct"]), {"source_counts": source_counts}
    boards = raw_pool.apply(lambda r: board_limit(r["code"], r["name"]), axis=1)
    raw_pool["board"] = [x[0] for x in boards]
    raw_pool["limit_pct"] = [x[1] for x in boards]
    raw_pool["is_limit_up"] = raw_pool.apply(lambda r: is_limit_up(sf(r["pct_chg"]), sf(r["limit_pct"])), axis=1)
    limit_pool = raw_pool[raw_pool["is_limit_up"]].copy()
    limit_pool = limit_pool.sort_values(["limit_pct", "pct_chg", "code"], ascending=[False, False, True]).reset_index(drop=True)
    diagnostics = {
        "source_counts": source_counts,
        "source_limit_counts": limit_pool["source"].value_counts().to_dict() if not limit_pool.empty else {},
        "board_counts": limit_pool["board"].value_counts().to_dict() if not limit_pool.empty else {},
        "total_limit_up_identified": int(len(limit_pool)),
        "max_deep_analyze": int(MAX_DEEP_ANALYZE),
        "not_deep_analyzed_count": int(max(0, len(limit_pool) - MAX_DEEP_ANALYZE)),
        "not_deep_analyzed": limit_pool.iloc[MAX_DEEP_ANALYZE:][["code", "name", "pct_chg", "board", "source"]].to_dict("records") if len(limit_pool) > MAX_DEEP_ANALYZE else [],
    }
    return limit_pool, diagnostics


def fetch_hist(code: str, target_date: str) -> pd.DataFrame:
    try:
        with timeout_guard(AK_TIMEOUT_SECONDS, f"hist-{code}"):
            raw = ak.stock_zh_a_hist(symbol=code, period="daily", start_date="20180101", end_date=target_date, adjust="qfq")
    except Exception as e:
        print(f"hist failed {code}:", e)
        return pd.DataFrame()
    if raw is None or raw.empty:
        return pd.DataFrame()
    mp = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount", "涨跌幅": "pct_chg", "换手率": "turnover"}
    df = raw.rename(columns={k: v for k, v in mp.items() if k in raw.columns})
    for c in ["open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"]:
        if c in df.columns:
            df[c] = df[c].apply(sf)
    if "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)
    return df


def add_ma(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for p in MA_PERIODS:
        df[f"ma{p}"] = df["close"].rolling(p).mean()
    return df


def ma_report(df: pd.DataFrame) -> Dict[str, Any]:
    if len(df) < 2:
        return {}
    last, prev = df.iloc[-1], df.iloc[-2]
    close, prev_close = sf(last["close"]), sf(prev["close"])
    out = {}
    for p in MA_PERIODS:
        ma, pma = sf(last.get(f"ma{p}")), sf(prev.get(f"ma{p}"))
        if ma <= 0:
            out[f"ma{p}"] = {"available": False}
            continue
        out[f"ma{p}"] = {
            "available": True,
            "value": round(ma, 3),
            "distance_pct": round((close / ma - 1) * 100, 2),
            "above": close >= ma,
            "reclaimed_today": bool(close >= ma and pma > 0 and prev_close < pma),
            "slope_pct": round((ma / pma - 1) * 100, 3) if pma > 0 else 0.0,
        }
    return out


def prev_high(df: pd.DataFrame, n: int) -> float:
    sub = df.iloc[max(0, len(df) - n - 1):len(df) - 1] if len(df) > 1 else pd.DataFrame()
    return sf(sub["high"].max()) if not sub.empty else 0.0


def prev_low(df: pd.DataFrame, n: int) -> float:
    sub = df.iloc[max(0, len(df) - n - 1):len(df) - 1] if len(df) > 1 else pd.DataFrame()
    return sf(sub["low"].min()) if not sub.empty else 0.0


def volume_report(df: pd.DataFrame) -> Dict[str, Any]:
    vol = sf(df.iloc[-1].get("volume"))
    out = {"today_volume": vol}
    for p in [5, 20, 60]:
        avg = sf(df.iloc[-p-1:-1]["volume"].mean()) if len(df) > p else 0.0
        out[f"vol_ratio_{p}"] = round(vol / avg, 2) if avg > 0 else 0.0
    return out


def candle_report(df: pd.DataFrame) -> Dict[str, Any]:
    last = df.iloc[-1]
    op, hi, lo, cl = sf(last.get("open")), sf(last.get("high")), sf(last.get("low")), sf(last.get("close"))
    rng = max(hi - lo, 1e-6)
    body = abs(cl - op)
    upper = max(hi - max(op, cl), 0.0)
    lower = max(min(op, cl) - lo, 0.0)
    return {
        "entity_pct": round((cl / op - 1) * 100, 2) if op > 0 else 0.0,
        "body_ratio": round(body / rng, 3),
        "close_position": round((cl - lo) / rng, 3),
        "upper_shadow_ratio": round(upper / rng, 3),
        "lower_shadow_ratio": round(lower / rng, 3),
    }


def position_report(df: pd.DataFrame, ma: Dict[str, Any]) -> Dict[str, Any]:
    close = sf(df.iloc[-1]["close"])
    h250, l250 = prev_high(df, 250), prev_low(df, 250)
    h100, l100 = prev_high(df, 100), prev_low(df, 100)
    pos250 = round((close - l250) / (h250 - l250), 3) if h250 > l250 > 0 else None
    pos100 = round((close - l100) / (h100 - l100), 3) if h100 > l100 > 0 else None
    dist_ma5 = sf(ma.get("ma5", {}).get("distance_pct"))
    dist_ma10 = sf(ma.get("ma10", {}).get("distance_pct"))
    return {
        "pos_250d": pos250,
        "pos_100d": pos100,
        "dist_ma5_pct": dist_ma5,
        "dist_ma10_pct": dist_ma10,
        "is_low_mid_position": bool((pos250 is not None and pos250 <= 0.65) or (pos100 is not None and pos100 <= 0.65)),
        "overheat": bool(dist_ma5 >= 18 or dist_ma10 >= 28 or (pos250 is not None and pos250 >= 0.92)),
    }


def consecutive_limit_up(df: pd.DataFrame, code: str, name: str) -> int:
    _, limit_pct = board_limit(code, name)
    c = 0
    for _, r in df.iloc[::-1].iterrows():
        if is_limit_up(sf(r.get("pct_chg")), limit_pct):
            c += 1
        else:
            break
    return c


def structure_tags(df: pd.DataFrame, ma: Dict[str, Any]) -> List[str]:
    close = sf(df.iloc[-1]["close"])
    tags = []
    for n in [20, 60, 100, 250]:
        h = prev_high(df, n)
        if h > 0 and close >= h:
            tags.append(f"突破{n}日左侧高点")
    for p in MA_PERIODS:
        if ma.get(f"ma{p}", {}).get("reclaimed_today"):
            tags.append(f"涨停收复MA{p}")
    if ma.get("ma250", {}).get("reclaimed_today"):
        tags.append("年线修复涨停")
    if ma.get("ma100", {}).get("reclaimed_today"):
        tags.append("100日线修复涨停")
    if all(ma.get(f"ma{p}", {}).get("above") for p in [5, 10, 20, 30, 60] if ma.get(f"ma{p}", {}).get("available")):
        tags.append("站上短中期均线群")
    return list(dict.fromkeys(tags))


def score_attribution(df: pd.DataFrame, code: str, name: str, board: str, ma: Dict[str, Any], vol: Dict[str, Any], candle: Dict[str, Any], pos: Dict[str, Any], tags: List[str], czt: int) -> Dict[str, Any]:
    score = 0.0
    add, deduct, risk = [], [], []

    if czt >= 3:
        score += 16; add.append(f"{czt}连板，市场辨识度高")
    elif czt == 2:
        score += 11; add.append("二连板，强度延续")
    else:
        score += 4; add.append("首板样本，需看左侧结构质量")

    if any("250日" in t or "年线" in t for t in tags):
        score += 18; add.append("大周期压力/年线相关突破或修复")
    if any("100日" in t for t in tags):
        score += 12; add.append("100日周期修复或突破")
    if any("60日" in t for t in tags):
        score += 8; add.append("60日结构突破")
    if any("20日" in t for t in tags):
        score += 5; add.append("短周期平台/前高突破")
    if len(tags) >= 3:
        score += 8; add.append("多结构共振")

    if pos.get("is_low_mid_position"):
        score += 10; add.append("位置处于低位/中低位，更像启动或修复")
    else:
        deduct.append("位置不低，需警惕高位加速")

    vr20, vr60 = sf(vol.get("vol_ratio_20")), sf(vol.get("vol_ratio_60"))
    if 1.6 <= vr20 <= 4.5:
        score += 14; add.append(f"20日量比{vr20}，放量健康")
    elif 0 < vr20 < 1.1:
        score += 4; add.append(f"20日量比{vr20}，可能是快速板/惜售板，需看承接")
    elif vr20 > 6:
        score -= 8; deduct.append(f"20日量比{vr20}过大，可能分歧过热")
    if 1.2 <= vr60 <= 5.5:
        score += 6; add.append(f"60日量比{vr60}支持资金活跃")

    if candle.get("close_position", 0) >= 0.9 and candle.get("upper_shadow_ratio", 1) <= 0.12:
        score += 10; add.append("收盘强、上影短，封板/攻击质量较好")
    elif candle.get("upper_shadow_ratio", 0) >= 0.35:
        score -= 10; deduct.append("上影较长，涨停质量/封板稳定性存疑")
    if candle.get("body_ratio", 0) >= 0.55:
        score += 6; add.append("实体占比高，K线攻击效率好")

    if board == "北交所":
        risk.append("北交所30cm波动大，归因需单独看流动性和次日承接")
        if vr20 <= 0 or sf(vol.get("today_volume")) <= 0:
            score -= 8; deduct.append("北交所样本量能数据不足，降级观察")
    if "ST" in name.upper() or board == "ST":
        score -= 12; deduct.append("ST涨停按特殊风险样本处理")
        risk.append("ST样本不能与普通涨停同权重")

    if pos.get("overheat"):
        score -= 18; deduct.append("远离MA5/MA10或接近长期区间高位，追高风险大")
        risk.append("高位过热，不能直接转化为战法正样本")

    if score >= 75:
        grade = "S"
        label = "高质量强势样本"
    elif score >= 60:
        grade = "A"
        label = "较高质量强势样本"
    elif score >= 43:
        grade = "B"
        label = "有效涨停样本"
    elif score >= 25:
        grade = "C"
        label = "普通涨停样本"
    else:
        grade = "D"
        label = "噪音/高风险涨停样本"

    return {"score": round(score, 1), "grade": grade, "grade_label": label, "add_reasons": add, "deduct_reasons": deduct, "risk_flags": risk}


def analyze_stock(row: pd.Series, target_date: str) -> Dict[str, Any]:
    code, name = ss(row.get("code")), ss(row.get("name"))
    board, limit_pct = board_limit(code, name)
    df = fetch_hist(code, target_date)
    time.sleep(REQUEST_SLEEP)
    if df.empty or len(df) < 30:
        return {"code": code, "name": name, "board": board, "source": ss(row.get("source")), "pct_chg": sf(row.get("pct_chg")), "error": "历史K线不足"}
    df = add_ma(df)
    ma = ma_report(df)
    vol = volume_report(df)
    candle = candle_report(df)
    pos = position_report(df, ma)
    tags = structure_tags(df, ma)
    czt = consecutive_limit_up(df, code, name)
    score_pack = score_attribution(df, code, name, board, ma, vol, candle, pos, tags, czt)
    last = df.iloc[-1]
    reasons = score_pack["add_reasons"][:5]
    return {
        "code": code, "name": name, "board": board, "source": ss(row.get("source")), "limit_pct": limit_pct,
        "close": round(sf(last.get("close")), 3), "pct_chg": round(sf(last.get("pct_chg")), 2),
        "consecutive_limit_up": czt, "grade": score_pack["grade"], "grade_label": score_pack["grade_label"], "attribution_score": score_pack["score"],
        "ma_lines": ma, "volume": vol, "candle": candle, "position": pos, "tags": tags,
        "add_reasons": score_pack["add_reasons"], "deduct_reasons": score_pack["deduct_reasons"], "risk_flags": score_pack["risk_flags"], "reasons": reasons,
    }


def build_outputs(target_date: str, full_pool: pd.DataFrame, analyzed_pool: pd.DataFrame, results: List[Dict[str, Any]], diagnostics: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    valid = [r for r in results if not r.get("error")]
    grade_count, board_count, tag_count = {}, {}, {}
    for r in valid:
        grade_count[r.get("grade", "NA")] = grade_count.get(r.get("grade", "NA"), 0) + 1
        board_count[r.get("board", "未知")] = board_count.get(r.get("board", "未知"), 0) + 1
        for t in r.get("tags", []):
            tag_count[t] = tag_count.get(t, 0) + 1
    top_tags = sorted(tag_count.items(), key=lambda x: x[1], reverse=True)[:10]
    top = sorted(valid, key=lambda x: (-sf(x.get("attribution_score")), -x.get("consecutive_limit_up", 0)))[:8]
    missed = diagnostics.get("not_deep_analyzed", [])
    lines = [
        "🧬【五号员工-涨停板归因】",
        f"日期：{target_date}",
        f"涨停总识别：{len(full_pool)}只",
        f"已深度归因：{len(analyzed_pool)}只",
        f"未深度归因：{len(missed)}只",
        f"板块分布：{json.dumps(diagnostics.get('board_counts', {}), ensure_ascii=False)}",
        f"来源分布：{json.dumps(diagnostics.get('source_limit_counts', {}), ensure_ascii=False)}",
        f"等级分布：{json.dumps(grade_count, ensure_ascii=False)}",
    ]
    if top_tags:
        lines.append("\n高频强势结构：")
        for tag, cnt in top_tags[:5]:
            lines.append(f"- {tag}：{cnt}只")
    lines.append("\n重点样本：")
    for r in top:
        plus = "；".join(r.get("add_reasons", [])[:3])
        minus = "；".join(r.get("deduct_reasons", [])[:2])
        lines.append(f"{r['name']}({r['code']}) {r['grade']}级/{r.get('attribution_score')}分 {r['consecutive_limit_up']}连板：{plus}" + (f"｜扣分：{minus}" if minus else ""))
    if missed:
        lines.append("\n未深度归因名单：")
        for x in missed[:20]:
            lines.append(f"- {x.get('name')}({x.get('code')}) {x.get('pct_chg')}% {x.get('board')} {x.get('source')}")
    data = {
        "target_date": target_date,
        "limit_up_count_total": len(full_pool),
        "deep_analyzed_count": len(analyzed_pool),
        "not_deep_analyzed_count": len(missed),
        "diagnostics": diagnostics,
        "full_limit_pool": full_pool[["code", "name", "pct_chg", "board", "limit_pct", "source"]].to_dict("records"),
        "grade_count": grade_count,
        "board_count": board_count,
        "top_tags": top_tags,
        "results": results,
    }
    return "\n".join(lines), data


def main() -> None:
    target_date = latest_trade_date()
    print(f"employee5 target date: {target_date}")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    full_pool, diagnostics = fetch_limit_pool(target_date)
    if full_pool.empty:
        msg = f"🧬【五号员工-涨停板归因】\n日期：{target_date}\n未识别到涨停样本。"
        (REPORT_DIR / "limit_up_research_report.md").write_text(msg, encoding="utf-8")
        (REPORT_DIR / "limit_up_research_report.json").write_text(json.dumps({"target_date": target_date, "limit_up_count_total": 0, "results": [], "diagnostics": diagnostics}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(msg)
        send_tg(msg)
        return
    analyzed_pool = full_pool.head(MAX_DEEP_ANALYZE).copy()
    results = []
    for i, (_, row) in enumerate(analyzed_pool.iterrows(), 1):
        print(f"分析 {i}/{len(analyzed_pool)}：{row.get('name')}({row.get('code')})")
        try:
            results.append(analyze_stock(row, target_date))
        except Exception as e:
            results.append({"code": row.get("code"), "name": row.get("name"), "source": row.get("source"), "pct_chg": row.get("pct_chg"), "board": row.get("board"), "error": str(e)})
    text, data = build_outputs(target_date, full_pool, analyzed_pool, results, diagnostics)
    (REPORT_DIR / "limit_up_research_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "limit_up_research_report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "limit_up_missing_or_not_deep_analyzed.json").write_text(json.dumps(diagnostics.get("not_deep_analyzed", []), ensure_ascii=False, indent=2), encoding="utf-8")
    print(text)
    send_tg(text)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        err = "❌ 五号员工运行失败\n" + str(e) + "\n" + traceback.format_exc()
        print(err)
        send_tg(err)
        raise
