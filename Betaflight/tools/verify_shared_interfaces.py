"""Verify ECF shared-pad protection through native settings and GPIO readback."""
import argparse
import datetime
import json
import re
import subprocess
from pathlib import Path
from bench_connections import require_unwired_shared_pads
require_unwired_shared_pads() 

from msp_client import connect

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--stlink-sn", required=True)
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
if args.out.exists():
    raise FileExistsError(args.out)
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), steps=[], passed=False)

def persist():
    args.out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def cli_commands(commands):
    client = connect(args.port, args.uid)
    responses = {}
    try:
        client.enter_cli()
        for command in commands:
            responses[command] = client.cli(command)
            if "###ERROR" in responses[command] or "Unknown command" in responses[command]:
                raise RuntimeError(responses[command])
        return responses
    finally:
        client.close()

def gpio():
    cube = "C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe"
    command = [cube, "-c", "port=SWD", f"sn={args.stlink_sn}", "mode=HOTPLUG", "freq=950", "-r32", "0x1FF1E800", "12"]
    for address in (0x58020800, 0x58020C00, 0x58021000):
        command += ["-r32", hex(address), "40"]
    command += ["-r32", "0x40022008", "4"]
    result = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=20)
    assert result.returncode == 0
    uid = bytes.fromhex(args.uid)
    expected_words = " ".join(f"{int.from_bytes(uid[i:i + 4], 'little'):08X}" for i in range(0, 12, 4))
    assert expected_words in result.stdout.upper()
    words = {}
    for line in result.stdout.splitlines():
        match = re.match(r"0x([\da-fA-F]{8})\s*:\s*((?:[\da-fA-F]{8}\s*)+)$", line)
        if match:
            address = int(match[1], 16)
            for word in match[2].split():
                words[address] = int(word, 16)
                address += 4
    modes = {}
    for name, address, pins in [("C", 0x58020800, (4, 5)), ("D", 0x58020C00, (8, 9, 12, 13)), ("E", 0x58021000, (0, 1))]:
        for pin in pins:
            modes[f"P{name}{pin}"] = dict(mode=(words[address] >> (pin * 2)) & 3, pull=(words[address + 12] >> (pin * 2)) & 3, open_drain=(words[address + 4] >> pin) & 1)
    return dict(command=command, stdout=result.stdout, modes=modes, adc1_cr=words[0x40022008])

def step(name, configure, required=(), absent=(), analog=(), alternate=(), tx_monitor=(), adc=None):
    item = dict(name=name, configure=cli_commands([*configure, "save"]))
    doc["steps"].append(item)
    persist()
    client = connect(args.port, args.uid)
    try:
        client.enter_cli()
        item["snapshot"] = {command: client.cli(command) for command in ["status", "resource", "serial", "get battery_meter", "get current_meter", "get i2c4", "get dronecan", "dma", "tasks"]}
        assert not any("###ERROR" in response for response in item["snapshot"].values())
        item["gpio"] = gpio()
        if adc is not None:
            assert bool(item["gpio"]["adc1_cr"] & 1) == adc, (name, item["gpio"]["adc1_cr"])
        resources = item["snapshot"]["resource"]
        for expected in required:
            assert expected in resources, expected
        for forbidden in absent:
            assert forbidden not in resources, forbidden
        for pin in analog:
            assert item["gpio"]["modes"][pin]["mode"] == 3, (pin, item["gpio"]["modes"][pin])
            assert item["gpio"]["modes"][pin]["pull"] == 0, pin
        for pin in alternate:
            assert item["gpio"]["modes"][pin]["mode"] == 2, (pin, item["gpio"]["modes"][pin])
        for pin in tx_monitor:
            state = item["gpio"]["modes"][pin]
            assert state["mode"] == 2 or (state["mode"] == 0 and state["pull"] == 1), (pin, state)
        if tx_monitor:
            item["tx_idle_note"] = "Native MSP SERIAL_CHECK_TX keeps an idle, unused TX as a pulled-up input until transmission; RX is in alternate-function mode. No physical TX/RX transfer is asserted."
        item["passed"] = True
        client.cli("exit")
    finally:
        client.close()
        persist()
    print(json.dumps(dict(step=name, passed=True)), flush=True)

