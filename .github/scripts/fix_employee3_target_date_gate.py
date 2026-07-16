# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
EMPLOYEE3_WORKFLOW = WORKFLOW_DIR / "employee3_runner.yml"
REPORT_GUARD = ROOT / "employee3_report_guard.py"
SELF_WORKFLOW = WORKFLOW_DIR / "_one_time_fix_employee3_target_date_gate.yml"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected once, found {count}")
    return text.replace(old, new, 1)


def patch_workflow(text: str) -> str:
    if "REQUESTED_TARGET_REASON:" not in text:
        text = replace_once(
            text,
            "          REQUESTED_TARGET_DATE: ${{ steps.config.outputs.target_date }}\n"
            "          EVENT_NAME: ${{ github.event_name }}\n",
            "          REQUESTED_TARGET_DATE: ${{ steps.config.outputs.target_date }}\n"
            "          REQUESTED_TARGET_REASON: ${{ steps.config.outputs.target_reason }}\n"
            "          EVENT_NAME: ${{ github.event_name }}\n",
            "cache-state target reason env",
        )

    if 'requested_reason = str(os.environ.get("REQUESTED_TARGET_REASON") or "").strip()' not in text:
        text = replace_once(
            text,
            '          requested = str(os.environ.get("REQUESTED_TARGET_DATE") or "").strip()\n'
            '          event_name = str(os.environ.get("EVENT_NAME") or "").strip()\n',
            '          requested = str(os.environ.get("REQUESTED_TARGET_DATE") or "").strip()\n'
            '          requested_reason = str(os.environ.get("REQUESTED_TARGET_REASON") or "").strip()\n'
            '          event_name = str(os.environ.get("EVENT_NAME") or "").strip()\n',
            "cache-state requested reason variable",
        )

    old_resolution = '          resolved = requested if event_name == "workflow_dispatch" and requested else effective\n'
    new_resolution = (
        '          preserve_requested = requested_reason == "manual_date" or (\n'
        '              requested_reason.startswith("dropdown_offset_") and requested_reason != "dropdown_offset_0"\n'
        '          )\n'
        '          resolved = requested if event_name == "workflow_dispatch" and preserve_requested and requested else effective\n'
    )
    if old_resolution in text:
        text = replace_once(text, old_resolution, new_resolution, "effective target resolution")
    elif new_resolution not in text:
        raise RuntimeError("effective target resolution marker missing")

    if 'print(f"target_resolution_reason=' not in text:
        text = replace_once(
            text,
            '          print(f"effective_target_date={resolved}")\n'
            '          print(f"cache_latest_trade_date={effective}")\n',
            '          print(f"effective_target_date={resolved}")\n'
            '          print(f"target_resolution_reason={\'requested_history\' if preserve_requested else \'cache_effective\'}")\n'
            '          print(f"cache_latest_trade_date={effective}")\n',
            "target resolution output",
        )

    if 'echo "target resolution: ${{ steps.cache_state.outputs.target_resolution_reason }}"' not in text:
        marker = '      - name: save refreshed shared kline cache\n'
        insert = (
            '      - name: inspect resolved employee3 trade date\n'
            '        if: always()\n'
            '        run: |\n'
            '          echo "requested target: ${{ steps.config.outputs.target_date }}"\n'
            '          echo "requested reason: ${{ steps.config.outputs.target_reason }}"\n'
            '          echo "cache latest trade date: ${{ steps.cache_state.outputs.cache_latest_trade_date }}"\n'
            '          echo "effective target: ${{ steps.cache_state.outputs.effective_target_date }}"\n'
            '          echo "target resolution: ${{ steps.cache_state.outputs.target_resolution_reason }}"\n\n'
        )
        if marker not in text:
            raise RuntimeError("cache save step marker missing")
        text = text.replace(marker, insert + marker, 1)

    return text


