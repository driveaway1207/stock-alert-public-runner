# -*- coding: utf-8 -*-
"""擎天：原生月K扫描器。

规则：月线真阳线实体涨幅严格大于30%；擎天位为阳线实体上1/3位
（开盘 + 实体 * 2/3）；其后至少4根月K收盘价全部不破擎天位。
输出和Telegram只使用：股票代码 + 中文简称。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    import baostock as bs
except Exception:
    bs = None

VERSION = "擎天-v5.1-native-monthly-stock-only-20260613"
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "artifacts"
OUTPUT_CSV = REPORT_DIR / "qingtian_latest.csv"
OUTPUT_JSON = REPORT_DIR / "qingtian_latest.json"
OUTPUT_MD = REPORT_DIR / "qingtian_report.md"
SELF_CHECK_JSON = REPORT_DIR / "qingtian_self_check.json"
CACHE_DIRS = [
    ROOT / "kline_cache",
    ROOT / "employee5_kline_cache",
    ROOT / "data" / "kline_cache",
    ROOT / "cache" / "kline_cache",
    ROOT.parent / "kline_cache",
]
MIN_BODY_PCT = 30.0
QINGTIAN_RATIO = 2.0 / 3.0
REQUIRED_CONFIRM_MONTHS = 4
MONTHLY_START_DATE = os.getenv("QINGTIAN_MONTHLY_START_DATE", "1990-01-01")
SCAN_PROGRESS_EVERY = int(os.getenv("QINGTIAN_PROGRESS_EVERY", "200"))
MAX_STOCKS = int(os.getenv("QINGTIAN_MAX_STOCKS", "0") or "0")
TARGET_RAW_KEYS = [
    "QINGTIAN_TARGET_DATE",
    "SELECTION_TRADE_DATE",
    "DATA_GATE_TARGET_DATE",
    "TARGET_TRADE_DATE",
    "LAST_TRADE_DAY_OVERRIDE",
]
STOCK_NAME_BLOCK_WORDS = (
    "指数", "B股指数", "A股指数", "综合指数", "成份指数",
    "基金", "ETF", "LOF", "REIT", "债", "转债", "国债", "企债", "可转债",
    "期货", "期权", "认购", "认沽", "CWB",
)


@dataclass
class StockItem:
    code: str
    bs_code: str
    name: str


@dataclass
class QingtianHit:
    code: str
    name: str
    anchor_month: str
    body_pct: float
    qingtian_level: float
    confirm_months: int


@dataclass
class ScanStat:
    version: str
    target_date: str
    stock_pool_count: int
    scanned_count: int
    monthly_success_count: int
    failed_count: int
    signal_count: int
    data_source: str


def now_bj() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def ss(value: Any) -> str:
    return "" if value is None else str(value).strip()


def sf(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(str(value).replace(",", "").replace("%", ""))
    except Exception:
        return default


def normalize_code(value: Any) -> str:
    raw = ss(value)
    m = re.search(r"(\d{6})", raw)
    return m.group(1) if m else ""


def valid_a_code(code: Any) -> bool:
    c = normalize_code(code)
    return bool(c) and c.startswith((
        "000", "001", "002", "003",
        "300", "301",
        "600", "601", "603", "605",
        "688", "689",
        "920", "8", "4",
    ))


def to_bs_code(code: str) -> str:
    c = normalize_code(code)
    if not c:
        return ""
    if c.startswith(("600", "601", "603", "605", "688", "689")):
        return f"sh.{c}"
    if c.startswith(("000", "001", "002", "003", "300", "301")):
        return f"sz.{c}"
    if c.startswith(("8", "4", "920")):
        return f"bj.{c}"
    return ""


def exchange_ok_for_common_stock(bs_code: str, code: str) -> bool:
    raw = ss(bs_code).lower()
    c = normalize_code(code or raw)
    if not c:
        return False
    if raw.startswith("sh."):
        return c.startswith(("600", "601", "603", "605", "688", "689"))
    if raw.startswith("sz."):
        return c.startswith(("000", "001", "002", "003", "300", "301"))
    if raw.startswith("bj."):
        return c.startswith(("8", "4", "920"))
    return valid_a_code(c)


def name_ok_for_common_stock(name: str) -> bool:
    n = ss(name)
    if not n:
        return True
    upper = n.upper()
    return not any(word in upper for word in STOCK_NAME_BLOCK_WORDS)


def is_common_a_stock(bs_code: str, code: str, name: str) -> bool:
    return exchange_ok_for_common_stock(bs_code, code) and name_ok_for_common_stock(name)


def norm_date(value: Any) -> str:
    raw = ss(value)
    if not raw:
        return ""
    raw = raw.replace("年", "-").replace("月", "-").replace("日", "")
    raw = raw.replace("/", "-").replace(".", "-").replace("_", "-").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def prev_workday(day: datetime) -> datetime:
    d = day
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def target_date() -> str:
    for key in TARGET_RAW_KEYS:
        value = os.getenv(key)
        if value:
            parsed = norm_date(value)
            if parsed:
                return parsed
    now = now_bj()
    if now.weekday() >= 5 or now.hour < 20 or (now.hour == 20 and now.minute < 35):
        now = prev_workday(now - timedelta(days=1))
    return now.strftime("%Y-%m-%d")


TARGET_DASH = target_date()


def ensure_report_dir() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def rows_from_baostock_query(rs: Any) -> List[List[str]]:
    rows: List[List[str]] = []
    while rs is not None and rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    return rows


def query_all_stocks(target: str) -> List[StockItem]:
    if bs is None:
        raise RuntimeError("baostock未安装，无法获取原生月K股票池")
    items: List[StockItem] = []
    seen: set[str] = set()

    def add(code_raw: Any, name_raw: Any) -> None:
        raw = ss(code_raw)
        code = normalize_code(raw)
        name = ss(name_raw) or "名称待补"
        bs_code = raw if "." in raw else to_bs_code(code)
        if not code or code in seen:
            return
        if not is_common_a_stock(bs_code, code, name):
            return
        seen.add(code)
        items.append(StockItem(code=code, bs_code=bs_code, name=name))

    rs = bs.query_all_stock(day=target)
    fields = list(getattr(rs, "fields", []) or [])
    for row in rows_from_baostock_query(rs):
        rec = dict(zip(fields, row))
        if ss(rec.get("tradeStatus")) not in {"", "1"}:
            continue
        add(rec.get("code"), rec.get("code_name") or rec.get("name"))

    if items:
        return items

    basic = bs.query_stock_basic()
    fields = list(getattr(basic, "fields", []) or [])
    for row in rows_from_baostock_query(basic):
        rec = dict(zip(fields, row))
        if ss(rec.get("status")) not in {"", "1"}:
            continue
        if ss(rec.get("type")) not in {"", "1"}:
            continue
        add(rec.get("code"), rec.get("code_name") or rec.get("name"))
    return items


def load_monthly_kline(item: StockItem, target: str) -> pd.DataFrame:
    if bs is None:
        raise RuntimeError("baostock未安装")
    fields = "date,code,open,high,low,close,volume,amount"
    rs = bs.query_history_k_data_plus(
        item.bs_code,
        fields,
        start_date=MONTHLY_START_DATE,
        end_date=target,
        frequency="m",
        adjustflag="2",
    )
    rows = rows_from_baostock_query(rs)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=fields.split(","))
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = df[col].map(sf)
    df["date"] = df["date"].map(norm_date)
    df = df[(df["date"] != "") & (df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
    return df.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def find_qingtian_hit(monthly: pd.DataFrame, item: StockItem) -> Optional[QingtianHit]:
    if monthly is None or monthly.empty or len(monthly) <= REQUIRED_CONFIRM_MONTHS:
        return None
    m = monthly.sort_values("date").reset_index(drop=True)
    best: Optional[QingtianHit] = None
    for idx in range(0, len(m) - REQUIRED_CONFIRM_MONTHS):
        row = m.iloc[idx]
        open_price = sf(row.get("open"))
        close_price = sf(row.get("close"))
        if open_price <= 0 or close_price <= open_price:
            continue
        body_pct = (close_price - open_price) / open_price * 100.0
        if body_pct <= MIN_BODY_PCT:
            continue
        level = open_price + (close_price - open_price) * QINGTIAN_RATIO
        future = m.iloc[idx + 1:].copy()
        if len(future) < REQUIRED_CONFIRM_MONTHS:
            continue
        if bool((future["close"].astype(float) >= level).all()):
            hit = QingtianHit(
                code=item.code,
                name=item.name,
                anchor_month=ss(row.get("date")),
                body_pct=round(body_pct, 2),
                qingtian_level=round(level, 4),
                confirm_months=int(len(future)),
            )
            best = hit
    return best


def build_report_text(hits: List[QingtianHit]) -> str:
    if hits:
        return "\n".join(f"{x.code} {x.name}" for x in hits)
    return "无符合擎天条件股票"


def write_outputs(hits: List[QingtianHit], stat: ScanStat, failures: List[Dict[str, str]]) -> None:
    ensure_report_dir()
    rows = [asdict(x) for x in hits]
    pd.DataFrame(rows, columns=["code", "name", "anchor_month", "body_pct", "qingtian_level", "confirm_months"]).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    payload = {"summary": asdict(stat), "signals": rows, "failures": failures[:300]}
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(build_report_text(hits).rstrip() + "\n", encoding="utf-8")


def run_scan(limit: int = 0) -> int:
    if bs is None:
        raise RuntimeError("baostock未安装，擎天无法获取原生月K")
    ensure_report_dir()
    lg = bs.login()
    if getattr(lg, "error_code", "") != "0":
        raise RuntimeError(f"baostock登录失败: {getattr(lg, 'error_code', '')} {getattr(lg, 'error_msg', '')}")
    try:
        pool = query_all_stocks(TARGET_DASH)
        if limit and limit > 0:
            pool = pool[:limit]
        if not pool:
            raise RuntimeError("股票池为空，不能伪装成无符合擎天股票")
        hits: List[QingtianHit] = []
        failures: List[Dict[str, str]] = []
        monthly_success = 0
        start = time.time()
        for idx, item in enumerate(pool, 1):
            try:
                monthly = load_monthly_kline(item, TARGET_DASH)
                if monthly.empty:
                    failures.append({"code": item.code, "name": item.name, "error": "原生月K为空"})
                    continue
                monthly_success += 1
                hit = find_qingtian_hit(monthly, item)
                if hit is not None:
                    hits.append(hit)
            except Exception as exc:
                failures.append({"code": item.code, "name": item.name, "error": str(exc)[:180]})
            if idx == 1 or idx % max(1, SCAN_PROGRESS_EVERY) == 0 or idx == len(pool):
                elapsed = max(time.time() - start, 0.001)
                print(f"擎天原生月K扫描 {idx}/{len(pool)}｜月K成功{monthly_success}｜命中{len(hits)}｜失败{len(failures)}｜速度{idx/elapsed:.2f}只/秒｜当前{item.code} {item.name}", flush=True)
        if monthly_success == 0:
            raise RuntimeError("原生月K成功数量为0，不能伪装成无符合擎天股票")
        stat = ScanStat(
            version=VERSION,
            target_date=TARGET_DASH,
            stock_pool_count=len(pool),
            scanned_count=len(pool),
            monthly_success_count=monthly_success,
            failed_count=len(failures),
            signal_count=len(hits),
            data_source="baostock_frequency_m_native_monthly",
        )
        write_outputs(hits, stat, failures)
        print(json.dumps(asdict(stat), ensure_ascii=False, indent=2), flush=True)
        return 0
    finally:
        try:
            bs.logout()
        except Exception:
            pass


def _synthetic_monthly(rows: List[Tuple[str, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame([{"date": d, "open": o, "high": h, "low": l, "close": c} for d, o, h, l, c in rows])


def self_check_once() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        items.append({"name": name, "ok": bool(ok), "detail": detail})

    src = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    called_names = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    add("source::no_daily_to_monthly_function", "to_month" not in function_names and "to_month" not in called_names, "生产代码不得存在日线聚合月线函数/调用")
    add("source::native_monthly_frequency", 'frequency="m"' in src, "必须使用BaoStock原生月K frequency=m")
    add("stock_pool::exclude_sh_000003_index", not is_common_a_stock("sh.000003", "000003", "上证B股指数"), "必须剔除sh.000003上证B股指数")
    add("stock_pool::exclude_sh_000001_index", not is_common_a_stock("sh.000001", "000001", "上证指数"), "必须剔除sh.000001上证指数")
    add("stock_pool::allow_sz_000001_stock", is_common_a_stock("sz.000001", "000001", "平安银行"), "允许sz.000001普通股票")
    add("stock_pool::allow_sh_600000_stock", is_common_a_stock("sh.600000", "600000", "浦发银行"), "允许sh.600000普通股票")
    add("stock_pool::allow_bj_920000_stock", is_common_a_stock("bj.920000", "920000", "测试北交所"), "允许bj.920000普通股票前缀")

    item = StockItem("000001", "sz.000001", "测试股")
    equal_30 = _synthetic_monthly([
        ("2024-01-31", 10, 13, 9, 13),
        ("2024-02-29", 13, 14, 8, 12.1),
        ("2024-03-31", 12, 14, 8, 12.2),
        ("2024-04-30", 12, 14, 8, 12.3),
        ("2024-05-31", 12, 14, 8, 12.4),
    ])
    add("rule::equal_30_not_hit", find_qingtian_hit(equal_30, item) is None, "实体涨幅等于30%不命中")

    hit_df = _synthetic_monthly([
        ("2024-01-31", 10, 14, 9, 13.2),
        ("2024-02-29", 13, 14, 8, 12.2),
        ("2024-03-31", 12, 14, 7, 12.2),
        ("2024-04-30", 12, 14, 7, 12.2),
        ("2024-05-31", 12, 14, 7, 12.2),
    ])
    add("rule::gt30_four_closes_hit", find_qingtian_hit(hit_df, item) is not None, "大于30%且后续4根收盘不破命中")
    add("rule::only_three_future_not_hit", find_qingtian_hit(hit_df.iloc[:4].copy(), item) is None, "后续仅3根月K不命中")

    break_close = hit_df.copy()
    break_close.loc[4, "close"] = 11.0
    add("rule::future_close_break_not_hit", find_qingtian_hit(break_close, item) is None, "任一后续月收盘跌破擎天位不命中")

    shadow_break = hit_df.copy()
    shadow_break.loc[2, "low"] = 6.0
    add("rule::shadow_break_still_hit", find_qingtian_hit(shadow_break, item) is not None, "影线跌破但收盘不破仍命中")

    report_text = build_report_text([QingtianHit("000001", "测试股", "2024-01-31", 32.0, 12.1333, 4)]).strip()
    add("output::code_name_only", report_text == "000001 测试股", f"报告内容={report_text!r}")
    empty_text = build_report_text([]).strip()
    add("output::empty_hit_text", empty_text == "无符合擎天条件股票", f"空命中文案={empty_text!r}")
    return items


def run_self_check(rounds: int) -> None:
    ensure_report_dir()
    all_rounds: List[Dict[str, Any]] = []
    for round_id in range(1, max(1, rounds) + 1):
        items = self_check_once()
        ok = all(x["ok"] for x in items)
        all_rounds.append({"round": round_id, "ok": ok, "items": items})
        print(f"擎天自检第{round_id}遍：{'通过' if ok else '失败'}", flush=True)
        for item in items:
            print(f"  [{'OK' if item['ok'] else 'FAIL'}] {item['name']}｜{item['detail']}", flush=True)
        if not ok:
            SELF_CHECK_JSON.write_text(json.dumps(all_rounds, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(f"擎天自检第{round_id}遍失败")
    SELF_CHECK_JSON.write_text(json.dumps(all_rounds, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="擎天原生月K扫描器")
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-rounds", type=int, default=3)
    parser.add_argument("--limit", type=int, default=MAX_STOCKS)
    args = parser.parse_args()
    try:
        if args.self_test:
            run_self_check(args.self_test_rounds)
            return 0
        if args.scan:
            return run_scan(args.limit)
        parser.print_help()
        return 0
    except Exception as exc:
        print(f"擎天运行失败: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
