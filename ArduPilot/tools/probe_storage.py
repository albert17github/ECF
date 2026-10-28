#!/usr/bin/env python3
"""Small, disarmed-only MAVFTP storage probe for PX4 and ArduPilot.

Creates one random private test directory and one file within it. The directory
is necessary because MAVFTP CreateFile truncates existing files on both servers.
All FTP requests are serialized; sessions always come from matching Open/Create
ACKs. Existing paths are never uploaded to, truncated, renamed, or deleted.

Example:
  python probe_storage.py --port COM20 --remote-dir /fs/microsd --output result.json
  python probe_storage.py --decode-wire capture.bin --output decoded.json
"""

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import struct
import time
from datetime import datetime, timezone


HEADER = struct.Struct("<HBBBBBBI")
MAX_DATA = 239
NAMES = {
    1: "TerminateSession", 2: "ResetSessions", 4: "OpenFileRO", 5: "ReadFile",
    6: "CreateFile", 7: "WriteFile", 8: "RemoveFile", 9: "CreateDirectory",
    10: "RemoveDirectory", 15: "BurstReadFile", 128: "ACK", 129: "NAK",
}
ERRORS = {1: "Fail", 2: "FailErrno", 3: "InvalidDataSize", 4: "InvalidSession",
          5: "NoSessionsAvailable", 6: "EOF", 7: "UnknownCommand",
          8: "FileExists", 9: "FileProtected", 10: "FileNotFound"}


class ProbeError(Exception):
    pass


class SafetyStop(ProbeError):
    pass


class FtpNak(ProbeError):
    def __init__(self, opcode, data):
        self.opcode = opcode
        self.code = data[0] if data else None
        self.errno = data[1] if len(data) > 1 else None
        super().__init__(f"{NAMES.get(opcode, opcode)}: {ERRORS.get(self.code, self.code)}"
                         + (f" errno={self.errno}" if self.errno is not None else ""))

    def missing(self):
        return self.code == 10 or (self.code == 2 and self.errno == 2)

    def exists(self):
        return self.code == 8 or (self.code == 2 and self.errno == 17)


def unpack(payload):
    raw = bytes(payload)
    if len(raw) < HEADER.size:
        raise ProbeError("Short FTP header")
    seq, session, opcode, size, request, burst, padding, offset = HEADER.unpack_from(raw)
    if size > MAX_DATA or len(raw) < HEADER.size + size:
        raise ProbeError("Invalid FTP payload length")
    return {"seq": seq, "session": session, "opcode": opcode, "size": size,
            "request_opcode": request, "burst_complete": burst, "offset": offset,
            "data": raw[HEADER.size:HEADER.size + size]}


def packet_summary(packet):
    result = {key: value for key, value in packet.items() if key != "data"}
    result["name"] = NAMES.get(packet["opcode"], str(packet["opcode"]))
    result["request_name"] = NAMES.get(packet["request_opcode"], str(packet["request_opcode"]))
    if packet["data"]:
        result["data_sha256"] = hashlib.sha256(packet["data"]).hexdigest()
    if packet["opcode"] == 129:
        result["error_bytes"] = list(packet["data"])
    elif packet["request_opcode"] in (4, 6) and len(packet["data"]) == 4:
        result["file_size"] = struct.unpack("<I", packet["data"])[0]
    return result


class Evidence:
    def __init__(self, data, path=None):
        self.data = data
        self.started = time.monotonic()
        self.file = open(path, "x", encoding="utf-8") if path is not None else None

    def event(self, kind, **fields):
        self.data.setdefault("events", []).append(
            {"elapsed_s": round(time.monotonic() - self.started, 6), "event": kind, **fields})

    def save(self):
        if self.file is not None:
            self.file.seek(0)
            json.dump(self.data, self.file, indent=2, ensure_ascii=False)
            self.file.write("\n")
            self.file.truncate()
            self.file.flush()

    def close(self):
        self.save()
        if self.file is not None:
            self.file.close()


