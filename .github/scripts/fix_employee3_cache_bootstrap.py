# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import re
import yaml

ROOT = Path('.github/workflows')
WORKFLOW = ROOT / 'employee3_runner.yml'
GUARD = Path('employee3_report_guard.py')
SELF_WORKFLOW = ROOT / '_one_time_employee3_cache_bootstrap_fix.yml'
SELF_SCRIPT = Path('.github/scripts/fix_employee3_cache_bootstrap.py')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected 1 occurrence, found {count}')
    return text.replace(old, new, 1)


def indent(line: str) -> int:
    return len(line) - len(line.lstrip(' '))


def is_top_on(line: str) -> bool:
    return indent(line) == 0 and line.strip() in {'on:', '"on":', "'on':"}


def find_schedules(text: str) -> list[str]:
    lines = text.splitlines()
    inside_on = False
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if is_top_on(line):
            inside_on = True
            i += 1
            continue
        if inside_on and indent(line) == 0 and stripped and not stripped.startswith('#'):
            inside_on = False
        if inside_on and indent(line) == 2 and stripped == 'schedule:':
            i += 1
            while i < len(lines):
                row = lines[i]
                row_text = row.strip()
                if row_text and indent(row) <= 2:
                    break
                m = re.search(r"cron:\s*['\"]?([^'\"]+)['\"]?", row_text)
                if m:
                    out.append(m.group(1).strip())
                i += 1
            continue
        i += 1
    return out


