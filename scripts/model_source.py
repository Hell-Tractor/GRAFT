"""Conservative model-code extraction; never rewrite Rust or execute prose."""
import hashlib
import re


def extract(text, validated_plain=False):
    raw=text.strip()
    if not raw:
        raise ValueError('empty model source')
    fences=list(re.finditer(r'(?m)^\s*```([^\n]*)\n',raw))
    if validated_plain or not fences:
        source=raw
        kind='plain_source'
    else:
        # Count every fence; one closed candidate only, including other languages.
        lines=raw.splitlines()
        delimiters=[i for i,line in enumerate(lines) if line.strip().startswith('```')]
        if len(delimiters)!=2:
            raise ValueError('ambiguous or incomplete model code fences')
        start,end=delimiters
        if lines[start].strip() not in ('```rust','```rs','```') or lines[end].strip()!='```' or end<=start+1:
            raise ValueError('expected one complete Rust source block')
        source='\n'.join(lines[start+1:end]).strip()
        kind='unique_rust_block'
    if not source:
        raise ValueError('empty extracted source')
    return source,{'schema':'graft-model-source-extraction-v1','kind':kind,
        'raw_sha256':hashlib.sha256(text.encode()).hexdigest(),
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'plain_syntax_verified':validated_plain,
        'scope':'Representation only; complete Rust/contract/compile/probe validation still required'}
