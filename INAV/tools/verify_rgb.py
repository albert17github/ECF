"""Display one native INAV LED color, then restore its exact saved LED configuration."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import time

from msp_client import connect

p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',required=True);p.add_argument('--uid',required=True)
p.add_argument('--color',choices=['red','green','blue','off'],required=True);p.add_argument('--seconds',type=int,default=25)
p.add_argument('--out',type=Path,required=True);a=p.parse_args();assert 10<=a.seconds<=120 and not a.out.exists()
doc=dict(at=datetime.now(timezone.utc).isoformat(),color=a.color,physical_observation='required',restored=False)
c=None;original=None;modified=False
try:
    c=connect(a.port,a.uid);assert c.request(2)==b'INAV';c.enter_cli()
    led=c.cli('led');colors=c.cli('color')
    original=[re.search(r'^led 0 [^\r\n]+',led,re.MULTILINE)[0],re.search(r'^color 2 [^\r\n]+',colors,re.MULTILINE)[0]]
    doc['original']=original
    hue=dict(red=0,green=120,blue=240,off=0)[a.color];level=0 if a.color=='off' else 4
    modified=True
    doc['temporary']=[c.cli('led 0 0,0::C:2'),c.cli(f'color 2 {hue},0,{level}'),c.cli('save')]
    c.close();c=connect(a.port,a.uid)
    print(json.dumps(dict(phase='visual_hold',color=a.color,hsv=[hue,0,level],seconds=a.seconds)),flush=True)
    end=time.monotonic()+a.seconds
    while time.monotonic()<end:c.guard();time.sleep(.2)
finally:
    if c:c.close();c=None
    if modified and original:
        c=connect(a.port,a.uid);c.enter_cli()
        doc['restore']=[c.cli(cmd) for cmd in original];doc['restore_save']=c.cli('save');c.close()
        c=connect(a.port,a.uid);c.enter_cli()
        doc['after']=[re.search(r'^led 0 [^\r\n]+',c.cli('led'),re.MULTILINE)[0],re.search(r'^color 2 [^\r\n]+',c.cli('color'),re.MULTILINE)[0]]
        assert doc['after']==original;doc['restored']=True;doc['exit']=c.cli('exit');c.close()
    doc['finished_at']=datetime.now(timezone.utc).isoformat();a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(doc,indent=2)+'\n')
print(json.dumps(dict(restored=doc['restored'],color=a.color)),flush=True)
