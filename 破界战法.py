# -*- coding: utf-8 -*-
"""
破界战法2.0｜核心压力线突破/临界战法

定位：独立战法员工，不改一号员工主模型。
核心思想：
1）先找“多方法论共振核心线”，不是普通压力线；
2）再看突破前左侧蓄势：阶段对阶段量能/成交额中枢抬升、价格重心抬升、突破前夕平量稳定；
3）最后看日线高级别突破K、空间/RR、风险过滤。

默认复用仓库现有一号员工的数据入口：get_a_stock_list / get_daily_kline / send_telegram。
因此本文件可以单独放在 GitHub 仓库根目录运行，不篡改原 stock_alert.py。
"""

import os
import sys
import json
import math
import time
import argparse
import glob
import importlib.util
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
import pandas as pd


MODEL_VERSION = "破界战法2.0｜专业核心压力线版｜多周期核心线+宽严双层选股"
DEFAULT_BASE_MODEL_FILE = os.environ.get("破界_基础模型文件", os.environ.get("POJIE_BASE_MODEL_FILE", "stock_alert.py"))
OUTPUT_DIR = os.environ.get("破界_输出目录", os.environ.get("POJIE_OUTPUT_DIR", "outputs/pojie"))


# ========================= 速度 / 日志控制 =========================
# 全量跑通后默认并行扫描缓存；如果今天没有缓存，需要 BaoStock 补拉，程序会自动回落为顺序补拉。
POJIE_WORKERS = max(1, int(os.environ.get("POJIE_WORKERS", "10")))
POJIE_PARALLEL_MIN_STOCKS = int(os.environ.get("POJIE_PARALLEL_MIN_STOCKS", "300"))
POJIE_PROGRESS_EVERY = int(os.environ.get("POJIE_PROGRESS_EVERY", "200"))
POJIE_PROGRESS_SECONDS = int(os.environ.get("POJIE_PROGRESS_SECONDS", "30"))
POJIE_VERBOSE_KLINE = os.environ.get("POJIE_VERBOSE_KLINE", "0") == "1"
POJIE_LOG_FIRST_N = int(os.environ.get("POJIE_LOG_FIRST_N", "5"))
# 2.0 默认使用 BALANCED：核心线更重“共振/临界”，筛选不再只盯强突破当天。
POJIE_STRATEGY_PROFILE = os.environ.get("POJIE_STRATEGY_PROFILE", "balanced").strip().lower()
POJIE_FAST_PREFILTER = os.environ.get("POJIE_FAST_PREFILTER", "1") == "1"
POJIE_MIN_CORELINE_SCORE = float(os.environ.get("POJIE_MIN_CORELINE_SCORE", "58" if POJIE_STRATEGY_PROFILE != "strict" else "68"))
POJIE_OUTPUT_OBSERVATION = os.environ.get("POJIE_OUTPUT_OBSERVATION", "1") == "1"

KLINE_STATS = {
    "cache_hit": 0,
    "cache_miss": 0,
    "remote_fetch": 0,
    "remote_success": 0,
    "remote_fail": 0,
    "cache_read_error": 0,
    "prefilter_skip": 0,
}


def fmt_duration(seconds: float) -> str:
    try:
        seconds = int(max(0, seconds))
    except Exception:
        seconds = 0
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s2 = seconds % 60
    if h > 0:
        return f"{h}小时{m}分{s2}秒"
    if m > 0:
        return f"{m}分{s2}秒"
    return f"{s2}秒"


def progress_text(done: int, total: int, start_ts: float, results: int, no_kline: int, failed: int) -> str:
    elapsed = time.time() - start_ts
    speed = done / elapsed if elapsed > 0 else 0
    eta = (total - done) / speed if speed > 0 and total and done < total else 0
    pct_done = done / total * 100 if total else 0
    valid = max(0, done - no_kline - failed)
    return (
        f"破界进度：{done}/{total} ({pct_done:.1f}%) | "
        f"K线有效={valid} 候选={results} 无K线={no_kline} 失败={failed} | "
        f"缓存命中={KLINE_STATS.get('cache_hit', 0)} 跳过无触发={KLINE_STATS.get('prefilter_skip', 0)} "
        f"补拉成功={KLINE_STATS.get('remote_success', 0)} | "
        f"耗时={fmt_duration(elapsed)} 剩余≈{fmt_duration(eta)}"
    )


def safe_float(x, default=0.0) -> float:
    try:
        if x is None:
            return default
        if isinstance(x, float) and math.isnan(x):
            return default
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def pct(a, b, default=0.0) -> float:
    a = safe_float(a)
    b = safe_float(b)
    if b == 0:
        return default
    return a / b - 1


def now_bj() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")



def force_kline_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    最后一层防御：任何进入扫描/重采样/评分函数的数据，都强制具备 date/open/high/low/close/volume/amount。
    这不是重新拉数据，只是字段兜底，防止某些缓存列名异常导致 KeyError('volume')。
    """
    if df is None:
        return pd.DataFrame()
    d = df.copy()
    d.columns = [str(c).strip().replace("\ufeff", "") for c in d.columns]
    # 再做一次轻量列名归一，避免部分函数拿到未标准化切片。
    colmap = {}
    for c in d.columns:
        x = str(c).strip().replace("\ufeff", "")
        xl = x.lower().strip()
        if x in ["日期", "交易日期"] or xl in ["date", "trade_date", "datetime", "time"]:
            colmap[c] = "date"
        elif x in ["开盘", "开盘价"] or xl == "open":
            colmap[c] = "open"
        elif x in ["最高", "最高价"] or xl == "high":
            colmap[c] = "high"
        elif x in ["最低", "最低价"] or xl == "low":
            colmap[c] = "low"
        elif x in ["收盘", "收盘价"] or xl == "close":
            colmap[c] = "close"
        elif x in ["成交量", "成交量(手)", "成交量(股)", "成交量(万手)"] or xl in ["volume", "vol", "volumn"]:
            colmap[c] = "volume"
        elif x in ["成交额", "成交额(元)", "成交额(万元)"] or xl in ["amount", "value", "turnover_value"]:
            colmap[c] = "amount"
        elif x in ["涨跌幅"] or xl in ["pctchg", "pct_chg"]:
            colmap[c] = "pct_chg"
    if colmap:
        d = d.rename(columns=colmap)
    # 合并重复列。
    if len(set(d.columns)) != len(d.columns):
        merged = pd.DataFrame(index=d.index)
        for col in dict.fromkeys(list(d.columns)):
            same = d.loc[:, d.columns == col]
            merged[col] = same.bfill(axis=1).iloc[:, 0] if same.shape[1] > 1 else same.iloc[:, 0]
        d = merged
    # 必要列兜底。
    for c in ["open", "high", "low", "close"]:
        if c not in d.columns:
            d[c] = np.nan
        d[c] = pd.to_numeric(d[c], errors="coerce")
    if "date" not in d.columns:
        d["date"] = pd.date_range(end=pd.Timestamp.today().normalize(), periods=len(d)).astype(str)
    if "amount" in d.columns:
        d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
    if "volume" in d.columns:
        d["volume"] = pd.to_numeric(d["volume"], errors="coerce")
    if "volume" not in d.columns or d["volume"].isna().all():
        if "amount" in d.columns and not d["amount"].isna().all():
            d["volume"] = d["amount"] / d["close"].replace(0, np.nan)
        else:
            d["volume"] = 0.0
    if "amount" not in d.columns or d["amount"].isna().all():
        d["amount"] = pd.to_numeric(d["volume"], errors="coerce").fillna(0) * pd.to_numeric(d["close"], errors="coerce").fillna(0)
    d["volume"] = pd.to_numeric(d["volume"], errors="coerce").fillna(0.0)
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce").fillna(0.0)
    if "pct_chg" not in d.columns:
        d["pct_chg"] = pd.to_numeric(d["close"], errors="coerce").pct_change().fillna(0) * 100
    else:
        d["pct_chg"] = pd.to_numeric(d["pct_chg"], errors="coerce").fillna(0)
    return d

def load_base_module(path: str):
    """动态导入现有一号员工文件，只复用数据入口和Telegram，不改原文件。"""
    candidates = []
    if path:
        candidates.append(path)
    candidates.extend(["stock_alert.py", "stock_alert_v25_6_hvn_dedup.py", "stock_alert_v25_5.py"])
    selected = None
    for p in candidates:
        if os.path.exists(p):
            selected = p
            break
    if selected is None:
        raise FileNotFoundError(f"找不到基础模型文件，已尝试：{candidates}")
    spec = importlib.util.spec_from_file_location("base_stock_model_for_pojie", selected)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    print(f"破界：已加载基础模型文件 {selected}")
    return mod


def normalize_kline(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    统一K线字段的唯一入口。

    这版把 volume 问题彻底放到最底层解决：
    - 兼容一号员工缓存：kline_cache/base/sh_600000.csv、kline_cache/000001.csv；
    - 兼容中文字段：日期/开盘/最高/最低/收盘/成交量/成交额；
    - 兼容英文字段：date/open/high/low/close/volume/vol/amount/value；
    - 缺 volume 但有 amount+close：用 amount/close 反推；
    - 缺 amount 但有 volume+close：用 volume*close 反推；
    - 返回前强制包含 volume，避免后面任何模块再报 KeyError('volume')。
    """
    if df is None or getattr(df, "empty", True):
        return None

    d = df.copy()
    d.columns = [str(c).strip().replace("\ufeff", "") for c in d.columns]

    def _norm_col(c: str) -> str:
        x = str(c).strip().replace("\ufeff", "")
        xl = x.lower().strip()
        mapping = {
            "日期": "date", "交易日期": "date", "trade_date": "date", "datetime": "date", "time": "date", "date": "date",
            "开盘": "open", "开盘价": "open", "open": "open",
            "最高": "high", "最高价": "high", "high": "high",
            "最低": "low", "最低价": "low", "low": "low",
            "收盘": "close", "收盘价": "close", "close": "close",
            "成交量": "volume", "成交量(手)": "volume", "成交量(股)": "volume", "成交量(万手)": "volume",
            "volume": "volume", "vol": "volume", "volumn": "volume",
            "成交额": "amount", "成交额(元)": "amount", "成交额(万元)": "amount",
            "amount": "amount", "value": "amount", "turnover_value": "amount",
            "涨跌幅": "pct_chg", "pctchg": "pct_chg", "pct_chg": "pct_chg",
            "换手率": "turnover_rate", "turn": "turnover_rate", "turnover": "turnover_rate",
        }
        return mapping.get(x, mapping.get(xl, xl))

    d = d.rename(columns={c: _norm_col(c) for c in d.columns})

    # 处理重名列，例如同时存在 vol 与 volume。
    if len(set(d.columns)) != len(d.columns):
        merged = pd.DataFrame(index=d.index)
        for col in dict.fromkeys(list(d.columns)):
            same = d.loc[:, d.columns == col]
            if same.shape[1] == 1:
                merged[col] = same.iloc[:, 0]
            else:
                merged[col] = same.bfill(axis=1).iloc[:, 0]
        d = merged

    # OHLC/date 必须存在；volume/amount 可以互相反推。
    required_price = ["date", "open", "high", "low", "close"]
    if not all(c in d.columns for c in required_price):
        return None

    d["date"] = pd.to_datetime(d["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")

    if "volume" not in d.columns:
        if "amount" in d.columns:
            d["volume"] = d["amount"] / d["close"].replace(0, np.nan)
        else:
            d["volume"] = 0.0

    if "amount" not in d.columns:
        d["amount"] = d["volume"] * d["close"]

    d["volume"] = pd.to_numeric(d["volume"], errors="coerce").fillna(0.0)
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
    d["amount"] = d["amount"].fillna(d["volume"] * pd.to_numeric(d["close"], errors="coerce"))

    d = d[["date", "open", "high", "low", "close", "volume", "amount"]].copy()
    d = d.dropna(subset=["date", "open", "high", "low", "close"])
    d = d[(d["open"] > 0) & (d["high"] > 0) & (d["low"] > 0) & (d["close"] > 0)]
    d = d.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if len(d) < 120:
        return None

    d["preclose"] = d["close"].shift(1)
    d["pct_chg"] = d["close"].pct_change().fillna(0) * 100
    d["body"] = d["close"] - d["open"]
    d["body_abs"] = d["body"].abs()
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    d["close_pos"] = ((d["close"] - d["low"]) / rng).fillna(0.5)
    d["upper_shadow_ratio"] = ((d["high"] - d[["open", "close"]].max(axis=1)) / rng).fillna(0)
    d["lower_shadow_ratio"] = ((d[["open", "close"]].min(axis=1) - d["low"]) / rng).fillna(0)
    d["body_top"] = d[["open", "close"]].max(axis=1)
    d["body_bottom"] = d[["open", "close"]].min(axis=1)
    d["body_mid"] = (d["body_top"] + d["body_bottom"]) / 2
    d["ma20"] = d["close"].rolling(20).mean()
    d["ma60"] = d["close"].rolling(60).mean()
    d["ma120"] = d["close"].rolling(120).mean()
    d["vol_ma20"] = d["volume"].rolling(20).mean()
    d["vr1"] = d["volume"] / d["volume"].shift(1).replace(0, np.nan)
    d["atr20_pct"] = ((d["high"] - d["low"]).rolling(20).mean() / d["close"].replace(0, np.nan)).fillna(0)
    return d

def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    d = force_kline_schema(df)
    if d is None or d.empty or "date" not in d.columns:
        return pd.DataFrame()
    d = d.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["date", "open", "high", "low", "close"])
    if d.empty:
        return pd.DataFrame()
    d = d.set_index("date").sort_index()
    out = pd.DataFrame()
    out["open"] = d["open"].resample(rule).first()
    out["high"] = d["high"].resample(rule).max()
    out["low"] = d["low"].resample(rule).min()
    out["close"] = d["close"].resample(rule).last()
    out["volume"] = d["volume"].resample(rule).sum()
    out["amount"] = d["amount"].resample(rule).sum()
    out = out.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return normalize_kline(out) if len(out) >= 20 else force_kline_schema(out)



