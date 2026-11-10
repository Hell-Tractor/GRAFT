"""Bounded, persistent plan lineage. No model calls and no budget-ledger mutation."""
import hashlib
import json
from pathlib import Path
import time

from admission_diagnostics import atomic_json, fingerprint, read_json


def plan_key(plan):
    fields=('target','crate_name','crate_version','features','default_features','steps','infer_call_results')
    return hashlib.sha256(json.dumps({k:plan.get(k) for k in fields},sort_keys=True).encode()).hexdigest()


def choose(original, directory, bounds, generate, preflight):
    """Generate at most two alternate producer plans within one cumulative budget.

    generate(report_path, timeout, max_alternatives) returns the planner JSON.
    preflight(plan_path, work_dir, timeout) returns the common core check result.
    A crash in an in-flight operation requires reconciliation, not a budget reset.
    """
    original,directory=Path(original),Path(directory)
    if (isinstance(bounds.get('max_candidates'),bool) or not isinstance(bounds.get('max_candidates'),int)
        or not 1<=bounds['max_candidates']<=3 or isinstance(bounds.get('wall_seconds'),bool)
        or not isinstance(bounds.get('wall_seconds'),(int,float)) or not 0<bounds['wall_seconds']<=60
        or not isinstance(bounds.get('search_nodes'),int) or isinstance(bounds.get('search_nodes'),bool)
        or not 0<bounds['search_nodes']<=2000):
        raise ValueError('invalid bounded replanning limits')
    initial=json.loads(original.read_text())['plan']
    directory.mkdir(parents=True,exist_ok=True)
    state_path=directory/'plan-lineage.json'
    state=read_json(state_path) or {'schema':'graft-plan-lineage-v1',
        'initial_plan':fingerprint(original),'target':initial['target'],
        'budget_target_directory':str(directory.parent.resolve()),
        'bounds':bounds,'elapsed_seconds':0,'trials':0,'inflight':False,
        'entries':[{'kind':'initial','plan':fingerprint(original),'key':plan_key(initial)}],
        'paid_budget_reset':False}
    if state['initial_plan']!=fingerprint(original) or state['bounds']!=bounds:
        raise ValueError('replanning changed the original plan or frozen bounds')
    if state['budget_target_directory']!=str(directory.parent.resolve()):
        raise ValueError('replanning cannot move to another target budget directory')
    if state.get('inflight'):raise ValueError('interrupted replanning must be reconciled; budget not reset')
    atomic_json(state_path,state)
    if state.get('selected'):
        selected=state['selected']
        if fingerprint(selected['path'])!=selected:raise ValueError('selected alternate plan changed')
        return Path(selected['path'])
    if state.get('status') in ('no_admissible_alternative','wall_budget_exhausted','infrastructure_failed'):
        return None
    if state['trials']>=bounds['max_candidates']-1 or state['elapsed_seconds']>=bounds['wall_seconds']:
        return None
    started=time.monotonic()
    def remaining():
        value=bounds['wall_seconds']-state['elapsed_seconds']-(time.monotonic()-started)
        if value<=0:raise TimeoutError('cumulative replanning wall budget exhausted')
        return value
    state.update(inflight=True,status='searching');atomic_json(state_path,state)
    try:
        report=generate(directory/'alternatives.json',remaining(),bounds['max_candidates']-1-state['trials'])
        remaining()
        if report.get('target')!=initial['target']:raise ValueError('alternate generator changed target')
        if report.get('search_nodes',0)>bounds['search_nodes']:raise ValueError('alternate generator exceeded search-node budget')
        state['generator_report']=report
        candidates=report.get('candidates',[])
        if len(candidates)>bounds['max_candidates']-1-state['trials']:
            raise ValueError('alternate generator exceeded candidate limit')
        for row in candidates:
            state['trials']+=1
            result=row['result']
            if result.get('status')!='planned':continue
            candidate=result['plan']
            for key in ('target','crate_name','crate_version','package_name','features','default_features','rustdoc_sha256'):
                if candidate.get(key)!=initial.get(key):raise ValueError('alternate plan changed pinned identity: '+key)
            key=plan_key(candidate)
            if any(e['key']==key for e in state['entries']):continue
            work=directory/f"candidate-{state['trials']}";work.mkdir(exist_ok=False)
            path=work/'plan.json';atomic_json(path,result)
            entry={'kind':'alternate_producer','plan':fingerprint(path),'key':key,
                   'excluded_producer':row.get('excluded_producer')}
            state['entries'].append(entry);atomic_json(state_path,state)
            entry['preflight']=preflight(path,work,remaining())
            remaining()
            if entry['preflight']['status']=='passed':
                state.update(selected=fingerprint(path),status='selected');break
            if entry['preflight']['status']=='infrastructure_failed':
                state['status']='infrastructure_failed';break
        else:state['status']='no_admissible_alternative'
    except TimeoutError as error:
        state.update(status='wall_budget_exhausted',error=str(error))
    except Exception as error:
        state.update(status='infrastructure_failed',error=str(error))
        raise
    finally:
        state['elapsed_seconds']+=time.monotonic()-started
        state['inflight']=False;atomic_json(state_path,state)
    return Path(state['selected']['path']) if state.get('selected') else None
