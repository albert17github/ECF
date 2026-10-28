"""Read native late-task statistics and arming protection, without arming."""
import argparse
import datetime
import json
import struct
import time
from pathlib import Path

from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--out", required=True, type=Path)
parser.add_argument("--seconds", type=float, default=30)
parser.add_argument("--settle-seconds", type=float, default=8)
parser.add_argument("--late-limit-permille", type=int, default=10)
args = parser.parse_args()
if args.out.exists():
    raise FileExistsError(args.out)
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), samples=[], passed=False, debug_mode="TIMING_ACCURACY", late_limit_permille=args.late_limit_permille)
client = connect(args.port, args.uid)
try:
    doc["identity"] = {str(command): client.request(command).hex() for command in (1, 2, 3, 5, 160)}
    time.sleep(args.settle_seconds)
    started = time.monotonic()
    while time.monotonic() - started < args.seconds:
        status = client.request(150)
        extra = status[15] & 15
        flags = int.from_bytes(status[17 + extra:21 + extra], "little")
        debug = struct.unpack("<8h", client.request(254)[:16])
        imu = struct.unpack("<9h", client.request(102)[:18])
        row = dict(elapsed=time.monotonic() - started, cycle_us=int.from_bytes(status[:2], "little"), i2c_errors=int.from_bytes(status[2:4], "little"), sensor_mask=int.from_bytes(status[4:6], "little"), cpu_load_percent=int.from_bytes(status[11:13], "little"), arming_disable_flags=flags, load_blocked=bool(flags & (1 << 11)), late_permille=debug[4], debug=list(debug), imu=list(imu))
        assert not int.from_bytes(status[6:10], "little") & 1
        doc["samples"].append(row)
        time.sleep(.2)
    rows = doc["samples"]
    doc.update(elapsed_seconds=time.monotonic() - started, maximum_late_permille=max(row["late_permille"] for row in rows), load_blocked_samples=sum(row["load_blocked"] for row in rows), i2c_errors_start_end=[rows[0]["i2c_errors"], rows[-1]["i2c_errors"]], arming_flags_seen=sorted({row["arming_disable_flags"] for row in rows}))
    doc["passed"] = doc["maximum_late_permille"] <= args.late_limit_permille and doc["load_blocked_samples"] == 0 and len({row["i2c_errors"] for row in rows}) == 1 and all(row["sensor_mask"] & 0x27 == 0x27 for row in rows)
finally:
    client.close()
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
print(json.dumps({key: value for key, value in doc.items() if key not in ("samples", "identity")}))
if not doc["passed"]:
    raise SystemExit(1)