def detect_resonance_coreline_candidates(d: pd.DataFrame, timeframe: str) -> List[Dict[str, Any]]:
    """
    破界2.0核心线重校准：
    不只依赖单根语义K线，还加入“共振点最多的核心线”：
    - 阶段高点/次高点
    - 收盘价/实体顶密集区
    - 上影线反压密集区
    - 近似成交密集区（按百分比价格桶）
    这一步解决“核心压力线没选准”的问题。
    """
    res: List[Dict[str, Any]] = []
    if d is None or len(d) < 40:
        return res
    d = force_kline_schema(d).reset_index(drop=True)
    cur = safe_float(d["close"].iloc[-1])
    if cur <= 0:
        return res
    # 周期越大，回看越长；日线只拿近一年左右，避免远古线干扰。
    lookback_map = {"D": 260, "W": 180, "M": 120, "Q": 80, "Y": 40}
    dd = d.tail(lookback_map.get(timeframe, 180)).copy().reset_index(drop=True)
    if len(dd) < 30:
        return res
    # 只保留与当前有交易价值的线：不是离当前太远的历史线。
    low_bound = cur * 0.68
    high_bound = cur * 1.22
    date_last = str(pd.to_datetime(dd["date"].iloc[-1]).date())

    # 1）阶段高点/次高点/滚动高点：压力线核心来源。
    for n, w in [(20, 1.4), (40, 1.8), (60, 2.0), (120, 2.4), (250, 2.8)]:
        if len(dd) >= min(n, 30):
            seg = dd.tail(min(n, len(dd)))
            h = safe_float(seg["high"].max())
            if low_bound <= h <= high_bound:
                res.append({"price": h, "source": f"近{n}周期阶段高点/压力上沿", "timeframe": timeframe, "date": date_last, "weight": w})
            # 次高收盘/实体顶比单纯影线更稳。
            body_top = seg[["open", "close"]].max(axis=1)
            bt = safe_float(body_top.quantile(0.92))
            if low_bound <= bt <= high_bound:
                res.append({"price": bt, "source": f"近{n}周期实体顶/收盘共振线", "timeframe": timeframe, "date": date_last, "weight": w + 0.4})
            ch = safe_float(seg["close"].quantile(0.94))
            if low_bound <= ch <= high_bound:
                res.append({"price": ch, "source": f"近{n}周期高位收盘共振线", "timeframe": timeframe, "date": date_last, "weight": w + 0.2})

    # 2）价格桶共振：简化版 Volume Profile / Price-by-Volume。
    # 用百分比桶，避免低价/高价股票固定金额失真。
    prices = []
    weights = []
    for i, row in dd.iterrows():
        decay = 0.55 + 0.45 * (i + 1) / max(1, len(dd))
        v = max(1.0, safe_float(row.get("amount"), 0) / 1e8)
        o, h, l, c = [safe_float(row.get(x)) for x in ["open", "high", "low", "close"]]
        bt, bb = max(o, c), min(o, c)
        cp = safe_float(row.get("close_pos"), 0.5)
        upper = safe_float(row.get("upper_shadow_ratio"), 0)
        for px, wt, src in [
            (c, 1.8, "收盘"),
            (bt, 1.5, "实体顶"),
            (h, 1.1 + (0.6 if upper >= 0.25 else 0), "影线高点"),
            (bb, 0.9, "实体底"),
        ]:
            if low_bound <= px <= high_bound:
                prices.append(px)
                weights.append(max(0.1, wt * decay * min(3.0, v ** 0.35)))
    if prices:
        bin_pct = 0.006 if timeframe in ["D", "W"] else 0.010
        buckets: Dict[int, Dict[str, float]] = {}
        for px, wt in zip(prices, weights):
            key = int(round(math.log(px / cur) / bin_pct))
            b = buckets.setdefault(key, {"w": 0.0, "pxw": 0.0, "cnt": 0})
            b["w"] += wt
            b["pxw"] += px * wt
            b["cnt"] += 1
        top = sorted(buckets.values(), key=lambda x: (x["w"], x["cnt"]), reverse=True)[:8]
        for b in top:
            if b["w"] <= 0 or b["cnt"] < 4:
                continue
            px = b["pxw"] / b["w"]
            if low_bound <= px <= high_bound:
                res.append({"price": px, "source": f"价格桶成交/实体/收盘共振区 cnt={int(b['cnt'])}", "timeframe": timeframe, "date": date_last, "weight": min(3.2, 1.2 + b["cnt"] / 10)})
    return [x for x in res if safe_float(x.get("price")) > 0]

def detect_gap_candidates(d: pd.DataFrame, timeframe: str) -> List[Dict[str, Any]]:
    res = []
    for i in range(1, len(d)):
        pre_high = safe_float(d.loc[i - 1, "high"])
        pre_low = safe_float(d.loc[i - 1, "low"])
        low = safe_float(d.loc[i, "low"])
        high = safe_float(d.loc[i, "high"])
        date = str(pd.to_datetime(d.loc[i, "date"]).date())
        if low > pre_high * 1.003:
            res.append({"price": pre_high, "source": "向上跳空缺口下沿/前高", "timeframe": timeframe, "date": date, "weight": 2.4})
            res.append({"price": low, "source": "向上跳空缺口上沿/当日低", "timeframe": timeframe, "date": date, "weight": 2.0})
        if high < pre_low * 0.997:
            res.append({"price": pre_low, "source": "向下跳空缺口上沿/前低", "timeframe": timeframe, "date": date, "weight": 2.4})
            res.append({"price": high, "source": "向下跳空缺口下沿/当日高", "timeframe": timeframe, "date": date, "weight": 2.0})
    return res


