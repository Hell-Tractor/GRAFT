"""Versioned request budgets; no network or credential access at import time."""
import contextlib
from decimal import Decimal, ROUND_FLOOR
import fcntl
import hashlib
import json
from pathlib import Path
import re
import time


class BudgetRejected(RuntimeError):
    pass


def read_policy(path):
    p=json.loads(Path(path).read_text())
    if p['model']!='deepseek-v4-pro' or p['endpoint']!='https://api.deepseek.com/chat/completions':
        raise ValueError('pricing snapshot is valid only for the pinned official Pro service')
    if p['thinking']!={'type':'disabled'}:raise ValueError('this cost profile requires explicit non-thinking mode')
    if not 0<Decimal(p['target_budget_cny'])<Decimal('0.25'):raise ValueError('target budget must be below CNY 0.25')
    return p


def usage_cost(usage,policy):
    """Unknown cache fields are charged as misses; missing usage is not zero."""
    if 'prompt_tokens' not in usage or 'completion_tokens' not in usage:return None
    total=int(usage['prompt_tokens']);output=int(usage['completion_tokens'])
    hit=int(usage.get('prompt_cache_hit_tokens',usage.get('prompt_tokens_details',{}).get('cached_tokens',0)))
    if not 0<=hit<=total or output<0:raise ValueError('invalid usage counters')
    rates=policy['rate_snapshot']
    return (Decimal(hit)*Decimal(rates['cache_hit_per_million'])+
            Decimal(total-hit)*Decimal(rates['cache_miss_per_million'])+
            Decimal(output)*Decimal(rates['output_per_million']))/Decimal(1000000)


def byte_limit(text,limit):
    return text.encode()[:max(0,limit)].decode('utf-8',errors='ignore')


def compact_diagnostics(text,limit):
    """Keep compiler errors and suggestions, excluding huge JSON source spans."""
    errors=[]
    for line in text.splitlines():
        try:record=json.loads(line)
        except ValueError:
            if re.match(r'\s*(error|help|note|-->|caused by)',line,re.I):errors.append(byte_limit(line,400))
            continue
        if not isinstance(record,dict) or record.get('level')!='error':continue
        code=(record.get('code') or {}).get('code','')
        errors.append(code+': '+record.get('message',''))
        for child in record.get('children',[]):
            if child.get('message'):errors.append(byte_limit(child['message'],300))
            for span in child.get('spans',[]):
                if span.get('suggested_replacement'):errors.append('suggestion: '+byte_limit(span['suggested_replacement'],200))
    return byte_limit('\n'.join(errors) if errors else text,limit)


def select_records(records,terms,limit):
    """Select whole JSON cards. Omitted long documentation is explicitly marked."""
    if not isinstance(records,(list,dict)):return '[]'
    rows=list(records.items()) if isinstance(records,dict) else list(enumerate(records))
    def score(row):
        s=json.dumps(row,ensure_ascii=False)
        return -sum(8 if t in str(row[0]) else 1 for t in terms if t and t in s)
    # Explicit tool-supplied priorities exist only in the new repair variant.
    def priority(row):
        value=row[1].get('_repair_priority',2) if isinstance(row[1],dict) else 2
        return value if type(value) is int and 0<=value<=2 else 2
    rows.sort(key=lambda row:(priority(row),score(row)))
    selected=[]
    for key,value in rows:
        row={'id':key,'value':value}
        encoded=json.dumps(row,ensure_ascii=False,separators=(',',':'))
        if len(encoded.encode())>limit:
            if isinstance(value,dict):
                value={k:v for k,v in value.items() if k not in ('documentation','docs')}
                row={'id':key,'value':value,'documentation_omitted_for_budget':True}
            else:continue
        candidate=json.dumps(selected+[row],ensure_ascii=False,separators=(',',':'))
        if len(candidate.encode())<=limit:selected.append(row)
    return json.dumps(selected,ensure_ascii=False,separators=(',',':'))


