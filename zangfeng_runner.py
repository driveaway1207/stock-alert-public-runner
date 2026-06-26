# -*- coding: utf-8 -*-
from __future__ import annotations

"""藏锋 Runner

入口：python -u zangfeng_runner.py --scan
输出：artifacts/zangfeng_report.md、artifacts/zangfeng_candidates.json

原则：
- 只扫描目标交易日命中的K线，避免旧缓存误报。
- 目标日期由 workflow 传入 ZANGFENG_TARGET_DATE / DATA_GATE_TARGET_DATE / TARGET_TRADE_DATE。
- runner 只生成报告；消息发送由 workflow 统一完成。
"""

import argparse
import json
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    import baostock as bs
except Exception:
    bs = None

from modules.zangfeng_indicator import calculate_zangfeng, self_check as indicator_self_check

BOOT = "ZANGFENG_RUNNER_V2_EMPLOYEE3_STYLE"
ROOT = Path(__file__).resolve().parent
ARTIFACT_DIR = ROOT / "artifacts"
MAIN_CACHE_DIR = ROOT / "kline_cache"
CACHE_DIRS = [MAIN_CACHE_DIR, ROOT / "employee5_kline_cache", ROOT / "data" / "kline_cache", ROOT / "cache" / "kline_cache", ROOT.parent / "kline_cache"]
OUTPUT_MD = ARTIFACT_DIR / "zangfeng_report.md"
OUTPUT_JSON = ARTIFACT_DIR / "zangfeng_candidates.json"
SELF_CHECK_JSON = ARTIFACT_DIR / "zangfeng_self_check.json"

TARGET_KEYS = ["ZANGFENG_TARGET_DATE", "SELECTION_TRADE_DATE", "DATA_GATE_TARGET_DATE", "TARGET_TRADE_DATE", "LAST_TRADE_DAY_OVERRIDE", "REQUIRED_CACHE_DATE"]
TOP_N = int(os.getenv("ZANGFENG_TOP_N", "20"))
MIN_SCORE = float(os.getenv("ZANGFENG_MIN_SCORE", "60"))
MAX_STOCKS = int(os.getenv("MAX_STOCKS", os.getenv("ZANGFENG_MAX_STOCKS", "0")))
MIN_ROWS = int(os.getenv("ZANGFENG_MIN_CACHE_ROWS", "60"))
PROGRESS_EVERY = int(os.getenv("ZANGFENG_PROGRESS_EVERY", "500"))
ALLOW_REFRESH = os.getenv("ZANGFENG_ALLOW_BAOSTOCK_FALLBACK", "1") == "1"
REFRESH_DAYS = int(os.getenv("ZANGFENG_RECENT_REFRESH_DAYS", "420"))
REFRESH_BUDGET_MIN = float(os.getenv("ZANGFENG_REFRESH_BUDGET_MIN", "35"))
QFQ_ADJUSTFLAG = "2"