def is_downtrend(d: pd.DataFrame, i: int, n: int = 5) -> bool:
    if i < n:
        return False
    seg = d.iloc[i - n:i]
    if seg.empty:
        return False
    return safe_float(seg["close"].iloc[-1]) < safe_float(seg["close"].iloc[0]) * 0.96 and int((seg["close"] < seg["open"]).sum()) >= max(2, n // 2)


def detect_semantic_coreline_candidates(d: pd.DataFrame, timeframe: str) -> List[Dict[str, Any]]:
    """核心：不是普通影线/实体共振，而是结构语义候选线。"""
    res = []
    if d is None or len(d) < 25:
        return res
    d = force_kline_schema(d).reset_index(drop=True)
    if "volume" not in d.columns:
        d["volume"] = 0.0
    vol_ma = d["volume"].rolling(10).mean()
    for i in range(6, len(d)):
        row = d.iloc[i]
        date = str(pd.to_datetime(row["date"]).date())
        o, h, l, c, v = [safe_float(row[x]) for x in ["open", "high", "low", "close", "volume"]]
        if c <= 0:
            continue
        body_pct = abs(c - o) / c
        rng_pct = (h - l) / c if c else 0
        vol_ratio = v / safe_float(vol_ma.iloc[i], v) if safe_float(vol_ma.iloc[i], 0) > 0 else 1
        up = c > o
        down = c < o

        # 1）连续下跌中的小阳线：下跌中继/弱修复成本线。
        if up and is_downtrend(d, i, 5) and body_pct <= 0.055 and rng_pct <= 0.12:
            res.append({"price": min(o, c), "source": "下跌中继小阳线实底", "timeframe": timeframe, "date": date, "weight": 2.2})
            res.append({"price": max(o, c), "source": "下跌中继小阳线实顶", "timeframe": timeframe, "date": date, "weight": 1.8})
            if l < min(o, c):
                res.append({"price": l, "source": "下跌中继小阳线虚底", "timeframe": timeframe, "date": date, "weight": 1.4})

        # 2）大阴线后高开阳线：修复资金实底/虚底。
        pre = d.iloc[i - 1]
        pre_o, pre_c = safe_float(pre["open"]), safe_float(pre["close"])
        pre_body_drop = (pre_o - pre_c) / pre_o if pre_o > 0 else 0
        if pre_body_drop >= 0.06 and up and o > pre_c * 1.003:
            res.append({"price": min(o, c), "source": "大阴后高开阳线实底", "timeframe": timeframe, "date": date, "weight": 2.8})
            res.append({"price": l, "source": "大阴后高开阳线虚底", "timeframe": timeframe, "date": date, "weight": 2.2})

        # 3）连续下跌后反抽最高点/上影线供应。
        if is_downtrend(d, i, 8) and safe_float(row.get("upper_shadow_ratio", 0)) >= 0.28:
            res.append({"price": h, "source": "连续下跌后反抽最高点/上影线", "timeframe": timeframe, "date": date, "weight": 2.6})
        if safe_float(row.get("upper_shadow_ratio", 0)) >= 0.38 and vol_ratio >= 1.1:
            res.append({"price": h, "source": "放量上影线反压", "timeframe": timeframe, "date": date, "weight": 2.0})
        if safe_float(row.get("lower_shadow_ratio", 0)) >= 0.38 and vol_ratio >= 1.1:
            res.append({"price": l, "source": "放量下影线承接", "timeframe": timeframe, "date": date, "weight": 1.8})

        # 4）大阳/大阴实体顶底：大换手供需边界。
        if body_pct >= 0.065 or vol_ratio >= 1.8:
            if up:
                res.append({"price": max(o, c), "source": "大阳线实顶", "timeframe": timeframe, "date": date, "weight": 1.8})
                res.append({"price": min(o, c), "source": "大阳线实底", "timeframe": timeframe, "date": date, "weight": 1.8})
            elif down:
                res.append({"price": max(o, c), "source": "大阴线实顶", "timeframe": timeframe, "date": date, "weight": 2.2})
                res.append({"price": min(o, c), "source": "大阴线实底", "timeframe": timeframe, "date": date, "weight": 2.4})

        # 5）最大量/次大量K线关键位。
        if i >= 30:
            rank = d["volume"].iloc[max(0, i - 120):i + 1].rank(pct=True).iloc[-1]
            if rank >= 0.96 and up and body_pct >= 0.025:
                res.append({"price": h, "source": "阶段最大量阳K高点", "timeframe": timeframe, "date": date, "weight": 2.6})
                res.append({"price": min(o, c), "source": "阶段最大量阳K实底", "timeframe": timeframe, "date": date, "weight": 2.4})
    res.extend(detect_gap_candidates(d, timeframe))
    return [x for x in res if safe_float(x.get("price")) > 0]


def cluster_corelines(candidates: List[Dict[str, Any]], current_price: float, atr_pct: float) -> List[Dict[str, Any]]:
    if not candidates:
        return []
    tol_pct = max(0.006, min(0.025, atr_pct * 0.45 if atr_pct > 0 else 0.010))
    cands = sorted(candidates, key=lambda x: safe_float(x["price"]))
    clusters = []
    current = []
    for c in cands:
        price = safe_float(c["price"])
        if not current:
            current.append(c)
            continue
        center = np.average([safe_float(x["price"]) for x in current], weights=[safe_float(x.get("weight", 1)) for x in current])
        if abs(price / center - 1) <= tol_pct:
            current.append(c)
        else:
            clusters.append(current)
            current = [c]
    if current:
        clusters.append(current)

    zones = []
    tf_weight = {"Y": 5.0, "Q": 4.0, "M": 3.2, "W": 2.2, "D": 1.0}
    semantic_keys = ["下跌中继", "大阴后", "反抽", "缺口", "大阳", "大阴", "上影", "下影", "最大量"]
    for mem in clusters:
        prices = [safe_float(x["price"]) for x in mem]
        weights = [safe_float(x.get("weight", 1)) * tf_weight.get(str(x.get("timeframe", "D")), 1.0) for x in mem]
        center = float(np.average(prices, weights=weights))
        low = min(prices)
        high = max(prices)
        sources = [str(x.get("source", "")) for x in mem]
        tfs = sorted(set(str(x.get("timeframe", "")) for x in mem))
        semantic_count = sum(1 for k in semantic_keys if any(k in s for s in sources))
        source_types = len(set(sources))
        reaction_count = len(mem)
        time_span = 0
        try:
            dates = pd.to_datetime([x.get("date") for x in mem], errors="coerce").dropna()
            if len(dates) >= 2:
                time_span = int((dates.max() - dates.min()).days)
        except Exception:
            time_span = 0
        multi_tf_score = min(25, sum(tf_weight.get(tf, 1) for tf in tfs) * 3.2)
        semantic_score = min(28, semantic_count * 4.5 + source_types * 1.4)
        reaction_score = min(22, reaction_count * 2.0)
        gap_bonus = 7 if any("缺口" in s for s in sources) else 0
        bigk_bonus = 5 if any(("大阳" in s or "大阴" in s or "最大量" in s) for s in sources) else 0
        stability_score = min(10, time_span / 90) if time_span else 0
        dist = abs(center / current_price - 1) if current_price > 0 else 99
        usability = 5 if 0.01 <= dist <= 0.35 else (2 if dist < 0.5 else -4)
        score = multi_tf_score + semantic_score + reaction_score + gap_bonus + bigk_bonus + stability_score + usability
        score = max(0, min(100, score))
        # 2.0：核心线分级略放宽，但仍要求多来源共振；严选可用 POJIE_STRATEGY_PROFILE=strict。
        if score >= (84 if POJIE_STRATEGY_PROFILE == "strict" else 80):
            level = "S"
        elif score >= (74 if POJIE_STRATEGY_PROFILE == "strict" else 68):
            level = "A"
        elif score >= (62 if POJIE_STRATEGY_PROFILE == "strict" else 55):
            level = "B"
        else:
            level = "C"
        zones.append({
            "center": center, "low": low, "high": high,
            "score": float(score), "level": level,
            "members": mem, "sources": sources[:18], "timeframes": tfs,
            "reaction_count": reaction_count, "source_types": source_types,
            "semantic_count": semantic_count, "time_span_days": time_span,
            "distance_to_current": center / current_price - 1 if current_price > 0 else 0,
        })
    return sorted(zones, key=lambda z: z["score"], reverse=True)


def build_timeframes(daily: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    # 年线/季线样本少时仍可输出，但评分会自然降权。
    daily = force_kline_schema(daily)
    return {
        "D": daily.tail(260).reset_index(drop=True),
        "W": resample_ohlcv(daily, "W-FRI"),
        "M": resample_ohlcv(daily, "ME"),
        "Q": resample_ohlcv(daily, "QE"),
        "Y": resample_ohlcv(daily, "YE"),
    }


def find_coreline_zones(daily: pd.DataFrame) -> List[Dict[str, Any]]:
    daily = force_kline_schema(daily)
    tfs = build_timeframes(daily)
    candidates: List[Dict[str, Any]] = []
    for tf, df in tfs.items():
        if df is None or len(df) < 15:
            continue
        lookback = {"D": 220, "W": 180, "M": 120, "Q": 80, "Y": 40}.get(tf, 100)
        window_df = df.tail(lookback).reset_index(drop=True)
        candidates.extend(detect_semantic_coreline_candidates(window_df, tf))
        candidates.extend(detect_resonance_coreline_candidates(window_df, tf))
    cur = safe_float(daily["close"].iloc[-1])
    atr = safe_float(daily["atr20_pct"].iloc[-1], 0.02)
    return cluster_corelines(candidates, cur, atr)


def segment_metrics(seg: pd.DataFrame) -> Dict[str, float]:
    if seg is None or len(seg) < 5:
        return {"price_center": 0, "low_center": 0, "vol_mean": 0, "amount_mean": 0, "vol_cv": 9, "atr_pct": 9, "down_big": 9, "flat_ratio": 0}
    s = force_kline_schema(seg)
    if "volume" not in s.columns:
        s["volume"] = 0.0
    if "amount" not in s.columns:
        s["amount"] = s["volume"] * s["close"]
    vol = pd.to_numeric(s["volume"], errors="coerce").replace(0, np.nan)
    amount = pd.to_numeric(s["amount"], errors="coerce").replace(0, np.nan)
    price_center = safe_float(s["close"].median())
    low_center = safe_float(s["low"].median())
    vol_mean = safe_float(vol.mean())
    amount_mean = safe_float(amount.mean())
    vol_cv = safe_float(vol.std() / vol_mean, 9) if vol_mean > 0 else 9
    atr_pct = safe_float(((s["high"] - s["low"]) / s["close"].replace(0, np.nan)).mean(), 9)
    down_big = int(((s["close"] < s["open"]) & (s["pct_chg"] <= -3.5) & (s["volume"] > s["volume"].rolling(10).mean() * 1.3)).sum())
    vr = vol / vol.shift(1)
    flat_ratio = safe_float(((vr >= 0.92) & (vr <= 1.08)).mean(), 0)
    return {"price_center": price_center, "low_center": low_center, "vol_mean": vol_mean, "amount_mean": amount_mean, "vol_cv": vol_cv, "atr_pct": atr_pct, "down_big": down_big, "flat_ratio": flat_ratio}


def score_left_buildup(d: pd.DataFrame, coreline: Dict[str, Any]) -> Dict[str, Any]:
    """突破前蓄势：阶段对阶段抬升 + 阶段内平量稳定。"""
    d = force_kline_schema(d)
    if d is None or len(d) < 90:
        return {"score": 0.0, "level": "不足", "reasons": ["样本不足"]}
    current_price = safe_float(d["close"].iloc[-1])
    line = safe_float(coreline["center"])
    # 只看突破前约90日，拆成旧平台/中平台/临界平台三段。
    pre = d.iloc[-91:-1].copy() if len(d) >= 91 else d.iloc[:-1].copy()
    if len(pre) < 45:
        return {"score": 0.0, "level": "不足", "reasons": ["突破前样本不足"]}
    parts = np.array_split(pre, 3)
    m1, m2, m3 = [segment_metrics(pd.DataFrame(x)) for x in parts]
    score = 0.0
    reasons = []

    # 阶段对阶段：量能/成交额中枢抬升，不是今天比昨天。
    if m2["vol_mean"] > m1["vol_mean"] * 1.03 and m3["vol_mean"] > m2["vol_mean"] * 0.98:
        score += 5
        reasons.append("阶段量能中枢较前一阶段温和抬升")
    if m2["amount_mean"] > m1["amount_mean"] * 1.05 and m3["amount_mean"] > m2["amount_mean"] * 0.98:
        score += 5
        reasons.append("阶段成交额中枢抬升，资金成本上移")
    if m2["price_center"] > m1["price_center"] * 1.01 and m3["price_center"] > m2["price_center"] * 1.005:
        score += 6
        reasons.append("价格收盘中枢阶段性抬升")
    if m2["low_center"] > m1["low_center"] * 1.005 and m3["low_center"] > m2["low_center"] * 1.002:
        score += 4
        reasons.append("阶段低点/回撤重心逐步抬高")

    # 爆发前夕：日与日之间平量、量能稳定，不是天天爆量。
    if m3["vol_cv"] < m2["vol_cv"] * 0.88 or m3["vol_cv"] <= 0.32:
        score += 5
        reasons.append("突破前夕量能波动率下降，平稳度提高")
    if m3["flat_ratio"] >= 0.38:
        score += 4
        reasons.append("临界平台平量比例较高，日间量能稳定")
    if m3["atr_pct"] < m2["atr_pct"] * 0.88 or m3["atr_pct"] <= 0.035:
        score += 4
        reasons.append("突破前价格波动压缩")
    if m3["down_big"] <= 1:
        score += 3
        reasons.append("临界平台放量长阴少，供应压力受控")

    # 核心线下方吸收：靠近但不被打下来。
    near = pre.tail(30)
    if line > 0 and not near.empty:
        close_near_ratio = safe_float(((near["close"] >= line * 0.90) & (near["close"] <= line * 1.015)).mean())
        fail_drop = int(((near["high"] >= line * 0.985) & (near["close"] < line * 0.94)).sum())
        if close_near_ratio >= 0.35:
            score += 4
            reasons.append("核心线下方/附近停留充分，有吸收过程")
        if fail_drop <= 2:
            score += 2
            reasons.append("多次靠近核心线后未出现明显大跌回落")

    # 攻击记忆：7%大阳、倍量、跳空等，但要避免高位乱炸。
    pre60 = d.tail(60)
    big7 = int(((pre60["pct_chg"] >= 7) & (pre60["close"] > pre60["open"]) & (pre60["close_pos"] >= 0.60)).sum())
    standard_vol = int(((pre60["vr1"] >= 1.8) & (pre60["vr1"] <= 2.5) & (pre60["close"] > pre60["open"])).sum())
    up_gap = int((pre60["low"] > pre60["high"].shift(1) * 1.003).sum())
    if big7 >= 1:
        score += 2
        reasons.append(f"近60日有{big7}次7%以上大阳攻击记忆")
    if standard_vol >= 1:
        score += 2
        reasons.append(f"近60日有{standard_vol}次标准倍量阳线")
    if up_gap >= 1:
        score += 1
        reasons.append(f"近60日有{up_gap}次向上跳空攻击记忆")

    # 防止死量横盘误判为好平量。
    if m3["vol_mean"] < m1["vol_mean"] * 0.72 and m3["amount_mean"] < m1["amount_mean"] * 0.75:
        score -= 7
        reasons.append("临界平台量能/成交额中枢明显下降，疑似冷门死量")
    if current_price > line * 1.12:
        score -= 4
        reasons.append("当前已明显远离核心线，突破后追高风险上升")

    score = max(0.0, min(35.0, score))
    level = "强" if score >= 27 else ("较好" if score >= 21 else ("一般" if score >= 14 else "弱"))
    return {"score": float(score), "level": level, "reasons": reasons[:10], "stage_metrics": {"旧平台": m1, "中平台": m2, "临界平台": m3}}


def detect_breakout(d: pd.DataFrame, zone: Dict[str, Any]) -> Dict[str, Any]:
    d = force_kline_schema(d)
    row = d.iloc[-1]
    pre = d.iloc[-2] if len(d) >= 2 else row
    close, open_, high, low = [safe_float(row[x]) for x in ["close", "open", "high", "low"]]
    zhigh = safe_float(zone["high"])
    zcenter = safe_float(zone["center"])
    zlow = safe_float(zone["low"])
    body_top = max(open_, close)
    body_bottom = min(open_, close)
    body_above_ratio = 0.0
    if body_top > body_bottom and zhigh > 0:
        body_above_ratio = max(0.0, min(1.0, (body_top - max(body_bottom, zhigh)) / (body_top - body_bottom)))
    close_pos = safe_float(row.get("close_pos", 0.5))
    upper = safe_float(row.get("upper_shadow_ratio", 0))
    vr1 = safe_float(row.get("vr1", 0))
    pct_chg = safe_float(row.get("pct_chg", 0))
    volr = safe_float(row["volume"] / safe_float(row.get("vol_ma20", 0), row["volume"]), 1)
    gap_break = safe_float(row["low"]) > safe_float(pre["high"]) * 1.003 and close > zhigh
    if POJIE_STRATEGY_PROFILE == "strict":
        effective = close > zhigh * 1.003 and body_above_ratio >= 0.45 and close_pos >= 0.68 and upper <= 0.42
    else:
        # 2.0 平衡模式：允许“刚突破/临界突破”进入观察，不再只认非常漂亮的大阳突破。
        effective = close > zhigh * 1.001 and body_above_ratio >= 0.28 and close_pos >= 0.58 and upper <= 0.55
    score = 0.0
    reasons = []
    if close > zhigh * 1.003:
        score += 8; reasons.append("收盘有效站上核心线高沿")
    if body_above_ratio >= 0.55:
        score += 7; reasons.append("实体大部分位于核心线上方")
    elif body_above_ratio >= 0.35:
        score += 4; reasons.append("实体部分站上核心线")
    if close_pos >= 0.80:
        score += 5; reasons.append("收盘位置强")
    elif close_pos >= 0.68:
        score += 3; reasons.append("收盘位置尚可")
    if upper <= 0.25:
        score += 4; reasons.append("上影线短，非明显冲高回落")
    elif upper > 0.50:
        score -= 6; reasons.append("上影线较长，存在假突破风险")
    if 1.8 <= vr1 <= 2.5:
        score += 6; reasons.append("标准倍量突破")
    elif 1.3 <= vr1 <= 3.2 or 1.2 <= volr <= 3.8:
        score += 4; reasons.append("健康放量突破")
    elif vr1 > 5 or volr > 6:
        score -= 5; reasons.append("极端爆量，需防分歧派发")
    if gap_break:
        score += 4; reasons.append("跳空越过核心线")
    if pct_chg >= 3 and close > open_:
        score += 3; reasons.append("突破K为有效阳线")
    if close > zcenter and close <= zhigh * 1.003:
        reasons.append("进入核心线区域但未完全突破高沿")
    if high >= zhigh and close < zcenter:
        score -= 8; reasons.append("盘中冲线但收盘回落，假突破风险")
    score = max(0.0, min(30.0, score))
    return {"effective": bool(effective), "score": float(score), "reasons": reasons[:10], "body_above_ratio": body_above_ratio, "close_pos": close_pos, "vr1": vr1, "volr": volr}


def calc_space_rr(d: pd.DataFrame, zone: Dict[str, Any], all_zones: List[Dict[str, Any]]) -> Dict[str, Any]:
    close = safe_float(d["close"].iloc[-1])
    zlow = safe_float(zone["low"])
    zhigh = safe_float(zone["high"])
    if close <= 0:
        return {"score": 0, "rr": 0, "defense": 0, "next_pressure": 0, "space": 0, "reasons": []}
    defense_candidates = [zlow * 0.985, safe_float(d["body_bottom"].iloc[-1]) * 0.99, safe_float(d["low"].tail(10).min()) * 0.995]
    defense = max([x for x in defense_candidates if x > 0], default=zlow * 0.985)
    risk = max(0.001, close / defense - 1) if defense > 0 and close > defense else 0.08
    upper_zones = [z for z in all_zones if safe_float(z["center"]) > close * 1.03]
    next_pressure = min([safe_float(z["center"]) for z in upper_zones], default=0.0)
    if next_pressure <= 0:
        # 用近250日高点作为保守空间代理。
        next_pressure = safe_float(d["high"].tail(250).max())
        if next_pressure <= close * 1.03:
            next_pressure = close * 1.18
    space = max(0.0, next_pressure / close - 1)
    rr = space / risk if risk > 0 else 0
    score = 0.0
    reasons = []
    if space >= 0.18:
        score += 4; reasons.append("上方空间较大")
    elif space >= 0.10:
        score += 2; reasons.append("上方仍有一定空间")
    else:
        score -= 3; reasons.append("上方空间偏小")
    if rr >= 2.5:
        score += 5; reasons.append("风险收益比优秀")
    elif rr >= 1.8:
        score += 3; reasons.append("风险收益比可接受")
    else:
        score -= 4; reasons.append("风险收益比不足")
    if risk <= 0.06:
        score += 2; reasons.append("距离防守位较近")
    elif risk > 0.11:
        score -= 3; reasons.append("距离防守位偏远")
    return {"score": float(max(-8, min(12, score))), "rr": float(rr), "defense": float(defense), "next_pressure": float(next_pressure), "space": float(space), "risk": float(risk), "reasons": reasons}


def risk_filter(d: pd.DataFrame, row_meta: Dict[str, Any]) -> Dict[str, Any]:
    d = force_kline_schema(d)
    close = safe_float(d["close"].iloc[-1])
    high250 = safe_float(d["high"].tail(250).max())
    low250 = safe_float(d["low"].tail(250).min())
    long_pos = (close - low250) / (high250 - low250) if high250 > low250 else 0.5
    bias20 = pct(close, safe_float(d["ma20"].iloc[-1]))
    amount = safe_float(d["amount"].iloc[-1])
    penalty = 0.0
    reasons = []
    hard = False
    if amount > 0 and amount < float(os.environ.get("破界_最低成交额", "50000000")):
        penalty -= 8; reasons.append("成交额偏低，实盘执行风险高")
    if long_pos >= 0.86:
        penalty -= 8; reasons.append("250日位置过高，追高风险")
    elif long_pos >= 0.76:
        penalty -= 4; reasons.append("250日位置偏高")
    if bias20 > 0.18:
        penalty -= 6; reasons.append("20日乖离偏高")
    if safe_float(d["vr1"].iloc[-1]) > 5:
        penalty -= 5; reasons.append("单日量能极端放大")
    name = str(row_meta.get("名称", row_meta.get("name", "")))
    if "ST" in name or "退" in name:
        hard = True; penalty -= 50; reasons.append("ST/退市风险标的剔除")
    return {"penalty": float(penalty), "hard_exclude": hard, "reasons": reasons, "long_pos_250": float(long_pos), "bias20": float(bias20)}


def scan_one(symbol: str, name: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    # 先做外部字段兼容，避免一号员工/BaoStock/缓存返回字段差异导致 volume/open/high/low/close KeyError。
    d = normalize_external_kline_df(df)
    if d is not None:
        d = force_kline_schema(d)
    if d is None or len(d) < 180:
        return None
    zones = find_coreline_zones(d)
    if not zones:
        return None
    close = safe_float(d["close"].iloc[-1])
    # 2.0：核心压力线可能略有偏差，因此不只看S/A/B强线，也允许近价C级共振线进入观察。
    valid_zones = []
    for z in zones:
        center = safe_float(z["center"])
        zscore = safe_float(z.get("score"))
        if center <= 0:
            continue
        near = close * 0.66 <= center <= close * 1.16
        high_quality = z["level"] in ["S", "A", "B"] and zscore >= POJIE_MIN_CORELINE_SCORE
        nearby_c = z["level"] == "C" and abs(center / close - 1) <= 0.045 and zscore >= max(45, POJIE_MIN_CORELINE_SCORE - 8)
        if near and (high_quality or nearby_c):
            valid_zones.append(z)
    if not valid_zones:
        # 若没有强核心线，至少拿最接近现价的前几条做低等级观察，避免“核心线选偏”导致全市场空。
        valid_zones = sorted(zones[:8], key=lambda z: abs(safe_float(z["center"]) / close - 1) if close > 0 else 99)[:3]
    best_result = None
    for z in valid_zones[:5]:
        core_score = safe_float(z["score"])
        buildup = score_left_buildup(d, z)
        breakout = detect_breakout(d, z)
        rr = calc_space_rr(d, z, zones)
        risk = risk_filter(d, {"名称": name})
        if risk["hard_exclude"]:
            continue
        total = core_score * 0.32 + safe_float(buildup["score"]) * (27 / 35) + safe_float(breakout["score"]) * (27 / 30) + safe_float(rr["score"]) + safe_float(risk["penalty"])
        # 2.0：未完全有效突破也可以作为“临界观察”，但封顶，不能冒充强确认。
        if not breakout["effective"]:
            total = min(total, 76 if POJIE_OUTPUT_OBSERVATION else 69)
        total = max(0, min(100, total))
        if total >= 88 and breakout["effective"] and z["level"] == "S" and rr["rr"] >= 2.0:
            level = "S"
        elif total >= 80 and breakout["effective"]:
            level = "A"
        elif total >= (68 if POJIE_STRATEGY_PROFILE != "strict" else 70):
            level = "B"
        else:
            level = "C"
        result = {
            "employee": "破界",
            "version": MODEL_VERSION,
            "symbol": symbol,
            "name": name,
            "date": str(pd.to_datetime(d["date"].iloc[-1]).date()),
            "close": close,
            "signal_level": level,
            "score": round(float(total), 2),
            "coreline": round(safe_float(z["center"]), 3),
            "coreline_zone": [round(safe_float(z["low"]), 3), round(safe_float(z["high"]), 3)],
            "coreline_level": z["level"],
            "coreline_score": round(core_score, 2),
            "strategy_profile": POJIE_STRATEGY_PROFILE,
            "coreline_sources": z["sources"][:12],
            "coreline_timeframes": z["timeframes"],
            "buildup_score": round(safe_float(buildup["score"]), 2),
            "buildup_level": buildup["level"],
            "buildup_reasons": buildup["reasons"],
            "breakout_effective": bool(breakout["effective"]),
            "breakout_score": round(safe_float(breakout["score"]), 2),
            "breakout_reasons": breakout["reasons"],
            "defense_price": round(safe_float(rr["defense"]), 3),
            "next_pressure": round(safe_float(rr["next_pressure"]), 3),
            "space": round(safe_float(rr["space"]), 4),
            "risk_distance": round(safe_float(rr["risk"]), 4),
            "rr": round(safe_float(rr["rr"]), 2),
            "space_reasons": rr["reasons"],
            "risk_reasons": risk["reasons"],
            "long_pos_250": round(safe_float(risk["long_pos_250"]), 4),
            "bias20": round(safe_float(risk["bias20"]), 4),
            "confirm_condition": "次日/后续不快速跌回核心线高沿下方，回踩核心线或突破K实体中位附近缩量止跌再转强。",
            "giveup_condition": "放量长上影回落到核心线下方，或跌破突破K实底/防守位，破界失败。",
        }
        if best_result is None or result["score"] > best_result["score"]:
            best_result = result
    return best_result


def build_report(results: List[Dict[str, Any]], scanned: int, failed: int = 0, no_kline: int = 0) -> str:
    lines = []
    valid_scanned = max(0, int(scanned) - int(failed) - int(no_kline))
    not_selected = max(0, valid_scanned - len(results))
    lines.append(f"【破界战法2.0｜核心线突破/临界观察独立战法】")
    lines.append(f"生成时间：{now_bj()}")
    lines.append(
        f"扫描：{scanned}只，K线有效：{valid_scanned}只，"
        f"候选：{len(results)}只，未入选：{not_selected}只，"
        f"无K线/数据不足：{no_kline}只，异常失败：{failed}只"
    )
    lines.append("")
    if not results:
        lines.append("今日暂无破界候选。")
        return "\n".join(lines)
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['symbol']} {r['name']}｜{r['signal_level']}｜{r['score']}分｜收盘{r['close']}")
        lines.append(f"   核心线：{r['coreline']} 区间{r['coreline_zone']}｜{r['coreline_level']}级｜{r['coreline_score']}分｜周期{','.join(r['coreline_timeframes'])}")
        lines.append(f"   线源：{'；'.join(r['coreline_sources'][:5])}")
        lines.append(f"   蓄势：{r['buildup_level']} {r['buildup_score']}分｜{'；'.join(r['buildup_reasons'][:3])}")
        lines.append(f"   突破：{'有效' if r['breakout_effective'] else '观察'} {r['breakout_score']}分｜{'；'.join(r['breakout_reasons'][:3])}")
        lines.append(f"   防守：{r['defense_price']}｜上压：{r['next_pressure']}｜空间{r['space']:.1%}｜风险{r['risk_distance']:.1%}｜RR={r['rr']}")
        if r['risk_reasons']:
            lines.append(f"   风险：{'；'.join(r['risk_reasons'][:3])}")
        lines.append(f"   确认：{r['confirm_condition']}")
        lines.append(f"   放弃：{r['giveup_condition']}")
        lines.append("")
    return "\n".join(lines)



# ========================= 数据入口增强补丁：参考一号员工数据层 =========================
# 目标：破界战法仍然独立运行，但数据入口更稳：
# 1）K线严格按“缓存优先”：先读一号员工事前缓存，命中就绝不联网；
# 2）缓存缺失/无效时，才调用一号员工 get_daily_kline 走 BaoStock 主通道补拉；
# 3）默认禁止 AkShare 拉K线，除非显式设置 POJIE_ALLOW_AKSHARE_KLINE=1；
# 4）股票池优先缓存/本地反推，再用一号员工入口，避免无缓存时直接失败；
# 5）最后启用应急股票池，保证流程能跑完并产出诊断报告。

VALID_STOCK_PREFIXES_POJIE = (
    "sh.600", "sh.601", "sh.603", "sh.605", "sh.688",
    "sz.000", "sz.001", "sz.002", "sz.003", "sz.300", "sz.301",
)


def plain_code_from_any(value: Any) -> str:
    text = str(value).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:].zfill(6)
    return ""


def bs_code_from_plain(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("600", "601", "603", "605", "688")):
        return "sh." + code
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz." + code
    return ""


def normalize_stock_list_df(df: pd.DataFrame, source: str = "unknown") -> pd.DataFrame:
    """统一股票池字段为：代码、名称、bs_code。"""
    empty = pd.DataFrame(columns=["代码", "名称", "bs_code"])
    if df is None or getattr(df, "empty", True):
        return empty
    d = df.copy()

    # BaoStock 常见字段：code / code_name
    if "code" in d.columns and ("代码" not in d.columns):
        d["bs_code"] = d["code"].astype(str)
        d["代码"] = d["bs_code"].apply(plain_code_from_any)
        if "code_name" in d.columns:
            d["名称"] = d["code_name"].astype(str)
        elif "name" in d.columns:
            d["名称"] = d["name"].astype(str)
        else:
            d["名称"] = ""
    else:
        code_col = None
        name_col = None
        bs_col = None
        for c in ["代码", "股票代码", "symbol", "证券代码", "code"]:
            if c in d.columns:
                code_col = c
                break
        for c in ["名称", "股票名称", "股票简称", "name", "code_name"]:
            if c in d.columns:
                name_col = c
                break
        for c in ["bs_code", "baostock_code"]:
            if c in d.columns:
                bs_col = c
                break
        if code_col is None and bs_col is None:
            print(f"破界股票池标准化失败：source={source} columns={list(d.columns)[:20]}")
            return empty
        if code_col is not None:
            d["代码"] = d[code_col].apply(plain_code_from_any)
        else:
            d["代码"] = d[bs_col].apply(plain_code_from_any)
        d["名称"] = d[name_col].astype(str) if name_col else ""
        if bs_col:
            d["bs_code"] = d[bs_col].astype(str)
        else:
            d["bs_code"] = d["代码"].apply(bs_code_from_plain)

    d["代码"] = d["代码"].astype(str).str.zfill(6)
    d["名称"] = d["名称"].fillna("").astype(str)
    d["bs_code"] = d["bs_code"].fillna("").astype(str)
    bad_bs = ~d["bs_code"].str.startswith(("sh.", "sz."), na=False)
    if bad_bs.any():
        d.loc[bad_bs, "bs_code"] = d.loc[bad_bs, "代码"].apply(bs_code_from_plain)
    d = d[d["bs_code"].str.startswith(VALID_STOCK_PREFIXES_POJIE, na=False)].copy()
    d = d[~d["名称"].str.contains("ST|\\*ST|退", regex=True, na=False)].copy()
    d = d.drop_duplicates(subset=["代码"])
    return d[["代码", "名称", "bs_code"]].reset_index(drop=True)


def stock_list_from_local_kline_cache() -> pd.DataFrame:
    """没有股票池接口时，从 kline_cache 里的CSV文件名反推股票池。"""
    roots = []
    for env_key in ["FULL_HISTORY_CACHE_DIR", "CACHE_DIR"]:
        v = os.environ.get(env_key, "").strip()
        if v:
            roots.append(v)
    roots.extend(["kline_cache", "K线缓存", "kline_cache/base", "kline_cache/deep"])

    rows = []
    seen = set()
    for root in roots:
        if not root or not os.path.exists(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                if not fn.lower().endswith(".csv") or fn.startswith("_"):
                    continue
                code = plain_code_from_any(fn[:12])
                if not code or code in seen:
                    continue
                bs_code = bs_code_from_plain(code)
                if not bs_code:
                    continue
                rows.append({"代码": code, "名称": "", "bs_code": bs_code})
                seen.add(code)
    df = pd.DataFrame(rows)
    if not df.empty:
        print(f"破界：从本地K线缓存反推股票池成功，数量={len(df)}")
    return normalize_stock_list_df(df, source="local_cache")


def get_emergency_stock_list() -> pd.DataFrame:
    """应急股票池：只在所有正式股票池入口失败时使用，避免 GitHub Actions 因股票池为空直接失败。"""
    rows = [
        ("000001", "平安银行"), ("000002", "万科A"), ("000063", "中兴通讯"), ("000333", "美的集团"),
        ("000338", "潍柴动力"), ("000538", "云南白药"), ("000568", "泸州老窖"), ("000651", "格力电器"),
        ("000725", "京东方A"), ("000858", "五粮液"), ("000938", "紫光股份"), ("000977", "浪潮信息"),
        ("002049", "紫光国微"), ("002129", "TCL中环"), ("002156", "通富微电"), ("002230", "科大讯飞"),
        ("002241", "歌尔股份"), ("002371", "北方华创"), ("002415", "海康威视"), ("002475", "立讯精密"),
        ("002594", "比亚迪"), ("002709", "天赐材料"), ("002812", "恩捷股份"), ("002916", "深南电路"),
        ("300014", "亿纬锂能"), ("300033", "同花顺"), ("300059", "东方财富"), ("300122", "智飞生物"),
        ("300124", "汇川技术"), ("300274", "阳光电源"), ("300308", "中际旭创"), ("300316", "晶盛机电"),
        ("300394", "天孚通信"), ("300408", "三环集团"), ("300750", "宁德时代"), ("300760", "迈瑞医疗"),
        ("300782", "卓胜微"), ("300896", "爱美客"), ("600000", "浦发银行"), ("600009", "上海机场"),
        ("600010", "包钢股份"), ("600030", "中信证券"), ("600031", "三一重工"), ("600036", "招商银行"),
        ("600050", "中国联通"), ("600111", "北方稀土"), ("600276", "恒瑞医药"), ("600309", "万华化学"),
        ("600406", "国电南瑞"), ("600438", "通威股份"), ("600519", "贵州茅台"), ("600570", "恒生电子"),
        ("600585", "海螺水泥"), ("600690", "海尔智家"), ("600703", "三安光电"), ("600760", "中航沈飞"),
        ("600809", "山西汾酒"), ("600887", "伊利股份"), ("600893", "航发动力"), ("600900", "长江电力"),
        ("601012", "隆基绿能"), ("601088", "中国神华"), ("601166", "兴业银行"), ("601318", "中国平安"),
        ("601398", "工商银行"), ("601601", "中国太保"), ("601628", "中国人寿"), ("601668", "中国建筑"),
        ("601688", "华泰证券"), ("601857", "中国石油"), ("601888", "中国中免"), ("603019", "中科曙光"),
        ("603259", "药明康德"), ("603288", "海天味业"), ("603501", "韦尔股份"), ("603799", "华友钴业"),
        ("603986", "兆易创新"), ("688008", "澜起科技"), ("688012", "中微公司"), ("688036", "传音控股"),
        ("688111", "金山办公"), ("688126", "沪硅产业"), ("688256", "寒武纪"), ("688981", "中芯国际"),
    ]
    df = pd.DataFrame(rows, columns=["代码", "名称"])
    df["bs_code"] = df["代码"].apply(bs_code_from_plain)
    print(f"破界：启用应急股票池，数量={len(df)}")
    return normalize_stock_list_df(df, source="emergency")


def get_stock_list_from_akshare_direct() -> pd.DataFrame:
    try:
        import akshare as ak
    except Exception as e:
        print(f"破界：AkShare未安装或导入失败，无法直接取股票池：{e}")
        return pd.DataFrame(columns=["代码", "名称", "bs_code"])

    for attempt in range(1, 4):
        try:
            print(f"破界股票池获取：source=akshare stage=stock_zh_a_spot_em retry={attempt}/3")
            df = ak.stock_zh_a_spot_em()
            out = normalize_stock_list_df(df, source="akshare_direct")
            if not out.empty:
                print(f"破界：AkShare直接股票池获取成功，数量={len(out)}")
                return out
        except Exception as e:
            print(f"破界：AkShare直接股票池失败 retry={attempt}/3 error={str(e)[:180]}")
            time.sleep(1.2 * attempt)
    return pd.DataFrame(columns=["代码", "名称", "bs_code"])


def get_stock_list_safe(base) -> pd.DataFrame:
    """
    股票池逻辑：
    1）先走一号员工全历史缓存股票池函数；
    2）再从本地 kline_cache 反推股票池；
    3）再走一号员工 get_a_stock_list，通常会优先缓存，必要时才 BaoStock；
    4）默认不直接用 AkShare 股票池，除非 POJIE_ALLOW_AKSHARE_STOCK_LIST=1；
    5）最后应急池兜底。
    """
    # 1. 一号员工缓存股票池：最符合你现在“先有缓存、破界消费缓存”的用法。
    fn = getattr(base, "get_a_stock_list_from_full_cache_universe", None)
    if fn is not None:
        try:
            print("破界：优先尝试一号员工全历史缓存股票池 base.get_a_stock_list_from_full_cache_universe()")
            df = fn()
            out = normalize_stock_list_df(df, source="base.full_cache_universe")
            if not out.empty:
                print(f"破界：股票池可用 source=base.full_cache_universe stocks={len(out)}")
                return out
        except Exception as e:
            print(f"破界：一号员工缓存股票池失败 error={str(e)[:180]}")

    # 2. 本地缓存反推：只要 kline_cache 里有CSV，就不需要联网取股票池。
    out = stock_list_from_local_kline_cache()
    if not out.empty:
        print(f"破界：股票池可用 source=local_kline_cache stocks={len(out)}")
        return out

    # 3. 一号员工标准股票池入口：一号员工内部是缓存优先，必要时 BaoStock 主通道。
    fn = getattr(base, "get_a_stock_list", None)
    if fn is not None:
        try:
            print("破界：本地缓存股票池为空，尝试一号员工标准股票池 base.get_a_stock_list()")
            df = fn()
            out = normalize_stock_list_df(df, source="base.get_a_stock_list")
            if not out.empty:
                print(f"破界：股票池可用 source=base.get_a_stock_list stocks={len(out)}")
                return out
        except Exception as e:
            print(f"破界：base.get_a_stock_list失败 error={str(e)[:180]}")

    # 4. AkShare 股票池默认关闭；只有显式打开才用。
    if os.environ.get("POJIE_ALLOW_AKSHARE_STOCK_LIST", "0") == "1":
        fn = getattr(base, "get_a_stock_list_from_akshare", None)
        if fn is not None:
            try:
                print("破界：显式允许，尝试 base.get_a_stock_list_from_akshare()")
                df = fn()
                out = normalize_stock_list_df(df, source="base.akshare_stock_list")
                if not out.empty:
                    print(f"破界：base AkShare备用股票池可用 stocks={len(out)}")
                    return out
            except Exception as e:
                print(f"破界：base AkShare股票池失败 error={str(e)[:180]}")

        out = get_stock_list_from_akshare_direct()
        if not out.empty:
            return out

    # 5. 最后应急股票池。
    if os.environ.get("POJIE_DISABLE_EMERGENCY_UNIVERSE", "0") != "1":
        return get_emergency_stock_list()

    return pd.DataFrame(columns=["代码", "名称", "bs_code"])

def normalize_external_kline_df(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """
    把一号员工缓存 / BaoStock / AkShare / 各类CSV统一成破界模型字段。

    关键修复：
    1）兼容一号员工真实缓存 kline_cache/base/sh_*.csv；
    2）兼容字段名：volume / vol / 成交量 / 成交量(手) / amount / value / 成交额；
    3）如果缺 volume 但有 amount+close，自动用 amount/close 近似反推 volume；
    4）如果缺 amount 但有 volume+close，自动用 volume*close 近似 amount；
    5）无论来源如何，返回前必须包含：date/open/high/low/close/volume/amount，避免后续 'volume' KeyError。
    """
    if df is None or getattr(df, "empty", True):
        return None

    d = df.copy()
    d.columns = [str(c).strip().replace("\ufeff", "") for c in d.columns]

    def norm_col(c: str) -> str:
        x = str(c).strip().replace("\ufeff", "")
        xl = x.lower().strip()
        mapping = {
            "日期": "date", "交易日期": "date", "trade_date": "date", "datetime": "date", "time": "date", "date": "date",
            "开盘": "open", "开盘价": "open", "open": "open",
            "最高": "high", "最高价": "high", "high": "high",
            "最低": "low", "最低价": "low", "low": "low",
            "收盘": "close", "收盘价": "close", "close": "close",
            "成交量": "volume", "成交量(手)": "volume", "成交量(股)": "volume", "成交量(万手)": "volume",
            "volume": "volume", "vol": "volume", "volumn": "volume",
            "成交额": "amount", "成交额(元)": "amount", "成交额(万元)": "amount", "amount": "amount", "value": "amount", "turnover_value": "amount",
            "涨跌幅": "pct_chg", "pctchg": "pct_chg", "pct_chg": "pct_chg",
            "换手率": "turnover_rate", "turn": "turnover_rate", "turnover": "turnover_rate",
        }
        return mapping.get(x, mapping.get(xl, xl))

    d = d.rename(columns={c: norm_col(c) for c in d.columns})

    # 如果重命名后出现重复列，例如同时有 vol 和 volume，保留第一个非空更多的列。
    if len(set(d.columns)) != len(d.columns):
        merged = pd.DataFrame(index=d.index)
        for col in dict.fromkeys(list(d.columns)):
            same = d.loc[:, d.columns == col]
            if same.shape[1] == 1:
                merged[col] = same.iloc[:, 0]
            else:
                # 逐行取第一个非空值。
                merged[col] = same.bfill(axis=1).iloc[:, 0]
        d = merged

    # 必要字段检查，volume/amount 可互相反推，但 OHLC/date 必须存在。
    required_price = ["date", "open", "high", "low", "close"]
    if not all(c in d.columns for c in required_price):
        return None

    # 日期标准化。
    d["date"] = pd.to_datetime(d["date"], errors="coerce").dt.strftime("%Y-%m-%d")

    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")

    # 关键兜底：防止后续任何模块再报 'volume'。
    if "volume" not in d.columns:
        if "amount" in d.columns:
            d["volume"] = d["amount"] / d["close"].replace(0, np.nan)
        else:
            d["volume"] = 0.0

    if "amount" not in d.columns:
        d["amount"] = d["volume"] * d["close"]

    d["volume"] = pd.to_numeric(d["volume"], errors="coerce").fillna(0.0)
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce").fillna(d["volume"] * pd.to_numeric(d["close"], errors="coerce"))

    d = d[["date", "open", "high", "low", "close", "volume", "amount"]].copy()
    d = d.dropna(subset=["date", "open", "high", "low", "close"])
    d = d[(d["open"] > 0) & (d["high"] > 0) & (d["low"] > 0) & (d["close"] > 0)]
    if d.empty:
        return None

    # 如果 volume 全为0，说明源文件没有有效量能；不让程序报错，但这种票模型会自然难出高分。
    return normalize_kline(d)

def local_kline_candidate_paths(bs_code: str) -> List[str]:
    """
    一号员工缓存懒加载路径，优先匹配真实缓存格式：
    kline_cache/base/sh_600000.csv / kline_cache/base/sz_000001.csv。
    命中任意有效CSV就直接用，不联网。
    """
    code = plain_code_from_any(bs_code)
    if not code:
        return []
    if str(bs_code).startswith(("sh.", "sz.")):
        bs_norm = str(bs_code)
    else:
        bs_norm = ("sh." + code) if code.startswith(("600", "601", "603", "605", "688")) else ("sz." + code)
    bs_file = bs_norm.replace(".", "_")

    roots = []
    for env_key in ["FULL_HISTORY_CACHE_DIR", "CACHE_DIR"]:
        v = os.environ.get(env_key, "").strip()
        if v:
            roots.append(v)
    roots.extend(["kline_cache", "K线缓存", "."])

    paths = []
    for root in roots:
        if not root:
            continue
        # 最高优先级：一号员工实际缓存格式。
        paths.extend([
            os.path.join(root, "base", f"{bs_file}.csv"),
            os.path.join(root, "deep", f"{bs_file}.csv"),
            os.path.join(root, f"{bs_file}.csv"),
            os.path.join(root, "base", f"{code}.csv"),
            os.path.join(root, "deep", f"{code}.csv"),
            os.path.join(root, f"{code}.csv"),
        ])

    # 常见固定目录再补一次，防止环境变量指到 kline_cache 后漏掉中文目录。
    for root in ["kline_cache", "K线缓存"]:
        paths.extend([
            os.path.join(root, "base", f"{bs_file}.csv"),
            os.path.join(root, "deep", f"{bs_file}.csv"),
            os.path.join(root, f"{bs_file}.csv"),
            os.path.join(root, "base", f"{code}.csv"),
            os.path.join(root, "deep", f"{code}.csv"),
            os.path.join(root, f"{code}.csv"),
        ])

    # 最后才递归兜底，避免每只股票先扫大目录导致慢。
    for root in ["kline_cache", "K线缓存"]:
        try:
            if os.path.exists(root):
                paths.extend(glob.glob(os.path.join(root, "**", f"*{bs_file}*.csv"), recursive=True))
                paths.extend(glob.glob(os.path.join(root, "**", f"*{code}*.csv"), recursive=True))
        except Exception:
            pass

    seen, out = set(), []
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out

def read_local_kline_cache_for_pojie(bs_code: str) -> Optional[pd.DataFrame]:
    for path in local_kline_candidate_paths(bs_code):
        if not path or not os.path.exists(path):
            continue
        try:
            try:
                df = pd.read_csv(path, dtype=str, encoding="utf-8")
            except UnicodeDecodeError:
                df = pd.read_csv(path, dtype=str, encoding="gbk")
            out = normalize_external_kline_df(df)
            if out is not None and len(out) >= 120:
                KLINE_STATS["cache_hit"] = KLINE_STATS.get("cache_hit", 0) + 1
                # 默认只打印少量命中样本；需要逐只排查时设置 POJIE_VERBOSE_KLINE=1。
                if POJIE_VERBOSE_KLINE or KLINE_STATS["cache_hit"] <= POJIE_LOG_FIRST_N:
                    print(f"破界K线：读取本地缓存成功[{KLINE_STATS['cache_hit']}] symbol={bs_code} file={path} rows={len(out)}")
                return out
        except Exception as e:
            KLINE_STATS["cache_read_error"] = KLINE_STATS.get("cache_read_error", 0) + 1
            if POJIE_VERBOSE_KLINE or KLINE_STATS["cache_read_error"] <= POJIE_LOG_FIRST_N:
                print(f"破界K线：读取本地缓存失败[{KLINE_STATS['cache_read_error']}] symbol={bs_code} file={path} error={str(e)[:120]}")
    KLINE_STATS["cache_miss"] = KLINE_STATS.get("cache_miss", 0) + 1
    return None


def save_pojie_normalized_kline_cache(bs_code: str, df: pd.DataFrame) -> None:
    """
    缓存缺失后补拉成功时，额外写一份破界标准扁平缓存：kline_cache/600000.csv。
    后续再跑时会直接命中这份缓存，不再联网。
    """
    try:
        code = plain_code_from_any(bs_code)
        if not code or df is None or getattr(df, "empty", True):
            return
        os.makedirs("kline_cache", exist_ok=True)
        out = normalize_external_kline_df(df)
        if out is None or out.empty:
            return
        keep = [c for c in ["date", "open", "high", "low", "close", "volume", "amount"] if c in out.columns]
        out[keep].to_csv(os.path.join("kline_cache", f"{code}.csv"), index=False, encoding="utf-8")
        print(f"破界K线：补拉成功后已写入标准缓存 file=kline_cache/{code}.csv rows={len(out)}")
    except Exception as e:
        print(f"破界K线：写入标准缓存失败 symbol={bs_code} error={str(e)[:120]}")


def get_daily_kline_akshare_direct(bs_code: str, lookback_days: int = 2600) -> Optional[pd.DataFrame]:
    try:
        import akshare as ak
    except Exception:
        return None
    symbol = plain_code_from_any(bs_code)
    if not symbol:
        return None
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=int(lookback_days))).strftime("%Y%m%d")
    for attempt in range(1, 3):
        try:
            time.sleep(0.15 * attempt)
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            out = normalize_external_kline_df(df)
            if out is not None and len(out) >= 120:
                print(f"破界K线：AkShare直接补拉成功 symbol={bs_code} rows={len(out)}")
                return out
        except Exception as e:
            if attempt <= 1:
                print(f"破界K线：AkShare直接补拉失败 symbol={bs_code} retry={attempt}/2 error={str(e)[:160]}")
    return None


def get_daily_kline_safe(base, bs_code: str) -> Optional[pd.DataFrame]:
    """
    K线数据逻辑，严格按你要的顺序：

    1）先查一号员工事前缓存 / GitHub恢复缓存 / 本地 kline_cache。
       命中有效CSV后，直接返回，绝不联网。

    2）只有缓存没有、缓存字段坏、或K线少于120根时，才补拉。
       补拉默认走一号员工 get_daily_kline，也就是 BaoStock 主通道。

    3）默认禁止 AkShare K线兜底。
       即使 workflow 里写了 KLINE_FALLBACK_AKSHARE=1，本函数也会临时改成0，
       防止一号员工内部自动跳到 AkShare。

    4）只有显式设置 POJIE_ALLOW_AKSHARE_KLINE=1，才允许最后用 AkShare。
    """
    # 第一步：缓存优先。命中就不拉。
    cached = read_local_kline_cache_for_pojie(bs_code)
    if cached is not None and len(cached) >= 120:
        return cached

    # 第二步：缓存没有，才允许远程补拉。默认允许 BaoStock 补拉。
    remote_on_miss = os.environ.get("POJIE_REMOTE_ON_CACHE_MISS", "1")
    if remote_on_miss != "1":
        print(f"破界K线：缓存未命中，且 POJIE_REMOTE_ON_CACHE_MISS=0，跳过 symbol={bs_code}")
        return None

    allow_akshare = os.environ.get("POJIE_ALLOW_AKSHARE_KLINE", "0") == "1"

    # 防止 workflow 里 KLINE_FALLBACK_AKSHARE=1 导致一号员工内部跳到AkShare。
    old_ak_env = os.environ.get("KLINE_FALLBACK_AKSHARE")
    if not allow_akshare:
        os.environ["KLINE_FALLBACK_AKSHARE"] = "0"

    # 第三步：调用一号员工数据入口。这个入口内部：缓存 -> BaoStock；AkShare已被上面关掉。
    try:
        if hasattr(base, "get_daily_kline"):
            try:
                KLINE_STATS["remote_fetch"] = KLINE_STATS.get("remote_fetch", 0) + 1
                if POJIE_VERBOSE_KLINE or KLINE_STATS["remote_fetch"] <= POJIE_LOG_FIRST_N:
                    print(f"破界K线：缓存未命中，开始BaoStock主通道补拉[{KLINE_STATS['remote_fetch']}] symbol={bs_code}")
                df = base.get_daily_kline(bs_code, cache_scope="deep")
            except TypeError:
                df = base.get_daily_kline(bs_code)

            out = normalize_external_kline_df(df)
            if out is not None and len(out) >= 120:
                KLINE_STATS["remote_success"] = KLINE_STATS.get("remote_success", 0) + 1
                if POJIE_VERBOSE_KLINE or KLINE_STATS["remote_success"] <= POJIE_LOG_FIRST_N:
                    print(f"破界K线：BaoStock/一号员工入口补拉成功[{KLINE_STATS['remote_success']}] symbol={bs_code} rows={len(out)}")
                save_pojie_normalized_kline_cache(bs_code, out)
                return out
    except Exception as e:
        KLINE_STATS["remote_fail"] = KLINE_STATS.get("remote_fail", 0) + 1
        if POJIE_VERBOSE_KLINE or KLINE_STATS["remote_fail"] <= POJIE_LOG_FIRST_N:
            print(f"破界K线：BaoStock/一号员工入口补拉失败[{KLINE_STATS['remote_fail']}] symbol={bs_code} error={str(e)[:180]}")
    finally:
        if old_ak_env is None:
            os.environ.pop("KLINE_FALLBACK_AKSHARE", None)
        else:
            os.environ["KLINE_FALLBACK_AKSHARE"] = old_ak_env

    # 第四步：AkShare 只在显式允许时作为最后兜底。
    if allow_akshare:
        print(f"破界K线：显式允许AkShare，开始最后兜底 symbol={bs_code}")
        return get_daily_kline_akshare_direct(bs_code)

    return None

def write_empty_report(reason: str, scanned: int = 0, failed: int = 0) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    payload = {
        "version": MODEL_VERSION,
        "generated_at": now_bj(),
        "scanned": scanned,
        "failed": failed,
        "results": [],
        "reason": reason,
    }
    json_path = os.path.join(OUTPUT_DIR, "pojie_signals.json")
    txt_path = os.path.join(OUTPUT_DIR, "pojie_report.txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"【破界｜核心线突破独立战法】\n{reason}\n扫描：{scanned}只，失败/无数据：{failed}只\n")
    print(f"破界：{reason}")
    print(f"破界空报告已保存：{json_path} / {txt_path}")

def parse_args():
    p = argparse.ArgumentParser(description="破界：核心线突破独立战法")
    p.add_argument("--模式", default="daily", choices=["daily", "selfcheck"], help="daily=执行破界选股；selfcheck=只做自检")
    p.add_argument("--基础模型文件", default=DEFAULT_BASE_MODEL_FILE, help="复用的一号员工主文件，默认 stock_alert.py")
    p.add_argument("--最多股票数量", type=int, default=int(os.environ.get("破界_最多股票数量", "0")), help="调试用，0=不限制")
    p.add_argument("--输出数量", type=int, default=int(os.environ.get("破界_输出数量", "10")), help="破界候选最多输出数量")
    p.add_argument("--最低分", type=float, default=float(os.environ.get("破界_最低分", "62" if os.environ.get("POJIE_STRATEGY_PROFILE", "balanced").lower() != "strict" else "70")), help="最低输出分数")
    p.add_argument("--发送Telegram", action="store_true", help="开启后调用基础模型 send_telegram 推送")
    p.add_argument("--不发送Telegram", action="store_true", help="强制不推送")
    return p.parse_args()



def quick_trigger_prefilter(df: pd.DataFrame) -> bool:
    """
    保守预筛：只过滤掉明显没有“破界触发K”的股票。
    目的是节省全量扫描时间；如果要完全不预筛，设置 POJIE_FAST_PREFILTER=0。
    返回 True = 需要进入完整 scan_one；False = 明显无触发，直接未入选。
    """
    if not POJIE_FAST_PREFILTER:
        return True
    d = normalize_external_kline_df(df)
    if d is None or len(d) < 120:
        return True
    last = d.iloc[-1]
    close = safe_float(last.get("close"))
    if close <= 0:
        return True
    pct_chg = safe_float(last.get("pct_chg"))
    close_pos = safe_float(last.get("close_pos"), 0.5)
    high = safe_float(last.get("high"))
    low = safe_float(last.get("low"))
    volume = safe_float(last.get("volume"))
    pre_high_20 = safe_float(d["high"].shift(1).tail(20).max())
    pre_high_60 = safe_float(d["high"].shift(1).tail(60).max())
    vol_ma20 = safe_float(d["volume"].tail(20).mean())
    # 只要有一个攻击/临界迹象，就进入完整模型。
    # 2.0预筛：更像“基础条件”，不过度严苛；只排除明显死水/远离压力的票。
    if pct_chg >= 1.0:
        return True
    if close_pos >= 0.62 and pct_chg >= 0.0:
        return True
    if pre_high_20 > 0 and close >= pre_high_20 * 0.975:
        return True
    if pre_high_60 > 0 and high >= pre_high_60 * 0.975 and close_pos >= 0.45:
        return True
    if vol_ma20 > 0 and volume >= vol_ma20 * 1.12 and close_pos >= 0.45:
        return True
    ma20 = safe_float(d["ma20"].iloc[-1]) if "ma20" in d.columns else 0
    ma60 = safe_float(d["ma60"].iloc[-1]) if "ma60" in d.columns else 0
    if ma20 > 0 and close >= ma20 * 0.985 and pre_high_60 > 0 and close >= pre_high_60 * 0.94:
        return True
    if ma60 > 0 and close >= ma60 * 0.99 and pre_high_60 > 0 and close >= pre_high_60 * 0.93:
        return True
    # 跳空/缺口类也保留。
    if len(d) >= 2:
        pre_high = safe_float(d["high"].iloc[-2])
        if pre_high > 0 and low > pre_high * 1.003:
            return True
    return False


def scan_worker_cache_only(row: Dict[str, Any]) -> Dict[str, Any]:
    """并行缓存扫描worker：只读本地缓存，不做BaoStock联网。"""
    code = str(row.get("代码", row.get("code", ""))).zfill(6)
    bs_code = str(row.get("bs_code", ""))
    name = str(row.get("名称", row.get("name", "")))
    if not bs_code or bs_code == "nan":
        bs_code = bs_code_from_plain(code)
    if not bs_code:
        return {"status": "failed", "failed_item": {"code": code, "name": name, "stage": "bad_bs_code"}, "kline_stats": {}}
    try:
        df = read_local_kline_cache_for_pojie(bs_code)
        df = normalize_external_kline_df(df)
        if df is None or len(df) < 120:
            return {"status": "cache_miss", "code": code, "name": name, "bs_code": bs_code, "kline_stats": {"cache_miss": 1}}
        if not quick_trigger_prefilter(df):
            return {"status": "not_selected", "prefilter_skip": 1, "kline_stats": {"cache_hit": 1}}
        res = scan_one(code, name, df)
        if res:
            return {"status": "candidate", "result": res, "kline_stats": {"cache_hit": 1}}
        return {"status": "not_selected", "kline_stats": {"cache_hit": 1}}
    except Exception as e:
        return {"status": "failed", "failed_item": {"code": code, "name": name, "bs_code": bs_code, "stage": "scan_exception", "error": str(e)[:200]}, "kline_stats": {}}


def merge_kline_stats(delta: Dict[str, int]) -> None:
    for k, v in (delta or {}).items():
        KLINE_STATS[k] = KLINE_STATS.get(k, 0) + int(v or 0)



# ========================= 破界战法2.0 专业核心压力线引擎 =========================
# 说明：以下函数覆盖上方同名函数。核心线标准不放宽，只把“选股逻辑”分层放宽。
# 核心压力线按一号员工体系重构：大周期优先、最大量阳K/实体位、次高次低收盘共振、
# 实体顶底共振、上影/缺口/假突破记忆、百分比价格桶成交密集区、核心带+确认上沿。

def _tf_base_weight(tf: str) -> float:
    return {"Y": 6.0, "Q": 5.0, "M": 4.0, "W": 2.6, "D": 1.25}.get(str(tf), 1.0)


def _cycle_lookback(tf: str) -> int:
    return {"D": 520, "W": 260, "M": 144, "Q": 96, "Y": 48}.get(str(tf), 200)


def _effective_max_volume_yang_k(row: pd.Series) -> bool:
    o, h, l, c = [safe_float(row.get(x)) for x in ["open", "high", "low", "close"]]
    if c <= o or c <= 0 or h <= l:
        return False
    body = abs(c - o)
    shadows = max(0.0, h - max(o, c)) + max(0.0, min(o, c) - l)
    body_pct = body / c if c else 0
    close_pos = safe_float(row.get("close_pos", (c - l) / (h - l) if h > l else 0.5), 0.5)
    return body_pct >= 0.018 and body >= shadows * 0.50 and close_pos >= 0.45


def detect_major_cycle_anchor_candidates(d: pd.DataFrame, timeframe: str) -> List[Dict[str, Any]]:
    """大级别核心锚点：年/季/月/周/日最大量阳K、次大量阳K、阶段最高、假突破高点。"""
    res: List[Dict[str, Any]] = []
    if d is None or len(d) < 12:
        return res
    x = force_kline_schema(d).tail(_cycle_lookback(timeframe)).reset_index(drop=True)
    if len(x) < 12:
        return res
    cur = safe_float(x["close"].iloc[-1])
    if cur <= 0:
        return res
    low_bound, high_bound = cur * 0.55, cur * 1.35
    tfw = _tf_base_weight(timeframe)
    vol = pd.to_numeric(x["volume"], errors="coerce").fillna(0)
    if vol.max() > 0:
        # 前3大量K，只重奖有效最大量阳K；阴线/十字/小实体只作为低权重供应参考。
        top_idx = list(vol.sort_values(ascending=False).head(min(5, len(x))).index)
        for rank, i in enumerate(top_idx, 1):
            row = x.loc[i]
            o, h, l, c = [safe_float(row.get(k)) for k in ["open", "high", "low", "close"]]
            if not (low_bound <= h <= high_bound or low_bound <= max(o, c) <= high_bound):
                continue
            date = str(pd.to_datetime(row.get("date")).date()) if pd.notna(pd.to_datetime(row.get("date"), errors="coerce")) else ""
            body_top, body_bottom = max(o, c), min(o, c)
            is_eff_yang = _effective_max_volume_yang_k(row)
            base_w = tfw * (1.55 if rank == 1 else 1.18 if rank <= 3 else 0.9)
            if is_eff_yang:
                res.append({"price": h, "source": f"{timeframe}级第{rank}大量有效阳K高点/核心确认线", "timeframe": timeframe, "date": date, "weight": base_w + 3.0})
                res.append({"price": body_top, "source": f"{timeframe}级第{rank}大量有效阳K实体顶", "timeframe": timeframe, "date": date, "weight": base_w + 2.4})
                res.append({"price": body_bottom, "source": f"{timeframe}级第{rank}大量有效阳K实体底/箱体底", "timeframe": timeframe, "date": date, "weight": base_w + 1.6})
            else:
                # 非有效阳K不能当箱底，但其高点/实体顶可作为供应参考，权重降低。
                res.append({"price": h, "source": f"{timeframe}级第{rank}大量K高点供应参考", "timeframe": timeframe, "date": date, "weight": base_w * 0.55})
                res.append({"price": body_top, "source": f"{timeframe}级第{rank}大量K实体顶供应参考", "timeframe": timeframe, "date": date, "weight": base_w * 0.50})
    # 年/季/月等大级别阶段高点、次高收盘价共振。
    for n, mult in [(12, 1.0), (24, 1.2), (36, 1.35), (60, 1.55), (100, 1.75)]:
        if len(x) < min(n, 12):
            continue
        seg = x.tail(min(n, len(x))).copy()
        hi = safe_float(seg["high"].max())
        if low_bound <= hi <= high_bound:
            res.append({"price": hi, "source": f"{timeframe}级近{n}根阶段最高点", "timeframe": timeframe, "date": str(pd.to_datetime(seg['date'].iloc[-1]).date()), "weight": tfw * mult})
        close_q = safe_float(seg["close"].quantile(0.94))
        body_top_q = safe_float(seg[["open", "close"]].max(axis=1).quantile(0.92))
        for px, src, extra in [(close_q, "次高/高位收盘共振", 1.4), (body_top_q, "实体顶共振", 1.6)]:
            if low_bound <= px <= high_bound:
                res.append({"price": px, "source": f"{timeframe}级近{n}根{src}", "timeframe": timeframe, "date": str(pd.to_datetime(seg['date'].iloc[-1]).date()), "weight": tfw * mult + extra})
    # 假突破/流动性扫单记忆：上影反压、突破后收不住。
    body_top = x[["open", "close"]].max(axis=1)
    prev_high = x["high"].rolling(20, min_periods=5).max().shift(1)
    upper_ratio = (x["high"] - body_top) / x["close"].replace(0, np.nan)
    fail_mask = (x["high"] >= prev_high * 0.995) & (x["close"] <= x["high"] * 0.965) & (upper_ratio >= 0.025)
    for i in list(x[fail_mask].tail(8).index):
        row = x.loc[i]
        px = safe_float(row["high"])
        if low_bound <= px <= high_bound:
            res.append({"price": px, "source": f"{timeframe}级假突破/长上影扫单高点", "timeframe": timeframe, "date": str(pd.to_datetime(row['date']).date()), "weight": tfw + 2.2})
    return res


def detect_volume_profile_resonance_candidates(d: pd.DataFrame, timeframe: str) -> List[Dict[str, Any]]:
    """百分比/对数价格桶 Volume Profile：成交密集区+实体/收盘/影线共振。"""
    res: List[Dict[str, Any]] = []
    if d is None or len(d) < 30:
        return res
    x = force_kline_schema(d).tail(_cycle_lookback(timeframe)).reset_index(drop=True)
    cur = safe_float(x["close"].iloc[-1])
    if cur <= 0:
        return res
    # 波动率自适应桶宽：越大周期桶越宽，日/周更细。
    atr_like = safe_float(((x["high"] - x["low"]) / x["close"].replace(0, np.nan)).tail(60).median(), 0.025)
    base_bin = {"D": 0.0055, "W": 0.0075, "M": 0.010, "Q": 0.014, "Y": 0.018}.get(timeframe, 0.008)
    bin_pct = max(base_bin, min(base_bin * 2.0, atr_like * 0.38))
    buckets: Dict[int, Dict[str, float]] = {}
    low_bound, high_bound = cur * 0.55, cur * 1.35
    for i, row in x.iterrows():
        o, h, l, c, amt = [safe_float(row.get(k)) for k in ["open", "high", "low", "close", "amount"]]
        if c <= 0:
            continue
        decay = 0.50 + 0.50 * (i + 1) / max(1, len(x))
        amt_w = max(1.0, min(4.0, (amt / 1e8) ** 0.30 if amt > 0 else 1.0))
        body_top, body_bottom = max(o, c), min(o, c)
        # 次高/收盘/实体顶权重高于普通影线。
        points = [
            (c, 2.2, "收盘共振"), (body_top, 2.0, "实体顶共振"),
            (body_bottom, 1.2, "实体底共振"), (h, 1.1, "影线高点"), (l, 0.7, "影线低点"),
        ]
        for px, wt, label in points:
            if not (low_bound <= px <= high_bound):
                continue
            key = int(round(math.log(px / cur) / bin_pct))
            b = buckets.setdefault(key, {"w": 0.0, "pxw": 0.0, "cnt": 0, "close_cnt": 0, "body_cnt": 0, "shadow_cnt": 0})
            weight = wt * decay * amt_w * _tf_base_weight(timeframe)
            b["w"] += weight; b["pxw"] += px * weight; b["cnt"] += 1
            if "收盘" in label: b["close_cnt"] += 1
            if "实体" in label: b["body_cnt"] += 1
            if "影线" in label: b["shadow_cnt"] += 1
    for b in sorted(buckets.values(), key=lambda y: (y["w"], y["close_cnt"], y["body_cnt"], y["cnt"]), reverse=True)[:12]:
        if b["w"] <= 0 or b["cnt"] < (5 if timeframe in ["D", "W"] else 3):
            continue
        px = b["pxw"] / b["w"]
        res.append({"price": px, "source": f"{timeframe}级成交密集/收盘实体共振桶 cnt={int(b['cnt'])} close={int(b['close_cnt'])} body={int(b['body_cnt'])}", "timeframe": timeframe, "date": str(pd.to_datetime(x['date'].iloc[-1]).date()), "weight": min(7.5, 1.5 + b["cnt"] * 0.18 + b["close_cnt"] * 0.25 + b["body_cnt"] * 0.22)})
    return res


def detect_gap_and_shadow_resonance_candidates(d: pd.DataFrame, timeframe: str) -> List[Dict[str, Any]]:
    res: List[Dict[str, Any]] = []
    if d is None or len(d) < 25:
        return res
    x = force_kline_schema(d).tail(_cycle_lookback(timeframe)).reset_index(drop=True)
    cur = safe_float(x["close"].iloc[-1])
    if cur <= 0:
        return res
    low_bound, high_bound = cur * 0.55, cur * 1.35
    tfw = _tf_base_weight(timeframe)
    for i in range(1, len(x)):
        row = x.loc[i]
        pre = x.loc[i - 1]
        date = str(pd.to_datetime(row["date"]).date())
        # 缺口边界：未回补/反复共振才在 cluster 里提权。
        if safe_float(row["low"]) > safe_float(pre["high"]) * 1.003:
            for px, src, wt in [(safe_float(pre["high"]), "向上跳空缺口下沿/前高", 1.8), (safe_float(row["low"]), "向上跳空缺口上沿/当日低", 1.5)]:
                if low_bound <= px <= high_bound:
                    res.append({"price": px, "source": f"{timeframe}级{src}", "timeframe": timeframe, "date": date, "weight": tfw + wt})
        if safe_float(row["high"]) < safe_float(pre["low"]) * 0.997:
            for px, src, wt in [(safe_float(pre["low"]), "向下跳空缺口上沿/前低", 2.0), (safe_float(row["high"]), "向下跳空缺口下沿/当日高", 1.7)]:
                if low_bound <= px <= high_bound:
                    res.append({"price": px, "source": f"{timeframe}级{src}", "timeframe": timeframe, "date": date, "weight": tfw + wt})
        upper = safe_float(row.get("upper_shadow_ratio", 0))
        lower = safe_float(row.get("lower_shadow_ratio", 0))
        volr = safe_float(row["volume"] / safe_float(x["volume"].rolling(10).mean().iloc[i], row["volume"]), 1)
        if upper >= 0.32 and volr >= 1.05:
            px = safe_float(row["high"])
            if low_bound <= px <= high_bound:
                res.append({"price": px, "source": f"{timeframe}级放量上影线共振/供应反应", "timeframe": timeframe, "date": date, "weight": tfw + 1.8})
        if lower >= 0.36 and volr >= 1.05:
            px = safe_float(row["low"])
            if low_bound <= px <= high_bound:
                res.append({"price": px, "source": f"{timeframe}级放量下影线需求反应", "timeframe": timeframe, "date": date, "weight": tfw + 1.0})
    return res


def cluster_corelines(candidates: List[Dict[str, Any]], current_price: float, atr_pct: float) -> List[Dict[str, Any]]:
    if not candidates or current_price <= 0:
        return []
    # 自适应容差：按百分比，不用固定金额；大波动适度放宽，但最高有限。
    tol_pct = max(0.0045, min(0.022, (atr_pct or 0.018) * 0.42))
    cands = sorted([c for c in candidates if safe_float(c.get("price")) > 0], key=lambda x: safe_float(x["price"]))
    clusters: List[List[Dict[str, Any]]] = []
    cur_list: List[Dict[str, Any]] = []
    for c in cands:
        px = safe_float(c["price"])
        if not cur_list:
            cur_list = [c]; continue
        center = np.average([safe_float(x["price"]) for x in cur_list], weights=[max(0.1, safe_float(x.get("weight", 1))) for x in cur_list])
        if abs(px / center - 1) <= tol_pct:
            cur_list.append(c)
        else:
            clusters.append(cur_list); cur_list = [c]
    if cur_list:
        clusters.append(cur_list)
    zones: List[Dict[str, Any]] = []
    for mem in clusters:
        prices = [safe_float(m["price"]) for m in mem]
        weights = [max(0.1, safe_float(m.get("weight", 1))) * _tf_base_weight(str(m.get("timeframe", "D"))) for m in mem]
        center = float(np.average(prices, weights=weights))
        low, high = min(prices), max(prices)
        sources = [str(m.get("source", "")) for m in mem]
        tfs = sorted(set(str(m.get("timeframe", "")) for m in mem if m.get("timeframe")))
        source_types = len(set(sources))
        reaction_count = len(mem)
        # 核心线质量：大周期 > 成交密集/最大量 > 次高收盘/实体 > 影线/缺口；多周期共振提权。
        tf_score = min(32, sum(_tf_base_weight(tf) for tf in tfs) * 3.2)
        major_bonus = 0
        if any(tf in tfs for tf in ["Y", "Q"]): major_bonus += 10
        if "M" in tfs: major_bonus += 6
        semantic_score = 0
        for key, val in [("大量有效阳K", 12), ("最大量", 9), ("成交密集", 8), ("次高", 7), ("收盘共振", 7), ("实体顶", 7), ("假突破", 8), ("上影", 5), ("缺口", 5), ("平台", 4), ("凹口", 4)]:
            if any(key in s for s in sources): semantic_score += val
        semantic_score = min(38, semantic_score)
        reaction_score = min(20, reaction_count * 1.6 + source_types * 1.1)
        dist = center / current_price - 1
        usability = 8 if -0.03 <= dist <= 0.18 else (3 if -0.10 <= dist <= 0.30 else -8)
        width_pct = (high / low - 1) if low > 0 else 0
        width_score = 5 if width_pct <= max(0.012, tol_pct * 1.6) else (2 if width_pct <= 0.04 else -3)
        score = max(0, min(100, tf_score + major_bonus + semantic_score + reaction_score + usability + width_score))
        if score >= 88: level = "S"
        elif score >= 76: level = "A"
        elif score >= 62: level = "B"
        else: level = "C"
        confirm_line = high  # 心理压力/突破确认线：核心带上沿，实盘最有价值。
        zones.append({
            "center": center, "low": low, "high": high, "confirm_line": confirm_line,
            "score": float(score), "level": level, "members": mem, "sources": sources[:24], "timeframes": tfs,
            "reaction_count": reaction_count, "source_types": source_types,
            "semantic_count": semantic_score, "distance_to_current": dist,
            "zone_width_pct": width_pct,
        })
    # 稀缺性：只保留靠近当前且质量较高的核心带，不罗列普通线。
    zones = [z for z in zones if safe_float(z["score"]) >= 50 and -0.18 <= safe_float(z["distance_to_current"]) <= 0.35]
    return sorted(zones, key=lambda z: (z["score"], -abs(safe_float(z["distance_to_current"]))), reverse=True)


def find_coreline_zones(daily: pd.DataFrame) -> List[Dict[str, Any]]:
    daily = force_kline_schema(daily)
    tfs = build_timeframes(daily)
    candidates: List[Dict[str, Any]] = []
    for tf, df in tfs.items():
        if df is None or len(df) < 12:
            continue
        window_df = df.tail(_cycle_lookback(tf)).reset_index(drop=True)
        candidates.extend(detect_major_cycle_anchor_candidates(window_df, tf))
        candidates.extend(detect_volume_profile_resonance_candidates(window_df, tf))
        candidates.extend(detect_gap_and_shadow_resonance_candidates(window_df, tf))
        # 保留原语义候选作为辅助，但权重被 cluster 的大周期/语义规则重新校准。
        candidates.extend(detect_semantic_coreline_candidates(window_df, tf))
    cur = safe_float(daily["close"].iloc[-1])
    atr = safe_float(daily["atr20_pct"].iloc[-1], 0.02)
    return cluster_corelines(candidates, cur, atr)


def detect_breakout(d: pd.DataFrame, zone: Dict[str, Any]) -> Dict[str, Any]:
    d = force_kline_schema(d)
    row = d.iloc[-1]
    pre = d.iloc[-2] if len(d) >= 2 else row
    close, open_, high, low = [safe_float(row[x]) for x in ["close", "open", "high", "low"]]
    line = safe_float(zone.get("confirm_line", zone.get("high", zone.get("center"))))
    zcenter = safe_float(zone.get("center", line))
    zlow = safe_float(zone.get("low", zcenter))
    body_top, body_bottom = max(open_, close), min(open_, close)
    body_above_ratio = 0.0
    if body_top > body_bottom and line > 0:
        body_above_ratio = max(0.0, min(1.0, (body_top - max(body_bottom, line)) / (body_top - body_bottom)))
    close_pos = safe_float(row.get("close_pos", 0.5))
    upper = safe_float(row.get("upper_shadow_ratio", 0))
    vr1 = safe_float(row.get("vr1", 0))
    volr = safe_float(row["volume"] / safe_float(row.get("vol_ma20", 0), row["volume"]), 1)
    pct_chg = safe_float(row.get("pct_chg", 0))
    gap_break = safe_float(row["low"]) > safe_float(pre["high"]) * 1.003 and close > line
    # 核心线标准不放宽：有效突破必须看核心带上沿/心理压力线，而不是中心线。
    effective = close > line * 1.002 and body_above_ratio >= 0.30 and close_pos >= 0.60 and upper <= 0.52
    score = 0.0; reasons = []
    if close > line * 1.006:
        score += 9; reasons.append("收盘明确站上心理压力线/核心上沿")
    elif close > line * 1.002:
        score += 6; reasons.append("收盘小幅站上心理压力线")
    elif high >= line and close >= zcenter:
        score += 2; reasons.append("盘中触及压力线但收盘未完全确认")
    if body_above_ratio >= 0.55:
        score += 7; reasons.append("实体大部分在压力线上方")
    elif body_above_ratio >= 0.30:
        score += 4; reasons.append("实体部分站上压力线")
    if close_pos >= 0.80:
        score += 5; reasons.append("收盘位置强")
    elif close_pos >= 0.62:
        score += 3; reasons.append("收盘位置尚可")
    if upper <= 0.25:
        score += 4; reasons.append("上影线短，非冲高回落")
    elif upper > 0.52:
        score -= 6; reasons.append("上影线偏长，有假突破风险")
    if 1.8 <= vr1 <= 2.5:
        score += 6; reasons.append("标准倍量突破")
    elif 1.25 <= vr1 <= 3.2 or 1.15 <= volr <= 3.8:
        score += 4; reasons.append("健康放量/温和放量")
    elif vr1 > 5 or volr > 6:
        score -= 5; reasons.append("极端爆量，防分歧派发")
    if gap_break:
        score += 4; reasons.append("跳空越过心理压力线")
    if pct_chg >= 3 and close > open_:
        score += 3; reasons.append("突破K为有效阳线")
    if high >= line and close < zcenter:
        score -= 9; reasons.append("冲线后收回核心带下方，假突破风险")
    return {"effective": bool(effective), "score": float(max(0, min(30, score))), "reasons": reasons[:10], "body_above_ratio": body_above_ratio, "close_pos": close_pos, "vr1": vr1, "volr": volr}


def scan_one(symbol: str, name: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    d = normalize_external_kline_df(df)
    if d is not None:
        d = force_kline_schema(d)
    if d is None or len(d) < 180:
        return None
    zones = find_coreline_zones(d)
    if not zones:
        return None
    close = safe_float(d["close"].iloc[-1])
    valid_zones = []
    for z in zones[:8]:
        line = safe_float(z.get("confirm_line", z.get("high")))
        if line <= 0:
            continue
        dist = line / close - 1 if close > 0 else 99
        # 核心压力线质量不放宽：必须至少B级、分数达标；放宽的是选股状态（贴近/临界/突破都可进深度）。
        if z["level"] in ["S", "A", "B"] and safe_float(z.get("score")) >= POJIE_MIN_CORELINE_SCORE and -0.08 <= dist <= 0.20:
            valid_zones.append(z)
    if not valid_zones:
        return None
    best_result = None
    for z in valid_zones[:4]:
        core_score = safe_float(z["score"])
        buildup = score_left_buildup(d, z)
        breakout = detect_breakout(d, z)
        rr = calc_space_rr(d, z, zones)
        risk = risk_filter(d, {"名称": name})
        if risk["hard_exclude"]:
            continue
        line = safe_float(z.get("confirm_line", z.get("high")))
        dist_to_line = line / close - 1 if close > 0 and line > 0 else 0
        # 放宽选股逻辑：贴近/临界/小突破也可以输出，但必须降低评级、不能冒充有效突破。
        proximity_bonus = 0
        proximity_reasons = []
        if -0.015 <= dist_to_line <= 0.025:
            proximity_bonus += 5; proximity_reasons.append("贴近心理压力线/临界状态")
        elif 0.025 < dist_to_line <= 0.08:
            proximity_bonus += 2; proximity_reasons.append("距离心理压力线不远，可观察")
        if safe_float(buildup["score"]) >= 21 and 0 <= dist_to_line <= 0.10:
            proximity_bonus += 3; proximity_reasons.append("线下蓄势较好，允许提前观察")
        total = core_score * 0.34 + safe_float(buildup["score"]) * (25 / 35) + safe_float(breakout["score"]) * (25 / 30) + safe_float(rr["score"]) + proximity_bonus + safe_float(risk["penalty"])
        if not breakout["effective"]:
            total = min(total, 78)  # 临界观察封顶
        total = max(0, min(100, total))
        if total >= 88 and breakout["effective"] and z["level"] == "S" and rr["rr"] >= 2.0:
            level = "S"
        elif total >= 80 and breakout["effective"]:
            level = "A"
        elif total >= 68:
            level = "B"
        elif total >= 58:
            level = "C"
        else:
            continue
        result = {
            "employee": "破界",
            "version": MODEL_VERSION,
            "symbol": symbol, "name": name,
            "date": str(pd.to_datetime(d["date"].iloc[-1]).date()),
            "close": round(close, 3),
            "signal_level": level,
            "score": round(float(total), 2),
            "coreline": round(safe_float(z["center"]), 3),
            "psychological_pressure_line": round(line, 3),
            "coreline_zone": [round(safe_float(z["low"]), 3), round(safe_float(z["high"]), 3)],
            "coreline_level": z["level"], "coreline_score": round(core_score, 2),
            "distance_to_pressure": round(dist_to_line, 4),
            "strategy_profile": POJIE_STRATEGY_PROFILE,
            "coreline_sources": z["sources"][:14], "coreline_timeframes": z["timeframes"],
            "buildup_score": round(safe_float(buildup["score"]), 2), "buildup_level": buildup["level"], "buildup_reasons": buildup["reasons"],
            "breakout_effective": bool(breakout["effective"]), "breakout_score": round(safe_float(breakout["score"]), 2), "breakout_reasons": breakout["reasons"],
            "proximity_reasons": proximity_reasons,
            "defense_price": round(safe_float(rr["defense"]), 3), "next_pressure": round(safe_float(rr["next_pressure"]), 3),
            "space": round(safe_float(rr["space"]), 4), "risk_distance": round(safe_float(rr["risk"]), 4), "rr": round(safe_float(rr["rr"]), 2),
            "space_reasons": rr["reasons"], "risk_reasons": risk["reasons"],
            "long_pos_250": round(safe_float(risk["long_pos_250"]), 4), "bias20": round(safe_float(risk["bias20"]), 4),
            "confirm_condition": "日线高级K实体站上心理压力线，或回踩心理压力线/突破K实体中位缩量止跌后再转强。",
            "giveup_condition": "放量长上影回落到心理压力线下方，或跌破突破K实底/交易防守位，破界失败。",
        }
        if best_result is None or result["score"] > best_result["score"]:
            best_result = result
    return best_result


def build_report(results: List[Dict[str, Any]], scanned: int, failed: int = 0, no_kline: int = 0) -> str:
    lines = []
    valid_scanned = max(0, int(scanned) - int(failed) - int(no_kline))
    not_selected = max(0, valid_scanned - len(results))
    lines.append("【破界战法2.0｜专业核心压力线版】")
    lines.append(f"生成时间：{now_bj()}")
    lines.append(f"扫描：{scanned}只，K线有效：{valid_scanned}只，入选：{len(results)}只，未入选：{not_selected}只，无K线/数据不足：{no_kline}只，异常失败：{failed}只")
    lines.append("核心线口径：大周期优先；最大量有效阳K、次高/收盘共振、实体顶底、上影/缺口/假突破、成交密集区共同定位；心理压力线=核心压力带上沿。")
    if results:
        summary = []
        for r in results:
            summary.append(f"{r['symbol']} {r['name']}({r['signal_level']} {r['score']}分 收盘{r['close']} 心理线{r.get('psychological_pressure_line', r.get('coreline'))} 距离{safe_float(r.get('distance_to_pressure')):.1%})")
        lines.append("")
        lines.append(f"本次选出 {len(results)} 只：" + "；".join(summary))
    else:
        lines.append("")
        lines.append("本次选出 0 只：没有股票同时满足专业核心压力线质量与破界/临界观察条件。")
    lines.append("")
    if not results:
        lines.append("今日暂无破界候选。")
        return "\n".join(lines)
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['symbol']} {r['name']}｜{r['signal_level']}｜{r['score']}分｜收盘{r['close']}｜心理压力线{r.get('psychological_pressure_line', r.get('coreline'))}｜距离{safe_float(r.get('distance_to_pressure')):.1%}")
        lines.append(f"   核心压力带：{r['coreline_zone']}｜中心{r['coreline']}｜{r['coreline_level']}级｜{r['coreline_score']}分｜周期{','.join(r['coreline_timeframes'])}")
        lines.append(f"   线源：{'；'.join(r['coreline_sources'][:6])}")
        lines.append(f"   蓄势：{r['buildup_level']} {r['buildup_score']}分｜{'；'.join(r['buildup_reasons'][:4])}")
        br = '有效突破' if r['breakout_effective'] else '临界/观察'
        extra = ('；' + '；'.join(r.get('proximity_reasons', [])[:2])) if r.get('proximity_reasons') else ''
        lines.append(f"   突破状态：{br} {r['breakout_score']}分｜{'；'.join(r['breakout_reasons'][:4])}{extra}")
        lines.append(f"   交易参数：防守{r['defense_price']}｜上方压力{r['next_pressure']}｜空间{r['space']:.1%}｜风险{r['risk_distance']:.1%}｜RR={r['rr']}")
        if r['risk_reasons']:
            lines.append(f"   风险：{'；'.join(r['risk_reasons'][:3])}")
        lines.append(f"   确认：{r['confirm_condition']}")
        lines.append(f"   放弃：{r['giveup_condition']}")
        lines.append("")
    return "\n".join(lines)

def main():
    args = parse_args()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 破界数据层原则：只读缓存；缓存命中不联网；缓存缺失直接跳过。
    os.environ.setdefault("USE_FULL_HISTORY_CACHE", "1")
    os.environ.setdefault("ALLOW_STALE_KLINE_CACHE", "1")
    os.environ.setdefault("POJIE_REMOTE_ON_CACHE_MISS", "0")
    os.environ.setdefault("POJIE_ALLOW_AKSHARE_KLINE", "0")
    os.environ.setdefault("POJIE_ALLOW_AKSHARE_STOCK_LIST", "0")
    # 硬覆盖：防止 workflow 里设置 KLINE_FALLBACK_AKSHARE=1 后，一号员工内部自动改用 AkShare 拉K线。
    if os.environ.get("POJIE_ALLOW_AKSHARE_KLINE", "0") != "1":
        os.environ["KLINE_FALLBACK_AKSHARE"] = "0"

    base = load_base_module(args.基础模型文件)

    # 破界战法默认只读缓存，不做BaoStock补拉；因此默认不登录BaoStock，节省时间并避免数据源压力。
    if os.environ.get("POJIE_REMOTE_ON_CACHE_MISS", "0") == "1" and hasattr(base, "baostock_login"):
        try:
            ok = base.baostock_login()
            print(f"破界：尝试调用一号员工 BaoStock 登录，ok={ok}")
        except Exception as e:
            print(f"破界：BaoStock登录异常但不阻断：{e}")
    else:
        print("破界：只读缓存模式，跳过BaoStock登录。")

    if args.模式 == "selfcheck":
        required = ["get_a_stock_list", "get_daily_kline"]
        missing = [x for x in required if not hasattr(base, x)]
        if missing:
            print(f"{MODEL_VERSION} 自检提醒：基础模型缺少 {missing}，但破界内置兜底仍可运行。")
        else:
            print(f"{MODEL_VERSION} 自检通过：基础模型入口存在。")
        return

    stock_list = get_stock_list_safe(base)
    stock_list = normalize_stock_list_df(stock_list, source="final")

    if stock_list is None or len(stock_list) == 0:
        write_empty_report("股票池为空：基础模型、AkShare、本地缓存、应急股票池均不可用，本次不强制失败。")
        return

    if args.最多股票数量 and args.最多股票数量 > 0:
        stock_list = stock_list.head(args.最多股票数量)

    print("破界战法2.0：K线模式=只读缓存；缓存命中不联网；缓存缺失直接跳过；BaoStock/AkShare默认禁用。")
    print(f"破界战法2.0：策略档位={POJIE_STRATEGY_PROFILE}，最低核心线分={POJIE_MIN_CORELINE_SCORE}，观察候选输出={POJIE_OUTPUT_OBSERVATION}")
    print(f"破界：POJIE_REMOTE_ON_CACHE_MISS={os.environ.get('POJIE_REMOTE_ON_CACHE_MISS', '1')}，POJIE_ALLOW_AKSHARE_KLINE={os.environ.get('POJIE_ALLOW_AKSHARE_KLINE', '0')}，KLINE_FALLBACK_AKSHARE={os.environ.get('KLINE_FALLBACK_AKSHARE', '0')}")
    print(f"破界：最终扫描股票池数量={len(stock_list)}")
    print(stock_list.head(20).to_string(index=False))

    results = []
    scanned = 0
    failed = 0
    no_kline = 0
    start = time.time()
    failed_items = []
    cache_miss_rows = []
    last_progress_ts = start

    rows = stock_list.to_dict("records")
    use_parallel = POJIE_WORKERS > 1 and len(rows) >= POJIE_PARALLEL_MIN_STOCKS

    if use_parallel:
        print(f"破界：启用并行只读缓存扫描 workers={POJIE_WORKERS}；缓存缺失股票直接记为无K线，不再补拉。")
        with ProcessPoolExecutor(max_workers=POJIE_WORKERS) as ex:
            futures = [ex.submit(scan_worker_cache_only, row) for row in rows]
            for fut in as_completed(futures):
                scanned += 1
                try:
                    item = fut.result()
                except Exception as e:
                    failed += 1
                    failed_items.append({"stage": "worker_exception", "error": str(e)[:200]})
                    item = {}
                merge_kline_stats(item.get("kline_stats", {}))
                status = item.get("status")
                if item.get("prefilter_skip"):
                    KLINE_STATS["prefilter_skip"] = KLINE_STATS.get("prefilter_skip", 0) + 1
                if status == "candidate":
                    r = item.get("result")
                    if r and safe_float(r.get("score")) >= args.最低分:
                        results.append(r)
                elif status == "cache_miss":
                    cache_miss_rows.append({"代码": item.get("code"), "名称": item.get("name"), "bs_code": item.get("bs_code")})
                elif status == "failed":
                    failed += 1
                    failed_items.append(item.get("failed_item", {"stage": "failed"}))

                now_ts = time.time()
                if (POJIE_PROGRESS_EVERY > 0 and scanned % POJIE_PROGRESS_EVERY == 0) or (now_ts - last_progress_ts >= POJIE_PROGRESS_SECONDS) or scanned == len(rows):
                    print(progress_text(scanned, len(rows), start, len(results), no_kline + len(cache_miss_rows), failed))
                    last_progress_ts = now_ts

        # 只读缓存模式：底层战法不做任何补拉。缓存缺失直接记录为无K线/缓存缺失。
        if cache_miss_rows:
            no_kline += len(cache_miss_rows)
            print(f"破界：并行阶段缓存缺失 {len(cache_miss_rows)} 只；只读缓存模式下不再BaoStock补拉。")
            for row in cache_miss_rows[:300]:
                failed_items.append({"code": row.get("代码"), "name": row.get("名称"), "bs_code": row.get("bs_code"), "stage": "cache_miss_readonly_no_remote"})
    else:
        print("破界：使用顺序只读缓存扫描模式。")
        for _, row in stock_list.iterrows():
            scanned += 1
            code = str(row.get("代码", row.get("code", ""))).zfill(6)
            bs_code = str(row.get("bs_code", ""))
            name = str(row.get("名称", row.get("name", "")))
            if not bs_code or bs_code == "nan":
                bs_code = bs_code_from_plain(code)
            if not bs_code:
                failed += 1
                failed_items.append({"code": code, "name": name, "stage": "bad_bs_code"})
                continue

            try:
                df = read_local_kline_cache_for_pojie(bs_code)
                df = normalize_external_kline_df(df)
                if df is None or len(df) < 120:
                    no_kline += 1
                    failed_items.append({"code": code, "name": name, "bs_code": bs_code, "stage": "no_kline_or_bad_columns"})
                    continue
                if not quick_trigger_prefilter(df):
                    KLINE_STATS["prefilter_skip"] = KLINE_STATS.get("prefilter_skip", 0) + 1
                    continue
                res = scan_one(code, name, df)
                if res and res["score"] >= args.最低分:
                    results.append(res)
            except Exception as e:
                failed += 1
                failed_items.append({"code": code, "name": name, "bs_code": bs_code, "stage": "scan_exception", "error": str(e)[:200]})
                if failed <= 20:
                    print(f"破界扫描失败：{code} {name} {e}")

            now_ts = time.time()
            if (POJIE_PROGRESS_EVERY > 0 and scanned % POJIE_PROGRESS_EVERY == 0) or (now_ts - last_progress_ts >= POJIE_PROGRESS_SECONDS) or scanned == len(stock_list):
                print(progress_text(scanned, len(stock_list), start, len(results), no_kline, failed))
                last_progress_ts = now_ts

    results = sorted(results, key=lambda x: (x["signal_level"] == "S", x["score"]), reverse=True)[:args.输出数量]
    payload = {
        "version": MODEL_VERSION,
        "generated_at": now_bj(),
        "scanned": scanned,
        "failed": failed,
        "no_kline": no_kline,
        "valid_kline": max(0, scanned - failed - no_kline),
        "not_selected": max(0, scanned - failed - no_kline - len(results)),
        "results": results,
        "failed_items_sample": failed_items[:200],
        "kline_stats": KLINE_STATS,
        "workers": POJIE_WORKERS,
        "fast_prefilter": POJIE_FAST_PREFILTER,
    }
    json_path = os.path.join(OUTPUT_DIR, "pojie_signals.json")
    txt_path = os.path.join(OUTPUT_DIR, "pojie_report.txt")
    failed_path = os.path.join(OUTPUT_DIR, "pojie_failed_items.json")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    report = build_report(results, scanned, failed=failed, no_kline=no_kline)
    if not results:
        report += "\n\n诊断：本次流程已跑通，但没有达到最低分的破界候选。"
        if no_kline > 0:
            report += f"\nK线无数据/不足：{no_kline}只。请优先检查 kline_cache 是否有有效CSV，或检查 BaoStock 补拉/一号员工缓存。"

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(report)
    with open(failed_path, "w", encoding="utf-8") as f:
        json.dump(failed_items, f, ensure_ascii=False, indent=2)

    stats_path = os.path.join(OUTPUT_DIR, "pojie_kline_stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(KLINE_STATS, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUTPUT_DIR, "pojie_remote_fetch_count.txt"), "w", encoding="utf-8") as f:
        f.write(str(int(KLINE_STATS.get("remote_success", 0) or 0)))

    print(report)
    print("破界K线汇总：" + json.dumps(KLINE_STATS, ensure_ascii=False))
    print(f"破界结果已保存：{json_path} / {txt_path}")
    print(f"破界异常/无K线清单已保存：{failed_path}")

    if args.发送Telegram and not args.不发送Telegram and hasattr(base, "send_telegram"):
        try:
            base.send_telegram(report)
        except Exception as e:
            print(f"Telegram发送失败，但不影响本次运行：{e}")

    # 尽量登出 BaoStock，但不强制。
    if hasattr(base, "baostock_logout"):
        try:
            base.baostock_logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
