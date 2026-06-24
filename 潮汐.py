# -*- coding: utf-8 -*-
"""
潮汐.py

定位：
    Python 近似 WINNER + 严格复刻用户给出的通达信“潮汐”公式。
    用途不是直接买股，而是观察：
        1）当天低位初动股票数量；
        2）前三名申万一级/二级板块集中度；
        3）全部触发股票清单；
        4）市场潮水是否扩散、板块是否开始集中。

边界：
    通达信 WINNER() 内部算法不是公开标准；本文件使用成本分布近似法。
    潮汐公式主体不做优化，逐句复刻原始条件。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore", category=FutureWarning)

指标名 = "潮汐"
输出前缀 = "潮汐"
价格口径 = "前复权"

最少扫描K线数 = 500
新股最少K线数 = 120
WINNER最大使用K线数 = 1200
价格格子数 = 240

排除ST = True
排除北交所 = True
最低近20日平均成交额 = 30_000_000


# =============================================================================
# 通达信函数
# =============================================================================


def REF(x: pd.Series, n: int = 1) -> pd.Series:
    return x.shift(n)


def LLV(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).min()


def HHV(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).max()


def MA(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def COUNT(cond: pd.Series, n: int) -> pd.Series:
    return cond.astype(float).rolling(n, min_periods=n).sum()


def INTPART(x: pd.Series) -> pd.Series:
    return pd.Series(np.trunc(pd.to_numeric(x, errors="coerce")), index=x.index, dtype="float64")


def SMA_TDX(x: pd.Series, n: int, m: int) -> pd.Series:
    arr = pd.to_numeric(x, errors="coerce").to_numpy(dtype=float)
    out = np.full(len(arr), np.nan)
    prev = np.nan
    for i, v in enumerate(arr):
        if np.isnan(v):
            continue
        prev = v if np.isnan(prev) else (m * v + (n - m) * prev) / n
        out[i] = prev
    return pd.Series(out, index=x.index, dtype="float64")


def EMA_TDX(x: pd.Series, n: int) -> pd.Series:
    arr = pd.to_numeric(x, errors="coerce").to_numpy(dtype=float)
    out = np.full(len(arr), np.nan)
    alpha = 2.0 / (n + 1.0)
    prev = np.nan
    for i, v in enumerate(arr):
        if np.isnan(v):
            continue
        prev = v if np.isnan(prev) else alpha * v + (1.0 - alpha) * prev
        out[i] = prev
    return pd.Series(out, index=x.index, dtype="float64")


# =============================================================================
# 字段识别 / 数据读取
# =============================================================================

字段别名 = {
    "date": ["date", "trade_date", "datetime", "time", "日期", "交易日期", "交易日"],
    "code": ["code", "ts_code", "symbol", "证券代码", "股票代码", "代码", "股票"],
    "name": ["name", "stock_name", "证券名称", "股票名称", "名称"],
    "open": ["open", "Open", "OPEN", "开盘", "开盘价"],
    "high": ["high", "High", "HIGH", "最高", "最高价"],
    "low": ["low", "Low", "LOW", "最低", "最低价"],
    "close": ["close", "Close", "CLOSE", "收盘", "收盘价"],
    "volume": ["volume", "vol", "VOL", "成交量", "总手", "成交量(手)"],
    "amount": ["amount", "amt", "AMOUNT", "成交额", "成交金额", "成交额(元)", "成交金额(元)"],
    "turnover": ["turnover", "turnover_rate", "换手", "换手率", "turn", "TURN"],
    "industry_l1": ["industry_l1", "sw_l1", "sw1", "申万一级", "申万一级行业", "一级行业", "行业一级"],
    "industry_l2": ["industry_l2", "sw_l2", "sw2", "申万二级", "申万二级行业", "二级行业", "行业二级"],
    "industry": ["industry", "行业", "所属行业", "通达信行业", "tdx_industry"],
}


def 找列(df: pd.DataFrame, key: str) -> Optional[str]:
    low = {str(c).strip().lower(): c for c in df.columns}
    for a in 字段别名.get(key, []):
        if a.strip().lower() in low:
            return low[a.strip().lower()]
    return None


def 规范代码(x) -> str:
    if pd.isna(x):
        return ""
    s = str(x).strip().upper().replace("_", ".").replace("-", ".")
    m = re.search(r"(\d{6})\.(SH|SZ|BJ)", s)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    m = re.search(r"(SH|SZ|BJ)\.?([0-9]{6})", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    m = re.search(r"(\d{6})", s)
    if not m:
        return s
    num = m.group(1)
    if num.startswith(("6", "5", "9")):
        return f"{num}.SH"
    if num.startswith(("0", "2", "3")):
        return f"{num}.SZ"
    if num.startswith(("4", "8")):
        return f"{num}.BJ"
    return num


def 从文件名取代码(path: Path) -> str:
    return 规范代码(path.stem)


def 读表(path: Path) -> pd.DataFrame:
    suf = path.suffix.lower()
    if suf == ".csv":
        for enc in ["utf-8-sig", "utf-8", "gbk", "gb18030"]:
            try:
                return pd.read_csv(path, encoding=enc)
            except UnicodeDecodeError:
                continue
        return pd.read_csv(path)
    if suf in [".parquet", ".pq"]:
        return pd.read_parquet(path)
    if suf in [".pkl", ".pickle"]:
        return pd.read_pickle(path)
    if suf == ".feather":
        return pd.read_feather(path)
    raise ValueError(f"不支持文件格式：{path}")


def 标准化行情(df: pd.DataFrame, inferred_code: str = "") -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for k in ["date", "code", "name", "open", "high", "low", "close", "volume", "amount", "turnover", "industry_l1", "industry_l2", "industry"]:
        c = 找列(df, k)
        if c is not None:
            out[k] = df[c]
    if "code" not in out:
        out["code"] = inferred_code
    out["code"] = out["code"].apply(规范代码)
    if "date" not in out:
        raise ValueError("缺少日期字段")
    out["date"] = pd.to_datetime(out["date"].astype(str).str.replace("/", "-"), errors="coerce")
    out = out.dropna(subset=["date"])
    for k in ["open", "high", "low", "close", "volume"]:
        if k not in out:
            raise ValueError(f"缺少行情字段：{k}")
    for k in ["open", "high", "low", "close", "volume", "amount", "turnover"]:
        if k in out:
            out[k] = pd.to_numeric(out[k], errors="coerce")
    out = out.dropna(subset=["open", "high", "low", "close", "volume"])
    out = out[(out["close"] > 0) & (out["high"] >= out["low"]) & (out["volume"] > 0)]
    out = out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    for k in ["name", "industry_l1", "industry_l2", "industry"]:
        if k not in out:
            out[k] = ""
    if "amount" not in out:
        out["amount"] = np.nan
    if "turnover" not in out:
        out["turnover"] = np.nan
    return out


def 默认缓存目录() -> List[Path]:
    roots = []
    for e in ["KLINE_CACHE_DIR", "CACHE_DIR"]:
        if os.environ.get(e):
            roots.append(Path(os.environ[e]))
    cwd = Path.cwd()
    roots += [cwd / "kline_cache", cwd / "cache" / "kline_cache", cwd / "data" / "kline_cache", cwd / "cache", cwd / "data"]
    return [p for p in roots if p.exists() and p.is_dir()]


def 表文件(root: Path) -> List[Path]:
    suffix = {".csv", ".parquet", ".pq", ".pkl", ".pickle", ".feather"}
    bad = ["report", "summary", "result", "output", "readme", "报告", "输出", "审计"]
    ans = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in suffix:
            if any(x in p.name.lower() for x in bad):
                continue
            ans.append(p)
    return ans


def 读取行情缓存(cache_dir: Optional[str]) -> Dict[str, pd.DataFrame]:
    roots = [Path(cache_dir)] if cache_dir else 默认缓存目录()
    roots = [p for p in roots if p.exists() and p.is_dir()]
    if not roots:
        raise FileNotFoundError("没有找到行情缓存目录。可用 --cache-dir 指定。")
    stock_map: Dict[str, pd.DataFrame] = {}
    errors = []
    for root in roots:
        for path in 表文件(root):
            try:
                raw = 读表(path)
                if raw.empty:
                    continue
                if not all(找列(raw, x) for x in ["date", "high", "low", "close", "volume"]):
                    continue
                code_col = 找列(raw, "code")
                if code_col is not None and raw[code_col].nunique(dropna=True) > 1:
                    for c, g in raw.groupby(code_col):
                        code = 规范代码(c)
                        df = 标准化行情(g.copy(), code)
                        if len(df) >= 新股最少K线数:
                            stock_map[code] = df
                else:
                    code = 从文件名取代码(path)
                    df = 标准化行情(raw.copy(), code)
                    code = 规范代码(df["code"].iloc[-1] or code)
                    df["code"] = code
                    if len(df) >= 新股最少K线数:
                        stock_map[code] = df
            except Exception as e:
                errors.append(f"{path}: {e}")
                continue
        if stock_map:
            break
    if not stock_map:
        msg = "没有从缓存中读到有效日线数据。"
        if errors[:5]:
            msg += " 前5个错误：" + " | ".join(errors[:5])
        raise RuntimeError(msg)
    return stock_map


def 行业候选文件(cache_dir: Optional[str]) -> List[Path]:
    roots = []
    if cache_dir:
        roots.append(Path(cache_dir))
    roots += 默认缓存目录() + [Path.cwd()]
    keys = ["industry", "sw", "申万", "板块", "sector"]
    seen, files = set(), []
    for r in roots:
        if not r.exists() or not r.is_dir():
            continue
        for p in 表文件(r):
            name = p.name.lower()
            if any(k.lower() in name for k in keys) or any(k in p.name for k in keys):
                if p not in seen:
                    seen.add(p)
                    files.append(p)
    return files


def 读取行业表(industry_file: Optional[str], cache_dir: Optional[str]) -> pd.DataFrame:
    paths = [Path(industry_file)] if industry_file else 行业候选文件(cache_dir)
    for p in paths:
        if not p.exists() or not p.is_file():
            continue
        try:
            raw = 读表(p)
            code_col = 找列(raw, "code")
            if code_col is None:
                continue
            out = pd.DataFrame()
            out["code"] = raw[code_col].apply(规范代码)
            ncol = 找列(raw, "name")
            l1 = 找列(raw, "industry_l1") or 找列(raw, "industry")
            l2 = 找列(raw, "industry_l2")
            out["name_industry"] = raw[ncol].astype(str) if ncol else ""
            out["industry_l1_map"] = raw[l1].astype(str) if l1 else "未知"
            out["industry_l2_map"] = raw[l2].astype(str) if l2 else "未知"
            return out.drop_duplicates("code", keep="last")
        except Exception:
            continue
    return pd.DataFrame(columns=["code", "name_industry", "industry_l1_map", "industry_l2_map"])


# =============================================================================
# Python 近似 WINNER
# =============================================================================


def 换手率序列(df: pd.DataFrame) -> np.ndarray:
    if "turnover" in df and df["turnover"].notna().sum() > len(df) * 0.5:
        x = pd.to_numeric(df["turnover"], errors="coerce").to_numpy(dtype=float)
        if np.nanmedian(x) > 1.0:
            x = x / 100.0
        return np.clip(np.nan_to_num(x, nan=0.02), 0.002, 0.35)
    vol = pd.to_numeric(df["volume"], errors="coerce").astype(float)
    med = vol.rolling(20, min_periods=5).median().replace(0, np.nan)
    ratio = (vol / med).replace([np.inf, -np.inf], np.nan).fillna(1.0).to_numpy(dtype=float)
    # 没有真实换手率时，只用相对成交量估算筹码替换速度。
    return np.clip(0.018 * np.sqrt(np.clip(ratio, 0.25, 9.0)), 0.002, 0.085)


def 单日价格分布(grid: np.ndarray, o: float, h: float, l: float, c: float) -> np.ndarray:
    w = np.zeros_like(grid, dtype=float)
    if not np.isfinite([o, h, l, c]).all() or h <= 0 or l <= 0:
        return np.ones_like(grid) / len(grid)
    if h < l:
        h, l = l, h
    if h == l:
        idx = int(np.argmin(np.abs(grid - c)))
        w[idx] = 1.0
        return w
    in_range = (grid >= l) & (grid <= h)
    if in_range.any():
        w[in_range] += 0.35
    lo, hi = min(o, c), max(o, c)
    entity = (grid >= lo) & (grid <= hi)
    if entity.any():
        w[entity] += 0.45
    sigma = max((h - l) / 4.0, c * 0.003, 1e-6)
    gauss = np.exp(-0.5 * ((grid - c) / sigma) ** 2)
    w += 0.20 * gauss
    s = w.sum()
    if s <= 0:
        w[int(np.argmin(np.abs(grid - c)))] = 1.0
        s = 1.0
    return w / s


def 近似WINNER(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series]:
    n = len(df)
    low = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
    high = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
    close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
    open_ = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype=float)
    lo = float(np.nanmin(low)) * 0.98
    hi = float(np.nanmax(high)) * 1.02
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        nan = pd.Series(np.nan, index=df.index)
        return nan, nan, nan
    grid = np.linspace(lo, hi, 价格格子数)
    chips = np.zeros_like(grid, dtype=float)
    turns = 换手率序列(df)
    wc, w110, w90 = [], [], []
    for i in range(n):
        t = float(turns[i]) if np.isfinite(turns[i]) else 0.02
        chips *= (1.0 - t)
        prof = 单日价格分布(grid, open_[i], high[i], low[i], close[i])
        chips += prof * t
        total = chips.sum()
        if total <= 0:
            wc.append(np.nan); w110.append(np.nan); w90.append(np.nan)
            continue
        cdf = np.cumsum(chips) / total
        def win(price: float) -> float:
            idx = np.searchsorted(grid, price, side="right") - 1
            if idx < 0:
                return 0.0
            if idx >= len(cdf):
                return 1.0
            return float(cdf[idx])
        wc.append(win(close[i]))
        w110.append(win(close[i] * 1.1))
        w90.append(win(close[i] * 0.9))
    return pd.Series(wc, index=df.index), pd.Series(w110, index=df.index), pd.Series(w90, index=df.index)


# =============================================================================
# 潮汐公式
# =============================================================================


def 计算潮汐(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("date").reset_index(drop=True).copy()
    if len(df) > WINNER最大使用K线数:
        df = df.iloc[-WINNER最大使用K线数:].reset_index(drop=True)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float)

    llv13 = LLV(low, 13)
    hhv13 = HHV(high, 13)
    RSV = (close - llv13) / (hhv13 - llv13).replace(0, np.nan) * 100
    K1 = SMA_TDX(RSV, 3, 1)
    D1 = SMA_TDX(K1, 3, 1)
    KK = INTPART(K1)
    DD = INTPART(D1)
    TFXXS = KK + DD
    TGLXS = TFXXS - REF(TFXXS, 1)

    winner_close, winner_110, winner_090 = 近似WINNER(df)
    WINNER_A = EMA_TDX(winner_close * 65, 3)
    WINNER_B = EMA_TDX((winner_110 - winner_090) * 75, 3)
    ZZLKP = WINNER_A / (WINNER_A + WINNER_B).replace(0, np.nan) * 100

    first_tglxs = (TGLXS > 10) & (TGLXS > REF(TGLXS, 1))
    signal = (
        (TGLXS > 10)
        & (TGLXS > REF(TGLXS, 1))
        & (ZZLKP > REF(ZZLKP, 1))
        & (ZZLKP < 20)
        & ((ZZLKP - REF(ZZLKP, 1)) > 1.5)
        & ((close / LLV(low, 60)) < 1.15)
        & (((close - REF(close, 1)) / REF(close, 1)) < 0.05)
        & (COUNT(first_tglxs, 3) == 1)
        & (close > REF(high, 1))
        & (vol <= REF(vol, 1) * 3)
        & (vol > REF(vol, 1))
        & (vol > MA(vol, 20))
    )

    df["RSV"] = RSV
    df["K1"] = K1
    df["D1"] = D1
    df["KK"] = KK
    df["DD"] = DD
    df["TFXXS"] = TFXXS
    df["TGLXS"] = TGLXS
    df["WINNER_CLOSE_PY"] = winner_close
    df["WINNER_CLOSE_110_PY"] = winner_110
    df["WINNER_CLOSE_090_PY"] = winner_090
    df["WINNER_A"] = WINNER_A
    df["WINNER_B"] = WINNER_B
    df["ZZLKP"] = ZZLKP
    df["潮汐信号"] = signal.fillna(False)
    return df


# =============================================================================
# 扫描 / 过滤 / 报告
# =============================================================================


def 最新交易日(stock_map: Dict[str, pd.DataFrame]) -> pd.Timestamp:
    dates = []
    for df in stock_map.values():
        if not df.empty:
            dates.append(pd.to_datetime(df["date"].iloc[-1]).normalize())
    if not dates:
        raise RuntimeError("无法识别最新交易日。")
    vc = pd.Series(dates).value_counts()
    for d in sorted(vc.index, reverse=True):
        if vc[d] >= 50:
            return pd.Timestamp(d)
    return pd.Timestamp(max(dates))


def 股票池过滤(df: pd.DataFrame) -> Tuple[bool, str]:
    code = str(df["code"].iloc[-1])
    name = str(df.get("name", pd.Series([""])).iloc[-1] or "")
    if 排除北交所 and code.endswith(".BJ"):
        return False, "北交所"
    if 排除ST and ("ST" in name.upper() or "退" in name):
        return False, "ST/退市风险名称"
    if len(df) < 新股最少K线数:
        return False, "上市时间过短"
    if len(df) < 最少扫描K线数:
        return False, "历史K线不足500根"
    if "amount" in df and df["amount"].notna().sum() >= 20:
        avg_amt = float(df["amount"].tail(20).mean())
        if np.isfinite(avg_amt) and avg_amt < 最低近20日平均成交额:
            return False, "近20日平均成交额过低"
    return True, "通过"


def 补行业(row: dict, df: pd.DataFrame, industry: pd.DataFrame) -> dict:
    code = row["code"]
    name = row.get("name", "")
    l1 = str(df.get("industry_l1", pd.Series([""])).iloc[-1] or "")
    l2 = str(df.get("industry_l2", pd.Series([""])).iloc[-1] or "")
    if industry is not None and not industry.empty:
        hit = industry[industry["code"] == code]
        if not hit.empty:
            h = hit.iloc[-1]
            if not name:
                name = str(h.get("name_industry", "") or "")
            if not l1:
                l1 = str(h.get("industry_l1_map", "") or "")
            if not l2:
                l2 = str(h.get("industry_l2_map", "") or "")
    row["name"] = name
    row["申万一级"] = l1 if l1 else "未知"
    row["申万二级"] = l2 if l2 else "未知"
    return row


def 扫描潮汐(stock_map: Dict[str, pd.DataFrame], industry: pd.DataFrame, target_date: pd.Timestamp, pool_filter: bool) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rows, audit = [], []
    target_date = pd.to_datetime(target_date).normalize()
    for code, raw in stock_map.items():
        try:
            df = raw.copy().sort_values("date").reset_index(drop=True)
            df["date"] = pd.to_datetime(df["date"])
            last_date = pd.to_datetime(df["date"].iloc[-1]).normalize()
            if last_date != target_date:
                audit.append({"code": code, "status": "跳过", "reason": "未更新到目标交易日"})
                continue
            if pool_filter:
                ok, reason = 股票池过滤(df)
                if not ok:
                    audit.append({"code": code, "status": "过滤", "reason": reason})
                    continue
            calc = 计算潮汐(df)
            last = calc.iloc[-1]
            sig = bool(last.get("潮汐信号", False))
            audit.append({"code": code, "status": "已扫描", "reason": "触发" if sig else "未触发"})
            if sig:
                row = {
                    "date": pd.to_datetime(last["date"]).strftime("%Y-%m-%d"),
                    "code": code,
                    "name": str(last.get("name", "") or ""),
                    "close": float(last["close"]),
                    "volume": float(last["volume"]),
                    "amount": float(last["amount"]) if pd.notna(last.get("amount", np.nan)) else np.nan,
                    "TGLXS": float(last["TGLXS"]),
                    "ZZLKP": float(last["ZZLKP"]),
                    "WINNER_CLOSE_PY": float(last["WINNER_CLOSE_PY"]),
                }
                rows.append(补行业(row, df, industry))
        except Exception as e:
            audit.append({"code": code, "status": "错误", "reason": str(e)[:300]})
    selected = pd.DataFrame(rows)
    if not selected.empty:
        selected = selected.sort_values(["申万一级", "申万二级", "code"]).reset_index(drop=True)
    return selected, pd.DataFrame(audit)


def 集中度表(selected: pd.DataFrame, col: str, n: int = 3) -> pd.DataFrame:
    if selected.empty or col not in selected:
        return pd.DataFrame(columns=["rank", "board", "count", "ratio"])
    total = len(selected)
    x = selected[col].fillna("未知").replace("", "未知").value_counts().head(n).reset_index()
    x.columns = ["board", "count"]
    x["rank"] = np.arange(1, len(x) + 1)
    x["ratio"] = x["count"] / total
    return x[["rank", "board", "count", "ratio"]]


def 百分比(x: float) -> str:
    return f"{x * 100:.2f}%" if pd.notna(x) else "0.00%"


def 生成报告(selected: pd.DataFrame, target_date: pd.Timestamp, audit: pd.DataFrame) -> str:
    date_str = pd.to_datetime(target_date).strftime("%Y-%m-%d")
    total = len(selected)
    scanned = int((audit["status"] == "已扫描").sum()) if not audit.empty else 0
    filtered = int((audit["status"] == "过滤").sum()) if not audit.empty else 0
    skipped = int((audit["status"] == "跳过").sum()) if not audit.empty else 0
    errors = int((audit["status"] == "错误").sum()) if not audit.empty else 0
    lines = [f"【{指标名}】", "", f"日期：{date_str}", f"今日触发：{total}只", f"扫描通过：{scanned}只；外层过滤：{filtered}只；未更新跳过：{skipped}只；错误：{errors}只", ""]
    lines.append("前三集中板块（申万一级）：")
    t1 = 集中度表(selected, "申万一级", 3)
    if t1.empty:
        lines.append("无")
    else:
        for _, r in t1.iterrows():
            lines.append(f"{int(r['rank'])}. {r['board']}：{int(r['count'])}只，占比{百分比(float(r['ratio']))}")
    lines.append("")
    lines.append("前三集中板块（申万二级）：")
    t2 = 集中度表(selected, "申万二级", 3)
    if t2.empty:
        lines.append("无")
    else:
        for _, r in t2.iterrows():
            lines.append(f"{int(r['rank'])}. {r['board']}：{int(r['count'])}只，占比{百分比(float(r['ratio']))}")
    lines.append("")
    lines.append("全部股票：")
    if selected.empty:
        lines.append("无触发股票。")
    else:
        for i, r in selected.reset_index(drop=True).iterrows():
            lines.append(f"{i+1}. {r['code']} {r.get('name','')}｜{r.get('申万一级','未知')}/{r.get('申万二级','未知')}｜收盘{float(r['close']):.2f}｜TGLXS {float(r['TGLXS']):.2f}｜ZZLKP {float(r['ZZLKP']):.2f}")
    lines += ["", "说明：", "- 潮汐公式主体严格对应原通达信条件。", "- WINNER 为 Python 成本分布近似，不是通达信内置 WINNER 的逐点复制。", "- 本报告用于观察市场低位初动扩散和板块集中度，不等同于买入建议。"]
    return "\n".join(lines)


def 保存输出(selected: pd.DataFrame, audit: pd.DataFrame, report: str, target_date: pd.Timestamp, out_dir: str) -> Tuple[Path, Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ds = pd.to_datetime(target_date).strftime("%Y%m%d")
    sig = out / f"{输出前缀}_{ds}_信号.csv"
    md = out / f"{输出前缀}_{ds}_报告.md"
    au = out / f"{输出前缀}_{ds}_审计.csv"
    selected.to_csv(sig, index=False, encoding="utf-8-sig")
    audit.to_csv(au, index=False, encoding="utf-8-sig")
    md.write_text(report, encoding="utf-8")
    return sig, md, au


def 发送Telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(f"[{指标名}] Telegram token/chat_id 不完整，跳过发送。")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks, cur = [], ""
    for line in text.splitlines():
        cand = cur + ("\n" if cur else "") + line
        if len(cand) > 3600:
            if cur:
                chunks.append(cur)
            cur = line
        else:
            cur = cand
    if cur:
        chunks.append(cur)
    ok = True
    for i, chunk in enumerate(chunks, 1):
        payload = {"chat_id": chat_id, "text": chunk if len(chunks) == 1 else f"{chunk}\n\n（{i}/{len(chunks)}）", "disable_web_page_preview": True}
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code >= 300:
                ok = False
                print(f"[{指标名}] Telegram发送失败：HTTP {r.status_code} {r.text[:200]}")
        except Exception as e:
            ok = False
            print(f"[{指标名}] Telegram发送异常：{e}")
    if ok:
        print(f"[{指标名}] Telegram已发送。")
    return ok


# =============================================================================
# CLI
# =============================================================================


def 参数(argv=None):
    p = argparse.ArgumentParser(description="潮汐：低位初动市场温度与板块集中度指标")
    p.add_argument("--cache-dir", default=None, help="行情缓存目录；不填则自动寻找 kline_cache 等目录。")
    p.add_argument("--industry-file", default=None, help="申万行业映射表；不填则自动寻找。")
    p.add_argument("--out-dir", default="./潮汐输出", help="输出目录。")
    p.add_argument("--date", default="latest", help="目标交易日，YYYY-MM-DD；latest=缓存最新交易日。")
    p.add_argument("--no-pool-filter", action="store_true", help="关闭外层股票池过滤，不改变潮汐公式。")
    p.add_argument("--print-report", action="store_true", help="控制台打印完整报告。")
    p.add_argument("--send-telegram", action="store_true", help="发送潮汐报告到 Telegram。")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = 参数(argv)
    print(f"[{指标名}] 读取行情缓存...")
    stock_map = 读取行情缓存(args.cache_dir)
    print(f"[{指标名}] 读取到 {len(stock_map)} 只股票行情。")
    print(f"[{指标名}] 读取申万行业映射...")
    industry = 读取行业表(args.industry_file, args.cache_dir)
    print(f"[{指标名}] 行业映射：{len(industry)} 条。")
    target = 最新交易日(stock_map) if str(args.date).lower() == "latest" else pd.to_datetime(args.date)
    print(f"[{指标名}] 目标交易日：{pd.to_datetime(target).strftime('%Y-%m-%d')}")
    selected, audit = 扫描潮汐(stock_map, industry, target, pool_filter=not args.no_pool_filter)
    report = 生成报告(selected, target, audit)
    sig, md, au = 保存输出(selected, audit, report, target, args.out_dir)
    print(f"[{指标名}] 今日触发：{len(selected)}只")
    print(f"[{指标名}] 信号清单：{sig}")
    print(f"[{指标名}] 推送报告：{md}")
    print(f"[{指标名}] 扫描审计：{au}")
    if args.print_report:
        print("\n" + report)
    send_env = str(os.environ.get("CHAOXI_SEND_TELEGRAM") or "").strip().lower()
    if args.send_telegram or send_env in {"1", "true", "yes", "on", "发送", "是"}:
        发送Telegram(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
