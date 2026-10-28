"""Probe owned files and capture native, unarmed PX4 raw-sensor ULog data."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import time

from px4_client import PX4Client
from probe_storage import Evidence,FtpClient,probe
from mavftp_files import list_directory,read_file_burst

p=argparse.ArgumentParser(description=__doc__);p.add_argument('--uid',required=True);p.add_argument('--port',default='auto')
p.add_argument('--out',type=Path,required=True);p.add_argument('--unloaded-bench',action='store_true',required=True)
p.add_argument('--recover-record',type=Path,help='Download the exact closed log retained by a failed transfer, without recording again')
a=p.parse_args();assert not a.out.exists();a.out.parent.mkdir(parents=True,exist_ok=True)
doc=dict(at=datetime.now(timezone.utc).isoformat(),passed=False,cycles=[],arming_commands_sent=0,actuator_commands_sent=0)
c=PX4Client(a.port,a.uid);original=None;logger_requested=False
def save():a.out.write_text(json.dumps(doc,indent=2)+'\n')


def inventory():
    pending=[('/fs/microsd',0)];result={}
    while pending:
        folder,depth=pending.pop();assert depth<8
        entries,_=list_directory(c,folder)
        for entry in entries:
            if entry['name'].lower() in ['system volume information','$recycle.bin']:continue
            path=folder.rstrip('/')+'/'+entry['name']
            if entry['directory']:pending.append((path,depth+1))
            else:result[path]=entry['bytes']
    return result


try:
    if a.recover_record:
        raw_record=a.recover_record.read_bytes();previous=json.loads(raw_record)
        assert len(previous['cycles'])==3 and all(v['passed'] for v in previous['cycles'])
        assert 'cleanup_error' not in previous and previous.get('module_stop') and previous.get('after_inventory')
        assert c.params(previous['original'])==previous['original']
        doc.update({k:v for k,v in previous.items() if k not in ['passed','error','finished_at','log']})
        doc.update(passed=False,recovered_from_record_sha256=hashlib.sha256(raw_record).hexdigest(),new_recording_started=False)
        paths=[path for path in previous['after_inventory'] if path not in previous['before_inventory'] and path.lower().endswith('.ulg')]
        assert len(paths)==1
        current=inventory();assert current[paths[0]]==previous['after_inventory'][paths[0]]
        raw,evidence=read_file_burst(c,paths[0],maximum_bytes=64*1024*1024)
        assert len(raw)==current[paths[0]]
        local=a.out.with_suffix('.ulg');assert not local.exists();local.write_bytes(raw)
        doc['log']=dict(remote=paths[0],bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),transfer=evidence)
        doc['passed']=True
        print(json.dumps(dict(recovered=True,bytes=len(raw),sha256=doc['log']['sha256'])),flush=True)
        raise SystemExit(0)
    doc['before_inventory']=inventory()
    for i in range(3):
        item=dict(cycle=i+1);doc['cycles'].append(item)
        ftp=FtpClient(c.link,Evidence(item),target_system=c.system,target_component=c.component)
        assert probe(ftp,'/fs/microsd',size=65536,chunk_size=239,reset_sessions=False)
        save();print(json.dumps(dict(storage_cycle=i+1,passed=True)),flush=True)
    original=c.params(['SDLOG_MODE','SDLOG_PROFILE']);doc['original']=original
    assert original['SDLOG_MODE']==-1
    c.shell('logger stop')
    c.set_param('SDLOG_PROFILE',833)
    logger_requested=True
    doc['logger_buffer_kib']=256
    doc['polling_topic']='sensor_gyro_fifo'
    doc['start']=c.shell('logger start -m file -f -b 256 -p sensor_gyro_fifo')
    end=time.monotonic()+35
    while time.monotonic()<end:c.receive(.2)
    doc['recording_status']=c.shell('logger status')
    doc['native_logger_status']=c.shell('listener logger_status -n 1')
    doc['module_stop']=c.shell('logger stop');logger_requested=False
    after=inventory();doc['after_inventory']=after
    new=[path for path in after if path not in doc['before_inventory'] and path.lower().endswith('.ulg')]
    assert len(new)==1,new
    raw,evidence=read_file_burst(c,new[0],maximum_bytes=64*1024*1024)
    assert len(raw)==after[new[0]]
    local=a.out.with_suffix('.ulg');assert not local.exists();local.write_bytes(raw)
    doc['log']=dict(remote=new[0],bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),transfer=evidence)
    doc['passed']=True
except Exception as error:doc['error']=repr(error);raise
finally:
    try:
        if logger_requested:doc['failure_stop']=c.shell('logger stop')
        if original is not None:
            doc['restore']=[c.set_param(k,v) for k,v in original.items()]
            doc['restore_save']=c.shell('param save')
            assert c.params(original)==original
    except Exception as error:doc['cleanup_error']=repr(error);doc['passed']=False
    c.close();doc['finished_at']=datetime.now(timezone.utc).isoformat();save()
print(json.dumps(dict(passed=doc['passed'],cycles=len(doc['cycles']),log={k:v for k,v in doc.get('log',{}).items() if k!='transfer'})),flush=True)
assert doc['passed']
