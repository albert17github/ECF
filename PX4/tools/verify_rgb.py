"""Temporarily override one native PX4 LED color, then restore normal indication."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import time

from px4_client import PX4Client

p=argparse.ArgumentParser(description=__doc__);p.add_argument('--uid',required=True);p.add_argument('--port',default='auto')
p.add_argument('--color',choices=['red','green','blue','off'],required=True);p.add_argument('--seconds',type=int,default=25)
p.add_argument('--out',type=Path,required=True);a=p.parse_args();assert 10<=a.seconds<=120 and not a.out.exists()
doc=dict(at=datetime.now(timezone.utc).isoformat(),color=a.color,physical_observation='required',restored=False)
c=PX4Client(a.port,a.uid)
try:
    cmd='led_control off' if a.color=='off' else 'led_control -c '+a.color+' on'
    doc['command']=cmd;doc['reply']=c.shell(cmd)
    print(json.dumps(dict(phase='visual_hold',color=a.color,seconds=a.seconds)),flush=True)
    end=time.monotonic()+a.seconds
    while time.monotonic()<end:c.receive(.2)
finally:
    doc['reset']=c.shell('led_control reset');doc['restored']=True;c.close()
    doc['finished_at']=datetime.now(timezone.utc).isoformat();a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(doc,indent=2)+'\n')
print(json.dumps(dict(restored=doc['restored'],color=a.color)),flush=True)
