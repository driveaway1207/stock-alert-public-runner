# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import calendar
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

VERSION = "擎天-v7-position-age-sort"
ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "artifacts"
OUTPUT_CSV = REPORT_DIR / "qingtian_latest.csv"
OUTPUT_JSON = REPORT_DIR / "qingtian_latest.json"
OUTPUT_MD = REPORT_DIR / "qingtian_report.md"
SELF_CHECK_JSON = REPORT_DIR / "qingtian_self_check.json"

MIN_BODY_PCT = 30.0
QINGTIAN_RATIO = 2.0 / 3.0
CONFIRM_MONTHS = int(os.getenv("QINGTIAN_CONFIRM_MONTHS", "4"))
MAX_CONFIRM_AGE = int(os.getenv("QINGTIAN_MAX_CONFIRM_AGE_MONTHS", "6"))
START_DATE = os.getenv("QINGTIAN_MONTHLY_START_DATE", "1990-01-01")
MAX_STOCKS = int(os.getenv("QINGTIAN_MAX_STOCKS", "0") or "0")
PROGRESS_EVERY = int(os.getenv("QINGTIAN_PROGRESS_EVERY", "200"))

VOL_LOOKBACK = int(os.getenv("QINGTIAN_VOLUME_LOOKBACK_MONTHS", "12"))
VOL_MIN_HIST = int(os.getenv("QINGTIAN_VOLUME_MIN_HISTORY", "6"))
VOL_MEDIAN_MULT = float(os.getenv("QINGTIAN_VOLUME_MEDIAN_MULT", "1.35"))
VOL_RANK_LOOKBACK = int(os.getenv("QINGTIAN_VOLUME_RANK_LOOKBACK", "36"))
VOL_TOP_Q = float(os.getenv("QINGTIAN_VOLUME_TOP_QUANTILE", "0.70"))

POS_LOOKBACK = int(os.getenv("QINGTIAN_POSITION_LOOKBACK_MONTHS", "60"))
POS_MIN_HIST = int(os.getenv("QINGTIAN_POSITION_MIN_HISTORY", "12"))
OPEN_POS_MAX = float(os.getenv("QINGTIAN_ANCHOR_OPEN_MAX_POSITION", "0.72"))
CLOSE_POS_MAX = float(os.getenv("QINGTIAN_ANCHOR_CLOSE_MAX_POSITION", "0.88"))
PRIOR_RUNUP_LOOKBACK = int(os.getenv("QINGTIAN_PRIOR_RUNUP_LOOKBACK", "24"))
PRIOR_RUNUP_MAX = float(os.getenv("QINGTIAN_PRIOR_RUNUP_MAX_PCT", "120.0"))

TARGET_KEYS = [
    "QINGTIAN_TARGET_DATE",
    "SELECTION_TRADE_DATE",
    "DATA_GATE_TARGET_DATE",
    "TARGET_TRADE_DATE",
    "LAST_TRADE_DAY_OVERRIDE",
]

