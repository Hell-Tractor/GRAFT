#!/usr/bin/env python3
"""Prepare a separate writable Cargo cache with exact, checksum-verified pins.

Existing global cache/index are read-only inputs. Missing exact archives are obtained
from crates.io, checked against the existing index or crates.io metadata, then saved
only under --output. No installation or model execution.
"""
import argparse,hashlib,json,os
from pathlib import Path
import urllib.request

def key(name):
    if len(name)==1:return '1/'+name
    if len(name)==2:return '2/'+name
    if len(name)==3:return '3/'+name[0]+'/'+name
    return name[:2]+'/'+name[2:4]+'/'+name
def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'GRAFT-revision-reproducibility/1'}),timeout=45) as response:return response.read()
def main():
    p=argparse.ArgumentParser();p.add_argument('--versions',type=Path);p.add_argument('--lock',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,default=Path('/home/cym/.cargo'));p.add_argument('--project',type=Path);p.add_argument('--serde-pin');a=p.parse_args()
    if bool(a.versions)==bool(a.lock):raise SystemExit('supply exactly one of --versions or --lock')
    root=a.output.resolve();root.mkdir(parents=True,exist_ok=True)
    registry=root/'registry';registry.mkdir(exist_ok=True)
    source_index=a.cache/'registry/index';index=registry/'index'
    if not index.exists():index.symlink_to(source_index,target_is_directory=True)
    cache=registry/'cache';cache.mkdir(exist_ok=True)
    for original in sorted((a.cache/'registry/cache').glob('*/*.crate')):
        dest=cache/original.parent.name/original.name;dest.parent.mkdir(exist_ok=True)
        if not dest.exists():dest.symlink_to(original)
    if a.lock:
        try:import tomllib
        except ImportError:import tomli as tomllib
        locked=tomllib.loads(a.lock.read_text())
        tracks=[(r['name'],r['version']) for r in locked['package'] if r.get('source','').startswith('registry+')]
    else:tracks=list(json.loads(a.versions.read_text())['versions'].items())
    rows=[]
    for name,version in tracks:
        candidates=list(cache.glob(f'*/{name}-{version}.crate'))
        checksum=None;origin=None
        for package in sorted(source_index.glob('*/.cache/'+key(name))):
            for part in package.read_bytes().split(b'\0'):
                if part.startswith(b'{'):
                    info=json.loads(part)
                    if info['vers']==version:checksum=info['cksum'];origin=str(package);break
            if checksum:break
        if not checksum:
            url=f'https://crates.io/api/v1/crates/{name}/{version}'
            checksum=json.loads(fetch(url))['version']['checksum'];origin=url
        if candidates:archive=candidates[0];method='existing_cache'
        else:
            archive=cache/'index.crates.io-1949cf8c6b5b557f'/f'{name}-{version}.crate';archive.parent.mkdir(exist_ok=True)
            data=fetch(f'https://crates.io/api/v1/crates/{name}/{version}/download')
            if hashlib.sha256(data).hexdigest()!=checksum:raise SystemExit(f'checksum mismatch: {name} {version}')
            archive.write_bytes(data);method='downloaded_exact_pin'
        actual=hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual!=checksum:raise SystemExit(f'cached checksum mismatch: {name} {version}')
        rows.append({'crate':name,'version':version,'archive':str(archive),'sha256':actual,'checksum_source':origin,'method':method})
        (root/'source-preparation.json').write_text(json.dumps({'schema':'graft-common-sources-v1','rows':rows},indent=2)+'\n')
        print(name,version,method,flush=True)
    if not a.lock:
        project=a.project or root.parent/'joint-compatibility';(project/'src').mkdir(parents=True,exist_ok=True)
        manifest='[package]\nname="graft_joint_compatibility"\nversion="0.0.0"\nedition="2021"\nrust-version="1.65"\n\n[dependencies]\n'+''.join(f'{name}="={version}"\n' for name,version in tracks)
        if a.serde_pin:manifest+='serde="='+a.serde_pin+'"\n'
        (project/'Cargo.toml').write_text(manifest);(project/'src/main.rs').write_text('fn main() {}\n')
if __name__=='__main__':main()
