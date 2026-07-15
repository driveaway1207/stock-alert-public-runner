# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path('.github/workflows')
EMPLOYEE3 = ROOT / 'employee3_runner.yml'
SELF = ROOT / '_one_time_employee3_only.yml'
SCRIPT = Path('.github/scripts/consolidate_employee3_only.py')
DISABLE = [
    ROOT / 'stock_alert.yml',
    ROOT / 'qingtian.yml',
    ROOT / '灵动.yml',
    ROOT / '破界.yml',
    ROOT / '潮汐.yml',
    ROOT / 'zangfeng.yml',
    ROOT / 'daily_overlap_report.yml',
    ROOT / 'export_neckline_candidates.yml',
]


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def top_on(line: str) -> bool:
    return indentation(line) == 0 and line.strip() in {'on:', '"on":', "'on':"}


def strip_top_schedule(text: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    inside_on = False
    removed = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if top_on(line):
            inside_on = True
            output.append(line)
            i += 1
            continue
        if inside_on and indentation(line) == 0 and stripped and not stripped.startswith('#'):
            inside_on = False
        if inside_on and indentation(line) == 2 and stripped == 'schedule:':
            removed = True
            i += 1
            while i < len(lines):
                candidate = lines[i]
                if candidate.strip() and indentation(candidate) <= 2:
                    break
                i += 1
            continue
        output.append(line)
        i += 1
    result = ''.join(output)
    if removed and '自动定时运行已关闭' not in result:
        rows = result.splitlines(keepends=True)
        for index, row in enumerate(rows):
            if top_on(row):
                rows.insert(index + 1, '  # 自动定时运行已关闭；保留原有手动或事件触发。\n')
                break
        result = ''.join(rows)
    return result, removed


def find_top_schedules(text: str) -> list[str]:
    lines = text.splitlines()
    inside_on = False
    values: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if top_on(line):
            inside_on = True
            i += 1
            continue
        if inside_on and indentation(line) == 0 and stripped and not stripped.startswith('#'):
            inside_on = False
        if inside_on and indentation(line) == 2 and stripped == 'schedule:':
            i += 1
            while i < len(lines):
                candidate = lines[i]
                candidate_text = candidate.strip()
                if candidate_text and indentation(candidate) <= 2:
                    break
                match = re.search(r"cron:\s*['\"]?([^'\"]+)['\"]?", candidate_text)
                if match:
                    values.append(match.group(1).strip())
                i += 1
            continue
        i += 1
    return values


def gh_expression(name: str) -> str:
    return '$' + '{{ ' + name + ' }}'


def patch_employee3(text: str) -> str:
    text = text.replace('北京时间20:10', '北京时间21:10')
    text, replacements = re.subn(
        r'(?m)^(\s*-\s*cron:\s*)["\']10 12 \* \* 1-5["\']\s*$',
        r'\1"10 13 * * 1-5"',
        text,
        count=1,
    )
    if replacements == 0 and '10 13 * * 1-5' not in text:
        raise RuntimeError('三号员工原定时表达式未找到')

    engine_marker = '      - name: run employee3 native engine\n'
    if engine_marker not in text:
        raise RuntimeError('三号员工引擎步骤未找到')

    requested_date = gh_expression('steps.config.outputs.target_date')
    effective_date = gh_expression('steps.cache_state.outputs.effective_target_date')
    cache_key = (
        'a-kline-cache-'
        + gh_expression('github.ref_name')
        + '-employee3-'
        + gh_expression('github.run_id')
    )

    cache_steps = f'''      - name: refresh shared kline cache for employee3
        env:
          PYTHONUNBUFFERED: 1
          MAX_RUNTIME_MINUTES: "45"
          SOFT_STOP_BUFFER_MINUTES: "5"
          MIN_FRESH_COVERAGE: "0.965"
          ALLOW_STOCK_ALERT_IF_STALE_ONLY: "1"
        run: |
          mkdir -p outputs kline_cache
          python -u tools/update_kline_cache_daily.py

      - name: validate refreshed cache and resolve effective trade date
        id: cache_state
        shell: bash
        env:
          REQUESTED_TARGET_DATE: {requested_date}
        run: |
          python - <<'PY' >> "$GITHUB_OUTPUT"
          import json
          from pathlib import Path
          state_file = Path("outputs/daily_kline_update_state.json")
          if not state_file.exists():
              raise SystemExit("missing cache refresh state")
          state = json.loads(state_file.read_text(encoding="utf-8"))
          effective = str(state.get("目标交易日") or "").strip()
          coverage = float(state.get("fresh_coverage") or 0.0)
          allowed = bool(state.get("should_run_stock_alert"))
          if not effective or not allowed:
              raise SystemExit(f"cache gate failed: effective={{effective}} coverage={{coverage:.6f}}")
          print(f"effective_target_date={{effective}}")
          print(f"fresh_coverage={{coverage:.6f}}")
          PY

      - name: save refreshed shared kline cache
        uses: actions/cache/save@v4
        with:
          path: kline_cache
          key: {cache_key}

      - name: upload employee3 cache refresh report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: employee3-cache-refresh-report
          path: |
            outputs/daily_kline_update_report_*.csv
            outputs/daily_kline_update_summary_*.csv
            outputs/daily_kline_update_state.json
          if-no-files-found: warn
          retention-days: 14

'''

    if 'refresh shared kline cache for employee3' not in text:
        before, after = text.split(engine_marker, 1)
        text = before + cache_steps + engine_marker + after

    engine_position = text.index(engine_marker)
    text = text[:engine_position] + text[engine_position:].replace(requested_date, effective_date)
    return text


def main() -> None:
    print('开始关闭非三号员工定时触发。', flush=True)
    for path in DISABLE:
        if not path.exists():
            raise RuntimeError(f'需要关闭定时的工作流不存在: {path}')
        original = path.read_text(encoding='utf-8')
        before = find_top_schedules(original)
        updated, removed = strip_top_schedule(original)
        print(f'{path.name}: schedule_before={before} removed={removed}', flush=True)
        if before and not removed:
            raise RuntimeError(f'未能关闭定时: {path}')
        path.write_text(updated, encoding='utf-8')

    original_employee3 = EMPLOYEE3.read_text(encoding='utf-8')
    print(f'employee3 schedule_before={find_top_schedules(original_employee3)}', flush=True)
    EMPLOYEE3.write_text(patch_employee3(original_employee3), encoding='utf-8')

    for path in DISABLE:
        assert find_top_schedules(path.read_text(encoding='utf-8')) == [], path
    assert find_top_schedules(EMPLOYEE3.read_text(encoding='utf-8')) == ['10 13 * * 1-5']
    print('SELF-CHECK-1 PASS：指定的非三号定时全部关闭，三号保留唯一日程。', flush=True)

    all_schedules = {
        path.name: find_top_schedules(path.read_text(encoding='utf-8'))
        for path in ROOT.glob('*.y*ml')
        if path != SELF
    }
    offenders = {name: value for name, value in all_schedules.items() if name != EMPLOYEE3.name and value}
    if offenders:
        raise AssertionError(f'全仓库仍有非三号定时: {offenders}')
    print('SELF-CHECK-2 PASS：全仓库扫描无遗漏定时。', flush=True)

    employee3_text = EMPLOYEE3.read_text(encoding='utf-8')
    required = [
        'python -u tools/update_kline_cache_daily.py',
        'id: cache_state',
        'actions/cache/save@v4',
        gh_expression('steps.cache_state.outputs.effective_target_date'),
        'python -u employee3_runner.py',
        'python -u employee3_report_guard.py',
        'TELEGRAM_BOT_TOKEN',
        'TELEGRAM_CHAT_ID',
        'DATA_GATE_TARGET_DATE',
        'restore shared kline cache',
    ]
    missing = [marker for marker in required if marker not in employee3_text]
    if missing:
        raise AssertionError(f'三号生产链路缺失: {missing}')
    if 'python -u stock_alert.py' in employee3_text:
        raise AssertionError('三号工作流错误调用了一号员工入口')
    print('SELF-CHECK-3 PASS：缓存、日期门控、引擎、报告和Telegram链路完整。', flush=True)


if __name__ == '__main__':
    main()
