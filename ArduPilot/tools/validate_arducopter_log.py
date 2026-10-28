"""Validate native DataFlash framing, independent decoding and IMU batch continuity."""
# AP_FLAKE8_CLEAN
import argparse
from collections import Counter
import contextlib
import hashlib
import io
import json
import math
from pathlib import Path

from pymavlink import DFReader


def frames(raw):
    formats = {128: (89, "FMT")}
    result = []
    offset = 0
    while offset < len(raw):
        assert raw[offset:offset + 2] == b"\xa3\x95", f"Invalid frame boundary at {offset}"
        kind = raw[offset + 2]
        assert kind in formats, f"Frame type {kind} has no preceding format at {offset}"
        length, name = formats[kind]
        assert 3 <= length <= 255 and offset + length <= len(raw), f"Truncated {name} at {offset}"
        packet = raw[offset:offset + length]
        if kind == 128:
            msg_id, msg_length = packet[3:5]
            msg_name = packet[5:9].split(b"\0", 1)[0].decode("ascii")
            assert 3 <= msg_length <= 255
            if msg_id in formats:
                assert formats[msg_id] == (msg_length, msg_name), "Conflicting format declaration"
            formats[msg_id] = (msg_length, msg_name)
        result.append(dict(offset=offset, length=length, name=name))
        offset += length
    assert offset == len(raw)
    return result


def inspect(path, minimum_seconds):
    raw = path.read_bytes()
    layout = frames(raw)
    counts = Counter(row["name"] for row in layout)
    official = Counter()
    selected = {name: [] for name in ("IMU", "BARO", "MAG", "DSF", "ISBH", "ISBD")}
    messages = []
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        reader = DFReader.DFReader_binary(str(path))
        while True:
            message = reader.recv_msg()
            if message is None:
                break
            name = message.get_type()
            official[name] += 1
            if name in selected:
                selected[name].append(message.to_dict())
            if name == "MSG":
                messages.append(message.to_dict())
    assert official == counts, dict(raw_counts=counts, decoder_counts=official)
    result = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), frames=len(layout),
                  frame_counts=dict(counts), decoder_output=output.getvalue(), messages=messages,
                  invalid_boundaries=0, truncated_frames=0, streams={}, batches={})
    for name in ("IMU", "BARO", "MAG"):
        rows = [row for row in selected[name] if row.get("I", 0) == 0]
        assert len(rows) >= minimum_seconds * (100 if name == "IMU" else 7), (name, len(rows))
        timestamps = [row["TimeUS"] for row in rows]
        gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
        assert all(gap > 0 for gap in gaps), name + " timestamps are not increasing"
        span = (timestamps[-1] - timestamps[0]) / 1e6
        assert span >= minimum_seconds, (name, span)
        assert max(gaps) < (100000 if name == "IMU" else 300000), (name, max(gaps))
        result["streams"][name] = dict(samples=len(rows), span_seconds=span, max_gap_us=max(gaps))
        if name == "IMU":
            assert all(row["GH"] == 1 and row["AH"] == 1 for row in rows)
            assert all(5 < math.sqrt(sum(row[key] ** 2 for key in ("AccX", "AccY", "AccZ"))) < 15 for row in rows)
        elif name == "BARO":
            assert all(30000 < row["Press"] < 125000 for row in rows)
        else:
            assert all(any(row[key] != 0 for key in ("MagX", "MagY", "MagZ")) for row in rows)
    stats = selected["DSF"]
    assert len(stats) >= 5, "No sustained native file-backend statistics"
    assert all(row["Dp"] == 0 for row in stats), "Native logger rejected writes"
    result["native_file_stats"] = dict(samples=len(stats), maximum_rejected_writes=max(row["Dp"] for row in stats),
                                       maximum_bytes_written_per_statistics_interval=max(row["Bytes"] for row in stats))
    headers = {}
    batches = {}
    order = []
    for header in selected["ISBH"]:
        number = header["N"]
        assert number not in headers, "Duplicate batch header"
        assert header["instance"] == 0 and header["smp_cnt"] % 32 == 0 and header["smp_rate"] > 0
        headers[number] = header
        batches[number] = []
        order.append(number)
    assert len(order) >= 4 and {row["type"] for row in headers.values()} == {0, 1}
    assert all((b - a) & 65535 == 1 for a, b in zip(order, order[1:])), "Missing complete batch"
    for row in selected["ISBD"]:
        assert row["N"] in headers, "Batch data precedes its header"
        assert all(len(row[key]) == 32 for key in ("x", "y", "z"))
        batches[row["N"]].append(row["seqno"])
    complete = 0
    for number in order:
        expected = int(headers[number]["smp_cnt"]) // 32
        actual = batches[number]
        assert actual == list(range(len(actual))), f"Missing or duplicate samples in batch {number}"
        if number != order[-1]:
            assert len(actual) == expected, f"Incomplete interior batch {number}"
        else:
            assert len(actual) <= expected
        complete += len(actual) == expected
    result["batches"] = dict(headers=len(headers), complete=complete,
                             final_partial=complete != len(headers), missing_interior_samples=0,
                             types=sorted({row["type"] for row in headers.values()}))
    result["passed"] = True
    return result, layout, headers, order


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--minimum-seconds", type=float, default=30)
    parser.add_argument("--negative-control", action="store_true")
    args = parser.parse_args()
    assert not args.out.exists()
    doc = dict(passed=False)
    try:
        positive, layout, headers, order = inspect(args.input, args.minimum_seconds)
        doc["positive"] = positive
        if args.negative_control:
            raw = args.input.read_bytes()
            # ISBD is QHHaaa. Remove a non-final data packet in a middle
            # complete batch, retaining valid packet boundaries throughout.
            candidates = [row for row in layout if row["name"] == "ISBD"]
            chosen = None
            middle = set(order[1:-1])
            for row in candidates[len(candidates) // 3:]:
                offset = row["offset"]
                number = int.from_bytes(raw[offset + 11:offset + 13], "little")
                sequence = int.from_bytes(raw[offset + 13:offset + 15], "little")
                if number in middle and sequence == 1:
                    chosen = row
                    break
            assert chosen is not None
            negative = args.out.with_suffix(".missing-ISBD.BIN")
            with negative.open("xb") as stream:
                stream.write(raw[:chosen["offset"]] + raw[chosen["offset"] + chosen["length"]:])
            try:
                inspect(negative, args.minimum_seconds)
            except AssertionError as error:
                assert "batch" in str(error).lower(), repr(error)
                doc["negative_control"] = dict(rejected=True, removed_frame=chosen, reason=str(error), file=str(negative))
            else:
                raise AssertionError("Missing raw batch packet was accepted")
        doc["passed"] = True
    except Exception as error:
        doc["error"] = repr(error)
        raise
    finally:
        args.out.write_text(json.dumps(doc, indent=2, default=list) + "\n")
    print(json.dumps(dict(passed=doc["passed"], positive=doc.get("positive"), negative_control=doc.get("negative_control"))))
