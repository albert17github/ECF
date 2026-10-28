"""Validate native INAV serial configuration and ECF shared-pad ownership."""
import argparse
import datetime
import json
from pathlib import Path
from bench_connections import require_unwired_shared_pads
require_unwired_shared_pads() 
import re
import struct
import subprocess

from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument('--port', required=True)
parser.add_argument('--uid', required=True)
parser.add_argument('--stlink-sn', required=True)
parser.add_argument('--cube', default='C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe')
parser.add_argument('--phase', choices=['serial', 'shared'], required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
assert not args.out.exists()
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), phase=args.phase, passed=False, steps=[])

def persist():
    args.out.write_text(json.dumps(doc, indent=2) + '\n')

def cli(commands):
    client = connect(args.port, args.uid)
    try:
        assert client.request(2) == b'INAV'
        client.enter_cli()
        replies = {}
        for command in commands:
            replies[command] = client.cli(command)
            assert not any(error in replies[command] for error in ('###ERROR', 'Unknown command', 'Invalid name', 'Parse error')), replies[command]
        return replies
    finally:
        client.close()

def registers():
    command = [args.cube, '-c', 'port=SWD', f'sn={args.stlink_sn}', 'mode=HOTPLUG', 'freq=950', '-r32', '0x1FF1E800', '12']
    for address in (0x58020800, 0x58020C00, 0x58021000, 0x58020400):
        command += ['-r32', hex(address), '40']
    command += ['-r32', '0x40022008', '4']
    result = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=25)
    assert result.returncode == 0
    uid = bytes.fromhex(args.uid)
    expected = ' '.join(f'{int.from_bytes(uid[i:i+4], "little"):08X}' for i in range(0, 12, 4))
    assert expected in result.stdout.upper(), 'Wrong MCU UID'
    words = {}
    for line in result.stdout.splitlines():
        match = re.match(r'0x([\da-fA-F]{8})\s*:\s*((?:[\da-fA-F]{8}\s*)+)$', line)
        if match:
            address = int(match[1], 16)
            for word in match[2].split():
                words[address] = int(word, 16)
                address += 4
    pins = {}
    for name, address, numbers in [('B', 0x58020400, (5, 6, 8, 9)), ('C', 0x58020800, (4, 5)),
                                   ('D', 0x58020C00, (8, 9, 12, 13)), ('E', 0x58021000, (0, 1, 9, 13))]:
        for pin in numbers:
            pins[f'P{name}{pin}'] = dict(mode=(words[address] >> (2*pin)) & 3,
                pull=(words[address+12] >> (2*pin)) & 3, open_drain=(words[address+4] >> pin) & 1)
    return dict(stdout=result.stdout, pins=pins, adc1_cr=words[0x40022008])

def step(name, commands, analog=(), alternate=(), status_required=(), settings_required=(), adc=None):
    row = dict(name=name, configure=cli([*commands, 'save']))
    doc['steps'].append(row)
    persist()
    row['snapshot'] = cli(['status', 'resource', 'serial', 'get vbat_meter_type', 'get current_meter_type', 'get adc_', 'get rssi_source', 'get pitot_hardware', 'get gyro_to_use', 'exit'])
    ready = connect(args.port, args.uid)
    ready.close()
    row['registers'] = registers()
    if adc is not None:
        assert bool(row['registers']['adc1_cr'] & 1) == adc, (name, row['registers']['adc1_cr'])
    for pin in analog:
        assert row['registers']['pins'][pin]['mode'] == 3 and row['registers']['pins'][pin]['pull'] == 0, (name, pin, row['registers']['pins'][pin])
    for pin in alternate:
        assert row['registers']['pins'][pin]['mode'] == 2, (name, pin, row['registers']['pins'][pin])
    for value in status_required:
        assert value in row['snapshot']['status'], (name, value)
    for value in settings_required:
        assert value in '\n'.join(row['snapshot'].values()), (name,value)
    row['passed'] = True
    persist()
    print(json.dumps(dict(step=name, passed=True)), flush=True)