class FtpClient:
    def __init__(self, connection, evidence, source_system=254, source_component=191,
                 target_system=None, target_component=1, timeout=1.5, retries=3,
                 heartbeat_max_age=3.0, initial_sequence=None):
        self.connection = connection
        self.evidence = evidence
        self.source = (source_system, source_component)
        self.target = (target_system, target_component)
        self.timeout = timeout
        self.retries = retries
        self.heartbeat_max_age = heartbeat_max_age
        self.sequence = secrets.randbelow(65534) if initial_sequence is None else initial_sequence
        self.last_heartbeat = None
        self.heartbeat = None
        self.armed_seen = False
        self.session = None
        self.session_outcome_unknown = False
        self.next_gcs_heartbeat = 0.0

    def _observe_heartbeat(self, message):
        if message.get_type() != "HEARTBEAT":
            return
        system, component = message.get_srcSystem(), message.get_srcComponent()
        if component != self.target[1] or (self.target[0] is not None and system != self.target[0]):
            return
        if message.autopilot not in (3, 12):
            return
        self.target = (system, component)
        self.last_heartbeat = time.monotonic()
        self.heartbeat = {"system": system, "component": component,
                          "autopilot": message.autopilot, "base_mode": message.base_mode,
                          "system_status": message.system_status, "type": message.type}
        if message.base_mode & 128:
            self.armed_seen = True
            raise SafetyStop("Armed heartbeat received; no further FTP operations")

    def confirm_disarmed(self, timeout=15.0):
        if self.armed_seen:
            raise SafetyStop("Armed state was observed")
        if self.last_heartbeat is not None and time.monotonic() - self.last_heartbeat <= self.heartbeat_max_age:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.connection.recv_match(blocking=True, timeout=min(0.2, deadline - time.monotonic()))
            if message is None:
                continue
            self._observe_heartbeat(message)
            if self.last_heartbeat is not None and time.monotonic() - self.last_heartbeat <= self.heartbeat_max_age:
                self.evidence.event("disarmed_heartbeat", **self.heartbeat)
                return
        raise SafetyStop("No fresh disarmed autopilot heartbeat")

    def request(self, opcode, *, session=0, offset=0, data=b"", read_size=None):
        self.confirm_disarmed()
        size = len(data) if read_size is None else read_size
        if not 0 <= size <= MAX_DATA or len(data) > MAX_DATA:
            raise ProbeError("FTP request exceeds payload capacity")
        sequence = self.sequence
        expected_sequence = (sequence + 1) & 0xFFFF
        payload = HEADER.pack(sequence, session, opcode, size, 0, 0, 0, offset) + data
        payload += bytes(251 - len(payload))
        try:
            for attempt in range(self.retries + 1):
                self.confirm_disarmed()
                self.evidence.event("tx", opcode=opcode, name=NAMES.get(opcode), seq=sequence,
                                    session=session, offset=offset, size=size, attempt=attempt,
                                    data_sha256=hashlib.sha256(data).hexdigest() if data else None)
                self.connection.mav.file_transfer_protocol_send(0, self.target[0], self.target[1], payload)
                deadline = time.monotonic() + self.timeout
                received_counts = collections.Counter()
                while time.monotonic() < deadline:
                    # Keep the normal GCS heartbeat present during FTP traffic.
                    # It also completes a short USB transfer after a request
                    # whose MAVLink 2 encoding ends on a full USB packet.
                    if time.monotonic() >= self.next_gcs_heartbeat and hasattr(self.connection.mav, "heartbeat_send"):
                        self.connection.mav.heartbeat_send(6, 8, 0, 0, 4)
                        self.next_gcs_heartbeat = time.monotonic() + 1.0
                    message = self.connection.recv_match(
                        blocking=True, timeout=max(0.001, min(0.2, deadline - time.monotonic())))
                    if message is None:
                        continue
                    received_counts[message.get_type()] += 1
                    self._observe_heartbeat(message)
                    if message.get_type() != "FILE_TRANSFER_PROTOCOL":
                        continue
                    if (message.get_srcSystem(), message.get_srcComponent()) != self.target:
                        self.evidence.event("ignored_ftp", reason="source",
                                            actual=[message.get_srcSystem(), message.get_srcComponent()])
                        continue
                    if (message.target_system, message.target_component) != self.source:
                        self.evidence.event("ignored_ftp", reason="target",
                                            actual=[message.target_system, message.target_component],
                                            expected=list(self.source))
                        continue
                    reply = unpack(message.payload)
                    if reply["seq"] != expected_sequence or reply["request_opcode"] != opcode:
                        self.evidence.event("ignored_ftp", reason="sequence_or_opcode", **packet_summary(reply))
                        continue
                    self.evidence.event("rx", **packet_summary(reply))
                    if reply["opcode"] == 129:
                        raise FtpNak(opcode, reply["data"])
                    if reply["opcode"] != 128:
                        raise ProbeError("Expected FTP ACK/NAK")
                    if opcode in (1, 5, 7) and reply["session"] != session:
                        raise ProbeError("FTP ACK session mismatch")
                    if opcode in (5, 7) and reply["offset"] != offset:
                        raise ProbeError("FTP ACK offset mismatch")
                    return reply
                self.evidence.event("timeout", opcode=opcode, seq=sequence, attempt=attempt,
                                    received_counts=dict(received_counts))
            raise ProbeError(f"{NAMES.get(opcode, opcode)} timed out; remote outcome may be unknown")
        finally:
            self.sequence = (expected_sequence + 1) & 0xFFFF

    def close_session(self):
        if self.session is not None:
            session = self.session
            self.request(1, session=session)
            self.session = None
            self.evidence.event("session_closed", session=session)

    def open_file(self, path, write=False):
        if self.session is not None:
            raise ProbeError("Previous file session is still open")
        self.session_outcome_unknown = True
        reply = self.request(6 if write else 4, data=path_payload(path))
        self.session = reply["session"]
        self.session_outcome_unknown = False
        self.evidence.event("session_opened", session=self.session, write=write, path=path)
        return reply


