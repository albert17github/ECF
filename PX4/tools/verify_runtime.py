"""Observe native PX4 sensor streams and factory settings while disarmed."""
import argparse
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import time

from px4_client import PX4Client

p=argparse.ArgumentParser(description=__doc__);p.add_argument('--uid',required=True);p.add_argument('--port',default='auto')
p.add_argument('--seconds',type=float,default=120);p.add_argument('--out',type=Path,required=True)
a=p.parse_args();assert a.seconds>=20 and not a.out.exists()
doc=dict(at=datetime.now(timezone.utc).isoformat(),passed=False,samples=[],flight_tested=False)
c=PX4Client(a.port,a.uid)
try:
    doc['version']=c.version.to_dict();doc['uid2']=bytes(c.version.uid2).hex()
    expected=dict(SYS_AUTOSTART=4001,ECF_IO_MODE=1,ECF_INA_ADDR=69,ECF_IMU2_EN=0,
        SENS_EN_INA238=0,INA238_CURRENT=120,INA238_SHUNT=.0005,BAT1_SOURCE=0,
        BAT1_V_CHANNEL=8,BAT1_I_CHANNEL=4,BAT1_V_DIV=11,BAT1_A_PER_V=50,BAT_V_OFFS_CURR=0,
        MAV_0_CONFIG=104,MAV_1_CONFIG=0,MAV_2_CONFIG=0,GPS_1_CONFIG=201,GPS_2_CONFIG=103,RC_PORT_CONFIG=101,RC_INPUT_PROTO=6,
        PWM_MAIN_TIM0=-4,PWM_MAIN_TIM1=50,PWM_MAIN_TIM2=50,SDLOG_MODE=-1,CBRK_SUPPLY_CHK=0)
    expected.update({f'PWM_MAIN_FUNC{i}':100+i if i<=4 else 0 for i in range(1,9)})
    doc['parameters']=c.params(expected)
    assert all(math.isclose(doc['parameters'][k],v,rel_tol=1e-6,abs_tol=1e-7) for k,v in expected.items()),doc['parameters']
    doc['native_before']={cmd:c.shell(cmd) for cmd in ['ver all','icm45686 status','bmp581 status','mmc5983ma status',
        'ecf_interfaces status','listener sensor_accel -n 1','listener sensor_gyro -n 1','listener sensor_baro -n 1',
        'listener sensor_mag -n 1','listener system_power -n 1','commander status','listener cpuload -n 1']}
    assert all('not running' not in doc['native_before'][cmd].lower() for cmd in ['icm45686 status','bmp581 status','mmc5983ma status'])
    for number in [1,105,30]:c.interval(number,200000)
    start=time.monotonic();next_save=start+15
    while time.monotonic()-start<a.seconds:
        m=c.receive(.2)
        if m is not None and m.get_type() in ['SYS_STATUS','HIGHRES_IMU','ATTITUDE','HEARTBEAT','STATUSTEXT']:
            doc['samples'].append(dict(elapsed=time.monotonic()-start,**m.to_dict()))
        if time.monotonic()>=next_save:
            a.out.write_text(json.dumps(doc,indent=2)+'\n');next_save+=15
            print(json.dumps(dict(elapsed=round(time.monotonic()-start,1),samples=len(doc['samples']))),flush=True)
    doc['seconds']=time.monotonic()-start
    doc['streams']={}
    for name in ['SYS_STATUS','HIGHRES_IMU','ATTITUDE']:
        rows=[r for r in doc['samples'] if r['mavpackettype']==name]
        gaps=[b['elapsed']-a['elapsed'] for a,b in zip(rows,rows[1:])]
        doc['streams'][name]=dict(samples=len(rows),maximum_gap_seconds=max(gaps,default=0))
        assert len(rows)>=a.seconds*3 and max(gaps,default=0)<1.5,(name,doc['streams'][name])
    imu=[r for r in doc['samples'] if r['mavpackettype']=='HIGHRES_IMU']
    assert len({r['time_usec'] for r in imu})==len(imu)
    for r in imu:
        assert all(math.isfinite(r[k]) for k in ['xacc','yacc','zacc','xgyro','ygyro','zgyro','xmag','ymag','zmag','abs_pressure'])
        assert 5<math.sqrt(sum(r[k]**2 for k in ['xacc','yacc','zacc']))<15
        assert 300<r['abs_pressure']<1250 and any(r[k]!=0 for k in ['xmag','ymag','zmag'])
    assert len({r['abs_pressure'] for r in imu})>1
    assert len({tuple(r[k] for k in ['xmag','ymag','zmag']) for r in imu})>1
    doc['maximum_load_permille']=max(r['load'] for r in doc['samples'] if r['mavpackettype']=='SYS_STATUS')
    assert doc['maximum_load_permille']<800
    doc['native_after']={cmd:c.shell(cmd) for cmd in ['icm45686 status','bmp581 status','mmc5983ma status','commander status','listener cpuload -n 1']}
    doc['native_checks_bypassed']=False;doc['passed']=True
except Exception as error:doc['error']=repr(error);raise
finally:
    c.close();doc['finished_at']=datetime.now(timezone.utc).isoformat();a.out.write_text(json.dumps(doc,indent=2)+'\n')
print(json.dumps({k:doc.get(k) for k in ['passed','seconds','streams','maximum_load_permille']}),flush=True)
