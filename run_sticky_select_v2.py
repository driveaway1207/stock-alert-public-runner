import os
import re
import traceback
from datetime import datetime
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
    return df.rename(columns={c: COL_MAP.get(str(c).strip(), str(c).strip().lower()) for c in df.columns})


def parse_dates(s):
    def one(x):
        if pd.isna(x):
            return pd.NaT
        v = str(x).strip()
        if v.endswith('.0'):
            v = v[:-2]
        digits = ''.join(ch for ch in v if ch.isdigit())
        if len(digits) == 8:
            return pd.to_datetime(f'{digits[:4]}-{digits[4:6]}-{digits[6:8]}', errors='coerce')
        return pd.to_datetime(v.replace('/', '-').replace('.', '-'), errors='coerce')
    return s.map(one)


def normalize_code(raw):
    s = str(raw).strip().lower().replace('.csv', '')
    m = re.search(r'(?:sh|sz|bj)[\.\-_]?(\d{6})', s)
    code = m.group(1) if m else None
    if not code:
        m = re.search(r'(\d{6})', s)
        code = m.group(1) if m else None
    if not code:
        return None
    if code.startswith(('60', '68', '90')):
        return f'sh.{code}'
    if code.startswith(('00', '30', '20')):
        return f'sz.{code}'
    if code.startswith(('43', '83', '87', '88', '92')):
        return f'bj.{code}'
    return f'sh.{code}'


def read_csv_file(path):
    for enc in ('utf-8-sig', 'utf-8', 'gbk'):
        try:
            df = pd.read_csv(path, encoding=enc)
            break
        except Exception:
            df = None
    if df is None:
        return None
    df = norm_cols(df)
    if any(c not in df.columns for c in ['date', 'open', 'high', 'low', 'close']):
        return None
    df['date'] = parse_dates(df['date'])
    for c in ['open', 'high', 'low', 'close', 'volume', 'amount']:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['date', 'open', 'high', 'low', 'close']).sort_values('date')
    if df.empty:
        return None
    if 'code' not in df.columns:
        df['code'] = path.stem
    if 'name' not in df.columns:
        df['name'] = ''
    return df


def infer_code(path, df=None):
    cands = []
    if df is not None and 'code' in df.columns and not df.empty:
        vals = df['code'].dropna().astype(str)
        if not vals.empty:
            cands += [vals.iloc[-1], vals.iloc[0]]
    cands.append(path.stem)
    for x in cands:
        code = normalize_code(x)
        if code:
            return code
    return None


def to_period(df, period):
    if period == 'daily':
        return df
    rule = 'W-FRI' if period == 'week' else 'M'
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'code': 'last', 'name': 'last'}
    if 'volume' in df.columns:
        agg['volume'] = 'sum'
    if 'amount' in df.columns:
        agg['amount'] = 'sum'
    return df.set_index('date').resample(rule).agg(agg).dropna(subset=['open', 'high', 'low', 'close']).reset_index()


def score_df(df, period, window):
    dfp = to_period(df, period)
    if len(dfp) < max(8, window // 2):
        return None
    r = calc_one_sticky(dfp, window=window)
    if not r:
        return None
    r['code'] = str(dfp['code'].iloc[-1])
    r['name'] = str(dfp['name'].iloc[-1]) if 'name' in dfp.columns else ''
    return r


def baostock_fetch(bs, code, start_date):
    rs = bs.query_history_k_data_plus(
        code,
        'date,code,open,high,low,close,volume,amount',
        start_date=start_date,
        end_date=datetime.now().strftime('%Y-%m-%d'),
        frequency='d',
        adjustflag='3',
    )
    if rs.error_code != '0':
        return None
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=rs.fields)
    df['date'] = parse_dates(df['date'])
    for c in ['open', 'high', 'low', 'close', 'volume', 'amount']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['name'] = ''
    df = df.dropna(subset=['date', 'open', 'high', 'low', 'close']).sort_values('date')
    return df if not df.empty else None


