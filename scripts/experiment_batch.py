#!/usr/bin/env python3
"""User entry point for preparation, readiness, calibration, and round execution.

Nothing runs on import. status/check/prepare never call a model. Only calibrate
reads the credential file, into a child environment; it never sources shell code.
"""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid

from coverage_snapshot import file_hash, load, verify
from run_experiment_rounds import check_tasks, checked_file, save

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BATCH = ROOT.parent.parent / 'graft-experiments-5min-6rounds-20261109'


def read_key(path):
    values = []
    for line in Path(path).expanduser().read_text().splitlines():
        match = re.fullmatch(r'\s*(?:export\s+)?DEEPSEEK_API_KEY\s*=\s*(.*?)\s*', line)
        if match:
            parts = shlex.split(match.group(1), comments=True)
            if len(parts) != 1 or not parts[0]:
                raise ValueError('DEEPSEEK_API_KEY 必须是非空的字面值')
            values.append(parts[0])
    if len(values) != 1:
        raise ValueError('凭据文件必须且只能包含一个 DEEPSEEK_API_KEY 定义')
    return values[0]


def model_config(source, model, endpoint):
    """Make a task-local config, without copying any credential or shell content."""
    lines, in_llm = [], False
    found = False
    for line in source.splitlines():
        if line == 'LLM:':
            in_llm, found = True, True
            lines += ['LLM:', '  type: "DeepSeek"', '  model_name: ' + json.dumps(model),
                      '  api_url_chat: ' + json.dumps(endpoint), '  max_tokens: 65536']
            continue
        if line and not line[0].isspace() and not line.startswith('#'):
            in_llm = False
        if in_llm and re.match(r'\s+(?:type|model_name|api_url_chat|max_tokens|api_key|enable_thinking|thinking_budget):', line):
            continue
        lines.append(line)
    if not found:
        raise ValueError('配置文件缺少 LLM 段')
    return '\n'.join(lines) + '\n'


