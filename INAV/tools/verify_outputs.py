"""Check ECFH743 INAV output allocation, native protection and DMA destinations."""
import argparse
import datetime
import json
from pathlib import Path
import re
import struct
import subprocess

from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument('--port', required=True)
parser.add_argument('--uid', required=True)
parser.add_argument('--stlink-sn', required=True)
parser.add_argument('--cube', default='C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe')
parser.add_argument('--unloaded-bench', action='store_true', required=True)
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
assert args.unloaded_bench and not args.out.exists()
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), passed=False, cases=[])

def save():
    args.out.write_text(json.dumps(doc, indent=2) + '\n')

def cli(commands):
    client = connect(args.port, args.uid)
    try:
        assert client.request(2) == b'INAV'
        client.enter_cli()
        responses = {}
        for command in commands:
            responses[command] = client.cli(command)
            assert not any(error in responses[command] for error in ('###ERROR', 'Parse error', 'Invalid name', 'Unknown command')), responses[command]
        return responses
    finally:
        client.close()

def hardware():
    command = [args.cube, '-c', 'port=SWD', f'sn={args.stlink_sn}', 'mode=HOTPLUG', 'freq=950', '-r32', '0x1FF1E800', '12']
    for address, count in [(0x40000000, 80), (0x40000400, 80), (0x40000800, 80), (0x40010000, 80), (0x40020000, 208)]:
        command += ['-r32', hex(address), str(count)]
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
    timers = {}
    for name, base in [('TIM2', 0x40000000), ('TIM3', 0x40000400), ('TIM4', 0x40000800), ('TIM1', 0x40010000)]:
        timers[name] = {key: words[base + offset] for key, offset in [('CR1',0),('DIER',12),('CCER',32),('PSC',40),('ARR',44),('DCR',72)]}
    destinations = [words[0x40020018 + stream*24] for stream in range(8)]
    return dict(stdout=result.stdout, timers=timers, dma1_peripheral_addresses=[hex(value) for value in destinations]), destinations

def run_case(name, commands, motor_count, servo_count, dma):
    case = dict(name=name, configure=cli([*commands, 'save']))
    doc['cases'].append(case)
    save()
    client = connect(args.port, args.uid)
    entered = False
    try:
        raw = client.request(0x2000)
        flags = struct.unpack_from('<I', raw, 9)[0]
        assert flags & ((1 << 2) | (1 << 15) | (1 << 27)) == 0, hex(flags)
        motors = list(struct.unpack('<8H', client.request(104)[:16]))
        servos = list(struct.unpack('<8H', client.request(103)[:16]))
        assert all(value <= 1000 for value in motors), motors
        case.update(arming_flags=flags, motors=motors, servos=servos)
        client.enter_cli()
        entered = True
        case['snapshot'] = {command: client.cli(command) for command in ['status','resource','timer_output_mode','get motor_pwm_protocol','get servo_pwm_rate','mmix','smix']}
        pin_names = ['A00','A01','A02','A03','B01','B00','D14','D15']
        for index in range(motor_count):
            assert f'{pin_names[index]}: MOTOR{index+1} OUT' in case['snapshot']['resource'], (index, case['snapshot']['resource'])
        for index in range(servo_count):
            assert f'{pin_names[motor_count+index]}: SERVO{motor_count+index+1} OUT' in case['snapshot']['resource'], (index, case['snapshot']['resource'])
        case['hardware'], actual_dma = hardware()
        for stream, address in dma.items():
            assert actual_dma[stream] == address, (stream, hex(actual_dma[stream]), hex(address))
        assert case['hardware']['timers']['TIM1']['CR1'] & 1 == 0
        assert case['hardware']['timers']['TIM1']['CCER'] == 0
        case['passed'] = True
        client.cli('exit')
        entered = False
    finally:
        if entered:
            try:
                case['exit_after_failure'] = client.cli('exit')
            except Exception as error:
                case['exit_after_failure_error'] = str(error)
        client.close()
        save()
    print(json.dumps(dict(case=name, passed=True)), flush=True)

try:
    doc['baseline'] = cli(['defaults'])
    run_case('default_four_dshot', [], 4, 0, {0:0x4000004c,4:0,6:0})
    servo_rules = [f'smix {index*2+polarity} {index} 29 {100 if polarity==0 else -100} 0 -1' for index in range(4) for polarity in range(2)]
    run_case('four_dshot_four_pwm', ['timer_output_mode 2 SERVOS','timer_output_mode 3 SERVOS','smix reset',*servo_rules], 4, 4, {0:0x4000004c,4:0,6:0})
    extra_mixers = ['mmix 4 1 -1 1 -1','mmix 5 1 -1 -1 1','mmix 6 1 1 1 1','mmix 7 1 1 -1 -1']
    run_case('eight_dshot', ['smix reset','timer_output_mode 2 MOTORS','timer_output_mode 3 MOTORS',*extra_mixers], 8, 0, {0:0x4000004c,4:0x4000044c,6:0x4000084c})
    run_case('eight_pwm', ['set motor_pwm_protocol = STANDARD'], 8, 0, {0:0,4:0,6:0})
    doc['restore'] = cli(['defaults'])
    doc['passed'] = True
finally:
    save()
print(json.dumps(dict(passed=doc['passed'], cases=len(doc['cases']))))
