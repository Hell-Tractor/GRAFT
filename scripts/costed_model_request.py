#!/usr/bin/env python3
"""Shared budgeted official-service client for Python arms and native deepSURF."""
import argparse
import json
import os
from pathlib import Path
import urllib.request

from experiment_batch import read_key
from model_cost_control import (BudgetRejected, compact_native, read_policy,
                                reserve, settle)


def send(messages,policy,target_ledger,crate_ledger,request_path,response_path,
         timeout=180,credential=None,on_send=None,batch_ledger=None,batch_budget_cny=None,target_identity=None):
    body,ticket=reserve(messages,policy,target_ledger,crate_ledger,batch_ledger,batch_budget_cny,target_identity)
    Path(request_path).write_text(json.dumps(body,ensure_ascii=False,indent=2)+'\n')
    raw=None
    try:
        key=credential or read_key(Path.home()/'.config/go-exit/deepseek.env')
        request=urllib.request.Request(policy['endpoint'],data=json.dumps(body).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
        if on_send:on_send()
        with urllib.request.urlopen(request,timeout=timeout) as response:raw=json.load(response)
        Path(response_path).write_text(json.dumps(raw,ensure_ascii=False,indent=2)+'\n')
    except BaseException as error:
        # A timeout may already have been billed. Its full reservation remains
        # spent until provider billing can be reconciled, including on resume.
        settle(ticket,None,policy,target_ledger,crate_ledger,type(error).__name__,batch_ledger,batch_budget_cny)
        raise
    settle(ticket,raw.get('usage'),policy,target_ledger,crate_ledger,batch_path=batch_ledger,batch_budget_cny=batch_budget_cny)
    if not raw.get('usage'):raise BudgetRejected('GRAFT_BUDGET_USAGE_UNKNOWN: no automatic retry without usage')
    return raw


def main():
    p=argparse.ArgumentParser();p.add_argument('--policy',type=Path,required=True)
    p.add_argument('--request',type=Path,required=True);p.add_argument('--response',type=Path,required=True)
    p.add_argument('--target-ledger',type=Path,required=True);p.add_argument('--crate-ledger',type=Path,required=True)
    p.add_argument('--native',action='store_true');a=p.parse_args()
    policy=read_policy(a.policy);body=json.loads(a.request.read_text())
    messages=compact_native(body['messages'],policy) if a.native else body['messages']
    credential=os.environ.get('LLM_API_KEY') or os.environ.get('DEEPSEEK_API_KEY')
    try:send(messages,policy,a.target_ledger,a.crate_ledger,a.request,a.response,credential=credential)
    except BudgetRejected as error:raise SystemExit(str(error))


if __name__=='__main__':main()