def patch_workflow(text: str) -> str:
    old_refresh = '''      - name: refresh shared kline cache for employee3
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
    new_refresh = '''      - name: bootstrap shared cache when restore is empty
        id: bootstrap_cache
        continue-on-error: true
        env:
          PYTHONUNBUFFERED: "1"
          FULL_REBUILD: "1"
          MIN_CACHE_FILES_REQUIRED: "3000"
          MAX_RUNTIME_MINUTES: "210"
          SOFT_STOP_BUFFER_MINUTES: "15"
          RUN_ROUND: "1"
          MAX_AUTO_ROUNDS: "1"
          AUTO_CONTINUE: "0"
          EASTMONEY_TIMEOUT: "20"
          AKSHARE_TIMEOUT: "35"
          BAOSTOCK_TIMEOUT: "45"
          SOURCE_FAIL_THRESHOLD: "5"
          SOURCE_COOLDOWN_SECONDS: "900"
        shell: bash
        run: |
          mkdir -p outputs kline_cache
          COUNT_BEFORE=$(find kline_cache -type f -name "*.csv" ! -name "_*.csv" | wc -l)
          echo "cache_count_before=$COUNT_BEFORE" >> "$GITHUB_OUTPUT"
          if [ "$COUNT_BEFORE" -lt 3000 ]; then
            echo "共享缓存不足3000只，启动三号员工冷启动建库。"
            python -u tools/export_neckline_candidates.py
          else
            echo "共享缓存已有 $COUNT_BEFORE 只，跳过冷启动建库。"
          fi
          COUNT_AFTER=$(find kline_cache -type f -name "*.csv" ! -name "_*.csv" | wc -l)
          echo "cache_count_after=$COUNT_AFTER" >> "$GITHUB_OUTPUT"
          if [ "$COUNT_AFTER" -lt 3000 ]; then
            echo "冷启动后缓存仍不足3000只，本轮不允许正式选股；已保留本轮缓存供下次续建。"
            exit 1
          fi

      - name: refresh shared kline cache for employee3
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
    text = replace_once(text, old_refresh, new_refresh, 'refresh block')

    text = replace_once(
        text,
        '''      - name: validate refreshed cache and resolve effective trade date
        id: cache_state
        shell: bash
''',
        '''      - name: validate refreshed cache and resolve effective trade date
        id: cache_state
        if: always()
        continue-on-error: true
        shell: bash
''',
        'cache gate header',
    )

    old_gate = '''          state_file = Path("outputs/daily_kline_update_state.json")
          if not state_file.exists():
              raise SystemExit("missing cache refresh state")
          state = json.loads(state_file.read_text(encoding="utf-8"))
          effective = str(state.get("目标交易日") or "").strip()
          requested = str(os.environ.get("REQUESTED_TARGET_DATE") or "").strip()
          event_name = str(os.environ.get("EVENT_NAME") or "").strip()
          coverage = float(state.get("fresh_coverage") or 0.0)
          allowed = bool(state.get("should_run_stock_alert"))
          if not effective or not allowed:
              raise SystemExit(f"cache gate failed: effective={effective} coverage={coverage:.6f}")
          resolved = requested if event_name == "workflow_dispatch" and requested else effective
          print(f"effective_target_date={resolved}")
          print(f"cache_latest_trade_date={effective}")
          print(f"fresh_coverage={coverage:.6f}")
'''
    new_gate = '''          state_file = Path("outputs/daily_kline_update_state.json")
          requested = str(os.environ.get("REQUESTED_TARGET_DATE") or "").strip()
          event_name = str(os.environ.get("EVENT_NAME") or "").strip()
          cache_files = len([p for p in Path("kline_cache").rglob("*.csv") if not p.name.startswith("_")])
          if not state_file.exists():
              print(f"effective_target_date={requested}")
              print("cache_latest_trade_date=")
              print("fresh_coverage=0.000000")
              print(f"cache_files={cache_files}")
              print("cache_ready=false")
              raise SystemExit("missing cache refresh state")
          state = json.loads(state_file.read_text(encoding="utf-8"))
          effective = str(state.get("目标交易日") or "").strip()
          coverage = float(state.get("fresh_coverage") or 0.0)
          allowed = bool(state.get("should_run_stock_alert"))
          resolved = requested if event_name == "workflow_dispatch" and requested else effective
          ready = bool(effective and allowed and cache_files >= 3000)
          print(f"effective_target_date={resolved}")
          print(f"cache_latest_trade_date={effective}")
          print(f"fresh_coverage={coverage:.6f}")
          print(f"cache_files={cache_files}")
          print(f"cache_ready={str(ready).lower()}")
          if not ready:
              raise SystemExit(f"cache gate failed: effective={effective} coverage={coverage:.6f} cache_files={cache_files}")
'''
    text = replace_once(text, old_gate, new_gate, 'cache gate body')

    text = replace_once(
        text,
        '''      - name: save refreshed shared kline cache
        uses: actions/cache/save@v4
''',
        '''      - name: save refreshed shared kline cache
        if: always()
        continue-on-error: true
        uses: actions/cache/save@v4
''',
        'cache save header',
    )

    text = replace_once(
        text,
        '''      - name: run employee3 native engine
        env:
''',
        '''      - name: run employee3 native engine
        id: employee3_engine
        if: steps.cache_state.outcome == 'success'
        continue-on-error: true
        env:
''',
        'engine header',
    )

    text = replace_once(
        text,
        '''      - name: guard report and send employee3 telegram
        if: always()
        env:
''',
        '''      - name: guard report and send employee3 telegram
        id: report_guard
        if: always()
        continue-on-error: true
        env:
''',
        'guard header',
    )

    old_guard_env = '''          EMPLOYEE3_TARGET_DATE: ${{ steps.cache_state.outputs.effective_target_date }}
          SELECTION_TRADE_DATE: ${{ steps.cache_state.outputs.effective_target_date }}
          TARGET_TRADE_DATE: ${{ steps.cache_state.outputs.effective_target_date }}
          DATA_GATE_TARGET_DATE: ${{ steps.cache_state.outputs.effective_target_date }}
          ENABLE_TELEGRAM: ${{ steps.config.outputs.send_telegram }}
          EMPLOYEE3_SEND_TELEGRAM: ${{ steps.config.outputs.send_telegram }}
'''
    new_guard_env = '''          EMPLOYEE3_TARGET_DATE: ${{ steps.cache_state.outputs.effective_target_date || steps.config.outputs.target_date }}
          SELECTION_TRADE_DATE: ${{ steps.cache_state.outputs.effective_target_date || steps.config.outputs.target_date }}
          TARGET_TRADE_DATE: ${{ steps.cache_state.outputs.effective_target_date || steps.config.outputs.target_date }}
          DATA_GATE_TARGET_DATE: ${{ steps.cache_state.outputs.effective_target_date || steps.config.outputs.target_date }}
          ENABLE_TELEGRAM: ${{ steps.config.outputs.send_telegram }}
          EMPLOYEE3_SEND_TELEGRAM: ${{ steps.config.outputs.send_telegram }}
          EMPLOYEE3_BOOTSTRAP_OUTCOME: ${{ steps.bootstrap_cache.outcome }}
          EMPLOYEE3_REFRESH_OUTCOME: ${{ steps.refresh_cache.outcome }}
          EMPLOYEE3_CACHE_GATE_OUTCOME: ${{ steps.cache_state.outcome }}
          EMPLOYEE3_ENGINE_OUTCOME: ${{ steps.employee3_engine.outcome }}
          EMPLOYEE3_CACHE_READY: ${{ steps.cache_state.outputs.cache_ready }}
          EMPLOYEE3_CACHE_FILES: ${{ steps.cache_state.outputs.cache_files }}
          EMPLOYEE3_FRESH_COVERAGE: ${{ steps.cache_state.outputs.fresh_coverage }}
'''
    text = replace_once(text, old_guard_env, new_guard_env, 'guard env')

    final_gate = '''
      - name: enforce employee3 pipeline result
        if: always()
        shell: bash
        env:
          CACHE_READY: ${{ steps.cache_state.outputs.cache_ready }}
          CACHE_GATE_OUTCOME: ${{ steps.cache_state.outcome }}
          ENGINE_OUTCOME: ${{ steps.employee3_engine.outcome }}
          GUARD_OUTCOME: ${{ steps.report_guard.outcome }}
        run: |
          echo "cache_ready=$CACHE_READY cache_gate=$CACHE_GATE_OUTCOME engine=$ENGINE_OUTCOME guard=$GUARD_OUTCOME"
          if [ "$CACHE_READY" != "true" ] || [ "$CACHE_GATE_OUTCOME" != "success" ]; then
            echo "三号员工缓存门控失败。"
            exit 1
          fi
          if [ "$ENGINE_OUTCOME" != "success" ]; then
            echo "三号员工主引擎未成功产出报告。"
            exit 1
          fi
          if [ "$GUARD_OUTCOME" != "success" ]; then
            echo "三号员工报告守门失败。"
            exit 1
          fi
'''
    marker = '''      - name: upload cache visibility snapshot
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: employee3-cache-visibility-snapshot
          path: |
            kline_cache/
            employee5_kline_cache/
            data/kline_cache/
            cache/kline_cache/
          if-no-files-found: ignore
          retention-days: 3
'''
    text = replace_once(text, marker, marker + final_gate, 'final gate insertion')
    return text