def patch_guard(text: str) -> str:
    stat_block = (
        '    prefilter_total = int(sf(stat.get("prefilter_total_cache_stocks"), 0))\n'
        '    prefilter_checked = int(sf(stat.get("prefilter_target_fresh_checked"), 0))\n'
        '    prefilter_stale = int(sf(stat.get("prefilter_stale_skipped"), 0))\n'
        '    date_gate_blocked_all = (\n'
        '        raw_total == 0\n'
        '        and prefilter_total > 0\n'
        '        and prefilter_checked == 0\n'
        '        and prefilter_stale >= prefilter_total\n'
        '    )\n'
    )
    if "date_gate_blocked_all = (" not in text:
        text = replace_once(
            text,
            '    target_pool = [r for r in rows if is_target_row(r, target)]\n\n',
            '    target_pool = [r for r in rows if is_target_row(r, target)]\n'
            + stat_block
            + '\n',
            "guard prefilter date-gate stats",
        )

    old_branch = (
        '    elif raw_total == 0:\n'
        '        guard_status = "WARN"\n'
        '        guard_action = "今日无核心线突破深度命中"\n'
        '        title = f"三号员工Top5｜无深度命中｜{target or \'\'}"\n'
    )
    new_branch = (
        '    elif date_gate_blocked_all:\n'
        '        guard_status = "DATA_STALE"\n'
        '        guard_action = "目标日与缓存最新交易日不一致，全部股票在日期门控前被跳过"\n'
        '        title = f"三号员工数据日期错位｜停止选股｜{target or \'\'}"\n'
        '    elif raw_total == 0:\n'
        '        guard_status = "WARN"\n'
        '        guard_action = "今日无核心线突破深度命中"\n'
        '        title = f"三号员工Top5｜无深度命中｜{target or \'\'}"\n'
    )
    if old_branch in text:
        text = replace_once(text, old_branch, new_branch, "guard date-gate branch")
    elif new_branch not in text:
        raise RuntimeError("guard raw-total branch marker missing")

    prefilter_line = (
        '        f"日期门控检查{prefilter_checked}/{prefilter_total}只｜日期错位跳过{prefilter_stale}只",\n'
    )
    if "日期门控检查{prefilter_checked}/{prefilter_total}" not in text:
        text = replace_once(
            text,
            '        f"缓存文件{stat.get(\'cache_files\', \'未知\')}｜有效缓存{stat.get(\'cache_hit\', \'未知\')}｜坏缓存{stat.get(\'bad\', 0)}｜短缓存{stat.get(\'short\', 0)}",\n',
            '        f"缓存文件{stat.get(\'cache_files\', \'未知\')}｜有效缓存{stat.get(\'cache_hit\', \'未知\')}｜坏缓存{stat.get(\'bad\', 0)}｜短缓存{stat.get(\'short\', 0)}",\n'
            + prefilter_line,
            "guard prefilter summary line",
        )

    old_conclusion = (
        '    if guard_status == "DATA_STALE":\n'
        '        lines += ["", "结论：目标日有效候选不足，当前结果按数据异常处理，不按‘市场无票’处理。", "动作：先更新公共K线缓存，或手动允许三号员工 BaoStock 补拉最近K线后重跑。"]\n'
    )
    new_conclusion = (
        '    if date_gate_blocked_all:\n'
        '        stale_dates = ss(stat.get("prefilter_stale_date_counts_text")) or "未知"\n'
        '        lines += [\n'
        '            "",\n'
        '            "结论：本轮没有进入核心线扫描，不是市场无票；目标日早于/晚于缓存实际交易日，全部股票被日期门控挡住。",\n'
        '            f"缓存实际日期分布:{stale_dates}",\n'
        '            "动作：使用缓存刷新程序确认的实际交易日重跑；默认‘当前交易日’不得覆盖缓存有效交易日。",\n'
        '        ]\n'
        '    elif guard_status == "DATA_STALE":\n'
        '        lines += ["", "结论：目标日有效候选不足，当前结果按数据异常处理，不按‘市场无票’处理。", "动作：先更新公共K线缓存，或手动允许三号员工 BaoStock 补拉最近K线后重跑。"]\n'
    )
    if old_conclusion in text:
        text = replace_once(text, old_conclusion, new_conclusion, "guard date-gate conclusion")
    elif new_conclusion not in text:
        raise RuntimeError("guard DATA_STALE conclusion marker missing")

    return text


def find_schedules(text: str) -> list[str]:
    return re.findall(r"cron:\s*[\"']?([^\"'\n]+)", text)


def self_check(workflow: str, guard: str) -> None:
    # 自检一：日期解析规则覆盖默认当前日、历史下拉、手填日期和定时运行。
    def resolve(event: str, reason: str, requested: str, effective: str) -> str:
        preserve = reason == "manual_date" or (reason.startswith("dropdown_offset_") and reason != "dropdown_offset_0")
        return requested if event == "workflow_dispatch" and preserve and requested else effective

    assert resolve("workflow_dispatch", "dropdown_offset_0", "2026-07-16", "2026-07-15") == "2026-07-15"
    assert resolve("workflow_dispatch", "dropdown_offset_2", "2026-07-14", "2026-07-15") == "2026-07-14"
    assert resolve("workflow_dispatch", "manual_date", "2026-06-30", "2026-07-15") == "2026-06-30"
    assert resolve("schedule", "dropdown_offset_0", "2026-07-16", "2026-07-16") == "2026-07-16"
    print("SELF-CHECK-1 PASS：默认当前日服从缓存实际交易日，显式历史回看保持不变。", flush=True)

    # 自检二：YAML和Python均可解析。
    parsed = yaml.load(workflow, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    compile(guard, str(REPORT_GUARD), "exec")
    assert "target_resolution_reason" in workflow
    assert "date_gate_blocked_all" in guard
    print("SELF-CHECK-2 PASS：Workflow YAML与守门Python语法正常。", flush=True)

    # 自检三：唯一日程、引擎及生产链路关键标记仍完整。
    offenders = {}
    for path in WORKFLOW_DIR.glob("*.y*ml"):
        if path == SELF_WORKFLOW:
            continue
        schedules = find_schedules(path.read_text(encoding="utf-8"))
        if schedules and path.name != EMPLOYEE3_WORKFLOW.name:
            offenders[path.name] = schedules
    assert offenders == {}, offenders
    assert find_schedules(workflow) == ["10 13 * * 1-5"]
    for marker in (
        "python -u tools/update_kline_cache_daily.py",
        "python -u employee3_runner.py",
        "python -u employee3_report_guard.py",
        "TELEGRAM_BOT_TOKEN",
        "DATA_GATE_TARGET_DATE",
        "actions/cache/save@v4",
    ):
        assert marker in workflow, marker
    print("SELF-CHECK-3 PASS：唯一日程、缓存、引擎、报告和Telegram链路完整。", flush=True)


def main() -> None:
    workflow = EMPLOYEE3_WORKFLOW.read_text(encoding="utf-8")
    guard = REPORT_GUARD.read_text(encoding="utf-8")

    workflow = patch_workflow(workflow)
    guard = patch_guard(guard)
    self_check(workflow, guard)

    EMPLOYEE3_WORKFLOW.write_text(workflow, encoding="utf-8")
    REPORT_GUARD.write_text(guard, encoding="utf-8")


if __name__ == "__main__":
    main()
