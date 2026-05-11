# -*- coding: utf-8 -*-
"""
A股K线缓存验收脚本

只读取 kline_cache，不联网，不改缓存。
输出：
1. cache_acceptance_summary_日期.csv        总结表
2. cache_acceptance_report_日期.csv         全市场逐股验收表
3. model_usable_universe_日期.csv           一号员工可用股票池
4. still_need_backfill_日期.csv             仍需回补/暂不可用
5. bad_cache_or_unusable_日期.csv           异常/不可用缓存
"""

import os
import json
from datetime import datetime

import pandas as pd


KLINE_CACHE_DIR = "kline_cache"
OUT_DIR = "outputs"
STATUS_META_PATH = os.path.join(KLINE_CACHE_DIR, "_full_history_status.csv")

os.makedirs(OUT_DIR, exist_ok=True)


def log(msg):
    print(msg, flush=True)


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def stock_code(code):
    code = str(code).zfill(6)
    return ("SH." if code.startswith("6") else "SZ.") + code


def market_name(code):
    code = str(code).zfill(6)
    return "沪市" if code.startswith("6") else "深市"


def normalize_daily_columns(df):
    if df is None or df.empty:
        return None

    rename_map = {
        "日期": "date",
        "交易日期": "date",
        "trade_date": "date",
        "Date": "date",
        "datetime": "date",
        "time": "date",

        "开盘": "open",
        "开盘价": "open",
        "Open": "open",

        "收盘": "close",
        "收盘价": "close",
        "Close": "close",

        "最高": "high",
        "最高价": "high",
        "High": "high",

        "最低": "low",
        "最低价": "low",
        "Low": "low",

        "成交量": "volume",
        "成交量(手)": "volume",
        "vol": "volume",
        "Volume": "volume",

        "成交额": "amount",
        "成交额(元)": "amount",
        "turnover": "amount",
        "Amount": "amount",
    }

    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})

    required = ["date", "open", "high", "low", "close", "volume"]
    if not all(c in df.columns for c in required):
        return None

    if "amount" not in df.columns:
        df["amount"] = 0

    df = df[["date", "open", "high", "low", "close", "volume", "amount"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["date"])
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)

    if df.empty:
        return None

    return df


def read_cache(code):
    path = os.path.join(KLINE_CACHE_DIR, f"{str(code).zfill(6)}.csv")
    if not os.path.exists(path):
        return None

    try:
        return normalize_daily_columns(pd.read_csv(path))
    except Exception:
        return None


def load_status_meta():
    if not os.path.exists(STATUS_META_PATH):
        return {}

    try:
        df = pd.read_csv(STATUS_META_PATH, dtype={"code": str})
        if df.empty:
            return {}
        df["code"] = df["code"].astype(str).str.zfill(6)
        return {str(r["code"]).zfill(6): dict(r) for _, r in df.iterrows()}
    except Exception as e:
        log(f"[WARN] 读取状态文件失败: {repr(e)}")
        return {}


def latest_gap_days(df):
    if df is None or df.empty:
        return ""

    last_date = pd.to_datetime(df["date"].max()).date()
    return int((datetime.now().date() - last_date).days)


def max_calendar_gap_days(df):
    if df is None or df.empty or len(df) < 2:
        return ""

    dates = pd.to_datetime(df["date"]).sort_values()
    gaps = dates.diff().dt.days.dropna()
    if gaps.empty:
        return ""

    return int(gaps.max())


def first_bad_date(df, mask):
    bad = df[mask]
    if bad.empty:
        return ""
    return bad.iloc[0]["date"].strftime("%Y-%m-%d")


