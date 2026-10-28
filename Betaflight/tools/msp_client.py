"""Minimal native MSP/CLI client for disarmed board validation."""
import struct
import time

import serial


def crc8(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = ((value << 1) ^ (0xD5 if value & 0x80 else 0)) & 255
    return value


class Client:
    def __init__(self, port, expected_uid, timeout=3):
        self.port = serial.Serial(port, 115200, timeout=.05, write_timeout=3)
        self.timeout = timeout
        self.packets = []
        self.port.reset_input_buffer()
        try:
            uid = self.request(160).hex()
            if uid.lower() != expected_uid.lower():
                raise RuntimeError(f"Unexpected MCU UID: {uid}")
            self.uid = uid
            self.guard()
        except BaseException:
            self.close()
            raise

    def close(self):
        self.port.close()

    def request(self, command, payload=b"", v2=False):
        use_v2 = v2 or command > 254 or len(payload) > 254
        if use_v2:
            body = struct.pack("<BHH", 0, command, len(payload)) + payload
            packet = b"$X<" + body + bytes([crc8(body)])
        else:
            body = bytes([len(payload), command]) + payload
            check = 0
            for byte in body:
                check ^= byte
            packet = b"$M<" + body + bytes([check])
        self.port.write(packet)
        result = bytearray()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            result.extend(self.port.read(max(1, self.port.in_waiting)))
            starts = [i for prefix in (b"$M>", b"$M!", b"$X>", b"$X!") if (i := result.find(prefix)) >= 0]
            if not starts:
                continue
            start = min(starts)
            if len(result) < start + 6:
                continue
            if result[start + 1] == 77:
                size = result[start + 3]
                received = result[start + 4]
                end = start + 6 + size
                if len(result) < end:
                    continue
                check = 0
                for byte in result[start + 3:end]:
                    check ^= byte
                valid = check == 0
                data = bytes(result[start + 5:end - 1])
            else:
                if len(result) < start + 9:
                    continue
                _, received, size = struct.unpack_from("<BHH", result, start + 3)
                end = start + 9 + size
                if len(result) < end:
                    continue
                valid = crc8(result[start + 3:end - 1]) == result[end - 1]
                data = bytes(result[start + 8:end - 1])
            if not valid or received != command:
                raise RuntimeError(f"Invalid MSP response: {bytes(result).hex()}")
            self.packets.append(dict(command=command, request=payload.hex(), response=data.hex(), v2=use_v2))
            if result[start + 2] == 33:
                raise RuntimeError(f"MSP rejected command {command}")
            return data
        raise TimeoutError(f"MSP {command}: {bytes(result).hex()}")

    def guard(self):
        status = self.request(101)
        if int.from_bytes(status[6:10], "little") & 1:
            raise RuntimeError("Board is armed")
        return status

    def enter_cli(self):
        self.guard()
        self.port.write(b"#")
        return self.read_cli()

    def read_cli(self, reboot=False):
        data = bytearray()
        deadline = time.monotonic() + (5 if reboot else 20)
        last = time.monotonic()
        while time.monotonic() < deadline:
            try:
                chunk = self.port.read(max(1, self.port.in_waiting))
            except serial.SerialException:
                if reboot:
                    return data.decode(errors="replace")
                raise
            if chunk:
                data.extend(chunk)
                last = time.monotonic()
            # A dump contains comment prefixes identical to the prompt. Only
            # accept the final prompt after the producer has gone quiet.
            if (data.endswith(b"# ") and time.monotonic() - last > .12) or (reboot and data and time.monotonic() - last > .3):
                return data.decode(errors="replace")
        if reboot and data:
            return data.decode(errors="replace")
        raise TimeoutError(f"CLI prompt missing: {data[-120:]!r}")

    def cli(self, command):
        self.port.write(command.encode() + b"\r\n")
        return self.read_cli(reboot=command.split()[0] in ("save", "exit", "defaults", "msc", "bl", "dfu"))


def connect(port, expected_uid, wait=35):
    deadline = time.monotonic() + wait
    while True:
        try:
            return Client(port, expected_uid)
        except (serial.SerialException, TimeoutError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(.3)
