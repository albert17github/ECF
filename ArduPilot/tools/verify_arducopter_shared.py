"""Exercise four native shared-interface modes on an unloaded ECFH743 bench."""
# AP_FLAKE8_CLEAN
import argparse
import datetime
import json
from pathlib import Path
from bench_connections import require_unwired_shared_pads
require_unwired_shared_pads() 
import time

from arducopter_client import APClient
from swd_readback import SwdReadback

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--stlink-sn", required=True)
parser.add_argument("--unloaded-bench", action="store_true", required=True)
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
assert not args.out.exists()
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), cases=[], passed=False)
client = None
swd = SwdReadback(args.uid, args.stlink_sn)


def persist():
    args.out.write_text(json.dumps(doc, indent=2) + "\n")


def restart(changes, item):
    global client
    item["changes"] = [client.set_param(name, value) for name, value in changes.items()]
    persist()
    client.reboot()
    client = None
    time.sleep(1)
    client = APClient(args.port, args.uid)
    doc["board_options"] = client.read_param("BRD_OPTIONS").param_value
    assert int(doc["board_options"]) & 8, "Enable native BRD_OPTIONS debug bit for live SWD readback"
    client.interval(1, 200000)
    status = client.wait("SYS_STATUS", lambda m: m.onboard_control_sensors_health & 15 == 15, timeout=30)
    item["sensor_health_mask"] = status.onboard_control_sensors_health & 15
    item["parameters"] = client.params(changes)
    assert item["parameters"] == changes


def pins():
    result = swd.read([(0x58020800, 40), (0x58020c00, 40), (0x58021000, 40),
                       (0x40022000, 64), (0x58026000, 16)])
    words = result["words"]
    states = {}
    for bank, base, numbers in [("C", 0x58020800, (4, 5)), ("D", 0x58020c00, (8, 9, 12, 13)),
                                ("E", 0x58021000, (0, 1))]:
        for pin in numbers:
            states[f"P{bank}{pin}"] = dict(mode=words[base] >> (pin * 2) & 3,
                                          pull=words[base + 12] >> (pin * 2) & 3,
                                          open_drain=words[base + 4] >> pin & 1,
                                          alternate=words[base + 32 + pin // 8 * 4] >> (pin % 8 * 4) & 15)
    return dict(raw=result, pins=states, adc1_cr=words[0x40022008], adc1_sqr1=words[0x40022030],
                adc3_cr=words[0x58026008])


def analog(state, names):
    for name in names:
        assert state[name]["mode"] == 3 and state[name]["pull"] == 0, (name, state[name])


try:
    client = APClient(args.port, args.uid)
    changed = ["BRD_ALT_CONFIG", "SERIAL3_PROTOCOL", "SERIAL8_PROTOCOL", "BATT_MONITOR"]
    doc["initial_parameters"] = client.params(changed)
    assert doc["initial_parameters"] == dict(BRD_ALT_CONFIG=1, SERIAL3_PROTOCOL=-1, SERIAL8_PROTOCOL=-1, BATT_MONITOR=4)
    for mode, monitor in [(mode, monitor) for monitor in (4, 21) for mode in range(4)]:
        uart3 = bool(mode & 1)
        uart8 = bool(mode & 2)
        item = dict(mode=mode, uart3=uart3, uart8=uart8, monitor=monitor)
        doc["cases"].append(item)
        restart(dict(BRD_ALT_CONFIG=mode, SERIAL3_PROTOCOL=2 if uart3 else -1,
                     SERIAL8_PROTOCOL=2 if uart8 else -1, BATT_MONITOR=monitor), item)
        if monitor == 4:
            item["adc_parameters"] = client.params(["BATT_VOLT_PIN", "BATT_CURR_PIN", "BATT_VOLT_MULT", "BATT_AMP_PERVLT"])
            assert item["adc_parameters"] == dict(BATT_VOLT_PIN=8, BATT_CURR_PIN=4, BATT_VOLT_MULT=11, BATT_AMP_PERVLT=50)
        else:
            item["ina_parameters"] = client.params(["BATT_I2C_BUS", "BATT_I2C_ADDR", "BATT_MAX_AMPS", "BATT_SHUNT"])
            assert item["ina_parameters"]["BATT_I2C_BUS"] == 2
            assert item["ina_parameters"]["BATT_I2C_ADDR"] == 69
            assert item["ina_parameters"]["BATT_MAX_AMPS"] == 120
            assert abs(item["ina_parameters"]["BATT_SHUNT"] - .0005) < 1e-8
        item["readback"] = pins()
        state = item["readback"]["pins"]
        analog(state, ("PC4", "PC5"))
        if uart3:
            analog(state, ("PD12", "PD13"))
            for name in ("PD8", "PD9"):
                assert state[name]["mode"] == 2 and state[name]["alternate"] == 7, (name, state[name])
        else:
            analog(state, ("PD8", "PD9"))
            for name in ("PD12", "PD13"):
                assert state[name]["mode"] == 2 and state[name]["alternate"] == 4 and state[name]["open_drain"] == 1
        if uart8:
            for name in ("PE0", "PE1"):
                assert state[name]["mode"] == 2 and state[name]["alternate"] == 8, (name, state[name])
            assert item["readback"]["adc1_cr"] & 1 == 0, "ADC1 active on UART8 shared pads"
        else:
            analog(state, ("PE0", "PE1"))
            assert item["readback"]["adc1_cr"] & 1, "ADC1 did not start"
            sqr = item["readback"]["adc1_sqr1"]
            assert sqr & 15 == 1 and {sqr >> 6 & 31, sqr >> 12 & 31} == {4, 8}, hex(sqr)
        assert item["readback"]["adc3_cr"] & 1, "Native board-voltage ADC stopped"
        client.interval(147, 200000)
        item["battery_status"] = client.wait("BATTERY_STATUS", lambda m: m.id == 0).to_dict()
        item["system_status"] = client.wait("SYS_STATUS").to_dict()
        item["battery_source_healthy"] = bool(item["system_status"]["onboard_control_sensors_health"] & (1 << 25))
        if monitor == 4:
            assert item["battery_source_healthy"] == (not uart8), "Shared ADC availability does not match native battery health"
        elif uart3:
            assert not item["battery_source_healthy"], "INA backend used an unselected shared I2C bus"
        item["passed"] = True
        persist()
        print(json.dumps(dict(mode=mode, monitor=monitor, passed=True, adc1_cr=hex(item["readback"]["adc1_cr"]))), flush=True)
    doc["restore"] = {}
    restart(doc["initial_parameters"], doc["restore"])
    doc["restore"]["readback"] = pins()
    doc["passed"] = True
except Exception as error:
    doc["error"] = repr(error)
    raise
finally:
    if client:
        client.close()
    persist()
print(json.dumps(dict(passed=doc["passed"], cases=len(doc["cases"]))))
