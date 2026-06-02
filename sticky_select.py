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


def calc_one_sticky(df, window=20):
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

    overlaps = []
    for i in range(1, len(df)):
        inter = max(0, min(body_high[i], body_high[i - 1]) - max(body_low[i], body_low[i - 1]))
        union = max(body_high[i], body_high[i - 1]) - min(body_low[i], body_low[i - 1])
        overlaps.append(inter / max(union, price * 0.004))
    body_overlap = np.nanmean(overlaps)

    close_mad_pct = _mad(c) / price
    close_cluster_score = 1 - _clip01((close_mad_pct - 0.015) / 0.04)

    band_half = max(price * 0.025, _mad(c) * 1.8)
    band_low = price - band_half
    band_high = price + band_half

    close_in_band = np.mean((c >= band_low) & (c <= band_high))
    body_touch_band = np.mean((body_high >= band_low) & (body_low <= band_high))

    upper_wick = (h - body_high) / rng
    lower_wick = (body_low - l) / rng
    long_wick = (upper_wick >= 0.35) | (lower_wick >= 0.35)
    if long_wick.sum() > 0:
        wick_recall = np.mean(((c >= band_low) & (c <= band_high))[long_wick])
    else:
        wick_recall = 0.55

    half = len(df) // 2
    center_drift = np.median(c[half:]) / max(np.median(c[:half]), 1e-9) - 1
    low_drift = np.median(l[half:]) / max(np.median(l[:half]), 1e-9) - 1
    up_score = _clip01(center_drift / 0.08) if center_drift <= 0.18 else 0.4
    low_score = _clip01((low_drift + 0.005) / 0.06)
    drift_score = 0.65 * up_score + 0.35 * low_score

    dislocated = []
    for i in range(1, len(df)):
        entity_overlap = min(body_high[i], body_high[i - 1]) >= max(body_low[i], body_low[i - 1])
        close_far = abs(c[i] - price) / price > 0.045
        body_far = body_high[i] < band_low or body_low[i] > band_high
        dislocated.append((not entity_overlap and close_far) or body_far)
    dislocation_ratio = np.mean(dislocated)

    avg_range = np.mean((h - l) / c)
    avg_body = np.mean(np.abs(c - o) / c)
    dead_flag = avg_range < 0.018 and avg_body < 0.008 and center_drift < 0.015

    close_pos = (c - l) / rng
    upper_supply_count = np.sum((upper_wick >= 0.35) & (close_pos <= 0.55))
    distribution_flag = upper_supply_count >= max(4, len(df) // 3) and center_drift <= 0.01

    score = (
        20 * _clip01(body_overlap)
        + 20 * close_cluster_score
        + 15 * close_in_band
        + 15 * body_touch_band
        + 15 * wick_recall
        + 15 * drift_score
    )
    score -= dislocation_ratio * 35
    if dead_flag:
        score = min(score, 55)
    if distribution_flag:
        score = min(score, 55)
    if center_drift < -0.01:
        score = min(score, 60)
    score = round(max(0, min(100, score)), 2)

    if dead_flag:
        state = 'DEAD_STICKY'
    elif distribution_flag:
        state = 'DISTRIBUTION_STICKY'
    elif dislocation_ratio > 0.25:
        state = 'LOOSE_VOLATILE'
    elif score >= 70 and center_drift > 0.015 and low_drift > -0.005:
        state = 'STICKY_UP'
    elif score >= 60:
        state = 'STICKY_FLAT'
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
        'wick_recall': round(float(wick_recall), 3),
        'center_drift': round(float(center_drift), 4),
        'low_drift': round(float(low_drift), 4),
        'dislocation_ratio': round(float(dislocation_ratio), 3),
        'dead_flag': bool(dead_flag),
        'distribution_flag': bool(distribution_flag),
    }


def select_sticky_stocks(panel, window=20, min_score=70, topn=100):
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

    res = res[(res['sticky_score'] >= min_score) & (res['sticky_state'] == 'STICKY_UP')]
    return res.sort_values('sticky_score', ascending=False).head(topn).reset_index(drop=True)
