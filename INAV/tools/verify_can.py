"""Validate both real H7 CAN controllers and DroneCAN decoding in internal loopback."""
import argparse
import datetime
import json
from pathlib import Path
import re
import secrets
import struct
import subprocess
import time

from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument('--port', required=True)
parser.add_argument('--uid', required=True)
parser.add_argument('--stlink-sn', required=True)
parser.add_argument('--cube', default='C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe')
parser.add_argument('--devices', nargs='+', type=int, choices=(1,2), default=[1,2])
parser.add_argument('--bitrates', nargs='+', type=int, choices=(125,250,500,1000), default=[125,250,500,1000])
parser.add_argument('--normal-config', action='store_true')
parser.add_argument('--unloaded-bench', action='store_true', required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
assert args.unloaded_bench and not args.out.exists()
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), passed=False, cases=[],
           physical_external_bus_tested=False, mode='internal controller loopback with a diagnostic virtual peer')
uid = bytes.fromhex(args.uid)
assert len(uid)==12
reply_frame = struct.Struct('<BBBBhIIBB8s8s')
node_frame = struct.Struct('<BBBIIH')

def persist():
    args.out.write_text(json.dumps(doc,indent=2)+'\n')

def cli(commands):
    client = connect(args.port,args.uid)
    try:
        assert client.request(2)==b'INAV'
        client.enter_cli()
        replies={}
        for command in commands:
            replies[command]=client.cli(command)
            assert not any(error in replies[command] for error in ('###ERROR','Invalid name','Unknown command','Parse error')), replies[command]
        return replies
    finally:
        client.close()

def reject(client,payload):
    try:
        client.request(0x2FE0,payload)
    except RuntimeError as error:
        assert str(error)=='MSP rejected command 12256', str(error)
        return True
    raise AssertionError('Unsafe or malformed diagnostic request accepted')

