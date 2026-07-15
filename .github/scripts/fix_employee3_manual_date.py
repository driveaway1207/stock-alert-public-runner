# -*- coding: utf-8 -*-
from pathlib import Path
import re
import yaml

WORKFLOW_DIR = Path('.github/workflows')
EMPLOYEE3 = WORKFLOW_DIR / 'employee3_runner.yml'
SELF = WORKFLOW_DIR / '_one_time_employee3_manual_date_fix.yml'


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def top_on(line: str) -> bool:
    return indentation(line) == 0 and line.strip() in {'on:', '"on":', "'on':"}


def find_schedules(text: str) -> list[str]:
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


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label} expected once, found {count}')
    return text.replace(old, new, 1)


def main() -> None:
    text = EMPLOYEE3.read_text(encoding='utf-8')

    if 'EVENT_NAME: ${{ github.event_name }}' not in text:
        text = replace_once(
            text,
            '          REQUESTED_TARGET_DATE: ${{ steps.config.outputs.target_date }}\n',
            '          REQUESTED_TARGET_DATE: ${{ steps.config.outputs.target_date }}\n'
            '          EVENT_NAME: ${{ github.event_name }}\n',
            'cache_state env block',
        )

    if '          import os\n          from pathlib import Path\n' not in text:
        text = replace_once(
            text,
            '          import json\n          from pathlib import Path\n',
            '          import json\n          import os\n          from pathlib import Path\n',
            'cache_state imports',
        )

    if '          requested = str(os.environ.get("REQUESTED_TARGET_DATE") or "").strip()\n' not in text:
        text = replace_once(
            text,
            '          effective = str(state.get("目标交易日") or "").strip()\n'
            '          coverage = float(state.get("fresh_coverage") or 0.0)\n',
            '          effective = str(state.get("目标交易日") or "").strip()\n'
            '          requested = str(os.environ.get("REQUESTED_TARGET_DATE") or "").strip()\n'
            '          event_name = str(os.environ.get("EVENT_NAME") or "").strip()\n'
            '          coverage = float(state.get("fresh_coverage") or 0.0)\n',
            'manual date variables',
        )

    if '          resolved = requested if event_name == "workflow_dispatch" and requested else effective\n' not in text:
        text = replace_once(
            text,
            '          print(f"effective_target_date={effective}")\n'
            '          print(f"fresh_coverage={coverage:.6f}")\n',
            '          resolved = requested if event_name == "workflow_dispatch" and requested else effective\n'
            '          print(f"effective_target_date={resolved}")\n'
            '          print(f"cache_latest_trade_date={effective}")\n'
            '          print(f"fresh_coverage={coverage:.6f}")\n',
            'effective target output',
        )

    EMPLOYEE3.write_text(text, encoding='utf-8')

    # 自检一：自动任务使用缓存最新交易日，手动任务保留用户指定日期。
    assert 'EVENT_NAME: ${{ github.event_name }}' in text
    assert 'resolved = requested if event_name == "workflow_dispatch" and requested else effective' in text
    assert 'effective_target_date={resolved}' in text
    print('SELF-CHECK-1 PASS：手动回看日期与每日自动日期已分流。', flush=True)

    # 自检二：工作流 YAML 可解析。
    parsed = yaml.load(text, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    assert isinstance(parsed.get('on'), dict)
    print('SELF-CHECK-2 PASS：三号员工工作流 YAML 解析正常。', flush=True)

    # 自检三：全仓库只有三号员工保留定时，生产链路标记完整。
    schedule_map = {
        path.name: find_schedules(path.read_text(encoding='utf-8'))
        for path in WORKFLOW_DIR.glob('*.y*ml')
        if path != SELF
    }
    offenders = {name: value for name, value in schedule_map.items() if name != EMPLOYEE3.name and value}
    assert offenders == {}, offenders
    assert schedule_map.get(EMPLOYEE3.name) == ['10 13 * * 1-5']
    for marker in (
        'python -u tools/update_kline_cache_daily.py',
        'actions/cache/save@v4',
        'python -u employee3_runner.py',
        'python -u employee3_report_guard.py',
        'TELEGRAM_BOT_TOKEN',
        'DATA_GATE_TARGET_DATE',
    ):
        assert marker in text, marker
    print('SELF-CHECK-3 PASS：唯一日程与三号生产链路完整。', flush=True)


if __name__ == '__main__':
    main()
