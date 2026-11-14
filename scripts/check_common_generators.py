#!/usr/bin/env python3
"""Run INSIDE an isolated baseline container against the shared vendored project.

Generate with a per-crate timeout, compile up to two raw replay targets, do not run
unreviewed real-library harnesses. Zero targets / emitter failures are tool outcomes,
not automatic reasons for further version downgrades.
"""
import argparse,hashlib,json,os,shutil,signal,subprocess,time
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--tool',choices=['rpg','rulf'],required=True);p.add_argument('--versions',type=Path,required=True);p.add_argument('--project',type=Path,required=True);p.add_argument('--target',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--cargo',required=True);p.add_argument('--rustc',required=True);p.add_argument('--generator',required=True);p.add_argument('--sysroot',required=True);p.add_argument('--rpg-output-wrapper');a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);rows=[]
    # Only the dedicated preflight build directory is mounted writable here.
    # Missing doc outputs force Cargo to invoke the selected generator again.
    if (a.target/'doc').exists():shutil.rmtree(a.target/'doc')
    env=os.environ.copy();env.update({'RUSTC':a.rustc,'RUSTDOC':a.generator,'RUSTDOCFLAGS':'--sysroot '+a.sysroot,'CARGO_HOME':'/tmp/cargo','CARGO_TARGET_DIR':str(a.target),'Strategy':'bfs','strategy':'bfs','GenericMode':'true','GenChoose':'heuristic'})
    def run(command,cwd,log,timeout=60):
        start=time.monotonic()
        proc=subprocess.Popen(command,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            data,_=proc.communicate(timeout=timeout);text=data.decode(errors='replace');code=proc.returncode;timed_out=False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGKILL);data,_=proc.communicate();text=data.decode(errors='replace');code=None;timed_out=True
        log.write_text(text)
        return {'command':command,'returncode':code,'timed_out':timed_out,'seconds':time.monotonic()-start,'log':str(log)}
    for name,version in json.loads(a.versions.read_text())['versions'].items():
        dest=a.output/name;dest.mkdir(exist_ok=True)
        if a.rpg_output_wrapper:
            env.update({'RUSTDOC':a.rpg_output_wrapper,'GRAFT_REAL_RUSTDOC':a.generator,'GRAFT_BASELINE_OUTPUT_DIR':str(dest)})
        row={'crate':name,'version':version,'tool':a.tool,'scope':'default-feature generator compatibility and first-two-replay compilation; not a coverage run'}
        command=[a.cargo,'--config',str(a.project/'.cargo/config.toml'),'doc','--manifest-path',str(a.project/'Cargo.toml'),'--frozen','--offline','--jobs','8','--no-deps','--package',name+'@'+version]
        row['generation']=run(command,dest,dest/'generation.log')
        base=(dest/'fuzz_target') if a.tool=='rpg' else Path('/home/jjf/rulf_work')/(name.replace('-','_')+'-work')
        replays=sorted(base.rglob('replay*.rs')) if base.exists() else []
        tests=sorted(base.rglob('test*.rs')) if base.exists() else []
        row['generated_target_count']=len(tests);row['generated_replay_count']=len(replays)
        row['generated_target_sha256']={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in tests}
        libs=list((a.target/'debug/deps').glob('lib'+name.replace('-','_')+'-*.rlib'))
        if len(libs)>1:
            # Two versions can share a crate name (e.g. chrono uses time 0.1).
            # Use Cargo's dep-info source directory, not the filename order.
            libs=[lib for lib in libs if (lib.parent/(lib.stem[3:]+'.d')).is_file() and '/'+name+'-'+version+'/' in (lib.parent/(lib.stem[3:]+'.d')).read_text()]
        row['replay_compilation']=[]
        if len(libs)==1:
            for index,source in enumerate(replays[:2]):
                cmd=[a.rustc,'--edition=2021',str(source),'--extern',name.replace('-','_')+'='+str(libs[0]),'-L','dependency='+str(a.target/'debug/deps'),'-o',str(dest/('replay-'+str(index)))]
                row['replay_compilation'].append(run(cmd,dest,dest/('compile-'+str(index)+'.log')))
        else:row['replay_compile_blocker']='missing/ambiguous cached rlib; do not guess library identity'
        row['replay_execution']='not_run: unsafe contracts / parsing assumptions of generated real-library harnesses not yet reviewed'
        rows.append(row);(a.output/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')
        print(a.tool,name,version,'generation',row['generation']['returncode'],'targets',len(tests),'compiled',[r['returncode'] for r in row['replay_compilation']],flush=True)
if __name__=='__main__':main()
