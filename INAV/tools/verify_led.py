"""Verify native PB7 LED polarity with steady levels; return to application."""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess

from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument('--port', required=True)
parser.add_argument('--uid', required=True)
parser.add_argument('--stlink-sn', required=True)
parser.add_argument('--cube', default='C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe')
parser.add_argument('--out', type=Path, required=True)
args = parser.parse_args()
assert not args.out.exists()
uid = bytes.fromhex(args.uid)
assert len(uid) == 12 and uid not in (bytes(12), bytes([255]) * 12)
expected_uid = ' '.join(f'{int.from_bytes(uid[i:i + 4], "little"):08X}' for i in range(0, 12, 4))
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), uid=args.uid, passed=False,
           measured_waveform=False, physical_rgb_visual_required=True, cases=[])
client = connect(args.port, args.uid)
entered = False
try:
    assert client.request(2) == b'INAV'
    doc['entry'] = client.enter_cli()
    entered = True
    for duty in (0, 100, 0):
        reply = client.cli(f'ledpinpwm {duty}')
        assert f'PWM started: {duty}%' in reply, reply
        result = subprocess.run([args.cube, '-c', 'port=SWD', f'sn={args.stlink_sn}', 'mode=HOTPLUG', 'freq=950',
                                 '-r32', '0x1ff1e800', '12', '-r32', '0x58020400', '40',
                                 '-r32', '0x40014800', '72'], capture_output=True, text=True, errors='replace', timeout=25)
        assert result.returncode == 0 and expected_uid in result.stdout.upper(), 'No UID-matched hardware snapshot'
        words = {}
        for line in result.stdout.splitlines():
            match = re.match(r'0x([\da-fA-F]{8})\s*:\s*((?:[\da-fA-F]{8}\s*)+)$', line)
            if match:
                address = int(match[1], 16)
                for word in match[2].split():
                    words[address] = int(word, 16)
                    address += 4
        level = (words[0x58020410] >> 7) & 1
        case = dict(duty=duty, pb7=level, reply=reply, hardware=result.stdout, passed=False)
        doc['cases'].append(case)
        assert words[0x58020400] >> 14 & 3 == 2, 'PB7 is not alternate function'
        assert words[0x5802040c] >> 14 & 3 == 2, 'Expected the native LED pull-down'
        assert words[0x58020420] >> 28 & 15 == 1, 'PB7 does not select AF1 TIM17'
        assert words[0x40014818] & 0x78 == 0x68, 'Expected native PWM1 with preload'
        assert words[0x40014820] & 15 == 4, 'Expected native complementary output without extra inversion'
        assert words[0x40014828] == 9 and words[0x4001482c] == 99
        assert words[0x40014834] == duty and level == int(duty == 100), 'Incorrect LED polarity'
        case['passed'] = True
        args.out.write_text(json.dumps(doc, indent=2) + '\n')
    doc['stop'] = client.cli('ledpinpwm')
    doc['passed'] = all(case['passed'] for case in doc['cases'])
finally:
    try:
        if entered:
            doc['exit'] = client.cli('exit')
    finally:
        client.close()
        doc['msp_packets'] = client.packets
        args.out.write_text(json.dumps(doc, indent=2) + '\n')
client = connect(args.port, args.uid)
client.close()
print(json.dumps(dict(passed=doc['passed'], pin='PB7', levels=[case['pb7'] for case in doc['cases']])))
