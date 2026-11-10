"""Authoritative batch-wide reservations, shared across processes and crates.

The batch ledger is written FIRST. After a crash its entries restore the two
subledgers; partial writes can never release an outstanding reservation.
"""
from decimal import Decimal, InvalidOperation
from pathlib import Path
import hashlib,json,time
from model_cost_control import (BudgetRejected, locked_ledgers, read_ledger,
                                spent, store, prepare_body, usage_cost)


def limit_value(value):
    try:limit=Decimal(str(value))
    except (InvalidOperation,ValueError):raise ValueError('invalid batch budget')
    if not limit.is_finite() or limit<=0:raise ValueError('batch budget must be positive and finite')
    return limit


def _identity(policy):
    return hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()


def _batch(path,policy,limit):
    identity=str(Path(path).resolve());digest=_identity(policy)
    if Path(path).exists():
        ledger=read_ledger(path)
        if (ledger.get('schema')!='graft-batch-model-budget-v1' or ledger.get('batch_identity')!=identity
            or Decimal(ledger.get('limit_cny','-1'))!=limit
            or ledger.get('policy_sha256')!=digest):
            raise BudgetRejected('GRAFT_BATCH_POLICY: batch identity, limit or policy changed')
        if any(e.get('batch')!=identity for e in ledger['entries']):
            raise BudgetRejected('GRAFT_BATCH_ACCOUNTING: entry belongs to another batch')
        return ledger
    return {'schema':'graft-batch-model-budget-v1','batch_identity':identity,
            'limit_cny':str(limit),'policy_revision':policy['revision'],'policy_sha256':digest,
            'rate_snapshot':policy['rate_snapshot'],'entries':[]}


def initialize(path,policy,limit):
    path=Path(path);limit=limit_value(limit)
    with locked_ledgers([path]):
        ledger=_batch(path,policy,limit);store(path,ledger)
    return ledger


def _paths(target,crate,batch):
    paths=list(map(lambda p:Path(p).resolve(),[target,crate,batch]))
    if len(set(paths))!=3:raise ValueError('batch, crate and target ledgers must be distinct')
    return paths


def _subledgers(batch,target_path,crate_path,policy,key):
    target,crate=read_ledger(target_path),read_ledger(crate_path)
    identity=str(target_path);crate_identity=str(crate_path)
    if any(e['target']!=identity or e['crate']!=crate_identity for e in batch['entries'] if e['target_key']==key):
        raise BudgetRejected('GRAFT_BATCH_IDENTITY: original target budget cannot move directories')
    target_rows=[e for e in batch['entries'] if e['target']==identity]
    if any(e['target_key']!=key or e['crate']!=crate_identity for e in target_rows):
        raise BudgetRejected('GRAFT_BATCH_IDENTITY: target binding changed')
    crate_rows=[e for e in batch['entries'] if e['crate']==crate_identity]
    for ledger,rows in [(target,target_rows),(crate,crate_rows)]:
        if ledger.get('policy_sha256',_identity(policy))!=_identity(policy):
            raise BudgetRejected('GRAFT_BATCH_POLICY: subledger policy changed')
        known={e['id'] for e in rows}
        if any(e['id'] not in known for e in ledger['entries']):
            raise BudgetRejected('GRAFT_BATCH_ACCOUNTING: subledger entries missing from authority')
        # A lagging or deleted subledger is restored from the committed batch.
        ledger.update(entries=[dict(e) for e in rows],policy_revision=policy['revision'],
                      policy_sha256=_identity(policy),rate_snapshot=policy['rate_snapshot'])
    return target,crate


def _write(batch_path,batch,target_path,target,crate_path,crate):
    for path,ledger in [(batch_path,batch),(crate_path,crate),(target_path,target)]:
        ledger['spent_or_reserved_cny']=str(spent(ledger));store(path,ledger)


def reserve(messages,policy,target_path,crate_path,batch_path,limit,target_identity=None):
    target_path,crate_path,batch_path=_paths(target_path,crate_path,batch_path)
    limit=limit_value(limit);key=target_identity or str(target_path)
    with locked_ledgers([target_path,crate_path,batch_path]):
        batch=_batch(batch_path,policy,limit)
        if any(e.get('reservation_exceeded') or e['status']=='unknown_charge_reserved' for e in batch['entries']):
            raise BudgetRejected('GRAFT_BATCH_ACCOUNTING: unknown usage or reservation violation; stop and reconcile')
        target,crate=_subledgers(batch,target_path,crate_path,policy,key)
        if len(target['entries'])>=policy['max_calls_per_target']:
            raise BudgetRejected('GRAFT_BUDGET_EXHAUSTED: maximum paid calls reached')
        keys={e['target_key'] for e in crate['entries']}
        if key not in keys and len(keys)>=policy['max_llm_targets_per_crate']:
            raise BudgetRejected('GRAFT_BUDGET_EXHAUSTED: maximum LLM targets per crate reached')
        remaining=min(limit-spent(batch),Decimal(policy['target_budget_cny'])-spent(target),
                      Decimal(policy['crate_budget_cny'])-spent(crate))
        try:body,estimate=prepare_body(messages,policy,remaining)
        except BudgetRejected as error:
            if 'GRAFT_BUDGET_EXHAUSTED' in str(error) and remaining==limit-spent(batch):
                raise BudgetRejected('GRAFT_BATCH_EXHAUSTED: insufficient unreserved batch budget') from error
            raise
        ticket=hashlib.sha256((str(batch_path)+key+str(time.time_ns())).encode()).hexdigest()
        entry={'id':ticket,'target':str(target_path),'target_key':key,'crate':str(crate_path),
               'batch':str(batch_path),'status':'reserved','charged_cny':estimate['reserved_cny'],**estimate}
        for ledger in [batch,crate,target]:ledger['entries'].append(dict(entry))
        _write(batch_path,batch,target_path,target,crate_path,crate)
    return body,ticket


def settle(ticket,usage,policy,target_path,crate_path,batch_path,limit,error=None):
    target_path,crate_path,batch_path=_paths(target_path,crate_path,batch_path)
    limit=limit_value(limit);cost=usage_cost(usage,policy) if usage else None;violation=False
    with locked_ledgers([target_path,crate_path,batch_path]):
        batch=_batch(batch_path,policy,limit)
        entry=next((e for e in batch['entries'] if e['id']==ticket),None)
        if entry is None or entry['target']!=str(target_path) or entry['crate']!=str(crate_path):
            raise BudgetRejected('GRAFT_BATCH_ACCOUNTING: settlement ticket binding mismatch')
        _subledgers(batch,target_path,crate_path,policy,entry['target_key'])
        if entry['status']=='usage_reported':
            if cost is None or cost!=Decimal(entry['charged_cny']) or usage!=entry.get('usage'):
                raise BudgetRejected('GRAFT_BATCH_ACCOUNTING: settled usage cannot be overwritten')
        elif cost is not None:
            violation=cost>Decimal(entry['reserved_cny'])
            entry.update(charged_cny=str(cost),usage=usage,status='usage_reported',reservation_exceeded=violation)
        else:entry.update(status='unknown_charge_reserved',error=error)
        target,crate=_subledgers(batch,target_path,crate_path,policy,entry['target_key'])
        _write(batch_path,batch,target_path,target,crate_path,crate)
    if violation:raise BudgetRejected('GRAFT_BATCH_ACCOUNTING: usage exceeded reservation; stop and reconcile')
