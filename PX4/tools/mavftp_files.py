"""UID-bound, read-only MAVFTP file access using the shared protocol client."""
# AP_FLAKE8_CLEAN
from pathlib import Path
import struct
import sys
import time

try:
    from probe_storage import Evidence, FtpClient, FtpNak, unpack
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from probe_storage import Evidence, FtpClient, FtpNak, unpack


def read_file(client, path, maximum_bytes=4 * 1024 * 1024):
    client.confirm_disarmed()
    evidence = Evidence(dict(path=path, read_only=True))
    ftp = FtpClient(client.link, evidence, target_system=client.system, target_component=client.component)
    try:
        opened = ftp.open_file(path)
        assert len(opened["data"]) == 4
        size = struct.unpack("<I", opened["data"])[0]
        virtual = path.startswith(("@SYS/", "@PARAM/"))
        if not virtual:
            assert size <= maximum_bytes, (path, size)
        evidence.data["advertised_bytes"] = size
        evidence.data["virtual_file_size_advisory"] = virtual
        data = bytearray()
        while virtual or len(data) < size:
            wanted = min(239, maximum_bytes + 1 - len(data)) if virtual else min(239, size - len(data))
            assert wanted > 0, "File exceeds the selected read limit"
            try:
                reply = ftp.request(5, session=ftp.session, offset=len(data), read_size=wanted)
            except FtpNak as error:
                if virtual and error.code == 6:
                    evidence.data["native_eof"] = True
                    break
                raise
            assert 0 < len(reply["data"]) <= wanted
            data.extend(reply["data"])
            assert len(data) <= maximum_bytes
        ftp.close_session()
        evidence.data.update(bytes=len(data), passed=True)
        return bytes(data), evidence.data
    finally:
        if ftp.session is not None:
            ftp.close_session()


def list_directory(client, path, maximum_entries=4096):
    client.confirm_disarmed()
    evidence = Evidence(dict(path=path, read_only=True))
    ftp = FtpClient(client.link, evidence, target_system=client.system, target_component=client.component)
    entries = []
    offset = 0
    while offset < maximum_entries:
        try:
            reply = ftp.request(3, offset=offset, data=path.encode("ascii") + b"\0")
        except FtpNak as error:
            if error.code == 6:
                evidence.data.update(passed=True, entries=len(entries))
                return entries, evidence.data
            raise
        rows = [row for row in reply["data"].split(b"\0") if row]
        if not rows:
            evidence.data.update(passed=True, entries=len(entries))
            return entries, evidence.data
        for row in rows:
            offset += 1
            kind = chr(row[0])
            if kind == "S":
                continue
            assert kind in ("F", "D"), row
            value = row[1:].decode("ascii")
            if kind == "F":
                name, size = value.rsplit("\t", 1)
                entry = dict(name=name, bytes=int(size), directory=False)
            else:
                name = value
                entry = dict(name=name, directory=True)
            if name in (".", ".."):
                continue
            assert "/" not in name and "\\" not in name
            entries.append(entry)
    raise RuntimeError("Directory exceeds the selected listing limit")


