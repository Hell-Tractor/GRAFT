#!/usr/bin/env python3
"""Read-only support inventory on the fixed 13-crate historical input set."""
import argparse,json,pathlib,subprocess,tempfile
CRATES=['autocfg','base64','bumpalo','byteorder','bytes','chrono','csv','fixedbitset','idna','regex','ryu','time','unicode-segmentation']
def main():
    p=argparse.ArgumentParser();p.add_argument('--planner',required=True);p.add_argument('--source',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True);rows=[]
    for name in CRATES:
        source=a.source/name/'doc.json'
        with tempfile.TemporaryDirectory(prefix='graft-support-') as tmp:
            catalog=pathlib.Path(tmp)/'catalog.json'
            subprocess.run([a.planner,'import',str(source),str(catalog),'--package',name],check=True)
            report=a.output/(name+'.json');subprocess.run([a.planner,'inventory',str(catalog),str(report),'--edges'],check=True)
            d=json.loads(report.read_text());rows.append({k:d[k] for k in ['crate','version','api_count','no_import_blocker','apis_with_obligations','candidate_parameter_pairs']})
    (a.output/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps({'crates':len(rows),'apis':sum(r['api_count'] for r in rows),'no_import_blocker':sum(r['no_import_blocker'] for r in rows)}))
if __name__=='__main__':main()
