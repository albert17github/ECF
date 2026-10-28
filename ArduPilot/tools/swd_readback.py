"""UID-checked peripheral readback and hardware reset; no Flash or option writes."""
# AP_FLAKE8_CLEAN
import os
import re
import subprocess


class SwdReadback:
    def __init__(self, uid, serial, cube=None):
        raw = bytes.fromhex(uid)
        assert len(raw) == 12
        self.expected = [int.from_bytes(raw[i:i + 4], "little") for i in range(0, 12, 4)]
        self.cube = cube or os.environ.get("ECF_CUBE_PROGRAMMER") or (
            "C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe")
        self.connection = ["-c", "port=SWD", f"sn={serial}", "mode=HOTPLUG", "freq=950"]

    def _run(self, arguments):
        result = subprocess.run([self.cube, *self.connection, *arguments], capture_output=True,
                                text=True, errors="replace", timeout=25)
        if result.returncode != 0:
            raise RuntimeError(result.stdout + result.stderr)
        words = {}
        for line in result.stdout.splitlines():
            match = re.match(r"0x([\da-fA-F]{8})\s*:\s*((?:[\da-fA-F]{8}\s*)+)$", line)
            if match:
                for offset, word in enumerate(match[2].split()):
                    words[int(match[1], 16) + offset * 4] = int(word, 16)
        return dict(words=words, stdout=result.stdout, connection=list(self.connection), arguments=arguments)

    def read(self, blocks=()):
        arguments = ["-r32", "0x1ff1e800", "12"]
        for address, length in blocks:
            assert address % 4 == 0 and length % 4 == 0 and 4 <= length <= 4096
            arguments += ["-r32", hex(address), str(length)]
        result = self._run(arguments)
        assert [result["words"][0x1ff1e800 + i * 4] for i in range(3)] == self.expected, "Different MCU"
        return result

    def hard_reset(self, under_reset_fallback=False):
        connection = self.connection
        first_error = None
        try:
            try:
                guard = self.read()
            except RuntimeError as error:
                if not under_reset_fallback:
                    raise
                first_error = str(error)
                self.connection = [value for value in connection if not value.startswith(("mode=", "freq="))]
                self.connection += ["mode=UR", "reset=HWrst", "freq=100"]
                guard = self.read()
            result = self._run(["-hardRst"])
            assert "Hard reset is performed" in result["stdout"]
            return dict(uid_guard=guard, reset=result, hotplug_error=first_error)
        finally:
            self.connection = connection
