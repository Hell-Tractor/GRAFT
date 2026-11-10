"""Bounded, evidence-driven repair tasks. No edits to model code or paid calls."""
import json
import hashlib
import re
from pathlib import Path
from model_cost_control import byte_limit


def type_spelling(ty):
    kind=ty['kind']
    if kind=='primitive':return ty['name']
    if kind=='var':return ty['var']['name']
    if kind=='nominal':
        return ty['path']+('<'+','.join(type_spelling(t) for t in ty['args'])+'>' if ty['args'] else '')
    if kind=='ref':return '&'+('mut ' if ty['mutable'] else '')+type_spelling(ty['inner'])
    if kind=='slice':return '['+type_spelling(ty['inner'])+']'
    if kind=='array':return '['+type_spelling(ty['inner'])+';'+str(ty['len'])+']'
    if kind=='tuple':return '('+','.join(type_spelling(t) for t in ty['items'])+(',' if len(ty['items'])==1 else '')+')'
    return 'unsupported'


def documentation_card(api,documentation,priority):
    """Human-readable signatures; nominal identity remains in the frozen plan."""
    documentation=documentation or ''
    return {'id':api['id'],'path':api['path'],
        'owner':type_spelling(api['owner']) if api.get('owner') else None,
        'inputs':[type_spelling(t) for t in api['inputs']],'output':type_spelling(api['output']),
        'unsupported':api.get('unsupported',[]),
        'documentation':byte_limit(documentation,180),
        'documentation_truncated_for_budget':len(documentation.encode())>180,'_repair_priority':priority}


def classification(plan,raw=None):
    call=next(s for s in reversed(plan['steps']) if s['kind']=='call' and s['api']==plan['target'])
    signature=plan.get('target_signature')
    if raw is not None:
        # rustdoc.rs hashes serde_json's canonical map serialization, not file bytes.
        canonical=json.dumps(raw,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
        if hashlib.sha256(canonical).hexdigest()!=plan['rustdoc_sha256']:
            raise ValueError('Changed canonical rustdoc signature source')
        item=raw['index'][plan['target']]['inner']['function']
        inputs=item['sig']['inputs']
        signature={'argument_count':len(inputs),'receiver_argument_index':next((i for i,v in enumerate(inputs) if v[0]=='self'),None),
            'kind':'method' if any(v[0]=='self' for v in inputs) else ('associated' if call['path'].startswith('<') else 'free'),
            'evidence':plan['rustdoc_sha256']}
    if signature and signature['argument_count']!=len(call['args']):raise ValueError('Signature/plan arity mismatch')
    return {'target_api':plan['target'],'target_path':call['path'],
        'declared_argument_count_including_receiver':len(call['args']),
        'receiver_argument_index':signature.get('receiver_argument_index') if signature else None,
        'signature':signature,'evidence':'frozen_rustdoc_signature' if signature else 'frozen_call_arity_receiver_unknown',
        'plan_has_byte_inputs':any(s['kind']=='input' for s in plan['steps']),
        'influence_applicability':'not_applicable_no_arguments' if not call['args'] else 'required'}


def task(plan,source,diagnosis):
    code=diagnosis.get('code','unknown');message=diagnosis.get('message','')
    calls=[s for s in plan['steps'] if s['kind']=='call']
    change='Fix the reported failure while keeping the active planned calls and their producer bindings.'
    if 'E0502' in message:
        change='Compute receiver.len() and the final index in separate locals BEFORE taking &mut receiver in the target call.'
    elif 'shadow' in message:
        change='Do not rebind the planned producer. Call grow(&mut original, size) directly, then compute a separate index local.'
    elif code=='runtime_panic' and re.search(r'data\s*\[',source):
        change='Empty input [] fails before the target. Replace direct byte indexing with data.get(index).copied().unwrap_or(0); do not return early.'
    elif code in ('producer_branch_rejected','target_not_reached') and plan['crate_name']=='time':
        change='Fix Date::from_ordinal_date arguments before the failing Ok extraction: safe byte b; year=2000+i32::from(b%100), ordinal=1+u16::from(b). Map actual clock/offset roles to valid ranges.'
    elif code=='input_influence_unobserved':
        change='The actual target object is constant. Populate the SAME producer using bounded input-derived content/state before the target; copying bytes only into trace is not repair.'
    elif 'no byte-input use' in message:
        change='This target has arguments/receiver even if the plan has no Input step. Preserve constructors; prepare their actual state from bytes before the target. An unused data.len() or trace copy is insufficient.'
    elif 'deleted, duplicated, or reordered' in message:
        change='Use exactly the CURRENT active call list below. Do not restore an old constructor or bypass a producer after replanning.'
    branch=re.search(r'GRAFT_BRANCH:(\d+):(Ok|Some):rejected',message)
    producer=None
    if branch:
        step=plan['steps'][int(branch[1])]
        producer={'step':int(branch[1]),'variant':branch[2],'path':step.get('path'),'instance':step.get('instance')}
    failed=next((s for s in diagnosis.get('samples',[]) if not s.get('target_reached') or s.get('exit_code')!=0),{})
    args=failed.get('args')
    try:input_hex=bytes(int(v) for v in args).hex() if args is not None else None
    except (TypeError,ValueError):input_hex=None
    returned=re.findall(r'GRAFT_CALL:(\d+):',failed.get('stderr',message))
    offending=next((line.strip() for line in source.splitlines() if
        ('E0502' in message and '&mut ' in line and '.len()' in line) or
        (code=='runtime_panic' and re.search(r'data\s*\[',line))),None)
    # Keep this task inside the existing diagnostic budget.
    return {'failure_kind':code,'failing_input':failed.get('args'),'failed_producer':producer,
        'failing_input_hex':input_hex,'last_returned_instance':int(returned[-1]) if returned else None,
        'offending_expression':byte_limit(offending,240) if offending else 'unknown',
        'required_local_change':byte_limit(change,600),
        'active_calls':[s['path'] for s in calls],
        'source_location':'unknown' if 'panicked at ' not in message else byte_limit(message.split('panicked at ',1)[1].splitlines()[0],160)}


def helper_names(plan):
    return {
        ('fixedbitset','0.5.7'):{'grow','len'},
        ('bytes','1.11.1'):{'extend_from_slice','len'},
        ('csv','1.3.1'):{'push_field','set_byte','set_line','set_record','as_slice'},
        ('bumpalo','3.14.0'):{'alloc_slice_copy','allocated_bytes','allocated_bytes_including_metadata'},
        ('time','0.3.9'):{'from_ordinal_date','from_hms'},
    }.get((plan['crate_name'],plan['crate_version']),set())
