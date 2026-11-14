#!/usr/bin/env python3
"""Isolated baseline preflight. Never start or modify historical containers.

Existing images, network disabled, readonly root/tool/source mounts, fresh output only.
"""
import argparse, hashlib, json, pathlib, subprocess, time

def call(args, timeout=60):
    start=time.monotonic()
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
    return {"command":args,"returncode":p.returncode,"elapsed_seconds":time.monotonic()-start,"output":p.stdout.decode(errors="replace")}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--output",type=pathlib.Path,required=True);ap.add_argument("--rulf-generator",type=pathlib.Path);ap.add_argument("--fixture",type=pathlib.Path);args=ap.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    source=out/"smoke.rs"
    source.write_text(args.fixture.read_text() if args.fixture else "pub fn scalar(x:u8)->u8{x.wrapping_add(1)}\npub struct Value(u8);\npub fn make(x:u8)->Value{Value(x)}\npub fn consume(v:Value)->u8{v.0}\n")
    records={"scope":"availability and tiny generation smoke only; not a matched benchmark", "source_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),"historical_containers":[]}
    for container in ["rust-rpg","rust-rulf"]:
        r=call(["docker","inspect","--format","{{.Name}} image={{.Image}} status={{.State.Status}} mounts={{json .Mounts}}",container]);records["historical_containers"].append(r)
    common=["docker","run","--rm","--user","1000:1000","--network","none","--read-only","--cap-drop","ALL","--security-opt","no-new-privileges","--tmpfs","/tmp:rw,exec,size=256m","--mount",f"type=bind,src={source},dst=/input/smoke.rs,readonly"]
    for tool,image in [("rpg","rust-rpg:latest"),("rulf","fuzz-target-generator:v0.1")]:
        dest=out/tool
        if dest.exists() and any(dest.iterdir()):
            raise SystemExit(f"refusing to reuse nonempty smoke output {dest}")
        dest.mkdir(exist_ok=True)
        records[tool+"_image"]=call(["docker","image","inspect","--format","{{.Id}}",image])
        if tool=="rpg":
            command=common+["--mount",f"type=bind,src={dest},dst=/work","--workdir","/work","-e","Strategy=bfs","-e","strategy=bfs","-e","GenericMode=true","-e","GenChoose=heuristic","--entrypoint","/bin/sh",image,"-c","/Rust-Lib-Testing/rust1.65/bin/rustc --version && /Rust-Lib-Testing/rust1.65/bin/fuzz-target-generator /input/smoke.rs --crate-name graft_baseline_smoke --edition=2021 --sysroot /Rust-Lib-Testing/rust1.65 -o /work/doc"]
        else:
            root="/home/jjf/Fuzzing-Target-Generator"
            extra=[];generator=f"{root}/build/x86_64-unknown-linux-gnu/stage2/bin/fuzz-target-generator"
            if args.rulf_generator:
                generator='/opt/graft-patched/fuzz-target-generator'
                extra=['--mount',f'type=bind,src={args.rulf_generator.resolve()},dst={generator},readonly','-e',f'LD_LIBRARY_PATH={root}/build/x86_64-unknown-linux-gnu/stage1/lib/rustlib/x86_64-unknown-linux-gnu/lib:{root}/build/x86_64-unknown-linux-gnu/stage1/lib']
                records['rulf_patched_binary_sha256']=hashlib.sha256(args.rulf_generator.read_bytes()).hexdigest()
            command=common+extra+["--mount","type=bind,src=/home/cym/workspace/RULF,dst="+root+",readonly","--mount",f"type=bind,src={dest},dst=/home/jjf/rulf_work","--workdir","/tmp","--entrypoint","/bin/sh",image,"-c",f"{root}/build/x86_64-unknown-linux-gnu/stage1/bin/rustc --version && {generator} /input/smoke.rs --crate-name graft_baseline_smoke --edition=2021 --sysroot {root}/build/x86_64-unknown-linux-gnu/stage1 -o /tmp/doc"]
        r=call(command);(out/(tool+".log")).write_text(r["output"]);records[tool]=r
        records[tool]["generated_test_files"]=[str(p.relative_to(out)) for p in sorted(dest.rglob("test*.rs"))]
        if records[tool]["generated_test_files"]:
            rustc="/Rust-Lib-Testing/rust1.65/bin/rustc" if tool=="rpg" else root+"/build/x86_64-unknown-linux-gnu/stage1/bin/rustc"
            replay_dir="/work/fuzz_target/graft_baseline_smoke_bfs_generic_fuzz/replay_files" if tool=="rpg" else "/home/jjf/rulf_work/graft_baseline_smoke-work/replay_files"
            replay_command=command[:-1]+[f"set -e; {rustc} --crate-type lib --crate-name graft_baseline_smoke --edition=2021 /input/smoke.rs -o /tmp/libgraft_baseline_smoke.rlib; printf '\\001' > /tmp/input; for replay in {replay_dir}/*.rs; do {rustc} --edition=2021 \"$replay\" --extern graft_baseline_smoke=/tmp/libgraft_baseline_smoke.rlib -o /tmp/replay; /tmp/replay /tmp/input; done"]
            replay=call(replay_command);records[tool]["compile_and_replay"]=replay;(out/(tool+"-replay.log")).write_text(replay["output"])
    (out/"preflight.json").write_text(json.dumps(records,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps({k:{"generation_returncode":records[k]["returncode"],"test_files":len(records[k]["generated_test_files"]),"compile_and_replay_returncode":records[k].get('compile_and_replay',{}).get('returncode')} for k in ["rpg","rulf"]}))
if __name__=="__main__":main()
