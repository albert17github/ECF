"""Verify native MSP v1/v2 serial settings and save an explicit test pattern."""
import argparse
import datetime
import json
import struct
from pathlib import Path

from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--out", required=True, type=Path)
parser.add_argument("--expect-initial-rx", action="store_true")
args = parser.parse_args()
if args.out.exists():
    raise FileExistsError(args.out)
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), passed=False)
client = None
v1row = struct.Struct("<BHBBBB")
v2row = struct.Struct("<BIBBBB")

def read_both():
    raw1 = client.request(54)
    raw2 = client.request(0x1009)
    assert len(raw1) % v1row.size == 0
    assert len(raw2) == 1 + raw2[0] * v2row.size
    rows1 = list(v1row.iter_unpack(raw1))
    rows2 = list(v2row.iter_unpack(raw2[1:]))
    assert rows1 == [tuple([row[0], row[1] & 65535, *row[2:]]) for row in rows2]
    return rows2

try:
    client = connect(args.port, args.uid)
    doc["identity"] = {str(command): client.request(command).hex() for command in (1, 2, 3, 4, 5, 160)}
    before = read_both()
    doc["before"] = before
    expected = {20, 51, 53, 54, 55, 56, 57}
    assert {row[0] for row in before} == expected, "Missing or unexpected serial port"
    if args.expect_initial_rx:
        assert next(row[1] for row in before if row[0] == 51) == 64, "Expected initial UART1 RX flag did not persist"
    masks = {20: 1, 51: 64, 53: 0, 54: 2, 55: 1, 56: 512, 57: 0}
    pattern1 = [tuple([row[0], masks[row[0]], *row[2:]]) for row in before]
    client.request(55, b"".join(v1row.pack(*row) for row in pattern1))
    doc["v1_roundtrip"] = read_both()
    assert doc["v1_roundtrip"] == pattern1
    # Exercise a native function bit above 16 bits without changing USB management.
    pattern2 = [tuple([row[0], 131073 if row[0] == 57 else row[1], *row[2:]]) for row in pattern1]
    client.request(0x100A, bytes([len(pattern2)]) + b"".join(v2row.pack(*row) for row in pattern2))
    doc["v2_roundtrip"] = read_both()
    assert doc["v2_roundtrip"] == pattern2
    client.request(250)
    doc["save_acknowledged"] = True
    doc["cli_entry"] = client.enter_cli()
    doc["cli"] = {}
    for command in ["feature GPS", "feature TELEMETRY", "feature RX_SERIAL", "set serialrx_provider = CRSF", "serial", "save"]:
        reply = client.cli(command)
        doc["cli"][command] = reply
        assert "###ERROR" not in reply
    doc["before_restart_packets"] = client.packets
    client.close()
    client = connect(args.port, args.uid)
    doc["after_save_reboot"] = read_both()
    assert doc["after_save_reboot"] == pattern2
    doc["passed"] = True
finally:
    if client:
        doc["packets"] = client.packets
        client.close()
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps({key: doc[key] for key in ("passed", "before", "after_save_reboot")}))
