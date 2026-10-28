"""Execute explicit native CLI commands and retain raw responses."""
import argparse
import datetime
import json
from pathlib import Path

from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--out", required=True, type=Path)
parser.add_argument("--cmd", action="append", required=True)
args = parser.parse_args()
if args.out.exists():
    raise FileExistsError(args.out)
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), commands=[], passed=False)
client = None
try:
    client = connect(args.port, args.uid)
    doc["uid"] = client.uid
    doc["entry"] = client.enter_cli()
    for command in args.cmd:
        print(command, flush=True)
        reply = client.cli(command)
        doc["commands"].append(dict(command=command, response=reply))
        args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if "###ERROR" in reply or "Invalid name" in reply or "Unknown command" in reply:
            raise RuntimeError(f"CLI rejected {command}: {reply}")
    if args.cmd[-1].split()[0] in ("save", "exit", "defaults"):
        doc["msp_packets"] = client.packets
        client.close()
        client = connect(args.port, args.uid)
        doc["application_after_restart"] = {str(cmd): client.request(cmd).hex() for cmd in (1, 2, 3, 160, 101)}
    doc["passed"] = True
finally:
    if client:
        doc.setdefault("msp_packets", []).extend(client.packets)
        client.close()
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps(dict(passed=doc["passed"], commands=[row["command"] for row in doc["commands"]])))
