# -*- coding: utf-8 -*-
"""单股K线粘合验证器。

只验证单只股票，不接入 stock_alert.py，不发 Telegram，不扫全市场。

当前粘合口径：
1. 粘合不是横盘震荡，和整体上涨、下跌、震荡趋势无关；
2. 核心是相邻K线实体之间经常互相重合、互相咬住；
3. 阳线经常低开后拉回，阴线经常高开后压回；
4. 上下影线互相咬合可以辅助，但不能替代实体粘合；
5. 最近N根K线里，至少约70%的相邻K线对要出现实体重合，才算强粘合。
"""

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

COL_MAP = {
    "日期": "date", "交易日期": "date", "trade_date": "date",
    "代码": "code", "股票代码": "code", "证券代码": "code",
    "名称": "name", "股票名称": "name", "证券名称": "name",
    "开盘": "open", "开盘价": "open",
    "最高": "high", "最高价": "high",
    "最低": "low", "最低价": "low",
    "收盘": "close", "收盘价": "close",
    "成交量": "volume", "成交额": "amount",
}


def mad(values):
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return np.nan
    med = np.median(arr)
    return float(np.median(np.abs(arr - med)))


def normalize_columns(df):
    return df.rename(columns={c: COL_MAP.get(str(c).strip(), str(c).strip().lower()) for c in df.columns})


def parse_date_series(s):
    def one(x):
        if pd.isna(x):
            return pd.NaT
        v = str(x).strip()
        if v.endswith(".0"):
            v = v[:-2]
        digits = "".join(ch for ch in v if ch.isdigit())
        if len(digits) == 8:
            return pd.to_datetime(f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}", errors="coerce")
        return pd.to_datetime(v.replace("/", "-").replace(".", "-"), errors="coerce")
    return s.map(one)