try:
    doc["reset_to_test_baseline"] = cli_commands(["defaults"])
    step("factory_adc_uart3", [], required=("resource SERIAL_TX 3 D08", "resource SERIAL_RX 3 D09", "resource ADC_BATT 1 C05", "resource ADC_CURR 1 C04"), absent=("resource I2C_SCL 4 D12", "resource I2C_SDA 4 D13"), analog=("PD12", "PD13", "PE0", "PE1", "PC4", "PC5"), adc=True)
    step("adc_i2c4", ["serial UART5 0 115200 57600 0 115200", "resource SERIAL_TX 3 NONE", "resource SERIAL_RX 3 NONE", "serial UART3 0 115200 57600 0 115200", "resource I2C_SCL 4 D12", "resource I2C_SDA 4 D13", "set i2c4_clockspeed_khz = 400"], required=("resource I2C_SCL 4 D12", "resource I2C_SDA 4 D13", "resource ADC_BATT 1 C05", "resource ADC_CURR 1 C04"), analog=("PD8", "PD9", "PE0", "PE1", "PC4", "PC5"), alternate=("PD12", "PD13"), adc=True)
    step("uart8_i2c4", ["resource SERIAL_TX 8 E01", "resource SERIAL_RX 8 E00", "serial UART8 1 115200 57600 0 115200", "set battery_meter = NONE", "set current_meter = NONE"], required=("resource SERIAL_TX 8 E01", "resource SERIAL_RX 8 E00", "resource I2C_SCL 4 D12", "resource I2C_SDA 4 D13"), absent=("resource ADC_BATT 1 C05", "resource ADC_CURR 1 C04"), analog=("PD8", "PD9", "PC4", "PC5"), alternate=("PD12", "PD13", "PE0"), tx_monitor=("PE1",), adc=False)
    step("uart3_overrides_conflicting_i2c4", ["resource SERIAL_TX 3 D08", "resource SERIAL_RX 3 D09", "serial UART3 1 115200 57600 0 115200"], required=("resource SERIAL_TX 3 D08", "resource SERIAL_RX 3 D09"), absent=("resource I2C_SCL 4 D12", "resource I2C_SDA 4 D13"), analog=("PD12", "PD13"), alternate=("PD9",), tx_monitor=("PD8",))
    step("return_adc_uart3", ["resource SERIAL_TX 8 NONE", "resource SERIAL_RX 8 NONE", "serial UART8 0 115200 57600 0 115200", "resource ADC_BATT 1 C05", "resource ADC_CURR 1 C04", "set battery_meter = ADC", "set current_meter = ADC"], required=("resource ADC_BATT 1 C05", "resource ADC_CURR 1 C04", "resource SERIAL_TX 3 D08", "resource SERIAL_RX 3 D09"), analog=("PD12", "PD13", "PE0", "PE1", "PC4", "PC5"), alternate=("PD9",), tx_monitor=("PD8",), adc=True)
    step("uart8_overrides_conflicting_adc", ["resource SERIAL_TX 8 E01", "resource SERIAL_RX 8 E00", "serial UART8 1 115200 57600 0 115200", "set battery_meter = NONE", "set current_meter = NONE"], required=("resource SERIAL_TX 8 E01", "resource SERIAL_RX 8 E00"), absent=("resource ADC_BATT 1 C05", "resource ADC_CURR 1 C04"), analog=("PC4", "PC5", "PD12", "PD13"), alternate=("PE0", "PD9"), tx_monitor=("PE1", "PD8"), adc=False)
    step("can1_native_configuration", ["set dronecan_enabled = ON", "set dronecan_device = 1"])
    step("can2_native_configuration", ["set dronecan_device = 2"])
    doc["restore"] = cli_commands(["defaults"])
    doc["passed"] = True
finally:
    persist()
print(json.dumps(dict(passed=doc["passed"], steps=len(doc["steps"]))))
