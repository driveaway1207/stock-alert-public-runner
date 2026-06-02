import numpy as np
import pandas as pd


def _mad(x):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return np.nan
    m = np.median(x)
    return np.median(np.abs(x - m))


def _clip01(x):
    return max(0.0, min(1.0, float(x)))


def _low_better(x, good, bad):
    if not np.isfinite(x):
        return 0.0
    if x <= good:
        return 1.0
    if x >= bad:
        return 0.0
    return (bad - x) / max(bad - good, 1e-9)


def calc_one_sticky(df, window=20):
    """
    纯K线粘合识别：只看K线是否黏在同一区域，不要求重心上移。

    核心：
    1）实体互相咬合；
    2）收盘集中；
    3）实体/收盘反复回到同一粘合带；
    4）长影线后能吸回；
    5）脱节少。
    """
    df = df.sort_values('date').tail(window).copy()
    if len(df) < max(8, window // 2):
        return None

    o = df['open'].astype(float).values
    h = df['high'].astype(float).values
    l = df['low'].astype(float).values
    c = df['close'].astype(float).values

    body_low = np.minimum(o, c)
    body_high = np.maximum(o, c)
    rng = np.maximum(h - l, 1e-9)
    price = np.nanmedian(c)
    if price <= 0:
        return None

    # 1）实体咬合：相邻实体交集/并集。十字小实体用价格底座防止虚高。
    overlaps = []
    for i in range(1, len(df)):
        inter = max(0.0, min(body_high[i], body_high[i - 1]) - max(body_low[i], body_low[i - 1]))
        union = max(body_high[i], body_high[i - 1]) - min(body_low[i], body_low[i - 1])
        overlaps.append(inter / max(union, price * 0.004))
    body_overlap = float(np.nanmean(overlaps)) if overlaps else 0.0
    body_overlap_score = _clip01(body_overlap)

    # 2）收盘集中：这是粘合的核心。越集中，分越高。
    close_mad_pct = _mad(c) / price
    close_cluster_score = _low_better(close_mad_pct, good=0.018, bad=0.070)

    # 3）粘合带：用收盘中位数做中心，带宽允许覆盖正常月K/周K粘合。
    # 不用重心上移，不用压力位，只问多数K线是否反复回到这一带。
    avg_range_pct = float(np.mean((h - l) / np.maximum(c, 1e-9)))
    band_half = max(price * 0.030, _mad(c) * 2.0, price * avg_range_pct * 0.20)
    band_low = price - band_half
    band_high = price + band_half

    close_in_band = float(np.mean((c >= band_low) & (c <= band_high)))
    body_touch_band = float(np.mean((body_high >= band_low) & (body_low <= band_high)))

    # 4）影线吸回：长影线后收盘回到粘合带，说明月内乱动但最终仍被吸回。
    upper_wick = (h - body_high) / rng
    lower_wick = (body_low - l) / rng
    long_wick = (upper_wick >= 0.35) | (lower_wick >= 0.35)
    if long_wick.sum() > 0:
        wick_recall = float(np.mean(((c >= band_low) & (c <= band_high))[long_wick]))
    else:
        wick_recall = 0.55

    # 5）脱节：相邻实体不咬合，且收盘/实体远离粘合带。
    dislocated = []
    for i in range(1, len(df)):
        entity_overlap = min(body_high[i], body_high[i - 1]) >= max(body_low[i], body_low[i - 1])
        close_far = abs(c[i] - price) / price > 0.055
        body_far = body_high[i] < band_low or body_low[i] > band_high
        dislocated.append((not entity_overlap and close_far) or body_far)
    dislocation_ratio = float(np.mean(dislocated)) if dislocated else 0.0
    no_dislocation_score = _low_better(dislocation_ratio, good=0.10, bad=0.45)

    # 6）重心变化只输出，不参与粘合筛选。
    half = len(df) // 2
    center_drift = np.median(c[half:]) / max(np.median(c[:half]), 1e-9) - 1
    low_drift = np.median(l[half:]) / max(np.median(l[:half]), 1e-9) - 1

    # 7）死股/出货标记。注意：标记只是降级，不因为“不上移”就淘汰。
    avg_body_pct = float(np.mean(np.abs(c - o) / np.maximum(c, 1e-9)))
    dead_flag = avg_range_pct < 0.012 and avg_body_pct < 0.005

    close_pos = (c - l) / rng
    upper_supply_count = int(np.sum((upper_wick >= 0.45) & (close_pos <= 0.45)))
    distribution_flag = bool(upper_supply_count >= max(5, len(df) // 2) and center_drift < -0.015)

    # 8）纯粘合分：不含上移分。
    score = (
        18 * body_overlap_score
        + 28 * close_cluster_score
        + 22 * close_in_band
        + 22 * body_touch_band
        + 6 * wick_recall
        + 4 * no_dislocation_score
    )

    score -= dislocation_ratio * 22
    if dead_flag:
        score = min(score, 58)
    if distribution_flag:
        score = min(score, 55)
    score = round(max(0, min(100, score)), 2)

    sticky_core_ok = (
        score >= 55
        and close_in_band >= 0.45
        and body_touch_band >= 0.55
        and dislocation_ratio <= 0.45
        and not distribution_flag
    )

    if dead_flag:
        state = 'DEAD_STICKY'
    elif distribution_flag:
        state = 'DISTRIBUTION_STICKY'
    elif dislocation_ratio > 0.45:
        state = 'LOOSE_VOLATILE'
    elif sticky_core_ok and score >= 75:
        state = 'STICKY_STRONG'
    elif sticky_core_ok:
        state = 'STICKY'
    elif score >= 50:
        state = 'STICKY_WEAK'
    else:
        state = 'NOT_STICKY'

    return {
        'date': df['date'].iloc[-1],
        'close': c[-1],
        'sticky_score': score,
        'sticky_state': state,
        'sticky_band_low': round(band_low, 3),
        'sticky_band_high': round(band_high, 3),
        'body_overlap': round(float(body_overlap), 3),
        'close_mad_pct': round(float(close_mad_pct), 4),
        'close_in_band': round(float(close_in_band), 3),
        'body_touch_band': round(float(body_touch_band), 3),
        'wick_recall': round(float(wick_recall), 3),
        'center_drift': round(float(center_drift), 4),
        'low_drift': round(float(low_drift), 4),
        'dislocation_ratio': round(float(dislocation_ratio), 3),
        'dead_flag': bool(dead_flag),
        'distribution_flag': bool(distribution_flag),
    }


def select_sticky_stocks(panel, window=20, min_score=55, topn=100):
    rows = []
    for code, g in panel.groupby('code'):
        r = calc_one_sticky(g, window=window)
        if r is None:
            continue
        r['code'] = code
        if 'name' in g.columns:
            r['name'] = g['name'].iloc[-1]
        rows.append(r)

    res = pd.DataFrame(rows)
    if res.empty:
        return res

    valid_states = ['STICKY_STRONG', 'STICKY']
    res = res[(res['sticky_score'] >= min_score) & (res['sticky_state'].isin(valid_states))]
    return res.sort_values('sticky_score', ascending=False).head(topn).reset_index(drop=True)
