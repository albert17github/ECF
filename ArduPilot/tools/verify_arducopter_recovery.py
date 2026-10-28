"""Check native bootloader identity/CRC and ROM DFU, returning without Flash writes."""
# AP_FLAKE8_CLEAN
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from bench_connections import require_unwired_shared_pads
require_unwired_shared_pads() 
import re
import struct
import subprocess
import time
import zlib

import serial
from serial.tools import list_ports

from arducopter_client import APClient, mavutil
from swd_readback import SwdReadback


def find_port(uid, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = [p.device for p in list_ports.comports()
                 if (p.vid, p.pid, p.serial_number) == (0x1209, 0x5741, uid.lower())]
        assert len(found) <= 1, "Multiple matching USB serial devices"
        if found:
            return found[0]
        time.sleep(.25)
    raise TimeoutError("Native USB serial device")


class Bootloader:
    def __init__(self, uid):
        self.port = None
        deadline = time.monotonic() + 35
        errors = []
        while time.monotonic() < deadline:
            try:
                self.port = serial.Serial(find_port(uid, 2), 115200, timeout=.1, write_timeout=3)
                self.port.reset_input_buffer()
                self.command(0x21)
                return
            except (OSError, TimeoutError, AssertionError) as error:
                errors.append(str(error))
                self.close()
                time.sleep(.25)
        raise TimeoutError("Native bootloader sync: " + repr(errors[-3:]))

    def close(self):
        if self.port is not None:
            self.port.close()
            self.port = None

    def command(self, opcode, payload=b"", size=0, timeout=5):
        assert opcode in (0x21, 0x22, 0x29, 0x2b, 0x30), "Only read/sync/native application boot allowed"
        self.port.write(bytes([opcode]) + payload + b"\x20")
        response = bytearray()
        deadline = time.monotonic() + timeout
        while len(response) < size + 2 and time.monotonic() < deadline:
            response.extend(self.port.read(size + 2 - len(response)))
        assert len(response) == size + 2 and response[-2:] == b"\x12\x10", response.hex()
        return bytes(response[:size])


def ready(uid):
    client = APClient(find_port(uid), uid)
    try:
        client.interval(1, 200000)
        status = client.wait("SYS_STATUS", lambda m: m.onboard_control_sensors_health & 15 == 15, timeout=35)
        return dict(port=client.port, uid2=bytes(client.version.uid2).hex(),
                    version=client.version.to_dict(), sensor_status=status.to_dict())
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uid", required=True)
    parser.add_argument("--stlink-sn", required=True)
    parser.add_argument("--apj", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument("--cube", default=os.environ.get("ECF_CUBE_PROGRAMMER"))
    args = parser.parse_args()
    assert not args.out.exists()
    raw_uid = bytes.fromhex(args.uid)
    assert len(raw_uid) == 12
    image = json.loads(args.apj.read_text())
    binary = zlib.decompress(base64.b64decode(image["image"]))
    assert image["board_id"] == 65001 and len(binary) == image["image_size"] <= 1703936
    # The native uploader uses initial state zero and no final CRC xor.
    expected_crc = zlib.crc32(binary + b"\xff" * (1703936 - len(binary)), 0xffffffff) ^ 0xffffffff
    swd = SwdReadback(args.uid, args.stlink_sn, args.cube)
    expected_words = " ".join(f"{int.from_bytes(raw_uid[i:i + 4], 'little'):08X}" for i in range(0, 12, 4))
    doc = dict(at=datetime.now(timezone.utc).isoformat(), passed=False, bootloader_cycles=[], rom_cycles=[],
               flash_written=False, option_bytes_changed=False, arbitrary_go_used=False,
               apj_sha256=hashlib.sha256(args.apj.read_bytes()).hexdigest(), expected_app_crc=expected_crc)

    def save():
        args.out.write_text(json.dumps(doc, indent=2) + "\n")

    save()
    try:
        doc["before"] = ready(args.uid)
        for index in range(args.cycles):
            row = dict(cycle=index + 1, passed=False)
            doc["bootloader_cycles"].append(row)
            client = APClient(find_port(args.uid), args.uid)
            try:
                client.confirm_disarmed()
                client.link.mav.command_long_send(
                    client.system, client.component, mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
                    0, 3, 0, 0, 0, 0, 0, 0)
            finally:
                client.close()
            time.sleep(.5)
            boot = Bootloader(args.uid)
            native_return_requested = False
            try:
                info = {str(which): struct.unpack("<I", boot.command(0x22, bytes([which]), 4))[0]
                        for which in (1, 2, 3, 4)}
                uid = b"".join(boot.command(0x2b, struct.pack("<I", offset), 4) for offset in (0, 4, 8))
                crc = struct.unpack("<I", boot.command(0x29, size=4, timeout=10))[0]
                row.update(device_info=info, native_uid=uid.hex(), application_crc=crc)
                assert info["1"] == 5 and info["2"] == 65001 and info["4"] == 1703936
                assert uid == raw_uid and crc == expected_crc
                try:
                    native_return_requested = True
                    row["native_boot_reply"] = boot.command(0x30).hex()
                except (OSError, AssertionError) as error:
                    row["boot_reply_lost_during_usb_return"] = str(error)
            finally:
                boot.close()
                if not native_return_requested:
                    row["failure_cleanup_nrst"] = swd.hard_reset(under_reset_fallback=True)
                    row["failure_cleanup_application"] = ready(args.uid)
                    save()
            row["after"] = ready(args.uid)
            row["passed"] = True
            save()
            print(json.dumps(dict(phase="native_bootloader", cycle=index + 1, passed=True)), flush=True)

        for index in range(args.cycles):
            row = dict(cycle=index + 1, passed=False, rom_reads=[])
            doc["rom_cycles"].append(row)
            client = APClient(find_port(args.uid), args.uid)
            requested = False
            try:
                client.confirm_disarmed()
                # Native HAL_ENABLE_DFU_BOOT branch; the other debug selectors are never used.
                requested = True
                client.link.mav.command_long_send(
                    client.system, client.component, mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
                    0, 42, 24, 71, 99, 0, 0, 0)
            finally:
                client.close()
            try:
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    result = subprocess.run([swd.cube, "-c", "port=USB1", "-r32", "0x1ff1e800", "12"],
                                            capture_output=True, text=True, errors="replace", timeout=15)
                    row["rom_reads"].append(dict(exit_code=result.returncode, stdout=result.stdout,
                                                  stderr=result.stderr))
                    match = re.search(r"0x1FF1E800\s*:\s*([\da-fA-F]{8})\s+([\da-fA-F]{8})\s+([\da-fA-F]{8})",
                                      result.stdout, re.IGNORECASE)
                    if match:
                        assert " ".join(match.groups()).upper() == expected_words, "Different ROM UID"
                        row["rom_uid_verified"] = True
                        break
                    time.sleep(.5)
                assert row.get("rom_uid_verified"), "ROM USB DFU UID was not received"
            finally:
                if requested:
                    row["hardware_nrst"] = swd.hard_reset(under_reset_fallback=True)
                    row["after"] = ready(args.uid)
                    save()
            row["passed"] = True
            save()
            print(json.dumps(dict(phase="rom_dfu_nrst", cycle=index + 1, passed=True)), flush=True)
        doc["passed"] = True
    except Exception as error:
        doc["error"] = repr(error)
        raise
    finally:
        save()


if __name__ == "__main__":
    main()
