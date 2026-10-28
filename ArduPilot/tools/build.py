"""Build the complete gitless ECFH743 source snapshot with the pinned compiler."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

project = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--arm-bin", default=os.environ.get("ECF_ARM_GCC_BIN"))
parser.add_argument("--jobs", type=int, default=8)
parser.add_argument("--build-dir", type=Path)
parser.add_argument("--allow-modified-source", action="store_true")
args = parser.parse_args()
assert sys.platform.startswith("linux"), "Use Linux or WSL for this native Waf target"
assert 1 <= args.jobs <= 64
release = json.loads((project / "release.json").read_text())
source = project / "source"
arm_bin = Path(args.arm_bin).resolve() if args.arm_bin else Path(shutil.which("arm-none-eabi-gcc")).parent
compiler = arm_bin / "arm-none-eabi-gcc"
version = subprocess.check_output([str(compiler), "-dumpfullversion"], text=True).strip()
assert version == "10.2.1", "Expected ARM GCC 10.2.1 (10-2020-q4-major), got " + version
assert shutil.which("make") and shutil.which("gcc"), "GNU Make and the native host compiler are required"


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def ignored(directory, names):
    return [name for name in names if name in (".git", "__pycache__") or name.endswith(".pyc")
            or name.startswith((".lock-waf", ".waf-"))]


source_files = sorted(p for p in source.rglob("*") if p.is_file() and
                      not any(part in (".git", "__pycache__") for part in p.relative_to(source).parts) and
                      not p.name.endswith(".pyc") and not p.name.startswith((".lock-waf", ".waf-")))
source_digests = {p.relative_to(source).as_posix(): sha256(p) for p in source_files}
source_manifest = "".join(f"{digest}  {name}\n" for name, digest in source_digests.items())
if not args.allow_modified_source:
    expected = project / release["source_manifest"]
    assert sha256(expected) == release["source_manifest_sha256"], "Release manifest digest differs"
    assert expected.read_text(encoding="utf-8-sig") == source_manifest, "Source differs from the release snapshot"

stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
output = project / ".build" / stamp
assert not output.exists()
output.mkdir(parents=True)
if args.build_dir:
    work = args.build_dir.resolve()
    assert not work.exists(), "Choose a new build directory"
    work.mkdir(parents=True)
else:
    work = Path(tempfile.mkdtemp(prefix="ecfh743-"))
assert not re.search(r"\s", str(work)), "The native ChibiOS Make work path must not contain whitespace"
(output / "source-SHA256SUMS.txt").write_text(source_manifest)
info = dict(started_at=datetime.now(timezone.utc).isoformat(), phase="copy", passed=False,
            source_files=len(source_files), source_manifest_sha256=hashlib.sha256(source_manifest.encode()).hexdigest(),
            compiler_version=version, compiler_sha256=sha256(compiler), work_directory=str(work),
            output_directory=str(output), upstream_commit=release["commit"], source_date_epoch=release["source_date_epoch"],
            debug_symbols=True, debug_build=False, modified_source_allowed=args.allow_modified_source)


def save():
    (output / "build-result.json").write_text(json.dumps(info, indent=2) + "\n")


save()
(project / ".build" / "LATEST.txt").write_text(stamp + "\n")
try:
    shutil.copytree(source, work / "source", ignore=ignored)
    copied = work / "source"
    copied_names = {p.relative_to(copied).as_posix() for p in copied.rglob("*") if p.is_file()}
    assert copied_names == set(source_digests), "The copied source inventory differs"
    for relative, expected_digest in source_digests.items():
        assert sha256(copied / relative) == expected_digest, relative
    env = dict(os.environ, PATH=str(arm_bin) + ":" + str(Path(sys.executable).parent) + ":" + os.environ["PATH"],
               PYTHONDONTWRITEBYTECODE="1", SOURCE_DATE_EPOCH=str(release["source_date_epoch"]), WAFNO_COLOR="1")
    maps = f"-ffile-prefix-map={work}=/ecf -fdebug-prefix-map={work}=/ecf"
    maps += f" -ffile-prefix-map={arm_bin.parent}=/ecf/toolchain -fdebug-prefix-map={arm_bin.parent}=/ecf/toolchain"
    env.update(CFLAGS=maps, CXXFLAGS=maps, ASFLAGS=maps)
    # The ChibiOS Make rules replace CFLAGS; pass the same maps through
    # their documented USE_OPT override, preserving the normal options.
    env["USE_OPT"] = "-fomit-frame-pointer -falign-functions=16 " + maps
    common = ["--board", "ECFH743", "--no-submodule-update", "--debug-symbols", "--assert-cc-version", "g++-10.2.1"]
    phases = [("configure", ["configure", *common, "--out", str(work / "application")]),
              ("copter", ["copter", f"-j{args.jobs}", "-v"]),
              ("configure-bootloader", ["configure", *common, "--bootloader", "--out", str(work / "bootloader")]),
              ("bootloader", ["bootloader", f"-j{args.jobs}", "-v"])]
    for name, arguments in phases:
        info["phase"] = name
        save()
        print(json.dumps(dict(phase=name, output=str(output))), flush=True)
        with (output / (name + ".log")).open("wb") as log:
            subprocess.run([sys.executable, "./waf", *arguments], cwd=copied, env=env, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=1800)
    assert re.search(r"Debug build\s*:\s*disabled", (output / "configure.log").read_text())
    subprocess.run([sys.executable, str(project / "tools/collect_build.py"), "--work", str(work),
                    "--output", str(output), "--arm-bin", str(arm_bin), "--release", str(project / "release.json")],
                   env=env, check=True, timeout=120)
    info.update(passed=True, phase="complete", firmware_directory=str(output / "firmware"))
finally:
    info["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
print(json.dumps(info), flush=True)
