#!/usr/bin/env python3
"""Out-of-cgroup host and experiment-container watchdog for round-one recovery."""
import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


def meminfo():
    values={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key,value=line.split(':',1)
        values[key]=int(value.split()[0])*1024
    return values


def psi_memory():
    path=Path('/proc/pressure/memory')
    if not path.exists():return None
    result={}
    for line in path.read_text().splitlines():
        kind,*fields=line.split()
        result[kind]={part.split('=')[0]:float(part.split('=')[1]) for part in fields}
    return result


def pid_alive(pid):
    if not pid:return False
    try:
        state=Path(f'/proc/{pid}/stat').read_text().split()[2]
        return state!='Z' and os.kill(pid,0) is None
    except (OSError,IndexError,FileNotFoundError):return False


def cgroup_metrics(pid):
    if not pid_alive(pid):return {}
    try:
        relative=Path(f'/proc/{pid}/cgroup').read_text().strip().split('::',1)[1]
        base=Path('/sys/fs/cgroup')/relative.lstrip('/')
        result={'path':str(base)}
        for name in ('memory.current','memory.max','memory.high','memory.swap.current'):
            path=base/name
            if path.exists():result[name]=path.read_text().strip()
        events=base/'memory.events'
        if events.exists():
            result['memory.events']={line.split()[0]:int(line.split()[1]) for line in events.read_text().splitlines()}
        return result
    except (OSError,IndexError):return {}


def owned_containers(label):
    try:
        output=subprocess.check_output(['docker','ps','-q','--filter','label='+label+'=true'],text=True)
        return [x for x in output.splitlines() if x]
    except (OSError,subprocess.CalledProcessError):return []


def cleanup_containers(label):
    ids=owned_containers(label)
    for container in ids:
        subprocess.run(['docker','stop','-t','10',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return ids


def sample(runner_pid,label):
    mem=meminfo();swap=psi_memory()
    return {'unix_seconds':time.time(),'runner_pid':runner_pid,
        'mem_available_bytes':mem.get('MemAvailable'),'mem_total_bytes':mem.get('MemTotal'),
        'swap_total_bytes':mem.get('SwapTotal'),'swap_free_bytes':mem.get('SwapFree'),
        'memory_psi':swap,'cgroup':cgroup_metrics(runner_pid),
        'owned_containers':owned_containers(label)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runner-pid-file',type=Path)
    p.add_argument('--scope-unit')
    p.add_argument('--label',default='graft-recovery')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--interval',type=float,default=5)
    p.add_argument('--available-pause-gib',type=float,default=24)
    p.add_argument('--available-emergency-gib',type=float,default=16)
    p.add_argument('--check',action='store_true')
    a=p.parse_args()
    if a.check:
        value=sample(None,a.label)
        value['limits']={'available_pause_bytes':int(a.available_pause_gib*1024**3),
            'available_emergency_bytes':int(a.available_emergency_gib*1024**3),
            'interval_seconds':a.interval}
        print(json.dumps(value,indent=2));return
    if a.scope_unit and '.' not in a.scope_unit:a.scope_unit+='.scope'
    started=time.time();initial_swap=sample(None,a.label)['swap_free_bytes']
    low_count=0;full_count=0;load_count=0
    while True:
        pid=None
        if (a.runner_pid_file and a.runner_pid_file.exists()
                and a.runner_pid_file.stat().st_mtime>=started-1):
            try:pid=int(a.runner_pid_file.read_text().strip())
            except ValueError:pid=None
        value=sample(pid,a.label);value['status']='running'
        available=value.get('mem_available_bytes') or 0
        low_count=low_count+1 if available<int(a.available_pause_gib*1024**3) else 0
        full=((value.get('memory_psi') or {}).get('full') or {}).get('avg10',0)
        full_count=full_count+1 if full>1 else 0
        try:load1,load5,load15=os.getloadavg()
        except OSError:load1=0
        iowait=0
        try:
            fields=Path('/proc/stat').read_text().splitlines()[0].split()[1:]
            values=list(map(int,fields));total=sum(values);iowait=100*values[4]/total if total else 0
        except (OSError,IndexError):pass
        load_count=load_count+1 if load1>128 and iowait>20 else 0
        swap_growth=max(0,(initial_swap or 0)-(value.get('swap_free_bytes') or 0))
        emergency=available<int(a.available_emergency_gib*1024**3)
        pause=low_count>=2 or swap_growth>256*1024**2 or full_count>=6 or load_count>=6
        dead=pid is not None and not pid_alive(pid)
        if emergency or pause or dead:
            value['status']='emergency_stop' if emergency else ('paused_resource' if pause else 'runner_exited')
            value['reason']={'available_bytes':available,'swap_growth_bytes':swap_growth,
                'memory_full_avg10':full,'load1':load1,'iowait_fraction':iowait,'runner_alive':not dead}
            if a.scope_unit:
                subprocess.run(['systemctl','--user','stop',a.scope_unit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            value['cleaned_containers']=cleanup_containers(a.label)
            a.output.parent.mkdir(parents=True,exist_ok=True)
            a.output.write_text(json.dumps(value,indent=2)+'\n')
            return 0
        if int(time.time()-started)%60<max(1,int(a.interval)):
            a.output.parent.mkdir(parents=True,exist_ok=True)
            a.output.write_text(json.dumps(value,indent=2)+'\n')
        time.sleep(a.interval)


if __name__=='__main__':main()