def main():
    outputs = Path('outputs')
    outputs.mkdir(exist_ok=True)
    period = (os.environ.get('STICKY_PERIOD') or 'month').lower().strip()
    if period in ('m', 'monthly'):
        period = 'month'
    if period in ('w', 'weekly'):
        period = 'week'
    if period in ('d', 'day'):
        period = 'daily'
    window = int((os.environ.get('STICKY_WINDOW') or '').strip() or {'daily': 20, 'week': 16, 'month': 9}[period])
    min_score = float(os.environ.get('STICKY_MIN_SCORE') or 55)
    topn = int(os.environ.get('STICKY_TOPN') or 100)
    files = [p for p in Path('kline_cache').rglob('*.csv') if not p.name.startswith('_')]

    stats = {'files': len(files), 'read_ok': 0, 'cache_rows': 0, 'fallback_used': 0, 'fallback_seen': 0, 'fallback_rows': 0, 'fallback_empty': 0, 'errors': 0}
    rows = []
    examples = []

    for p in files:
        df = read_csv_file(p)
        if df is None:
            continue
        stats['read_ok'] += 1
        if len(examples) < 5:
            examples.append(f'{p.name}: rows={len(df)}, {df.date.min().date()}~{df.date.max().date()}, code={infer_code(p, df)}')
        try:
            r = score_df(df, period, window)
            if r is not None:
                rows.append(r)
                stats['cache_rows'] += 1
        except Exception:
            stats['errors'] += 1

    if not rows and period in ('month', 'week'):
        stats['fallback_used'] = 1
        import baostock as bs
        bs.login()
        seen = set()
        start_date = '2022-01-01' if period == 'month' else '2023-01-01'
        try:
            for i, p in enumerate(files, 1):
                df0 = read_csv_file(p)
                code = infer_code(p, df0)
                if not code or code in seen:
                    continue
                seen.add(code)
                stats['fallback_seen'] = len(seen)
                try:
                    df = baostock_fetch(bs, code, start_date)
                    if df is None:
                        stats['fallback_empty'] += 1
                        continue
                    r = score_df(df, period, window)
                    if r is not None:
                        rows.append(r)
                        stats['fallback_rows'] += 1
                except Exception:
                    stats['errors'] += 1
                if i % 200 == 0:
                    print(f'Fallback progress: file={i}/{len(files)}, seen={len(seen)}, period_rows={len(rows)}, empty={stats["fallback_empty"]}')
        finally:
            bs.logout()

    res = pd.DataFrame(rows)
    before = len(res)
    if not res.empty:
        valid = ['STICKY', 'STICKY_STRONG', 'STICKY_UP', 'STICKY_BASE', 'STICKY_FLAT']
        res = res[(res['sticky_score'] >= min_score) & (res['sticky_state'].isin(valid))]
        res = res.sort_values('sticky_score', ascending=False).head(topn).reset_index(drop=True)

    csv_path = outputs / f'sticky_select_{period}.csv'
    md_path = outputs / f'sticky_select_{period}.md'
    res.to_csv(csv_path, index=False, encoding='utf-8-sig')

    lines = [f'# 粘合K线选股结果 - {period}', '', f'- period: {period}', f'- window: {window}', f'- min_score: {min_score}', f'- scanned_csv_files: {len(files)}', f'- readable_csv_files: {stats["read_ok"]}', f'- fallback_used: {stats["fallback_used"]}', f'- candidates_before_filter: {before}', f'- selected_count: {len(res)}', '']
    if res.empty:
        lines.append('没有选出符合条件的粘合股票。请查看 sticky_select_debug.txt。')
    else:
        cols = ['code', 'name', 'date', 'close', 'sticky_score', 'sticky_state', 'sticky_band_low', 'sticky_band_high', 'close_in_band', 'body_touch_band', 'center_drift', 'low_drift', 'dislocation_ratio']
        cols = [c for c in cols if c in res.columns]
        lines.append(res[cols].to_markdown(index=False))
    md_path.write_text('\n'.join(lines), encoding='utf-8')
    debug = [f'{k}: {v}' for k, v in stats.items()] + examples + [f'before_filter: {before}', f'after_filter: {len(res)}']
    (outputs / 'sticky_select_debug.txt').write_text('\n'.join(debug), encoding='utf-8')
    print('\n'.join(lines))
    print('\nDebug:\n' + '\n'.join(debug))


if __name__ == '__main__':
    main()
