"""LLM parameter repair prompts. Calls are verified/instrumented by Rust, not LLM."""
import json
from pathlib import Path

from admission_diagnostics import repair_feedback


def enabled(profile):
    return bool(profile and profile.get('assembly_mode') == 'llm-parameter-repair')


def base_prompt(plan_path, original_prompt):
    result = json.loads(Path(plan_path).read_text())
    plan = result['plan']
    hints = ''
    if (plan['crate_name'],plan['crate_version']) == ('fixedbitset','0.5.7'):
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
        ' ' + plan['crate_version'] + '\n' + hints)


def prompt(base, source, diagnosis):
    feedback = repair_feedback({'diagnosis': diagnosis})
    return base + '\nREPAIR SOURCE:\n' + source + '\nDIAGNOSTICS:\n' + feedback