BLOCK_NAME = (
    "指数", "B股指数", "A股指数", "综合指数", "成份指数",
    "基金", "ETF", "LOF", "REIT",
    "债", "转债", "国债", "企债", "可转债",
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
    confirm_month: str
    latest_complete_month: str
    body_pct: float
    qingtian_level: float
    confirm_months: int
    confirm_age_months: int
    amount_ratio: float
    position_open: float
    position_close: float


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


def bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or pd.isna(value):
            return default
        return float(str(value).replace(",", "").replace("%", ""))
    except Exception:
        return default


def code6(value: Any) -> str:
    match = re.search(r"(\d{6})", s(value))
    return match.group(1) if match else ""


def norm_date(value: Any) -> str:
    raw = (
        s(value)
        .replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace(".", "-")
        .replace("_", "-")
    )
    digits = re.sub(r"\D", "", raw)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return ""


def prev_workday(day: datetime) -> datetime:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def target_date() -> str:
    for key in TARGET_KEYS:
        value = os.getenv(key)
        if value:
            parsed = norm_date(value)
            if parsed:
                return parsed

    now = bj_now()
    if now.weekday() >= 5 or now.hour < 20 or (now.hour == 20 and now.minute < 35):
        now = prev_workday(now - timedelta(days=1))
    return now.strftime("%Y-%m-%d")


TARGET_DASH = target_date()


def bs_code_of(code: str) -> str:
    c = code6(code)
    if c.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh." + c
    if c.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz." + c
    if c.startswith(("8", "4", "920")):
        return "bj." + c
    return ""


def exchange_stock_ok(bs_code: str, code: str) -> bool:
    raw = s(bs_code).lower()
    c = code6(code or bs_code)
    if not c:
        return False

    if raw.startswith("sh."):
        return c.startswith(("600", "601", "603", "605", "688", "689"))
    if raw.startswith("sz."):
        return c.startswith(("000", "001", "002", "003", "300", "301"))
    if raw.startswith("bj."):
        return c.startswith(("8", "4", "920"))

    return bool(bs_code_of(c))


def name_stock_ok(name: str) -> bool:
    upper = s(name).upper()
    return not any(word in upper for word in BLOCK_NAME)


def common_stock_ok(bs_code: str, code: str, name: str) -> bool:
    return exchange_stock_ok(bs_code, code) and name_stock_ok(name)


def ensure_report_dir() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def bs_rows(rs: Any) -> List[List[str]]:
    rows: List[List[str]] = []
    while rs is not None and rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    return rows


def query_stock_pool(target: str) -> List[StockItem]:
    if bs is None:
        raise RuntimeError("baostock未安装，无法获取原生月K股票池")

    out: List[StockItem] = []
    seen: set[str] = set()

    def add(code_raw: Any, name_raw: Any) -> None:
        raw = s(code_raw)
        code = code6(raw)
        name = s(name_raw) or "名称待补"
        bs_code = raw if "." in raw else bs_code_of(code)

        if not code or code in seen:
            return
        if not common_stock_ok(bs_code, code, name):
            return

        seen.add(code)
        out.append(StockItem(code=code, bs_code=bs_code, name=name))

    rs = bs.query_all_stock(day=target)
    fields = list(getattr(rs, "fields", []) or [])
    for row in bs_rows(rs):
        rec = dict(zip(fields, row))
        if s(rec.get("tradeStatus")) not in {"", "1"}:
            continue
        add(rec.get("code"), rec.get("code_name") or rec.get("name"))

    if out:
        return out

    basic = bs.query_stock_basic()
    fields = list(getattr(basic, "fields", []) or [])
    for row in bs_rows(basic):
        rec = dict(zip(fields, row))
        if s(rec.get("status")) not in {"", "1"}:
            continue
        if s(rec.get("type")) not in {"", "1"}:
            continue
        add(rec.get("code"), rec.get("code_name") or rec.get("name"))

    return out


def load_monthly(item: StockItem, target: str) -> pd.DataFrame:
    if bs is None:
        raise RuntimeError("baostock未安装")

    fields = "date,code,open,high,low,close,volume,amount"
    rs = bs.query_history_k_data_plus(
        item.bs_code,
        fields,
        start_date=START_DATE,
        end_date=target,
        frequency="m",
        adjustflag="2",
    )

    rows = bs_rows(rs)
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=fields.split(","))
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        df[col] = df[col].map(f)
    df["date"] = df["date"].map(norm_date)

    df = df[
        (df["date"] != "")
        & (df["open"] > 0)
        & (df["high"] > 0)
        & (df["low"] > 0)
        & (df["close"] > 0)
    ]
    return df.sort_values("date").drop_duplicates("date").reset_index(drop=True)


def month_finished(target: str) -> bool:
    t = pd.to_datetime(target, errors="coerce")
    if pd.isna(t):
        return False
    return int(t.day) >= calendar.monthrange(int(t.year), int(t.month))[1]