def patch_guard(text: str) -> str:
    text = replace_once(
        text,
        '''GUARD_JSON = REPORT_DIR / "employee3_report_guard.json"
''',
        '''GUARD_JSON = REPORT_DIR / "employee3_report_guard.json"
CACHE_STATE_JSON = ROOT / "outputs" / "daily_kline_update_state.json"
SELF_CHECK_JSON = REPORT_DIR / "employee3_self_check.json"
''',
        'guard constants',
    )

    old_load = '''def load_payload() -> Dict[str, Any]:
    if not OUTPUT_JSON.exists():
        return {"rows": [], "stat": {}, "target_dash": "", "load_error": f"missing {OUTPUT_JSON}"}
    try:
        return json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"rows": [], "stat": {}, "target_dash": "", "load_error": f"json_load_error: {exc}"}
'''
    new_load = '''def load_json_file(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def pipeline_diagnostic(reason: str) -> Dict[str, Any]:
    cache_state = load_json_file(CACHE_STATE_JSON)
    self_check = load_json_file(SELF_CHECK_JSON)
    cache_files = int(sf(os.getenv("EMPLOYEE3_CACHE_FILES") or cache_state.get("缓存股票数"), 0))
    coverage = sf(os.getenv("EMPLOYEE3_FRESH_COVERAGE") or cache_state.get("fresh_coverage"), 0.0)
    details = [
        reason,
        f"bootstrap={ss(os.getenv('EMPLOYEE3_BOOTSTRAP_OUTCOME')) or 'unknown'}",
        f"refresh={ss(os.getenv('EMPLOYEE3_REFRESH_OUTCOME')) or 'unknown'}",
        f"cache_gate={ss(os.getenv('EMPLOYEE3_CACHE_GATE_OUTCOME')) or 'unknown'}",
        f"cache_ready={ss(os.getenv('EMPLOYEE3_CACHE_READY')) or 'false'}",
        f"engine={ss(os.getenv('EMPLOYEE3_ENGINE_OUTCOME')) or 'unknown'}",
        f"cache_files={cache_files}",
        f"fresh_coverage={coverage:.4f}",
    ]
    if self_check:
        details.append(f"self_check={ss(self_check.get('status')) or 'unknown'}")
        hard_errors = self_check.get("hard_errors") if isinstance(self_check.get("hard_errors"), list) else []
        if hard_errors:
            first = hard_errors[0] if isinstance(hard_errors[0], dict) else {}
            details.append(f"self_check_error={ss(first.get('detail'))[:180]}")
    return {
        "rows": [],
        "stat": {
            "cache_files": cache_files,
            "cache_hit": int(round(cache_files * coverage)) if cache_files else 0,
            "bad": int(sf(cache_state.get("failed_失败数"), 0)),
            "short": int(sf(cache_state.get("no_cache_无缓存数"), 0)),
            "fresh_coverage": coverage,
        },
        "target_dash": os.getenv("EMPLOYEE3_TARGET_DATE") or os.getenv("TARGET_TRADE_DATE") or "",
        "load_error": "；".join(x for x in details if x),
    }


def load_payload() -> Dict[str, Any]:
    if not OUTPUT_JSON.exists():
        return pipeline_diagnostic(f"missing {OUTPUT_JSON}")
    try:
        return json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    except Exception as exc:
        return pipeline_diagnostic(f"json_load_error: {exc}")
'''
    text = replace_once(text, old_load, new_load, 'load payload')

    text = replace_once(
        text,
        '''    elif raw_total == 0:
        lines += ["", "结论：今日没有识别到最近20日高质量突破核心线的深度命中。"]
    if load_error:
        lines.append(f"读取错误:{load_error}")
''',
        '''    elif raw_total == 0 and not load_error:
        lines += ["", "结论：今日没有识别到最近20日高质量突破核心线的深度命中。"]
    if load_error:
        lines += [
            "",
            "结论：这不是‘今日无票’，而是三号员工上游缓存或主引擎未成功产出正式JSON。",
            f"流水线诊断:{load_error}",
        ]
''',
        'misleading empty conclusion',
    )
    return text


