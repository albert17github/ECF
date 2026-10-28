"""Verify native disarmed sensor updates, identity and board defaults."""
# AP_FLAKE8_CLEAN
import argparse
import datetime
import json
import math
from pathlib import Path
import time

from arducopter_client import APClient

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--out", required=True, type=Path)
parser.add_argument("--seconds", type=float, default=120)
args = parser.parse_args()
assert not args.out.exists()
assert args.seconds >= 20
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), passed=False, samples=[])
client = None


def persist():
    args.out.write_text(json.dumps(doc, indent=2) + "\n")


try:
    client = APClient(args.port, args.uid)
    doc["version"] = client.version.to_dict()
    doc["uid2"] = bytes(client.version.uid2).hex()
    names = ["INS_ACC_ID", "INS_GYR_ID", "COMPASS_DEV_ID", "BARO1_DEVID", "INS_ENABLE_MASK",
             "AHRS_ORIENTATION", "BRD_ALT_CONFIG", "BRD_OPTIONS", "FRAME_CLASS", "FRAME_TYPE",
             "MOT_PWM_TYPE", "ARMING_SKIPCHK",
             "BATT_MONITOR", "BATT_VOLT_PIN", "BATT_CURR_PIN", "BATT_VOLT_MULT", "BATT_AMP_PERVLT", "BATT_AMP_OFFSET",
             "LOG_BACKEND_TYPE", "LOG_DISARMED", "LOG_FILE_MB_FREE", "NTF_LED_TYPES", "NTF_LED_LEN",
             "NTF_LED_BRIGHT", "NTF_LED_OVERRIDE"]
    names += [f"SERVO{i}_FUNCTION" for i in range(1, 10)]
    names += [f"SERIAL{i}_PROTOCOL" for i in (0, 1, 3, 4, 5, 6, 7, 8)]
    doc["parameters"] = client.params(names)
    parameters = doc["parameters"]
    for name in ("INS_ACC_ID", "INS_GYR_ID", "COMPASS_DEV_ID", "BARO1_DEVID"):
        assert parameters[name] > 0, (name, parameters[name])
    expected = dict(INS_ENABLE_MASK=1, AHRS_ORIENTATION=0, BRD_ALT_CONFIG=1, BRD_OPTIONS=1, FRAME_CLASS=1, FRAME_TYPE=1,
                    MOT_PWM_TYPE=5, ARMING_SKIPCHK=0, BATT_MONITOR=4, BATT_VOLT_PIN=8, BATT_CURR_PIN=4,
                    BATT_VOLT_MULT=11, BATT_AMP_PERVLT=50, BATT_AMP_OFFSET=0, LOG_BACKEND_TYPE=1, LOG_DISARMED=0,
                    LOG_FILE_MB_FREE=16, NTF_LED_TYPES=256, NTF_LED_LEN=1, NTF_LED_BRIGHT=1, NTF_LED_OVERRIDE=0)
    expected.update({f"SERVO{i}_FUNCTION": 32 + i if i <= 4 else (120 if i == 9 else 0) for i in range(1, 10)})
    expected.update({f"SERIAL{i}_PROTOCOL": value for i,value in {0:2,1:23,3:-1,4:5,5:2,6:5,7:16,8:-1}.items()})
    doc["expected_defaults"] = expected
    doc["default_mismatches"] = {name: dict(expected=value, actual=parameters[name])
                                 for name, value in expected.items()
                                 if not math.isclose(parameters[name], value, rel_tol=1e-6, abs_tol=1e-8)}
    assert not doc["default_mismatches"], doc["default_mismatches"]
    streams = {1: "SYS_STATUS", 27: "RAW_IMU", 29: "SCALED_PRESSURE", 30: "ATTITUDE"}
    for message_id in streams:
        client.interval(message_id, 200000)
    started = time.monotonic()
    next_save = started + 15
    while time.monotonic() - started < args.seconds:
        message = client.receive(.2)
        if message is not None and message.get_type() in set(streams.values()) | {"HEARTBEAT", "STATUSTEXT"}:
            doc["samples"].append(dict(elapsed=time.monotonic() - started, **message.to_dict()))
        if time.monotonic() >= next_save:
            persist()
            print(json.dumps(dict(elapsed=round(time.monotonic() - started, 1), samples=len(doc["samples"]))), flush=True)
            next_save += 15
    doc["seconds"] = time.monotonic() - started
    doc["texts"] = client.texts
    doc["stream_results"] = {}
    for name in streams.values():
        rows = [row for row in doc["samples"] if row["mavpackettype"] == name]
        gaps = [b["elapsed"] - a["elapsed"] for a, b in zip(rows, rows[1:])]
        doc["stream_results"][name] = dict(samples=len(rows), max_gap_seconds=max(gaps, default=0))
        assert len(rows) >= args.seconds * 3, (name, len(rows))
        assert max(gaps, default=0) < 1.2, (name, max(gaps))
    imu = [row for row in doc["samples"] if row["mavpackettype"] == "RAW_IMU"]
    pressures = [row for row in doc["samples"] if row["mavpackettype"] == "SCALED_PRESSURE"]
    status = [row for row in doc["samples"] if row["mavpackettype"] == "SYS_STATUS"]
    assert len({row["time_usec"] for row in imu}) == len(imu)
    assert all(500 < math.sqrt(sum(row[key] ** 2 for key in ("xacc", "yacc", "zacc"))) < 1500 for row in imu)
    assert all(any(row[key] != 0 for key in ("xmag", "ymag", "zmag")) for row in imu)
    # RAW_IMU quantizes gyro to 1 mrad/s: a stationary, calibrated rig can be all zero.
    # Fresh sample times and native gyro health remain mandatory below.
    doc['raw_gyro_distinct_vectors'] = len({tuple(row[key] for key in ("xgyro", "ygyro", "zgyro")) for row in imu})
    assert all(abs(row[key]) < 1000 for row in imu for key in ("xgyro", "ygyro", "zgyro")), 'Unexpected stationary rotation rate'
    assert len({tuple(row[key] for key in ("xmag", "ymag", "zmag")) for row in imu}) > 1
    assert all(300 < row["press_abs"] < 1250 and -4000 <= row["temperature"] <= 8500 for row in pressures)
    assert len({row["time_boot_ms"] for row in pressures}) == len(pressures)
    assert all(row["onboard_control_sensors_present"] & 15 == 15 for row in status)
    assert all(row["onboard_control_sensors_health"] & 15 == 15 for row in status)
    doc["maximum_load_permille"] = max(row["load"] for row in status)
    doc["battery_monitor_health"] = dict(
        healthy_samples=sum(bool(row["onboard_control_sensors_health"] & (1 << 25)) for row in status),
        samples=len(status), physical_monitor_tested=False)
    assert doc["maximum_load_permille"] < 800
    assert not any("Config Error" in row.get("text", "") for row in client.texts)
    client.confirm_disarmed()
    doc["latest"] = {name: client.latest.get(name) for name in streams.values()}
    doc["passed"] = True
except Exception as error:
    doc["error"] = repr(error)
    if client:
        doc["texts"] = client.texts
        doc["latest"] = client.latest
    raise
finally:
    if client:
        client.close()
    persist()
print(json.dumps({key: doc.get(key) for key in ("passed", "seconds", "stream_results", "maximum_load_permille", "error")}))
