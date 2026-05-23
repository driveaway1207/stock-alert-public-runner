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
MAX_POOL_SCAN = int(os.getenv("EMPLOYEE5_MAX_STOCKS", "300"))
DEEP_SAMPLE_COUNT = int(os.getenv("EMPLOYEE5_DEEP_SAMPLE_COUNT", "3"))
AK_TIMEOUT_SECONDS = int(os.getenv("EMPLOYEE5_AK_TIMEOUT_SECONDS", "18"))
REQUEST_SLEEP = float(os.getenv("EMPLOYEE5_REQUEST_SLEEP", "0.12"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
MA_PERIODS = [5, 10, 20, 30, 60, 100, 250]
RET_WINDOWS = [20, 30, 60, 100]


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
            values = [str(x)[:10] for x in df["trade_date"].tolist() if str(x)[:10] <= today_ymd]
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


def split_text(text: str, limit: int = 3500) -> List[str]:
    chunks, buf = [], ""
    for line in text.splitlines():
        if len(buf) + len(line) + 1 > limit:
            if buf:
                chunks.append(buf)
            buf = line
        else:
            buf = line if not buf else buf + "\n" + line
    if buf:
        chunks.append(buf)
    return chunks or [text[:limit]]


def send_tg(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram missing; skip")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for i, chunk in enumerate(split_text(text), 1):
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "disable_web_page_preview": True}
        r = requests.post(url, json=payload, timeout=30)
        print(f"Telegram chunk {i} status:", r.status_code, r.text[:200])
        time.sleep(0.35)


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
    return out[out["code"].str.len() == 6]


def safe_source_call(fn_name: str, **kwargs) -> pd.DataFrame:
    try:
        fn = getattr(ak, fn_name)
    except Exception:
        print(f"source not available: {fn_name}")
        return pd.DataFrame()
    try:
        with timeout_guard(AK_TIMEOUT_SECONDS, fn_name):
            return fn(**kwargs)
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


def fetch_limit_pool(target_date: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    source_defs = [
        ("zt_pool", "stock_zt_pool_em", {"date": target_date}),
        ("zt_st_pool", "stock_zt_pool_st_em", {"date": target_date}),
        ("zt_previous_pool", "stock_zt_pool_previous_em", {"date": target_date}),
        ("a_spot", "stock_zh_a_spot_em", {}),
        ("bj_spot", "stock_bj_a_spot_em", {}),
    ]
    parts, source_counts = [], {}
    for source, fn_name, kwargs in source_defs:
        raw = safe_source_call(fn_name, **kwargs)
        norm = normalize_pool(raw, source)
        source_counts[source] = int(len(norm)) if norm is not None and not norm.empty else 0
        if norm is not None and not norm.empty:
            parts.append(norm)
    if not parts:
        return pd.DataFrame(), {"source_counts": source_counts}
    raw_pool = pd.concat(parts, ignore_index=True)
    raw_pool["source_rank"] = raw_pool["source"].map({"zt_pool": 1, "zt_st_pool": 2, "zt_previous_pool": 3, "bj_spot": 4, "a_spot": 5}).fillna(9)
    raw_pool = raw_pool.sort_values(["source_rank", "pct_chg"], ascending=[True, False]).drop_duplicates("code", keep="first").drop(columns=["source_rank"])
    boards = raw_pool.apply(lambda r: board_limit(r["code"], r["name"]), axis=1)
    raw_pool["board"] = [x[0] for x in boards]
    raw_pool["limit_pct"] = [x[1] for x in boards]
    raw_pool["is_limit_up"] = raw_pool.apply(lambda r: is_limit_up(sf(r["pct_chg"]), sf(r["limit_pct"])), axis=1)
    pool = raw_pool[raw_pool["is_limit_up"]].sort_values(["limit_pct", "pct_chg", "code"], ascending=[False, False, True]).reset_index(drop=True)
    diagnostics = {
        "source_counts": source_counts,
        "source_limit_counts": pool["source"].value_counts().to_dict() if not pool.empty else {},
        "board_counts": pool["board"].value_counts().to_dict() if not pool.empty else {},
        "total_limit_up_identified": int(len(pool)),
    }
    return pool, diagnostics


def fetch_hist(code: str, target_date: str) -> pd.DataFrame:
    try:
        with timeout_guard(AK_TIMEOUT_SECONDS, f"hist-{code}"):
            raw = ak.stock_zh_a_hist(symbol=str(code).zfill(6), period="daily", start_date="20180101", end_date=target_date, adjust="qfq")
    except Exception as e:
        print(f"hist failed {code}: {e}")
        return pd.DataFrame()
    if raw is None or raw.empty:
        return pd.DataFrame()
    mp = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount", "涨跌幅": "pct_chg", "换手率": "turnover"}
    df = raw.rename(columns={k: v for k, v in mp.items() if k in raw.columns})
    for c in ["open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"]:
        if c in df.columns:
            df[c] = df[c].apply(sf)
    return df.sort_values("date").reset_index(drop=True) if "date" in df.columns else df.reset_index(drop=True)


def add_ma(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for p in MA_PERIODS:
        df[f"ma{p}"] = df["close"].rolling(p).mean()
    return df


def ret_pct(df: pd.DataFrame, n: int) -> float:
    if len(df) <= n:
        return 0.0
    base = sf(df.iloc[-n-1]["close"])
    cur = sf(df.iloc[-1]["close"])
    return round((cur / base - 1) * 100, 2) if base > 0 else 0.0


def prev_high(df: pd.DataFrame, n: int) -> float:
    sub = df.iloc[max(0, len(df)-n-1):len(df)-1]
    return sf(sub["high"].max()) if not sub.empty else 0.0


def prev_low(df: pd.DataFrame, n: int) -> float:
    sub = df.iloc[max(0, len(df)-n-1):len(df)-1]
    return sf(sub["low"].min()) if not sub.empty else 0.0


def avg_vol(df: pd.DataFrame, n: int) -> float:
    if len(df) <= n or "volume" not in df.columns:
        return 0.0
    return sf(df.iloc[-n-1:-1]["volume"].mean())


def vol_ratio(df: pd.DataFrame, n: int) -> float:
    v = sf(df.iloc[-1].get("volume")) if not df.empty else 0.0
    av = avg_vol(df, n)
    return round(v / av, 2) if av > 0 else 0.0


def range_position(df: pd.DataFrame, n: int) -> Optional[float]:
    h, l, c = prev_high(df, n), prev_low(df, n), sf(df.iloc[-1]["close"])
    return round((c - l) / (h - l), 3) if h > l > 0 else None


def candle_metrics(df: pd.DataFrame) -> Dict[str, float]:
    r = df.iloc[-1]
    op, hi, lo, cl = sf(r.get("open")), sf(r.get("high")), sf(r.get("low")), sf(r.get("close"))
    rng = max(hi - lo, 1e-6)
    return {
        "body_ratio": round(abs(cl - op) / rng, 3),
        "close_pos": round((cl - lo) / rng, 3),
        "upper_ratio": round(max(hi - max(op, cl), 0) / rng, 3),
        "entity_pct": round((cl / op - 1) * 100, 2) if op > 0 else 0.0,
    }


def structure_tags(df: pd.DataFrame) -> List[str]:
    tags: List[str] = []
    c = sf(df.iloc[-1]["close"])
    for n in [20, 60, 100, 250]:
        h = prev_high(df, n)
        if h > 0 and c >= h:
            tags.append(f"突破{n}日左侧高点")
    for p in MA_PERIODS:
        ma = sf(df.iloc[-1].get(f"ma{p}"))
        pma = sf(df.iloc[-2].get(f"ma{p}")) if len(df) > 1 else 0
        pc = sf(df.iloc[-2].get("close")) if len(df) > 1 else 0
        if ma > 0 and c >= ma and pc < pma:
            tags.append(f"涨停收复MA{p}")
    if range_position(df, 250) is not None and range_position(df, 250) <= 0.35:
        tags.append("低位区间启动")
    elif range_position(df, 250) is not None and range_position(df, 250) >= 0.85:
        tags.append("高位加速")
    return list(dict.fromkeys(tags))


def classify_simple(row: pd.Series, hist: pd.DataFrame) -> List[str]:
    if hist.empty or len(hist) < 30:
        return ["历史K线不足"]
    hist = add_ma(hist)
    tags = structure_tags(hist)
    r20, r60 = ret_pct(hist, 20), ret_pct(hist, 60)
    vr20 = vol_ratio(hist, 20)
    pos250 = range_position(hist, 250)
    if r20 >= 50:
        tags.append("20日涨幅超50%")
    if r60 >= 100:
        tags.append("60日涨幅超100%")
    if 1.6 <= vr20 <= 4.5:
        tags.append("健康放量涨停")
    elif vr20 > 6:
        tags.append("爆量分歧涨停")
    elif 0 < vr20 < 1.1:
        tags.append("缩量快速板")
    if pos250 is not None and pos250 >= 0.9:
        tags.append("长期区间高位")
    return tags or ["普通涨停"]


def sample_score(hist: pd.DataFrame) -> float:
    if hist.empty or len(hist) < 30:
        return -999
    r20, r30, r60, r100 = [ret_pct(hist, n) for n in RET_WINDOWS]
    vr20 = vol_ratio(hist, 20)
    tags = structure_tags(add_ma(hist))
    return max(r20, 0) * 1.6 + max(r30, 0) * 1.2 + max(r60, 0) * 0.9 + max(r100, 0) * 0.6 + len(tags) * 8 + min(max(vr20, 0), 8) * 3


def deep_observations(code: str, name: str, board: str, hist: pd.DataFrame) -> List[str]:
    df = add_ma(hist.copy())
    cm = candle_metrics(df)
    r20, r30, r60, r100 = [ret_pct(df, n) for n in RET_WINDOWS]
    vr5, vr20, vr60 = vol_ratio(df, 5), vol_ratio(df, 20), vol_ratio(df, 60)
    pos20, pos60, pos100, pos250 = [range_position(df, n) for n in [20, 60, 100, 250]]
    close = sf(df.iloc[-1]["close"])
    obs = []
    def add(cat: str, text: str):
        obs.append(f"【{cat}】{text}")
    add("大周期", f"250日区间分位={pos250}，用于判断是低位启动、中位修复还是高位加速样本。")
    add("大周期", f"100日区间分位={pos100}，观察中长期筹码修复程度。")
    add("大周期", f"60日区间分位={pos60}，观察季度级别主升/修复位置。")
    add("大周期", f"20日区间分位={pos20}，观察近似月线窗口是否已经处于极强状态。")
    for n in [20, 60, 100, 250]:
        h = prev_high(df, n)
        add("核心线", f"当前收盘{close:.2f}相对{n}日左侧高点{h:.2f}，判断是否突破该周期核心压力。")
    add("涨幅路径", f"20/30/60/100日涨幅分别为{r20}%/{r30}%/{r60}%/{r100}%，用于识别周期性大涨和井喷程度。")
    add("历史筹码", f"近250日最大成交量约{sf(df.tail(250)['volume'].max()) if 'volume' in df.columns else 0:.0f}，用于定位历史筹码交换记忆。")
    add("历史筹码", f"近60日最大成交量约{sf(df.tail(60)['volume'].max()) if 'volume' in df.columns else 0:.0f}，观察右侧资金活跃峰值。")
    tags = structure_tags(df)
    add("形态结构", f"当前结构标签：{'、'.join(tags) if tags else '普通涨停/待细分'}。")
    add("形态结构", "若左侧存在双峰/双肩/平台上沿，当前涨停需记录是否突破该固定压力线。")
    add("趋势结构", f"MA5距离={round((close/sf(df.iloc[-1].get('ma5'))-1)*100,2) if sf(df.iloc[-1].get('ma5'))>0 else 0}% ，判断短线加速程度。")
    add("趋势结构", f"MA20距离={round((close/sf(df.iloc[-1].get('ma20'))-1)*100,2) if sf(df.iloc[-1].get('ma20'))>0 else 0}% ，判断是否脱离月线中枢。")
    add("趋势结构", f"MA60距离={round((close/sf(df.iloc[-1].get('ma60'))-1)*100,2) if sf(df.iloc[-1].get('ma60'))>0 else 0}% ，判断季度修复/主升强度。")
    add("洗盘", f"近20日最低点{prev_low(df,20):.2f}，用于观察涨停前是否经历回撤洗盘。")
    add("洗盘", f"近60日最低点{prev_low(df,60):.2f}，用于观察中周期洗盘底部。")
    add("波动率", f"近20日日均振幅约{round(((df.tail(20)['high']-df.tail(20)['low'])/df.tail(20)['close']).mean()*100,2) if len(df)>=20 else 0}% ，判断爆发前是否压缩。")
    add("量能", f"5/20/60日量比分别为{vr5}/{vr20}/{vr60}，观察短中期量能放大质量。")
    add("量能", f"今日成交量{sf(df.iloc[-1].get('volume')):.0f}，与历史高量区比较，判断是否为资金再激活。")
    add("量能", "若20日量比在1.6-4.5之间，偏健康放量；若大于6，偏分歧爆量。")
    add("量能", "若涨停前多日量能由乱转平，属于爆发前夜重要观察点。")
    add("试盘", "需检查前期是否存在长上影/冲高回落试盘，当前涨停是否越过前次失败高点。")
    add("试盘", "若前次试盘失败后缩量回落，再次涨停突破，属于供应吸收后的再确认样本。")
    add("供应吸收", "左侧前高/平台/肩部区域若被多次攻击，说明供应可能已被逐步消耗。")
    add("供应吸收", "若涨停收盘站上前高密集区，记录为压力转支撑的候选样本。")
    add("当前触发", f"今日实体涨幅={cm['entity_pct']}%，实体占振幅比例={cm['body_ratio']}。")
    add("当前触发", f"今日收盘位置={cm['close_pos']}，上影线比例={cm['upper_ratio']}，用于判断涨停质量。")
    add("K线质量", "收盘位置越接近1且上影越短，涨停攻击效率越好；反之可能是烂板或分歧板。")
    add("位置", f"当前所属板块={board}，不同涨停幅度制度下不能同权比较。")
    add("位置", "若远离MA5/MA10过大，归因为高位情绪样本，不能直接沉淀为买点。")
    add("板块市场", "该样本需结合当日板块涨停扩散、连板高度和市场风险偏好再做后验验证。")
    add("风险反证", "若次日长上影或放量跌回涨停触发位，说明该结构可能只是情绪噪音。")
    add("风险反证", "若北交所/ST样本，需单独标记高波动或特殊制度风险。")
    add("后续验证", "T+1观察是否站稳涨停触发位。")
    add("后续验证", "T+3观察是否不跌回核心结构线。")
    add("后续验证", "T+5/T+8观察是否形成二次承接或板块扩散。")
    add("后续验证", "T+13/T+20验证该结构是否真正具备持续性，而不是单日脉冲。")
    return obs[:40]


def build_report(target_date: str, pool: pd.DataFrame, diagnostics: Dict[str, Any], enriched: List[Dict[str, Any]], deep: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    type_map: Dict[str, List[str]] = {}
    for item in enriched:
        for t in item.get("tags", []):
            type_map.setdefault(t, []).append(f"{item['name']}({item['code']})")
    lines = [
        "🧬【五号员工-涨停板归因】",
        f"日期：{target_date}",
        f"涨停总识别：{len(pool)}只",
        f"板块分布：{json.dumps(diagnostics.get('board_counts', {}), ensure_ascii=False)}",
        f"来源分布：{json.dumps(diagnostics.get('source_limit_counts', {}), ensure_ascii=False)}",
        "",
        "一、全市场涨停类型统计：",
    ]
    for tag, names in sorted(type_map.items(), key=lambda x: len(x[1]), reverse=True)[:12]:
        lines.append(f"- {tag}：{len(names)}只；{ '、'.join(names[:12]) }")
    lines.append("")
    lines.append("二、今日3只周期性大涨深度样本：")
    for i, item in enumerate(deep, 1):
        lines.append(f"\n【深度样本{i}】{item['name']}({item['code']}) {item['board']}")
        lines.append(f"20/30/60/100日涨幅：{item['returns']}")
        lines.append("30+跨维度归因观察：")
        for j, obs in enumerate(item.get("observations", []), 1):
            lines.append(f"{j}. {obs}")
    data = {"target_date": target_date, "diagnostics": diagnostics, "summary_types": type_map, "enriched": enriched, "deep_samples": deep}
    return "\n".join(lines), data


def main() -> None:
    target_date = latest_trade_date()
    print(f"employee5 target date: {target_date}")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    pool, diagnostics = fetch_limit_pool(target_date)
    if pool.empty:
        msg = f"🧬【五号员工-涨停板归因】\n日期：{target_date}\n未识别到涨停样本。"
        send_tg(msg)
        return
    pool = pool.head(MAX_POOL_SCAN).copy()
    enriched: List[Dict[str, Any]] = []
    candidates: List[Tuple[float, Dict[str, Any], pd.DataFrame]] = []
    for idx, row in pool.iterrows():
        code, name, board = ss(row.get("code")), ss(row.get("name")), ss(row.get("board"))
        hist = fetch_hist(code, target_date)
        time.sleep(REQUEST_SLEEP)
        if not hist.empty and len(hist) >= 30:
            tags = classify_simple(row, hist)
            sc = sample_score(hist)
            returns = {f"{n}d": ret_pct(hist, n) for n in RET_WINDOWS}
            item = {"code": code, "name": name, "board": board, "pct_chg": sf(row.get("pct_chg")), "tags": tags, "returns": returns}
            candidates.append((sc, item, hist))
        else:
            item = {"code": code, "name": name, "board": board, "pct_chg": sf(row.get("pct_chg")), "tags": ["历史K线不足"], "returns": {}}
        enriched.append(item)
    deep: List[Dict[str, Any]] = []
    for _, item, hist in sorted(candidates, key=lambda x: x[0], reverse=True)[:DEEP_SAMPLE_COUNT]:
        item = dict(item)
        item["observations"] = deep_observations(item["code"], item["name"], item["board"], hist)
        deep.append(item)
    text, data = build_report(target_date, pool, diagnostics, enriched, deep)
    (REPORT_DIR / "limit_up_research_report.md").write_text(text, encoding="utf-8")
    (REPORT_DIR / "limit_up_research_report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPORT_DIR / "limit_up_deep_samples.json").write_text(json.dumps(deep, ensure_ascii=False, indent=2), encoding="utf-8")
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
