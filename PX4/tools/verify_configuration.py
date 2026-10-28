"""Check native PX4 configuration, inactive pad states, timers and CAN without arming."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from bench_connections import require_unwired_shared_pads
import re
import time

from px4_client import PX4Client
from swd_readback import SwdReadback

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--uid',required=True);p.add_argument('--port',default='auto')
p.add_argument('--stlink-sn',required=True);p.add_argument('--cube')
p.add_argument('--phase',choices=['serial','shared','outputs','can'],required=True)
p.add_argument('--unloaded-bench',action='store_true',required=True)
p.add_argument('--out',type=Path,required=True);a=p.parse_args();assert not a.out.exists()
if a.phase in ('serial','shared'):
    require_unwired_shared_pads()
doc=dict(at=datetime.now(timezone.utc).isoformat(),phase=a.phase,passed=False,cases=[],actuator_commands_sent=0,
    physical_waveforms_tested=False,external_peripheral_interoperability_tested=False)
swd=SwdReadback(a.uid,a.stlink_sn,a.cube);client=None;original=None


def save():
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(doc,indent=2)+'\n')


def connect():return PX4Client(a.port,a.uid)


def apply(values):
    global client
    client=connect()
    try:
        changes=[client.set_param(k,v) for k,v in values.items()]
        stored=client.shell('param save');client.reboot()
    finally:client.close();client=None
    time.sleep(.5);client=connect()
    actual=client.params(values)
    assert all(abs(actual[k]-v)<=max(1e-7,abs(v)*1e-6) for k,v in values.items()),actual
    return dict(changes=changes,save_reply=stored,readback=actual)


def snapshot():
    raw=swd.read([(base,40) for base in [0x58020000,0x58020400,0x58020800,0x58020c00,0x58021000]]+
        [(0x40022000,32),(0x58026000,32),(0x40000000,80),(0x40000400,80),(0x40000800,80),
         (0x40010000,80),(0x4000a000,80),(0x4000a400,80)])
    return raw


def pin(words,port,number):
    base=dict(A=0x58020000,B=0x58020400,C=0x58020800,D=0x58020c00,E=0x58021000)[port]
    return dict(mode=words[base]>>(number*2)&3,pull=words[base+12]>>(number*2)&3,
        af=words[base+32+4*(number//8)]>>((number%8)*4)&15)


def analog(words,port,numbers):
    result={str(n):pin(words,port,n) for n in numbers}
    assert all(v['mode']==3 and v['pull']==0 for v in result.values()),(port,result)
    return result


try:
    client=connect()
    names=['ECF_IO_MODE','SENS_EN_INA238','MAV_0_CONFIG','MAV_1_CONFIG','MAV_2_CONFIG','GPS_1_CONFIG','GPS_2_CONFIG','RC_PORT_CONFIG','RC_INPUT_PROTO','DSHOT_TEL_CFG']
    if a.phase in ['serial','shared']:names+=['SER_GPS1_BAUD','SER_GPS2_BAUD','SER_RC_BAUD']
    if a.phase=='outputs':names+=['DSHOT_BIDIR_EN',*[f'PWM_MAIN_FUNC{i}' for i in range(1,9)],*[f'PWM_MAIN_TIM{i}' for i in range(3)]]
    if a.phase=='can':names+=['UAVCAN_ENABLE','UAVCAN_BITRATE']
    original=client.params(names);doc['original']=original;client.close();client=None
    if a.phase=='shared':
        for mode in range(4):
            for digital in range(2):
                values=dict(ECF_IO_MODE=mode,SENS_EN_INA238=digital,MAV_0_CONFIG=102 if mode&1 else 300 if mode&2 else 0,
                    MAV_1_CONFIG=300 if mode==3 else 0,MAV_2_CONFIG=0,SER_RC_BAUD=115200,
                    GPS_1_CONFIG=0,GPS_2_CONFIG=0,RC_PORT_CONFIG=0,RC_INPUT_PROTO=0,DSHOT_TEL_CFG=0)
                row=dict(mode=mode,digital_monitor=digital,passed=False,parameters=apply(values));doc['cases'].append(row);save()
                row['native']=client.shell('ecf_interfaces status')
                assert re.search(r'ECF_IO_V1 MODE='+str(mode)+r'\b',row['native'])
                raw=snapshot();words=raw['words'];row['registers']=raw
                row['inactive_uart3_or_i2c4']=analog(words,'D',[12,13] if mode&1 else [8,9])
                row['analog_inputs']=analog(words,'C',[4,5])
                if not mode&2:row['inactive_uart8']=analog(words,'E',[0,1])
                else:
                    assert all(pin(words,'E',n)['mode']==2 and pin(words,'E',n)['af']==8 for n in [0,1])
                if mode&1:assert all(pin(words,'D',n)['mode']==2 and pin(words,'D',n)['af']==7 for n in [8,9])
                adc1=bool(words[0x40022008]&1);expected_adc1=not(mode&2) and not digital
                assert adc1==expected_adc1,(mode,digital,adc1)
                row['adc1_enabled']=adc1;row['adc3_enabled']=bool(words[0x58026008]&1)
                if not expected_adc1:assert row['adc3_enabled']
                row['passed']=True;save();client.close();client=None
                print(json.dumps(dict(mode=mode,digital=digital,passed=True)),flush=True)
    elif a.phase=='serial':
        groups=[[(1,101,'ttyS0'),(3,102,'ttyS2'),(4,103,'ttyS3')],
                [(5,104,'ttyS4'),(6,201,'ttyS5'),(7,202,'ttyS6')],[(8,300,'ttyS7')]]
        for group in groups:
            values=dict(ECF_IO_MODE=3,SENS_EN_INA238=0,SER_GPS1_BAUD=115200,SER_GPS2_BAUD=115200,SER_RC_BAUD=115200,
                GPS_1_CONFIG=0,GPS_2_CONFIG=0,RC_PORT_CONFIG=0,RC_INPUT_PROTO=0,DSHOT_TEL_CFG=0,
                **{f'MAV_{i}_CONFIG':group[i][1] if i<len(group) else 0 for i in range(3)})
            row=dict(uarts=[v[0] for v in group],passed=False,parameters=apply(values));doc['cases'].append(row);save()
            row['native']=client.shell('mavlink status');row['devices']=client.shell('ls /dev')
            for _,_,node in group:assert '/dev/'+node in row['native'],row['native']
            row['registers']=snapshot();save();client.close();client=None
            uart_pins={1:('A',9,10,7),3:('D',8,9,7),4:('D',1,0,8),5:('B',13,12,14),
                6:('C',6,7,7),7:('E',8,7,7),8:('E',1,0,8)}
            row['uart_pins']={}
            for uart,_,_ in group:
                port,tx,rx,af=uart_pins[uart]
                states=[pin(row['registers']['words'],port,n) for n in [tx,rx]]
                row['uart_pins'][str(uart)]=states
                assert all(s['mode']==2 and s['af']==af for s in states),(uart,states)
            row['passed']=True;save()
            print(json.dumps(dict(uarts=row['uarts'],passed=True)),flush=True)
    elif a.phase=='can':
        for bitrate in [125000,250000,500000,1000000]:
            values={**original,'UAVCAN_ENABLE':1,'UAVCAN_BITRATE':bitrate}
            row=dict(bitrate=bitrate,passed=False,parameters=apply(values));doc['cases'].append(row);save()
            row['native']=client.shell('uavcan status');row['registers']=snapshot()
            words=row['registers']['words']
            row['controllers']={}
            for number,base in [(1,0x4000a000),(2,0x4000a400)]:
                timing=words[base+28]
                divisor=((timing>>16&511)+1)*((timing>>8&255)+(timing&127)+3)
                actual_rate=8000000/divisor
                row['controllers'][str(number)]=dict(cccr=hex(words[base+24]),nbtp=hex(timing),
                    protocol_status=hex(words[base+68]),nominal_bitrate=actual_rate)
                assert abs(actual_rate-bitrate)<1,(number,bitrate,actual_rate)
                if words[base+24]&1:
                    assert words[base+68]&(1<<7),'CAN initialization incomplete without bus-off evidence'
            for n in [5,6,8,9]:assert pin(words,'B',n)['mode']==2 and pin(words,'B',n)['af']==9
            row['passed']=True;save();client.close();client=None
            print(json.dumps(dict(bitrate=bitrate,controllers=2,passed=True)),flush=True)
    else:
        cases=[('four_dshot_four_pwm',[-4,50,50],False),('eight_dshot',[-4,-4,-4],False),
               ('eight_pwm',[50,50,50],False),('four_bidirectional_dshot_four_pwm',[-4,50,50],True)]
        for name,timers,bidirectional in cases:
            values={**original,**{f'PWM_MAIN_TIM{i}':v for i,v in enumerate(timers)},
                    **{f'PWM_MAIN_FUNC{i}':100+i if i<=4 or name=='eight_dshot' else 196+i for i in range(1,9)},'DSHOT_BIDIR_EN':int(bidirectional)}
            row=dict(name=name,passed=False,parameters=apply(values));doc['cases'].append(row);save()
            row['dshot']=client.shell('dshot status');row['pwm']=client.shell('pwm_out status')
            row['registers']=snapshot();words=row['registers']['words']
            row['output_pins']={}
            for channel,(port,number,af) in enumerate([('A',0,1),('A',1,1),('A',2,1),('A',3,1),
                ('B',1,2),('B',0,2),('D',14,2),('D',15,2)],1):
                state=pin(words,port,number);row['output_pins'][str(channel)]=state
                assert state['mode']==2 and state['af']==af,(channel,state)
            # Timer1 remains reserved for IMU clocks in every output configuration.
            assert words[0x40010000]&1==0,'TIM1 was enabled as an actuator timer'
            if any(v<0 for v in timers):assert 'Outputs initialized: yes' in row['dshot']
            if any(v<0 for v in timers):
                mask=255 if name=='eight_dshot' else 15
                assert re.search(r'Outputs used: 0x'+format(mask,'x')+r'\b',row['dshot'])
            if bidirectional:
                assert 'Bidirectional DShot enabled' in row['dshot']
                capture=re.findall(r'Timer 0, Channel ([0-3]): read (\d+), failed nibble (\d+), failed CRC (\d+), invalid/zero (\d+)',row['dshot'])
                assert len(capture)==4 and all(sum(map(int,r[1:]))>0 for r in capture),row['dshot']
                row['native_capture_channels']=[int(r[0])+1 for r in capture]
            row['passed']=True;save();client.close();client=None
            print(json.dumps(dict(case=name,passed=True)),flush=True)
    doc['passed']=True
except Exception as error:
    doc['error']=repr(error)
    raise
finally:
    if client:client.close();client=None
    if original is not None:
        try:
            doc['restore']=apply(original);client.close();client=None;doc['restored']=True
        except Exception as error:
            doc['restore_error']=repr(error);doc['passed']=False
    if client:client.close()
    doc['finished_at']=datetime.now(timezone.utc).isoformat();save()
print(json.dumps(dict(passed=doc['passed'],phase=a.phase,cases=len(doc['cases']))),flush=True)
assert doc['passed']
