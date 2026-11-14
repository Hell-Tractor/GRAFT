#!/usr/bin/env python3
"""Compile-only Cargo bridge for deepSURF's original generated source.

Uses the baseline's custom compiler and the exact pinned dependency rlibs.
The minimal AFL checking shim retains afl::fuzz<F>, so native called-URAPI
feedback is preserved. Actual fuzzing uses the common real AFL compiler.
"""
import hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

def main():
    project=Path.cwd()
    source=list((project/'src').glob('*.rs'))
    if len(source)!=1:raise RuntimeError('expected one native harness source')
    package=re.search(r'(?m)^name\s*=\s*"([^"]+)"',(project/'Cargo.toml').read_text()).group(1).replace('-','_')
    config=json.loads(Path(os.environ['SURF_CHECK_CONFIG']).read_text())
    digest=hashlib.sha256(str(project).encode()).hexdigest()[:16]
    output=Path(config['output'])/digest;output.mkdir(parents=True,exist_ok=True)
    command=[config['rustc'],'--edition=2021','--crate-name',package,str(source[0]),'--emit=metadata','-o',str(output/'checked.rmeta'),'-L','dependency='+config['dependencies'],'--cfg','fuzzing','-Awarnings']
    for name,path in config['externs'].items():command+=['--extern',name+'='+path]
    env={k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ['API_KEY','TOKEN','SECRET','AUTHORIZATION'])}
    start=time.monotonic();r=subprocess.run(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    sys.stdout.buffer.write(r.stdout);sys.stderr.buffer.write(r.stderr)
    record={'source':str(source[0]),'source_sha256':hashlib.sha256(source[0].read_bytes()).hexdigest(),'command':command,'seconds':time.monotonic()-start,'returncode':r.returncode,'record_urapis':bool(env.get('SURF_RECORD_URAPIS'))}
    (output/('check-'+str(time.time_ns())+'.json')).write_text(json.dumps(record,indent=2))
    sys.exit(r.returncode)
if __name__=='__main__':main()
