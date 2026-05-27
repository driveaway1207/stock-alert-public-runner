# -*- coding: utf-8 -*-
from __future__ import annotations
import json, math, os, re, time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
import pandas as pd
try:
    import requests
except Exception:
    requests = None

BOOT='EMPLOYEE5_PUBLIC_BOOT_20260527_CACHE_ONLY_NO_AK_V3'
ROOT=Path(__file__).resolve().parent
REPORT_DIR=ROOT/'employee5_reports'
TARGET=re.sub(r'\D','',os.getenv('EMPLOYEE5_TARGET_DATE') or datetime.now().strftime('%Y%m%d'))[:8]
TOP_N=int(os.getenv('EMPLOYEE5_TOP_N','3'))
MIN_ROWS=int(os.getenv('EMPLOYEE5_MIN_CACHE_ROWS','22'))
BOT=os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_TOKEN')
CHAT=os.getenv('TELEGRAM_CHAT_ID')
CACHE_DIRS=[ROOT/'employee5_kline_cache',ROOT/'kline_cache',ROOT/'data'/'kline_cache',ROOT/'cache'/'kline_cache',ROOT.parent/'kline_cache']

def ss(x:Any)->str: return '' if x is None else str(x).strip()
def sf(x:Any,d:float=0.0)->float:
    try:
        if x is None or pd.isna(x): return d
        return float(str(x).replace('%','').replace(',',''))
    except Exception: return d
def rd(x:Any,n:int=2)->float:
    v=sf(x); return 0.0 if math.isnan(v) or math.isinf(v) else round(v,n)
def code_of(p:Path)->str:
    m=re.search(r'(\d{6})',p.stem); return m.group(1) if m else ''
def valid(c:str)->bool: return c.startswith(('000','001','002','003','300','301','600','601','603','605','688','689','920','8','4'))
def nd(x:Any)->str:
    s=re.sub(r'\D','',ss(x)[:10]); return f'{s[:4]}-{s[4:6]}-{s[6:8]}' if len(s)>=8 else ss(x)[:10]
def norm(df:pd.DataFrame)->pd.DataFrame:
    if df is None or df.empty: return pd.DataFrame()
    mp={'日期':'date','交易日期':'date','date':'date','开盘':'open','open':'open','收盘':'close','close':'close','最高':'high','high':'high','最低':'low','low':'low','成交量':'volume','volume':'volume','成交额':'amount','amount':'amount','涨跌幅':'pct_chg','pct_chg':'pct_chg'}
    d=df.rename(columns={c:mp.get(str(c),mp.get(str(c).lower(),c)) for c in df.columns}).copy()
    if not set(['date','open','high','low','close']).issubset(d.columns): return pd.DataFrame()
    for c in ['open','high','low','close','volume','amount','pct_chg']:
        if c in d.columns: d[c]=d[c].map(sf)
    if 'volume' not in d.columns: d['volume']=0.0
    d['date']=d['date'].map(nd)
    d=d[(d.date!='')&(d.open>0)&(d.high>0)&(d.low>0)&(d.close>0)].sort_values('date').drop_duplicates('date')
    d=d[d.date<=nd(TARGET)]
    if 'pct_chg' not in d.columns or d.pct_chg.abs().sum()==0:
        prev=d.close.shift(1); d['pct_chg']=(d.close/prev-1.0)*100.0
    return d.reset_index(drop=True)
def rows(obj:Any)->Any:
    if isinstance(obj,list): return obj
    if isinstance(obj,dict):
        for k in ['rows','data','klines','kline','daily','history','records']:
            if k in obj: return obj[k]
    return []
def read(p:Path)->pd.DataFrame:
    try:
        s=p.suffix.lower()
        if s=='.json': return norm(pd.DataFrame(rows(json.loads(p.read_text(encoding='utf-8')))))
        if s in ['.csv','.txt']: return norm(pd.read_csv(p))
        if s in ['.pkl','.pickle']: return norm(pd.read_pickle(p))
    except Exception: return pd.DataFrame()
    return pd.DataFrame()
