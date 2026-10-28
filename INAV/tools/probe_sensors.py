"""Capture native INAV sensor data and full realtime protection flags, disarmed."""
import argparse
import datetime
import json
from pathlib import Path
import struct
import time

from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument('--port', required=True)
parser.add_argument('--uid', required=True)
parser.add_argument('--out', required=True, type=Path)
parser.add_argument('--seconds', type=float, default=120)
parser.add_argument('--settle-seconds', type=float, default=8)
parser.add_argument('--expect-missing-gps', action='store_true', help='Unloaded bench: require healthy onboard sensors and preserve native missing-GPS failure')
args = parser.parse_args()
assert not args.out.exists() and args.seconds > 0 and args.settle_seconds >= 0
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), passed=False, samples=[], startup_samples=[], settle_seconds=args.settle_seconds)
client = connect(args.port, args.uid)

def capture(origin, destination):
    status = client.request(0x2000)
    cycle, errors, sensors, load = struct.unpack_from('<4H', status)
    flags = struct.unpack_from('<I', status, 9)[0]
    raw = struct.unpack('<9h', client.request(102)[:18])
    altitude = client.request(109)
    row = dict(seconds=round(time.monotonic() - origin, 6), cycle_us=cycle,
               i2c_errors=errors, sensors=sensors, load_percent=load, arming_flags=flags,
               status_payload=status.hex(), imu=list(raw), navigation_altitude_cm=struct.unpack_from('<i', altitude)[0],
               barometer_altitude_cm=struct.unpack_from('<i', altitude, 6)[0])
    destination.append(row)
    assert flags & (1 << 2) == 0, 'Armed'
    health = list(client.request(151))
    row['native_hardware_health'] = health
    assert len(health) == 9 and health[1:5] == [1, 1, 1, 1], ('Onboard sensor unhealthy', health)
    if args.expect_missing_gps:
        assert health[6:] == [0, 0, 0], ('Unexpected missing hardware', health)
        if destination is doc['startup_samples']:
            assert health[5] in (0, 2), ('Unexpected GPS state', health)
            assert health[0] == int(health[5] == 0), health
        else:
            assert health[5] == 2 and health[0] == 0 and flags & (1 << 15), 'Native missing-GPS protection was not preserved'
    else:
        assert flags & (1 << 15) == 0 and health[0] == 1, ('Native hardware failure', health)
    assert flags & (1 << 27) == 0, 'Native PWM output initialization error'
    assert sensors & 7 == 7, 'Missing ACC, BARO or MAG'
    return row

try:
    doc['identity'] = {str(command): client.request(command).hex() for command in (1, 2, 3, 4, 5, 160)}
    assert doc['identity']['2'] == b'INAV'.hex() and doc['identity']['3'] == '090100'
    settling = time.monotonic()
    missing_gps_ready = not args.expect_missing_gps
    while time.monotonic() - settling < args.settle_seconds or not missing_gps_ready:
        row = capture(settling, doc['startup_samples'])
        if args.expect_missing_gps:
            missing_gps_ready = row['native_hardware_health'][5] == 2 and bool(row['arming_flags'] & (1 << 15))
            assert time.monotonic()-settling < max(120,args.settle_seconds+10), 'Native missing-GPS timeout did not occur'
        time.sleep(.1)
    doc['actual_settle_seconds'] = time.monotonic()-settling
    started = time.monotonic()
    while time.monotonic() - started < args.seconds:
        row = capture(started, doc['samples'])
        assert row['arming_flags'] & (1 << 10) == 0, 'Native system overload protection active'
        time.sleep(.1)
    rows = doc['samples']
    doc['elapsed_seconds'] = time.monotonic() - started
    doc['summary'] = dict(samples=len(rows), i2c_errors_start_end=[rows[0]['i2c_errors'], rows[-1]['i2c_errors']],
                          maximum_load_percent=max(row['load_percent'] for row in rows),
                          barometer_altitude_cm_range=[min(row['barometer_altitude_cm'] for row in rows), max(row['barometer_altitude_cm'] for row in rows)],
                          imu_ranges=[[min(row['imu'][i] for row in rows), max(row['imu'][i] for row in rows)] for i in range(9)],
                          arming_flags_seen=sorted(set(row['arming_flags'] for row in rows)),
                          startup_overload_samples=sum(bool(row['arming_flags'] & (1 << 10)) for row in doc['startup_samples']))
    assert rows[0]['i2c_errors'] == rows[-1]['i2c_errors'], 'I2C error counter increased'
    assert any(row['imu'][2] for row in rows) and any(any(row['imu'][6:9]) for row in rows)
    doc['passed'] = True
finally:
    client.close()
    args.out.write_text(json.dumps(doc, indent=2) + '\n')
print(json.dumps(dict(passed=doc['passed'], **doc['summary'])))