def main() -> None:
    workflow_text = WORKFLOW.read_text(encoding='utf-8')
    guard_text = GUARD.read_text(encoding='utf-8')
    WORKFLOW.write_text(patch_workflow(workflow_text), encoding='utf-8')
    GUARD.write_text(patch_guard(guard_text), encoding='utf-8')

    updated_workflow = WORKFLOW.read_text(encoding='utf-8')
    updated_guard = GUARD.read_text(encoding='utf-8')

    # 自检一：工作流 YAML 可解析，且全仓库只有三号员工保留日程。
    parsed = yaml.load(updated_workflow, Loader=yaml.BaseLoader)
    assert isinstance(parsed, dict) and isinstance(parsed.get('jobs'), dict)
    schedule_map = {
        path.name: find_schedules(path.read_text(encoding='utf-8'))
        for path in ROOT.glob('*.y*ml')
        if path != SELF_WORKFLOW
    }
    offenders = {name: value for name, value in schedule_map.items() if name != WORKFLOW.name and value}
    assert offenders == {}, offenders
    assert schedule_map.get(WORKFLOW.name) == ['10 13 * * 1-5']
    print('SELF-CHECK-1 PASS：YAML正常且只有三号员工保留定时。', flush=True)

    # 自检二：冷启动、增量、缓存门控、主引擎和最终失败闸门完整。
    required_workflow = [
        'bootstrap shared cache when restore is empty',
        'FULL_REBUILD: "1"',
        'python -u tools/export_neckline_candidates.py',
        'id: refresh_cache',
        'id: cache_state',
        'cache_ready=',
        'id: employee3_engine',
        "if: steps.cache_state.outcome == 'success'",
        'id: report_guard',
        'enforce employee3 pipeline result',
        'python -u employee3_runner.py',
        'python -u employee3_report_guard.py',
    ]
    missing_workflow = [x for x in required_workflow if x not in updated_workflow]
    assert missing_workflow == [], missing_workflow
    print('SELF-CHECK-2 PASS：三号员工缓存冷启动与生产链路完整。', flush=True)

    # 自检三：守门报告能读取上游诊断，且不再把JSON缺失写成今日无票。
    required_guard = [
        'CACHE_STATE_JSON',
        'pipeline_diagnostic',
        'EMPLOYEE3_ENGINE_OUTCOME',
        "这不是‘今日无票’",
        'elif raw_total == 0 and not load_error',
    ]
    missing_guard = [x for x in required_guard if x not in updated_guard]
    assert missing_guard == [], missing_guard
    compile(updated_guard, str(GUARD), 'exec')
    print('SELF-CHECK-3 PASS：守门诊断准确且Python语法正常。', flush=True)


if __name__ == '__main__':
    main()