def readiness(protocol_path, batch):
    protocol = load(protocol_path)
    protocol['_file_sha256'] = file_hash(protocol_path)
    errors = []
    try:
        for name in ('common_versions', 'shared_lock', 'shared_manifest'):
            checked_file(protocol[name])
        for crate, record in protocol['universes'].items():
            universe = load(checked_file(record))
            verify(universe)
            if universe['crate'] != crate or universe['version'] != protocol['versions'][crate]:
                raise ValueError('分母版本不一致')
    except (KeyError, OSError, ValueError) as error:
        errors.append(str(error))
    counts = Counter()
    for number in range(1, protocol['rounds'] + 1):
        path = batch/f'round-{number:02d}'/'tasks.json'
        try:
            manifest = load(path)
            counts.update(task['status'] for task in manifest['tasks'])
            check_tasks(protocol, manifest, number)
        except (KeyError, OSError, ValueError) as error:
            errors.append(f'第 {number} 轮：{error}')
    if protocol['max_workers'] > len(os.sched_getaffinity(0)):
        errors.append('当前可用 CPU 数小于固定调度参数')
    report = {'ready': not errors, 'per_target_seconds': protocol['seconds_per_target'],
        'rounds': protocol['rounds'], 'configurations': len(protocol['methods']),
        'planned_target_runs': len(protocol['methods'])*len(protocol['versions'])*protocol['target_slots']*protocol['rounds'],
        'task_status_counts': dict(counts), 'errors': errors}
    return protocol, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['status', 'check', 'budget', 'prepare', 'calibrate', 'run'])
    p.add_argument('--protocol', type=Path, default=ROOT/'experiments/week5/protocol.json')
    p.add_argument('--batch', type=Path, default=DEFAULT_BATCH)
    p.add_argument('--paper', type=Path, default=ROOT.parent/'GRAFTPaper')
    p.add_argument('--port', type=Path, default=ROOT.parent.parent/'graft-baseline-port-20261109')
    p.add_argument('--credential-file', type=Path, default=Path.home()/'.config/go-exit/deepseek.env')
    p.add_argument('--jobs', type=int, default=8, help='编译预检并发；正式 AFL 固定 56 workers')
    p.add_argument('--plan', type=Path, help='calibrate 使用的已冻结计划；默认用现有 base64 诊断计划')
    a = p.parse_args()
    a.batch = a.batch.resolve()
    protocol, state = readiness(a.protocol, a.batch)
    if a.action in ['status', 'check']:
        print(f'配置：{state["configurations"]} 个；每目标 {state["per_target_seconds"]} 秒；{state["rounds"]} 轮；计划 {state["planned_target_runs"]} 次目标运行。')
        print('准备状态：' + json.dumps(state['task_status_counts'], ensure_ascii=False))
        for error in state['errors']:
            print(error)
        print('可启动完整批次：' + ('是' if state['ready'] else '否'))
        a.batch.mkdir(parents=True, exist_ok=True)
        save(a.batch/'readiness.json', state)
        return 0 if a.action == 'status' or state['ready'] else 3
    if a.action == 'budget':
        a.batch.mkdir(parents=True, exist_ok=True)
        code = subprocess.call([sys.executable, str(ROOT/'scripts/estimate_experiment_time.py'),
            '--output', str(a.batch/'time-budget.json'), '--slots', '56', '64', '--seconds',
            str(protocol['seconds_per_target']), '--repeats', str(protocol['rounds']), '--round-barriers'])
        if code == 0:
            rows = load(a.batch/'time-budget.json')['equal_workers_sensitivity']
            selected = next(r for r in rows if r['scenario'] == 'all_variants_formal_equal_1_workers' and r['slots'] == 56)
            print(f'本批统一单 worker、逐轮等待统计的纯测试排程：{selected["selected_feasible_fuzz_hours"]} 小时；生成、编译、回放和 PDF 更新另计。')
        return code
    planner = a.batch/'tools/graft-plan'
    graft = a.batch/'tools/graft'
    if a.action == 'prepare':
        if not planner.is_file():
            raise ValueError('缺少批次工具副本；请按 README 重建并复制 graft-plan')
        directory = a.batch/('preparation-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
        return subprocess.call([sys.executable, str(ROOT/'scripts/prepare_round_preflight.py'),
            '--protocol', str(a.protocol.resolve()), '--port', str(a.port.resolve()),
            '--output', str(directory), '--planner', str(planner), '--jobs', str(a.jobs)])
    if a.action == 'calibrate':
        if not graft.is_file():
            raise ValueError('缺少批次工具副本；请按 README 重建并复制 graft')
        # Credential loading is isolated to this action. run and its fuzz/replay
        # children do not receive this variable from this driver.
        key = read_key(a.credential_file)
        shared = a.port.resolve()/'joint-compatibility-v2'
        if file_hash(shared/'Cargo.lock') != protocol['shared_lock']['sha256'] or file_hash(shared/'Cargo.toml') != protocol['shared_manifest']['sha256']:
            raise ValueError('运行工程与冻结的共同 manifest/lock 不一致')
        plan = (a.plan or a.batch/'executor-smoke/plan.json').resolve()
        if not plan.is_file():
            raise ValueError('缺少校准计划，请用 --plan 指定已冻结计划')
        directory = a.batch/('calibration-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
        directory.mkdir(parents=True)
        config = model_config((ROOT/'config.yaml').read_text(), protocol['model'], protocol['model_endpoint'])
        (directory/'config.yaml').write_text(config)
        env = dict(os.environ, GRAFT_API_KEY=key, CARGO_HOME=str(a.port.resolve()/'cargo-home'), RUST_LOG='info')
        command = [str(graft), 'checked-testcase', str(plan), str(directory/'target'),
                   '--shared-project', str(shared), '--max-repairs', str(protocol['max_repairs_after_initial'])]
        start = time.monotonic()
        with (directory/'generation.log').open('wb') as log:
            proc = subprocess.Popen(command, cwd=directory, env=env, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            try:
                proc.wait(timeout=300)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        save(directory/'calibration.json', {'returncode': proc.returncode,
            'seconds': time.monotonic()-start, 'model': protocol['model'],
            'scope': 'single target calibration, not a formal round', 'command': command})
        print(f'校准退出码 {proc.returncode}；记录：{directory}')
        return 0 if proc.returncode == 0 else 1
    if a.action == 'run':
        if not state['ready']:
            print('完整批次尚未就绪，未启动 AFL。请先运行 status 查看缺项。', file=sys.stderr)
            return 3
        # exec preserves signals and the runner's exit status for terminal,
        # tmux, and systemd callers; completed tasks are reused on restart.
        os.execv(sys.executable, [sys.executable, str(ROOT/'scripts/run_experiment_rounds.py'),
            '--protocol', str(a.protocol.resolve()), '--batch', str(a.batch), '--paper', str(a.paper.resolve())])
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        # No environment/credential values or HTTP Authorization are logged.
        print('无法执行：'+str(error), file=sys.stderr)
        sys.exit(2)
