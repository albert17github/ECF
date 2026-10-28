import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import struct
import sys

p=argparse.ArgumentParser()
p.add_argument('--root',type=Path,required=True)
p.add_argument('--port',required=True)
p.add_argument('--uid',required=True)
p.add_argument('--out',type=Path,required=True)
a=p.parse_args()
assert not a.out.exists()
sys.path.insert(0,str(a.root/'tools'))
from msp_client import connect
r=json.loads((a.root/'release.json').read_text(encoding='utf-8'))
is_bf=r['project']=='Betaflight'
commands=['version','status','resource','serial','feature','get serialrx_provider','get mag','get dronecan']
commands+=['get vbat_scale','get ibata_scale','get ibata_offset','get battery_meter','get current_meter'] if is_bf else ['get vbat_scale','get current_meter_scale','get current_meter_offset','get vbat_meter_type','get current_meter_type','get receiver_type','get ecf_io_mode']
doc={'at':datetime.now(timezone.utc).isoformat(),'project':r['project'],'revision':r['ecf_revision'],'image_sha256':r['firmware']['sha256'],'passed':False}
c=connect(a.port,a.uid)
try:
    doc['variant']=c.request(2).decode()
    assert doc['variant']==('BTFL' if is_bf else 'INAV')
    frame=struct.Struct('<BIBBBB')
    raw=c.request(0x1009)
    if is_bf:
        assert len(raw)==1+raw[0]*frame.size
        raw=raw[1:]
    rows=list(frame.iter_unpack(raw))
    doc['native_serial_rows']=rows
    masks={row[0]:row[1] for row in rows}
    expected={20:1,51:64,53:0,54:0,55:1,56:2,57:1024,58:0} if is_bf else {20:1,0:64,2:0,3:0,4:1,5:2,6:262144,7:0}
    # BF hides the unassigned shared UART8 row while ADC owns these pads.
    for number,value in expected.items():
        if is_bf and number==58 and number not in masks:
            continue
        assert masks.get(number)==value,(number,masks,expected)
    c.enter_cli()
    doc['cli']={command:c.cli(command) for command in commands}
    for command,response in doc['cli'].items():
        assert not any(error in response for error in ('###ERROR','Invalid name','Unknown command','Parse error')),(command,response)
    all_text='\n'.join(doc['cli'].values())
    checks={'vbat_scale':110 if is_bf else 1100,'ibata_scale' if is_bf else 'current_meter_scale':200,'ibata_offset' if is_bf else 'current_meter_offset':0,'serialrx_provider':'CRSF','dronecan_enabled':'ON'}
    if not is_bf:
        checks.update(receiver_type='SERIAL',ecf_io_mode=1,mag_hardware='AUTO',align_mag='DEFAULT')
    for key,value in checks.items():
        assert re.search(r'\b'+re.escape(key)+r'\s*=\s*'+re.escape(str(value))+r'(?:\s|$)',all_text),(key,value)
    resources=doc['cli']['resource']
    if is_bf:
        for expected_resource in ['resource SERIAL_TX 3 D08','resource SERIAL_RX 3 D09','resource ADC_BATT 1 C05','resource ADC_CURR 1 C04']:
            assert expected_resource in resources,expected_resource
        assert 'resource I2C_SCL 4 D12' not in resources
        assert 'resource I2C_SDA 4 D13' not in resources
    assert 'MMC5983' in doc['cli']['status'],'Onboard fallback compass absent on unloaded bench'
    c.cli('exit')
    doc['passed']=True
finally:
    c.close()
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(dict(project=r['project'],revision=r['ecf_revision'],passed=doc['passed'])),flush=True)
