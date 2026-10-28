"""Back up and program an explicitly identified H743 using a checked HEX."""
import argparse
import datetime
import hashlib
import json
import re
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--image", required=True, type=Path)
parser.add_argument("--sha256", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--stlink-sn", required=True)
parser.add_argument("--backup", required=True, type=Path)
parser.add_argument("--out", required=True, type=Path)
parser.add_argument("--cube", default="C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe")
args = parser.parse_args()
if args.out.exists() or args.backup.exists():
    raise FileExistsError("Keep previous evidence and choose new output paths")
image = args.image.resolve()
assert image.suffix.lower() == ".hex", "This entry accepts addressed Intel HEX only"
assert hashlib.sha256(image.read_bytes()).hexdigest() == args.sha256.lower()
uid = bytes.fromhex(args.uid)
assert len(uid) == 12 and uid not in (bytes(12), bytes([255]) * 12)
expected = " ".join(f"{int.from_bytes(uid[i:i + 4], 'little'):08X}" for i in range(0, 12, 4))
base = 0
for line in image.read_text(encoding="ascii").splitlines():
    record = bytes.fromhex(line[1:])
    assert sum(record) & 255 == 0
    size, offset, kind = record[0], int.from_bytes(record[1:3], "big"), record[3]
    if kind == 4:
        base = int.from_bytes(record[4:6], "big") << 16
    elif kind == 0:
        assert 0x08000000 <= base + offset and base + offset + size <= 0x08200000, "HEX extends outside the H743 2MiB Flash"
    else:
        assert kind in (1, 3, 5), "Unsupported HEX addressing record"

doc = dict(started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), image=str(image), sha256=args.sha256.lower(), uid=args.uid, stlink=args.stlink_sn, passed=False, option_bytes_changed=False, uid_attempts=[])

def run(arguments, timeout):
    command = [args.cube, *arguments]
    try:
        result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=timeout)
        return dict(command=command, exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    except subprocess.TimeoutExpired as error:
        def decoded(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
        return dict(command=command, exit_code=None, timed_out=True, timeout_seconds=timeout,
                    stdout=decoded(error.stdout), stderr=decoded(error.stderr))

normal = ["-c", "port=SWD", f"sn={args.stlink_sn}", "mode=HOTPLUG", "freq=950"]
under_reset = ["-c", "port=SWD", f"sn={args.stlink_sn}", "mode=UR", "reset=HWrst", "freq=950"]
try:
    selected = None
    for connection in (normal, under_reset, [*under_reset[:-1], "freq=100"]):
        result = run([*connection, "-r32", "0x1FF1E800", "12"], 25)
        doc["uid_attempts"].append(result)
        match = re.search(r"0x1FF1E800\s*:\s*([\da-fA-F]{8})\s+([\da-fA-F]{8})\s+([\da-fA-F]{8})", result["stdout"])
        if match:
            assert " ".join(match.groups()).upper() == expected, "Wrong MCU; no Flash write attempted"
            selected = connection
            break
    assert selected is not None, "No UID-matched SWD connection"
    slow_connection = "freq=100" in selected
    doc["backup"] = run([*selected, "-u", "0x08000000", "0x200000", str(args.backup.resolve())],
                        360 if slow_connection else 120)
    assert doc["backup"]["exit_code"] == 0 and args.backup.stat().st_size == 2097152
    doc["backup_sha256"] = hashlib.sha256(args.backup.read_bytes()).hexdigest()
    args.backup.with_suffix(args.backup.suffix + ".sha256").write_text(doc["backup_sha256"] + "  " + args.backup.name + "\n")
    program_connection = [*under_reset[:-1], "freq=100"] if slow_connection else under_reset
    doc["program"] = run([*program_connection, "-w", str(image), "-v", "-hardRst"],
                         900 if slow_connection else 240)
    assert doc["program"]["exit_code"] == 0 and "Download verified successfully" in doc["program"]["stdout"]
    doc["passed"] = True
finally:
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
print(json.dumps(dict(passed=doc["passed"], image=str(image), sha256=args.sha256.lower())))