def load_cache()->Tuple[Dict[str,pd.DataFrame],Dict[str,Any]]:
    fs=[]
    for d in CACHE_DIRS:
        if d.exists():
            for pat in ['*.json','*.csv','*.txt','*.pkl','*.pickle']: fs+=list(d.rglob(pat))
    h={}; st={'cache_files':len(fs),'cache_hit':0,'cache_bad':0,'cache_short':0,'target_date':TARGET,'cache_dirs':[str(x) for x in CACHE_DIRS]}
    for i,p in enumerate(fs,1):
        c=code_of(p)
        if not valid(c): continue
        df=read(p)
        if df.empty: st['cache_bad']+=1; continue
        if len(df)<MIN_ROWS or df.iloc[-1].date.replace('-','')<TARGET: st['cache_short']+=1; continue
        h[c]=df; st['cache_hit']+=1
        if i%500==0: print(f'cache scan {i}/{len(fs)} hit={st["cache_hit"]}',flush=True)
    return h,st
def gain20(df:pd.DataFrame):
    if len(df)<22: return None
    a,b=df.iloc[-21],df.iloc[-1]; g=(sf(b.close)/sf(a.close)-1)*100 if sf(a.close) else 0
    return {'gain_20d':rd(g),'start_date':a.date,'end_date':b.date,'start_close':rd(a.close),'end_close':rd(b.close)}
def lim(c:str)->float:
    if c.startswith(('688','689','300','301')): return 20.0
    if c.startswith(('920','8','4')): return 30.0
    return 10.0
def pick(h:Dict[str,pd.DataFrame]):
    a=[]; b=[]
    for i,(c,df) in enumerate(h.items(),1):
        last=df.iloc[-1]; pct=sf(last.pct_chg); lp=lim(c)
        if pct>=lp-.35 or pct>=min(8.0,lp*.75): a.append({'code':c,'name':c,'date':last.date,'close':rd(last.close),'pct_chg':rd(pct),'sample_type':'涨停/近涨停' if pct>=lp-.35 else '极强上涨'})
        g=gain20(df)
        if g: b.append({'code':c,'name':c,**g})
        if i%500==0: print(f'sample scan {i}/{len(h)} A={len(a)} B={len(b)}',flush=True)
    A=pd.DataFrame(a); B=pd.DataFrame(b)
    if not A.empty: A=A.sort_values(['pct_chg','close'],ascending=[False,False]).head(TOP_N).reset_index(drop=True)
    if not B.empty: B=B.sort_values('gain_20d',ascending=False).head(TOP_N).reset_index(drop=True)
    return A,B
