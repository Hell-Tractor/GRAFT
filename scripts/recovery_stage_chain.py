#!/usr/bin/env python3
"""Start Stage C only after a clean, fully recorded Stage B exit."""
import argparse
import json
import subprocess
import time
from pathlib import Path


def active(unit):
    return subprocess.run(['systemctl','--user','is-active','--quiet',unit]).returncode==0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--recovery',type=Path,required=True)
    p.add_argument('--batch',type=Path,required=True)
    p.add_argument('--stage-b-scope',default='graft-r1-main-20261112')
    p.add_argument('--timeout',type=int,default=21600)
    a=p.parse_args();started=time.monotonic();report=a.recovery/'stage-chain.json'
    while True:
        progress_path=a.recovery/'progress.json'
        progress=json.loads(progress_path.read_text()) if progress_path.exists() else {}
        scope_active=active(a.stage_b_scope)
        if progress.get('phase')=='stopped' and not scope_active:
            summary=a.recovery/'round1-recovery-summary.json'
            if not summary.exists():raise SystemExit('Stage B stopped without recovery summary')
            if active('graft-r1-watchdog-20261112.service'):
                time.sleep(10)
            watchdog=['systemd-run','--user','--unit=graft-r1-watchdog-stage-c','--collect','-p','MemoryMax=256M',
                'python3','/home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/recovery_resource_guard.py',
                '--runner-pid-file',str(a.recovery/'runner.pid'),'--scope-unit','graft-r1-main-stage-c',
                '--label','graft-recovery','--output',str(a.recovery/'watchdog-stage-c.json'),
                '--interval','5','--available-pause-gib','24','--available-emergency-gib','16']
            subprocess.run(watchdog,check=True,capture_output=True,text=True)
            command=['systemd-run','--user','--scope','--unit=graft-r1-main-stage-c',
                '-p','MemoryHigh=64G','-p','MemoryMax=80G','-p','MemorySwapMax=0','-p','TasksMax=1024',
                'python3','/home/cym/workspace/GRAFT-2.0/GRAFTTool/scripts/run_round1_recovery.py',
                '--batch',str(a.batch),'--recovery',str(a.recovery),'--execute']
            with (a.recovery/'stage-c.log').open('wb') as log:
                process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,start_new_session=True)
            report.write_text(json.dumps({'status':'stage_c_started','pid':process.pid,
                'command':command,'stage_b_summary':str(summary)},indent=2)+'\n')
            return 0
        if not scope_active and progress.get('phase')!='stopped':
            report.write_text(json.dumps({'status':'blocked','reason':'Stage B scope exited before clean phase stop',
                'progress':progress},indent=2)+'\n')
            return 1
        if time.monotonic()-started>a.timeout:
            report.write_text(json.dumps({'status':'blocked','reason':'Stage B chain timeout'},indent=2)+'\n')
            return 1
        time.sleep(20)


if __name__=='__main__':raise SystemExit(main())
