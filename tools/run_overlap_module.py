# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from datetime import timedelta

import daily_overlap_report as report


def completed_trade_date() -> str:
    manual = str(os.getenv("OVERLAP_TARGET_DATE") or "").strip()
    if manual:
        return manual
    now = report.now_bj()
    cutoff_hour = int(os.getenv("OVERLAP_TRADE_DATE_CUTOFF_HOUR_BJ", "18"))
    include_today = now.hour >= cutoff_hour
    try:
        import akshare as ak

        cal = ak.tool_trade_date_hist_sina()
        col = "trade_date" if "trade_date" in cal.columns else cal.columns[0]
        today = now.date()
        dates = report.pd.to_datetime(cal[col], errors="coerce").dt.date.dropna()
        valid = [d for d in dates if d <= today] if include_today else [d for d in dates if d < today]
        if valid:
            return max(valid).isoformat()
    except Exception:
        pass
    d = now.date()
    if not include_today:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def main() -> int:
    report.default_trade_date = completed_trade_date
    wanted = {x.strip() for x in os.getenv("OVERLAP_ONLY_MODULES", "").split(",") if x.strip()}
    if wanted:
        report.MODULES = [module for module in report.MODULES if module["key"] in wanted]
    return report.main()


if __name__ == "__main__":
    raise SystemExit(main())