def read_file_burst(client, path, maximum_bytes=32 * 1024 * 1024):
    """Retain valid burst ranges and repair missing bytes without discarding later packets."""
    assert not path.startswith("@"), "Use the EOF reader for virtual files"
    client.confirm_disarmed()
    evidence = Evidence(dict(path=path, read_only=True, bursts=[], bad_data_messages=0))
    ftp = FtpClient(client.link, evidence, target_system=client.system, target_component=client.component)
    if hasattr(client.link.port, "set_buffer_size"):
        client.link.port.set_buffer_size(rx_size=1024 * 1024, tx_size=65536)
    try:
        opened = ftp.open_file(path)
        assert len(opened["data"]) == 4
        size = struct.unpack("<I", opened["data"])[0]
        assert size <= maximum_bytes, (path, size)
        evidence.data["advertised_bytes"] = size
        data = bytearray(size)
        covered = bytearray(size)
        received_bytes = 0
        no_progress = 0
        last_keepalive = time.monotonic()
        reopened_sessions = 0
        evidence.data['native_session_keepalives'] = 0
        def maintain_session():
            nonlocal last_keepalive
            if time.monotonic()-last_keepalive>3:
                directory=path.rsplit('/',1)[0] or '/'
                ftp.request(3,offset=0,data=directory.encode('ascii')+b'\0')
                last_keepalive=time.monotonic()
                evidence.data['native_session_keepalives']+=1
        def reopen_session():
            nonlocal reopened_sessions,last_keepalive
            assert reopened_sessions<4,'Repeated native file-session expiration'
            ftp.session=None
            opened=ftp.open_file(path)
            assert struct.unpack('<I',opened['data'])[0]==size,'File changed during read-only recovery'
            reopened_sessions+=1;last_keepalive=time.monotonic()
            evidence.data['reopened_sessions']=reopened_sessions
        # PX4 v1.17 ends each native stream burst after about 35 kB.
        maximum_bursts = (size + 34999) // 35000 + 20
        while received_bytes < size:
            assert len(evidence.data["bursts"]) < maximum_bursts, "Excessive burst loss"
            start = covered.find(0)
            before_received = received_bytes
            sequence = ftp.sequence
            first_sequence = (sequence + 1) & 65535
            burst = dict(offset=start, packets=0, gaps=0, duplicates=0, complete=False)
            evidence.data["bursts"].append(burst)
            # Native idle expiry also applies during long gap-repair sequences.
            maintain_session()
            sequence = ftp.sequence
            first_sequence = (sequence + 1) & 65535
            try:
                packet = ftp.request(15, session=ftp.session, offset=start, read_size=239)
            except FtpNak as error:
                if error.code != 4:
                    raise
                reopen_session()
                continue
            maximum_sequence_delta = 0
            deadline = time.monotonic() + 4
            total_deadline = time.monotonic() + 90
            while time.monotonic() < total_deadline:
                if packet is None:
                    ftp.confirm_disarmed()
                    now = time.monotonic()
                    if now >= ftp.next_gcs_heartbeat:
                        client.link.mav.heartbeat_send(6, 8, 0, 0, 4)
                        ftp.next_gcs_heartbeat = now + 1
                    message = client.link.recv_match(blocking=True, timeout=.2)
                    if message is None:
                        if time.monotonic() > deadline:
                            burst["timed_out"] = True
                            break
                        continue
                    ftp._observe_heartbeat(message)
                    if message.get_type() == "BAD_DATA":
                        evidence.data["bad_data_messages"] += 1
                        continue
                    if message.get_type() != "FILE_TRANSFER_PROTOCOL":
                        continue
                    if (message.get_srcSystem(), message.get_srcComponent()) != ftp.target:
                        continue
                    if (message.target_system, message.target_component) != ftp.source:
                        continue
                    packet = unpack(message.payload)
                if packet["request_opcode"] != 15 or packet["session"] != ftp.session:
                    packet = None
                    continue
                delta = (packet["seq"] - first_sequence) & 65535
                if delta > 2000:
                    packet = None
                    continue
                maximum_sequence_delta = max(maximum_sequence_delta, delta)
                deadline = time.monotonic() + 4
                if packet["opcode"] == 129:
                    error = FtpNak(15, packet["data"])
                    if error.code==4:
                        burst['native_session_expired']=True
                        break
                    if error.code != 6:
                        raise error
                    burst["native_eof"] = True
                    burst["complete"] = True
                    break
                assert packet["opcode"] == 128 and 0 < packet["size"] <= 239
                offset = packet["offset"]
                assert offset == start + delta * 239, "Burst sequence/offset mismatch"
                assert offset + len(packet["data"]) <= size, "Read exceeds the advertised file"
                burst["packets"] += 1
                end = offset + len(packet["data"])
                existing = covered[offset:end]
                new_count = existing.count(0)
                if new_count == len(existing):
                    data[offset:end] = packet["data"]
                    covered[offset:end] = b"\x01" * len(existing)
                    received_bytes += new_count
                elif new_count == 0:
                    assert data[offset:end] == packet["data"], "Conflicting duplicate bytes"
                    burst["duplicates"] += 1
                else:
                    for index, value in enumerate(packet["data"], offset):
                        if covered[index]:
                            assert data[index] == value, "Conflicting overlapping bytes"
                        else:
                            data[index] = value
                            covered[index] = 1
                            received_bytes += 1
                if covered.find(0, start, offset) != -1:
                    burst["gaps"] += 1
                if packet["burst_complete"]:
                    burst["complete"] = True
                    break
                packet = None
            # PX4 closes a burst after its 35,000-byte threshold (147 packets).
            # Reserve its tail and repair only the bytes that burst could cover.
            ftp.sequence = (sequence + max(151,maximum_sequence_delta+2)) & 65535
            if burst.get('native_session_expired'):reopen_session()
            block_end = min(size, start + 147 * 239)
            burst["gap_repairs"] = []
            missing = covered.find(0, start, block_end)
            while missing >= 0:
                maintain_session()
                length = min(239, block_end - missing)
                try:repaired = ftp.request(5, session=ftp.session, offset=missing, read_size=length)
                except FtpNak as error:
                    if error.code!=4:raise
                    reopen_session()
                    repaired=ftp.request(5,session=ftp.session,offset=missing,read_size=length)
                assert repaired["offset"] == missing and 0 < len(repaired["data"]) <= length
                for index, value in enumerate(repaired["data"], missing):
                    if covered[index]:
                        assert data[index] == value, "Conflicting gap-repair bytes"
                    else:
                        data[index] = value
                        covered[index] = 1
                        received_bytes += 1
                burst["gap_repairs"].append(dict(offset=missing, bytes=len(repaired["data"])))
                missing = covered.find(0, missing, block_end)
            first_missing = covered.find(0)
            burst["contiguous_bytes_after"] = first_missing if first_missing >= 0 else size
            burst["unique_bytes_after"] = received_bytes
            no_progress = no_progress + 1 if received_bytes == before_received else 0
            assert no_progress < 4, "No contiguous progress from native bursts"
        assert covered.count(0) == 0 and received_bytes == size
        ftp.close_session()
        evidence.data.update(bytes=len(data), passed=True)
        return bytes(data), evidence.data
    except Exception as error:
        evidence.data.update(bytes_received=locals().get("received_bytes", 0), error=repr(error), passed=False)
        error.ftp_evidence = evidence.data
        raise
    finally:
        if ftp.session is not None:
            try:
                ftp.close_session()
            except FtpNak as error:
                if error.code != 4:
                    raise
                ftp.session=None
