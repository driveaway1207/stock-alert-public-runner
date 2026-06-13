# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

VERSION = "擎天-v4-code-name-only"
ROOT = Path(__file__).resolve().parent
CACHE_DIRS = [ROOT / "kline_cache", ROOT / "employee5_kline_cache", ROOT / "data" / "kline_cache", ROOT / "cache" / "kline_cache", ROOT.parent / "kline_cache"]
OUT_DIR = ROOT / "artifacts"
MIN_ROWS = 80


def s(x: Any) -> str:
    return "" if x is None else str(x).strip()


def f(x: Any, default: float = 0.0) -> float:
    try:
        return float(str(x).replace(",", "").replace("%", ""))
    except Exception:
        return default


def code_of(x: Any) -> str:
    raw = x.stem if isinstance(x, Path) else s(x)
    m = re.search(r"(\d{6})", raw)
    return m.group(1) if m else ""


def valid_code(code: str) -> bool:
    return bool(code) and code.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689"))


def norm_date(x: Any) -> str:
    raw = s(x)[:10].replace("/", "-").replace(".", "-").replace("_", "-")
    d = re.sub(r"\D", "", raw)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else ""


def target_date() -> str:
    for key in ["QINGTIAN_TARGET_DATE", "SELECTION_TRADE_DATE", "TARGET_TRADE_DATE", "DATA_GATE_TARGET_DATE"]:
        v = os.getenv(key, "")
        if v:
            return norm_date(v)
    return ""

TARGET = target_date()


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    mp = {"日期":"date", "交易日期":"date", "date":"date", "开盘":"open", "open":"open", "最高":"high", "high":"high", "最低":"low", "low":"low", "收盘":"close", "close":"close", "名称":"name", "股票名称":"name", "股票简称":"name", "name":"name", "代码":"code", "股票代码":"code", "code":"code"}
    d = df.rename(columns={c: mp.get(str(c), str(c)) for c in df.columns}).copy()
    if not {"date", "open", "high", "low", "close"}.issubset(d.columns):
        return pd.DataFrame()
    for col in ["open", "high", "low", "close"]:
        d[col] = d[col].map(f)
    if "name" not in d.columns:
        d["name"] = ""
    d["date"] = d["date"].map(norm_date)
    d["name"] = d["name"].map(s)
    d = d[(d.date != "") & (d.open > 0) & (d.high > 0) & (d.low > 0) & (d.close > 0)]
    if TARGET:
        d = d[d.date <= TARGET]
    return d.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def read_kline(path: Path) -> pd.DataFrame:
    try:
        if path.suffix.lower() == ".csv":
            return normalize(pd.read_csv(path))
        obj = json.loads(path.read_text(encoding="utf-8"))
        rows = obj.get("rows") or obj.get("data") or obj.get("klines") or [] if isinstance(obj, dict) else obj
        return normalize(pd.DataFrame(rows))
    except Exception:
        return pd.DataFrame()


def cache_files(limit: int = 0) -> List[Path]:
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
    out = list(seen.values())
    return out[:limit] if limit and limit > 0 else out


def to_month(df: pd.DataFrame) -> pd.DataFrame:
    d = normalize(df)
    if d.empty:
        return d
    d["dt"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["dt"]).sort_values("dt")
    d["m"] = d["dt"].dt.to_period("M")
    rows = []
    for _, g in d.groupby("m"):
        g = g.sort_values("dt")
        rows.append({"date": g.iloc[-1].dt.strftime("%Y-%m-%d"), "open": float(g.iloc[0].open), "high": float(g.high.max()), "low": float(g.low.min()), "close": float(g.iloc[-1].close)})
    return pd.DataFrame(rows)


def stock_name(df: pd.DataFrame) -> str:
    if "name" not in df.columns:
        return "名称待补"
    vals = [s(x) for x in df.name.tolist() if s(x) and s(x).lower() not in {"nan", "none", "null"} and not re.fullmatch(r"\d{6}", s(x))]
    return vals[-1] if vals else "名称待补"


def hit_qingtian(month: pd.DataFrame) -> bool:
    if month is None or len(month) < 5:
        return False
    m = month.sort_values("date").reset_index(drop=True)
    for i in range(0, len(m) - 4):
        o = float(m.loc[i, "open"])
        c = float(m.loc[i, "close"])
        if c <= o:
            continue
        body_pct = (c - o) / o * 100.0
        if body_pct <= 30.0:
            continue
        level = o + (c - o) * (2 / 3)
        future = m.iloc[i + 1:]
        ok = 0
        for _, r in future.iterrows():
            if float(r.close) >= level:
                ok += 1
            else:
                break
        if ok >= 4:
            return True
    return False


def run(limit: int) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = cache_files(limit)
    hits: List[Dict[str, str]] = []
    failed = 0
    for idx, path in enumerate(files, 1):
        code = code_of(path)
        df = read_kline(path)
        if df.empty or len(df) < MIN_ROWS:
            failed += 1
            continue
        if hit_qingtian(to_month(df)):
            hits.append({"code": code, "name": stock_name(df)})
        if idx == 1 or idx % 500 == 0 or idx == len(files):
            print(f"擎天扫描 {idx}/{len(files)} 命中{len(hits)} 当前{code}", flush=True)
    out = pd.DataFrame(hits, columns=["code", "name"])
    out.to_csv(OUT_DIR / "qingtian_latest.csv", index=False, encoding="utf-8-sig")
    data = {"summary": {"version": VERSION, "cache_files": len(files), "scanned_count": len(files), "failed_count": failed, "signal_count": len(hits)}, "signals": hits}
    (OUT_DIR / "qingtian_latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if hits:
        text = "\n".join([f"{x['code']} {x['name']}" for x in hits[:200]])
    else:
        text = "无符合擎天条件股票"
    (OUT_DIR / "qingtian_report.md").write_text(text, encoding="utf-8")
    print(json.dumps(data["summary"], ensure_ascii=False, indent=2))
    return 0


def self_test() -> None:
    df = pd.DataFrame([
        {"date":"2024-01-31","open":10,"high":14,"low":9,"close":13.2},
        {"date":"2024-02-29","open":13,"high":13,"low":11,"close":12.2},
        {"date":"2024-03-31","open":12,"high":13,"low":11,"close":12.2},
        {"date":"2024-04-30","open":12,"high":13,"low":11,"close":12.2},
        {"date":"2024-05-31","open":12,"high":13,"low":11,"close":12.2},
    ])
    assert hit_qingtian(df) is True
    df.loc[0, "close"] = 13.0
    assert hit_qingtian(df) is False
    print("擎天自检通过")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scan", action="store_true")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--limit", type=int, default=int(os.getenv("QINGTIAN_SCAN_LIMIT", "0") or 0))
    args = p.parse_args()
    try:
        if args.self_test:
            self_test(); return 0
        if args.scan:
            return run(args.limit)
        p.print_help(); return 0
    except Exception as exc:
        print(f"擎天运行失败: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