def registers():
    command=[args.cube,'-c','port=SWD',f'sn={args.stlink_sn}','mode=HOTPLUG','freq=950','-r32','0x1FF1E800','12']
    for address,count in [(0x58020400,40),(0x4000A000,80),(0x4000A400,80)]:
        command+=['-r32',hex(address),str(count)]
    result=subprocess.run(command,capture_output=True,text=True,errors='replace',timeout=25)
    expected=' '.join(f'{int.from_bytes(uid[i:i+4],"little"):08X}' for i in range(0,12,4))
    assert result.returncode==0 and expected in result.stdout.upper()
    words={}
    for line in result.stdout.splitlines():
        match=re.match(r'0x([\da-fA-F]{8})\s*:\s*((?:[\da-fA-F]{8}\s*)+)$',line)
        if match:
            address=int(match[1],16)
            for word in match[2].split():
                words[address]=int(word,16)
                address+=4
    gpio={}
    base=0x58020400
    for pin in (5,6,8,9):
        gpio[f'PB{pin}']=dict(mode=(words[base]>>(2*pin))&3,pull=(words[base+12]>>(2*pin))&3,
                             af=(words[base+32+(pin//8)*4]>>((pin%8)*4))&15)
    controllers={}
    for device,base in [(1,0x4000A000),(2,0x4000A400)]:
        controllers[str(device)]={name:words[base+offset] for name,offset in [('TEST',16),('CCCR',24),('NBTP',28),('ECR',64),('PSR',68)]}
    return dict(stdout=result.stdout,gpio=gpio,controllers=controllers)

def node_table(client):
    data=client.request(0x2042)
    assert len(data)==1+data[0]*node_frame.size
    return list(node_frame.iter_unpack(data[1:]))

try:
    doc['baseline']=cli(['defaults'])
    doc['disable_for_negative_control']=cli(['set dronecan_enabled = OFF','save'])
    client=connect(args.port,args.uid)
    try:
        assert client.request(3)==b'\x09\x01\x00'
        flags=struct.unpack_from('<I',client.request(0x2000),9)[0]
        assert flags & ((1<<2)|(1<<31))==0
        doc['disabled_diagnostic_rejected']=reject(client,uid+struct.pack('<I',1))
    finally:
        client.close()
    doc['disabled_gpio']=registers()
    assert all(state['mode']==3 and state['pull']==0 for state in doc['disabled_gpio']['gpio'].values())
    persist()
    for device in args.devices:
        for bitrate in args.bitrates:
            row=dict(device=device,bitrate_kbps=bitrate,passed=False,exchanges=[])
            doc['cases'].append(row)
            row['configure']=cli(['set dronecan_enabled = ON',f'set dronecan_device = {device}',
                'set dronecan_loopback = ON',f'set dronecan_bitrate_kbps = {bitrate}','save'])
            persist()
            client=connect(args.port,args.uid)
            entered=False
            try:
                # Observe the native arming block after initialization settles.
                until=time.monotonic()+2
                while time.monotonic()<until:
                    status=client.request(0x2000)
                    assert struct.unpack_from('<I',status,9)[0] & (1<<2)==0
                    time.sleep(.1)
                flags=struct.unpack_from('<I',client.request(0x2000),9)[0]
                assert flags & (1<<31), 'Loopback must inhibit arming'
                row['arming_flags']=flags
                row['truncated_requests_rejected']=[reject(client,bytes(size)) for size in (0,1,4,12,15,17)]
                row['wrong_uid_rejected']=reject(client,bytes(12)+struct.pack('<I',0x12345678))
                for index in range(3):
                    nonce=secrets.randbits(32)
                    raw=client.request(0x2FE0,uid+struct.pack('<I',nonce))
                    assert len(raw)==reply_frame.size
                    actual_device,peer,payload_match,node_match,parsed,tx_id,rx_id,tx_len,rx_len,tx,rx=reply_frame.unpack(raw)
                    item=dict(nonce=nonce,raw=raw.hex(),device=actual_device,peer=peer,payload_matched=payload_match,
                              node_matched=node_match,parse_result=parsed,tx_id=hex(tx_id),rx_id=hex(rx_id),tx=tx.hex(),rx=rx.hex())
                    row['exchanges'].append(item)
                    persist()
                    assert actual_device==device and peer in (126,127)
                    assert payload_match==node_match==1 and parsed>=0
                    assert tx_id==rx_id==(0x80000000|(16<<24)|(341<<8)|peer)
                    assert tx_len==rx_len==8 and tx==rx
                    assert tx[:7]==struct.pack('<IBH',nonce,0,0xECF3) and tx[7]&0xE0==0xC0
                    nodes=node_table(client)
                    item['native_nodes']=nodes
                    assert any(node[0]==peer and node[1:3]==(0,0) and node[4]==nonce and node[5]==0xECF3 for node in nodes)
                client.enter_cli()
                entered=True
                row['snapshot']={command:client.cli(command) for command in ('status','can_info','get dronecan','resource')}
                row['registers']=registers()
                pins=(8,9) if device==1 else (5,6)
                for pin,state in row['registers']['gpio'].items():
                    if int(pin[2:]) in pins:
                        assert state['mode']==2 and state['af']==9, (pin,state)
                    else:
                        assert state['mode']==3 and state['pull']==0, (pin,state)
                controller=row['registers']['controllers'][str(device)]
                assert controller['CCCR'] & 1==0 and controller['CCCR'] & (1<<5) and controller['CCCR'] & (1<<7)
                assert controller['TEST'] & (1<<4)
                nbtp=controller['NBTP']
                prescaler=((nbtp>>16)&511)+1
                seg1=((nbtp>>8)&255)+1
                seg2=(nbtp&127)+1
                clock=int(re.search(r'clock=(\d+)',row['snapshot']['can_info'])[1])
                assert clock==80000000 and clock==bitrate*1000*prescaler*(1+seg1+seg2)
                row['timing']=dict(clock=clock,prescaler=prescaler,segment1=seg1,segment2=seg2)
                row['passed']=True
                client.cli('exit')
                entered=False
            finally:
                if entered:
                    try: row['exit_after_failure']=client.cli('exit')
                    except Exception as error: row['exit_after_failure_error']=str(error)
                row['packets']=client.packets
                client.close()
                persist()
            print(json.dumps(dict(device=device,bitrate_kbps=bitrate,passed=True)),flush=True)
    if args.normal_config:
        doc['normal_mode_cases']=[]
        for device in args.devices:
            normal=dict(device=device,passed=False)
            doc['normal_mode_cases'].append(normal)
            normal['configure']=cli(['set dronecan_enabled = ON',f'set dronecan_device = {device}',
                'set dronecan_loopback = OFF','set dronecan_bitrate_kbps = 1000','save'])
            client=connect(args.port,args.uid)
            entered=False
            try:
                flags=struct.unpack_from('<I',client.request(0x2000),9)[0]
                assert flags & ((1<<2)|(1<<31))==0
                normal['arming_flags']=flags
                normal['diagnostic_rejected']=reject(client,uid+struct.pack('<I',1))
                client.enter_cli()
                entered=True
                normal['snapshot']={command:client.cli(command) for command in ('can_info','get dronecan','resource')}
                normal['registers']=registers()
                control=normal['registers']['controllers'][str(device)]
                assert control['CCCR'] & ((1<<5)|(1<<7)|1)==0, control
                assert control['TEST'] & (1<<4)==0
                normal['passed']=True
                client.cli('exit')
                entered=False
            finally:
                if entered:
                    try: normal['exit_after_failure']=client.cli('exit')
                    except Exception as error: normal['exit_after_failure_error']=str(error)
                client.close()
                persist()
            print(json.dumps(dict(device=device,normal_configuration=True,passed=True)),flush=True)
    doc['restore']=cli(['defaults'])
    ready=connect(args.port,args.uid)
    ready.close()
    doc['restored_gpio']=registers()
    assert all(doc['restored_gpio']['gpio'][pin]['mode']==3 and doc['restored_gpio']['gpio'][pin]['pull']==0 for pin in ('PB5','PB6'))
    assert all(doc['restored_gpio']['gpio'][pin]['mode']==2 for pin in ('PB8','PB9'))
    doc['passed']=True
finally:
    persist()
print(json.dumps(dict(passed=doc['passed'],cases=len(doc['cases']))))