def infer_volume_unit_and_break(df):
    """
    粗略检查成交量/成交额口径是否异常。
    不直接改数据，只给验收提示。
    """
    if df is None or df.empty:
        return "", "否", ""

    tmp = df.copy()
    tmp = tmp[(tmp["volume"] > 0) & (tmp["amount"] > 0) & (tmp["close"] > 0)].copy()

    if len(tmp) < 60:
        return "样本不足", "否", ""

    ratio = tmp["amount"] / (tmp["volume"] * tmp["close"])
    ratio = ratio.replace([float("inf"), -float("inf")], pd.NA).dropna()
    ratio = ratio[(ratio > 0) & (ratio < 100000)]

    if len(ratio) < 60:
        return "样本不足", "否", ""

    med = float(ratio.median())

    if 50 <= med <= 200:
        unit = "疑似手"
    elif 0.5 <= med <= 2:
        unit = "疑似股"
    else:
        unit = f"比例异常 median={med:.2f}"

    n = len(ratio)
    first_half = ratio.iloc[: n // 2]
    second_half = ratio.iloc[n // 2 :]

    if len(first_half) < 30 or len(second_half) < 30:
        return unit, "否", ""

    m1 = float(first_half.median())
    m2 = float(second_half.median())

    if m1 <= 0 or m2 <= 0:
        return unit, "否", ""

    max_ratio = max(m1, m2) / min(m1, m2)

    if max_ratio >= 20:
        return unit, "是", f"前后成交量/成交额比例疑似断层 m1={m1:.2f}, m2={m2:.2f}"

    return unit, "否", ""


def classify_cache(code, meta_row):
    code = str(code).zfill(6)

    name = str(meta_row.get("name", "")) if meta_row else ""
    meta_status = str(meta_row.get("status", "")) if meta_row else ""
    meta_reason = str(meta_row.get("reason", "")) if meta_row else ""
    meta_source = str(meta_row.get("source", "")) if meta_row else ""

    item = {
        "股票代码": stock_code(code),
        "原始代码": code,
        "股票名称": name,
        "市场": market_name(code),
        "是否ST": "是" if "ST" in name.upper() else "否",
        "是否N/C新股": "是" if name.startswith(("N", "C")) else "否",

        "缓存是否存在": "否",
        "状态文件结论": meta_status,
        "状态文件原因": meta_reason,
        "状态文件数据源": meta_source,

        "K线起始日期": "",
        "K线最新日期": "",
        "距离今天自然日": "",
        "K线总根数": 0,
        "最大自然日断档": "",

        "价格硬错误数量": 0,
        "价格异常日期示例": "",
        "成交量缺失数": 0,
        "成交量为0数量": 0,
        "零成交/疑似停牌日期示例": "",
        "成交额缺失数": 0,
        "成交量单位推断": "",
        "成交量单位疑似断层": "否",
        "成交量单位断层备注": "",

        "缓存长度类型": "不可用",
        "可用于日线短周期": "否",
        "可用于年度结构": "否",
        "可用于月线周线模型": "否",
        "可用于长周期时间模型": "否",
        "是否进入一号员工模型池": "否",

        "验收等级": "D",
        "验收结论": "不通过",
        "限制原因": "",
    }

    df = read_cache(code)

    if df is None or df.empty:
        item["限制原因"] = "缓存不存在或不可读"
        return item

    item["缓存是否存在"] = "是"

    n = len(df)
    first_date = pd.to_datetime(df["date"].min())
    last_date = pd.to_datetime(df["date"].max())

    item["K线起始日期"] = first_date.strftime("%Y-%m-%d")
    item["K线最新日期"] = last_date.strftime("%Y-%m-%d")
    item["距离今天自然日"] = latest_gap_days(df)
    item["K线总根数"] = n
    item["最大自然日断档"] = max_calendar_gap_days(df)

    missing_ohlc = df[["open", "high", "low", "close"]].isna().any(axis=1)
    zero_price = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    high_low_error = df["high"] < df["low"]
    high_less_oc = (df["high"] < df["open"]) | (df["high"] < df["close"])
    low_greater_oc = (df["low"] > df["open"]) | (df["low"] > df["close"])

    price_error = missing_ohlc | zero_price | high_low_error | high_less_oc | low_greater_oc

    item["价格硬错误数量"] = int(price_error.sum())
    item["价格异常日期示例"] = first_bad_date(df, price_error)

    volume_missing = df["volume"].isna()
    volume_zero = df["volume"] <= 0
    amount_missing = df["amount"].isna()

    item["成交量缺失数"] = int(volume_missing.sum())
    item["成交量为0数量"] = int(volume_zero.sum())
    item["零成交/疑似停牌日期示例"] = first_bad_date(df, volume_zero)
    item["成交额缺失数"] = int(amount_missing.sum())

    unit, unit_break, unit_note = infer_volume_unit_and_break(df)
    item["成交量单位推断"] = unit
    item["成交量单位疑似断层"] = unit_break
    item["成交量单位断层备注"] = unit_note

    reasons = []

    recent_gap = item["距离今天自然日"]
    if recent_gap != "" and recent_gap > 15:
        reasons.append(f"最新K距离今天{recent_gap}天，可能停牌/退市/数据未更新")

    if item["价格硬错误数量"] > 0:
        reasons.append("存在价格硬错误")

    if item["成交量缺失数"] > 0 or item["成交额缺失数"] > 0:
        reasons.append("存在成交量/成交额缺失")

    if item["成交量单位疑似断层"] == "是":
        reasons.append("成交量单位或成交额比例疑似断层")

    # 缓存长度分层
    if n < 30:
        item["缓存长度类型"] = "极短缓存/新股或异常"
    elif n < 120:
        item["缓存长度类型"] = "短缓存/新股或异常"
    elif n < 250:
        item["缓存长度类型"] = "120-249根，仅短周期观察"
        item["可用于日线短周期"] = "是"
    elif n < 500:
        item["缓存长度类型"] = "250-499根，可做短周期，长周期不足"
        item["可用于日线短周期"] = "是"
        item["可用于年度结构"] = "视情况"
    elif n < 2000:
        item["缓存长度类型"] = "500-1999根，可做日线/年度，长周期不足"
        item["可用于日线短周期"] = "是"
        item["可用于年度结构"] = "是"
        item["可用于月线周线模型"] = "有限"
    else:
        item["缓存长度类型"] = "2000根以上，全周期基础较好"
        item["可用于日线短周期"] = "是"
        item["可用于年度结构"] = "是"
        item["可用于月线周线模型"] = "是"
        item["可用于长周期时间模型"] = "是"

    # 近期上市短数据识别：短历史不一定是坏数据
    first_gap_days = (datetime.now().date() - first_date.date()).days
    is_new_short = n < 250 and first_gap_days <= 450

    if n < 250 and not is_new_short:
        reasons.append("K线少于250根且不像近期新股，模型层限制使用")

    if is_new_short:
        reasons.append("近期上市/次新股，短历史正常，但暂不进长周期模型")

    hard_block = False

    if item["价格硬错误数量"] > 0:
        hard_block = True

    if item["成交量单位疑似断层"] == "是":
        hard_block = True

    if recent_gap != "" and recent_gap > 30:
        hard_block = True

    # 一号员工正式模型池：至少250根，没硬错误，最新K不能太旧
    if not hard_block and n >= 250 and (recent_gap == "" or recent_gap <= 15):
        item["是否进入一号员工模型池"] = "是"

    # 验收等级
    if hard_block:
        item["验收等级"] = "D"
        item["验收结论"] = "不通过"
    elif item["是否进入一号员工模型池"] == "是" and n >= 2000:
        item["验收等级"] = "A"
        item["验收结论"] = "通过"
    elif item["是否进入一号员工模型池"] == "是":
        item["验收等级"] = "B"
        item["验收结论"] = "通过但限制长周期"
    elif is_new_short:
        item["验收等级"] = "C"
        item["验收结论"] = "新股短历史，暂不进正式模型池"
    else:
        item["验收等级"] = "D"
        item["验收结论"] = "不通过"

    if not reasons:
        reasons.append("数据层可用")

    item["限制原因"] = "；".join(reasons)

    return item


def main():
    today = today_str()

    log("========== A股K线缓存验收开始 ==========")

    meta_map = load_status_meta()

    cache_codes = set()
    if os.path.exists(KLINE_CACHE_DIR):
        for f in os.listdir(KLINE_CACHE_DIR):
            if f.lower().endswith(".csv") and not f.startswith("_"):
                cache_codes.add(f[:6])

    meta_codes = set(meta_map.keys())
    all_codes = sorted(cache_codes | meta_codes)

    total = len(all_codes)
    log(f"[INFO] cache_codes={len(cache_codes)}, meta_codes={len(meta_codes)}, total={total}")

    rows = []

    for idx, code in enumerate(all_codes, start=1):
        item = classify_cache(code, meta_map.get(code, {}))
        rows.append(item)

        if idx % 500 == 0 or idx == total:
            log(f"[进度] {idx}/{total}")

    report = pd.DataFrame(rows)

    summary = {
        "总股票数": len(report),
        "缓存存在数": int((report["缓存是否存在"] == "是").sum()),
        "缓存缺失数": int((report["缓存是否存在"] != "是").sum()),
        "验收通过A数": int((report["验收等级"] == "A").sum()),
        "验收通过B数": int((report["验收等级"] == "B").sum()),
        "新股短历史C数": int((report["验收等级"] == "C").sum()),
        "不通过D数": int((report["验收等级"] == "D").sum()),
        "进入一号员工模型池数": int((report["是否进入一号员工模型池"] == "是").sum()),
        "价格硬错误股票数": int((report["价格硬错误数量"] > 0).sum()),
        "成交量单位疑似断层股票数": int((report["成交量单位疑似断层"] == "是").sum()),
        "最新K超过15天股票数": int((pd.to_numeric(report["距离今天自然日"], errors="coerce") > 15).sum()),
        "生成日期": today,
    }

    summary_df = pd.DataFrame([summary])

    model_usable = report[report["是否进入一号员工模型池"] == "是"].copy()

    still_need = report[
        (report["缓存是否存在"] != "是")
        | (
            (report["验收等级"] == "D")
            & (~report["限制原因"].astype(str).str.contains("价格硬错误", na=False))
        )
    ].copy()

    bad_cache = report[
        (report["验收等级"] == "D")
        | (report["价格硬错误数量"] > 0)
        | (report["成交量单位疑似断层"] == "是")
    ].copy()

    report_path = os.path.join(OUT_DIR, f"cache_acceptance_report_{today}.csv")
    summary_path = os.path.join(OUT_DIR, f"cache_acceptance_summary_{today}.csv")
    usable_path = os.path.join(OUT_DIR, f"model_usable_universe_{today}.csv")
    still_need_path = os.path.join(OUT_DIR, f"still_need_backfill_{today}.csv")
    bad_path = os.path.join(OUT_DIR, f"bad_cache_or_unusable_{today}.csv")
    state_path = os.path.join(OUT_DIR, "cache_acceptance_state.json")

    report.to_csv(report_path, index=False, encoding="utf-8-sig")
    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
    model_usable.to_csv(usable_path, index=False, encoding="utf-8-sig")
    still_need.to_csv(still_need_path, index=False, encoding="utf-8-sig")
    bad_cache.to_csv(bad_path, index=False, encoding="utf-8-sig")

    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    log("========== A股K线缓存验收完成 ==========")
    log(f"[输出] {summary_path}")
    log(f"[输出] {report_path}")
    log(f"[输出] {usable_path}")
    log(f"[输出] {still_need_path}")
    log(f"[输出] {bad_path}")
    log("")
    log("[SUMMARY]")
    for k, v in summary.items():
        log(f"{k}: {v}")


if __name__ == "__main__":
    main()
