import os
import traceback
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
            return None, 'read_fail'

    df = norm_cols(df)
    need = ['date', 'open', 'high', 'low', 'close']
    if any(c not in df.columns for c in need):
        return None, 'missing_ohlc'

    df = df.copy()
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    for c in ['open', 'high', 'low', 'close', 'volume', 'amount']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')

    df = df.dropna(subset=['date', 'open', 'high', 'low', 'close'])
    if df.empty:
        return None, 'empty_after_clean'

    if 'code' not in df.columns:
        df['code'] = path.stem
    else:
        df['code'] = df['code'].astype(str).replace('', path.stem).fillna(path.stem)

    if 'name' not in df.columns:
        df['name'] = ''
    return df.sort_values('date'), 'ok'


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
    return out.reset_index()


def write_empty_report(outputs, period, window, min_score, file_count, msg):
    res = pd.DataFrame(columns=[
        'code', 'name', 'date', 'close', 'sticky_score', 'sticky_state',
        'sticky_band_low', 'sticky_band_high', 'close_in_band', 'body_touch_band', 'dislocation_ratio'
    ])
    csv_path = outputs / f'sticky_select_{period}.csv'
    md_path = outputs / f'sticky_select_{period}.md'
    res.to_csv(csv_path, index=False, encoding='utf-8-sig')
    lines = [
        f'# 粘合K线选股结果 - {period}',
        '',
        f'- period: {period}',
        f'- window: {window}',
        f'- min_score: {min_score}',
        f'- scanned_csv_files: {file_count}',
        '- selected_count: 0',
        '',
        msg,
    ]
    md_path.write_text('\n'.join(lines), encoding='utf-8')
    print('\n'.join(lines))


def main():
    outputs = Path('outputs')
    outputs.mkdir(exist_ok=True)

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
    raw_window = (os.environ.get('STICKY_WINDOW') or '').strip()
    window = int(raw_window) if raw_window else default_window
    min_score = float(os.environ.get('STICKY_MIN_SCORE') or 60)
    topn = int(os.environ.get('STICKY_TOPN') or 100)

    cache_dir = Path('kline_cache')
    files = [p for p in cache_dir.rglob('*.csv') if not p.name.startswith('_')] if cache_dir.exists() else []
    rows = []
    stats = {
        'files': len(files),
        'read_ok': 0,
        'read_fail': 0,
        'missing_ohlc': 0,
        'empty_after_clean': 0,
        'too_short_daily': 0,
        'too_short_period': 0,
        'calc_none': 0,
        'calc_error': 0,
    }

    if not files:
        write_empty_report(outputs, period, window, min_score, 0, '没有找到 kline_cache 缓存CSV。请先运行一号员工或缓存补拉任务，生成/恢复K线缓存后再跑本任务。')
        (outputs / 'sticky_select_debug.txt').write_text(str(stats), encoding='utf-8')
        return

    for p in files:
        df, status = read_one_csv(p)
        if status != 'ok':
            stats[status] = stats.get(status, 0) + 1
            continue
        stats['read_ok'] += 1

        if len(df) < max(30, window):
            stats['too_short_daily'] += 1
            continue
        try:
            dfp = to_period(df, period)
            if len(dfp) < max(8, window // 2):
                stats['too_short_period'] += 1
                continue
            r = calc_one_sticky(dfp, window=window)
            if not r:
                stats['calc_none'] += 1
                continue
            r['code'] = str(dfp['code'].iloc[-1])
            r['name'] = str(dfp['name'].iloc[-1]) if 'name' in dfp.columns else ''
            rows.append(r)
        except Exception:
            stats['calc_error'] += 1
            if stats['calc_error'] <= 5:
                with open(outputs / 'sticky_select_error.txt', 'a', encoding='utf-8') as f:
                    f.write(f'ERROR_FILE={p}\n')
                    f.write(traceback.format_exc())
                    f.write('\n')

    res = pd.DataFrame(rows)
    before_filter = len(res)
    if not res.empty:
        valid_states = ['STICKY', 'STICKY_STRONG', 'STICKY_UP', 'STICKY_BASE', 'STICKY_FLAT']
        res = res[(res['sticky_score'] >= min_score) & (res['sticky_state'].isin(valid_states))]
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
    lines.append(f'- readable_csv_files: {stats["read_ok"]}')
    lines.append(f'- candidates_before_filter: {before_filter}')
    lines.append(f'- selected_count: {len(res)}')
    lines.append('')
    if res.empty:
        lines.append('没有选出符合条件的粘合股票。可以先把 min_score 降到 55，或改跑 week/daily。')
    else:
        show_cols = ['code', 'name', 'date', 'close', 'sticky_score', 'sticky_state', 'sticky_band_low', 'sticky_band_high', 'close_in_band', 'body_touch_band', 'center_drift', 'low_drift', 'dislocation_ratio']
        show_cols = [c for c in show_cols if c in res.columns]
        lines.append(res[show_cols].to_markdown(index=False))
    md_path.write_text('\n'.join(lines), encoding='utf-8')

    debug_lines = [f'{k}: {v}' for k, v in stats.items()]
    debug_lines.append(f'before_filter: {before_filter}')
    debug_lines.append(f'after_filter: {len(res)}')
    (outputs / 'sticky_select_debug.txt').write_text('\n'.join(debug_lines), encoding='utf-8')

    print('\n'.join(lines))
    print('')
    print('Debug:')
    print('\n'.join(debug_lines))
    print(f'CSV saved: {csv_path}')
    print(f'MD saved: {md_path}')


if __name__ == '__main__':
    main()
