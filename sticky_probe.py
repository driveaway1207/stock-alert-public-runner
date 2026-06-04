# -*- coding: utf-8 -*-
"""单股K线粘合验证器。

只验证单只股票，不接入 stock_alert.py，不发 Telegram，不扫全市场。

强粘合口径：最近N根K线中，至少70%的K线实体覆盖同一个实体重合价位；
影线重合只做参考，不作为粘合核心。
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


def low_better(x, good, bad):
    if not np.isfinite(x):
        return 0.0
    if x <= good:
        return 1.0
    if x >= bad:
        return 0.0
    return float((bad - x) / max(bad - good, 1e-9))


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


def calc_body_stack_ratio(body_low, body_high, center):
    """返回最大实体覆盖比例：某一价格线被多少根K线实体同时覆盖。"""
    lo = float(np.nanmin(body_low))
    hi = float(np.nanmax(body_high))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return 0.0, 0.0

    grid = np.linspace(lo, hi, 200)
    coverage = np.zeros_like(grid)
    for bl, bh in zip(body_low, body_high):
        mid = (bl + bh) / 2.0
        min_half = center * 0.0015
        adj_low = min(bl, mid - min_half)
        adj_high = max(bh, mid + min_half)
        coverage += ((grid >= adj_low) & (grid <= adj_high)).astype(float)

    stack_ratio = float(np.max(coverage) / max(len(body_low), 1))
    dense_ratio = float(np.mean(coverage >= max(3, int(np.ceil(len(body_low) * 0.45)))))
    return stack_ratio, dense_ratio


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
    body_stack_ratio, body_dense_zone_ratio = calc_body_stack_ratio(body_low, body_high, center)

    body_center = float(np.median(body_mid))
    band_half = max(center * 0.020, mad(body_mid) * 1.6)
    band_low = body_center - band_half
    band_high = body_center + band_half

    close_in_band = float(np.mean((c >= band_low) & (c <= band_high)))
    body_touch_band = float(np.mean((body_high >= band_low) & (body_low <= band_high)))

    range_overlaps = []
    body_overlaps = []
    dislocated = []
    for i in range(1, len(k)):
        range_inter = max(0.0, min(h[i], h[i - 1]) - max(l[i], l[i - 1]))
        range_base = max(min(h[i] - l[i], h[i - 1] - l[i - 1]), center * 0.01)
        range_overlaps.append(range_inter / range_base)

        body_inter = max(0.0, min(body_high[i], body_high[i - 1]) - max(body_low[i], body_low[i - 1]))
        body_union = max(body_high[i], body_high[i - 1]) - min(body_low[i], body_low[i - 1])
        body_overlaps.append(body_inter / max(body_union, center * 0.004))

        body_far = body_high[i] < band_low or body_low[i] > band_high
        body_mid_far = abs(body_mid[i] - body_center) / max(center, 1e-9) > 0.050
        poor_body_overlap = body_inter <= center * 0.001 and body_mid_far
        dislocated.append(body_far or poor_body_overlap)

    range_overlap_avg = float(np.mean(range_overlaps)) if range_overlaps else 0.0
    body_overlap = float(np.mean(body_overlaps)) if body_overlaps else 0.0
    dislocation_ratio = float(np.mean(dislocated)) if dislocated else 0.0

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
    trend_push_flag = abs(net_close_change) >= 0.25 or (abs(net_close_change) >= 0.18 and abs(half_center_drift) >= 0.08)

    score = (
        50 * body_stack_ratio
        + 15 * low_better(body_mid_mad_pct, 0.018, 0.055)
        + 12 * body_touch_band
        + 8 * reverse_open_ratio
        + 5 * close_in_band
        + 5 * low_better(close_mad_pct, 0.025, 0.085)
        - 25 * dislocation_ratio
    )
    if trend_push_flag:
        score -= 18
    score = round(max(0.0, min(100.0, score)), 2)

    core_sticky = (
        body_stack_ratio >= 0.70
        and body_mid_mad_pct <= 0.035
        and body_touch_band >= 0.70
        and reverse_open_ratio >= 0.30
        and dislocation_ratio <= 0.25
        and not trend_push_flag
    )
    loose_sticky = (
        body_stack_ratio >= 0.60
        and body_mid_mad_pct <= 0.050
        and body_touch_band >= 0.60
        and reverse_open_ratio >= 0.25
        and dislocation_ratio <= 0.40
        and not trend_push_flag
    )

    if trend_push_flag:
        state = "TREND_PUSH"
    elif core_sticky:
        state = "STICKY"
    elif loose_sticky:
        state = "WEAK_STICKY"
    else:
        state = "NOT_STICKY"

    return {
        "state": state,
        "score": score,
        "band_low": round(band_low, 3),
        "band_high": round(band_high, 3),
        "body_stack_ratio": round(body_stack_ratio, 3),
        "body_dense_zone_ratio": round(body_dense_zone_ratio, 3),
        "body_mid_mad_pct": round(float(body_mid_mad_pct), 4),
        "body_overlap": round(body_overlap, 3),
        "range_overlap_avg": round(range_overlap_avg, 3),
        "reverse_open_ratio": round(reverse_open_ratio, 3),
        "reverse_open_hits": int(reverse_open_hits),
        "directional_count": int(directional_count),
        "net_close_change": round(float(net_close_change), 4),
        "half_center_drift": round(float(half_center_drift), 4),
        "trend_push_flag": bool(trend_push_flag),
        "close_mad_pct": round(float(close_mad_pct), 4),
        "close_in_band": round(close_in_band, 3),
        "body_touch_band": round(body_touch_band, 3),
        "dislocation_ratio": round(dislocation_ratio, 3),
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
        print("符合粘合K线：70%以上K线实体覆盖同一实体重合带，且不是单边趋势推进。")
    elif result["state"] == "WEAK_STICKY":
        print("弱粘合：实体有部分重合，但没有达到70%以上实体粘合的强标准。")
    elif result["state"] == "TREND_PUSH":
        print("不算粘合：最近窗口更像单边反弹/下跌推进，虽然K线可能有重叠，但不是实体原地互相咬合。")
    else:
        print("不符合当前粘合规则。强粘合必须看 body_stack_ratio 是否达到0.70。")


if __name__ == "__main__":
    main()
