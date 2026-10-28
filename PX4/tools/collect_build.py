"""Collect the standalone H743 image and its source-level debug artifact."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess

parser=argparse.ArgumentParser()
parser.add_argument('--build',required=True,type=Path)
parser.add_argument('--output',required=True,type=Path)
parser.add_argument('--arm-bin',required=True,type=Path)
parser.add_argument('--release',required=True,type=Path)
args=parser.parse_args()
release=json.loads(args.release.read_text());target=release['px4_target']
binary=(args.build/(target+'.bin')).read_bytes();elf=args.build/(target+'.elf')
assert 0<len(binary)<=0x200000
stack,entry=struct.unpack_from('<II',binary)
assert any(a<=stack<=b for a,b in [(0x20000000,0x20020000),(0x24000000,0x24080000),(0x30000000,0x30048000)])
assert entry&1 and 0x08000000<=entry&~1<0x08000000+len(binary)
sections=subprocess.check_output([str(args.arm_bin/'arm-none-eabi-objdump'),'-h',str(elf)],text=True)
assert '.debug_info' in sections or '.zdebug_info' in sections
symbols=subprocess.check_output([str(args.arm_bin/'arm-none-eabi-nm'),'-C',str(elf)],text=True)
for name in ['ecf_interfaces_main','ina238_main','icm45686_main','board_adc_main','uavcan_main']:
    assert name in symbols,name
for name in [b'ECF_IO_MODE',b'ECF_INA_ADDR',b'INA238_CURRENT',b'INA238_SHUNT']:
    assert name in binary,name
firmware=args.output/'firmware';assert not firmware.exists()
(firmware/'debug').mkdir(parents=True)
stem='ECFH743-PX4-'+release['tag'].lstrip('v')
image=firmware/(stem+'-factory.hex')
raw=binary+b'\xff'*(0x200000-len(binary))
def record(kind,address,data):
    body=bytes([len(data)])+address.to_bytes(2,'big')+bytes([kind])+data
    return ':'+(body+bytes([-sum(body)&255])).hex().upper()+'\n'
with image.open('w',encoding='ascii',newline='\n') as stream:
    for base in range(0,len(raw),65536):
        stream.write(record(4,0,((0x08000000+base)>>16).to_bytes(2,'big')))
        for offset in range(0,65536,16):stream.write(record(0,offset,raw[base+offset:base+offset+16]))
    stream.write(record(5,0,entry.to_bytes(4,'big')));stream.write(record(1,0,b''))
shutil.copyfile(elf,firmware/('debug/'+stem+'.elf'))
files={p.relative_to(firmware).as_posix():dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
       for p in sorted(firmware.rglob('*')) if p.is_file()}
manifest=dict(project='PX4',target='ECFH743',tag=release['tag'],commit=release['commit'],
    source_manifest_sha256=hashlib.sha256((args.output/'source-SHA256SUMS.txt').read_bytes()).hexdigest(),
    image=image.name,image_sha256=files[image.name]['sha256'],app_bytes=len(binary),load_address='0x08000000',
    includes_bootloader=False,parameters_location='SD FAT /ecf-px4/parameters.bson',parameters_cleared_by_flash=False,
    debug_symbols=True,hardware_validation='pending',files=files)
(firmware/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(firmware/'SHA256SUMS.txt').write_text(''.join(v['sha256']+'  '+n+'\n' for n,v in files.items()))
print(json.dumps(dict(image=image.name,image_sha256=manifest['image_sha256'],app_bytes=len(binary))),flush=True)
