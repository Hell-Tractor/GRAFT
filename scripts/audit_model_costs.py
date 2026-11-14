#!/usr/bin/env python3
"""Reconcile saved usage without making model calls; deduplicate provider IDs."""
import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
import statistics

from model_cost_control import read_policy, usage_cost, read_ledger, spent


def audit(root,policy):
    records={};without_usage=0
    for file in sorted(Path(root).rglob('*response*.json')):
        if '/vendor/' in str(file):continue
        try:
            outer=json.loads(file.read_text());raw=outer.get('response',outer)
            if raw.get('model','').startswith('deepseek-v4-pro') and raw.get('usage'):
                identity=raw.get('id') or str(file)
                if identity in records:continue
                usage=raw['usage'];choices=raw.get('choices',[])
                cost=usage_cost(usage,policy)
                if cost is None:without_usage+=1;continue
                parts=file.relative_to(root).parts
                methods=['GRAFT','LLM-only','random-API+LLM','syntactic+LLM','no-cross-argument-unification','random-target']
                method=next((m for m in methods if m in parts),'deepSURF' if 'llm-calls' in parts else 'preflight')
                reasoning=usage.get('completion_tokens_details',{}).get('reasoning_tokens')
                records[identity]={'response_id':raw.get('id'),'file':str(file),'method':method,
                    'usage':usage,'peak_estimate_cny':str(cost),'reasoning_tokens':reasoning,
                    'visible_code_chars':sum(len(c.get('message',{}).get('content') or '') for c in choices)}
            elif outer.get('http_status') or outer.get('transport_error'):without_usage+=1
        except (ValueError,OSError,AttributeError):continue
    groups=defaultdict(list)
    for r in records.values():groups[r['method']].append(r)
    summary=[]
    for method,rows in sorted(groups.items()):
        costs=[Decimal(r['peak_estimate_cny']) for r in rows]
        known=[r for r in rows if r['reasoning_tokens'] is not None]
        output=sum(r['usage']['completion_tokens'] for r in known)
        summary.append({'method':method,'paid_responses':len(rows),'total_peak_estimate_cny':str(sum(costs)),
            'mean_peak_estimate_cny_per_response':str(sum(costs)/len(costs)),
            'mean_prompt_tokens':statistics.mean(r['usage']['prompt_tokens'] for r in rows),
            'mean_completion_tokens':statistics.mean(r['usage']['completion_tokens'] for r in rows),
            'known_reasoning_fraction_of_output':sum(r['reasoning_tokens'] for r in known)/output if output else None,
            'reasoning_metadata_responses':len(known),
            'visible_code_chars_mean':statistics.mean(r['visible_code_chars'] for r in rows)})
    budgets=[]
    ledgers=[(p,p.parts[-3],p.parts[-2],p.stem) for p in Path(root).glob('model-cost-ledgers/round-*/*/*.json')]
    ledgers += [(p,p.parts[-4],'deepSURF',p.parts[-3]) for p in Path(root).glob('deepsurf-native/round-*/*/llm-calls/crate-model-cost.json')]
    for path,round_name,method,crate in ledgers:
        ledger=read_ledger(path);cost=spent(ledger)
        tasks=[]
        for task_file in (Path(root)/'preparation'/round_name/method/crate).glob('*/task.json'):
            try:tasks.append(json.loads(task_file.read_text()))
            except (OSError,ValueError):pass
        admitted=sum(t.get('status')=='ready' for t in tasks)
        targets=len({e['target'] for e in ledger['entries']})
        budgets.append({'round':round_name,'method':method,'crate':crate,'ledger':str(path),
            'spent_or_reserved_cny':str(cost),'paid_or_reserved_calls':len(ledger['entries']),
            'targets_with_paid_reservations':targets,'admitted_targets':admitted,
            'usage_unknown_calls':sum(e['status']!='usage_reported' for e in ledger['entries']),
            'cny_per_attempted_paid_target':str(cost/targets) if targets else None,
            'cny_per_admitted_target_including_failures':str(cost/admitted) if admitted else None})
    return {'schema':'graft-saved-usage-cost-audit-v1','root':str(root),'rate_snapshot':policy['rate_snapshot'],
        'scope':'All saved response records in this root, including retry/preflight archives; provider IDs deduplicated. Unknown cache hit counters charged as misses. Peak estimates are not provider invoices.',
        'responses_without_priceable_usage':without_usage,'summary':summary,'responses':list(records.values()),
        'budget_ledgers':budgets}


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--policy',type=Path,default=Path(__file__).resolve().parents[1]/'experiments/cost-control/policy.json')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=audit(a.root.resolve(),read_policy(a.policy));a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['summary'],indent=2))


if __name__=='__main__':main()
