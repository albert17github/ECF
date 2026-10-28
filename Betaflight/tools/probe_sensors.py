"""Read-only MSP v1 bench capture; no arming or actuator writes."""
import argparse
import datetime
import json
import struct
import time
from pathlib import Path
import serial

parser = argparse.ArgumentParser()
parser.add_argument('--port', required=True)
parser.add_argument('--out', required=True)
parser.add_argument('--samples', type=int, default=100)
parser.add_argument('--seconds', type=float, help='Capture for this elapsed duration instead of a sample count')
parser.add_argument('--settle-seconds', type=float, default=0)
parser.add_argument('--expected-uid', help='Expected 12-byte MSP UID, hexadecimal in wire order')
parser.add_argument('--wait-ready-seconds', type=float, default=35, help='Wait for USB enumeration and a valid disarmed MSP reply')
parser.add_argument('--require-realtime', action='store_true', help='Reject a native Betaflight LOAD arming-protection flag after settling')
args = parser.parse_args()
if args.samples <= 0 or (args.seconds is not None and args.seconds <= 0) or args.settle_seconds < 0:
    parser.error('Capture duration and sample count must be positive')

def request(s, command):
    s.write(bytes([36, 77, 60, 0, command, command]))
    deadline = time.monotonic() + 2
    buf = bytearray()
    while time.monotonic() < deadline:
        buf.extend(s.read(max(1, s.in_waiting)))
        start = buf.find(b'$M>')
        if start < 0: continue
        if len(buf) < start + 6: continue
        count, received = buf[start + 3:start + 5]
        if len(buf) < start + 6 + count: continue
        frame = buf[start + 3:start + 6 + count]
        crc = 0
        for b in frame: crc ^= b
        if crc or received != command:
            raise RuntimeError(f'Invalid MSP reply {buf.hex()}')
        return bytes(buf[start + 5:start + 5 + count])
    raise TimeoutError(f'MSP {command}: {buf.hex()}')

rows = []
doc = {'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
       'port':args.port,'identity':{},'status':[],'samples':rows}
if Path(args.out).exists():
    raise FileExistsError(args.out)

def open_ready():
    deadline=time.monotonic()+args.wait_ready_seconds
    doc['readiness_attempts']=[]
    while True:
        port=None
        try:
            port=serial.Serial(args.port,115200,timeout=.05,write_timeout=3)
            port.reset_input_buffer()
            status=request(port,101)
            if int.from_bytes(status[6:10],'little')&1:
                raise RuntimeError('Armed board')
            return port
        except (serial.SerialException,TimeoutError) as exc:
            if port is not None:port.close()
            doc['readiness_attempts'].append(str(exc))
            if time.monotonic()>=deadline:raise
            time.sleep(.3)
        except BaseException:
            if port is not None:port.close()
            raise
try:
    with open_ready() as s:
        s.reset_input_buffer()
        doc['identity'] = {str(c): request(s, c).hex() for c in (1, 2, 3, 4, 5, 101, 160)}
        if args.expected_uid and doc['identity']['160'].lower() != args.expected_uid.lower():
            raise RuntimeError('Unexpected MCU UID')
        settle_end=time.monotonic()+args.settle_seconds
        while time.monotonic()<settle_end:
            status=request(s,101)
            if int.from_bytes(status[6:10],'little') & 1: raise RuntimeError('Armed board')
            time.sleep(.2)
        started=time.monotonic();last_status=0
        while (time.monotonic()-started<args.seconds) if args.seconds is not None else (len(rows)<args.samples):
            if time.monotonic()-last_status>=1:
                status=request(s,101);last_status=time.monotonic()
                decoded={'t':time.time(),'cycle_time':int.from_bytes(status[:2],'little'),
                    'i2c_errors':int.from_bytes(status[2:4],'little'),'sensor_mask':int.from_bytes(status[4:6],'little'),
                    'mode_flags':int.from_bytes(status[6:10],'little')}
                if doc['identity'].get('2') == b'BTFL'.hex() and len(status) >= 21:
                    extra = status[15] & 15
                    if len(status) >= 21 + extra:
                        decoded['arming_disable_flags'] = int.from_bytes(status[17+extra:21+extra], 'little')
                        decoded['load_blocked'] = bool(decoded['arming_disable_flags'] & (1 << 11))
                    decoded['cpu_load_percent'] = int.from_bytes(status[11:13], 'little')
                doc['status'].append(decoded)
                if args.require_realtime and decoded.get('load_blocked'):
                    raise RuntimeError('Native realtime LOAD protection is active')
                if decoded['mode_flags'] & 1: raise RuntimeError('Armed during capture')
            raw = request(s, 102)
            alt = request(s, 109)
            attitude = request(s, 108)
            rows.append({'t':time.time(), 'imu':list(struct.unpack('<9h', raw[:18])),
                         'altitude_cm':struct.unpack('<i', alt[:4])[0],
                         'attitude':list(struct.unpack('<3h', attitude[:6]))})
            time.sleep(.1)
        doc['elapsed_seconds']=time.monotonic()-started
except Exception as exc:
    doc['error']=repr(exc)
    raise
finally:
    Path(args.out).write_text(json.dumps(doc, indent=2), encoding='utf-8')
print(json.dumps({'samples':len(rows),'elapsed_seconds':doc.get('elapsed_seconds'),
    'uid':doc['identity'].get('160'),'first':rows[0], 'last':rows[-1],
    'min_imu':[min(r['imu'][i] for r in rows) for i in range(9)],
    'max_imu':[max(r['imu'][i] for r in rows) for i in range(9)],
    'i2c_errors_start_end':[doc['status'][0]['i2c_errors'],doc['status'][-1]['i2c_errors']],
    'altitude_cm_range':[min(r['altitude_cm'] for r in rows), max(r['altitude_cm'] for r in rows)]}))
