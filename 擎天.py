# -*- coding: utf-8 -*-
"""
擎天.py

擎天战法全市场月线扫描器。默认对齐三号员工链路：先读公共 kline_cache，
不把 AkShare 股票池作为第一入口，避免远端断开导致空报告。

规则：月线真阳线；实体涨幅严格大于30%；擎天位为实体66.7%；
后续至少4根月K收盘不破擎天位。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

VERSION = "擎天-v3.0-cache-first"
ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = Path("artifacts")
DEFAULT_CACHE_DIR = Path("qingtian_cache")
CACHE_DIRS = [ROOT / "kline_cache", ROOT / "employee5_kline_cache", ROOT / "data" / "kline_cache", ROOT / "cache" / "kline_cache", ROOT.parent / "kline_cache"]
DEFAULT_MIN_BODY_PCT = 30.0
DEFAULT_CONFIRM_MONTHS = 4
DEFAULT_QINGTIAN_RATIO = 2 / 3
MIN_CACHE_ROWS = 80


def now_cn() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def ss(x: Any) -> str:
    return "" if x is None else str(x).strip()


def sf(x: Any, default: float = 0.0) -> float:
    try:
        if x is None or pd.isna(x):
            return default
        return float(str(x).replace("%", "").replace(",", ""))
    except Exception:
        return default


def code_of(x: Any) -> str:
    s = x.stem if isinstance(x, Path) else ss(x)
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else ""


def normalize_code(raw: Any) -> str:
    c = code_of(raw)
    if c:
        return c
    digits = re.sub(r"\D", "", ss(raw))
    return digits.zfill(6)[-6:] if digits else ""


def valid_code(code: Any) -> bool:
    c = normalize_code(code)
    return c.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "920", "8", "4"))


def norm_date(x: Any) -> str:
    s = ss(x)[:10].replace("/", "-").replace(".", "-").replace("_", "-")
    digits = re.sub(r"\D", "", s)
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else ""


def target_date() -> str:
    for k in ["QINGTIAN_TARGET_DATE", "TARGET_TRADE_DATE", "DATA_GATE_TARGET_DATE"]:
        v = os.getenv(k)
        if v:
            return norm_date(v)
    return now_cn().strftime("%Y-%m-%d")

TARGET_DASH = target_date()


@dataclass
class QingtianSignal:
    code: str
    name: str
    qingtian_month: str
    qingtian_open: float
    qingtian_close: float
    qingtian_high: float
    qingtian_low: float
    body_pct: float
    qingtian_level: float
    confirm_months: int
    last_month: str
    last_close: float
    distance_to_qingtian_pct: float
    max_drawdown_close_pct: float
    lowest_low_after_pct: float
    months_since_qingtian: int
    status: str
    rank_score: float = 0.0


@dataclass
class ScanSummary:
    version: str
    run_time: str
    target_date: str
    scanned_count: int
    success_count: int
    failed_count: int
    signal_count: int
    strict_body_pct: float
    required_confirm_months: int
    qingtian_ratio: float
    data_status: str
    cache_files: int


def qingtian_level(open_price: float, close_price: float, ratio: float = DEFAULT_QINGTIAN_RATIO) -> float:
    return float(open_price + (close_price - open_price) * ratio)


def normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    mp = {"日期":"date","交易日期":"date","date":"date","time":"date","代码":"code","股票代码":"code","证券代码":"code","code":"code","symbol":"code","名称":"name","股票名称":"name","股票简称":"name","证券简称":"name","name":"name","开盘":"open","开盘价":"open","open":"open","最高":"high","最高价":"high","high":"high","最低":"low","最低价":"low","low":"low","收盘":"close","收盘价":"close","close":"close","成交量":"volume","volume":"volume","vol":"volume","成交额":"amount","amount":"amount"}
    d = df.rename(columns={c: mp.get(str(c), mp.get(str(c).lower(), c)) for c in df.columns}).copy()
    if not {"date", "open", "high", "low", "close"}.issubset(d.columns):
        return pd.DataFrame()
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in d.columns:
            d[col] = d[col].map(sf)
    if "name" not in d.columns:
        d["name"] = ""
    if "code" not in d.columns:
        d["code"] = ""
    d["date"] = d["date"].map(norm_date)
    d["code"] = d["code"].map(normalize_code)
    d["name"] = d["name"].map(ss)
    d = d[(d.date != "") & (d.open > 0) & (d.high > 0) & (d.low > 0) & (d.close > 0)]
    if TARGET_DASH:
        d = d[d.date <= TARGET_DASH]
    return d.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def read_any_kline(path: Path) -> pd.DataFrame:
    try:
        if path.suffix.lower() == ".csv":
            return normalize_hist(pd.read_csv(path))
        obj = json.loads(path.read_text(encoding="utf-8"))
        rows = obj.get("rows") or obj.get("data") or obj.get("klines") or [] if isinstance(obj, dict) else obj
        return normalize_hist(pd.DataFrame(rows))
    except Exception:
        return pd.DataFrame()


def iter_cache_files(limit: int = 0) -> List[Path]:
    seen: Dict[str, Path] = {}
    for d in CACHE_DIRS:
        if not d.exists():
            continue
        for p in sorted(d.glob("*")):
            if p.suffix.lower() not in {".csv", ".json"}:
                continue
            c = code_of(p)
            if valid_code(c) and c not in seen:
                seen[c] = p
    files = list(seen.values())
    return files[:limit] if limit and limit > 0 else files


def to_monthly(df: pd.DataFrame) -> pd.DataFrame:
    d = normalize_hist(df)
    if d.empty:
        return pd.DataFrame()
    d["dt"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["dt"]).sort_values("dt")
    d["month"] = d["dt"].dt.to_period("M")
    rows = []
    for _, g in d.groupby("month"):
        g = g.sort_values("dt")
        rows.append({"date": g.iloc[-1]["dt"].strftime("%Y-%m-%d"), "open": float(g.iloc[0]["open"]), "high": float(g["high"].max()), "low": float(g["low"].min()), "close": float(g.iloc[-1]["close"])})
    return pd.DataFrame(rows)


def stock_name(df: pd.DataFrame) -> str:
    if "name" not in df.columns:
        return "名称待补"
    vals = [ss(x) for x in df["name"].tolist() if ss(x) and ss(x).lower() not in {"nan", "none", "null"} and not re.fullmatch(r"\d{6}", ss(x))]
    return vals[-1] if vals else "名称待补"


def prepare_monthly_df(df: pd.DataFrame) -> pd.DataFrame:
    d = normalize_hist(df)
    if d.empty:
        raise ValueError("月线数据为空或字段不完整")
    return d[["date", "open", "high", "low", "close"]].copy()


def find_qingtian_signals(monthly_df: pd.DataFrame, code: str = "", name: str = "", min_body_pct: float = DEFAULT_MIN_BODY_PCT, required_confirm_months: int = DEFAULT_CONFIRM_MONTHS, ratio: float = DEFAULT_QINGTIAN_RATIO) -> List[QingtianSignal]:
    df = prepare_monthly_df(monthly_df)
    if required_confirm_months < 4:
        raise ValueError("擎天要求后续超过3根月K，required_confirm_months不能小于4")
    signals: List[QingtianSignal] = []
    if len(df) <= required_confirm_months:
        return signals
    for i in range(0, len(df) - required_confirm_months):
        row = df.iloc[i]
        o, c = float(row.open), float(row.close)
        h, l = float(row.high), float(row.low)
        if c <= o:
            continue
        body_pct = (c - o) / o * 100.0
        if body_pct <= float(min_body_pct):
            continue
        level = qingtian_level(o, c, ratio=float(ratio))
        future = df.iloc[i + 1:]
        confirm = 0
        for close_price in future.close.astype(float).tolist():
            if close_price >= level:
                confirm += 1
            else:
                break
        if confirm < int(required_confirm_months):
            continue
        accepted = future.iloc[:confirm]
        last = accepted.iloc[-1]
        min_close_after = float(accepted.close.min())
        min_low_after = float(accepted.low.min())
        close_cushion = (min_close_after / level - 1.0) * 100.0
        low_drawdown = (min_low_after / level - 1.0) * 100.0
        status = "擎天长期锁筹" if confirm >= 8 and close_cushion >= 0 else ("擎天收盘不破但影线深踩" if low_drawdown < -12 else "擎天确认")
        signals.append(QingtianSignal(normalize_code(code), name or "名称待补", str(row.date), round(o,4), round(c,4), round(h,4), round(l,4), round(body_pct,2), round(level,4), int(confirm), str(last.date), round(float(last.close),4), round((float(last.close)/level-1)*100,2), round(close_cushion,2), round(low_drawdown,2), int(len(df)-i-1), status))
    return signals


def rank_signals(signals: Iterable[QingtianSignal]) -> pd.DataFrame:
    rows = [asdict(s) for s in signals]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["qingtian_month_dt"] = pd.to_datetime(df["qingtian_month"], errors="coerce")
    df["rank_score"] = (df["body_pct"].clip(upper=85) * 0.45 + df["confirm_months"].clip(upper=18) * 2.8 + df["distance_to_qingtian_pct"].clip(lower=0, upper=55) * 0.18 - df["lowest_low_after_pct"].clip(upper=0).abs() * 0.12 - df["months_since_qingtian"].clip(lower=0, upper=60) * 0.03).round(2)
    return df.sort_values(["qingtian_month_dt", "rank_score", "confirm_months"], ascending=[False, False, False]).drop(columns=["qingtian_month_dt"]).reset_index(drop=True)


def build_report(df: pd.DataFrame, summary: ScanSummary) -> str:
    lines = ["# 擎天扫描报告", "", f"- 版本：{summary.version}", f"- 运行时间：{summary.run_time}", f"- 目标日期：{summary.target_date}", f"- 数据状态：{summary.data_status}", f"- 缓存文件：{summary.cache_files}", f"- 扫描数量：{summary.scanned_count}", f"- 成功数量：{summary.success_count}", f"- 失败数量：{summary.failed_count}", f"- 命中数量：{summary.signal_count}", f"- 规则：月线阳实体涨幅 > {summary.strict_body_pct:.1f}%，后续至少 {summary.required_confirm_months} 根月K收盘不破擎天位", "", "擎天看的是一根月线大阳线把筹码成本抬高后，后续至少四个月收盘都守在实体66.7%位之上。", ""]
    if df.empty:
        lines.append("本次没有命中擎天结构。")
        return "\n".join(lines)
    cols = ["code", "name", "qingtian_month", "body_pct", "qingtian_level", "confirm_months", "last_month", "last_close", "distance_to_qingtian_pct", "lowest_low_after_pct", "status", "rank_score"]
    lines += ["## 命中列表", ""]
    try:
        lines.append(df[cols].head(150).to_markdown(index=False))
    except Exception:
        lines.append(df[cols].head(150).to_csv(index=False))
    return "\n".join(lines)


def write_outputs(df: pd.DataFrame, summary: ScanSummary, failed: List[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "qingtian_latest.csv", index=False, encoding="utf-8-sig")
    (out_dir / "qingtian_latest.json").write_text(json.dumps({"summary": asdict(summary), "signals": df.to_dict("records")}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "qingtian_report.md").write_text(build_report(df, summary), encoding="utf-8")
    if failed:
        pd.DataFrame(failed).to_csv(out_dir / "qingtian_failed.csv", index=False, encoding="utf-8-sig")


def run_scan(args: argparse.Namespace) -> Tuple[pd.DataFrame, ScanSummary]:
    out_dir = Path(args.output_dir)
    files = iter_cache_files(limit=int(args.limit or 0))
    all_signals: List[QingtianSignal] = []
    failed: List[dict] = []
    success = 0
    print(f"擎天按三号员工方式读取公共缓存：{len(files)} 个文件", flush=True)
    start = time.time()
    for i, p in enumerate(files, 1):
        code = code_of(p)
        try:
            daily = read_any_kline(p)
            if daily.empty or len(daily) < MIN_CACHE_ROWS:
                failed.append({"code": code, "name": "", "error": "缓存为空或K线太短"})
            else:
                monthly = to_monthly(daily)
                all_signals.extend(find_qingtian_signals(monthly, code=code, name=stock_name(daily), min_body_pct=float(args.min_body_pct), required_confirm_months=int(args.confirm_months), ratio=float(args.ratio)))
                success += 1
        except Exception as exc:
            failed.append({"code": code, "name": "", "error": str(exc)[:300]})
        if i == 1 or i % 500 == 0 or i == len(files):
            elapsed = max(time.time() - start, 0.001)
            print(f"擎天缓存进度 {i}/{len(files)}｜成功{success}｜失败{len(failed)}｜命中{len(all_signals)}｜速度{i/elapsed:.2f}只/秒｜当前{code}", flush=True)
    result_df = rank_signals(all_signals)
    summary = ScanSummary(VERSION, now_cn().strftime("%Y-%m-%d %H:%M:%S"), TARGET_DASH, int(len(files)), int(success), int(len(failed)), int(len(result_df)), float(args.min_body_pct), int(args.confirm_months), float(args.ratio), "public_kline_cache", int(len(files)))
    write_outputs(result_df, summary, failed, out_dir)
    return result_df, summary


def self_test() -> None:
    df_equal_30 = pd.DataFrame([{"date":"2024-01-31","open":10,"high":13,"low":9.8,"close":13},{"date":"2024-02-29","open":13,"high":13.5,"low":12,"close":12.1},{"date":"2024-03-31","open":12.1,"high":13,"low":12,"close":12.2},{"date":"2024-04-30","open":12.2,"high":13,"low":12,"close":12.3},{"date":"2024-05-31","open":12.3,"high":13,"low":12,"close":12.4}])
    assert len(find_qingtian_signals(df_equal_30)) == 0
    df_hit = pd.DataFrame([{"date":"2024-01-31","open":10,"high":14,"low":9.8,"close":13.2},{"date":"2024-02-29","open":13.2,"high":13.5,"low":11.0,"close":12.2},{"date":"2024-03-31","open":12.2,"high":13,"low":11.1,"close":12.2},{"date":"2024-04-30","open":12.0,"high":13,"low":11.2,"close":12.2},{"date":"2024-05-31","open":12.1,"high":13,"low":11.1,"close":12.3}])
    sig = find_qingtian_signals(df_hit, code="000001", name="测试")
    assert len(sig) == 1 and sig[0].confirm_months == 4
    assert math.isclose(sig[0].qingtian_level, round(10 + (13.2 - 10) * 2 / 3, 4), rel_tol=0, abs_tol=1e-4)
    assert len(find_qingtian_signals(df_hit.iloc[:4].copy())) == 0
    df_break = df_hit.copy(); df_break.loc[3, "close"] = 11.0
    assert len(find_qingtian_signals(df_break)) == 0
    df_low_break = df_hit.copy(); df_low_break.loc[2, "low"] = 8.0
    assert len(find_qingtian_signals(df_low_break)) == 1
    daily = pd.DataFrame([{"date":"2024-01-02","open":10,"high":11,"low":9.8,"close":10.5},{"date":"2024-01-31","open":10.5,"high":14,"low":10.1,"close":13.2},{"date":"2024-02-29","open":13.2,"high":13.5,"low":11,"close":12.2},{"date":"2024-03-29","open":12.2,"high":13,"low":11.1,"close":12.2},{"date":"2024-04-30","open":12,"high":13,"low":11.2,"close":12.2},{"date":"2024-05-31","open":12.1,"high":13,"low":11.1,"close":12.3}])
    assert not to_monthly(daily).empty
    print("擎天自检通过：8/8")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="擎天战法全市场月线扫描器")
    p.add_argument("--scan", action="store_true")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    p.add_argument("--min-body-pct", type=float, default=DEFAULT_MIN_BODY_PCT)
    p.add_argument("--confirm-months", type=int, default=DEFAULT_CONFIRM_MONTHS)
    p.add_argument("--ratio", type=float, default=DEFAULT_QINGTIAN_RATIO)
    p.add_argument("--limit", type=int, default=int(os.getenv("QINGTIAN_SCAN_LIMIT", "0") or 0))
    p.add_argument("--sleep", type=float, default=0.0)
    p.add_argument("--progress-every", type=int, default=500)
    p.add_argument("--force-refresh", action="store_true")
    p.add_argument("--telegram", action="store_true")
    return p


def main() -> int:
    args = build_arg_parser().parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if args.scan:
            _, summary = run_scan(args)
            print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
            return 0
        build_arg_parser().print_help(); return 0
    except Exception as exc:
        print(f"擎天运行失败: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
