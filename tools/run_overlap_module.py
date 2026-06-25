# -*- coding: utf-8 -*-
from __future__ import annotations

import os

import daily_overlap_report as report


def main() -> int:
    wanted = {x.strip() for x in os.getenv("OVERLAP_ONLY_MODULES", "").split(",") if x.strip()}
    if wanted:
        report.MODULES = [module for module in report.MODULES if module["key"] in wanted]
    return report.main()


if __name__ == "__main__":
    raise SystemExit(main())
