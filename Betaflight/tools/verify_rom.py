"""Verify native ROM-DFU entry and UID, returning through hardware NRST."""
import argparse
import datetime
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from bench_connections import require_unwired_shared_pads
require_unwired_shared_pads() 

from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--stlink-sn", required=True)
parser.add_argument("--elf", type=Path, required=True)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--cycles", type=int, default=3)
args = parser.parse_args()
assert 1 <= args.cycles <= 3 and not args.out.exists()
cube = "C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe"
uid = bytes.fromhex(args.uid)
words = " ".join(f"{int.from_bytes(uid[i:i+4], 'little'):08X}" for i in range(0, 12, 4))
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), elf=str(args.elf), elf_sha256=hashlib.sha256(args.elf.read_bytes()).hexdigest(), cycles=[], passed=False, option_bytes_changed=False)

def save():
    args.out.write_text(json.dumps(doc, indent=2) + "\n")

def cube_run(command):
    result = subprocess.run([cube, *command], capture_output=True, text=True, errors="replace", timeout=15)
    return dict(command=[cube, *command], exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)

preflight = cube_run(["-c", "port=SWD", f"sn={args.stlink_sn}", "mode=HOTPLUG", "freq=950", "-r32", "0x1FF1E800", "12"])
doc["swd_uid"] = preflight
assert preflight["exit_code"] == 0 and words in preflight["stdout"].upper()
try:
    for index in range(args.cycles):
        row = dict(index=index + 1, passed=False, rom_reads=[])
        doc["cycles"].append(row)
        client = connect(args.port, args.uid)
        entered = False
        try:
            variant = client.request(2).decode()
            assert variant in ("BTFL", "INAV"), variant
            command = "bl rom" if variant == "BTFL" else "dfu"
            row.update(variant=variant, native_command=command)
            client.enter_cli()
            reply = client.cli(command)
            assert "###ERROR" not in reply and "Unknown command" not in reply
            row["command_reply"] = reply
            entered = True
        finally:
            client.close()
        try:
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                result = cube_run(["-c", "port=USB1", "-r32", "0x1FF1E800", "12"])
                row["rom_reads"].append(result)
                match = re.search(r"0x1FF1E800\s*:\s*([\da-fA-F]{8})\s+([\da-fA-F]{8})\s+([\da-fA-F]{8})", result["stdout"])
                if match:
                    assert " ".join(match.groups()).upper() == words
                    row["rom_uid_verified"] = True
                    break
                time.sleep(.4)
            assert row.get("rom_uid_verified"), "Native ROM-DFU UID was not received"
        finally:
            if entered:
                row["hardware_reset"] = cube_run(["-c", "port=SWD", f"sn={args.stlink_sn}", "mode=UR", "reset=HWrst", "freq=100", "-hardRst"])
                assert row["hardware_reset"]["exit_code"] == 0
                client = connect(args.port, args.uid)
                try:
                    row["after"] = {str(command): client.request(command).hex() for command in (1, 2, 3, 160, 101, 102)}
                    assert client.request(2).decode() == variant
                    row["application_returned"] = True
                finally:
                    client.close()
                save()
        row["passed"] = row.get("rom_uid_verified", False) and row.get("application_returned", False)
        save()
        print(json.dumps(dict(index=index + 1, passed=row["passed"], return_method="hardware NRST")), flush=True)
    doc["passed"] = all(row["passed"] for row in doc["cycles"])
finally:
    save()
