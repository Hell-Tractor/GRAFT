#!/usr/bin/env python3
"""Audit pinned package MSRV metadata without downloading or changing crate versions.

Only a lower-bound compatibility check: passing metadata is not a compiler witness.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile
try:
    import tomllib
except ImportError:
    import tomli as tomllib

def version_tuple(v):
    match=re.match(r'^(\d+)\.(\d+)(?:\.(\d+))?',str(v))
    if not match:return None
    return tuple(int(x or 0) for x in match.groups())

def read_manifest(crate,version,roots):
    for root in roots:
        matches=sorted(root.glob(f'*/{crate}-{version}.crate'))
        if not matches:continue
        archive=matches[0];raw=archive.read_bytes()
        with tarfile.open(archive,'r:gz') as tf:
            data=tf.extractfile(f'{crate}-{version}/Cargo.toml').read()
        return tomllib.loads(data.decode()),{'archive':str(archive),'archive_sha256':hashlib.sha256(raw).hexdigest(),'manifest_sha256':hashlib.sha256(data).hexdigest()}
    return None,{'error':'exact pinned source not in local archive cache'}

def classify(package,compiler):
    edition=str(package.get('edition','2015'))
    required=package.get('rust-version')
    if edition not in ['2015','2018','2021'] and version_tuple(compiler)<(1,85,0):return 'blocked_edition'
    if required and version_tuple(required) and version_tuple(required)>version_tuple(compiler):return 'blocked_declared_msrv'
    return 'metadata_eligible_not_built'

def main():
    p=argparse.ArgumentParser();p.add_argument('--tracks',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,nargs='+',default=[Path('/home/cym/.cargo/registry/cache')]);a=p.parse_args()
    rows=[]
    for row in json.loads(a.tracks.read_text()):
        name=row['crate']
        for track,version in [('historical_candidate',row['historical_compatibility_candidate']),('newer_frozen',row['newer_track_version'])]:
            manifest,identity=read_manifest(name,version,a.cache)
            record={'crate':name,'version':version,'track':track,**identity}
            if manifest:
                package=manifest['package'];features=manifest.get('features',{})
                record.update({'edition':str(package.get('edition','2015')),'declared_rust_version':package.get('rust-version'),'default_features':features.get('default',[]),'rulf_1_66':classify(package,'1.66.0'),'rpg_1_65':classify(package,'1.65.0'),'deepsurf_1_81_documented_only':classify(package,'1.81.0'),'graft_execution_1_94':classify(package,'1.94.1'),'normal_dependencies':list(manifest.get('dependencies',{}))})
            else:record['rulf_1_66']=record['rpg_1_65']='unknown_missing_pinned_source'
            rows.append(record)
    report={'schema':'graft-baseline-compatibility-v1','scope':'Declared package MSRV/edition only. Toolchain dev prerelease handling, target-specific and transitive dependencies, language features and actual compiler/plugin execution remain unvalidated. Never silently substitute versions. deepSURF version is from local README, not a verified container binary.','rows':rows}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2)+'\n')
    for r in rows:
        if r['track']=='newer_frozen':print(r['crate'],r['version'],r.get('declared_rust_version'),r['rulf_1_66'],r['rpg_1_65'])
if __name__=='__main__':main()
