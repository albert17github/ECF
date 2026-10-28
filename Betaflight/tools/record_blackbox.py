"""Record native Blackbox data without arming or actuator writes."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import time

from msp_client import connect

parser=argparse.ArgumentParser()
parser.add_argument('--port',required=True)
parser.add_argument('--out',required=True,type=Path)
parser.add_argument('--expected-uid',required=True)
parser.add_argument('--seconds',type=float,default=35)
parser.add_argument('--settle-seconds',type=float,default=8)
parser.add_argument('--require-realtime',action='store_true')
parser.add_argument('--unloaded-bench',action='store_true',required=True)
parser.add_argument('--verify-deadline',action='store_true',help='Observe native automatic log stop after 60 seconds')
args=parser.parse_args()
assert not args.out.exists() and ((args.verify_deadline and 64<=args.seconds<=70) or (not args.verify_deadline and 30<=args.seconds<=45))
STATUS=0x7ec0
CONTROL=0x7ec1
doc=dict(at=datetime.now(timezone.utc).isoformat(),passed=False,samples=[],actuator_commands_sent=0,
         native_arming_checks_changed=False,recording_control='ECF disarmed log-only extension')
client=None
started=False
allowed={2,79,101,102,103,104,160,STATUS,CONTROL}


def request(command,payload=b''):
    assert command in allowed
    assert not payload or command==CONTROL
    return client.request(command,payload)


def guard():
    raw=request(101)
    assert not int.from_bytes(raw[6:10],'little')&1,'Armed board'
    item=dict(time=time.monotonic(),cpu_load_percent=int.from_bytes(raw[11:13],'little'))
    if len(raw)>=21:
        extra=raw[15]&15
        flags=int.from_bytes(raw[17+extra:21+extra],'little')
        item.update(arming_disable_flags=flags,load_blocked=bool(flags&(1<<11)))
        if args.require_realtime:
            assert not item['load_blocked'],'Native realtime LOAD protection is active'
    doc['samples'].append(item)


def log_status():
    data=request(STATUS)
    assert len(data)==8
    magic,version,active,maximum=struct.unpack('<4sBBH',data)
    assert magic==b'ECFL' and version==1 and active in (0,1) and maximum==60
    return dict(version=version,active=bool(active),maximum_seconds=maximum)


def outputs():
    return dict(servos=list(struct.unpack('<8H',request(103)[:16])),
                motors=list(struct.unpack('<8H',request(104)[:16])))


def reject(payload):
    try:
        request(CONTROL,payload)
    except RuntimeError as error:
        assert 'MSP rejected command' in str(error)
        return dict(payload=payload.hex(),rejected=True)
    raise AssertionError('Invalid log control accepted')


try:
    client=connect(args.port,args.expected_uid)
    assert request(2)==b'BTFL'
    doc['uid']=client.uid
    doc['capability']=log_status()
    assert not doc['capability']['active']
    settling=time.monotonic()+args.settle_seconds
    while time.monotonic()<settling:
        raw=client.guard()
        time.sleep(.2)
    guard()
    doc['before']=outputs()
    doc['negative_controls']=[reject(b'bad'),reject(b'FAIL\x00'),reject(b'ECFL\x02')]
    request(CONTROL,b'ECFL\x01');started=True
    assert log_status()['active']
    doc['duplicate_start']=reject(b'ECFL\x01')
    start=time.monotonic()
    deadline_states=[]
    while time.monotonic()-start<args.seconds:
        guard();request(102);request(79);time.sleep(.25)
        if args.verify_deadline:
            deadline_states.append(dict(elapsed=time.monotonic()-start,**log_status()))
    doc['recording_seconds']=time.monotonic()-start
    if args.verify_deadline:
        doc['deadline_states']=deadline_states
        assert all(row['active'] for row in deadline_states if row['elapsed']<59)
        stopped=next(row['elapsed'] for row in deadline_states if not row['active'])
        assert 59<=stopped<=61.5 and not deadline_states[-1]['active']
        doc['native_deadline_verified']=True
        doc['automatic_stop_first_observed_seconds']=stopped
    request(CONTROL,b'ECFL\x00');started=False
    doc['after_stop']=log_status();assert not doc['after_stop']['active']
    end=time.monotonic()+8
    while time.monotonic()<end:
        guard();request(79);time.sleep(.5)
    doc['normal_stop_wait_seconds']=8
    doc['after']=outputs()
    assert doc['before']==doc['after'],'Disarmed outputs changed during recording'
    assert not any(row['command']==214 for row in client.packets)
    doc['passed']=True
except Exception as error:
    doc['error']=repr(error);raise
finally:
    if client:
        if started:
            try:request(CONTROL,b'ECFL\x00')
            except Exception as error:doc['stop_error']=repr(error)
        doc['packets']=client.packets
        client.close()
    args.out.write_text(json.dumps(doc,indent=2)+'\n')
print(json.dumps({k:v for k,v in doc.items() if k not in {'packets','samples','deadline_states'}}))
