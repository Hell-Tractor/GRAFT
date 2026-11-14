#!/usr/bin/env python3
"""Generate new docs/universes for the shared compatibility pins; old docs stay untouched."""
import argparse,copy,json,os,shutil,subprocess,time
from pathlib import Path
from coverage_snapshot import freeze,save,load,digest,file_hash
from check_public_surface import doctest_analysis_view

PUBLIC={'publicly_named_function','method_on_publicly_named_receiver','trait_method_on_publicly_named_receiver','public_trait_declaration'}
REVIEW={'owner_visibility_or_applicability_unresolved','trait_impl_requires_applicability_or_opaque_receiver_review'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--versions',type=Path,required=True);p.add_argument('--project',type=Path,required=True);p.add_argument('--cargo-home',type=Path,required=True);p.add_argument('--target',type=Path,required=True);p.add_argument('--raw',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--surface-binary',type=Path,required=True);p.add_argument('--vendor-root',type=Path);a=p.parse_args()
    output=a.output.resolve();output.mkdir(parents=True,exist_ok=True);rawdir=a.raw.resolve();rawdir.mkdir(parents=True,exist_ok=True)
    rows=[];env=os.environ.copy();env['CARGO_HOME']=str(a.cargo_home.resolve());env['CARGO_TARGET_DIR']=str(a.target.resolve())
    lock_hash=file_hash(a.project/'Cargo.lock');manifest_hash=file_hash(a.project/'Cargo.toml')
    def call(cmd,log,cwd=None):
        start=time.monotonic();r=subprocess.run(list(map(str,cmd)),cwd=cwd,env=env,capture_output=True,text=True,timeout=180)
        (output/log).write_text(r.stdout+r.stderr)
        return {'command':list(map(str,cmd)),'returncode':r.returncode,'seconds':time.monotonic()-start,'log':log}
    for name,version in json.loads(a.versions.read_text())['versions'].items():
        row={'crate':name,'version':version,'features':'package defaults, shared aggregate lock, Linux x86_64','cargo_lock_sha256':lock_hash,'root_manifest_sha256':manifest_hash}
        command=['cargo','+nightly','rustdoc','--frozen','--offline','--jobs','16','--package',name+'@'+version,'--lib','--','-Z','unstable-options','--output-format','json','--document-private-items','--crate-version',version]
        row['rustdoc']=call(command,name+'-rustdoc.log',cwd=a.project)
        if row['rustdoc']['returncode']:
            row['status']='rustdoc_failed';rows.append(row);save(output/'summary.json',rows);continue
        raw=rawdir/(name+'-'+version+'.json');shutil.copyfile(a.target/'doc'/(name.replace('-','_')+'.json'),raw)
        krate=load(raw)
        if krate['crate_version']!=version or krate['format_version']!=57:raise RuntimeError('version/format mismatch')
        row.update({'raw_rustdoc_path':str(raw),'raw_rustdoc_sha256':file_hash(raw)})
        # All definitions retained; no historical name-based filter is applied.
        universe=freeze(raw,name,[],source_root=str((a.vendor_root or a.project.parent/'vendor')/(name+'-'+version)),expected_version=version)
        analysis=output/(name+'-surface.json');row['surface']=call([a.surface_binary,raw,analysis],name+'-surface.log')
        edits=[]
        if row['surface']['returncode']:
            view,edits=doctest_analysis_view(krate)
            if edits:
                viewpath=rawdir/(name+'-'+version+'-doctest-analysis-view.json');save(viewpath,view)
                row['normalization']={'original_failure':row['surface'],'edits':edits,'analysis_view_path':str(viewpath),'analysis_view_sha256':file_hash(viewpath)}
                row['surface']=call([a.surface_binary,viewpath,analysis],name+'-surface-view.log')
        if row['surface']['returncode']:
            row['status']='surface_failed';rows.append(row);save(output/'summary.json',rows);continue
        report=load(analysis);functions={f['rustdoc_id']:f for f in report['functions']}
        universe['public_surface_policy']={'name':'publicly_named_crate_owned_function_definitions_with_body_v1','impl_obligations':'retained syntactically, applicability not proven','unknown_items':'separate review population','crosscheck_semantic_sha256':digest({'functions':report['functions'],'input_doc_sha256':row['raw_rustdoc_sha256'],'binary_sha256':file_hash(a.surface_binary),'normalization_edits':edits})}
        for api in universe['apis']:
            f=functions[api['rustdoc_id']];local=not api.get('external_definition',False)
            # The snapshot records all local functions already; external summaries are not rows.
            api['public_surface_status']=f['status'];api['public_name_body_definition']=local and f['status'] in PUBLIC and f['has_body']
            api['public_surface_review_item']=local and f['status'] in REVIEW
            api['public_declaration_without_body']=local and f['status'] in PUBLIC and not f['has_body']
        universe['universe_sha256']=digest({k:v for k,v in universe.items() if k!='universe_sha256'})
        save(output/'api_universes'/(name+'.json'),universe)
        row.update({'status':'frozen','universe_sha256':universe['universe_sha256'],'public_name_body_definitions':sum(x['public_name_body_definition'] for x in universe['apis']),'review_items':sum(x['public_surface_review_item'] for x in universe['apis'])})
        rows.append(row);save(output/'summary.json',rows);print(name,version,row['status'],row['public_name_body_definitions'],flush=True)
    if file_hash(a.project/'Cargo.lock')!=lock_hash:raise RuntimeError('lock changed')
    if any(r['status']!='frozen' for r in rows):raise SystemExit('some shared universes failed; see summary.json')
if __name__=='__main__':main()
