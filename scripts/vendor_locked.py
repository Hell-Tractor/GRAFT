#!/usr/bin/env python3
"""Materialize a separate vendor tree from Cargo.lock's checksummed archives."""
import argparse, concurrent.futures, hashlib, io, json, tarfile, urllib.request
from pathlib import Path
try:
    import tomllib
except ImportError:
    import tomli as tomllib

def main():
    p=argparse.ArgumentParser();p.add_argument('--lock',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    packages=[x for x in tomllib.loads(a.lock.read_text())['package'] if x.get('source','').startswith('registry+')]
    def one(pkg):
        name,version,checksum=pkg['name'],pkg['version'],pkg['checksum'];folder=name+'-'+version;dest=a.output/folder
        if (dest/'.cargo-checksum.json').exists():return {'name':name,'version':version,'cached':True}
        caches=[Path('/home/cym/.cargo'),Path('/tmp/graft-week1-cargo-home'),Path('/home/cym/workspace/graft-baseline-port-20261109/cargo-home')]
        archive=next((f for cache in caches for f in cache.glob('registry/cache/*/'+folder+'.crate')),None)
        if archive:data=archive.read_bytes()
        else:
            with urllib.request.urlopen('https://static.crates.io/crates/'+name+'/'+folder+'.crate',timeout=60) as response:data=response.read()
        if hashlib.sha256(data).hexdigest()!=checksum:raise ValueError('checksum mismatch '+folder)
        with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as tar:
            for member in tar.getmembers():
                if Path(member.name).is_absolute() or '..' in Path(member.name).parts or not member.name.startswith(folder+'/') or member.issym() or member.islnk():raise ValueError('unsafe archive member')
            tar.extractall(a.output)
        hashes={str(f.relative_to(dest)):hashlib.sha256(f.read_bytes()).hexdigest() for f in dest.rglob('*') if f.is_file() and f.name!='.cargo-checksum.json'}
        (dest/'.cargo-checksum.json').write_text(json.dumps({'package':checksum,'files':hashes}))
        return {'name':name,'version':version,'archive_sha256':checksum}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:rows=list(pool.map(one,packages))
    (a.output/'source-manifest.json').write_text(json.dumps(rows,indent=2)+'\n');print('verified packages',len(rows))
if __name__=='__main__':main()
