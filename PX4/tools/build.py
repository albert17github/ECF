"""Build a checked PX4 target snapshot in a fresh Linux scratch directory."""
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

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--arm-bin',default=os.environ.get('ECF_ARM_GCC_BIN'))
parser.add_argument('--jobs',type=int,default=8)
parser.add_argument('--allow-modified-source',action='store_true')
args=parser.parse_args()
assert sys.platform.startswith('linux'),'Use Linux or WSL'
assert 1<=args.jobs<=64
release=json.loads((root/'release.json').read_text())
layout=json.loads((root/release['source_layout']).read_text())
source=root/'source'
manifest_path=root/release['source_manifest']
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
expected={name:sha for sha,name in (line.split('  ',1) for line in manifest_path.read_text().splitlines())}
excluded={'.git','__pycache__','obj','build','.cache'}
actual={}
for path in source.rglob('*'):
    if not (path.is_file() or path.is_symlink()):continue
    name=path.relative_to(source).as_posix()
    if any(part in excluded for part in path.relative_to(source).parts):continue
    data=os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
    actual[name]=hashlib.sha256(data).hexdigest()
if not args.allow_modified_source:
    assert digest(manifest_path)==release['source_manifest_sha256']
    assert actual==expected,'Source snapshot differs'
compiler_dir=Path(args.arm_bin).resolve() if args.arm_bin else Path(shutil.which('arm-none-eabi-gcc')).parent
compiler=compiler_dir/'arm-none-eabi-gcc'
version=subprocess.check_output([str(compiler),'-dumpfullversion'],text=True).strip()
assert version=='13.2.1',version
assert shutil.which('cmake') and shutil.which('ninja') and shutil.which('make') and shutil.which('gcc')
stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
output=root/'.build'/stamp;output.mkdir(parents=True)
work=Path(tempfile.mkdtemp(prefix='ecfh743-px4-'))
assert not re.search(r'\s',str(work))
copied=work/'source';copied.mkdir()
executable=set(layout['executable_files'])
links=layout['symlinks']
for name in sorted(actual):
    relative=Path(name)
    assert not relative.is_absolute() and '..' not in relative.parts
    original=source/name;destination=copied/name
    destination.parent.mkdir(parents=True,exist_ok=True)
    if name in links:
        raw=os.readlink(original).encode() if original.is_symlink() else original.read_bytes()
        assert raw==links[name].encode(),name
        continue
    assert not original.is_symlink(),'New links require source-layout metadata'
    shutil.copyfile(original,destination)
    destination.chmod(0o755 if name in executable else 0o644)
for name,target in links.items():
    assert name in actual and not Path(target).is_absolute()
    destination=copied/name
    assert (destination.parent/target).resolve().is_relative_to(copied)
    destination.symlink_to(target)
source_manifest=''.join(sha+'  '+name+'\n' for name,sha in sorted(actual.items()))
(output/'source-SHA256SUMS.txt').write_text(source_manifest)
info=dict(started_at=datetime.now(timezone.utc).isoformat(),passed=False,phase='configure',
    source_files=len(actual),source_manifest_sha256=hashlib.sha256(source_manifest.encode()).hexdigest(),
    compiler_version=version,compiler_sha256=digest(compiler),source_date_epoch=release['source_date_epoch'],
    work_directory=str(work),clean_source_copy=True,restored_source_links=len(links),
    modified_source_allowed=args.allow_modified_source)
def save():(output/'build-result.json').write_text(json.dumps(info,indent=2)+'\n')
(root/'.build/LATEST.txt').write_text(stamp+'\n')
build=work/'build'
maps=f'-ffile-prefix-map={copied}=/ecf/source -fdebug-prefix-map={copied}=/ecf/source'
maps+=f' -ffile-prefix-map={build}=/ecf/build -fdebug-prefix-map={build}=/ecf/build'
env=dict(os.environ,PATH=str(compiler_dir)+':'+str(Path(sys.executable).parent)+':'+os.environ['PATH'],
    SOURCE_DATE_EPOCH=str(release['source_date_epoch']),PYTHONDONTWRITEBYTECODE='1',CCACHE_DISABLE='1',
    GIT_SUBMODULES_ARE_EVIL='1')
save()
try:
    configure=['cmake','-S',str(copied),'-B',str(build),'-G','Ninja','-DCONFIG='+release['px4_target'],
        '-DECF_GITLESS_SOURCE=ON','-DCMAKE_BUILD_TYPE=MinSizeRel','-DPYTHON_EXECUTABLE='+sys.executable,
        '-DCCACHE=OFF','-DCMAKE_C_FLAGS='+maps,'-DCMAKE_CXX_FLAGS='+maps,'-DCMAKE_ASM_FLAGS='+maps,
        '-DECF_DEBUG_PREFIX_FLAGS='+maps]
    with (output/'configure.log').open('wb') as log:
        subprocess.run(configure,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
    info['phase']='compile';save();print(json.dumps(dict(phase='compile',output=str(output))),flush=True)
    with (output/'build.log').open('wb') as log:
        subprocess.run(['cmake','--build',str(build),'--','-j'+str(args.jobs)],env=env,stdout=log,
            stderr=subprocess.STDOUT,check=True,timeout=1800)
    native_config=(copied/'platforms/nuttx/NuttX/nuttx/.config').read_text()
    for setting in ['CONFIG_NSH_ROMFSETC=y','CONFIG_NSH_CROMFSETC=y','CONFIG_FS_CROMFS=y',
                    'CONFIG_FS_ROMFS=y','CONFIG_NO_SERIAL_CONSOLE=y','CONFIG_NSH_LINELEN=128',
                    'CONFIG_STM32H7_SERIAL_DISABLE_REORDERING=y']:
        assert setting in native_config.splitlines(),'Missing native startup setting: '+setting
    subprocess.run([sys.executable,str(root/'tools/collect_build.py'),'--build',str(build),'--output',str(output),
        '--arm-bin',str(compiler_dir),'--release',str(root/'release.json')],env=env,check=True,timeout=120)
    elf=(output/'firmware/debug/ECFH743-PX4-1.17.0.elf').read_bytes()
    assert b'/tmp/ecfh743-px4-' not in elf,'Private scratch paths remain in application or NuttX app debug information'
    info.update(passed=True,phase='complete')
finally:
    info['finished_at']=datetime.now(timezone.utc).isoformat();save()
print(json.dumps(info),flush=True)
