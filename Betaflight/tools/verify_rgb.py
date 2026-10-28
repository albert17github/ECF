"""Temporarily select a native race-profile color without saving configuration."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from msp_client import connect

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--port',required=True);p.add_argument('--uid',required=True)
p.add_argument('--color',choices=['red','green','blue','off'],required=True)
p.add_argument('--seconds',type=int,default=35);p.add_argument('--out',type=Path,required=True)
a=p.parse_args();assert 5<=a.seconds<=120 and not a.out.exists()
color=dict(red='RED',green='GREEN',blue='BLUE',off='BLACK')[a.color]
doc=dict(at=datetime.now(timezone.utc).isoformat(),color=a.color,physical_observation='required',restored=False)
c=connect(a.port,a.uid);entered=False
try:
    assert c.request(2)==b'BTFL';c.enter_cli();entered=True
    doc['original']=c.cli('get ledstrip')
    doc['temporary']=[c.cli('set ledstrip_profile = RACE'),c.cli('set ledstrip_race_color = '+color)]
    assert 'ledstrip_race_color set to '+color in doc['temporary'][-1]
    print(json.dumps(dict(phase='visual_hold',color=a.color,seconds=a.seconds)),flush=True)
    time.sleep(a.seconds)
finally:
    if entered:doc['exit']=c.cli('exit')
    c.close()
c=connect(a.port,a.uid)
try:
    c.enter_cli();doc['after']=c.cli('get ledstrip')
    # The CLI echo differs; compare the native parameter lines only.
    def values(text):return sorted(line.strip() for line in text.splitlines() if line.startswith('ledstrip_') and ' = ' in line)
    assert values(doc['original'])==values(doc['after'])
    doc['restored']=True;doc['exit_after_readback']=c.cli('exit')
finally:c.close()
doc['finished_at']=datetime.now(timezone.utc).isoformat();a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(doc,indent=2)+'\n')
print(json.dumps(dict(restored=True,color=a.color)),flush=True)
