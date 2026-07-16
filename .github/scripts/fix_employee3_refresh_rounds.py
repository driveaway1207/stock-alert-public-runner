# -*- coding: utf-8 -*-
from pathlib import Path
import yaml

WORKFLOW = Path('.github/workflows/employee3_runner.yml')
SELF = Path('.github/workflows/_one_time_employee3_refresh_rounds_fix.yml')
SCRIPT = Path('.github/scripts/fix_employee3_refresh_rounds.py')

OLD = '''      - name: refresh shared kline cache for employee3
        id: refresh_cache
        if: always()
        continue-on-error: true
        env:
          PYTHONUNBUFFERED: 1
          MAX_RUNTIME_MINUTES: "45"
          SOFT_STOP_BUFFER_MINUTES: "5"
          MIN_FRESH_COVERAGE: "0.965"
          ALLOW_STOCK_ALERT_IF_STALE_ONLY: "1"
        run: |
          mkdir -p outputs kline_cache
          python -u tools/update_kline_cache_daily.py
'''

NEW = '''      - name: refresh shared kline cache for employee3
        id: refresh_cache
        if: always()
        continue-on-error: true
        env:
          PYTHONUNBUFFERED: 1
          MAX_RUNTIME_MINUTES: "45"
          SOFT_STOP_BUFFER_MINUTES: "5"
          MIN_FRESH_COVERAGE: "0.965"
          ALLOW_STOCK_ALERT_IF_STALE_ONLY: "1"
          EMPLOYEE3_MAX_REFRESH_ROUNDS: "3"
        shell: bash
        run: |
          set +e
          mkdir -p outputs kline_cache
          MAX_ROUNDS="${EMPLOYEE3_MAX_REFRESH_ROUNDS:-3}"
          ROUNDS_USED=0
          FINAL_READY=false
          FINAL_COVERAGE=0

          for ROUND in $(seq 1 "$MAX_ROUNDS"); do
            ROUNDS_USED="$ROUND"
            echo "========== 三号员工缓存增量续刷：第 ${ROUND}/${MAX_ROUNDS} 轮 =========="
            python -u tools/update_kline_cache_daily.py
            UPDATE_EXIT=$?
            echo "第 ${ROUND} 轮更新程序退出码: ${UPDATE_EXIT}"

            STATE=$(python - <<'PY'
          import json
          from pathlib import Path
          path = Path("outputs/daily_kline_update_state.json")
          if not path.exists():
              print("false|0.000000|missing_state")
          else:
              try:
                  state = json.loads(path.read_text(encoding="utf-8"))
                  ready = bool(state.get("should_run_stock_alert"))
                  coverage = float(state.get("fresh_coverage") or 0.0)
                  target = str(state.get("目标交易日") or "")
                  print(f"{str(ready).lower()}|{coverage:.6f}|{target}")
              except Exception as exc:
                  print(f"false|0.000000|state_error:{exc}")
          PY
          )
            IFS='|' read -r ROUND_READY ROUND_COVERAGE ROUND_TARGET <<< "$STATE"
            FINAL_READY="$ROUND_READY"
            FINAL_COVERAGE="$ROUND_COVERAGE"
            echo "第 ${ROUND} 轮结果: ready=${ROUND_READY} coverage=${ROUND_COVERAGE} target=${ROUND_TARGET}"

            if [ "$ROUND_READY" = "true" ]; then
              echo "缓存覆盖率已经达到正式选股门槛，停止续刷。"
              break
            fi

            if [ "$ROUND" -lt "$MAX_ROUNDS" ]; then
              echo "缓存覆盖率尚未达标；下一轮只处理剩余旧日期股票。"
              sleep 5
            fi
          done

          echo "rounds_used=${ROUNDS_USED}" >> "$GITHUB_OUTPUT"
          echo "final_ready=${FINAL_READY}" >> "$GITHUB_OUTPUT"
          echo "final_coverage=${FINAL_COVERAGE}" >> "$GITHUB_OUTPUT"
          echo "缓存续刷完成: rounds=${ROUNDS_USED} ready=${FINAL_READY} coverage=${FINAL_COVERAGE}"
'''


def main() -> None:
    text = WORKFLOW.read_text(encoding='utf-8')
    if OLD not in text:
        if 'EMPLOYEE3_MAX_REFRESH_ROUNDS: "3"' in text:
            print('refresh continuation block already installed')
        else:
            raise RuntimeError('original refresh block not found')
    else:
        text = text.replace(OLD, NEW, 1)
        WORKFLOW.write_text(text, encoding='utf-8')

    current = WORKFLOW.read_text(encoding='utf-8')

    # 自检一：工作流 YAML 可解析，三号唯一日程不变。
    parsed = yaml.load(current, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict)
    on_obj = parsed.get('on')
    assert isinstance(on_obj, dict)
    schedule = on_obj.get('schedule')
    assert isinstance(schedule, list) and schedule
    assert schedule[0].get('cron') == '10 13 * * 1-5'
    print('SELF-CHECK-1 PASS：YAML与三号唯一日程正常。')

    # 自检二：续刷最多三轮、达标即退出、不降低96.5%门槛。
    required = [
        'EMPLOYEE3_MAX_REFRESH_ROUNDS: "3"',
        'for ROUND in $(seq 1 "$MAX_ROUNDS")',
        'python -u tools/update_kline_cache_daily.py',
        'if [ "$ROUND_READY" = "true" ]',
        'MIN_FRESH_COVERAGE: "0.965"',
        'rounds_used=${ROUNDS_USED}',
        'final_coverage=${FINAL_COVERAGE}',
    ]
    missing = [item for item in required if item not in current]
    assert not missing, missing
    assert current.count('python -u tools/update_kline_cache_daily.py') == 1
    print('SELF-CHECK-2 PASS：三轮续刷、达标早停与原覆盖率门槛完整。')

    # 自检三：缓存、日期门控、引擎、报告和Telegram生产链路未被破坏。
    production_markers = [
        'actions/cache/restore@v4',
        'actions/cache/save@v4',
        'id: cache_state',
        'python -u employee3_runner.py',
        'python -u employee3_report_guard.py',
        'TELEGRAM_BOT_TOKEN',
        'DATA_GATE_TARGET_DATE',
        'timeout-minutes: 360',
    ]
    missing_production = [item for item in production_markers if item not in current]
    assert not missing_production, missing_production
    print('SELF-CHECK-3 PASS：三号生产链路未改动。')


if __name__ == '__main__':
    main()
