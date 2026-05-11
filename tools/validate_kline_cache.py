# -*- coding: utf-8 -*-
"""
A股K线缓存验收脚本 V16.4
只读取 kline_cache，不联网，不改缓存。
输出 model_usable_universe，用于一号员工只读缓存股票池。
"""
import os, json
from datetime import datetime
import pandas as pd
import numpy as np

KLINE_CACHE_DIR = "kline_cache"
OUT_DIR = "outputs"
STATUS_META_PATH = os.path.join(KLINE_CACHE_DIR, "_full_history_status.csv")
os.makedirs(OUT_DIR, exist_ok=True)

EXCLUDE_CODES = {x.strip().zfill(6) for x in os.getenv("EXCLUDE_MODEL_CODES", "600415,603407").split(",") if x.strip()}
MODEL_MIN_ROWS = int(os.getenv("MODEL_MIN_ROWS", "250"))
MAX_LATEST_GAP_DAYS = int(os.getenv("MAX_LATEST_GAP_DAYS", "15"))

TARGET_TRADE_DATE_ENV = os.getenv("TARGET_TRADE_DATE", "").strip()
TARGET_TRADE_DATE = None

def log(msg):
    print(msg, flush=True)

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def stock_code(code):
    code=str(code).zfill(6)
    return ("SH." if code.startswith("6") else "SZ.") + code

def market_name(code):
    return "沪市" if str(code).zfill(6).startswith("6") else "深市"

def normalize_daily_columns(df):
    if df is None or df.empty:
        return None
    rename_map = {
        "日期":"date","交易日期":"date","trade_date":"date","Date":"date","datetime":"date","time":"date",
        "开盘":"open","开盘价":"open","Open":"open",
        "收盘":"close","收盘价":"close","Close":"close",
        "最高":"high","最高价":"high","High":"high",
        "最低":"low","最低价":"low","Low":"low",
        "成交量":"volume","成交量(手)":"volume","vol":"volume","Volume":"volume",
        "成交额":"amount","成交额(元)":"amount","turnover":"amount","Amount":"amount",
    }
    d=df.rename(columns={c:rename_map.get(c,c) for c in df.columns}).copy()
    need=["date","open","high","low","close","volume"]
    if not all(c in d.columns for c in need):
        return None
    if "amount" not in d.columns:
        d["amount"]=0
    d=d[["date","open","high","low","close","volume","amount"]].copy()
    d["date"]=pd.to_datetime(d["date"], errors="coerce")
    for c in ["open","high","low","close","volume","amount"]:
        d[c]=pd.to_numeric(d[c], errors="coerce")
    d=d.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return None if d.empty else d

def read_cache(code):
    p=os.path.join(KLINE_CACHE_DIR, f"{str(code).zfill(6)}.csv")
    if not os.path.exists(p):
        return None
    try:
        return normalize_daily_columns(pd.read_csv(p))
    except Exception:
        return None

def load_status_meta():
    if not os.path.exists(STATUS_META_PATH):
        return {}
    try:
        df=pd.read_csv(STATUS_META_PATH, dtype={"code":str})
        if df.empty or "code" not in df.columns:
            return {}
        df["code"]=df["code"].astype(str).str.zfill(6)
        return {str(r["code"]).zfill(6): dict(r) for _,r in df.iterrows()}
    except Exception as e:
        log(f"[WARN] 读取状态文件失败: {repr(e)}")
        return {}

def load_target_trade_date():
    """优先使用workflow传入的目标交易日；否则读取每日增量更新状态。"""
    global TARGET_TRADE_DATE
    raw = TARGET_TRADE_DATE_ENV
    if not raw:
        state_path = os.path.join(OUT_DIR, "daily_kline_update_state.json")
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    raw = json.load(f).get("目标交易日", "")
            except Exception:
                raw = ""
    try:
        TARGET_TRADE_DATE = pd.to_datetime(raw).date() if raw else None
    except Exception:
        TARGET_TRADE_DATE = None
    if TARGET_TRADE_DATE:
        log(f"[目标交易日过滤] TARGET_TRADE_DATE={TARGET_TRADE_DATE}")
    else:
        log("[目标交易日过滤] 未提供目标交易日，仅按自然日新鲜度验收。")


def latest_gap_days(df):
    if df is None or df.empty: return ""
    return int((datetime.now().date()-pd.to_datetime(df["date"].max()).date()).days)

