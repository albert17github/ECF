"""Hold one native LED_CONTROL color for visual inspection, then restore status LEDs."""
# AP_FLAKE8_CLEAN
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from arducopter_client import APClient

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--port', default='auto')
p.add_argument('--uid', required=True)
p.add_argument('--color', required=True, choices=['red', 'green', 'blue', 'off'])
p.add_argument('--seconds', type=int, default=45)
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
assert 5 <= a.seconds <= 120 and not a.out.exists()
rgb = dict(red=[255, 0, 0], green=[0, 255, 0], blue=[0, 0, 255], off=[0, 0, 0])[a.color]
doc = dict(at=datetime.now(timezone.utc).isoformat(), color=a.color, rgb=rgb,
           physical_observation='required', control_sent=False, restored=False)
client = APClient(a.port, a.uid)
original = None
try:
    original = client.read_param('NTF_LED_OVERRIDE').param_value
    assert original == 0, 'Expected normal status indication before visual verification'
    doc['uid2'] = bytes(client.version.uid2).hex()
    doc['enable'] = client.set_param('NTF_LED_OVERRIDE', 1)
    client.link.mav.srcSystem = 255
    end = time.monotonic() + a.seconds
    print(json.dumps(dict(phase='visual_hold', color=a.color, seconds=a.seconds)), flush=True)
    while time.monotonic() < end:
        client.confirm_disarmed()
        client.link.mav.led_control_send(client.system, 0, 255, 255, 3, rgb + [0] * 21)
        client.sample(.4, {'HEARTBEAT'})
        doc['control_sent'] = True
finally:
    if original is not None:
        doc['restore'] = client.set_param('NTF_LED_OVERRIDE', original)
        doc['restored'] = client.read_param('NTF_LED_OVERRIDE').param_value == original
    client.close()
    doc['finished_at'] = datetime.now(timezone.utc).isoformat()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(doc, indent=2) + '\n')
print(json.dumps(dict(restored=doc['restored'])), flush=True)
