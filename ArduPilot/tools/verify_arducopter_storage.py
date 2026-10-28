"""UID-bound native MAVFTP SHA write/read and owned-file cleanup checks."""
# AP_FLAKE8_CLEAN
import argparse
import datetime
import json
from pathlib import Path
import sys

from arducopter_client import APClient

try:
    from probe_storage import Evidence, FtpClient, probe
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from probe_storage import Evidence, FtpClient, probe

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--out", required=True, type=Path)
parser.add_argument("--cycles", type=int, default=3, choices=(1, 2, 3))
parser.add_argument("--remote-root", default="/")
args = parser.parse_args()
assert not args.out.exists()
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), cycles=[], passed=False)
client = None
try:
    client = APClient(args.port, args.uid)
    doc["uid2"] = bytes(client.version.uid2).hex()
    for index in range(args.cycles):
        item = dict(index=index + 1)
        doc["cycles"].append(item)
        evidence = Evidence(item)
        ftp = FtpClient(client.link, evidence, target_system=client.system, target_component=client.component)
        assert probe(ftp, args.remote_root, size=65536, chunk_size=239, reset_sessions=False)
        args.out.write_text(json.dumps(doc, indent=2) + "\n")
        print(json.dumps(dict(cycle=index + 1, passed=True, bytes=item["read_bytes"],
                              sha256=item["actual_sha256"], cleanup_complete=item["cleanup_complete"])), flush=True)
    client.confirm_disarmed()
    doc["passed"] = True
except Exception as error:
    doc["error"] = repr(error)
    raise
finally:
    if client:
        client.close()
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
print(json.dumps(dict(passed=doc["passed"], cycles=len(doc["cycles"]))))
