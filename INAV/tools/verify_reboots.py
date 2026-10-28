"""Compare native INAV configuration over UID-guarded ordinary CLI reboots."""
import argparse
import datetime
import json
from pathlib import Path
import re
import time

from msp_client import connect

parser=argparse.ArgumentParser()
parser.add_argument('--port',required=True)
parser.add_argument('--uid',required=True)
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args()
assert not args.out.exists()
doc=dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),passed=False,cycles=[],
         ignored_per_boot_ram_fields=['gyro_zero_x','gyro_zero_y','gyro_zero_z'])
client=None
entered=False

def persist():
    args.out.write_text(json.dumps(doc,indent=2)+'\n')

def snapshot(minimum_uptime=0):
    while True:
        status=client.cli('status')
        match=re.search(r'System Uptime:\s*(\d+)\s*seconds',status)
        assert match and 'INAV/ECFH743 9.1.0' in status
        uptime=int(match[1])
        if uptime>=minimum_uptime:
            break
        time.sleep(min(1,minimum_uptime-uptime))
    return dict(status=status,uptime=uptime,diff=client.cli('diff all'))

def normalized(text):
    assert '# INAV/' in text
    return re.sub(r'(?m)^set gyro_zero_[xyz] = -?\d+\r?\n','',text)

try:
    client=connect(args.port,args.uid)
    assert client.request(2)==b'INAV'
    client.enter_cli()
    entered=True
    before=snapshot(20)
    doc['before']=before
    for index in range(3):
        client.cli('exit')
        entered=False
        client.close()
        client=connect(args.port,args.uid)
        client.enter_cli()
        entered=True
        after=snapshot()
        row=dict(index=index+1,before_uptime=before['uptime'],after=after,
                 uptime_restarted=after['uptime']<before['uptime'],
                 parameters_unchanged=normalized(after['diff'])==normalized(doc['before']['diff']))
        doc['cycles'].append(row)
        persist()
        assert row['uptime_restarted'] and row['parameters_unchanged']
        print(json.dumps(dict(index=index+1,passed=True)),flush=True)
        if index<2:
            before=snapshot(20)
    doc['return_to_application']=client.cli('exit')
    entered=False
    client.close()
    client=connect(args.port,args.uid)
    doc['identity_after']={str(command):client.request(command).hex() for command in (2,3,160,0x2000)}
    doc['additional_exit_reset_after_comparisons']=True
    doc['passed']=True
finally:
    if client:
        if entered:
            try:doc['exit_after_failure']=client.cli('exit')
            except Exception as error:doc['exit_after_failure_error']=str(error)
        client.close()
    persist()
print(json.dumps(dict(passed=doc['passed'],cycles=len(doc['cycles']))))