def core(df:pd.DataFrame)->Dict[str,Any]:
    if len(df)<45: return {'level':'数据不足','line':None,'text':'20日聚合K不足，不能硬画核心线。'}
    d=df.copy().reset_index(drop=True); d['grp']=[(len(d)-1-i)//20 for i in range(len(d))]
    bars=[]
    for gid,g in d.groupby('grp'):
        g=g.sort_index(); bars.append({'start':g.iloc[0].date,'end':g.iloc[-1].date,'open':sf(g.iloc[0].open),'high':sf(g.high.max()),'low':sf(g.low.min()),'close':sf(g.iloc[-1].close),'volume':sf(g.volume.sum())})
    k=pd.DataFrame(bars).sort_values('end').reset_index(drop=True); rng=(k.high-k.low).replace(0,pd.NA)
    k['body_ratio']=((k.close-k.open).abs()/rng).fillna(0); k['close_pos']=((k.close-k.low)/rng).fillna(0)
    c=k.iloc[:-1]; c=c[(c.close>c.open)&(c.body_ratio>=.25)&(c.close_pos>=.5)]
    if c.empty: return {'level':'疑似','line':None,'text':'没有找到足够清楚的大量阳K核心线。'}
    r=c.loc[c.volume.idxmax()]; line=rd(r.high)
    return {'level':'核心线候选','line':line,'text':f'核心线约{line}元，来自{r.start}~{r.end}的20日聚合大量阳K高点。'}
def build(h,st):
    A,B=pick(h) if h else (pd.DataFrame(),pd.DataFrame())
    lines=['# 五号员工：大涨/涨停归因学习报告','',f'- 日期：{TARGET}',f'- 启动指纹：{BOOT}','- 运行纪律：只读缓存，不逐票联网重拉历史K线，不荐股。',f'- 缓存命中：{st.get("cache_hit",0)} / 文件数 {st.get("cache_files",0)}','']
    lines+=['## 核心线状态分布']
    merged=pd.concat([A.assign(_group='A组'),B.assign(_group='B组')],ignore_index=True) if not(A.empty and B.empty) else pd.DataFrame()
    if merged.empty: lines.append('- 没有有效样本：这是缓存不足或未覆盖目标日，不代表市场没有涨停/大涨股。')
    else:
        for _,r in merged.iterrows():
            cl=core(h.get(r.code,pd.DataFrame())); lines.append(f'- {r._group} {r.code}：核心线约 {cl.get("line")} 元｜{cl.get("level")}')
    lines+=['','## A组：当日涨停/极强样本']
    lines += ['- A组为空：缓存中未反推出目标日涨停/极强样本。'] if A.empty else [f'{i+1}. {r.code}：{r.sample_type}｜涨幅{r.pct_chg}%｜收盘{r.close}元' for i,r in A.iterrows()]
    lines+=['','## B组：近20个交易日累计涨幅前三']
    lines += ['- B组为空：缓存中未能计算近20日涨幅。'] if B.empty else [f'{i+1}. {r.code}：{r.gain_20d}%｜{r.start_date}→{r.end_date}' for i,r in B.iterrows()]
    lines+=['','## 逐只故事归因']
    res=[]
    for group,pool in [('A组',A),('B组',B)]:
        for _,r in pool.iterrows():
            cl=core(h.get(r.code,pd.DataFrame())); lines += [f'### {r.code}｜{group}',f'- 核心线状态：{cl.get("level")}｜{cl.get("line")}元','',cl.get('text',''),'这只票只作为归因样本，不输出买入建议。','']
            res.append({'group':group,'code':r.code,'sample':r.to_dict(),'core_line':cl})
    return '\n'.join(lines), {'target_date':TARGET,'boot_id':BOOT,'cache_stats':st,'a_pool':A.to_dict('records') if not A.empty else [],'b_pool':B.to_dict('records') if not B.empty else [],'results':res,'research_only':True}
def send(text:str):
    if not BOT or not CHAT or requests is None: print(text[:1600]); return
    url=f'https://api.telegram.org/bot{BOT}/sendMessage'
    for part in [text[i:i+3600] for i in range(0,len(text),3600)]:
        try: requests.post(url,json={'chat_id':CHAT,'text':part,'disable_web_page_preview':True},timeout=30)
        except Exception as e: print('telegram failed:',e)
        time.sleep(.4)
def main():
    print(BOOT,flush=True); print(f'file={Path(__file__).resolve()}',flush=True); print(f'target_date={TARGET} network_hist_allowed=False',flush=True); print('cache_dirs='+' | '.join(str(x) for x in CACHE_DIRS),flush=True)
    h,st=load_cache(); print(f'cache_stats={st}',flush=True); md,payload=build(h,st)
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    for name,content in {'limit_up_research_report.md':md,'big_rise_story_report.md':md,'left_trace_research_report.md':md,'limit_up_research_report.json':json.dumps(payload,ensure_ascii=False,indent=2),'employee5_runtime_feedback.json':json.dumps({'boot_id':BOOT,'network_hist_allowed':False},ensure_ascii=False,indent=2)}.items(): (REPORT_DIR/name).write_text(content,encoding='utf-8')
    send(md[:9000]); print(f'Employee5 done. Reports: {REPORT_DIR}',flush=True)
if __name__=='__main__': main()
