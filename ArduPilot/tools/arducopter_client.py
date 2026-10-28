"""Native MAVLink access for disarmed ECFH743 board verification."""
# AP_FLAKE8_CLEAN
import math
import os
import time

os.environ.setdefault("MAVLINK20", "1")
from pymavlink import mavutil
from serial.tools import list_ports


class SafetyStop(RuntimeError):
    pass


class APClient:
    def __init__(self, port, uid, timeout=35):
        self.port = port
        self.uid = bytes.fromhex(uid)
        assert len(self.uid) == 12
        self.link = None
        self.last_gcs = 0
        self.latest = {}
        self.texts = []
        self.counts = {}
        self.last_heartbeat = 0
        self.version = None
        deadline = time.monotonic() + timeout
        errors = []
        while time.monotonic() < deadline:
            try:
                available = list(list_ports.comports())
                actual = next((p for p in available if p.device == port), None)
                if actual is None:
                    matches = [p for p in available
                               if (p.vid, p.pid, p.serial_number) == (0x1209, 0x5741, uid.lower())]
                    if len(matches) > 1:
                        raise SafetyStop("Multiple USB devices match the selected controller")
                    if not matches:
                        time.sleep(.25)
                        continue
                    actual = matches[0]
                if (actual.vid, actual.pid, actual.serial_number) != (0x1209, 0x5741, uid.lower()):
                    raise SafetyStop("USB identity differs from the selected controller")
                self.port = actual.device
                self.link = mavutil.mavlink_connection(
                    self.port, baud=115200, source_system=254, source_component=191, autoreconnect=False)
                self.last_gcs = 0
                heartbeat = self.wait("HEARTBEAT", timeout=5)
                if heartbeat.autopilot != mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA:
                    raise SafetyStop("Unexpected autopilot")
                self.system = heartbeat.get_srcSystem()
                self.component = heartbeat.get_srcComponent()
                self.version = self.request_message(148, "AUTOPILOT_VERSION", timeout=5)
                if bytes(self.version.uid2)[:12] != self.uid:
                    raise SafetyStop("Native UID2 differs from the selected controller")
                if self.version.flight_sw_version >> 8 != 0x040702:
                    raise SafetyStop("Expected ArduCopter 4.7.2")
                return
            except SafetyStop:
                self.close()
                raise
            except Exception as error:
                errors.append(str(error))
                self.close()
                time.sleep(.25)
        raise TimeoutError("Native ArduCopter connection: " + repr(errors[-3:]))

    def close(self):
        if self.link is not None:
            self.link.close()
            self.link = None

    def receive(self, timeout=.2):
        now = time.monotonic()
        if now - self.last_gcs >= 1:
            self.link.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                0, 0, mavutil.mavlink.MAV_STATE_ACTIVE)
            self.last_gcs = now
        message = self.link.recv_match(blocking=True, timeout=timeout)
        if message is None or message.get_srcComponent() != 1:
            return None
        name = message.get_type()
        if name == "HEARTBEAT":
            if message.autopilot != mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA:
                raise SafetyStop("Unexpected native heartbeat")
            if message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
                raise SafetyStop("Armed heartbeat; stop verification")
            self.last_heartbeat = time.monotonic()
        self.latest[name] = message.to_dict()
        self.counts[name] = self.counts.get(name, 0) + 1
        if name == "STATUSTEXT":
            self.texts.append(message.to_dict())
        return message

    def wait(self, name, predicate=None, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.receive(min(.2, deadline - time.monotonic()))
            if message is not None and message.get_type() == name and (predicate is None or predicate(message)):
                return message
        raise TimeoutError(name)

    def confirm_disarmed(self):
        if time.monotonic() - self.last_heartbeat > 2:
            self.wait("HEARTBEAT", timeout=4)

    def request_message(self, message_id, name, timeout=4):
        self.confirm_disarmed()
        self.link.mav.command_long_send(
            self.system, self.component, mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
            0, message_id, 0, 0, 0, 0, 0, 0)
        return self.wait(name, timeout=timeout)

    def interval(self, message_id, microseconds):
        self.confirm_disarmed()
        self.link.mav.command_long_send(
            self.system, self.component, mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
            0, message_id, microseconds, 0, 0, 0, 0, 0)
        reply = self.wait("COMMAND_ACK", lambda m: m.command == mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL)
        if reply.result != mavutil.mavlink.MAV_RESULT_ACCEPTED:
            raise RuntimeError("Message interval rejected: " + str(reply.result))

    def read_param(self, name, attempts=3):
        self.confirm_disarmed()
        for attempt in range(attempts):
            self.link.mav.param_request_read_send(self.system, self.component, name.encode("ascii"), -1)
            try:
                return self.wait("PARAM_VALUE", lambda m: m.param_id == name, timeout=2)
            except TimeoutError:
                if attempt + 1 == attempts:
                    raise TimeoutError("Parameter " + name)

    def set_param(self, name, value):
        before = self.read_param(name)
        self.confirm_disarmed()
        self.link.mav.param_set_send(self.system, self.component, name.encode("ascii"), value, before.param_type)
        reply = self.wait("PARAM_VALUE", lambda m: m.param_id == name)
        if not math.isclose(reply.param_value, value, rel_tol=1e-6, abs_tol=1e-6):
            raise RuntimeError(f"Parameter {name} rejected {value}: {reply.param_value}")
        after = self.read_param(name)
        if not math.isclose(after.param_value, value, rel_tol=1e-6, abs_tol=1e-6):
            raise RuntimeError("Parameter readback differs: " + name)
        return dict(name=name, before=before.param_value, after=after.param_value, type=after.param_type)

    def params(self, names):
        return {name: self.read_param(name).param_value for name in names}

    def reboot(self):
        self.confirm_disarmed()
        self.link.mav.command_long_send(
            self.system, self.component, mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
            0, 1, 0, 0, 0, 0, 0, 0)
        self.close()

    def sample(self, seconds, names):
        samples = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            message = self.receive(min(.2, end - time.monotonic()))
            if message is not None and message.get_type() in names:
                samples.append(dict(host_monotonic=time.monotonic(), **message.to_dict()))
        self.confirm_disarmed()
        return samples
