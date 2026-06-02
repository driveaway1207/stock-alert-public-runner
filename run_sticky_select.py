import os
from pathlib import Path

import pandas as pd

from sticky_select import calc_one_sticky


COL_MAP = {
    '日期': 'date', '交易日期': 'date', 'trade_date': 'date',
    '代码': 'code', '股票代码': 'code', '证券代码': 'code',
    '名称': 'name', '股票名称': 'name', '证券名称': 'name',
    '开盘': 'open', '开盘价': 'open',
    '最高': 'high', '最高价': 'high',
    '最低': 'low', '最低价': 'low',
    '收盘': 'close', '收盘价': 'close',
    '成交量': 'volume', '成交额': 'amount',
}


def norm_cols(df):
    rename = {}
    for c in df.columns:
        s = str(c).strip()
        rename[c] = COL_MAP.get(s, s.lower())
    return df.rename(columns=rename)


def read_one_csv(path):
    try:
        df = pd.read_csv(path, encoding='utf-8-sig')
    except Exception:
        try:
            df = pd.read_csv(path)
        except Exception:
            return None

    df = norm_cols(df)
    need = ['date', 'open', 'high', 'low', 'close']
    if any(c not in df.columns for c in need):
        return None

    df = df.copy()
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    for c in ['open', 'high', 'low', 'close', 'volume', 'amount']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')

    df = df.dropna(subset=['date', 'open', 'high', 'low', 'close'])
    if df.empty:
        return None

    if 'code' not in df.columns:
        df['code'] = path.stem
    else:
        df['code'] = df['code'].astype(str).replace('', path.stem).fillna(path.stem)

    if 'name' not in df.columns:
        df['name'] = ''
    return df.sort_values('date')


def to_period(df, period):
    if period == 'daily':
        return df

    rule = 'W-FRI' if period == 'week' else 'M'
    agg = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'code': 'last',
        'name': 'last',
    }
    if 'volume' in df.columns:
        agg['volume'] = 'sum'
    if 'amount' in df.columns:
        agg['amount'] = 'sum'

    out = df.set_index('date').resample(rule).agg(agg).dropna(subset=['open', 'high', 'low', 'close'])
    out = out.reset_index()
    return out


def main():
    period = os.environ.get('STICKY_PERIOD', 'month').strip().lower()
    if period in ('monthly', 'm'):
        period = 'month'
    if period in ('weekly', 'w'):
        period = 'week'
    if period in ('day', 'd'):
        period = 'daily'
    if period not in ('daily', 'week', 'month'):
        raise SystemExit('STICKY_PERIOD must be daily/week/month')

    default_window = {'daily': 20, 'week': 16, 'month': 9}[period]
    window = int(os.environ.get('STICKY_WINDOW') or default_window)
    min_score = float(os.environ.get('STICKY_MIN_SCORE') or 70)
    topn = int(os.environ.get('STICKY_TOPN') or 100)

    cache_dir = Path('kline_cache')
    outputs = Path('outputs')
    outputs.mkdir(exist_ok=True)

    files = [p for p in cache_dir.rglob('*.csv') if not p.name.startswith('_')] if cache_dir.exists() else []
    rows = []

    for p in files:
        df = read_one_csv(p)
        if df is None or len(df) < max(30, window):
            continue
        dfp = to_period(df, period)
        if len(dfp) < max(8, window // 2):
            continue
        r = calc_one_sticky(dfp, window=window)
        if not r:
            continue
        r['code'] = str(dfp['code'].iloc[-1])
        r['name'] = str(dfp['name'].iloc[-1]) if 'name' in dfp.columns else ''
        rows.append(r)

    res = pd.DataFrame(rows)
    if not res.empty:
        res = res[(res['sticky_score'] >= min_score) & (res['sticky_state'] == 'STICKY_UP')]
        res = res.sort_values('sticky_score', ascending=False).head(topn).reset_index(drop=True)

    csv_path = outputs / f'sticky_select_{period}.csv'
    md_path = outputs / f'sticky_select_{period}.md'
    res.to_csv(csv_path, index=False, encoding='utf-8-sig')

    lines = []
    lines.append(f'# 粘合K线选股结果 - {period}')
    lines.append('')
    lines.append(f'- period: {period}')
    lines.append(f'- window: {window}')
    lines.append(f'- min_score: {min_score}')
    lines.append(f'- scanned_csv_files: {len(files)}')
    lines.append(f'- selected_count: {len(res)}')
    lines.append('')
    if res.empty:
        lines.append('没有选出符合条件的粘合上移股票。')
    else:
        show_cols = ['code', 'name', 'date', 'close', 'sticky_score', 'sticky_state', 'sticky_band_low', 'sticky_band_high', 'center_drift', 'low_drift', 'dislocation_ratio']
        show_cols = [c for c in show_cols if c in res.columns]
        lines.append(res[show_cols].to_markdown(index=False))
    md_path.write_text('\n'.join(lines), encoding='utf-8')

    print('\n'.join(lines))
    print(f'CSV saved: {csv_path}')
    print(f'MD saved: {md_path}')


if __name__ == '__main__':
    main()
