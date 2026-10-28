"""Verify normal reboot persistence and ROM DFU UID/NRST return without Flash writes."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
from bench_connections import require_unwired_shared_pads
require_unwired_shared_pads() 
import re
import subprocess
import time

from px4_client import PX4Client
from swd_readback import SwdReadback

p=argparse.ArgumentParser(description=__doc__);p.add_argument('--uid',required=True);p.add_argument('--port',default='auto')
p.add_argument('--stlink-sn',required=True);p.add_argument('--cube');p.add_argument('--out',type=Path,required=True)
a=p.parse_args();assert not a.out.exists();swd=SwdReadback(a.uid,a.stlink_sn,a.cube)
words=' '.join(f'{int.from_bytes(bytes.fromhex(a.uid)[i:i+4],"little"):08X}' for i in [0,4,8])
doc=dict(at=datetime.now(timezone.utc).isoformat(),passed=False,normal=[],rom=[],flash_written=False,option_bytes_changed=False,arbitrary_go_used=False)
names=['ECF_IO_MODE','SENS_EN_INA238','ECF_INA_ADDR','INA238_CURRENT','INA238_SHUNT','SDLOG_MODE']
def save():a.out.write_text(json.dumps(doc,indent=2)+'\n')
def ready():
    c=PX4Client(a.port,a.uid)
    try:return dict(port=c.port,uid2=bytes(c.version.uid2).hex(),parameters=c.params(names),native=c.shell('ecf_interfaces status'))
    finally:c.close()
try:
    before=ready();doc['before']=before
    for i in range(3):
        c=PX4Client(a.port,a.uid);c.reboot();time.sleep(.3)
        after=ready();assert after['parameters']==before['parameters']
        doc['normal'].append(dict(cycle=i+1,passed=True,after=after));save()
        print(json.dumps(dict(phase='ordinary_reboot',cycle=i+1,passed=True)),flush=True)
    for i in range(3):
        row=dict(cycle=i+1,reads=[],passed=False);doc['rom'].append(row);requested=False
        c=PX4Client(a.port,a.uid)
        try:requested=True;c.enter_rom_dfu()
        finally:c.close()
        try:
            end=time.monotonic()+35
            while time.monotonic()<end:
                r=subprocess.run([swd.cube,'-c','port=USB1','-r32','0x1ff1e800','12'],capture_output=True,text=True,errors='replace',timeout=15)
                row['reads'].append(dict(exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
                match=re.search(r'0x1FF1E800\s*:\s*([\da-fA-F]{8})\s+([\da-fA-F]{8})\s+([\da-fA-F]{8})',r.stdout,re.IGNORECASE)
                if match:
                    assert ' '.join(match.groups()).upper()==words,'Different ROM UID'
                    row['rom_uid_verified']=True;break
                time.sleep(.5)
            assert row.get('rom_uid_verified'),'ROM DFU did not return the expected UID'
        finally:
            if requested:row['nrst']=swd.hard_reset(under_reset_fallback=True);row['after']=ready();save()
        assert row['after']['parameters']==before['parameters'];row['passed']=True;save()
        print(json.dumps(dict(phase='rom_dfu_nrst',cycle=i+1,passed=True)),flush=True)
    doc['passed']=True
except Exception as error:doc['error']=repr(error);raise
finally:doc['finished_at']=datetime.now(timezone.utc).isoformat();save()
print(json.dumps(dict(passed=doc['passed'],normal_reboots=len(doc['normal']),rom_dfu_cycles=len(doc['rom']))),flush=True)
