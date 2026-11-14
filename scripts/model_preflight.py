#!/usr/bin/env python3
"""One-shot GRAFT and deepSURF model smoke test; never retries empty responses."""
import argparse
import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

from experiment_batch import read_key

MODEL = 'deepseek-v4-pro'
ENDPOINT = 'https://api.deepseek.com/chat/completions'
MAX_TOKENS = 65536


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--graft-prompt', type=Path)
    parser.add_argument('--graft-plan', type=Path)
    parser.add_argument('--batch-root', type=Path)
    parser.add_argument('--planner', type=Path, default=Path('/tmp/graft-week1-target/debug/graft-plan'))
    parser.add_argument('--crate', default='ryu')
    parser.add_argument('--deepsurf-request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    key = read_key(Path.home()/'.config/go-exit/deepseek.env')

    def send(name, body):
        body.update(model=MODEL, temperature=0.0, stream=False, max_tokens=MAX_TOKENS,
                    thinking={'type': 'enabled'}, reasoning_effort='high')
        request_path = args.output/f'{name}-request.json'
        request_path.write_text(json.dumps(body, indent=2)+'\n')
        request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+key})
        start = time.monotonic()
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            raw = json.load(response)
        latency = time.monotonic()-start
        (args.output/f'{name}-response.json').write_text(json.dumps(raw, indent=2)+'\n')
        choice = (raw.get('choices') or [{}])[0]
        message = choice.get('message') or {}
        content = message.get('content') or ''
        source = args.output/f'{name}.rs'
        source.write_text(content)
        result = {'name': name, 'latency_seconds': latency, 'model': raw.get('model'),
            'finish_reason': choice.get('finish_reason'), 'content_length': len(content),
            'reasoning_content_length': len(message.get('reasoning_content') or ''),
            'usage': raw.get('usage', {}), 'source_sha256': hashlib.sha256(content.encode()).hexdigest(),
            'contains_fn': 'fn ' in content, 'request': str(request_path), 'source': str(source)}
        if not content.strip() or choice.get('finish_reason') != 'stop' or not result['contains_fn']:
            raise RuntimeError(name+' did not return complete Rust source: '+json.dumps(result))
        return result

    if args.graft_prompt:
        graft_prompt = args.graft_prompt.read_text()
    else:
        if not args.graft_plan or not args.batch_root:
            parser.error('--graft-prompt or both --graft-plan and --batch-root are required')
        universe = json.loads((args.batch_root/'runtime/universes/api_universes'/f'{args.crate}.json').read_text())
        raw_path = next((args.batch_root/'runtime/raw-rustdoc').glob(args.crate+'-*.json'))
        raw = json.loads(raw_path.read_text())
        docs = [{'id': row['api_id'], 'path': row.get('path', row['api_id']),
                 'documentation': raw.get('index', {}).get(row['rustdoc_id'], {}).get('docs', '')}
                for row in universe['apis'] if row.get('public_name_body_definition')]
        context = json.dumps(docs, ensure_ascii=False, sort_keys=True)[:32000]
        prompt_path = args.output/'graft-core-prompt.txt'
        subprocess.run([str(args.planner), 'prompt', str(args.graft_plan), str(prompt_path)],
                       check=True, capture_output=True)
        version = universe['version']
        graft_prompt = (f'Use only the pinned {args.crate} {version} default-feature library. '
                        f'Public documentation pool:\n{context}\n'+prompt_path.read_text())
    deepsurf_body = json.loads(args.deepsurf_request.read_text())
    results = [
        send('graft', {'messages': [{'role': 'user', 'content': graft_prompt}]}),
        send('deepsurf', deepsurf_body),
    ]
    report = {'status': 'passed', 'scope': 'one request each; not formal experiment data',
              'model': MODEL, 'max_tokens': MAX_TOKENS, 'results': results}
    (args.output/'preflight.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
