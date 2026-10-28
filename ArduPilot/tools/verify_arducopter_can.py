"""Validate native CAN1/CAN2 drivers and nominal timing without external transceivers."""
# AP_FLAKE8_CLEAN
import argparse
import datetime
import json
from pathlib import Path
import re
import time

from arducopter_client import APClient
from arducopter_ftp import read_file
from swd_readback import SwdReadback

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--stlink-sn", required=True)
parser.add_argument("--unloaded-bench", action="store_true", required=True)
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
assert not args.out.exists()
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), cases=[], passed=False,
           physical_external_bus_tested=False, internal_loopback_tested=False, native_kernel_clock_hz=80000000)
client = None
swd = SwdReadback(args.uid, args.stlink_sn)


def persist():
    args.out.write_text(json.dumps(doc, indent=2) + "\n")


def restart(changes, item):
    global client
    item["changes"] = [client.set_param(name, value) for name, value in changes.items()]
    persist()
    client.reboot()
    client = None
    time.sleep(1)
    client = APClient(args.port, args.uid)
    client.interval(1, 200000)
    client.wait("SYS_STATUS", lambda m: m.onboard_control_sensors_health & 15 == 15, timeout=30)
    item["parameters"] = client.params(changes)
    assert item["parameters"] == changes


def inspect_controller(number):
    raw, evidence = read_file(client, f"@SYS/can{number - 1}_stats.txt", maximum_bytes=65536)
    text = raw.decode()
    assert text.strip(), "Native CAN driver was not created"
    requested = re.search(r"tx_requests:\s*(\d+)", text)
    assert requested and int(requested[1]) > 0, text
    base = 0x4000a000 + (number - 1) * 0x400
    snapshot = swd.read([(base + 24, 8), (0x58020400, 40)])
    words = snapshot["words"]
    nbtp = words[base + 28]
    prescaler = (nbtp >> 16 & 0x1ff) + 1
    segment1 = (nbtp >> 8 & 255) + 1
    segment2 = (nbtp & 127) + 1
    actual_rate = 80000000 / (prescaler * (1 + segment1 + segment2))
    for pin in ((8, 9) if number == 1 else (5, 6)):
        assert words[0x58020400] >> (pin * 2) & 3 == 2
        assert words[0x58020420 + pin // 8 * 4] >> (pin % 8 * 4) & 15 == 9
    return dict(native_stats=text, transfer=evidence, readback=snapshot, decoded_bitrate=actual_rate,
                tx_requests=int(requested[1]), transmitted_frames_verified=False)


try:
    client = APClient(args.port, args.uid)
    assert int(client.read_param("BRD_OPTIONS").param_value) & 8
    names = [f"CAN_P{i}_{field}" for i in (1, 2) for field in ("DRIVER", "BITRATE")]
    names += ["CAN_D1_PROTOCOL", "CAN_D2_PROTOCOL"]
    doc["initial_parameters"] = client.params(names)
    assert {i:doc["initial_parameters"][f"CAN_P{i}_DRIVER"] for i in (1, 2)} == {1:1,2:2}
    for number in (1, 2):
        for rate in (125000, 250000, 500000, 1000000):
            item = dict(controller=number, bitrate=rate)
            doc["cases"].append(item)
            changes = dict(CAN_P1_DRIVER=1 if number == 1 else 0, CAN_P2_DRIVER=2 if number == 2 else 0,
                           CAN_D1_PROTOCOL=1, CAN_D2_PROTOCOL=1)
            changes[f"CAN_P{number}_BITRATE"] = rate
            restart(changes, item)
            item["controller_state"] = inspect_controller(number)
            assert item["controller_state"]["decoded_bitrate"] == rate
            item["passed"] = True
            persist()
            print(json.dumps(dict(controller=number, bitrate=rate, passed=True)), flush=True)
    both = dict(name="both_independent_drivers")
    doc["cases"].append(both)
    restart(dict(CAN_P1_DRIVER=1, CAN_P2_DRIVER=2, CAN_P1_BITRATE=500000, CAN_P2_BITRATE=500000,
                 CAN_D1_PROTOCOL=1, CAN_D2_PROTOCOL=1), both)
    both["controllers"] = {str(number): inspect_controller(number) for number in (1, 2)}
    assert all(state["decoded_bitrate"] == 500000 for state in both["controllers"].values())
    both["passed"] = True
    doc["restore"] = {}
    restart(doc["initial_parameters"], doc["restore"])
    doc["passed"] = True
except Exception as error:
    doc["error"] = repr(error)
    if client:
        doc["texts"] = client.texts
        doc["latest"] = client.latest
    raise
finally:
    if client:
        client.close()
    persist()
print(json.dumps(dict(passed=doc["passed"], cases=len(doc["cases"]))))
