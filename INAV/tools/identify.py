"""Read and check the application identity before configuration changes."""
import argparse
import json
from pathlib import Path
from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--variant", required=True)
parser.add_argument("--version-hex", required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
assert not args.out.exists()
doc = dict(passed=False)
client = connect(args.port, args.uid)
try:
    doc.update(uid=client.uid, variant=client.request(2).decode(), version_hex=client.request(3).hex(), board_info_hex=client.request(4).hex(), build_info_hex=client.request(5).hex())
    assert doc["variant"] == args.variant and doc["version_hex"] == args.version_hex
    assert b"ECFH743" in bytes.fromhex(doc["board_info_hex"])
    doc["passed"] = True
finally:
    client.close()
    args.out.write_text(json.dumps(doc, indent=2) + "\n")
print(json.dumps(doc))