def complete_months(df: pd.DataFrame, target: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    m = df.sort_values("date").drop_duplicates("date").reset_index(drop=True).copy()
    t = pd.to_datetime(target, errors="coerce")

    if pd.isna(t) or month_finished(target):
        return m

    m["period"] = pd.to_datetime(m["date"], errors="coerce").dt.to_period("M")
    if len(m) and m.iloc[-1]["period"] == t.to_period("M"):
        m = m.iloc[:-1].copy()

    return m.drop(columns=["period"], errors="ignore").reset_index(drop=True)


def money(row: pd.Series) -> float:
    amount = f(row.get("amount"))
    if amount > 0:
        return amount
    return f(row.get("volume"))


def volume_ok(df: pd.DataFrame, idx: int) -> Tuple[bool, float]:
    metric = df.apply(money, axis=1)
    anchor = f(metric.iloc[idx])
    prev = metric.iloc[max(0, idx - VOL_LOOKBACK):idx]
    prev = prev[prev > 0]

    if anchor <= 0 or len(prev) < VOL_MIN_HIST:
        return False, 0.0

    median = float(prev.median())
    if median <= 0:
        return False, 0.0

    ratio = anchor / median
    if anchor < median:
        return False, round(ratio, 3)

    rank_window = metric.iloc[max(0, idx - VOL_RANK_LOOKBACK):idx + 1]
    rank_window = rank_window[rank_window > 0]
    rank_pct = float((rank_window <= anchor).mean()) if len(rank_window) >= VOL_MIN_HIST else 0.0

    ok = ratio >= VOL_MEDIAN_MULT or rank_pct >= VOL_TOP_Q
    return ok, round(ratio, 3)


def position_ok(df: pd.DataFrame, idx: int) -> Tuple[bool, float, float]:
    # 位置过滤只判断“锚定大阳月启动前”的位置。
    # 擎天月本身往往会突破阶段高点，如果把锚定月的高点/收盘纳入区间，
    # 会用启动后的冲高结果反向误杀真正的大级别启动。
    prior_window = df.iloc[max(0, idx - POS_LOOKBACK):idx]
    if len(prior_window) < POS_MIN_HIST:
        return False, 1.0, 1.0

    low = float(prior_window["low"].min())
    high = float(prior_window["high"].max())
    if high <= low or low <= 0:
        return False, 1.0, 1.0

    row = df.iloc[idx]
    open_pos = (f(row.get("open")) - low) / (high - low)
    close_pos = (f(row.get("close")) - low) / (high - low)

    # 硬过滤只看锚定月开盘是否已经处在启动前高位。
    # 收盘突破到区间上方是擎天本身的正常结果，不再作为直接淘汰条件；
    # close_pos 继续输出，用于排序和审计。
    if open_pos > OPEN_POS_MAX:
        return False, round(open_pos, 3), round(close_pos, 3)

    prior = df.iloc[max(0, idx - PRIOR_RUNUP_LOOKBACK):idx]
    if len(prior) >= 6:
        prior_low = float(prior["low"].min())
        if prior_low > 0:
            runup_pct = (f(row.get("open")) - prior_low) / prior_low * 100.0
            if runup_pct > PRIOR_RUNUP_MAX:
                return False, round(open_pos, 3), round(close_pos, 3)

    return True, round(open_pos, 3), round(close_pos, 3)


def find_qingtian_hit(monthly: pd.DataFrame, item: StockItem, target: str = TARGET_DASH) -> Optional[QingtianHit]:
    m = complete_months(monthly, target)
    if m.empty or len(m) <= CONFIRM_MONTHS:
        return None

    m = m.sort_values("date").reset_index(drop=True)
    latest_idx = len(m) - 1
    latest_month = s(m.iloc[-1].get("date"))

    for idx in range(latest_idx - CONFIRM_MONTHS, -1, -1):
        confirm_idx = idx + CONFIRM_MONTHS
        confirm_age = latest_idx - confirm_idx

        if confirm_age > MAX_CONFIRM_AGE:
            break

        row = m.iloc[idx]
        open_price = f(row.get("open"))
        close_price = f(row.get("close"))

        if open_price <= 0 or close_price <= open_price:
            continue

        body_pct = (close_price - open_price) / open_price * 100.0
        if body_pct <= MIN_BODY_PCT:
            continue

        vol_ok, vol_ratio = volume_ok(m, idx)
        if not vol_ok:
            continue

        pos_ok, open_pos, close_pos = position_ok(m, idx)
        if not pos_ok:
            continue

        qingtian_level = open_price + (close_price - open_price) * QINGTIAN_RATIO
        future = m.iloc[idx + 1:].copy()

        if len(future) < CONFIRM_MONTHS:
            continue

        if not bool((future["close"].astype(float) >= qingtian_level).all()):
            continue

        return QingtianHit(
            code=item.code,
            name=item.name,
            anchor_month=s(row.get("date")),
            confirm_month=s(m.iloc[confirm_idx].get("date")),
            latest_complete_month=latest_month,
            body_pct=round(body_pct, 2),
            qingtian_level=round(qingtian_level, 4),
            confirm_months=int(len(future)),
            confirm_age_months=int(confirm_age),
            amount_ratio=round(vol_ratio, 3),
            position_open=round(open_pos, 3),
            position_close=round(close_pos, 3),
        )

    return None


def sort_hits(hits: List[QingtianHit]) -> List[QingtianHit]:
    return sorted(
        hits,
        key=lambda x: (
            x.confirm_age_months,
            x.position_open,
            -x.amount_ratio,
            -x.body_pct,
            -x.confirm_months,
            x.code,
        ),
    )


def build_report_text(hits: List[QingtianHit]) -> str:
    if hits:
        return "\n".join(f"{x.code} {x.name}" for x in hits)
    return "无符合擎天条件股票"


def write_outputs(hits: List[QingtianHit], stat: ScanStat, failures: List[Dict[str, str]]) -> None:
    ensure_report_dir()
    rows = [asdict(x) for x in hits]

    pd.DataFrame(
        rows,
        columns=[
            "code", "name", "anchor_month", "confirm_month", "latest_complete_month",
            "body_pct", "qingtian_level", "confirm_months", "confirm_age_months",
            "amount_ratio", "position_open", "position_close",
        ],
    ).to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    payload = {"summary": asdict(stat), "signals": rows, "failures": failures[:300]}
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(build_report_text(hits).rstrip() + "\n", encoding="utf-8")


def run_scan(limit: int = 0) -> int:
    if bs is None:
        raise RuntimeError("baostock未安装，擎天无法获取原生月K")

    ensure_report_dir()
    login_result = bs.login()
    if getattr(login_result, "error_code", "") != "0":
        raise RuntimeError(
            f"baostock登录失败: {getattr(login_result, 'error_code', '')} {getattr(login_result, 'error_msg', '')}"
        )

    try:
        pool = query_stock_pool(TARGET_DASH)
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
                monthly = load_monthly(item, TARGET_DASH)
                full_monthly = complete_months(monthly, TARGET_DASH)

                if full_monthly.empty:
                    failures.append({"code": item.code, "name": item.name, "error": "原生完整月K为空"})
                    continue

                monthly_success += 1
                hit = find_qingtian_hit(full_monthly, item, TARGET_DASH)
                if hit is not None:
                    hits.append(hit)

            except Exception as exc:
                failures.append({"code": item.code, "name": item.name, "error": str(exc)[:180]})

            if idx == 1 or idx % max(1, PROGRESS_EVERY) == 0 or idx == len(pool):
                elapsed = max(time.time() - start, 0.001)
                print(
                    f"擎天原生月K扫描 {idx}/{len(pool)}"
                    f"｜完整月K成功{monthly_success}"
                    f"｜命中{len(hits)}"
                    f"｜失败{len(failures)}"
                    f"｜速度{idx / elapsed:.2f}只/秒"
                    f"｜当前{item.code} {item.name}",
                    flush=True,
                )

        if monthly_success == 0:
            raise RuntimeError("原生完整月K成功数量为0，不能伪装成无符合擎天股票")

        hits = sort_hits(hits)

        stat = ScanStat(
            version=VERSION,
            target_date=TARGET_DASH,
            stock_pool_count=len(pool),
            scanned_count=len(pool),
            monthly_success_count=monthly_success,
            failed_count=len(failures),
            signal_count=len(hits),
            data_source="baostock_frequency_m_native_monthly_complete_month_only",
        )

        write_outputs(hits, stat, failures)
        print(json.dumps(asdict(stat), ensure_ascii=False, indent=2), flush=True)
        return 0

    finally:
        try:
            bs.logout()
        except Exception:
            pass


def synthetic_monthly(rows: List[Tuple[Any, ...]]) -> pd.DataFrame:
    normalized: List[Dict[str, Any]] = []

    for row in rows:
        if len(row) == 5:
            d, o, h, l, c = row
            volume = 100.0
            amount = 100.0
        elif len(row) == 7:
            d, o, h, l, c, volume, amount = row
        else:
            raise ValueError("synthetic row must have 5 or 7 fields")

        normalized.append({"date": d, "open": o, "high": h, "low": l, "close": c, "volume": volume, "amount": amount})

    return pd.DataFrame(normalized)


def base_low_months(start_year: int = 2022, count: int = 12, amount: float = 100.0) -> List[Tuple[Any, ...]]:
    rows: List[Tuple[Any, ...]] = []
    y, m = start_year, 1

    for _ in range(count):
        rows.append((f"{y:04d}-{m:02d}-28", 10.0, 18.0, 9.5, 10.2, amount, amount))
        m += 1
        if m > 12:
            y += 1
            m = 1

    return rows


def valid_recent_qingtian_rows(anchor_amount: float = 180.0, anchor_open: float = 10.0, anchor_close: float = 13.2) -> pd.DataFrame:
    rows = base_low_months()
    rows.extend([
        ("2023-01-31", anchor_open, max(anchor_close, 13.5), 9.8, anchor_close, anchor_amount, anchor_amount),
        ("2023-02-28", 12.8, 13.0, 8.0, 12.2, 120.0, 120.0),
        ("2023-03-31", 12.2, 12.8, 7.0, 12.2, 120.0, 120.0),
        ("2023-04-30", 12.2, 12.9, 7.0, 12.2, 120.0, 120.0),
        ("2023-05-31", 12.2, 12.9, 7.0, 12.2, 120.0, 120.0),
    ])
    return synthetic_monthly(rows)


def realistic_breakout_qingtian_rows() -> pd.DataFrame:
    rows: List[Tuple[Any, ...]] = []
    y, m = 2022, 1
    for i in range(12):
        high = 10.8 + (0.02 if i % 3 == 0 else 0.0)
        rows.append((f"{y:04d}-{m:02d}-28", 9.6, high, 8.8, 9.9, 100.0, 100.0))
        m += 1
        if m > 12:
            y += 1
            m = 1

    rows.extend([
        ("2023-01-31", 10.0, 13.7, 9.8, 13.5, 190.0, 190.0),
        ("2023-02-28", 13.0, 13.6, 12.0, 12.6, 120.0, 120.0),
        ("2023-03-31", 12.6, 13.4, 11.8, 12.7, 120.0, 120.0),
        ("2023-04-30", 12.7, 13.5, 11.9, 12.8, 120.0, 120.0),
        ("2023-05-31", 12.8, 13.6, 11.9, 12.9, 120.0, 120.0),
    ])
    return synthetic_monthly(rows)


def self_check_once() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        items.append({"name": name, "ok": bool(ok), "detail": detail})

    src = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    add("source::native_monthly_frequency", 'frequency="m"' in src, "必须使用BaoStock原生月K frequency=m")
    add("source::no_daily_to_monthly_function", "to_month" not in function_names and "to_month" not in called_names, "生产代码不得存在日线聚合月线函数/调用")
    add("source::complete_month_filter", "complete_months" in function_names, "必须剔除未完成当前月K")
    add("source::volume_filter", "volume_ok" in function_names, "必须检查大阳月量能")
    add("source::position_filter", "position_ok" in function_names, "必须检查大阳月位置")
    add("source::recent_signal_limit", "MAX_CONFIRM_AGE" in src, "必须限制历史老信号反复推送")
    add("source::hit_sort", "sort_hits" in function_names, "命中结果必须排序，不能按股票池原始顺序输出")

    add("stock_pool::exclude_sh_000003_index", not common_stock_ok("sh.000003", "000003", "上证B股指数"), "必须剔除 sh.000003 上证B股指数")
    add("stock_pool::exclude_sh_000001_index", not common_stock_ok("sh.000001", "000001", "上证指数"), "必须剔除 sh.000001 上证指数")
    add("stock_pool::allow_sz_000001_stock", common_stock_ok("sz.000001", "000001", "平安银行"), "必须保留 sz.000001 普通股票")
    add("stock_pool::allow_sh_600000_stock", common_stock_ok("sh.600000", "600000", "浦发银行"), "必须保留 sh.600000 普通股票")

    item = StockItem("000001", "sz.000001", "测试股")
    good = valid_recent_qingtian_rows()

    add("rule::gt30_four_closes_hit", find_qingtian_hit(good, item, "2023-05-31") is not None, "大于30%且后续4根完整月收盘不破，必须命中")

    realistic = realistic_breakout_qingtian_rows()
    add("rule::real_breakout_position_not_mis killed".replace(" ", "_"), find_qingtian_hit(realistic, item, "2023-05-31") is not None, "真实突破型擎天：锚定月收盘可突破前高，不能因close_pos过高误杀")

    equal_30 = valid_recent_qingtian_rows(anchor_close=13.0)
    add("rule::equal_30_not_hit", find_qingtian_hit(equal_30, item, "2023-05-31") is None, "实体涨幅等于30%，不能命中")

    only_3 = good.iloc[:-1].copy()
    add("rule::only_three_future_not_hit", find_qingtian_hit(only_3, item, "2023-04-30") is None, "后续仅3根完整月K，不能命中")

    break_close = good.copy()
    break_close.loc[16, "close"] = 11.0
    add("rule::future_close_break_not_hit", find_qingtian_hit(break_close, item, "2023-05-31") is None, "任意后续完整月收盘跌破擎天位，原结构作废")

    shadow_break = good.copy()
    shadow_break.loc[14, "low"] = 6.0
    add("rule::shadow_break_still_hit", find_qingtian_hit(shadow_break, item, "2023-05-31") is not None, "影线跌破但收盘不破，仍然有效")

    low_volume = valid_recent_qingtian_rows(anchor_amount=90.0)
    add("rule::low_volume_big_yang_not_hit", find_qingtian_hit(low_volume, item, "2023-05-31") is None, "缩量大阳不能命中")

    high_position = valid_recent_qingtian_rows(anchor_open=30.0, anchor_close=40.0, anchor_amount=300.0)
    add("rule::high_position_big_yang_not_hit", find_qingtian_hit(high_position, item, "2023-05-31") is None, "高位大阳不能命中")

    sustained_signal = pd.concat([
        good,
        synthetic_monthly([
            ("2023-06-30", 12.1, 13.0, 11.5, 12.2, 120.0, 120.0),
            ("2023-07-31", 12.2, 13.0, 11.5, 12.2, 120.0, 120.0),
            ("2023-08-31", 12.2, 13.0, 11.5, 12.2, 120.0, 120.0),
            ("2023-09-30", 12.2, 13.0, 11.5, 12.2, 120.0, 120.0),
            ("2023-10-31", 12.2, 13.0, 11.5, 12.2, 120.0, 120.0),
            ("2023-11-30", 12.2, 13.0, 11.5, 12.2, 120.0, 120.0),
        ]),
    ], ignore_index=True)
    add("rule::sustained_signal_age6_still_hit", find_qingtian_hit(sustained_signal, item, "2023-11-30") is not None, "确认完成后6个月内仍未跌破擎天位，允许作为持续有效擎天")

    too_old_signal = pd.concat([
        sustained_signal,
        synthetic_monthly([("2023-12-31", 12.2, 13.0, 11.5, 12.2, 120.0, 120.0)]),
    ], ignore_index=True)
    add("rule::too_old_signal_not_hit", find_qingtian_hit(too_old_signal, item, "2023-12-31") is None, "确认完成超过默认6个月的历史擎天，不允许反复推送")

    unfinished = pd.concat([
        good,
        synthetic_monthly([("2023-06-15", 12.0, 13.0, 5.0, 5.5, 130.0, 130.0)]),
    ], ignore_index=True)
    add("rule::unfinished_current_month_ignored", find_qingtian_hit(unfinished, item, "2023-06-15") is not None, "当前月未走完时，必须剔除最后一根月K")

    unordered_hits = [
        QingtianHit("000003", "慢确认", "2023-01-31", "2023-05-31", "2023-07-31", 40.0, 12.2, 6, 2, 2.5, 0.2, 1.4),
        QingtianHit("000002", "新确认高开", "2023-01-31", "2023-05-31", "2023-05-31", 35.0, 12.2, 4, 0, 2.0, 0.6, 1.3),
        QingtianHit("000001", "新确认低开", "2023-01-31", "2023-05-31", "2023-05-31", 32.0, 12.2, 4, 0, 1.8, 0.3, 1.2),
    ]
    sorted_codes = [x.code for x in sort_hits(unordered_hits)]
    add("output::sort_hits_priority", sorted_codes == ["000001", "000002", "000003"], f"排序结果={sorted_codes}")

    report_text = build_report_text([
        QingtianHit("000001", "测试股", "2023-01-31", "2023-05-31", "2023-05-31", 32.0, 12.1333, 4, 0, 1.8, 0.3, 0.7)
    ]).strip()
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
