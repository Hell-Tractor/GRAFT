"""LLM parameter repair prompts. Calls are verified/instrumented by Rust, not LLM."""
import json
from pathlib import Path

from admission_diagnostics import repair_feedback, fingerprint
from repair_context import task


def enabled(profile):
    return bool(profile and profile.get('assembly_mode') == 'llm-parameter-repair')


def extended(profile):
    return bool(profile and profile.get('repair_contract')=='planned-call-v3')


def read_rules(path):
    value=json.loads(Path(path).read_text())
    if value.get('schema')!='graft-parameter-guidance-v1' or value.get('kind')!='llm_guidance_not_universal_precondition_proof':
        raise ValueError('unsupported parameter guidance')
    ids=set()
    for rule in value['rules']:
        if rule['id'] in ids or not rule.get('crate') or not rule.get('version') or not rule.get('evidence'):
            raise ValueError('duplicate or unversioned parameter rule')
        ids.add(rule['id'])
    if not 0<value.get('max_context_utf8_bytes',0)<=2200:
        raise ValueError('parameter guidance must remain within the existing context budget')
    return value


def rule_context(plan,rules):
    if not rules:return []
    selected=[]
    for rule in rules['rules']:
        if (rule['crate'],rule['version'])!=(plan['crate_name'],plan['crate_version']):continue
        instances=[s['instance'] for s in plan['steps'] if s['kind']=='call' and
                   any(s['path'].endswith(suffix) for suffix in rule['suffixes']) and
                   (not rule.get('owners') or any(s['path'].startswith(owner+'::') for owner in rule['owners']))]
        if not instances:continue
        row={k:rule[k] for k in ('id','roles','advice','evidence')};row['instances']=instances
        if len(json.dumps(selected+[row],ensure_ascii=False).encode())<=rules['max_context_utf8_bytes']:
            selected.append(row)
    return selected


def base_prompt(plan_path, original_prompt, rules=None):
    result = json.loads(Path(plan_path).read_text())
    plan = result['plan']
    hints = ''
    if rules:
        hints='\nVERSIONED PARAMETER GUIDANCE (advice, not proof):\n'+json.dumps(rule_context(plan,rules),ensure_ascii=False,separators=(',',':'))+'\n'
    elif (plan['crate_name'],plan['crate_version']) == ('fixedbitset','0.5.7'):
        hints = ('\nRECEIVER STATE HINT (fixedbitset 0.5.7 public API): '
                 'FixedBitSet::new() has length zero. Before set/toggle/insert, preserve the new() '
                 'call and add receiver.grow(bits) where bits is a bounded positive input-derived '
                 'size, e.g. 1 + (decoded % 4096). Then derive the index modulo receiver.len(). '
                 'Adding grow/len helper calls is explicitly allowed; replacing/deleting the '
                 'original planned new or target call is not. Returning early on all probes is not a repair. '
                 'Use FixedBitSet::grow and FixedBitSet::len documentation.\n')
    # Retain the existing complete public-doc pool for the shared cost compactor.
    documentation = original_prompt.split('Render the following pinned SequencePlan', 1)[0]
    return documentation + '\n' + (
        'Repair the Rust parameter preparation for the pinned plan below. Return only '
        'extern crate alloc and pub fn graft_body(data: &[u8]) -> Vec<(usize, Vec<u8>)>. '
        'No main, other functions/items/imports, attributes, macros, unsafe, raw pointers, '
        'explicit loops, filesystem, environment, process, network, trace output or catch_unwind. '
        'Keep every original fully-qualified planned callee, type argument, producer dependency '
        'and call order. Each planned call must remain a top-level let [mut] result [: Type] = '
        'ORIGINAL_CALLEE(arguments); do not wrap that initializer in a block, if, closure or ?. '
        'Local variables may be renamed consistently when adding helpers; do not bypass the '
        'original object-producing calls by substituting unrelated receivers. '
        'You MAY change input decoding, scalar argument expressions, borrows, local type '
        'annotations, bounded helper computations and safe object initialization. '
        'Fix real API preconditions, not just crashes: capacity is not length; fill a buffer '
        'before split/index operations; bound allocation arguments to at most 65536 and '
        'bound total prepared data; derive valid date/month/day/time fields from the bytes. '
        'Use the actual pinned dependency identities, e.g. chrono::Duration for chrono APIs '
        'when the reference has a conflicting time dependency type. Local type inference is allowed. '
        'Do not replace all input-dependent values by constants. Guards may reject some inputs, '
        'but empty, byte 0 and byte 1 must return normally and the original target must actually '
        'execute for valid inputs. Direct scalar target arguments must vary across these inputs. '
        'A source that only returns early will fail. Tooling will insert its own markers AFTER '
        'the real planned calls; do not generate observation/trace code. '
        'Additional helpers must be safe calls on the pinned library or basic Vec/primitive '
        'operations. No version changes, target deletion, call replacement or new paid target IDs.\n'
        'TARGET ID: ' + plan['target'] + '\nPINNED CRATE: ' + plan['crate_name'] +
        ' ' + plan['crate_version'] + '\n' + hints +
        '\nACTIVE PLAN SHA256: '+fingerprint(plan_path)['sha256']+
        '\nACTIVE PLANNED CALLS (preserve this current list, not an earlier constructor):\n'+
        json.dumps([s['path'] for s in plan['steps'] if s['kind']=='call'])+
        '\nOnly truly zero-argument targets have input influence marked not applicable; targets with receivers require real object preparation. '
        'Empty input must work: use data.get(i).copied().unwrap_or(0), never unguarded data[i]. '
        'Compute indices before taking mutable borrows. Call grow directly on the original binding; do not shadow it.\n')


def prompt(base, source, diagnosis, plan=None):
    feedback = repair_feedback({'diagnosis': diagnosis})
    if plan is not None:
        p=json.loads(Path(plan).read_text())['plan']
        instruction=task(p,source,diagnosis)
        # Append the concise required change first so the budget compactor retains it.
        feedback='error: repair task: '+instruction['required_local_change']+'\n'+feedback
    return base + '\nREPAIR SOURCE:\n' + source + '\nDIAGNOSTICS:\n' + feedback
