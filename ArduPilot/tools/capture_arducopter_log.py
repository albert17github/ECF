"""Record all three sensors with native disarmed logging and preserve the file."""
# AP_FLAKE8_CLEAN
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

from arducopter_client import APClient
from arducopter_ftp import FtpNak, list_directory, read_file_burst

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--unloaded-bench", action="store_true", required=True)
parser.add_argument("--out", required=True, type=Path)
parser.add_argument("--seconds", type=float, default=35)
args = parser.parse_args()
assert not args.out.exists() and 30 <= args.seconds <= 120
doc = dict(at=datetime.now(timezone.utc).isoformat(), passed=False, samples=[])
client = None
log_directory = "/APM/LOGS"


def persist():
    args.out.write_text(json.dumps(doc, indent=2) + "\n")


def ready():
    client.interval(1, 200000)
    return client.wait("SYS_STATUS", lambda m: m.onboard_control_sensors_health & 15 == 15, timeout=35).to_dict()


try:
    client = APClient(args.port, args.uid)
    doc["uid2"] = bytes(client.version.uid2).hex()
    doc["status_before"] = ready()
    doc["minimum_free_mb"] = client.params(["LOG_FILE_MB_FREE"])["LOG_FILE_MB_FREE"]
    assert doc["minimum_free_mb"] == 16, "Expected the board's 16MiB free-space reserve"
    names = ["LOG_DISARMED", "LOG_BITMASK", "INS_LOG_BAT_MASK", "INS_LOG_BAT_OPT",
             "INS_LOG_BAT_CNT", "INS_LOG_BAT_LGIN", "INS_LOG_BAT_LGCT"]
    doc["original_parameters"] = client.params(names)
    assert doc["original_parameters"]["LOG_DISARMED"] == 0
    try:
        before, listing = list_directory(client, log_directory)
    except FtpNak as error:
        assert error.missing()
        before, listing = [], dict(directory_not_created=True)
    doc.update(files_before=before, listing_before=listing)
    settings = dict(INS_LOG_BAT_MASK=1, INS_LOG_BAT_OPT=0, INS_LOG_BAT_CNT=256,
                    INS_LOG_BAT_LGIN=0, INS_LOG_BAT_LGCT=32)
    settings["LOG_BITMASK"] = int(doc["original_parameters"]["LOG_BITMASK"]) | (1 << 7) | (1 << 13) | (1 << 18)
    doc["changes"] = [client.set_param(name, value) for name, value in settings.items()]
    client.reboot()
    client = None
    time.sleep(1)
    client = APClient(args.port, args.uid)
    doc["status_after_settings"] = ready()
    assert client.params(settings) == settings
    doc["logging_on"] = client.set_param("LOG_DISARMED", 1)
    started = time.monotonic()
    next_save = started + 10
    while time.monotonic() - started < args.seconds:
        message = client.receive(.2)
        if message is not None and message.get_type() in ("SYS_STATUS", "HEARTBEAT", "STATUSTEXT"):
            doc["samples"].append(dict(elapsed=time.monotonic() - started, **message.to_dict()))
        if time.monotonic() >= next_save:
            persist()
            print(json.dumps(dict(phase="recording", elapsed=round(time.monotonic() - started, 1))), flush=True)
            next_save += 10
    doc["recording_seconds"] = time.monotonic() - started
    doc["logging_off"] = client.set_param("LOG_DISARMED", 0)
    # Leave the file open while the native IO thread drains its write buffer.
    time.sleep(3)
    doc["stop_drain_seconds"] = 3
    doc["restore"] = [client.set_param(name, value) for name, value in doc["original_parameters"].items()]
    client.reboot()
    client = None
    time.sleep(1)
    client = APClient(args.port, args.uid)
    doc["status_after_stop"] = ready()
    assert client.params(names) == doc["original_parameters"]
    after, listing = list_directory(client, log_directory)
    doc.update(files_after=after, listing_after=listing)
    old_names = {row["name"] for row in before}
    new = [row for row in after if row["name"] not in old_names and not row["directory"]
           and re.fullmatch(r"[0-9]{8}\.BIN", row["name"], flags=re.IGNORECASE) and row["bytes"] > 0]
    assert len(new) == 1, new
    remote = log_directory + "/" + new[0]["name"]
    raw, transfer = read_file_burst(client, remote, maximum_bytes=32 * 1024 * 1024)
    assert len(raw) == new[0]["bytes"]
    local = args.out.with_suffix(".BIN")
    with local.open("xb") as stream:
        stream.write(raw)
    doc.update(remote_file=remote, local_file=str(local), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
               transfer=transfer, remote_log_retained=True, recording_capture_passed=True, integrity_validation="pending")
    doc["passed"] = True
except Exception as error:
    doc["error"] = repr(error)
    if client:
        doc["failure_texts"] = client.texts
        client.close()
        client = None
    if "original_parameters" in doc:
        try:
            client = APClient(args.port, args.uid)
            doc["failure_logging_off"] = client.set_param("LOG_DISARMED", 0)
            time.sleep(3)
            doc["failure_restore"] = [client.set_param(name, value)
                                      for name, value in doc["original_parameters"].items()]
            client.reboot()
            client = None
            doc["failure_settings_restored"] = True
        except Exception as cleanup_error:
            doc["failure_restore_error"] = repr(cleanup_error)
    raise
finally:
    if client:
        client.close()
    persist()
print(json.dumps({key: doc.get(key) for key in ("passed", "bytes", "sha256", "remote_file", "integrity_validation")}))
