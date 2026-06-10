# -*- coding: utf-8 -*-
"""
擎天.py

擎天战法全市场月线扫描器。

核心定义：
1）月线大阳线必须是真阳线，且实体涨幅严格大于 30%。
   实体涨幅 = (收盘价 - 开盘价) / 开盘价 * 100。
2）擎天位 = 大阳线开盘价 + 大阳线实体高度 * 2/3，约等于实体上 1/3 位，也就是 66.7% 位。
3）大阳线之后必须连续超过 3 根月K收盘不破擎天位，即至少 4 根后续月K收盘价 >= 擎天位。
4）只看收盘不破；最低价只作为回踩深度与风险提示，不作为硬淘汰。

输出目录：artifacts/
- qingtian_latest.csv
- qingtian_latest.json
- qingtian_report.md
- qingtian_failed.csv（仅失败时生成）

运行：
python 擎天.py --self-test
python 擎天.py --scan
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
from typing import Iterable, List, Optional, Tuple

import pandas as pd

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


VERSION = "擎天-v2.0.0"
DEFAULT_OUTPUT_DIR = Path("artifacts")
DEFAULT_CACHE_DIR = Path("qingtian_cache")
DEFAULT_MIN_BODY_PCT = 30.0
DEFAULT_CONFIRM_MONTHS = 4
DEFAULT_QINGTIAN_RATIO = 2 / 3


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
    scanned_count: int
    success_count: int
    failed_count: int
    signal_count: int
    strict_body_pct: float
    required_confirm_months: int
    qingtian_ratio: float
    cache_dir: str
    output_dir: str
    telegram_enabled: bool


def now_cn() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def normalize_code(raw: str) -> str:
    code = str(raw or "").strip()
    if "." in code:
        parts = code.split(".")
        code = parts[-1] if len(parts[-1]) == 6 else parts[0]
    digits = re.sub(r"\D", "", code)
    return digits.zfill(6)[-6:]


def qingtian_level(open_price: float, close_price: float, ratio: float = DEFAULT_QINGTIAN_RATIO) -> float:
    return float(open_price + (close_price - open_price) * ratio)


def prepare_monthly_df(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "日期": "date", "时间": "date", "交易日期": "date",
        "开盘": "open", "开盘价": "open",
        "收盘": "close", "收盘价": "close",
        "最高": "high", "最高价": "high",
        "最低": "low", "最低价": "low",
        "成交量": "volume", "成交额": "amount",
        "date": "date", "open": "open", "close": "close", "high": "high", "low": "low",
        "volume": "volume", "amount": "amount",
    }
    if df is None or df.empty:
        raise ValueError("月线数据为空")
    data = df.rename(columns={c: rename_map.get(c, c) for c in df.columns}).copy()
    required = ["date", "open", "high", "low", "close"]
    missing = [c for c in required if c not in data.columns]
    if missing:
        raise ValueError(f"月线数据缺少字段: {missing}")
    keep_cols = required + [c for c in ["volume", "amount"] if c in data.columns]
    data = data[keep_cols].copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    for col in ["open", "high", "low", "close"]:
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.dropna(subset=required)
    data = data[(data["open"] > 0) & (data["high"] > 0) & (data["low"] > 0) & (data["close"] > 0)]
    data = data.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    data["date"] = data["date"].dt.strftime("%Y-%m-%d")
    return data


def find_qingtian_signals(
    monthly_df: pd.DataFrame,
    code: str = "",
    name: str = "",
    min_body_pct: float = DEFAULT_MIN_BODY_PCT,
    required_confirm_months: int = DEFAULT_CONFIRM_MONTHS,
    ratio: float = DEFAULT_QINGTIAN_RATIO,
) -> List[QingtianSignal]:
    df = prepare_monthly_df(monthly_df)
    signals: List[QingtianSignal] = []
    if required_confirm_months < 4:
        raise ValueError("擎天规则要求后续超过3根月K，required_confirm_months不能小于4")
    if len(df) <= required_confirm_months:
        return signals

    for i in range(0, len(df) - required_confirm_months):
        row = df.iloc[i]
        o, c = float(row["open"]), float(row["close"])
        h, l = float(row["high"]), float(row["low"])
        if c <= o:
            continue
        body_pct = (c - o) / o * 100.0
        # “超过30个点”必须严格大于30%，不能写成 >= 30%。
        if body_pct <= float(min_body_pct):
            continue

        level = qingtian_level(o, c, ratio=float(ratio))
        future = df.iloc[i + 1 :].copy()
        confirm = 0
        for close_price in future["close"].astype(float).tolist():
            if close_price >= level:
                confirm += 1
            else:
                break
        if confirm < int(required_confirm_months):
            continue

        accepted = future.iloc[:confirm]
        last = accepted.iloc[-1]
        last_close = float(last["close"])
        min_close_after = float(accepted["close"].min())
        min_low_after = float(accepted["low"].min())
        distance = (last_close / level - 1.0) * 100.0
        close_cushion = (min_close_after / level - 1.0) * 100.0
        low_drawdown = (min_low_after / level - 1.0) * 100.0
        if confirm >= 8 and close_cushion >= 0:
            status = "擎天长期锁筹"
        elif confirm >= 4 and low_drawdown < -12:
            status = "擎天收盘不破但影线深踩"
        else:
            status = "擎天确认"
        signals.append(
            QingtianSignal(
                code=normalize_code(code) if code else "",
                name=name or "",
                qingtian_month=str(row["date"]),
                qingtian_open=round(o, 4),
                qingtian_close=round(c, 4),
                qingtian_high=round(h, 4),
                qingtian_low=round(l, 4),
                body_pct=round(body_pct, 2),
                qingtian_level=round(level, 4),
                confirm_months=int(confirm),
                last_month=str(last["date"]),
                last_close=round(last_close, 4),
                distance_to_qingtian_pct=round(distance, 2),
                max_drawdown_close_pct=round(close_cushion, 2),
                lowest_low_after_pct=round(low_drawdown, 2),
                months_since_qingtian=int(len(df) - i - 1),
                status=status,
            )
        )
    return signals


def get_stock_pool() -> pd.DataFrame:
    try:
        import akshare as ak
    except Exception as exc:
        raise RuntimeError("缺少 akshare，请先 pip install akshare") from exc

    spot = ak.stock_zh_a_spot_em()
    if spot is None or spot.empty:
        raise RuntimeError("AkShare股票池返回为空")
    code_col = "代码" if "代码" in spot.columns else "code"
    name_col = "名称" if "名称" in spot.columns else "name"
    pool = spot[[code_col, name_col]].rename(columns={code_col: "code", name_col: "name"}).copy()
    pool["code"] = pool["code"].map(normalize_code)
    pool["name"] = pool["name"].astype(str).str.strip()
    bad_name = pool["name"].str.contains(r"ST|\*ST|退市|退|ETF|基金|债|转债|B股", case=False, regex=True, na=False)
    bad_code = pool["code"].str.startswith(("8", "4"))
    pool = pool[~bad_name & ~bad_code]
    pool = pool[pool["code"].str.len() == 6]
    return pool.drop_duplicates("code").sort_values("code").reset_index(drop=True)


def read_cache(cache_file: Path) -> Optional[pd.DataFrame]:
    if not cache_file.exists():
        return None
    try:
        cached = pd.read_csv(cache_file)
        if cached.empty:
            return None
        return cached.drop(columns=["cache_date"], errors="ignore")
    except Exception:
        return None


def fetch_monthly_with_cache(code: str, cache_dir: Path, sleep_sec: float = 0.12, force_refresh: bool = False) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    code = normalize_code(code)
    cache_file = cache_dir / f"{code}_monthly.csv"
    today = now_cn().strftime("%Y-%m-%d")
    if not force_refresh and cache_file.exists():
        try:
            cached_raw = pd.read_csv(cache_file)
            if "cache_date" in cached_raw.columns and str(cached_raw["cache_date"].iloc[-1]) == today:
                return prepare_monthly_df(cached_raw.drop(columns=["cache_date"], errors="ignore"))
        except Exception:
            pass

    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=code, period="monthly", start_date="19900101", end_date="20991231", adjust="qfq")
        if df is None or df.empty:
            raise ValueError("AkShare返回空月线数据")
        df = prepare_monthly_df(df)
        out = df.copy()
        out["cache_date"] = today
        out.to_csv(cache_file, index=False, encoding="utf-8-sig")
        if sleep_sec > 0:
            time.sleep(float(sleep_sec))
        return df
    except Exception as exc:
        cached = read_cache(cache_file)
        if cached is not None and not cached.empty:
            return prepare_monthly_df(cached)
        raise RuntimeError(f"获取月线失败 {code}: {exc}") from exc


def rank_signals(signals: Iterable[QingtianSignal]) -> pd.DataFrame:
    rows = [asdict(s) for s in signals]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["qingtian_month_dt"] = pd.to_datetime(df["qingtian_month"], errors="coerce")
    df["rank_score"] = (
        df["body_pct"].clip(upper=85) * 0.45
        + df["confirm_months"].clip(upper=18) * 2.8
        + df["distance_to_qingtian_pct"].clip(lower=0, upper=55) * 0.18
        - df["lowest_low_after_pct"].clip(upper=0).abs() * 0.12
        - df["months_since_qingtian"].clip(lower=0, upper=60) * 0.03
    ).round(2)
    df = df.sort_values(["qingtian_month_dt", "rank_score", "confirm_months"], ascending=[False, False, False])
    return df.drop(columns=["qingtian_month_dt"]).reset_index(drop=True)


def build_markdown_report(df: pd.DataFrame, summary: ScanSummary) -> str:
    lines = [
        "# 擎天扫描报告", "",
        f"- 版本：{summary.version}",
        f"- 运行时间：{summary.run_time}",
        f"- 扫描数量：{summary.scanned_count}",
        f"- 成功数量：{summary.success_count}",
        f"- 失败数量：{summary.failed_count}",
        f"- 命中数量：{summary.signal_count}",
        f"- 严格规则：月线阳实体涨幅 > {summary.strict_body_pct:.1f}%，后续至少 {summary.required_confirm_months} 根月K收盘不破擎天位",
        f"- 擎天位比例：实体 {summary.qingtian_ratio * 100:.2f}% 位",
        "", "## 交易解释", "",
        "擎天不是单纯追大阳线，而是看一根月线大阳线把筹码成本抬上去后，后续至少四个月收盘都守在实体上三分之一位置之上。守得住，说明这根大阳线不是一日游，资金在高位承接，筹码没有快速崩掉。",
        "",
    ]
    if df.empty:
        lines.append("本次没有命中擎天结构。")
        return "\n".join(lines)
    show_cols = [
        "code", "name", "qingtian_month", "body_pct", "qingtian_level",
        "confirm_months", "last_month", "last_close", "distance_to_qingtian_pct",
        "lowest_low_after_pct", "status", "rank_score",
    ]
    lines += ["## 命中列表", ""]
    try:
        lines.append(df[show_cols].head(120).to_markdown(index=False))
    except Exception:
        lines.append(df[show_cols].head(120).to_csv(index=False))
    lines += ["", "## 字段说明", ""]
    lines += [
        "- 擎天月：出现月线大阳实体涨幅超过30%的月份。",
        "- 擎天位：大阳线实体66.7%位，也就是后续收盘不能跌破的强势承接线。",
        "- 确认月数：擎天月之后连续收盘不破擎天位的月K数量，必须大于3。",
        "- 最低影线回踩：允许影线刺穿，但如果影线回踩过深，报告会提示风险。",
        "- rank_score：只用于排序，不等于买入建议。",
    ]
    return "\n".join(lines)


def telegram_enabled_by_env(args_telegram: bool) -> bool:
    if not args_telegram:
        return False
    for key in ["ENABLE_TELEGRAM", "QINGTIAN_SEND_TELEGRAM"]:
        value = str(os.getenv(key, "")).strip().lower()
        if value in {"0", "false", "no", "off"}:
            return False
    return True


def send_telegram(text: str) -> bool:
    token = (
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        or os.getenv("TELEGRAM_TOKEN", "").strip()
        or os.getenv("TG_BOT_TOKEN", "").strip()
    )
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or os.getenv("TG_CHAT_ID", "").strip()
    if not token or not chat_id or requests is None:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:3900], "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=25,
        )
        return bool(resp.ok)
    except Exception:
        return False


def build_telegram_text(df: pd.DataFrame, summary: ScanSummary) -> str:
    lines = [
        "擎天扫描完成",
        f"命中：{summary.signal_count}",
        f"成功：{summary.success_count}",
        f"失败：{summary.failed_count}",
    ]
    if not df.empty:
        lines.append("\nTop命中：")
        for r in df.head(12).itertuples():
            lines.append(f"{r.code} {r.name}｜擎天位{r.qingtian_level}｜{r.qingtian_month}｜守{r.confirm_months}月｜{r.status}")
    return "\n".join(lines)


def run_scan(args: argparse.Namespace) -> Tuple[pd.DataFrame, ScanSummary]:
    output_dir, cache_dir = Path(args.output_dir), Path(args.cache_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    pool = get_stock_pool()
    if args.limit and int(args.limit) > 0:
        pool = pool.head(int(args.limit)).copy()

    all_signals: List[QingtianSignal] = []
    failed: List[dict] = []
    success = 0
    for idx, item in pool.iterrows():
        code, name = normalize_code(item["code"]), str(item["name"])
        try:
            monthly = fetch_monthly_with_cache(code, cache_dir=cache_dir, sleep_sec=float(args.sleep), force_refresh=bool(args.force_refresh))
            all_signals.extend(
                find_qingtian_signals(
                    monthly,
                    code=code,
                    name=name,
                    min_body_pct=float(args.min_body_pct),
                    required_confirm_months=int(args.confirm_months),
                    ratio=float(args.ratio),
                )
            )
            success += 1
        except Exception as exc:
            failed.append({"code": code, "name": name, "error": str(exc)[:500]})
        if (idx + 1) % int(args.progress_every) == 0:
            print(f"已扫描 {idx + 1}/{len(pool)}，当前命中 {len(all_signals)}，失败 {len(failed)}", flush=True)

    result_df = rank_signals(all_signals)
    enable_tg = telegram_enabled_by_env(bool(args.telegram))
    summary = ScanSummary(
        version=VERSION,
        run_time=now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        scanned_count=int(len(pool)),
        success_count=int(success),
        failed_count=int(len(failed)),
        signal_count=int(len(result_df)),
        strict_body_pct=float(args.min_body_pct),
        required_confirm_months=int(args.confirm_months),
        qingtian_ratio=float(args.ratio),
        cache_dir=str(cache_dir),
        output_dir=str(output_dir),
        telegram_enabled=enable_tg,
    )

    result_df.to_csv(output_dir / "qingtian_latest.csv", index=False, encoding="utf-8-sig")
    (output_dir / "qingtian_latest.json").write_text(json.dumps({"summary": asdict(summary), "signals": result_df.to_dict("records")}, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "qingtian_report.md").write_text(build_markdown_report(result_df, summary), encoding="utf-8")
    if failed:
        pd.DataFrame(failed).to_csv(output_dir / "qingtian_failed.csv", index=False, encoding="utf-8-sig")
    else:
        failed_file = output_dir / "qingtian_failed.csv"
        if failed_file.exists():
            failed_file.unlink()

    if enable_tg:
        ok = send_telegram(build_telegram_text(result_df, summary))
        print(f"Telegram发送状态: {'成功' if ok else '未发送或失败'}", flush=True)
    return result_df, summary


def self_test() -> None:
    # 1. 正好30%不算命中，必须严格大于30%。
    df_equal_30 = pd.DataFrame([
        {"date": "2024-01-31", "open": 10, "high": 13, "low": 9.8, "close": 13},
        {"date": "2024-02-29", "open": 13, "high": 13.5, "low": 12, "close": 12.1},
        {"date": "2024-03-31", "open": 12.1, "high": 13, "low": 12, "close": 12.2},
        {"date": "2024-04-30", "open": 12.2, "high": 13, "low": 12, "close": 12.3},
        {"date": "2024-05-31", "open": 12.3, "high": 13, "low": 12, "close": 12.4},
    ])
    assert len(find_qingtian_signals(df_equal_30, min_body_pct=30, required_confirm_months=4)) == 0

    # 2. 大于30%，后续4根收盘不破，命中。
    df_hit = pd.DataFrame([
        {"date": "2024-01-31", "open": 10, "high": 14, "low": 9.8, "close": 13.2},
        {"date": "2024-02-29", "open": 13.2, "high": 13.5, "low": 11.0, "close": 12.2},
        {"date": "2024-03-31", "open": 12.2, "high": 13, "low": 11.1, "close": 12.2},
        {"date": "2024-04-30", "open": 12.0, "high": 13, "low": 11.2, "close": 12.2},
        {"date": "2024-05-31", "open": 12.1, "high": 13, "low": 11.1, "close": 12.3},
    ])
    sig = find_qingtian_signals(df_hit, code="000001", name="测试", min_body_pct=30, required_confirm_months=4)
    assert len(sig) == 1
    assert sig[0].confirm_months == 4
    assert math.isclose(sig[0].qingtian_level, round(10 + (13.2 - 10) * 2 / 3, 4), rel_tol=0, abs_tol=1e-4)

    # 3. 只有3根后续K线不算，必须大于3根，即至少4根。
    assert len(find_qingtian_signals(df_hit.iloc[:4].copy(), min_body_pct=30, required_confirm_months=4)) == 0

    # 4. 收盘跌破擎天位，失败。
    df_break = df_hit.copy(); df_break.loc[3, "close"] = 11.0
    assert len(find_qingtian_signals(df_break, min_body_pct=30, required_confirm_months=4)) == 0

    # 5. 影线跌破但收盘不破，仍然命中。
    df_low_break = df_hit.copy(); df_low_break.loc[2, "low"] = 8.0
    assert len(find_qingtian_signals(df_low_break, min_body_pct=30, required_confirm_months=4)) == 1

    # 6. 字段中文/英文兼容。
    cn = df_hit.rename(columns={"date": "日期", "open": "开盘", "high": "最高", "low": "最低", "close": "收盘"})
    assert len(find_qingtian_signals(cn, min_body_pct=30, required_confirm_months=4)) == 1

    # 7. 排序与报告生成不报错。
    ranked = rank_signals(sig)
    summary = ScanSummary(VERSION, now_cn().strftime("%Y-%m-%d %H:%M:%S"), 1, 1, 0, len(ranked), 30, 4, DEFAULT_QINGTIAN_RATIO, "cache", "artifacts", False)
    report = build_markdown_report(ranked, summary)
    assert "擎天扫描报告" in report and "000001" in report
    print("擎天自检通过：7/7")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="擎天战法全市场月线扫描器")
    parser.add_argument("--scan", action="store_true", help="运行全市场扫描")
    parser.add_argument("--self-test", action="store_true", help="运行内置自检")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR), help="缓存目录")
    parser.add_argument("--min-body-pct", type=float, default=DEFAULT_MIN_BODY_PCT, help="月线实体涨幅阈值，严格大于该值")
    parser.add_argument("--confirm-months", type=int, default=DEFAULT_CONFIRM_MONTHS, help="后续至少多少根月K收盘不破；4表示大于3根")
    parser.add_argument("--ratio", type=float, default=DEFAULT_QINGTIAN_RATIO, help="擎天位比例，默认实体66.7%%位")
    parser.add_argument("--limit", type=int, default=int(os.getenv("QINGTIAN_SCAN_LIMIT", "0") or 0), help="调试用，仅扫描前N只；0为全市场")
    parser.add_argument("--sleep", type=float, default=float(os.getenv("QINGTIAN_REQUEST_SLEEP", "0.12") or 0.12), help="单票请求间隔秒数")
    parser.add_argument("--progress-every", type=int, default=200, help="每扫描N只打印一次进度")
    parser.add_argument("--force-refresh", action="store_true", help="忽略当日缓存，强制刷新月线")
    parser.add_argument("--telegram", action="store_true", help="若环境变量存在且开关允许则推送Telegram")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
            return 0
        if args.scan:
            _, summary = run_scan(args)
            print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
            return 0
        parser.print_help()
        return 0
    except Exception as exc:
        print(f"擎天运行失败: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
