"""Verify all eight native serial entries, saved roles and physical UART mappings."""
# AP_FLAKE8_CLEAN
import argparse
import datetime
import json
from pathlib import Path
from bench_connections import require_unwired_shared_pads
require_unwired_shared_pads() 
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
           external_uart_wire_transfer_tested=False)
client = None
swd = SwdReadback(args.uid, args.stlink_sn)
ports = (1, 3, 4, 5, 6, 7, 8)
definitions = {
    1: (0x40011000, ("A", 9, 7), ("A", 10, 7)),
    3: (0x40004800, ("D", 8, 7), ("D", 9, 7)),
    4: (0x40004c00, ("D", 1, 8), ("D", 0, 8)),
    5: (0x40005000, ("B", 13, 14), ("B", 12, 14)),
    6: (0x40011400, ("C", 6, 7), ("C", 7, 7)),
    7: (0x40007800, ("E", 8, 7), ("E", 7, 7)),
    8: (0x40007c00, ("E", 1, 8), ("E", 0, 8)),
}
gpio_bases = {bank: 0x58020000 + index * 0x400 for index, bank in enumerate("ABCDE")}


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
    item["readback_parameters"] = client.params(changes)
    expected = dict(changes)
    for i in ports:
        if changes.get(f"SERIAL{i}_PROTOCOL") == 16:
            expected[f"SERIAL{i}_BAUD"] = 115
    item["expected_native_parameters"] = expected
    assert item["readback_parameters"] == expected


try:
    client = APClient(args.port, args.uid)
    assert int(client.read_param("BRD_OPTIONS").param_value) & 8
    names = ["BRD_ALT_CONFIG"] + [f"SERIAL{i}_{field}" for i in ports for field in ("PROTOCOL", "BAUD")]
    doc["initial_parameters"] = client.params(names)
    assert doc["initial_parameters"]["BRD_ALT_CONFIG"] == 1
    assert {i:doc['initial_parameters'][f'SERIAL{i}_PROTOCOL'] for i in ports} == {1:23,3:-1,4:5,5:2,6:5,7:16,8:-1}
    assert client.read_param("SERIAL0_PROTOCOL").param_value == 2
    configurations = [(2, 5, 23, 16, 2, 1, 2), (23, 2, 5, 1, 16, 6, 2)]
    for index, roles in enumerate(configurations):
        item = dict(index=index + 1)
        doc["cases"].append(item)
        changes = dict(BRD_ALT_CONFIG=3)
        changes.update({f"SERIAL{i}_PROTOCOL": role for i, role in zip(ports, roles)})
        changes.update({f"SERIAL{i}_BAUD": 115 if index == 0 else 57 for i in ports})
        restart(changes, item)
        text, evidence = read_file(client, "@SYS/uarts.txt", maximum_bytes=65536)
        item["native_uarts"] = text.decode()
        item["transfer"] = evidence
        assert "SERIAL0 OTG1" in text.decode() and "SERIAL2 EMPTY" in text.decode()
        for i in ports:
            assert f"SERIAL{i} UART{i}" in text.decode()
        raw = swd.read([(base, 40) for base in gpio_bases.values()] + [(definition[0], 16) for definition in definitions.values()])
        item["hardware"] = raw
        words = raw["words"]
        item["pin_mapping"] = {}
        for i, (base, tx, rx) in definitions.items():
            state = dict(cr1=words[base], pins={})
            for direction, (bank, pin, alternate) in (("TX", tx), ("RX", rx)):
                address = gpio_bases[bank]
                mode = words[address] >> (pin * 2) & 3
                af = words[address + 32 + pin // 8 * 4] >> (pin % 8 * 4) & 15
                state["pins"][direction] = dict(pin=f"P{bank}{pin}", mode=mode, alternate=af)
                if direction == "RX" or changes[f"SERIAL{i}_PROTOCOL"] in (1, 2, 5, 6):
                    assert mode == 2 and af == alternate, (i, direction, state)
            assert state["cr1"] & 1, (i, "UART not enabled", state)
            item["pin_mapping"][str(i)] = state
        assert client.read_param("SERIAL0_PROTOCOL").param_value == 2
        item["passed"] = True
        persist()
        print(json.dumps(dict(case=index + 1, passed=True, physical_serial_entries=8)), flush=True)
    doc["restore"] = {}
    restart(doc["initial_parameters"], doc["restore"])
    doc["passed"] = True
except Exception as error:
    doc["error"] = repr(error)
    if client:
        doc["texts"] = client.texts
    raise
finally:
    if client:
        client.close()
    persist()
print(json.dumps(dict(passed=doc["passed"], cases=len(doc["cases"]))))