def now_bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def prev_workday(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def norm_target_date(x: Any, today: Optional[date] = None) -> str:
    today = today or now_bj().date()
    s = "" if x is None else str(x).strip()
    if not s:
        return ""
    s = s.replace("年", "-").replace("月", "-").replace("日", "")
    s = s.replace("/", "-").replace(".", "-").replace("_", "-").strip()
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
        return s[:10]
    raw = re.sub(r"\D", "", s)
    if len(raw) >= 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    if len(raw) in (3, 4):
        return f"{today.year:04d}-{int(raw[:-2]):02d}-{int(raw[-2:]):02d}"
    return ""


def target_raw() -> str:
    for key in TARGET_KEYS:
        value = os.getenv(key)
        if value:
            return value
    return prev_workday(now_bj().date()).isoformat()


TARGET_DASH = norm_target_date(target_raw())


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        return float(str(x).replace("%", "").replace(",", ""))
    except Exception:
        return default


def rd(x: Any, n: int = 3) -> float:
    v = sf(x)
    return 0.0 if math.isnan(v) or math.isinf(v) else round(v, n)


def norm_date(x: Any) -> str:
    s = re.sub(r"\D", "", ss(x)[:10])
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else ""


def code_of(x: Any) -> str:
    s = x.stem if isinstance(x, Path) else ss(x)
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else ""


def valid_code(code: str) -> bool:
    c = code_of(code)
    return c.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4"))


def bs_code(code: str) -> str:
    c = code_of(code)
    if c.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh." + c
    if c.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz." + c
    if c.startswith(("920", "8", "4")):
        return "bj." + c
    return c


def fmt_seconds(seconds: float) -> str:
    if seconds <= 0 or math.isnan(seconds) or math.isinf(seconds):
        return "0秒"
    if seconds < 60:
        return f"{seconds:.1f}秒"
    if seconds < 3600:
        return f"{seconds / 60:.1f}分钟"
    return f"{seconds / 3600:.1f}小时"


def normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    mp = {
        "日期": "date", "交易日期": "date", "date": "date", "time": "date",
        "代码": "code", "code": "code", "证券代码": "code",
        "名称": "name", "股票名称": "name", "name": "name",
        "开盘": "open", "open": "open", "开盘价": "open",
        "最高": "high", "high": "high", "最高价": "high",
        "最低": "low", "low": "low", "最低价": "low",
        "收盘": "close", "close": "close", "收盘价": "close",
        "成交量": "volume", "volume": "volume", "vol": "volume",
        "成交额": "amount", "amount": "amount",
        "涨跌幅": "pct_chg", "pct_chg": "pct_chg", "pctChg": "pct_chg", "涨幅": "pct_chg",
    }
    d = df.rename(columns={c: mp.get(str(c), mp.get(str(c).lower(), c)) for c in df.columns}).copy()
    if not {"date", "open", "high", "low", "close"}.issubset(d.columns):
        return pd.DataFrame()
    for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
        if col in d.columns:
            d[col] = d[col].map(sf)
    if "volume" not in d.columns:
        d["volume"] = 0.0
    if "amount" not in d.columns:
        d["amount"] = 0.0
    d["date"] = d["date"].map(norm_date)
    d = d[(d.date != "") & (d.open > 0) & (d.high > 0) & (d.low > 0) & (d.close > 0)]
    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if TARGET_DASH:
        d = d[d.date <= TARGET_DASH].reset_index(drop=True)
    if d.empty:
        return d
    if "pct_chg" not in d.columns or d["pct_chg"].abs().sum() == 0:
        prev = d.close.shift(1)
        d["pct_chg"] = (d.close / prev - 1.0) * 100.0
        d.loc[prev <= 0, "pct_chg"] = 0.0
        d["pct_chg"] = d["pct_chg"].fillna(0.0)
    return d


def read_cache_file(path: Path) -> pd.DataFrame:
    try:
        return normalize_hist(pd.read_csv(path))
    except Exception:
        pass
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        rows = obj.get("rows") or obj.get("data") or obj.get("klines") or []
        return normalize_hist(pd.DataFrame(rows))
    except Exception:
        return pd.DataFrame()


def iter_cache_files() -> List[Path]:
    seen: Dict[str, Path] = {}
    for d in CACHE_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*")):
            if p.suffix.lower() not in {".csv", ".json"}:
                continue
            code = code_of(p)
            if valid_code(code) and code not in seen:
                seen[code] = p
    files = list(seen.values())
    if MAX_STOCKS > 0:
        files = files[:MAX_STOCKS]
    return files


def fetch_baostock_kline(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    if bs is None:
        return pd.DataFrame()
    fields = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
    try:
        rs = bs.query_history_k_data_plus(bs_code(code), fields, start_date=start_date, end_date=end_date, frequency="d", adjustflag=QFQ_ADJUSTFLAG)
        if getattr(rs, "error_code", "1") != "0":
            return pd.DataFrame()
        rows: List[List[str]] = []
        while rs.next():
            rows.append(rs.get_row_data())
        return normalize_hist(pd.DataFrame(rows, columns=fields.split(","))) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def save_cache(code: str, df: pd.DataFrame) -> None:
    MAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    keep = [c for c in ["date", "code", "name", "open", "high", "low", "close", "volume", "amount", "pct_chg"] if c in df.columns]
    df[keep].to_csv(MAIN_CACHE_DIR / f"{code}.csv", index=False, encoding="utf-8")


def refresh_stale_cache(files: List[Path]) -> Dict[str, Any]:
    stat = {"enabled": ALLOW_REFRESH, "attempt": 0, "success": 0, "failed": 0, "skipped": 0}
    if not ALLOW_REFRESH or not TARGET_DASH or bs is None:
        return stat
    try:
        lg = bs.login()
        if getattr(lg, "error_code", "1") != "0":
            stat["enabled"] = False
            return stat
    except Exception:
        stat["enabled"] = False
        return stat
    start_ts = time.time()
    budget = max(1.0, REFRESH_BUDGET_MIN * 60.0)
    start_date = (datetime.strptime(TARGET_DASH, "%Y-%m-%d").date() - timedelta(days=REFRESH_DAYS)).isoformat()
    try:
        for i, p in enumerate(files, 1):
            if time.time() - start_ts > budget:
                print("藏锋补拉达到预算，停止补拉", flush=True)
                break
            code = code_of(p)
            old = read_cache_file(p)
            latest = ss(old.date.iloc[-1]) if not old.empty and "date" in old.columns else ""
            if latest == TARGET_DASH:
                stat["skipped"] += 1
                continue
            stat["attempt"] += 1
            new = fetch_baostock_kline(code, start_date, TARGET_DASH)
            if new.empty or ss(new.date.iloc[-1]) != TARGET_DASH:
                stat["failed"] += 1
                continue
            merged = normalize_hist(pd.concat([old, new], ignore_index=True)) if not old.empty else new
            if not merged.empty and ss(merged.date.iloc[-1]) == TARGET_DASH:
                save_cache(code, merged)
                stat["success"] += 1
            else:
                stat["failed"] += 1
            if PROGRESS_EVERY > 0 and i % PROGRESS_EVERY == 0:
                print(f"藏锋补拉 {i}/{len(files)} attempt={stat['attempt']} success={stat['success']} failed={stat['failed']}", flush=True)
    finally:
        try:
            bs.logout()
        except Exception:
            pass
    return stat


def estimate_lines(df: pd.DataFrame) -> Tuple[float, float, float, float]:
    close, high, low = df.close.astype(float), df.high.astype(float), df.low.astype(float)
    last = float(close.iloc[-1])
    high20 = float(high.tail(20).max())
    low20 = float(low.tail(20).min())
    high120 = float(high.tail(min(len(high), 120)).max())
    high250 = float(high.tail(min(len(high), 250)).max())
    pressure = high20 if high20 >= last else last * 1.02
    support = low20 if 0 < low20 < last else last * 0.94
    next_pressure = max(high120, high250, pressure * 1.12)
    if next_pressure <= last:
        next_pressure = last * 1.18
    return pressure, support, pressure, next_pressure


def scan() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    files = iter_cache_files()
    refresh = refresh_stale_cache(files)
    files = iter_cache_files()
    rows: List[Dict[str, Any]] = []
    stat = {"boot": BOOT, "target_date": TARGET_DASH, "cache_files": len(files), "target_hit": 0, "stale_skip": 0, "scanned": 0, "bad": 0, "short": 0, "passed": 0, "refresh": refresh}
    start = time.time()
    print(f"藏锋启动 target={TARGET_DASH} cache_files={len(files)} min_score={MIN_SCORE}", flush=True)
    for i, path in enumerate(files, 1):
        code = code_of(path)
        df = read_cache_file(path)
        if df.empty:
            stat["bad"] += 1
            continue
        latest = ss(df.date.iloc[-1]) if "date" in df.columns and not df.empty else ""
        if TARGET_DASH and latest != TARGET_DASH:
            stat["stale_skip"] += 1
            continue
        stat["target_hit"] += 1
        if len(df) < MIN_ROWS:
            stat["short"] += 1
            continue
        stat["scanned"] += 1
        pressure, support, trigger, next_pressure = estimate_lines(df)
        result = calculate_zangfeng(df, pressure_price=pressure, support_price=support, trigger_price=trigger, next_pressure_price=next_pressure)
        if result.get("score", 0) >= MIN_SCORE:
            stat["passed"] += 1
            last_row = df.iloc[-1]
            rows.append({"code": code, "name": ss(last_row.get("name", "")), "date": latest, "close": rd(last_row.get("close")), "score": result["score"], "grade": result["grade"], "action_bias": result["action_bias"], "dimensions": result["dimensions"], "flags": result["flags"], "metrics": result["metrics"], "pressure_price": rd(pressure), "support_price": rd(support), "trigger_price": rd(trigger), "next_pressure_price": rd(next_pressure)})
        if PROGRESS_EVERY > 0 and i % PROGRESS_EVERY == 0:
            print(f"藏锋进度 {i}/{len(files)} target_hit={stat['target_hit']} scanned={stat['scanned']} passed={stat['passed']} stale={stat['stale_skip']} elapsed={fmt_seconds(time.time() - start)}", flush=True)
    rows.sort(key=lambda x: (x["score"], x["metrics"].get("flat_volume_ratio_10d", 0), -x["metrics"].get("return_20d", 0)), reverse=True)
    return rows[:TOP_N], stat


def render_report(candidates: List[Dict[str, Any]], stat: Dict[str, Any]) -> str:
    r = stat.get("refresh", {}) or {}
    lines = ["# 藏锋｜爆发前夜压缩指标", "", f"- 运行时间：{now_bj().strftime('%Y-%m-%d %H:%M:%S')} 北京时间", f"- 目标交易日：{stat.get('target_date') or '-'}", f"- 缓存文件：{stat.get('cache_files', 0)}", f"- 目标日命中：{stat.get('target_hit', 0)}", f"- 旧缓存跳过：{stat.get('stale_skip', 0)}", f"- 有效扫描：{stat.get('scanned', 0)}", f"- 入选数量：{len(candidates)}", f"- 最低分：{MIN_SCORE}", f"- 数据补拉：attempt={r.get('attempt', 0)} success={r.get('success', 0)} failed={r.get('failed', 0)}", "", "说明：藏锋不是买点，只判断是否进入爆发前夜；真正交易仍要等放量突破、站稳、回踩确认和RR合格。", ""]
    if not candidates:
        lines += ["## 今日无藏锋候选", "", "可能原因：目标日缓存不足、补拉未取得目标日数据、压缩质量不足，或最低分阈值过高。"]
        return "\n".join(lines) + "\n"
    lines += ["## Top 候选", "", "| 排名 | 代码 | 名称 | 日期 | 收盘 | 分数 | 等级 | 压力/触发 | 防守 | 标签 |", "|---:|---|---|---|---:|---:|---|---:|---:|---|"]
    for idx, item in enumerate(candidates, 1):
        flags = "、".join(item.get("flags", [])[:4])
        lines.append(f"| {idx} | {item['code']} | {item.get('name','')} | {item.get('date','')} | {item.get('close',0)} | {item['score']} | {item['grade']} | {item.get('trigger_price',0)} | {item.get('support_price',0)} | {flags} |")
    lines += ["", "## 明细"]
    for idx, item in enumerate(candidates, 1):
        d, m = item["dimensions"], item["metrics"]
        lines += ["", f"### {idx}. {item['code']} {item.get('name','')}", f"- 结论：{item['action_bias']}", f"- 分项：锋势{d['锋势']} / 锋气{d['锋气']} / 锋骨{d['锋骨']} / 锋意{d['锋意']} / 出鞘准备{d['出鞘准备']}", f"- 结构线：压力/触发 {item.get('trigger_price')}，防守 {item.get('support_price')}，下一压力 {item.get('next_pressure_price')}", f"- 压缩：ATR收缩 {m.get('atr_contract_ratio')}，振幅收缩 {m.get('range_contract_ratio')}，20日平台宽度 {m.get('platform_width_20d')}", f"- 量能：平量比例 {m.get('flat_volume_ratio_10d')}，量能CV收缩 {m.get('volume_cv_contract_ratio')}，阴/阳量比 {m.get('down_up_vol_ratio')}", f"- 标签：{'、'.join(item.get('flags', []))}"]
    return "\n".join(lines) + "\n"


def run_self_check(rounds: int) -> Dict[str, Any]:
    results = []
    for i in range(1, max(1, rounds) + 1):
        item = indicator_self_check()
        item["round"] = i
        results.append(item)
        print(f"藏锋自检 {i}/{rounds}: ok={item.get('ok')} good={item.get('good_score')} bad={item.get('bad_score')}", flush=True)
    return {"ok": all(bool(x.get("ok")) for x in results), "rounds": max(1, rounds), "results": results}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="藏锋爆发前夜压缩指标")
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-rounds", type=int, default=7)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    global MAX_STOCKS
    if args.limit is not None and args.limit >= 0:
        MAX_STOCKS = args.limit
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    check = run_self_check(args.self_test_rounds)
    SELF_CHECK_JSON.write_text(json.dumps(check, ensure_ascii=False, indent=2), encoding="utf-8")
    if not check.get("ok"):
        raise SystemExit("藏锋指标自检失败")
    if args.self_test and not args.scan:
        print(json.dumps(check, ensure_ascii=False, indent=2), flush=True)
        return 0
    candidates, stat = scan()
    OUTPUT_MD.write_text(render_report(candidates, stat), encoding="utf-8")
    OUTPUT_JSON.write_text(json.dumps({"stat": stat, "candidates": candidates}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUTPUT_MD.read_text(encoding="utf-8"), flush=True)
    print(f"藏锋报告已生成：{OUTPUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
