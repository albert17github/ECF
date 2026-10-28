"""UID-guarded MSC file validation, safe eject, NRST, and application return."""
import argparse
import ctypes
import datetime
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--stlink-sn", required=True)
parser.add_argument("--out", required=True, type=Path)
parser.add_argument("--variant", default="BTFL")
parser.add_argument("--write-probe", action="store_true")
parser.add_argument("--hash-existing-files", action="store_true")
parser.add_argument("--baseline", type=Path)
parser.add_argument("--copy-log", type=Path)
pool_actions = parser.add_mutually_exclusive_group()
pool_actions.add_argument("--rebuild-afatfs-pool", action="store_true", help="Back up the small native allocation file and empty it so AFatFS reallocates it after reset")
pool_actions.add_argument("--release-afatfs-pool", action="store_true", help="Back up the native allocation file and retain one cluster for FatFS handoff")
parser.add_argument("--log-extension", default=".bfl")
parser.add_argument("--cube", default="C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe")
args = parser.parse_args()
if args.rebuild_afatfs_pool or args.release_afatfs_pool:
    args.hash_existing_files = True
if args.out.exists() or (args.copy_log and args.copy_log.exists()):
    raise FileExistsError("Evidence or destination already exists")
uid_bytes = bytes.fromhex(args.uid)
assert len(uid_bytes) == 12
uid_words = [int.from_bytes(uid_bytes[i:i + 4], "little") for i in range(0, 12, 4)]
expected_serial = f"{(uid_words[0] + uid_words[2]) & 0xFFFFFFFF:08X}{uid_words[1] >> 16:04X}"
instance = "USB\\VID_0483&PID_572A\\" + expected_serial
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), uid=args.uid, expected_usb_serial=expected_serial, passed=False)

def save():
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def find_volume():
    script = r'''[Console]::OutputEncoding=[Text.Encoding]::UTF8
$ecfDisks=@(Get-CimInstance Win32_DiskDrive | Where-Object { $_.PNPDeviceID -match '__ECF_SERIAL__' })
if ($ecfDisks.Count -eq 1) {
  $ecfLetters=@(foreach ($ecfPartition in (Get-CimAssociatedInstance -InputObject $ecfDisks[0] -ResultClassName Win32_DiskPartition)) {
    Get-CimAssociatedInstance -InputObject $ecfPartition -ResultClassName Win32_LogicalDisk | Select-Object -ExpandProperty DeviceID
  })
  if ($ecfLetters.Count -eq 1) {
    [pscustomobject]@{ drive=$ecfLetters[0]; pnp=$ecfDisks[0].PNPDeviceID; model=$ecfDisks[0].Model; bytes=$ecfDisks[0].Size } | ConvertTo-Json -Compress
  }
}'''.replace("__ECF_SERIAL__", expected_serial)
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, text=True, encoding="utf-8-sig", errors="replace", timeout=12)
        if result.returncode == 0 and result.stdout.strip():
            device = json.loads(result.stdout)
            volume = Path(device["drive"] + "\\")
            assert volume.parent == volume and volume.is_dir()
            return device, volume
        time.sleep(.5)
    raise TimeoutError("UID-matched MSC disk with one mounted volume was not found")

def inventory(volume):
    result = []
    for directory, dirs, files in os.walk(volume):
        dirs[:] = [name for name in dirs if name.casefold() not in ("system volume information", "$recycle.bin")]
        for name in sorted(files):
            path = Path(directory) / name
            result.append(dict(path=path.relative_to(volume).as_posix(), bytes=path.stat().st_size))
    return sorted(result, key=lambda item: item["path"])

def write_probe(volume):
    directory = volume / ("ecf_" + secrets.token_hex(12))
    assert directory.resolve().parent == volume.resolve()
    directory.mkdir()
    file = directory / "probe.bin"
    data = secrets.token_bytes(65536)
    with file.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    actual = file.read_bytes()
    result = dict(bytes=len(actual), expected_sha256=hashlib.sha256(data).hexdigest(), readback_sha256=hashlib.sha256(actual).hexdigest(), owned_path=str(file), cleanup_complete=False)
    assert actual == data
    file.unlink()
    directory.rmdir()
    result.update(passed=True, cleanup_complete=True)
    return result


def content_hashes(volume, entries):
    hashes = []
    for entry in entries:
        # FREESPAC.E is the native allocator's pool of currently free clusters.
        if entry['path'].casefold() == 'freespac.e':
            continue
        path = volume / entry['path']
        assert path.resolve().is_relative_to(volume.resolve())
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        hashes.append(dict(**entry, sha256=digest.hexdigest()))
    return hashes


