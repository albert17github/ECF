"""Read the ECF factory wiring profile through the native ArduCopter interface."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from arducopter_client import APClient
from arducopter_ftp import read_file

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--uid',required=True)
p.add_argument('--port',default='auto')
p.add_argument('--out',type=Path,required=True)
a=p.parse_args()
assert not a.out.exists()
expected={'BRD_ALT_CONFIG':1,'BATT_MONITOR':4,'BATT_VOLT_PIN':8,'BATT_CURR_PIN':4,
 'BATT_VOLT_MULT':11,'BATT_AMP_PERVLT':50,'BATT_AMP_OFFSET':0,'RC_PROTOCOLS':512,'RC_OPTIONS':8192,
 'GPS1_TYPE':1,'GPS2_TYPE':1,'CAN_P1_DRIVER':1,'CAN_P2_DRIVER':2,
 'CAN_P1_BITRATE':1000000,'CAN_P2_BITRATE':1000000,'CAN_D1_PROTOCOL':1,'CAN_D2_PROTOCOL':1}
expected.update({f'SERIAL{i}_PROTOCOL':v for i,v in {1:23,3:-1,4:5,5:2,6:5,7:16,8:-1}.items()})
for i in (4,5,6,7):
 expected[f'SERIAL{i}_BAUD']=115
root=Path(__file__).resolve().parents[1]
release=json.loads((root/'release.json').read_text())
doc={'at':datetime.now(timezone.utc).isoformat(),'image_sha256':release['firmware']['sha256'],'passed':False}
c=APClient(a.port,a.uid)
try:
 doc['parameters']=c.params(expected)
 for key,value in expected.items():
  assert abs(doc['parameters'][key]-value)<1e-5,(key,value,doc['parameters'][key])
 raw,transfer=read_file(c,'@SYS/uarts.txt',maximum_bytes=65536)
 doc['native_uarts']=raw.decode()
 doc['native_uarts_transfer']=transfer
 for i in (1,3,4,5,6,7,8):
  assert f'SERIAL{i} UART{i}' in doc['native_uarts'],i
 doc['passed']=True
finally:
 c.close()
 a.out.parent.mkdir(parents=True,exist_ok=True)
 a.out.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'factory_defaults':doc['passed']}),flush=True)
