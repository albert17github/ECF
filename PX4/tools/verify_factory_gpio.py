"""Read the physical factory interface configuration from the UID-matched H743."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import struct
import time

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
p.add_argument('--uid',required=True); p.add_argument('--stlink-sn',required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--port',default='COM31')
p.add_argument('--cube',default='C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe')
a=p.parse_args(); assert not a.out.exists()
r=json.loads((a.root/'release.json').read_text(encoding='utf-8-sig'))
uid=bytes.fromhex(a.uid); assert len(uid)==12
expected=' '.join(f'{int.from_bytes(uid[i:i+4],"little"):08X}' for i in (0,4,8))
command=[a.cube,'-c','port=SWD',f'sn={a.stlink_sn}','mode=HOTPLUG','freq=950','-r32','0x1ff1e800','12']
for address,count in [(0x58020000,40),(0x58020400,40),(0x58020800,40),(0x58020c00,40),(0x58021000,40),(0x40022000,32)]:
    command+=['-r32',hex(address),str(count)]
time.sleep(5)  # USB/MSP can answer before BF finishes its phased initialization.
result=subprocess.run(command,capture_output=True,text=True,errors='replace',timeout=30)
doc=dict(at=datetime.now(timezone.utc).isoformat(),project=r['project'],image_sha256=r['firmware']['sha256'],
    uid=a.uid,passed=False,physical_external_interoperability_tested=False,stdout=result.stdout,pins={})
try:
    assert result.returncode==0 and expected in result.stdout.upper(),'No UID-matched register capture'
    words={}
    for line in result.stdout.splitlines():
        m=re.match(r'0x([\da-fA-F]{8})\s*:\s*((?:[\da-fA-F]{8}\s*)+)$',line)
        if m:
            address=int(m[1],16)
            for word in m[2].split(): words[address]=int(word,16); address+=4
    def pin(name):
        port,number=name[1],int(name[2:]); base=0x58020000+('ABCDE'.index(port)*0x400)
        state=dict(mode=words[base]>>(number*2)&3,pull=words[base+12]>>(number*2)&3,
            af=words[base+32+4*(number//8)]>>((number%8)*4)&15)
        doc['pins'][name]=state
        return state
    def analog(name):
        state=pin(name); assert state['mode']==3 and state['pull']==0,(name,state)
    def af(name,function):
        state=pin(name); assert state['mode']==2 and state['af']==function,(name,state,function)
    def uart_tx(name,function):
        state=pin(name)
        if r['project']=='Betaflight' and state['mode']==0 and state['pull']==1:
            doc.setdefault('native_tx_monitor_idle',[]).append(name)
        else:
            assert state['mode']==2 and state['af']==function,(name,state,function)
    for name in ['PC4','PC5','PE0','PE1','PD12','PD13']: analog(name)
    if r['project']=='PX4':
        states=[pin(name) for name in ['PA8','PC9']]
        active=all(state['mode']==2 and state['af']==4 for state in states)
        unbound=all(state['mode']==0 and state['pull']==0 for state in states)
        assert active or unbound,states
        doc['i2c3_native_no_external_device_release']=unbound
    else:
        for name in ['PA8','PC9']: af(name,4)
    uart_tx('PA9',7); af('PA10',7)
    af('PB12',14)
    uart_tx('PB13',14)
    uart_tx('PC6',7); af('PC7',7)
    if r['project']=='INAV':
        # INAV's native telemetry driver is gated by its output-enable feature.
        sys.path.insert(0,str(a.root/'tools'))
        from msp_client import connect
        client=connect(a.port,a.uid)
        try:
            assert client.request(2)==b'INAV'
            feature_mask=struct.unpack('<I',client.request(36))[0]
        finally:
            client.close()
        feature_header=(a.root/'source/src/main/fc/config.h').read_text()
        output_bit=int(re.search(r'FEATURE_PWM_OUTPUT_ENABLE\s*=\s*1\s*<<\s*(\d+)',feature_header)[1])
        doc['native_pwm_output_enabled']=bool(feature_mask&(1<<output_bit))
        if doc['native_pwm_output_enabled']:
            af('PE7',7)
        else:
            analog('PE7')
            doc['esc_telemetry_native_condition']='UART7 ESCSERIAL role is assigned; native driver opens after PWM output enable and motor mixer setup'
    else:
        af('PE7',7)  # Native ESC telemetry receive; unused TX may remain analog.
    if r['project'] in ('ArduCopter','PX4'):
        for name in ['PD0','PD1']: af(name,8)
    else:
        for name in ['PD0','PD1']:
            state=pin(name)
            assert state['mode'] in (0,3),(name,state)  # Native unused inputs may keep pullups.
    for name in ['PB8','PB9']: af(name,9)
    if r['project']=='INAV':
        for name in ['PB5','PB6']: analog(name)
    else:
        for name in ['PB5','PB6']: af(name,9)
    doc['adc1_enabled']=bool(words[0x40022008]&1)
    assert doc['adc1_enabled'],'ADC1 disabled in the factory analog profile'
    doc['passed']=True
finally:
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(doc,indent=2)+'\n')
print(json.dumps({k:doc[k] for k in ['project','passed','adc1_enabled']}))