def handoff_pool(volume, entries):
    """Change only AFatFS allocation bookkeeping after complete, verified backups."""
    pool = volume / 'FREESPAC.E'
    assert pool.is_file() and pool.resolve().parent == volume.resolve()
    old_size = pool.stat().st_size
    assert 0 < old_size <= 512 * 1024 * 1024
    if args.rebuild_afatfs_pool:
        assert old_size <= 1024 * 1024, 'An existing large AFatFS pool does not need rebuilding'
    current_device, current_volume = find_volume()
    assert current_volume.resolve() == volume.resolve() and current_device['pnp'] == doc['device']['pnp']
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    kernel.GetFileAttributesW.restype = ctypes.c_ulong
    kernel.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong]
    sectors, sector_bytes, free_clusters, clusters = (ctypes.c_ulong() for _ in range(4))
    assert kernel.GetDiskFreeSpaceW(str(volume), ctypes.byref(sectors), ctypes.byref(sector_bytes),
                                    ctypes.byref(free_clusters), ctypes.byref(clusters))
    cluster_bytes = sectors.value * sector_bytes.value
    assert 512 <= cluster_bytes <= 65536 and old_size % cluster_bytes == 0
    attrs = kernel.GetFileAttributesW(str(pool))
    assert attrs != 0xFFFFFFFF and attrs & 5 == 5, 'Expected native read-only/system allocation file'
    backup = args.out.parent / (args.out.stem + '-sd-backup')
    assert not backup.exists() and not backup.resolve().is_relative_to(volume.resolve())
    backup.mkdir()
    checked = []
    # Copy all payloads and the full allocation file; retain them outside the disk.
    for entry in entries:
        source = volume / entry['path']; target = backup / entry['path']
        assert source.resolve().is_relative_to(volume.resolve()) and target.resolve().is_relative_to(backup.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        source_hash = hashlib.sha256()
        with source.open('rb') as src, target.open('xb') as dst:
            while block := src.read(1024 * 1024):
                source_hash.update(block); dst.write(block)
            dst.flush(); os.fsync(dst.fileno())
        def sha(path):
            value = hashlib.sha256()
            with path.open('rb') as stream:
                while block := stream.read(1024 * 1024): value.update(block)
            return value.hexdigest()
        original, copied = source_hash.hexdigest(), sha(target)
        assert original == copied and target.stat().st_size == entry['bytes']
        checked.append(dict(**entry, sha256=original))
    (backup / 'backup-manifest.json').write_text(json.dumps(checked, indent=2) + '\n')
    # Recheck disk identity immediately before the sole in-volume mutation.
    current_device, _ = find_volume()
    assert current_device['pnp'] == doc['device']['pnp']
    new_size = 0 if args.rebuild_afatfs_pool else cluster_bytes
    assert new_size < old_size or (args.release_afatfs_pool and new_size == old_size)
    assert kernel.SetFileAttributesW(str(pool), attrs & ~1)
    try:
        with pool.open('r+b') as stream:
            stream.truncate(new_size); stream.flush(); os.fsync(stream.fileno())
    finally:
        assert kernel.SetFileAttributesW(str(pool), attrs)
    assert pool.stat().st_size == new_size and kernel.GetFileAttributesW(str(pool)) == attrs
    after = inventory(volume)
    assert [x for x in after if x['path'].casefold() != 'freespac.e'] == [x for x in entries if x['path'].casefold() != 'freespac.e']
    assert content_hashes(volume, after) == doc['existing_content_sha256_before']
    return dict(operation='rebuild' if args.rebuild_afatfs_pool else 'release', before_bytes=old_size,
                after_bytes=new_size, cluster_bytes=cluster_bytes, attributes=attrs,
                backup=str(backup), backed_up_files=len(checked), all_backups_verified=True,
                other_files_unchanged=True, native_reallocation_after_reset_required=args.rebuild_afatfs_pool)

def eject_once():
    cfg = ctypes.WinDLL("cfgmgr32", use_last_error=True)
    cfg.CM_Locate_DevNodeW.argtypes = [ctypes.POINTER(ctypes.c_ulong), ctypes.c_wchar_p, ctypes.c_ulong]
    cfg.CM_Request_Device_EjectW.argtypes = [ctypes.c_ulong, ctypes.POINTER(ctypes.c_int), ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
    node = ctypes.c_ulong()
    veto = ctypes.c_int()
    reason = ctypes.create_unicode_buffer(512)
    located = cfg.CM_Locate_DevNodeW(ctypes.byref(node), instance, 0)
    result = cfg.CM_Request_Device_EjectW(node.value, ctypes.byref(veto), reason, len(reason), 0) if located == 0 else None
    return dict(instance=instance, locate_result=located, eject_result=result, veto_type=veto.value, veto_name=reason.value, ejected=result == 0)


def eject():
    attempts = []
    deadline = time.monotonic() + 20
    while True:
        result = eject_once()
        attempts.append(result)
        if result['ejected'] or result['veto_type'] != 6 or time.monotonic() >= deadline:
            return dict(**result, attempts=attempts)
        # Windows may briefly retain a volume handle after a new disk is mounted.
        # Retry the same safe-eject request; never force a dismount or reset.
        time.sleep(.5)

entered = False
try:
    client = connect(args.port, args.uid)
    try:
        assert client.request(2).decode() == args.variant
        doc["identity_before"] = {str(command): client.request(command).hex() for command in (1, 2, 3, 5, 160)}
        client.enter_cli()
        doc["msc_command"] = client.cli("msc")
        entered = True
    finally:
        client.close()
    save()
    device, volume = find_volume()
    doc.update(device=device, volume=str(volume), inventory=inventory(volume))
    if args.hash_existing_files:
        doc['existing_content_sha256_before'] = content_hashes(volume, doc['inventory'])
        doc['content_hash_scope_excludes'] = ['FREESPAC.E native free-cluster allocation pool', 'Windows system directories']
    if args.rebuild_afatfs_pool or args.release_afatfs_pool:
        doc['pool_handoff'] = handoff_pool(volume, doc['inventory'])
        save()
    if args.write_probe:
        doc["file_probe"] = write_probe(volume)
        doc["inventory_after_probe"] = inventory(volume)
        expected_inventory = [dict(entry) for entry in doc["inventory"]]
        if doc.get('pool_handoff'):
            for entry in expected_inventory:
                if entry['path'].casefold() == 'freespac.e':
                    entry['bytes'] = doc['pool_handoff']['after_bytes']
        assert expected_inventory == doc["inventory_after_probe"], "Existing file inventory changed"
    if args.hash_existing_files:
        doc['existing_content_sha256_after'] = content_hashes(volume, doc['inventory'])
        assert doc['existing_content_sha256_before'] == doc['existing_content_sha256_after'], 'Existing file content changed'
        doc['existing_content_sha256_unchanged'] = True
    if args.baseline:
        assert args.copy_log
        before = json.loads(args.baseline.read_text(encoding="utf-8"))
        assert before["uid"] == args.uid and before["passed"]
        names = {item["path"] for item in before["inventory"]}
        new = [item for item in doc["inventory"] if item["path"] not in names and item["path"].lower().endswith(args.log_extension.lower())]
        assert len(new) == 1, new
        remote = volume / new[0]["path"]
        assert remote.resolve().is_relative_to(volume.resolve())
        data = remote.read_bytes()
        assert len(data) == new[0]["bytes"] and len(data) > 512
        with args.copy_log.open("xb") as handle:
            handle.write(data)
        assert args.copy_log.read_bytes() == data
        doc["download"] = dict(remote=new[0]["path"], local=str(args.copy_log), bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), existing_logs_preserved=True)
    doc["file_operations_completed"] = True
except BaseException as exc:
    doc["error"] = repr(exc)
    raise
finally:
    if entered:
        try:
            doc["safe_eject"] = eject()
            assert doc["safe_eject"]["ejected"], doc["safe_eject"]
            command = [args.cube, "-c", "port=SWD", f"sn={args.stlink_sn}", "mode=UR", "reset=HWrst", "freq=100", "-r32", "0x1FF1E800", "12", "-hardRst"]
            result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=20)
            assert result.returncode == 0
            assert " ".join(f"{word:08X}" for word in uid_words) in result.stdout.upper()
            doc["nrst"] = dict(command=command, exit_code=result.returncode, stdout=result.stdout)
            client = connect(args.port, args.uid)
            try:
                doc["identity_after"] = {str(command): client.request(command).hex() for command in (1, 2, 3, 160, 101, 102)}
                assert client.request(2).decode() == args.variant
            finally:
                client.close()
            doc["application_returned"] = True
            doc["passed"] = bool(doc.get("file_operations_completed"))
        except BaseException as exc:
            doc["return_error"] = repr(exc)
    save()
print(json.dumps({key: value for key, value in doc.items() if key in ("passed", "device", "file_probe", "download", "application_returned", "return_error")}))
if not doc["passed"]:
    raise SystemExit(1)
