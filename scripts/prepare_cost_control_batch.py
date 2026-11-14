#!/usr/bin/env python3
"""Create a separate cost-policy batch without copying or changing old results."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True);a=p.parse_args()
    source=a.source.resolve();destination=a.destination.resolve()
    if destination.exists():raise SystemExit('destination already exists; do not mix policy variants')
    for name in ('runtime','deepsurf-port'):
        if not (source/name).is_dir():raise SystemExit('missing prepared prerequisite: '+name)
    destination.mkdir(parents=True)
    (destination/'runtime').symlink_to(source/'runtime',target_is_directory=True)
    native=destination/'deepsurf-port/build/release/harness_generator'
    native.parent.mkdir(parents=True)
    # An absolute host symlink outside the batch would be broken inside the
    # isolated container, where only this batch is mounted. Copy the executable.
    shutil.copy2(source/'deepsurf-port/build/release/harness_generator',native)
    # Keep the existing operator decision. In the current source batch this
    # disables regeneration for round two; do not re-enable paid calls implicitly.
    control=json.loads((source/'control.json').read_text()) if (source/'control.json').exists() else {'auto_second':False,'stop_after_round':1}
    (destination/'control.json').write_text(json.dumps(control,indent=2)+'\n')
    scripts=Path(__file__).resolve().parent
    files=['model_cost_control.py','costed_model_request.py','launch_controlled_round.py','prepare_deepsurf_round.py']
    manifest={'scope':'Separate non-thinking cost policy variant; no copied responses, targets or coverage results.',
        'source_batch':str(source),'source_protocol_sha256':hashlib.sha256((source/'protocol.json').read_bytes()).hexdigest() if (source/'protocol.json').exists() else None,
        'implementation_sha256':{n:hashlib.sha256((scripts/n).read_bytes()).hexdigest() for n in files},
        'borrowed_readonly_inputs':{'runtime':str((source/'runtime').resolve())},
        'native_generator':{'source':str(source/'deepsurf-port/build/release/harness_generator'),
            'copy':str(native),'sha256':hashlib.sha256(native.read_bytes()).hexdigest()},
        'baseline_generation_note':'Round-one RPG/RULF inventories and native generation times are reused from the common prepared baseline directory; do not label them as newly timed generation.'}
    (destination/'batch-origin.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(destination)


if __name__=='__main__':main()
