"""Validate original INAV bytes with the unchanged official C parser on Linux."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--input', type=Path, required=True)
parser.add_argument('--diagnostics', type=Path, required=True)
parser.add_argument('--tools-root', type=Path, required=True)
parser.add_argument('--output-prefix', type=Path, required=True)
parser.add_argument('--negative-control', action='store_true')
args = parser.parse_args()
source, diagnostics, tools = args.input.resolve(), args.diagnostics.resolve(), args.tools_root.resolve()
prefix = args.output_prefix.resolve()
prefix.parent.mkdir(parents=True, exist_ok=True)
upstream = json.loads((tools / 'upstream.json').read_text())
assert upstream['commit'] == '1918a752851d99beb092bb2f3a2e7ba16ccb86db'
checked = []
for line in (tools / 'source-SHA256SUMS.txt').read_text().splitlines():
    expected, name = line.split('  ', 1)
    path = tools / name
    assert path.resolve().is_relative_to(tools)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
    checked.append(name)
binary_root = tools / 'bin/linux-x86_64'
raw = source.read_bytes()

def target(suffix):
    return prefix.with_name(prefix.name + suffix)

frames_path = target('-frames.jsonl')
with frames_path.open('wb') as out, target('-parser-stderr.txt').open('wb') as error:
    subprocess.run([str(binary_root / 'check_frames'), str(source)], stdout=out, stderr=error, check=True)
events = [json.loads(line) for line in frames_path.read_text().splitlines()]
frames = [item for item in events if 'kind' in item]
stats = {item['stat_kind']: item for item in events if 'stat_kind' in item}
parse_result = next(item for item in events if 'parse_success' in item)
assert parse_result['parse_success']
main = [item for item in frames if item['kind'] in ('I','P') and item['valid']]
assert main
boundary = 0
while raw[boundary:boundary+2] == b'H ':
    newline = raw.find(b'\n', boundary)
    assert newline >= 0 and all(byte in (9,13) or 32 <= byte <= 126 for byte in raw[boundary:newline])
    boundary = newline + 1
assert boundary == frames[0]['offset']
header_raw = raw[:boundary]
target('-header.txt').write_bytes(header_raw)
header_lines = header_raw.decode('ascii').splitlines()
header = dict(line[2:].split(':',1) for line in header_lines)
assert header['Firmware revision'] == 'INAV 9.1.0 (e519b69b) ECFH743'
assert header_lines[-1].startswith('H rpm_gyro_q:')
assert int(header['mag_hardware']) in (0, 17), 'AUTO or explicit onboard MMC5983MA required'
if int(header['mag_hardware']) == 0:
    diagnostic_document = json.loads(diagnostics.read_text(encoding='utf-8-sig'))
    status_text = next(item['response'] for item in diagnostic_document['commands'] if item['command'] == 'status')
    assert 'MAG=MMC5983MA' in status_text, 'AUTO must actually detect the onboard MMC5983MA on this bench'
assert all(name in header for name in ('Field I name','Field P encoding','Field S name','features'))
numerator, step = map(int, header['P interval'].split('/'))
assert numerator == 1 and step == max(1, math.ceil(1000 / int(header['looptime']))), 'Maximum upstream-supported native logging rate required'
end = re.search(rb'E\xffEnd of log \(disarm reason:(\d+)\)\x00$', raw)
assert end, 'No complete native end marker at exact EOF'
# This official parser predates the extended textual disarm reason. Account for
# precisely that original EOF event, without rewriting any input or payload.
compat = [item for item in frames if not item['valid'] and item['kind']=='E'
          and item['offset'] in (end.start(), end.start()+2)]
if compat:
    assert [item['offset'] for item in compat] == [end.start(), end.start()+2]
invalid = [item for item in frames if not item['valid'] and item not in compat]
expected = boundary
discontinuities = []
for item in frames:
    if not item['valid'] or item['offset'] >= end.start():
        continue
    if item['offset'] != expected:
        discontinuities.append(dict(expected=expected, actual=item['offset']))
    expected = item['offset'] + item['size']
gaps = []
for before, after in zip(main, main[1:]):
    if after['iteration'] - before['iteration'] != step or after['time_us'] <= before['time_us']:
        gaps.append(dict(before=before, after=after))

ranges = {}
csv_rows = 0
csv_mismatches = 0
first_rows, last_rows = [], []
with target('-decoder-stderr.txt').open('w') as error:
    process = subprocess.Popen([str(binary_root / 'blackbox_decode'), '--stdout', '--unit-flags', 'raw', str(source)],
                               stdout=subprocess.PIPE, stderr=error, text=True)
    with process.stdout:
        reader = csv.DictReader(process.stdout, skipinitialspace=True)
        fields = reader.fieldnames
        assert fields and 'loopIteration' in fields and 'time (us)' in fields
        for row in reader:
            identity = int(row['loopIteration']), int(row['time (us)'])
            if csv_rows >= len(main) or identity != (main[csv_rows]['iteration'], main[csv_rows]['time_us']):
                csv_mismatches += 1
            for name, text in row.items():
                if name.startswith(('gyroADC[','accSmooth[','magADC[','BaroAlt')):
                    value = float(text)
                    low, high = ranges.get(name, (value,value))
                    ranges[name] = [min(low,value),max(high,value)]
            sample = {name: row[name] for name in fields if name in ('loopIteration','time (us)') or name in ranges}
            if csv_rows < 2:
                first_rows.append(sample)
            last_rows = (last_rows + [sample])[-2:]
            csv_rows += 1
    assert process.wait() == 0
assert csv_rows == len(main) and csv_mismatches == 0
for required in ('accSmooth[0]','accSmooth[1]','accSmooth[2]','magADC[0]','magADC[1]','magADC[2]','BaroAlt'):
    assert any(name.startswith(required) for name in ranges), required
with target('-samples.csv').open('w', newline='') as out:
    writer = csv.DictWriter(out, fieldnames=list(first_rows[0]))
    writer.writeheader()
    writer.writerows(first_rows + last_rows)
diag = json.loads(diagnostics.read_text(encoding='utf-8-sig'))
diag_text = next(item['response'] for item in diag['commands'] if item['command']=='sd_info')
match = re.search(r'ECFSD requested=(\d+) accepted=(\d+) refused=(\d+)', diag_text)
assert match
requested, accepted, refused = map(int, match.groups())
clean = (not invalid and not gaps and not discontinuities and expected == end.start()
         and requested == accepted == len(raw) and refused == 0
         and parse_result['total_corrupt'] == len(compat))
result = dict(passed=clean, input=dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()),
              decoder=dict(upstream=upstream, source_files_checked=checked,
                           binaries={name:hashlib.sha256((binary_root/name).read_bytes()).hexdigest() for name in ('blackbox_decode','check_frames')}),
              header=dict(bytes=boundary, lines=len(header_lines), last_line=header_lines[-1],
                          firmware=header['Firmware revision'], mag_hardware=int(header['mag_hardware']),
                          p_interval=header['P interval'], main_fields=header['Field I name'].split(',')),
              frames=stats, main_rows=len(main), duration_seconds=(main[-1]['time_us']-main[0]['time_us'])/1e6,
              first_iteration=main[0]['iteration'], last_iteration=main[-1]['iteration'], gaps=gaps,
              byte_coverage=dict(complete=not discontinuities and expected==end.start(), discontinuities=discontinuities),
              invalid_payload_frames=invalid, true_corrupt_frames=parse_result['total_corrupt']-len(compat),
              exact_eof_parser_format_mismatches=len(compat), end_marker=dict(offset=end.start(), bytes=len(end.group()), disarm_reason=int(end.group(1))),
              write_bytes=dict(requested=requested, accepted=accepted, refused=refused), sensor_ranges=ranges,
              csv=dict(rows=csv_rows, identity_mismatches=csv_mismatches))
target('-validation.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({name:result[name] for name in ('passed','input','main_rows','duration_seconds','true_corrupt_frames','exact_eof_parser_format_mismatches','write_bytes','sensor_ranges')}))
assert clean, 'Native log validation failed; inspect the preserved evidence'
if args.negative_control:
    selected=[item for item in main if item['kind']=='P'][100]
    offset,size=selected['offset'],selected['size']
    fixture=target('-missing-frame.TXT');assert not fixture.exists()
    mutated=raw[:offset]+raw[offset+size:];fixture.write_bytes(mutated)
    for command in diag['commands']:
        if command['command']=='sd_info':
            command['response']=re.sub(r'ECFSD requested=\d+ accepted=\d+ refused=\d+',
                f'ECFSD requested={len(mutated)} accepted={len(mutated)} refused=0',command['response'])
    synthetic=target('-synthetic-matching-byte-totals.json');synthetic.write_text(json.dumps(diag,indent=2)+'\n')
    negative_prefix=target('-missing-frame')
    checked=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--input',str(fixture),
        '--diagnostics',str(synthetic),'--tools-root',str(tools),'--output-prefix',str(negative_prefix)],capture_output=True)
    target('-negative-stdout.txt').write_bytes(checked.stdout);target('-negative-stderr.txt').write_bytes(checked.stderr)
    assert checked.returncode!=0,'Missing native frame was accepted'
    negative=json.loads(negative_prefix.with_name(negative_prefix.name+'-validation.json').read_text())
    assert not negative['passed'] and negative['gaps'],'Rejection did not reach native continuity checks'
    result['negative_control']=dict(rejected=True,removed_whole_frame_bytes=size,detected_gaps=len(negative['gaps']),
        true_corrupt_frames=negative['true_corrupt_frames'],synthetic_byte_totals_match_fixture=True)
    target('-validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(negative_control=result['negative_control'])))
