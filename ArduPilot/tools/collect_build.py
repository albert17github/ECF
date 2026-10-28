"""Check matched app/bootloader artifacts and generate an addressed factory image."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import zlib

parser = argparse.ArgumentParser()
parser.add_argument("--work", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--arm-bin", required=True, type=Path)
parser.add_argument("--release", required=True, type=Path)
args = parser.parse_args()
release = json.loads(args.release.read_text())
app_root = args.work / "application/ECFH743"
boot_root = args.work / "bootloader/ECFH743"
out = args.output / "firmware"
assert not out.exists()
(out / "debug").mkdir(parents=True)
stem = "ECFH743-ArduCopter-" + release["tag"].removeprefix("Copter-")
app = (app_root / "bin/arducopter.bin").read_bytes()
boot = (boot_root / "bin/AP_Bootloader.bin").read_bytes()
apj_path = app_root / "bin/arducopter.apj"
apj = json.loads(apj_path.read_text())
assert apj["board_id"] == 65001 and apj["git_identity"] == release["commit"][:8]
assert zlib.decompress(base64.b64decode(apj["image"])) == app
assert apj["image_size"] == len(app) and apj["image_maxsize"] == 0x1a0000
assert len(boot) < 0x20000 and len(app) <= 0x1a0000
for raw, low, high in ((boot, 0x08000000, 0x08020000), (app, 0x08020000, 0x081c0000)):
    stack, entry = struct.unpack_from("<II", raw)
    assert any(start <= stack <= end for start, end in ((0x20000000, 0x20020000), (0x24000000, 0x24080000),
                                                      (0x30000000, 0x30048000)))
    assert entry & 1 and low <= entry & ~1 < high
for root, is_boot in ((app_root, False), (boot_root, True)):
    header = (root / "hwdef.h").read_text()
    assert "#define HAL_FLASH_PROTECTION 0" in header
    if is_boot:
        assert "#define FLASH_BOOTLOADER_LOAD_KB 128" in header and "#define FLASH_RESERVE_END_KB 256" in header
    else:
        assert "#define HAL_LOGGER_MIN_MB_FREE 16" in header
bootlog = (args.output / "bootloader.log").read_text()
assert any("Tools/AP_Bootloader/support.cpp" in line and "-fno-tree-loop-distribute-patterns" in line
           for line in bootlog.splitlines() if "runner [" in line)
objdump = str(args.arm_bin / "arm-none-eabi-objdump")
disassembly = subprocess.check_output([objdump, "-d", "--disassemble=strlen", str(boot_root / "bootloader/AP_Bootloader")],
                                    text=True)
assert "<strlen>:" in disassembly and any(op in disassembly for op in ("ldrb", "ldrsb")) and "bx" in disassembly
(args.output / "bootloader-strlen.txt").write_text(disassembly)
entry = subprocess.check_output([objdump, "-d", "--disassemble=_crt0_entry",
                                 str(boot_root / "bootloader/AP_Bootloader")], text=True)
assert "<__entry_hook>" in entry and "<ecf_crt0_continue>" in entry, "Missing native DFU startup call"
hook = subprocess.check_output([objdump, "-d", "--disassemble=__entry_hook",
                                str(boot_root / "bootloader/AP_Bootloader")], text=True)
assert "<__entry_hook>:" in hook and "msr" in hook.lower(), "Missing native ROM handoff"
(args.output / "bootloader-dfu-entry.txt").write_text(entry + "\n" + hook)
for elf, name in ((app_root / "bin/arducopter", stem + ".elf"),
                  (boot_root / "bootloader/AP_Bootloader", "ECFH743-bootloader.elf")):
    sections = subprocess.check_output([objdump, "-h", str(elf)], text=True)
    assert ".debug_info" in sections or ".zdebug_info" in sections, "Expected source-level debug information"
    shutil.copyfile(elf, out / "debug" / name)
shutil.copyfile(apj_path, out / (stem + ".apj"))
factory = bytearray(b"\xff" * 0x200000)
factory[:len(boot)] = boot
factory[0x20000:0x20000 + len(app)] = app
assert factory[0x1c0000:] == b"\xff" * 0x40000


def record(kind, address, data):
    body = bytes([len(data)]) + address.to_bytes(2, "big") + bytes([kind]) + data
    return ":" + (body + bytes([(-sum(body)) & 255])).hex().upper() + "\n"


image = out / (stem + "-factory.hex")
with image.open("w", encoding="ascii", newline="\n") as stream:
    for offset in range(0, len(factory), 65536):
        stream.write(record(4, 0, ((0x08000000 + offset) >> 16).to_bytes(2, "big")))
        for index in range(0, 65536, 16):
            stream.write(record(0, index, factory[offset + index:offset + index + 16]))
    stream.write(record(5, 0, (0x08000000).to_bytes(4, "big")))
    stream.write(record(1, 0, b""))
seen = bytearray()
base = 0
for line in image.read_text().splitlines():
    row = bytes.fromhex(line[1:])
    assert sum(row) & 255 == 0
    if row[3] == 4:
        base = int.from_bytes(row[4:6], "big") << 16
    elif row[3] == 0:
        assert base + int.from_bytes(row[1:3], "big") == 0x08000000 + len(seen)
        seen.extend(row[4:-1])
assert seen == factory
files = {p.relative_to(out).as_posix(): dict(bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
         for p in sorted(out.rglob("*")) if p.is_file()}
manifest = dict(project="ArduCopter", target="ECFH743", tag=release["tag"], commit=release["commit"],
                compiler="ARM GCC 10.2.1", source_manifest_sha256=hashlib.sha256(
                    (args.output / "source-SHA256SUMS.txt").read_bytes()).hexdigest(),
                app_bytes=len(app), bootloader_bytes=len(boot), image=image.name,
                image_sha256=files[image.name]["sha256"], bootloader_address="0x08000000",
                application_address="0x08020000", parameter_region=["0x081c0000", "0x08200000"],
                configuration_cleared_bytes=0x40000, includes_matching_bootloader=True,
                debug_symbols=True, hardware_validation="pending for this exact image", files=files)
(out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
(out / "SHA256SUMS.txt").write_text("".join(item["sha256"] + "  " + name + "\n" for name, item in files.items()))
print(json.dumps({key: manifest[key] for key in ("image", "image_sha256", "app_bytes", "bootloader_bytes")}))
