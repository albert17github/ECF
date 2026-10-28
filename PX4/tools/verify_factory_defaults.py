"""Read the ECF factory wiring profile and native driver state without arming."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
from px4_client import PX4Client

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--uid',required=True)
p.add_argument('--port',default='auto')
p.add_argument('--out',type=Path,required=True)
a=p.parse_args()
assert not a.out.exists()
expected={'ECF_IO_MODE':1,'SENS_EN_INA238':0,'BAT1_SOURCE':0,
 'BAT1_V_CHANNEL':8,'BAT1_I_CHANNEL':4,'BAT1_V_DIV':11,'BAT1_A_PER_V':50,'BAT_V_OFFS_CURR':0,
 'MAV_0_CONFIG':104,'MAV_0_MODE':0,'SER_TEL4_BAUD':115200,
 'GPS_1_CONFIG':201,'GPS_2_CONFIG':103,'RC_PORT_CONFIG':101,'RC_INPUT_PROTO':6,
 'DSHOT_TEL_CFG':202,'UAVCAN_ENABLE':2,'UAVCAN_NODE_ID':10,'UAVCAN_BITRATE':1000000,'SENS_EXT_I2C_PRB':1}
root=Path(__file__).resolve().parents[1]
release=json.loads((root/'release.json').read_text())
doc={'at':datetime.now(timezone.utc).isoformat(),'image_sha256':release['firmware']['sha256'],'passed':False}
c=PX4Client(a.port,a.uid)
try:
 doc['parameters']=c.params(expected)
 for key,value in expected.items():
  assert abs(doc['parameters'][key]-value)<1e-5,(key,value,doc['parameters'][key])
 doc['native']={command:c.shell(command) for command in ['ecf_interfaces status','rc_input status','gps status','dshot status','uavcan status','mavlink status']}
 assert 'ECF_IO_V1 MODE=1' in doc['native']['ecf_interfaces status']
 assert '/dev/ttyS4' in doc['native']['mavlink status']
 doc['passed']=True
finally:
 c.close()
 a.out.parent.mkdir(parents=True,exist_ok=True)
 a.out.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'factory_defaults':doc['passed']}),flush=True)
