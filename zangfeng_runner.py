# -*- coding: utf-8 -*-
from __future__ import annotations

"""兼容入口。

正式藏锋指标已经移到根目录 `藏锋.py`，与 `灵动.py`、`破界.py`、`潮汐.py` 并列。
保留本文件只是为了防止旧命令 `python -u zangfeng_runner.py` 失效；新 workflow 直接运行 `藏锋.py`。
"""

import runpy
import sys
from pathlib import Path


def main() -> int:
    target = Path(__file__).resolve().with_name("藏锋.py")
    if not target.exists():
        raise SystemExit("缺少根目录藏锋.py")
    ns = runpy.run_path(str(target))
    fn = ns.get("main")
    if not callable(fn):
        raise SystemExit("藏锋.py 缺少 main(argv) 入口")
    return int(fn(sys.argv[1:]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
