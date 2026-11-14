#!/usr/bin/env python3
"""Host launcher for two independent isolated generator preflights."""
import argparse,concurrent.futures,json,os,subprocess,time
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--project',type=Path,required=True);p.add_argument('--suffix',required=True);p.add_argument('--versions',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=a.root.resolve();project=a.project.resolve();a.output.mkdir(parents=True,exist_ok=True);scripts=Path(__file__).resolve().parent
    specs={'rpg':('rust-rpg:latest','/Rust-Lib-Testing/rust1.65/bin/cargo','/Rust-Lib-Testing/rust1.65/bin/rustc','/Rust-Lib-Testing/rust1.65/bin/fuzz-target-generator','/Rust-Lib-Testing/rust1.65'),'rulf':('fuzz-target-generator:v0.1','/home/cym/workspace/RULF/build/x86_64-unknown-linux-gnu/stage2-tools-bin/cargo','/home/cym/workspace/RULF/build/x86_64-unknown-linux-gnu/stage1/bin/rustc',str(root/'container-rulf/build/fuzz-target-generator'),'/home/cym/workspace/RULF/build/x86_64-unknown-linux-gnu/stage1')}
    def run(tool):
        image,cargo,rustc,generator,sysroot=specs[tool];target=root/(tool+'-target-'+a.suffix);target.mkdir(parents=True,exist_ok=True);results=root/('generator-preflight-'+a.suffix)/tool;results.mkdir(parents=True,exist_ok=True)
        command=['docker','run','--rm','--user','1000:1000','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:rw,exec,size=1g','--mount',f'type=bind,src={root},dst={root},readonly','--mount',f'type=bind,src={target},dst=/build','--mount',f'type=bind,src={results},dst=/results','--mount',f'type=bind,src={a.versions.resolve()},dst=/opt/versions.json,readonly','--mount',f'type=bind,src={scripts/"check_common_generators.py"},dst=/opt/check.py,readonly']
        if tool=='rulf':
            work=root/('rulf-work-'+a.suffix);work.mkdir(exist_ok=True)
            command+=['--mount','type=bind,src=/home/cym/workspace/RULF,dst=/home/cym/workspace/RULF,readonly','--mount',f'type=bind,src={work},dst=/home/jjf/rulf_work','-e',f'LD_LIBRARY_PATH={sysroot}/lib/rustlib/x86_64-unknown-linux-gnu/lib:{sysroot}/lib']
        else:command+=['--mount',f'type=bind,src={scripts/"baseline_output_wrapper.py"},dst=/opt/output-wrapper.py,readonly']
        command+=['--entrypoint','python3',image,'/opt/check.py','--tool',tool,'--versions','/opt/versions.json','--project',str(project),'--target','/build','--output','/results','--cargo',cargo,'--rustc',rustc,'--generator',generator,'--sysroot',sysroot]
        if tool=='rpg':command+=['--rpg-output-wrapper','/opt/output-wrapper.py']
        start=time.monotonic()
        with (a.output/(tool+'-generator.log')).open('w') as log:r=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=900)
        return tool,{'command':command,'returncode':r.returncode,'seconds':time.monotonic()-start,'result_path':str(results/'summary.json')}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:report=dict(executor.map(run,['rpg','rulf']))
    (a.output/'generator-witnesses.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