try:
    doc['baseline'] = cli(['defaults'])
    if args.phase == 'serial':
        client = connect(args.port, args.uid)
        frame = struct.Struct('<BIBBBB')
        try:
            before = list(frame.iter_unpack(client.request(0x1009)))
            assert {row[0] for row in before} == {20, 0, 2, 3, 4, 5, 6, 7}
            doc['before'] = before
            assert next(row[1] for row in before if row[0] == 2) == 0, 'Factory UART3 must be spare'
            assert next(row[1] for row in before if row[0] == 4) == 1, 'Factory UART5 MSP missing'
            assert next(row[1] for row in before if row[0] == 7) == 0, 'Factory UART8 must be disabled'
            masks = {20: 1, 0: 64, 2: 0, 3: 2, 4: 1, 5: 512, 6: 131072, 7: 0}
            pattern = [tuple([row[0], masks[row[0]], *row[2:]]) for row in before]
            client.request(0x100A, b''.join(frame.pack(*row) for row in pattern))
            assert list(frame.iter_unpack(client.request(0x1009))) == pattern
            client.request(250)
            client.enter_cli()
            doc['save'] = client.cli('save')
        finally:
            client.close()
        client = connect(args.port, args.uid)
        try:
            after = list(frame.iter_unpack(client.request(0x1009)))
            assert after == pattern
            doc.update(after_save_reboot=after, high_function_bits_preserved=True)
        finally:
            client.close()
    else:
        step('factory_adc_uart3', [], analog=('PD12','PD13','PE0','PE1','PC4','PC5','PD8','PD9','PB5','PB6','PE9','PE13'), alternate=('PB8','PB9'), adc=True,
             status_required=('BATTERY : configured = ADC 1, used = ADC 1','CURRENT : configured = ADC 2, used = ADC 2'))
        step('adc', ['set ecf_io_mode = 0', 'serial 4 0 115200 115200 0 115200', 'serial 2 0 115200 115200 0 115200', 'serial 7 0 115200 115200 0 115200',
                     'feature VBAT', 'feature CURRENT_METER', 'set vbat_meter_type = ADC', 'set current_meter_type = ADC'],
             analog=('PE0','PE1','PC4','PC5','PD8','PD9'), alternate=('PD12','PD13'),
             status_required=('BATTERY : configured = ADC 1, used = ADC 1','CURRENT : configured = ADC 2, used = ADC 2'))
        step('uart8_clears_conflicting_adc', ['set ecf_io_mode = 2', 'serial 7 1 115200 115200 0 115200'],
             analog=('PC4','PC5','PD8','PD9'), alternate=('PE0','PE1','PD12','PD13'), adc=False,
             status_required=('BATTERY : configured = ADC 1, used = none','CURRENT : configured = ADC 2, used = none'))
        step('uart8_uart3', ['set ecf_io_mode = 3', 'serial 2 1 115200 115200 0 115200'],
             analog=('PC4','PC5','PD12','PD13'), alternate=('PE0','PE1','PD8','PD9'), adc=False)
        step('return_to_adc_without_defaults', ['set ecf_io_mode = 1', 'serial 7 0 115200 115200 0 115200',
             'feature VBAT','feature CURRENT_METER','set vbat_meter_type = ADC','set current_meter_type = ADC'],
             analog=('PE0','PE1','PC4','PC5','PD12','PD13'), alternate=('PD8','PD9'), adc=True,
             status_required=('BATTERY : configured = ADC 1, used = ADC 1','CURRENT : configured = ADC 2, used = ADC 2'))
        step('swapped_adc_routing', ['set adc_vbat_channel = 2','set adc_current_channel = 1'],
             analog=('PE0','PE1','PC4','PC5'),
             status_required=('BATTERY : configured = ADC 2, used = ADC 2','CURRENT : configured = ADC 1, used = ADC 1'))
        step('auxiliary_adc_routing', ['feature -VBAT','feature -CURRENT_METER',
             'set vbat_meter_type = NONE','set current_meter_type = NONE',
             'set adc_vbat_channel = 0','set adc_current_channel = 0',
             'set adc_rssi_channel = 1','set adc_airspeed_channel = 2',
             'feature RSSI_ADC','set rssi_source = ADC','set pitot_hardware = ADC'],
             analog=('PE0','PE1','PC4','PC5'),
             status_required=('RSSI : configured = ADC 1, used = ADC 1','AIRSPEED : configured = ADC 2, used = ADC 2'))
        step('uart8_disables_all_effective_analog_roles', ['set ecf_io_mode = 3', 'serial 7 1 115200 115200 0 115200'],
             analog=('PC4','PC5'),alternate=('PE0','PE1'),
             status_required=('RSSI : configured = ADC 1, used = none','AIRSPEED : configured = ADC 2, used = none'),
             settings_required=('rssi_source = NONE','pitot_hardware = NONE'))
        step('return_to_auxiliary_adc_without_defaults', ['set ecf_io_mode = 1', 'serial 7 0 115200 115200 0 115200',
             'feature RSSI_ADC','set rssi_source = ADC','set pitot_hardware = ADC'],
             analog=('PE0','PE1','PC4','PC5'),
             status_required=('RSSI : configured = ADC 1, used = ADC 1','AIRSPEED : configured = ADC 2, used = ADC 2'))
    doc['restore'] = cli(['defaults'])
    doc['passed'] = True
finally:
    persist()
print(json.dumps(dict(passed=doc['passed'], phase=args.phase)))
