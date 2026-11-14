#!/usr/bin/env python3
"""Four compiler witnesses on a single vendored project/lock; no generation or fuzzing."""
import argparse,concurrent.futures,json,os,subprocess,time
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--project',type=Path,required=True);p.add_argument('--suffix',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);root=a.root.resolve();project=a.project.resolve()
    common=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:rw,exec,size=1g','--mount',f'type=bind,src={root},dst={root},readonly','--workdir',str(project),'-e','CARGO_HOME=/tmp/cargo']
    specs={
      'rpg':('rust-rpg:latest','/Rust-Lib-Testing/rust1.65/bin/cargo','/Rust-Lib-Testing/rust1.65/bin/rustc'),
      'rulf':('fuzz-target-generator:v0.1','/home/cym/workspace/RULF/build/x86_64-unknown-linux-gnu/stage2-tools-bin/cargo','/home/cym/workspace/RULF/build/x86_64-unknown-linux-gnu/stage1/bin/rustc'),
      'deepsurf':('deepsurf:ready','/root/.rustup/toolchains/nightly-2024-06-13-x86_64-unknown-linux-gnu/bin/cargo','/root/deepSURF/code/rust/build/host/stage2/bin/rustc')}
    def run(tool):
        target=root/(tool+'-target-'+a.suffix);target.mkdir(exist_ok=True);env=os.environ.copy()
        if tool=='modern':
            command=['cargo','build','--frozen','--offline','--jobs','12','--target-dir',str(target),'--manifest-path',str(project/'Cargo.toml')];env['CARGO_HOME']=str(root/'cargo-home')
        else:
            image,cargo,rustc=specs[tool];extra=[]
            if tool=='deepsurf':target.chmod(0o777)
            else:extra+=['--user','1000:1000']
            if tool=='rulf':extra+=['--mount','type=bind,src=/home/cym/workspace/RULF,dst=/home/cym/workspace/RULF,readonly']
            command=common+extra+['--mount',f'type=bind,src={target},dst=/build','-e','RUSTC='+rustc,'--entrypoint',cargo,image,'build','--frozen','--offline','--jobs','12','--target-dir','/build']
        start=time.monotonic()
        with (a.output/(tool+'-build.log')).open('w') as log:r=subprocess.run(command,cwd=project,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=300)
        return tool,{'command':command,'returncode':r.returncode,'seconds':time.monotonic()-start,'log':tool+'-build.log'}
    report={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for tool,data in executor.map(run,['modern','rpg','rulf','deepsurf']):
            report[tool]=data;print(tool,data['returncode'],round(data['seconds'],2),flush=True)
    (a.output/'compiler-witnesses.json').write_text(json.dumps(report,indent=2)+'\n')
    if any(r['returncode'] for r in report.values()):raise SystemExit('shared compilation failed')
if __name__=='__main__':main()
