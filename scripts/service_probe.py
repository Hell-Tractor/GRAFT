#!/usr/bin/env python3
"""Single low-token service probe; prints no credential or response body."""
import json
import time
import urllib.request
from pathlib import Path

from experiment_batch import read_key

key = read_key(Path.home()/'.config/go-exit/deepseek.env')
body = {'model': 'deepseek-v4-pro', 'messages': [{'role': 'user',
        'content': 'Return exactly this Rust expression and nothing else: 1 + 1'}],
        'temperature': 0, 'stream': False, 'max_tokens': 64,
        'thinking': {'type': 'disabled'}}
request = urllib.request.Request('https://api.deepseek.com/chat/completions',
    data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
    'Authorization': 'Bearer '+key})
start = time.monotonic()
with urllib.request.urlopen(request, timeout=60) as response:
    raw = json.load(response)
choice = (raw.get('choices') or [{}])[0]
print(json.dumps({'http_ok': True, 'latency_seconds': time.monotonic()-start,
    'finish_reason': choice.get('finish_reason'), 'model': raw.get('model'),
    'usage': raw.get('usage', {})}, indent=2))
