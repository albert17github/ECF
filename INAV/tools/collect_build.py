"""Collect an INAV H743 build and prove the addressed factory-image geometry."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess

parser=argparse.ArgumentParser()
parser.add_argument('--build',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--arm-bin',type=Path,required=True)
parser.add_argument('--release',type=Path,required=True)
args=parser.parse_args()
release=json.loads(args.release.read_text(encoding='utf-8-sig'))
assert release['project']=='INAV' and release['target']=='ECFH743' and release['tag']=='9.1.0'
args.output.mkdir(parents=True,exist_ok=True)
base=f"{release['target']}-{release['project']}-{release['tag']}"
elf=args.output/(base+'.elf')
image=args.output/(base+'-factory.hex')
assert not elf.exists() and not image.exists()
shutil.copyfile(args.build/'bin/ECFH743.elf',elf)
maps=list((args.build/'bin').glob('ECFH743*.map'))
assert len(maps)==1
shutil.copyfile(maps[0],args.output/(base+'.map'))
subprocess.run([str(args.arm_bin/'arm-none-eabi-objcopy.exe'),'-O','ihex','--gap-fill','0xff','--set-start','0x08000000',str(elf),str(image)],check=True)
addresses={}
base_address=0
for line in image.read_text().splitlines():
    data=bytes.fromhex(line[1:])
    assert len(data)==data[0]+5 and sum(data)&255==0
    if data[3]==4:
        base_address=int.from_bytes(data[4:6],'big')<<16
    elif data[3]==0:
        offset=int.from_bytes(data[1:3],'big')+base_address
        for index,value in enumerate(data[4:4+data[0]]):
            address=offset+index
            assert 0x08000000<=address<0x08200000
            assert address not in addresses or addresses[address]==value
            addresses[address]=value
    elif data[3]==5:
        assert int.from_bytes(data[4:8],'big')==0x08000000
    else:
        assert data[3] in (1,3,5)
assert all(addresses.get(address)==255 for address in range(0x08020000,0x08040000))
stack,entry=struct.unpack('<II',bytes(addresses[0x08000000+i] for i in range(8)))
assert 0x20000000<stack<=0x20020000 and entry&1 and entry&~1 in addresses
compiler=args.arm_bin/'arm-none-eabi-gcc.exe'
metadata=dict(project='INAV',target='ECFH743',upstream_tag=release['tag'],upstream_commit=release['commit'],
              can_backport=release['can_backport'],source_date_epoch=release['source_date_epoch'],
              source_manifest_sha256=release.get('source_manifest_sha256'),
              compiler=subprocess.check_output([str(compiler),'--version'],text=True).splitlines()[0],
              compiler_sha256=hashlib.sha256(compiler.read_bytes()).hexdigest(),
              image=image.name,image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
              elf=elf.name,elf_sha256=hashlib.sha256(elf.read_bytes()).hexdigest(),
              load_address='0x08000000',configuration_range=['0x08020000','0x08040000'],configuration_cleared_bytes=131072,
              addressed_bytes=len(addresses),stack=hex(stack),reset_entry=hex(entry),
              built_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),build='passed',hardware_validation='pending')
(args.output/'manifest.json').write_text(json.dumps(metadata,indent=2)+'\n')
checksums=''.join(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n'
                  for path in sorted(args.output.iterdir()) if path.is_file() and path.name!='SHA256SUMS.txt')
(args.output/'SHA256SUMS.txt').write_text(checksums)
print(json.dumps({key:metadata[key] for key in ('build','image','image_sha256','addressed_bytes')}))