def compact_controlled(prompt,policy):
    # Preserve constraints and the immutable core verbatim. The pretty plan is
    # redundant with that core, so it need not be transmitted a second time.
    prompt=re.sub(r'\nPLAN:\n.*?\nIMMUTABLE CORE:\n','\nIMMUTABLE CORE:\n',prompt,flags=re.S)
    marker='Public documentation pool:\n'
    if marker in prompt:
        before,tail=prompt.split(marker,1)
        try:docs,end=json.JSONDecoder().raw_decode(tail)
        except ValueError:raise BudgetRejected('GRAFT_BUDGET_CONTEXT: malformed/truncated documentation pool')
        rest=tail[end:]
        terms=re.findall(r'(?:[A-Za-z_]\w*::)+[A-Za-z_]\w*',rest)
        prompt=before+marker+select_records(docs,terms,policy['documentation_utf8_bytes'])+rest
    # Rustc diagnostics are supplementary; never truncate current source/core.
    if '\nDIAGNOSTICS:\n' in prompt:
        left,right=prompt.rsplit('\nDIAGNOSTICS:\n',1)
        prompt=left+'\nDIAGNOSTICS:\n'+compact_diagnostics(right,policy['diagnostics_utf8_bytes'])
    return prompt


def compact_native(messages,policy,adaptive=True):
    """Keep native instructions and harness; retrieve bounded analysis/docs."""
    result=[]
    for message in messages:
        m=dict(message);text=m['content']
        if m['role']=='user':
            without=re.sub(r'=== (STATIC ANALYSIS|DOCUMENTATION) START ===.*?=== \1 END ===','',text,flags=re.S)
            terms=re.findall(r'(?:[A-Za-z_]\w*::)+[A-Za-z_]\w*',without)
            terms+=re.findall(r'DefId\([^\n]+?\)',without)
            def replace(match):
                section,content=match.group(1),match.group(2)
                limit=policy['analysis_utf8_bytes'] if section=='STATIC ANALYSIS' else policy['documentation_utf8_bytes']
                decoder=json.JSONDecoder();records=[];rest=content.strip()
                while rest:
                    try:obj,end=decoder.raw_decode(rest)
                    except ValueError:break
                    if isinstance(obj,dict):records.extend({'key':k,'value':v} for k,v in obj.items())
                    elif isinstance(obj,list):records.extend(obj)
                    rest=rest[end:].lstrip()
                selected=select_records(records,terms,limit)
                return '=== '+section+' START ===\n'+selected+'\n=== '+section+' END ==='
            text=re.sub(r'=== (STATIC ANALYSIS|DOCUMENTATION) START ===(.*?)=== \1 END ===',replace,text,flags=re.S)
            text=re.sub(r'=== ERROR MESSAGE START ===(.*?)=== ERROR MESSAGE END ===',
                lambda m:'=== ERROR MESSAGE START ===\n'+compact_diagnostics(m[1],policy['diagnostics_utf8_bytes'])+'\n=== ERROR MESSAGE END ===',text,flags=re.S)
        m['content']=text;result.append(m)
    size=sum(len(m['content'].encode()) for m in result)
    if adaptive and size>policy['max_prompt_utf8_bytes']:
        # Native system constraints and source are essential. Reduce only
        # supplementary cards to fit the shared input budget, as whole records.
        reduced=dict(policy)
        for field,section in [('documentation_utf8_bytes','DOCUMENTATION'),('analysis_utf8_bytes','STATIC ANALYSIS')]:
            used=sum(len(match[1].strip().encode()) for m in result for match in
                re.finditer('=== '+section+' START ===(.*?)=== '+section+' END ===',m['content'],re.S))
            reduced[field]=max(0,min(reduced[field],used)-(size-policy['max_prompt_utf8_bytes'])-32)
            result=compact_native(messages,reduced,adaptive=False)
            size=sum(len(m['content'].encode()) for m in result)
            if size<=policy['max_prompt_utf8_bytes']:break
    return result


def prepare_body(messages,policy,remaining):
    if any(m.get('role') not in ('system','user') or not isinstance(m.get('content'),str) for m in messages):
        raise BudgetRejected('GRAFT_BUDGET_CONTEXT: only explicit text prompts are priced')
    size=sum(len(m['content'].encode()) for m in messages)
    if size>policy['max_prompt_utf8_bytes']:
        raise BudgetRejected('GRAFT_BUDGET_CONTEXT: essential prompt exceeds byte limit; no API call made')
    # Byte-level tokenizers cannot emit more text tokens than UTF-8 bytes; an
    # additional allowance covers the two-message envelope. Actual usage is
    # checked against the reservation and retained for billing reconciliation.
    upper=size+policy['message_overhead_token_allowance']
    rates=policy['rate_snapshot'];input_cost=Decimal(upper)*Decimal(rates['cache_miss_per_million'])/1000000
    token_rate=Decimal(rates['output_per_million'])/1000000
    affordable=int(((remaining-input_cost)/token_rate).to_integral_value(rounding=ROUND_FLOOR))
    output=min(policy['max_output_tokens'],affordable)
    if output<policy['min_output_tokens']:
        raise BudgetRejected('GRAFT_BUDGET_EXHAUSTED: not enough remaining budget for a complete response')
    reserved=input_cost+Decimal(output)*token_rate
    body={'model':policy['model'],'messages':messages,'thinking':policy['thinking'],
          'temperature':policy['temperature'],'stream':False,'max_tokens':output}
    return body,{'input_token_upper_bound':upper,'reserved_cny':str(reserved),'prompt_utf8_bytes':size}