def path_payload(path):
    encoded = path.encode("ascii")
    if len(encoded) + 1 > MAX_DATA or any(ord(c) < 32 for c in path) or "\x7f" in path:
        raise ProbeError("Remote path is too long or contains control characters")
    return encoded + b"\0"


def normalized_root(root):
    if not root or ".." in PurePosixPath(root).parts or "@" in root or "\\" in root:
        raise ProbeError("Use a normal remote directory without '..', '@', or backslashes")
    result = str(PurePosixPath(root))
    path_payload(result + "/ecf_" + "0" * 32 + "/probe.bin")
    return result.rstrip("/")


def probe(client, remote_root, size=4096, chunk_size=128, reset_sessions=False):
    if not 1 <= size <= 65536 or not 32 <= chunk_size <= MAX_DATA:
        raise ProbeError("size must be 1..65536 and chunk-size 32..239")
    evidence = client.evidence
    client.confirm_disarmed()
    evidence.data["initial_heartbeat"] = client.heartbeat
    root = normalized_root(remote_root)
    seed = secrets.token_bytes(32)
    payload = hashlib.shake_256(seed).digest(size)
    expected_hash = hashlib.sha256(payload).hexdigest()
    evidence.data.update(size_bytes=size, expected_sha256=expected_hash, seed_hex=seed.hex(),
                         payload_algorithm="SHAKE256(seed).digest(size)", passed=False,
                         write_readback_verified=False, cleanup_complete=False)
    owned_directory = None
    file_path = None
    file_may_exist = False
    cleanup_errors = []
    failure = None
    if reset_sessions:
        client.request(2)
        evidence.event("explicit_reset_sessions")
    try:
        for _attempt in range(3):
            # Keep the component below NuttX's default NAME_MAX=40 while
            # retaining 128 random bits and the ownership ACK requirement.
            directory = root + "/ecf_" + secrets.token_hex(16)
            evidence.data["attempted_directory"] = directory
            evidence.save()
            try:
                client.request(9, data=path_payload(directory))
            except FtpNak as exc:
                if exc.exists():
                    evidence.event("name_collision_untouched", path=directory)
                    continue
                raise
            owned_directory = directory
            break
        if owned_directory is None:
            raise ProbeError("Could not allocate a unique directory; collision paths untouched")
        file_path = owned_directory + "/probe.bin"
        evidence.data.update(remote_directory=owned_directory, remote_file=file_path)
        evidence.event("private_directory_created", path=owned_directory)
        evidence.save()
        file_may_exist = True
        client.open_file(file_path, write=True)
        for offset in range(0, size, chunk_size):
            chunk = payload[offset:offset + chunk_size]
            reply = client.request(7, session=client.session, offset=offset, data=chunk)
            if reply["size"] not in (0, 4):
                raise ProbeError("Unexpected WriteFile ACK size")
            if reply["size"] == 4 and struct.unpack("<I", reply["data"])[0] != len(chunk):
                raise ProbeError("Server reported a short write")
        evidence.event("write_acknowledged", bytes=size)
        client.close_session()
        evidence.event("write_session_closed_before_read")
        evidence.save()
        opened = client.open_file(file_path)
        if opened["size"] != 4 or struct.unpack("<I", opened["data"])[0] != size:
            raise ProbeError("Read-open file length differs from written length")
        received = bytearray()
        while len(received) < size:
            requested = min(chunk_size, size - len(received))
            reply = client.request(5, session=client.session, offset=len(received), read_size=requested)
            if not 0 < reply["size"] <= requested:
                raise ProbeError("Invalid or empty read before expected EOF")
            received.extend(reply["data"])
        try:
            eof = client.request(5, session=client.session, offset=size, read_size=1)
            if eof["size"] != 0:
                raise ProbeError("Unexpected bytes after expected EOF")
        except FtpNak as exc:
            if exc.code != 6:
                raise
        client.close_session()
        actual_hash = hashlib.sha256(received).hexdigest()
        evidence.data.update(read_bytes=len(received), actual_sha256=actual_hash)
        if received != payload or actual_hash != expected_hash:
            raise ProbeError("Read-back content/hash mismatch")
        evidence.data["write_readback_verified"] = True
        evidence.event("sha256_verified", sha256=actual_hash)
    except (Exception, KeyboardInterrupt) as exc:
        failure = exc
        evidence.data["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if client.session is not None:
            try:
                client.close_session()
            except Exception as exc:
                cleanup_errors.append("close: " + str(exc))
        if owned_directory is not None:
            if file_may_exist:
                try:
                    client.request(8, data=path_payload(file_path))
                    evidence.event("test_file_removed", path=file_path)
                except FtpNak as exc:
                    if not exc.missing():
                        cleanup_errors.append("remove file: " + str(exc))
                except Exception as exc:
                    cleanup_errors.append("remove file: " + str(exc))
            try:
                client.request(10, data=path_payload(owned_directory))
                evidence.event("empty_test_directory_removed", path=owned_directory)
            except Exception as exc:
                cleanup_errors.append("remove directory: " + str(exc))
        if client.session_outcome_unknown:
            cleanup_errors.append("Open/Create ACK not confirmed; a remote session may remain")
        evidence.data["cleanup_errors"] = cleanup_errors
        evidence.data["cleanup_complete"] = owned_directory is not None and not cleanup_errors
        evidence.data["passed"] = failure is None and evidence.data["write_readback_verified"] and not cleanup_errors
        if owned_directory is None and failure is not None:
            evidence.data["directory_creation_outcome"] = "unconfirmed; no path deleted without ownership ACK"
        evidence.save()
    return evidence.data["passed"]


def decode_wire(path):
    from pymavlink.dialects.v20 import ardupilotmega
    raw = Path(path).read_bytes()
    parser = ardupilotmega.MAVLink(None)
    parser.robust_parsing = True
    messages = parser.parse_buffer(raw) or []
    controls, bursts = [], []
    counts = collections.Counter()
    for index, message in enumerate(messages):
        counts[message.get_type()] += 1
        if message.get_type() != "FILE_TRANSFER_PROTOCOL":
            continue
        packet = unpack(message.payload)
        summary = {"message_index": index, "source": [message.get_srcSystem(), message.get_srcComponent()],
                   "target": [message.target_system, message.target_component], **packet_summary(packet)}
        if packet["request_opcode"] == 15:
            bursts.append(summary)
        else:
            controls.append(summary)
    return {"mode": "offline_decode", "wire": str(path), "wire_bytes": len(raw),
            "wire_sha256": hashlib.sha256(raw).hexdigest(), "message_counts": dict(counts),
            "control_packets": controls, "burst_reply_count": len(bursts),
            "burst_max_offset": max((b["offset"] for b in bursts), default=None),
            "contains_outgoing_ftp": any(p["opcode"] < 128 for p in controls + bursts)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--port", help="Explicit serial/UDP endpoint; no port discovery is performed")
    mode.add_argument("--decode-wire", type=Path, help="Offline MAVLink capture decoding; never connects")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--remote-dir", help="Default: /fs/microsd for PX4, / for ArduPilot")
    parser.add_argument("--size", type=int, default=4096)
    parser.add_argument("--chunk-size", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=1.5)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--target-system", type=int)
    parser.add_argument("--target-component", type=int, default=1)
    parser.add_argument("--source-system", type=int, default=254)
    parser.add_argument("--source-component", type=int, default=191)
    parser.add_argument("--reset-sessions", action="store_true",
                        help="Explicitly close server FTP sessions first; use only with exclusive FTP access")
    parser.add_argument("--output", type=Path, help="New JSON file; existing files are never overwritten")
    args = parser.parse_args()
    if not 1 <= args.size <= 65536 or not 32 <= args.chunk_size <= MAX_DATA:
        parser.error("--size must be 1..65536 and --chunk-size 32..239")
    if not 0.05 <= args.timeout <= 30 or not 0 <= args.retries <= 10:
        parser.error("--timeout must be 0.05..30 and --retries 0..10")
    for value in (args.target_system, args.target_component, args.source_system, args.source_component):
        if value is not None and not 1 <= value <= 255:
            parser.error("MAVLink system/component IDs must be 1..255")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or Path(f"ecf_storage_probe_{stamp}_{secrets.token_hex(4)}.json")
    data = {"tool": "ecf.probe_storage", "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "hardware_validation": "not_started", "format_requested": False, "events": []}
    evidence = Evidence(data, output)
    connection = None
    passed = False
    try:
        if args.decode_wire is not None:
            data.update(decode_wire(args.decode_wire))
            data["hardware_validation"] = "not_attempted"
            passed = True
        else:
            os.environ.setdefault("MAVLINK20", "1")
            from pymavlink import mavutil
            connection = mavutil.mavlink_connection(
                args.port, baud=args.baud, source_system=args.source_system,
                source_component=args.source_component, autoreconnect=False)
            client = FtpClient(connection, evidence, args.source_system, args.source_component,
                               args.target_system, args.target_component, args.timeout, args.retries)
            client.confirm_disarmed()
            remote = args.remote_dir or ("/fs/microsd" if client.heartbeat["autopilot"] == 12 else "/")
            data.update(port=args.port, baud=args.baud, remote_root=remote,
                        hardware_validation="started", reset_sessions_requested=args.reset_sessions)
            passed = probe(client, remote, args.size, args.chunk_size, args.reset_sessions)
            data["hardware_validation"] = "passed" if passed else "failed_or_incomplete"
    except (Exception, KeyboardInterrupt) as exc:
        data["error"] = {"type": type(exc).__name__, "message": str(exc)}
        data["passed"] = False
    finally:
        if connection is not None:
            connection.close()
        data["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        evidence.close()
    print(json.dumps({"passed": passed, "evidence": str(output),
                      "hardware_validation": data["hardware_validation"], "error": data.get("error")},
                     ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
