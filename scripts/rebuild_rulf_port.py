#!/usr/bin/env python3
"""Rebuild only RULF's generator from a patched source copy and frozen stage1 deps.

The original checkout, compiler, cached dependency artifacts and old targets are read-only.
No Cargo resolution, downloads, LLVM rebuild or changes to historical containers.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--patch',type=Path,required=True)
    a=p.parse_args();source=a.source.resolve();out=a.output.resolve()
    if out.exists() and any(out.iterdir()):raise SystemExit('output must be new/empty')
    out.mkdir(parents=True,exist_ok=True)
    build=out/'build';build.mkdir()
    manifest={'schema':'graft-rulf-port-v1','original_source':str(source),'patch_sha256':digest(a.patch),'phases':[],'dependency_policy':'reuse frozen stage1 artifact deps; no Cargo resolution or original mutations'}
    def save():(out/'port-build.json').write_text(json.dumps(manifest,indent=2)+'\n')
    def run(name,command,env=None,cwd=None):
        start=time.monotonic()
        with (out/(name+'.log')).open('w') as log:
            result=subprocess.run(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT)
        manifest['phases'].append({'name':name,'command':list(map(str,command)),'seconds':time.monotonic()-start,'returncode':result.returncode})
        save()
        if result.returncode:raise SystemExit(f'{name} failed; see {out}/{name}.log')
    for rel in ['src/librustdoc','src/tools/fuzz-target-generator']:
        shutil.copytree(source/rel,out/rel)
    affected=Path('src/librustdoc/fuzz_target/fuzz_target_renderer.rs')
    manifest['original_file_sha256']=digest(source/affected)
    run('apply-patch',['git','apply',str(a.patch.resolve())],cwd=out)
    manifest['patched_file_sha256']=digest(out/affected)
    sysroot=source/'build/x86_64-unknown-linux-gnu/stage1'
    rustc=sysroot/'bin/rustc'
    deps=source/'build/x86_64-unknown-linux-gnu/stage1-tools/x86_64-unknown-linux-gnu/release/deps'
    host_deps=source/'build/x86_64-unknown-linux-gnu/stage1-tools/release/deps'
    fingerprints=deps.parent/'.fingerprint'
    fp=next(fingerprints.glob('rustdoc-*/lib-rustdoc.json'))
    info=json.loads(fp.read_text());extern=[];artifacts={}
    for _,name,_,expected_fingerprint in info['deps']:
        candidates=list(deps.glob('lib'+name+'-*.rlib'))
        expected=expected_fingerprint.to_bytes(8,'little').hex()
        candidates=[c for c in candidates if any(f.is_file() and f.suffix!='.json' and f.read_text().strip()==expected for f in fingerprints.glob('*-'+c.stem.rsplit('-',1)[1]+'/lib-*'))]
        if len(candidates)!=1:raise SystemExit(f'ambiguous dependency {name}: {candidates}')
        extern+=['--extern',name+'='+str(candidates[0])];artifacts[str(candidates[0])]=digest(candidates[0])
    env=os.environ.copy()
    libraries=[sysroot/'lib',sysroot/'lib/rustlib/x86_64-unknown-linux-gnu/lib',host_deps]
    env.update({'LD_LIBRARY_PATH':':'.join(map(str,libraries)),'RUSTC_BOOTSTRAP':'1','DOC_RUST_LANG_ORG_CHANNEL':'nightly','CARGO_MANIFEST_DIR':str(out/'src/librustdoc'),'CARGO_PKG_VERSION':'0.0.0'})
    manifest.update({'rustc_sha256':digest(rustc),'original_generator_sha256':digest(source/'build/x86_64-unknown-linux-gnu/stage2/bin/fuzz-target-generator'),'prebuilt_dependencies':artifacts,'compiler_fingerprint':info['rustc'],'rustflags':info['rustflags']})
    common=[str(rustc),'--edition=2021','--sysroot',str(sysroot),'-L','dependency='+str(deps),'-L','dependency='+str(host_deps),'-C','opt-level=1','-C','codegen-units=8','-C','debug-assertions=off']+info['rustflags']
    lib=build/'librustdoc_graft_port.rlib'
    run('rustdoc-build',common+['--crate-name','rustdoc','--crate-type','rlib',str(out/'src/librustdoc/lib.rs'),'-o',str(lib)]+extern,env=env,cwd=out/'src/librustdoc')
    binary=build/'fuzz-target-generator'
    run('generator-build',common+['--crate-name','fuzz_target_generator',str(out/'src/tools/fuzz-target-generator/src/main.rs'),'--extern','rustdoc='+str(lib),'-C','link-arg=-Wl,-rpath,'+str(sysroot/'lib/rustlib/x86_64-unknown-linux-gnu/lib'),'-o',str(binary)],env=env,cwd=out)
    run('version',[str(binary),'--version'],env=env)
    manifest['patched_generator_sha256']=digest(binary)
    manifest['original_unchanged']=manifest['original_file_sha256']==digest(source/affected) and manifest['original_generator_sha256']==digest(source/'build/x86_64-unknown-linux-gnu/stage2/bin/fuzz-target-generator')
    save();print(json.dumps({'binary':str(binary),'sha256':manifest['patched_generator_sha256'],'original_unchanged':manifest['original_unchanged']}))
if __name__=='__main__':main()
