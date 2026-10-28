"""UID-bound, disarmed-only native PX4 MAVLink and NSH access."""
import math
import os
import re
import secrets
import struct
import time

os.environ.setdefault('MAVLINK20', '1')
from pymavlink import mavutil
from serial.tools import list_ports


class SafetyStop(RuntimeError):
    pass


class PX4Client:
    def __init__(self, port, uid, timeout=45):
        self.uid = bytes.fromhex(uid)
        assert len(self.uid) == 12
        self.link = None
        self.last_gcs = 0
        self.last_heartbeat = 0
        self.latest = {}
        self.texts = []
        errors = []
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                available = list(list_ports.comports())
                actual = next((p for p in available if p.device == port), None)
                if actual is None:
                    matches = [p for p in available if (p.vid, p.pid) == (0x1209, 0x0001)]
                    if len(matches) > 1:
                        raise SafetyStop('Multiple prototype PX4 USB devices; select an explicit port')
                    if not matches:
                        time.sleep(.25)
                        continue
                    actual = matches[0]
                if (actual.vid, actual.pid) != (0x1209, 0x0001):
                    raise SafetyStop('Unexpected prototype USB identity')
                self.port = actual.device
                self.usb_serial = actual.serial_number
                self.link = mavutil.mavlink_connection(self.port, baud=115200, source_system=254,
                    source_component=191, autoreconnect=False)
                self.last_gcs = 0
                hb = self.wait('HEARTBEAT', timeout=6)
                self.system = hb.get_srcSystem()
                self.component = hb.get_srcComponent()
                self.version = self.request_message(148, 'AUTOPILOT_VERSION', timeout=6)
                # Native STM32 PX4 GUID: SoC prefix, zero padding, reversed MCU UID bytes.
                if bytes(self.version.uid2)[-12:] != self.uid[::-1]:
                    raise SafetyStop('Native PX4 GUID does not match the selected MCU UID')
                if self.version.flight_sw_version >> 8 != 0x011100:
                    raise SafetyStop('Expected PX4 v1.17.0')
                return
            except SafetyStop:
                self.close()
                raise
            except Exception as error:
                errors.append(repr(error)); self.close(); time.sleep(.3)
        raise TimeoutError('Native PX4 connection: '+repr(errors[-3:]))

    def close(self):
        if self.link is not None:
            try:
                self.link.mav.serial_control_send(10, 0, 0, 0, 0, [0] * 70)
            except Exception:
                pass
            self.link.close(); self.link = None

    def receive(self, timeout=.2):
        if time.monotonic() - self.last_gcs >= 1:
            self.link.mav.heartbeat_send(6, 8, 0, 0, 4)
            self.last_gcs = time.monotonic()
        message = self.link.recv_match(blocking=True, timeout=timeout)
        if message is None or message.get_srcComponent() != 1:
            return None
        name = message.get_type()
        if name == 'HEARTBEAT':
            if message.autopilot != mavutil.mavlink.MAV_AUTOPILOT_PX4:
                raise SafetyStop('Unexpected autopilot heartbeat')
            if message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
                raise SafetyStop('Armed heartbeat; stop verification')
            self.last_heartbeat = time.monotonic()
        self.latest[name] = message.to_dict()
        if name == 'STATUSTEXT':
            self.texts.append(message.to_dict())
        return message

    def wait(self, name, predicate=None, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            message = self.receive(min(.2, end-time.monotonic()))
            if message is not None and message.get_type() == name and (predicate is None or predicate(message)):
                return message
        raise TimeoutError(name)

    def confirm_disarmed(self):
        if time.monotonic() - self.last_heartbeat > 2:
            self.wait('HEARTBEAT', timeout=5)

    def request_message(self, number, name, timeout=5):
        self.confirm_disarmed()
        self.link.mav.command_long_send(self.system, self.component,
            mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE, 0, number, 0, 0, 0, 0, 0, 0)
        return self.wait(name, timeout=timeout)

    def interval(self, number, microseconds):
        self.confirm_disarmed()
        self.link.mav.command_long_send(self.system, self.component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0, number, microseconds, 0, 0, 0, 0, 0)
        reply = self.wait('COMMAND_ACK', lambda m: m.command == mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL)
        assert reply.result == mavutil.mavlink.MAV_RESULT_ACCEPTED, reply.to_dict()

    @staticmethod
    def parameter_value(message):
        if message.param_type == mavutil.mavlink.MAV_PARAM_TYPE_INT32:
            return struct.unpack('<i', struct.pack('<f', message.param_value))[0]
        assert message.param_type == mavutil.mavlink.MAV_PARAM_TYPE_REAL32, message.to_dict()
        return message.param_value

    def read_param(self, name):
        assert re.fullmatch('[A-Z0-9_]{1,16}', name)
        self.confirm_disarmed()
        for attempt in range(3):
            self.link.mav.param_request_read_send(self.system, self.component, name.encode('ascii'), -1)
            try:
                return self.wait('PARAM_VALUE', lambda m: m.param_id == name, timeout=2)
            except TimeoutError:
                if attempt == 2:
                    raise TimeoutError('Parameter '+name)

    def params(self, names):
        return {name:self.parameter_value(self.read_param(name)) for name in names}

    def set_param(self, name, value):
        before = self.read_param(name)
        encoded = value
        if before.param_type == mavutil.mavlink.MAV_PARAM_TYPE_INT32:
            assert int(value) == value
            encoded = struct.unpack('<f', struct.pack('<i', int(value)))[0]
        self.confirm_disarmed()
        self.link.mav.param_set_send(self.system, self.component, name.encode('ascii'), encoded, before.param_type)
        reply = self.wait('PARAM_VALUE', lambda m:m.param_id == name)
        actual = self.parameter_value(reply)
        assert math.isclose(actual, value, rel_tol=1e-6, abs_tol=1e-7), (name, value, actual)
        checked = self.parameter_value(self.read_param(name))
        assert math.isclose(checked, value, rel_tol=1e-6, abs_tol=1e-7)
        return dict(name=name,before=self.parameter_value(before),after=checked)

    def shell(self, command, timeout=12):
        assert command and '\n' not in command and '\r' not in command and ';' not in command
        self.confirm_disarmed()
        marker='ECF_DONE_'+secrets.token_hex(6)
        data=('\n'+command+'\necho '+marker+'\n').encode('ascii')
        flags=mavutil.mavlink.SERIAL_CONTROL_FLAG_EXCLUSIVE | mavutil.mavlink.SERIAL_CONTROL_FLAG_RESPOND
        for offset in range(0,len(data),70):
            chunk=data[offset:offset+70]
            self.link.mav.serial_control_send(10,flags,0,0,len(chunk),list(chunk)+[0]*(70-len(chunk)))
        result=''; end=time.monotonic()+timeout
        while time.monotonic()<end:
            message=self.receive(.1)
            if message is not None and message.get_type()=='SERIAL_CONTROL' and message.count:
                result+=bytes(message.data[:message.count]).decode(errors='replace')
                if re.search(r'[\r\n]'+marker+r'[\r\n]',result):
                    return result
                echoed=re.search(r'echo '+marker+r'[\r\n]+',result)
                if echoed:
                    tail=re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',result[echoed.end():])
                    tail=re.sub(r'(?:INFO|WARN|ERROR|DEBUG)\s+\[[^\]]+\][^\r\n]*(?:[\r\n]+|$)','',tail)
                    if marker in re.sub(r'[\r\n\t]','',tail):
                        return result
        raise TimeoutError('Native NSH command '+command+': '+repr(result[-500:]))

    def reboot(self):
        self.confirm_disarmed()
        self.link.mav.command_long_send(self.system,self.component,
            mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,0,1,0,0,0,0,0,0)
        self.close()

    def enter_rom_dfu(self):
        self.confirm_disarmed()
        data=b'\nreboot -i\n'
        flags=mavutil.mavlink.SERIAL_CONTROL_FLAG_EXCLUSIVE | mavutil.mavlink.SERIAL_CONTROL_FLAG_RESPOND
        self.link.mav.serial_control_send(10,flags,0,0,len(data),list(data)+[0]*(70-len(data)))
        self.link.port.flush()
        time.sleep(.3)
        self.close()
