"""Write or verify the complete source snapshot used by a release build."""
import argparse
import hashlib
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True, type=Path)
parser.add_argument("--manifest", required=True, type=Path)
parser.add_argument("--write", action="store_true")
args = parser.parse_args()
root = args.source.resolve()
excluded = {".git", "obj", ".cache", "__pycache__"}

def files(directory):
    for path in sorted(directory.iterdir()):
        if path.name in excluded:
            continue
        if path.is_dir():
            yield from files(path)
        elif path.is_file():
            yield path

def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()

actual = {path.relative_to(root).as_posix(): digest(path) for path in files(root)}
if args.write:
    args.manifest.write_text("".join(f"{sha}  {name}\n" for name, sha in sorted(actual.items())), encoding="utf-8")
    print(f"Recorded {len(actual)} source files")
else:
    expected = {}
    for line in args.manifest.read_text(encoding="utf-8-sig").splitlines():
        sha, name = line.split("  ", 1)
        expected[name] = sha
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    changed = sorted(name for name in actual.keys() & expected.keys() if actual[name] != expected[name])
    if missing or extra or changed:
        raise SystemExit(f"Source mismatch: missing={missing[:12]}, extra={extra[:12]}, changed={changed[:12]}")
    print(f"Verified {len(actual)} source files")
