"""Validate native eight-output ownership and timer protocol combinations, disarmed."""
import argparse
import datetime
import json
import re
import struct
import subprocess
from pathlib import Path

from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--stlink-sn", required=True)
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
if args.out.exists():
    raise FileExistsError(args.out)
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), passed=False, steps=[], physical_waveform_verified=False)
pins = ["A00", "A01", "A02", "A03", "B01", "B00", "D14", "D15"]

def persist():
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def configure(commands):
    client = connect(args.port, args.uid)
    result = {}
    try:
        client.enter_cli()
        for command in commands:
            reply = client.cli(command)
            result[command] = reply
            assert "###ERROR" not in reply, reply
        return result
    finally:
        client.close()

def timer_registers(bitbang=False):
    command = ["C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe", "-c", "port=SWD", f"sn={args.stlink_sn}", "mode=HOTPLUG", "freq=950", "-r32", "0x1FF1E800", "12"]
    definitions = [(8, 0x40010400)] if bitbang else [(2, 0x40000000), (3, 0x40000400), (4, 0x40000800)]
    for _, address in definitions:
        command += ["-r32", hex(address), "64"]
    result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=20)
    assert result.returncode == 0
    uid = bytes.fromhex(args.uid)
    assert " ".join(f"{int.from_bytes(uid[i:i+4], 'little'):08X}" for i in range(0, 12, 4)) in result.stdout.upper()
    words = {}
    for line in result.stdout.splitlines():
        match = re.match(r"0x([\da-fA-F]{8})\s*:\s*((?:[\da-fA-F]{8}\s*)+)$", line)
        if match:
            address = int(match[1], 16)
            for value in match[2].split():
                words[address] = int(value, 16)
                address += 4
    timers = {str(index): dict(cr1=words[base], dier=words[base + 12], ccer=words[base + 32], prescaler=words[base + 40], auto_reload=words[base + 44]) for index, base in definitions}
    return dict(command=command, stdout=result.stdout, decoded=timers)

def snapshot(name, commands, owners, protocol):
    item = dict(name=name, configure=configure([*commands, "save"]))
    doc["steps"].append(item)
    persist()
    client = connect(args.port, args.uid)
    try:
        item["identity"] = {str(command): client.request(command).hex() for command in (1, 2, 3, 5, 160)}
        item["motor_values"] = list(struct.unpack("<8H", client.request(104)[:16]))
        item["servo_values"] = list(struct.unpack("<8H", client.request(103)[:16]))
        active_motors = 8 if name.startswith("eight_") else 4
        assert all(value >= 1000 for value in item["motor_values"][:active_motors]), item["motor_values"]
        client.enter_cli()
        item["cli"] = {command: client.cli(command) for command in ["status", "mixer", "resource show all", "timer", "dma", "get motor_pwm_protocol", "get servo_pwm_rate", "get dshot", "diff all"]}
        assert not any("###ERROR" in reply for reply in item["cli"].values())
        for pin, owner in owners.items():
            assert f"{pin}: {owner}" in item["cli"]["resource show all"], (pin, owner)
        assert f"motor_pwm_protocol = {protocol}" in item["cli"]["get motor_pwm_protocol"]
        item["timers"] = timer_registers(bitbang="bitbang" in name)
        if "bitbang" in name:
            assert "TIM1: FREE" in item["cli"]["resource show all"]
            assert "DSHOT_BITBANG" in item["cli"]["resource show all"]
            assert "dshot_bidir = ON" in item["cli"]["get dshot"]
            assert "dshot_bitbang_timer = TIM8" in item["cli"]["get dshot"]
            item["esc_telemetry_link_verified"] = False
        if "eight_dshot" in name:
            for index in (2, 3, 4):
                assert re.search(rf"(?:DMA[12] Stream \d+: TIMUP {index})", item["cli"]["resource show all"]), f"TIM{index} DMA burst not active"
        item["passed"] = True
        client.cli("exit")
    finally:
        client.close()
        persist()
    print(json.dumps(dict(step=name, passed=True)), flush=True)

try:
    doc["baseline_reset"] = configure(["defaults"])
    rear_servo = [f"resource MOTOR {index + 5} NONE" for index in range(4)]
    rear_servo += [f"resource SERVO {index + 1} {pins[index + 4]}" for index in range(4)]
    mixed_owners = {pin: f"MOTOR {index + 1}" if index < 4 else f"SERVO {index - 3}" for index, pin in enumerate(pins)}
    snapshot("four_dshot_four_pwm", [*rear_servo, "feature CHANNEL_FORWARDING", "set servo_pwm_rate = 50", "set motor_pwm_protocol = DSHOT300", "set dshot_bitbang = OFF", "set dshot_burst = ON"], mixed_owners, "DSHOT300")
    motor8 = [f"resource SERVO {index + 1} NONE" for index in range(4)]
    motor8 += [f"resource MOTOR {index + 5} {pins[index + 4]}" for index in range(4)]
    motor_owners = {pin: f"MOTOR {index + 1}" for index, pin in enumerate(pins)}
    snapshot("eight_dshot", [*motor8, "feature -CHANNEL_FORWARDING", "mixer OCTOX8", "set motor_pwm_protocol = DSHOT300", "set dshot_burst = ON"], motor_owners, "DSHOT300")
    snapshot("eight_pwm", ["set motor_pwm_protocol = PWM", "set motor_pwm_rate = 400"], motor_owners, "PWM")
    snapshot("eight_bidir_bitbang", ["set motor_pwm_protocol = DSHOT300", "set dshot_bitbang = AUTO", "set dshot_bidir = ON"], motor_owners, "DSHOT300")
    doc["restore"] = configure(["defaults"])
    client = connect(args.port, args.uid)
    client.close()
    doc["passed"] = True
finally:
    persist()
print(json.dumps(dict(passed=doc["passed"], combinations=len(doc["steps"]))))
