"""Verify the running image against an addressed HEX, excluding mutable config."""
import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--image", type=Path, required=True)
parser.add_argument("--sha256", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--stlink-sn", required=True)
parser.add_argument("--backup", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--configuration-start", type=lambda value: int(value, 0), default=0x08020000)
parser.add_argument("--configuration-end", type=lambda value: int(value, 0), default=0x08040000)
parser.add_argument("--cube", default="C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe")
args = parser.parse_args()
assert not args.backup.exists() and not args.out.exists()
assert hashlib.sha256(args.image.read_bytes()).hexdigest() == args.sha256.lower()
uid = bytes.fromhex(args.uid)
assert len(uid) == 12
assert 0x08000000 <= args.configuration_start < args.configuration_end <= 0x08200000
expected = " ".join(f"{int.from_bytes(uid[i:i+4], 'little'):08X}" for i in range(0, 12, 4))
cube = args.cube
connection = [cube, "-c", "port=SWD", f"sn={args.stlink_sn}", "mode=HOTPLUG", "freq=950"]
doc = dict(image=str(args.image), image_sha256=args.sha256.lower(), uid=args.uid, passed=False,
           mutable_configuration=[hex(args.configuration_start), hex(args.configuration_end)])
try:
    doc['uid_read_attempts'] = []
    for attempt in range(12):
        result = subprocess.run([*connection, "-r32", "0x1FF1E800", "12"], capture_output=True, text=True, errors="replace", timeout=20)
        doc["uid_read"] = dict(exit_code=result.returncode, stdout=result.stdout)
        doc['uid_read_attempts'].append(doc['uid_read'])
        match = re.search(r'0x1FF1E800\s*:\s*([\da-fA-F]{8})\s+([\da-fA-F]{8})\s+([\da-fA-F]{8})', result.stdout)
        if match:
            assert ' '.join(match.groups()).upper() == expected, 'Wrong MCU UID'
            if result.returncode == 0:
                break
        time.sleep(.25)
    else:
        raise RuntimeError('UID-matched SWD readback connection did not become ready')
    result = subprocess.run([*connection, "-u", "0x08000000", "0x200000", str(args.backup.resolve())], capture_output=True, text=True, errors="replace", timeout=120)
    doc["readback"] = dict(exit_code=result.returncode, stdout=result.stdout)
    raw = args.backup.read_bytes()
    assert result.returncode == 0 and len(raw) == 2097152
    doc["backup_sha256"] = hashlib.sha256(raw).hexdigest()
    base = 0
    compared = skipped = 0
    mismatches = []
    for line in args.image.read_text(encoding="ascii").splitlines():
        record = bytes.fromhex(line[1:])
        assert sum(record) & 255 == 0
        kind = record[3]
        if kind == 4:
            base = int.from_bytes(record[4:6], "big") << 16
        elif kind == 0:
            address = base + int.from_bytes(record[1:3], "big")
            for index, value in enumerate(record[4:4 + record[0]]):
                current = address + index
                assert 0x08000000 <= current < 0x08200000
                if args.configuration_start <= current < args.configuration_end:
                    skipped += 1
                    continue
                compared += 1
                if raw[current - 0x08000000] != value:
                    if len(mismatches) < 16:
                        mismatches.append(hex(current))
    doc.update(compared_bytes=compared, configuration_bytes_excluded=skipped, mismatches=mismatches, passed=not mismatches)
finally:
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
print(json.dumps({key: doc[key] for key in ("passed", "compared_bytes", "configuration_bytes_excluded", "mismatches")}))
if not doc["passed"]:
    raise SystemExit(1)
