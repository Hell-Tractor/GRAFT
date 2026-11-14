#!/usr/bin/env python3
"""Run inside the isolated deepSURF image; never needs model credentials."""
import json, os, subprocess, sys
from pathlib import Path

def main():
    c,v,work,port,build=sys.argv[1:]
    work,port,build=map(Path,[work,port,build]);build.mkdir(parents=True,exist_ok=True)
    rustc='/root/deepSURF/code/rust/build/host/stage2/bin/rustc'
    cargo='/root/.rustup/toolchains/nightly-2024-06-13-x86_64-unknown-linux-gnu/bin/cargo'
    env=dict(os.environ,RUSTC=rustc,CARGO_HOME='/tmp/cargo',CARGO_TARGET_DIR=str(build),SURF_ANALYZE_LIB='1',SURF_WORKING_PATH=str(work/'library'))
    manifest=port/'joint-compatibility-v2/Cargo.toml'
    config=port/'joint-compatibility-v2/.cargo/config.toml'
    report_dir=work/'library/deepSURF/report'
    stale=list(report_dir.glob(c.replace('-','_')+'_*.urapi.json')) if report_dir.exists() else []
    for path in stale:path.unlink()
    # `cargo rustc` may otherwise reuse a cached crate without rerunning the
    # report-producing native analysis. Remove only this package's artifacts.
    subprocess.run([cargo,'--config',str(config),'clean','--manifest-path',str(manifest),
                    '--package',c+'@'+v],env=env,check=True)
    command=[cargo,'--config',str(config),'rustc','--offline','--locked','--manifest-path',str(manifest),'--package',c+'@'+v,'--lib','-j','8','--','--cfg','fuzzing','--cfg','graft_deep_analysis']
    subprocess.run(command,env=env,check=True)
    reports=list(report_dir.glob(c.replace('-','_')+'_*.urapi.json')) if report_dir.exists() else []
    if len(reports)!=1:
        raise RuntimeError('forced native analysis did not produce exactly one report: '+str(reports))
    # serde is an explicit common harness dependency, including when the crate
    # itself does not depend on it (e.g. base64).
    subprocess.run([cargo,'--config',str(config),'build','--offline','--locked','--manifest-path',str(manifest),'--package','serde@1.0.228','--lib','-j','8'],env=env,check=True)
    deps=build/'debug/deps';externs={}
    for name in [c.replace('-','_'),'serde']:
        candidates=list(deps.glob('lib'+name+'-*.rlib'))
        if len(candidates)!=1:raise RuntimeError('ambiguous pinned rlib '+name+': '+str(candidates))
        externs[name]=str(candidates[0])
    # Include the other already-built common dependencies for native helpers.
    for name in ['autocfg','base64','bumpalo','byteorder','bytes','chrono','csv','fixedbitset','idna','regex','ryu','time','unicode_segmentation']:
        candidates=list(deps.glob('lib'+name+'-*.rlib'))
        if len(candidates)==1:externs[name]=str(candidates[0])
    support=work/'support';support.mkdir(exist_ok=True)
    afl=support/'afl.rs';afl.write_text('#[inline(never)] pub fn fuzz<F: FnMut(&[u8])>(mut f:F){let bytes=[0u8;1024];f(&bytes);}\n#[macro_export] macro_rules! fuzz_nohook {($f:expr)=>{{$crate::fuzz($f)}};}\n#[macro_export] macro_rules! fuzz {($f:expr)=>{{$crate::fuzz($f)}};}\n')
    for name,source in [('afl',afl),('global_data',Path('/root/deepSURF/code/global_data/src/lib.rs'))]:
        out=support/('lib'+name+'.rlib')
        subprocess.run([rustc,'--edition=2021','--crate-name',name,'--crate-type=rlib',str(source),'-o',str(out)],check=True)
        externs[name]=str(out)
    checks=work/'native-checks';checks.mkdir(exist_ok=True)
    (work/'check-config.json').write_text(json.dumps({'rustc':rustc,'dependencies':str(deps),'output':str(checks),'externs':externs},indent=2))
if __name__=='__main__':main()