@contextlib.contextmanager
def locked_ledgers(paths):
    files=[]
    try:
        for path in sorted(map(Path,paths)):
            path.parent.mkdir(parents=True,exist_ok=True)
            f=path.with_suffix(path.suffix+'.lock').open('a+')
            fcntl.flock(f,fcntl.LOCK_EX);files.append(f)
        yield
    finally:
        for f in reversed(files):fcntl.flock(f,fcntl.LOCK_UN);f.close()


def read_ledger(path):
    return json.loads(Path(path).read_text()) if Path(path).exists() else {'entries':[]}


def spent(ledger):
    return sum((Decimal(e['charged_cny']) for e in ledger['entries']),Decimal(0))


def store(path,value):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.replace(path)


def reserve(messages,policy,target_path,crate_path):
    target_path,crate_path=map(Path,[target_path,crate_path])
    with locked_ledgers([target_path,crate_path]):
        target,crate=read_ledger(target_path),read_ledger(crate_path)
        revision=policy['revision']
        fingerprint=hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()
        if any(d.get('policy_revision',revision)!=revision for d in [target,crate]):raise BudgetRejected('GRAFT_BUDGET_POLICY: ledger revision differs')
        if any(d.get('policy_sha256',fingerprint)!=fingerprint for d in [target,crate]):raise BudgetRejected('GRAFT_BUDGET_POLICY: frozen ledger policy changed')
        if any(e.get('reservation_exceeded') for d in [target,crate] for e in d['entries']):
            raise BudgetRejected('GRAFT_BUDGET_ACCOUNTING: prior reservation violation requires reconciliation')
        identity=str(target_path.resolve())
        known={e['id']:e for e in crate['entries'] if e['target']==identity}
        if any(e['id'] not in known for e in target['entries']):
            raise BudgetRejected('GRAFT_BUDGET_ACCOUNTING: target and crate ledger disagree')
        # Directory archival must not reset a target's budget on balance retry.
        target['entries']=list(known.values())
        if len(target['entries'])>=policy['max_calls_per_target']:raise BudgetRejected('GRAFT_BUDGET_EXHAUSTED: maximum paid calls reached')
        targets={e['target'] for e in crate['entries']}
        if identity not in targets and len(targets)>=policy['max_llm_targets_per_crate']:
            raise BudgetRejected('GRAFT_BUDGET_EXHAUSTED: maximum LLM targets per crate reached')
        remaining=min(Decimal(policy['target_budget_cny'])-spent(target),Decimal(policy['crate_budget_cny'])-spent(crate))
        body,estimate=prepare_body(messages,policy,remaining)
        ticket=hashlib.sha256((identity+str(time.time_ns())).encode()).hexdigest()
        entry={'id':ticket,'target':identity,'status':'reserved','charged_cny':estimate['reserved_cny'],**estimate}
        for path,ledger in [(target_path,target),(crate_path,crate)]:
            ledger.update(policy_revision=revision,policy_sha256=fingerprint,rate_snapshot=policy['rate_snapshot']);ledger['entries'].append(dict(entry));store(path,ledger)
    return body,ticket


def settle(ticket,usage,policy,target_path,crate_path,error=None):
    cost=usage_cost(usage,policy) if usage else None
    violation=False
    with locked_ledgers([target_path,crate_path]):
        for path in [target_path,crate_path]:
            ledger=read_ledger(path);entry=next(e for e in ledger['entries'] if e['id']==ticket)
            if cost is not None:
                violation=violation or cost>Decimal(entry['reserved_cny'])
                entry.update(charged_cny=str(cost),usage=usage,status='usage_reported',reservation_exceeded=cost>Decimal(entry['reserved_cny']))
            else:entry.update(status='unknown_charge_reserved',error=error)
            ledger['spent_or_reserved_cny']=str(spent(ledger));store(path,ledger)
    if violation:raise BudgetRejected('GRAFT_BUDGET_ACCOUNTING: usage exceeded reservation; stop and reconcile provider billing')
