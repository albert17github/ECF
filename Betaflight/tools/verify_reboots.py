"""Check three ordinary CLI exit/reboots and persisted configuration, disarmed."""
import argparse,datetime,json,re,time
from pathlib import Path
import serial
p=argparse.ArgumentParser()
p.add_argument('--port',required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--ignore-inav-runtime-gyro-zero',action='store_true',help='Ignore only INAV per-boot gyro_zero_x/y/z RAM values in CLI comparison; raw snapshots are retained')
p.add_argument('--min-initial-uptime',type=int,default=0,help='Wait in CLI until the first before-reboot uptime reaches this many seconds')
a=p.parse_args()
if a.output.exists(): raise FileExistsError(a.output)
d={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cycles':[],'passed':False}
if a.ignore_inav_runtime_gyro_zero:
    d['ignored_runtime_fields']=['gyro_zero_x','gyro_zero_y','gyro_zero_z']

def comparable(text):
    if not a.ignore_inav_runtime_gyro_zero:return text
    if '# INAV/' not in text:raise RuntimeError('Runtime gyro normalization is limited to INAV')
    return re.sub(r'(?m)^set gyro_zero_[xyz] = -?\d+\r?\n','',text)

def quiet(s):
    out=bytearray();end=time.monotonic()+7;last=time.monotonic()
    while time.monotonic()<end:
        part=s.read(max(s.in_waiting,1))
        if part:out.extend(part);last=time.monotonic()
        if out and time.monotonic()-last>.25:break
    return out.decode(errors='replace')

def snapshot(reboot):
    deadline=time.monotonic()+35
    while True:
        try:
            s=serial.Serial(a.port,115200,timeout=.05)
            s.reset_input_buffer();s.write(bytes([36,77,60,0,101,101]))
            raw=bytearray();reply_deadline=min(deadline,time.monotonic()+1.5)
            ready=False
            while time.monotonic()<reply_deadline:
                raw.extend(s.read(max(1,s.in_waiting)))
                start=raw.find(b'$M>')
                if start<0 or len(raw)<start+6:continue
                size=raw[start+3]
                if len(raw)<start+6+size:continue
                check=0
                for b in raw[start+3:start+6+size]:check^=b
                if check or raw[start+4]!=101:raise RuntimeError('Invalid MSP STATUS')
                payload=raw[start+5:start+5+size]
                if int.from_bytes(payload[6:10],'little')&1:raise RuntimeError('Armed board')
                ready=True;break
            if ready:break
            s.close()
            if time.monotonic()>deadline:raise TimeoutError('No MSP STATUS after startup wait')
            time.sleep(.3)
        except serial.SerialException:
            if time.monotonic()>deadline: raise
            time.sleep(.3)
    try:
        s.write(b'\r\n#');quiet(s);s.write(b'\r\n');quiet(s)
        values={}
        for cmd in ['status','diff all']:
            s.write((cmd+'\r\n').encode());values[cmd]=quiet(s)
        match=re.search(r'System Uptime:\s*(\d+)\s*seconds',values['status'])
        if not match:raise RuntimeError('No CLI uptime')
        values['uptime_seconds']=int(match[1])
        if reboot and values['uptime_seconds'] < a.min_initial_uptime:
            time.sleep(a.min_initial_uptime-values['uptime_seconds']+1)
            s.write(b'status\r\n');values['status']=quiet(s)
            values['uptime_seconds']=int(re.search(r'System Uptime:\s*(\d+)\s*seconds',values['status'])[1])
        # Exit without saving is the native ordinary software restart.
        if reboot:s.write(b'exit\r\n')
        return values
    finally:s.close()

try:
    before=snapshot(True)
    d['before']=before
    for i in range(3):
        time.sleep(2)
        after=snapshot(False)
        row={'index':i+1,'before_uptime':before['uptime_seconds'],'after':after,
             'uptime_restarted':after['uptime_seconds']<before['uptime_seconds'],
             'parameters_unchanged':comparable(after['diff all'])==comparable(d['before']['diff all'])}
        d['cycles'].append(row)
        if not row['uptime_restarted'] or not row['parameters_unchanged']:raise RuntimeError('Reboot or config mismatch')
        print(json.dumps({k:row[k] for k in ['index','uptime_restarted','parameters_unchanged']}),flush=True)
        # We are still in CLI: leave it before the next MSP check.
        if i<2:
            time.sleep(10)
            with serial.Serial(a.port,115200,timeout=.05) as s:
                s.write(b'status\r\n');text=quiet(s)
                before={'uptime_seconds':int(re.search(r'System Uptime:\s*(\d+)\s*seconds',text)[1])}
                s.write(b'exit\r\n')
        else:
            # Leave normal application running. This extra exit is not counted.
            with serial.Serial(a.port,115200,timeout=.05) as s:s.write(b'exit\r\n')
    d['passed']=True
finally:
    d['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    a.output.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
