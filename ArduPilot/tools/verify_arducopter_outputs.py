"""Check native output modes, timers and DMA on an unloaded ECFH743 bench."""
# AP_FLAKE8_CLEAN
import argparse
import datetime
import json
from pathlib import Path
import re
import time

from arducopter_client import APClient
from arducopter_ftp import read_file
from swd_readback import SwdReadback

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--stlink-sn", required=True)
parser.add_argument("--unloaded-bench", action="store_true", required=True)
parser.add_argument("--require-native-bidir-state", action="store_true")
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
assert not args.out.exists()
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), cases=[], passed=False,
           external_esc_telemetry_tested=False, physical_waveform_verified=False)
client = None
swd = SwdReadback(args.uid, args.stlink_sn)


def persist():
    args.out.write_text(json.dumps(doc, indent=2) + "\n")


def apply_and_restart(changes, item):
    global client
    item["changes"] = [client.set_param(name, value) for name, value in changes.items()]
    persist()
    client.reboot()
    client = None
    time.sleep(1)
    client = APClient(args.port, args.uid)
    client.interval(1, 200000)
    client.wait("SYS_STATUS", lambda m: m.onboard_control_sensors_health & 15 == 15, timeout=30)
    item["parameters"] = client.params(changes)
    assert item["parameters"] == changes


def hardware():
    result = swd.read([(0x40000000, 80), (0x40000400, 80), (0x40000800, 80),
                       (0x40010000, 80), (0x40014800, 80), (0x40020000, 208), (0x40020400, 208),
                       (0x58020000, 40), (0x58020400, 40), (0x58020c00, 40)])
    words = result["words"]
    result["timers"] = {
        str(number): {key: words[base + offset] for key, offset in [("CR1", 0), ("DIER", 12), ("CCMR1", 24),
                                                                 ("CCMR2", 28), ("CCER", 32),
                                                                 ("PSC", 40), ("ARR", 44), ("DCR", 72)]}
        for number, base in [(2, 0x40000000), (3, 0x40000400), (4, 0x40000800), (1, 0x40010000), (17, 0x40014800)]}
    result["dma_targets"] = [words[base + 24 + stream * 24] for base in (0x40020000, 0x40020400) for stream in range(8)]
    return result


def observe_bidirectional(initial):
    snapshots = [initial]
    evidence = {str(n): dict(transmit_period=False, capture_period=False,
                             capture_dma=False, input_mode=False) for n in (2, 3, 4)}
    for attempt in range(24):
        if attempt:
            snapshots.append(hardware())
        for number in (2, 3, 4):
            timer = snapshots[-1]["timers"][str(number)]
            observed = evidence[str(number)]
            observed["transmit_period"] |= 0 < timer["ARR"] < 256
            observed["capture_period"] |= timer["ARR"] == 65535
            observed["capture_dma"] |= bool(timer["DIER"] & 0x1e00)
            ccmr = timer["CCMR2"] | (timer["CCMR1"] if number == 2 else 0)
            observed["input_mode"] |= bool(ccmr & 0x0303)
        if all(all(row.values()) for row in evidence.values()):
            return dict(per_timer=evidence, snapshots=snapshots, register_reads_are_not_atomic=True)
    raise AssertionError(dict(missing_bidirectional_phase_evidence=evidence, snapshots=snapshots))


try:
    client = APClient(args.port, args.uid)
    assert int(client.read_param("BRD_OPTIONS").param_value) & 8, "Native debug bit required for live SWD"
    names = ["MOT_PWM_TYPE", "SERVO_BLH_MASK", "SERVO_BLH_OTYPE", "SERVO_BLH_BDMASK"]
    names += [f"SERVO{i}_FUNCTION" for i in range(5, 9)]
    doc["initial_parameters"] = client.params(names)
    for label, motor_type, mask, override, bidi in [("four_dshot_four_pwm", 5, 0, 0, 0),
                                                   ("eight_dshot", 5, 255, 5, 0),
                                                   ("eight_pwm", 0, 0, 0, 0),
                                                   ("eight_bidir_dshot", 5, 255, 5, 255)]:
        item = dict(name=label)
        doc["cases"].append(item)
        changes = dict(MOT_PWM_TYPE=motor_type, SERVO_BLH_MASK=mask, SERVO_BLH_OTYPE=override, SERVO_BLH_BDMASK=bidi)
        changes.update({f"SERVO{i}_FUNCTION": 1 for i in range(5, 9)})
        apply_and_restart(changes, item)
        raw, evidence = read_file(client, "@SYS/timers.txt", maximum_bytes=65536)
        item["native_timers"] = raw.decode()
        item["timer_transfer"] = evidence
        item["readback"] = hardware()
        if bidi:
            item["bidirectional_phases"] = observe_bidirectional(item["readback"])
        modes = {int(number): mode for number, mode in re.findall(r"TIM(\d+)\s+CLK=\s*\d+Mhz MODE=\s*(\S+)", raw.decode())}
        expected = {2: "DS300" if motor_type else "PWM", 3: "DS300" if mask else "PWM",
                    4: "DS300" if mask else "PWM", 17: "NeoP"}
        assert modes == expected, (modes, expected)
        timers = item["readback"]["timers"]
        assert timers["1"]["CR1"] & 1 == 0 and timers["1"]["CCER"] == 0, "TIM1 reservation changed"
        for number in (2, 3, 4):
            timer = timers[str(number)]
            if expected[number] == "PWM":
                assert timer["CR1"] & 1 and timer["ARR"] >= 1000 and timer["DIER"] & 0x100 == 0
                assert timer["CCER"] & (0x1111 if number == 2 else 0x1100) == (0x1111 if number == 2 else 0x1100)
            else:
                if bidi:
                    assert all(item["bidirectional_phases"]["per_timer"][str(number)].values())
                else:
                    assert timer["ARR"] < 256 and timer["DCR"] != 0
                if not bidi:
                    target = {2: 0x4000004c, 3: 0x4000044c, 4: 0x4000084c}[number]
                    assert target in item["readback"]["dma_targets"], (number, item["readback"]["dma_targets"])
        native = re.search(r"ECF_DSHOT_V1 MASK=([0-9a-fA-F]+) DISABLED=([0-9a-fA-F]+)", raw.decode())
        item["native_bidir_state_verified"] = native is not None
        if native is not None:
            assert int(native[1], 16) == bidi and int(native[2], 16) == 0, native[0]
            groups = {int(number): (int(channels, 16), int(enabled)) for number, channels, enabled
                      in re.findall(r"ECF_TIMER_V1 TIM=(\d+) CHANNELS=([0-9a-fA-F]+) BIDIR=(\d+)", raw.decode())}
            for number in (2, 3, 4):
                assert groups[number][1] == bool(bidi), groups
        elif args.require_native_bidir_state:
            raise RuntimeError("The matching native bidirectional runtime diagnostic is required")
        item["texts"] = client.texts
        assert not any("failed to" in row["text"].lower() or "Config Error" in row["text"] for row in client.texts)
        item["passed"] = True
        persist()
        print(json.dumps(dict(case=label, passed=True, modes=modes, native_bidir_state_verified=native is not None)), flush=True)
    doc["restore"] = {}
    apply_and_restart(doc["initial_parameters"], doc["restore"])
    doc["passed"] = True
except Exception as error:
    doc["error"] = repr(error)
    if client:
        doc["failure_texts"] = client.texts
        doc["failure_latest"] = client.latest
    raise
finally:
    if client:
        client.close()
    persist()
print(json.dumps(dict(passed=doc["passed"], combinations=len(doc["cases"]))))