def read_csv(path):
    last_error = None
    df = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            df = pd.read_csv(path, encoding=enc)
            break
        except Exception as exc:
            last_error = exc
    if df is None:
        raise RuntimeError(f"读取CSV失败: {path}; {last_error}")

    df = normalize_columns(df)
    missing = [c for c in ("date", "open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise RuntimeError(f"CSV字段不完整，缺少 {missing}; 当前字段={list(df.columns)}")

    df = df.copy()
    df["date"] = parse_date_series(df["date"])
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date")
    if df.empty:
        raise RuntimeError(f"CSV清洗后为空: {path}")
    return df


def code_digits(x):
    m = re.search(r"(\d{6})", str(x))
    return m.group(1) if m else ""


def find_code_csv(root, code):
    root = Path(root)
    target = code_digits(code)
    if not root.exists():
        raise RuntimeError(f"找不到目录: {root}")
    if not target:
        raise RuntimeError(f"股票代码无效: {code}")

    csv_files = [p for p in root.rglob("*.csv") if not p.name.startswith("_")]
    name_hits = [p for p in csv_files if target in p.stem]
    if name_hits:
        return sorted(name_hits, key=lambda p: len(str(p)))[0]

    for p in csv_files:
        try:
            raw = pd.read_csv(p, nrows=5, encoding="utf-8-sig")
        except Exception:
            try:
                raw = pd.read_csv(p, nrows=5)
            except Exception:
                continue
        raw = normalize_columns(raw)
        if "code" in raw.columns and target in " ".join(raw["code"].dropna().astype(str).tolist()):
            return p
    raise RuntimeError(f"没有在 {root} 里找到代码 {target} 对应CSV")


def to_period(df, period):
    if period == "daily":
        return df.copy()
    rule = "W-FRI" if period == "week" else "ME"
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    if "amount" in df.columns:
        agg["amount"] = "sum"
    if "code" in df.columns:
        agg["code"] = "last"
    if "name" in df.columns:
        agg["name"] = "last"
    return df.set_index("date").resample(rule).agg(agg).dropna(subset=["open", "high", "low", "close"]).reset_index()


def adjusted_body(body_low, body_high, center):
    adj_low = []
    adj_high = []
    min_half = center * 0.0015
    for bl, bh in zip(body_low, body_high):
        mid = (bl + bh) / 2.0
        adj_low.append(min(bl, mid - min_half))
        adj_high.append(max(bh, mid + min_half))
    return np.asarray(adj_low, dtype=float), np.asarray(adj_high, dtype=float)


def calc_same_price_stack_ratio(body_low, body_high, center):
    lo = float(np.nanmin(body_low))
    hi = float(np.nanmax(body_high))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return 0.0
    grid = np.linspace(lo, hi, 200)
    coverage = np.zeros_like(grid)
    adj_low, adj_high = adjusted_body(body_low, body_high, center)
    for bl, bh in zip(adj_low, adj_high):
        coverage += ((grid >= bl) & (grid <= bh)).astype(float)
    return float(np.max(coverage) / max(len(body_low), 1))


def calc_sticky(df, window=9):
    k = df.tail(window).copy()
    if len(k) < max(6, window // 2):
        raise RuntimeError(f"周期K数量不足: 当前={len(k)}")

    o = k["open"].astype(float).to_numpy()
    h = k["high"].astype(float).to_numpy()
    l = k["low"].astype(float).to_numpy()
    c = k["close"].astype(float).to_numpy()

    body_low = np.minimum(o, c)
    body_high = np.maximum(o, c)
    body_mid = (body_low + body_high) / 2.0
    center = float(np.median(c))
    close_mad_pct = mad(c) / max(center, 1e-9)
    body_mid_mad_pct = mad(body_mid) / max(center, 1e-9)
    adj_body_low, adj_body_high = adjusted_body(body_low, body_high, center)

    body_pair_hits = []
    body_pair_strengths = []
    range_pair_strengths = []
    wick_pair_hits = []
    dislocated = []
    for i in range(1, len(k)):
        body_inter = max(0.0, min(adj_body_high[i], adj_body_high[i - 1]) - max(adj_body_low[i], adj_body_low[i - 1]))
        body_base = max(min(adj_body_high[i] - adj_body_low[i], adj_body_high[i - 1] - adj_body_low[i - 1]), center * 0.003)
        body_strength = body_inter / body_base
        body_pair_strengths.append(body_strength)
        body_pair_hits.append(body_inter > center * 0.001)

        range_inter = max(0.0, min(h[i], h[i - 1]) - max(l[i], l[i - 1]))
        range_base = max(min(h[i] - l[i], h[i - 1] - l[i - 1]), center * 0.01)
        range_strength = range_inter / range_base
        range_pair_strengths.append(range_strength)
        wick_pair_hits.append(range_strength >= 0.35)

        body_mid_far = abs(body_mid[i] - body_mid[i - 1]) / max(center, 1e-9) > 0.055
        dislocated.append((body_inter <= center * 0.001) and body_mid_far)

    body_pair_overlap_ratio = float(np.mean(body_pair_hits)) if body_pair_hits else 0.0
    body_pair_overlap_strength = float(np.mean(body_pair_strengths)) if body_pair_strengths else 0.0
    range_overlap_avg = float(np.mean(range_pair_strengths)) if range_pair_strengths else 0.0
    wick_interlock_ratio = float(np.mean(wick_pair_hits)) if wick_pair_hits else 0.0
    dislocation_ratio = float(np.mean(dislocated)) if dislocated else 0.0
    same_price_body_stack_ratio = calc_same_price_stack_ratio(body_low, body_high, center)

    reverse_open_hits = 0
    directional_count = 0
    eps = 0.002
    for i in range(1, len(k)):
        prev_close = c[i - 1]
        is_up = c[i] > o[i] * (1 + eps)
        is_down = c[i] < o[i] * (1 - eps)
        if is_up:
            directional_count += 1
            if o[i] <= prev_close * (1 + eps):
                reverse_open_hits += 1
        elif is_down:
            directional_count += 1
            if o[i] >= prev_close * (1 - eps):
                reverse_open_hits += 1
    reverse_open_ratio = reverse_open_hits / directional_count if directional_count else 0.0

    net_close_change = c[-1] / max(c[0], 1e-9) - 1
    half = len(k) // 2
    first_half_center = float(np.median(c[:half])) if half > 0 else float(np.median(c))
    second_half_center = float(np.median(c[half:])) if half > 0 else float(np.median(c))
    half_center_drift = second_half_center / max(first_half_center, 1e-9) - 1

    score = (
        45 * body_pair_overlap_ratio
        + 20 * min(body_pair_overlap_strength, 1.0)
        + 15 * reverse_open_ratio
        + 8 * min(range_overlap_avg, 1.0)
        + 5 * wick_interlock_ratio
        + 4 * max(0.0, 1.0 - body_mid_mad_pct / 0.08)
        + 3 * max(0.0, 1.0 - close_mad_pct / 0.10)
        - 20 * dislocation_ratio
    )
    score = round(max(0.0, min(100.0, score)), 2)

    core_sticky = (
        body_pair_overlap_ratio >= 0.70
        and body_pair_overlap_strength >= 0.18
        and reverse_open_ratio >= 0.30
        and dislocation_ratio <= 0.30
    )
    loose_sticky = (
        body_pair_overlap_ratio >= 0.55
        and body_pair_overlap_strength >= 0.12
        and reverse_open_ratio >= 0.20
        and dislocation_ratio <= 0.45
    )

    if core_sticky:
        state = "STICKY"
    elif loose_sticky:
        state = "WEAK_STICKY"
    else:
        state = "NOT_STICKY"

    return {
        "state": state,
        "score": score,
        "body_pair_overlap_ratio": round(body_pair_overlap_ratio, 3),
        "body_pair_overlap_strength": round(body_pair_overlap_strength, 3),
        "reverse_open_ratio": round(reverse_open_ratio, 3),
        "reverse_open_hits": int(reverse_open_hits),
        "directional_count": int(directional_count),
        "range_overlap_avg": round(range_overlap_avg, 3),
        "wick_interlock_ratio": round(wick_interlock_ratio, 3),
        "same_price_body_stack_ratio": round(same_price_body_stack_ratio, 3),
        "body_mid_mad_pct": round(float(body_mid_mad_pct), 4),
        "close_mad_pct": round(float(close_mad_pct), 4),
        "dislocation_ratio": round(dislocation_ratio, 3),
        "net_close_change": round(float(net_close_change), 4),
        "half_center_drift": round(float(half_center_drift), 4),
        "start_date": str(k["date"].iloc[0].date()),
        "end_date": str(k["date"].iloc[-1].date()),
        "rows": len(k),
    }


def main():
    parser = argparse.ArgumentParser(description="单股K线粘合验证器")
    parser.add_argument("--code", default="301376", help="股票代码，默认301376")
    parser.add_argument("--csv", default="", help="指定CSV路径；不填则从kline_cache里按代码查找")
    parser.add_argument("--root", default="kline_cache", help="K线缓存目录，默认kline_cache")
    parser.add_argument("--period", default="month", choices=["daily", "week", "month"], help="周期")
    parser.add_argument("--window", type=int, default=9, help="最近N根周期K，默认9")
    args = parser.parse_args()

    csv_path = Path(args.csv) if args.csv else find_code_csv(args.root, args.code)
    day = read_csv(csv_path)
    period_df = to_period(day, args.period)
    result = calc_sticky(period_df, window=args.window)

    print("=== 单股K线粘合验证 ===")
    print(f"code: {args.code}")
    print(f"csv: {csv_path}")
    print(f"raw_rows: {len(day)}")
    print(f"raw_date_range: {day['date'].min().date()} ~ {day['date'].max().date()}")
    print(f"period: {args.period}")
    print(f"period_rows: {len(period_df)}")
    print(f"window: {args.window}")
    for key, value in result.items():
        print(f"{key}: {value}")

    print("\n结论:")
    if result["state"] == "STICKY":
        print("符合粘合K线：70%以上相邻K线实体互相重合，阳线低开/阴线高开有配合；趋势方向不影响判断。")
    elif result["state"] == "WEAK_STICKY":
        print("弱粘合：实体有互相咬合，但没有达到强粘合标准。")
    else:
        print("不符合当前粘合规则。重点看 body_pair_overlap_ratio 是否达到0.70。")


if __name__ == "__main__":
    main()
