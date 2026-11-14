"""Read only DeepSURF-selected targets, including its final moved inventory."""
import re
from pathlib import Path


def selected_sources(library):
    base=Path(library)/'deepSURF'
    selected=base/'deepsurf_fuzzing_targets.txt'
    if not selected.exists():return []
    names=set(re.findall(r'(?m)^\s+\* (\S+)\s*$',selected.read_text()))
    if any('/' in n or n in ('.','..') for n in names):raise ValueError('invalid native target name')
    found={}
    for parent in ['fuzzing_corpus','no_llm/compilable','llm/compilable']:
        for name in sorted(names):
            source=base/'fuzz'/parent/name/'src'
            candidates=sorted(source.glob('*.rs'))
            if name in found:continue
            if len(candidates)>1:raise ValueError('ambiguous selected target source: '+name)
            if candidates:found[name]=candidates[0]
    missing=names-set(found)
    if missing:raise ValueError('selected native targets missing source: '+','.join(sorted(missing)[:5]))
    return [found[n] for n in sorted(found)]
