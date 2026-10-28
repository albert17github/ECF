"""Explicit unloaded, disarmed INAV raw Blackbox recording with native stop."""
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
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--seconds', type=float, default=35)
parser.add_argument('--unloaded-bench', action='store_true', required=True)
parser.add_argument('--expect-missing-gps', action='store_true')
args = parser.parse_args()
assert args.unloaded_bench and 20 <= args.seconds <= 60 and not args.out.exists()
key = b'blackbox_arm_control\x00'
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), passed=False, samples=[], motor_commands_sent=False)
client = connect(args.port, args.uid)
started = False

def status(check_health=False):
    raw = client.request(0x2000)
    cycle, errors, sensors, load = struct.unpack_from('<4H', raw)
    flags = struct.unpack_from('<I', raw, 9)[0]
    assert flags & (1 << 2) == 0, 'Armed'
    assert flags & ((1 << 10) | (1 << 27)) == 0, hex(flags)
    health = list(client.request(151)) if check_health else None
    if args.expect_missing_gps:
        assert flags & (1 << 15), hex(flags)
    else:
        assert not flags & (1 << 15), hex(flags)
    if check_health:
        assert len(health) == 9 and health[1:5] == [1, 1, 1, 1], health
        if args.expect_missing_gps:
            assert health[0] == 0 and health[5:] == [2, 0, 0, 0], health
        else:
            assert health[0] == 1, health
    assert sensors & 7 == 7, 'All three onboard sensors must remain present'
    assert flags & ((1 << 12) | (1 << 13)) == (1 << 12) | (1 << 13), 'Factory uncalibrated flags changed'
    imu = list(struct.unpack('<9h', client.request(102)[:18]))
    row = dict(seconds=round(time.monotonic() - begin, 6), cycle_us=cycle, errors=errors,
               sensors=sensors, load=load, arming_flags=flags, imu=imu, native_hardware_health=health)
    doc['samples'].append(row)
    return row

begin = time.monotonic()
try:
    assert client.request(2) == b'INAV' and client.request(3) == bytes([9, 1, 0])
    assert client.request(0x1003, key) == b'\x00', 'Begin with normal logging control'
    assert client.request(0x1003, b'blackbox_device\x00') == b'\x02', 'Onboard SD logging required'
    if args.expect_missing_gps:
        time.sleep(8)
    doc['before'] = status(check_health=True)
    # MSP_SENSOR_STATUS marks a configurator as active and inhibits manual logs.
    # Wait for that native timeout; never poll this command while recording.
    time.sleep(4)
    client.request(0x1004, key + b'\xff')
    started = True
    assert client.request(0x1003, key) == b'\xff'
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        status()
        time.sleep(.2)
    client.request(0x1004, key + b'\x00')
    started = False
    doc['native_stop_sent'] = True
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        status()
        time.sleep(.2)
    assert client.request(0x1003, key) == b'\x00'
    client.request(250)
    doc['normal_control_persisted'] = True
    doc['after'] = status(check_health=True)
    assert doc['before']['errors'] == doc['after']['errors'], 'I2C errors increased'
    doc['elapsed_seconds'] = time.monotonic() - begin
    doc['passed'] = True
finally:
    if started:
        try:
            client.request(0x1004, key + b'\x00')
            doc['stop_after_error'] = True
        except Exception as error:
            doc['stop_error'] = str(error)
    doc['packets'] = client.packets
    client.close()
    args.out.write_text(json.dumps(doc, indent=2) + '\n')
print(json.dumps({name: doc[name] for name in ('passed', 'elapsed_seconds', 'native_stop_sent', 'normal_control_persisted', 'motor_commands_sent')}))
