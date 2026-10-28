"""Check three native reboots, sensor recovery and parameter persistence."""
# AP_FLAKE8_CLEAN
import argparse
import datetime
import json
from pathlib import Path
import time

from arducopter_client import APClient

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--uid", required=True)
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
assert not args.out.exists()
doc = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), cycles=[], passed=False)
client = None


def persist():
    args.out.write_text(json.dumps(doc, indent=2) + "\n")


def ready(client, minimum_uptime=0):
    client.interval(30, 200000)
    client.interval(1, 200000)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        message = client.receive()
        if message is not None and message.get_type() == "ATTITUDE" and message.time_boot_ms >= minimum_uptime:
            status = client.latest.get("SYS_STATUS")
            if status and status["onboard_control_sensors_health"] & 15 == 15:
                return message.time_boot_ms
    raise TimeoutError("Initialized sensors and application uptime")


try:
    client = APClient(args.port, args.uid)
    names = ["MOT_PWM_TYPE", "LOG_DISARMED", "ARMING_SKIPCHK", "BRD_ALT_CONFIG",
             "INS_ACC_ID", "INS_GYR_ID", "COMPASS_DEV_ID", "BARO1_DEVID"]
    names += [f"SERVO{i}_FUNCTION" for i in range(1, 10)]
    doc["parameters_before"] = client.params(names)
    for index in range(3):
        before = ready(client, 20000)
        bootcount = client.read_param("STAT_BOOTCNT").param_value
        item = dict(index=index + 1, before_time_boot_ms=before, before_bootcount=bootcount)
        doc["cycles"].append(item)
        persist()
        client.reboot()
        client = None
        time.sleep(1)
        client = APClient(args.port, args.uid)
        after = ready(client)
        bootcount_after = client.read_param("STAT_BOOTCNT").param_value
        item.update(after_time_boot_ms=after, after_bootcount=bootcount_after,
                    uptime_restarted=after < before, healthy_sensor_mask=client.latest["SYS_STATUS"]["onboard_control_sensors_health"] & 15,
                    parameters=client.params(names), texts=client.texts)
        assert after < before and bootcount_after == bootcount + 1, item
        assert item["parameters"] == doc["parameters_before"]
        assert not any("Config Error" in row["text"] for row in client.texts)
        item["passed"] = True
        persist()
        print(json.dumps(dict(cycle=index + 1, passed=True, before_ms=before, after_ms=after)), flush=True)
    doc["passed"] = True
except Exception as error:
    doc["error"] = repr(error)
    raise
finally:
    if client:
        client.close()
    persist()
print(json.dumps(dict(passed=doc["passed"], cycles=len(doc["cycles"]))))