def max_calendar_gap_days(df):
    if df is None or df.empty or len(df)<2: return ""
    gaps=pd.to_datetime(df["date"]).sort_values().diff().dt.days.dropna()
    return "" if gaps.empty else int(gaps.max())

def first_bad_date(df, mask):
    bad=df[mask]
    if bad.empty: return ""
    return bad.iloc[0]["date"].strftime("%Y-%m-%d")

def positive_mask(df):
    return (df["open"]>0)&(df["high"]>0)&(df["low"]>0)&(df["close"]>0)

def first_positive_date(df):
    g=df[positive_mask(df)]
    return "" if g.empty else g.iloc[0]["date"].strftime("%Y-%m-%d")

def infer_volume_unit_and_break(df):
    if df is None or df.empty: return "", "否", ""
    d=df[positive_mask(df)&(df["volume"]>0)&(df["amount"]>0)].copy()
    if len(d)<60: return "样本不足", "否", ""
    ratio=(d["amount"]/(d["volume"]*d["close"])).replace([np.inf,-np.inf], np.nan).dropna()
    ratio=ratio[(ratio>0)&(ratio<100000)]
    if len(ratio)<60: return "样本不足", "否", ""
    med=float(ratio.median())
    unit="疑似手" if 50<=med<=200 else ("疑似股" if 0.5<=med<=2 else f"比例异常 median={med:.2f}")
    n=len(ratio); a=ratio.iloc[:n//2]; b=ratio.iloc[n//2:]
    if len(a)<30 or len(b)<30: return unit,"否",""
    m1=float(a.median()); m2=float(b.median())
    if m1<=0 or m2<=0: return unit,"否",""
    if max(m1,m2)/min(m1,m2)>=20:
        return unit,"是",f"前后成交量/成交额比例疑似断层 m1={m1:.2f}, m2={m2:.2f}"
    return unit,"否",""

def classify_cache(code, meta_row):
    code=str(code).zfill(6)
    name=str(meta_row.get("name", "")) if meta_row else ""
    item={
        "股票代码": stock_code(code), "原始代码": code, "股票名称": name, "市场": market_name(code),
        "是否ST": "是" if "ST" in name.upper() else "否", "是否N/C新股": "是" if name.startswith(("N","C")) else "否",
        "缓存是否存在":"否", "状态文件结论": str(meta_row.get("status", "")) if meta_row else "", "状态文件原因": str(meta_row.get("reason", "")) if meta_row else "", "状态文件数据源": str(meta_row.get("source", "")) if meta_row else "",
        "K线起始日期":"", "K线最新日期":"", "距离今天自然日":"", "K线总根数":0, "最大自然日断档":"",
        "是否达到目标交易日":"", "目标交易日":"",
        "真正价格硬错误数量":0, "真正价格硬错误日期示例":"", "前复权非正价数量":0, "前复权非正价日期示例":"", "正价可用起始日期":"", "正价可用K线数":0,
        "成交量缺失数":0, "成交量为0数量":0, "零成交/疑似停牌日期示例":"", "成交额缺失数":0, "成交量单位推断":"", "成交量单位疑似断层":"否", "成交量单位断层备注":"",
        "缓存长度类型":"不可用", "可用于日线短周期":"否", "可用于年度结构":"否", "可用于月线周线模型":"否", "可用于长周期时间模型":"否", "是否需要裁剪前复权非正价":"否", "是否进入一号员工模型池":"否",
        "验收等级":"D", "验收结论":"不通过", "限制原因":"",
    }
    df=read_cache(code)
    if df is None or df.empty:
        item["限制原因"]="缓存不存在或不可读"
        return item
    item["缓存是否存在"]="是"
    n=len(df); first=pd.to_datetime(df["date"].min()); last=pd.to_datetime(df["date"].max())
    item["K线起始日期"]=first.strftime("%Y-%m-%d"); item["K线最新日期"]=last.strftime("%Y-%m-%d"); item["距离今天自然日"]=latest_gap_days(df); item["K线总根数"]=n; item["最大自然日断档"]=max_calendar_gap_days(df)
    if TARGET_TRADE_DATE:
        item["目标交易日"] = TARGET_TRADE_DATE.strftime("%Y-%m-%d")
        item["是否达到目标交易日"] = "是" if last.date() >= TARGET_TRADE_DATE else "否"
    else:
        item["是否达到目标交易日"] = "未知"
    missing=df[["open","high","low","close"]].isna().any(axis=1)
    nonpos=~positive_mask(df)
    true_err=missing | (df["high"]<df["low"]) | (df["high"]<df["open"]) | (df["high"]<df["close"]) | (df["low"]>df["open"]) | (df["low"]>df["close"])
    item["真正价格硬错误数量"]=int(true_err.sum()); item["真正价格硬错误日期示例"]=first_bad_date(df,true_err)
    item["前复权非正价数量"]=int(nonpos.sum()); item["前复权非正价日期示例"]=first_bad_date(df,nonpos); item["正价可用起始日期"]=first_positive_date(df); item["正价可用K线数"]=int(positive_mask(df).sum())
    if item["前复权非正价数量"]>0: item["是否需要裁剪前复权非正价"]="是"
    vol_missing=df["volume"].isna(); vol_zero=df["volume"]<=0; amt_missing=df["amount"].isna()
    item["成交量缺失数"]=int(vol_missing.sum()); item["成交量为0数量"]=int(vol_zero.sum()); item["零成交/疑似停牌日期示例"]=first_bad_date(df,vol_zero); item["成交额缺失数"]=int(amt_missing.sum())
    unit, brk, note=infer_volume_unit_and_break(df); item["成交量单位推断"]=unit; item["成交量单位疑似断层"]=brk; item["成交量单位断层备注"]=note
    reasons=[]; gap=item["距离今天自然日"]
    if gap!="" and gap>MAX_LATEST_GAP_DAYS: reasons.append(f"最新K距离今天{gap}天，可能停牌/退市/数据未更新")
    if TARGET_TRADE_DATE and last.date() < TARGET_TRADE_DATE:
        reasons.append(f"最新K未达到目标交易日{TARGET_TRADE_DATE}，疑似停牌/无交易/数据源未同步，当日不进正式模型池")
    if item["真正价格硬错误数量"]>0: reasons.append("存在真正价格硬错误")
    if item["前复权非正价数量"]>0: reasons.append(f"存在前复权早期非正价{item['前复权非正价数量']}条，模型读取时应从{item['正价可用起始日期']}开始裁剪使用")
    if item["成交量缺失数"]>0 or item["成交额缺失数"]>0: reasons.append("存在成交量/成交额缺失")
    if item["成交量单位疑似断层"]=="是": reasons.append("成交量单位或成交额比例疑似断层")
    usable_n=item["正价可用K线数"] or n
    if usable_n<30: item["缓存长度类型"]="极短缓存/新股或异常"
    elif usable_n<120: item["缓存长度类型"]="短缓存/新股或异常"
    elif usable_n<250: item["缓存长度类型"]="120-249根，仅短周期观察"; item["可用于日线短周期"]="是"
    elif usable_n<500: item["缓存长度类型"]="250-499根，可做短周期，长周期不足"; item["可用于日线短周期"]="是"; item["可用于年度结构"]="视情况"
    elif usable_n<2000: item["缓存长度类型"]="500-1999根，可做日线/年度，长周期不足"; item["可用于日线短周期"]="是"; item["可用于年度结构"]="是"; item["可用于月线周线模型"]="有限"
    else: item["缓存长度类型"]="2000根以上，全周期基础较好"; item["可用于日线短周期"]="是"; item["可用于年度结构"]="是"; item["可用于月线周线模型"]="是"; item["可用于长周期时间模型"]="是"
    is_new_short=usable_n<250 and (datetime.now().date()-first.date()).days<=450
    if usable_n<250 and not is_new_short: reasons.append("正价可用K线少于250根且不像近期新股，模型层限制使用")
    if is_new_short: reasons.append("近期上市/次新股，短历史正常，但暂不进长周期模型")
    hard_block = item["真正价格硬错误数量"]>0 or item["成交量单位疑似断层"]=="是" or (gap!="" and gap>30) or code in EXCLUDE_CODES or (TARGET_TRADE_DATE is not None and last.date() < TARGET_TRADE_DATE)
    if not hard_block and usable_n>=MODEL_MIN_ROWS and (gap=="" or gap<=MAX_LATEST_GAP_DAYS): item["是否进入一号员工模型池"]="是"
    if hard_block: item["验收等级"]="D"; item["验收结论"]="不通过"
    elif item["是否进入一号员工模型池"]=="是" and item["是否需要裁剪前复权非正价"]=="是": item["验收等级"]="Q"; item["验收结论"]="前复权早期非正价，裁剪后通过"
    elif item["是否进入一号员工模型池"]=="是" and usable_n>=2000: item["验收等级"]="A"; item["验收结论"]="通过"
    elif item["是否进入一号员工模型池"]=="是": item["验收等级"]="B"; item["验收结论"]="通过但限制长周期"
    elif is_new_short: item["验收等级"]="C"; item["验收结论"]="新股短历史，暂不进正式模型池"
    else: item["验收等级"]="D"; item["验收结论"]="不通过"
    if not reasons: reasons.append("数据层可用")
    item["限制原因"]="；".join(reasons)
    return item

def main():
    today=today_str(); log("========== A股K线缓存验收 V16.5 开始 ==========")
    load_target_trade_date()
    meta=load_status_meta(); cache_codes=set()
    if os.path.exists(KLINE_CACHE_DIR):
        for f in os.listdir(KLINE_CACHE_DIR):
            if f.lower().endswith(".csv") and not f.startswith("_") and f[:6].isdigit(): cache_codes.add(f[:6])
    all_codes=sorted(cache_codes | set(meta.keys()))
    log(f"[INFO] cache_codes={len(cache_codes)}, meta_codes={len(meta)}, total={len(all_codes)}")
    rows=[]
    for i,code in enumerate(all_codes,1):
        rows.append(classify_cache(code, meta.get(code, {})))
        if i%500==0 or i==len(all_codes): log(f"[进度] {i}/{len(all_codes)}")
    report=pd.DataFrame(rows)
    summary={
        "总股票数": len(report), "缓存存在数": int((report["缓存是否存在"]=="是").sum()), "缓存缺失数": int((report["缓存是否存在"]!="是").sum()),
        "验收通过A数": int((report["验收等级"]=="A").sum()), "验收通过B数": int((report["验收等级"]=="B").sum()), "前复权裁剪后通过Q数": int((report["验收等级"]=="Q").sum()), "新股短历史C数": int((report["验收等级"]=="C").sum()), "不通过D数": int((report["验收等级"]=="D").sum()),
        "进入一号员工模型池数": int((report["是否进入一号员工模型池"]=="是").sum()), "真正价格硬错误股票数": int((report["真正价格硬错误数量"]>0).sum()), "前复权非正价股票数": int((report["前复权非正价数量"]>0).sum()), "成交量单位疑似断层股票数": int((report["成交量单位疑似断层"]=="是").sum()), "最新K超过15天股票数": int((pd.to_numeric(report["距离今天自然日"], errors="coerce")>MAX_LATEST_GAP_DAYS).sum()), "未达到目标交易日股票数": int((report["是否达到目标交易日"]=="否").sum()) if "是否达到目标交易日" in report.columns else 0, "目标交易日": TARGET_TRADE_DATE.strftime("%Y-%m-%d") if TARGET_TRADE_DATE else "", "生成日期": today,
    }
    model=report[report["是否进入一号员工模型池"]=="是"].copy()
    still=report[(report["缓存是否存在"]!="是") | ((report["验收等级"]=="D") & (~report["限制原因"].astype(str).str.contains("真正价格硬错误", na=False)))].copy()
    bad=report[(report["验收等级"]=="D") | (report["真正价格硬错误数量"]>0) | (report["成交量单位疑似断层"]=="是")].copy()
    paths={
        "summary": os.path.join(OUT_DIR,f"cache_acceptance_summary_{today}.csv"),
        "report": os.path.join(OUT_DIR,f"cache_acceptance_report_{today}.csv"),
        "usable": os.path.join(OUT_DIR,f"model_usable_universe_{today}.csv"),
        "still": os.path.join(OUT_DIR,f"still_need_backfill_{today}.csv"),
        "bad": os.path.join(OUT_DIR,f"bad_cache_or_unusable_{today}.csv"),
        "state": os.path.join(OUT_DIR,"cache_acceptance_state.json"),
    }
    pd.DataFrame([summary]).to_csv(paths["summary"], index=False, encoding="utf-8-sig")
    report.to_csv(paths["report"], index=False, encoding="utf-8-sig")
    model.to_csv(paths["usable"], index=False, encoding="utf-8-sig")
    still.to_csv(paths["still"], index=False, encoding="utf-8-sig")
    bad.to_csv(paths["bad"], index=False, encoding="utf-8-sig")
    with open(paths["state"], "w", encoding="utf-8") as f: json.dump(summary, f, ensure_ascii=False, indent=2)
    log("========== A股K线缓存验收 V16.5 完成 ==========")
    for k,v in summary.items(): log(f"{k}: {v}")
    for k,p in paths.items(): log(f"[输出] {p}")

if __name__=="__main__":
    main()
