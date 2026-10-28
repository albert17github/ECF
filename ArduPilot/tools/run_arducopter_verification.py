"""Run the independent, disarmed ArduCopter ECFH743 software acceptance suite."""
# AP_FLAKE8_CLEAN
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from arducopter_client import APClient, SafetyStop
from arducopter_ftp import list_directory, read_file, read_file_burst


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--uid', required=True)
    parser.add_argument('--stlink-sn', required=True)
    parser.add_argument('--port', default='auto')
    parser.add_argument('--unloaded-bench', action='store_true', required=True)
    parser.add_argument('--flash-factory-image', action='store_true')
    parser.add_argument('--cube', default=os.environ.get('ECF_CUBE_PROGRAMMER'))
    parser.add_argument('--out', type=Path)
    parser.add_argument('--continue-record', type=Path)
    args = parser.parse_args()
    assert len(bytes.fromhex(args.uid)) == 12
    root = Path(__file__).resolve().parents[1]
    tools = root / 'tools'
    release = json.loads((root / 'release.json').read_text())
    assert release['target'] == 'ECFH743' and release['tag'] == 'Copter-4.7.2'
    assert not release.get('firmware_build_pending', True), 'Rebuild the current source before board verification'
    firmware = release['firmware']
    image = root / firmware['image']
    apj = root / firmware['apj']
    image_manifest = json.loads((image.parent / 'manifest.json').read_text())
    assert image_manifest['source_manifest_sha256'] == release['source_manifest_sha256'], 'Source/image revision mismatch'
    for name, digest in [(firmware['image'], firmware['sha256']),
                         (firmware['apj'], firmware['apj_sha256']),
                         (firmware['elf'], firmware['elf_sha256'])]:
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest, name
    output = args.out or root / '.build/verification' / datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    assert not output.exists(), 'Choose a new verification output directory'
    output.mkdir(parents=True)
    previous = None
    imported = {}
    if args.continue_record:
        previous = json.loads((args.continue_record / 'result.json').read_text())
        assert previous['uid'] == args.uid and previous['image_sha256'] == firmware['sha256']
        assert previous['source_manifest_sha256'] == release['source_manifest_sha256']
        assert previous.get('baseline_controls') and previous.get('sd_files_before')
        for script, filename in [('verify_arducopter_serial.py', 'serial.json'),
                                 ('verify_arducopter_shared.py', 'shared.json'),
                                 ('verify_arducopter_can.py', 'can.json')]:
            path = args.continue_record / filename
            if script in previous['steps'] and path.exists() and json.loads(path.read_text()).get('passed') is True:
                imported[script] = path
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    if args.cube:
        os.environ['ECF_CUBE_PROGRAMMER'] = args.cube
    doc = dict(started_at=datetime.now(timezone.utc).isoformat(), passed=False, uid=args.uid,
               image_sha256=firmware['sha256'], source_manifest_sha256=release['source_manifest_sha256'],
               steps=[], flight_tested=False, external_peripheral_interoperability_tested=False,
               physical_waveforms_tested=False, physical_rgb_tested=False, gcs_ui_tested=False)
    doc['imported_steps'] = {}
    baseline = None

    def save():
        (output / 'result.json').write_text(json.dumps(doc, indent=2) + '\n')

    def step(script, arguments):
        if script in imported:
            destination = Path(arguments[arguments.index('--out') + 1])
            assert destination.resolve().parent == output.resolve() and not destination.exists()
            shutil.copyfile(imported[script], destination)
            doc['imported_steps'][script] = dict(source_record=str(imported[script]),
                sha256=hashlib.sha256(destination.read_bytes()).hexdigest(), executed_in_this_run=False)
            doc['steps'].append(script)
            save()
            print(json.dumps(dict(phase=script, reused_passed_record=True)), flush=True)
            return
        print(json.dumps(dict(phase=script)), flush=True)
        save()
        subprocess.run([sys.executable, str(tools / script), *map(str, arguments)],
                       check=True, timeout=1800, env=dict(os.environ))
        doc['steps'].append(script)
        save()

    def connect():
        return APClient(args.port, args.uid)

    def apply(parameters):
        client = connect()
        try:
            changes = [client.set_param(name, value) for name, value in parameters.items()]
            client.reboot()
        finally:
            client.close()
        time.sleep(1)
        return changes

    def restore_available(parameters):
        client = connect()
        changes, unavailable = [], {}
        try:
            for name, value in parameters.items():
                try:
                    client.read_param(name, attempts=1)
                    changes.append(client.set_param(name, value))
                except SafetyStop:
                    raise
                except TimeoutError as error:
                    client.confirm_disarmed()
                    unavailable[name] = str(error)
            client.reboot()
        finally:
            client.close()
        time.sleep(1)
        client = connect()
        try:
            readback = client.params(parameters)
            assert readback == parameters
            client.interval(1, 200000)
            healthy = client.wait('SYS_STATUS', lambda m:m.onboard_control_sensors_health & 15 == 15, timeout=35)
            return dict(changes=changes, parameters_unavailable_during_error=unavailable, readback=readback,
                        restored_sensor_health=healthy.onboard_control_sensors_health & 15)
        finally:
            client.close()

    def sd_hashes():
        client = connect()
        hashes = {}
        try:
            pending = [('/', 0)]
            while pending:
                directory, depth = pending.pop()
                assert depth < 8
                entries, _ = list_directory(client, directory)
                for entry in entries:
                    path = directory.rstrip('/') + '/' + entry['name']
                    if entry['name'].startswith('@'):
                        continue
                    if path.upper().startswith('/APM/STRG_BAK') or entry['name'].upper() in {'FREESPAC.E', 'LASTLOG.TXT'}:
                        continue
                    if entry['directory']:
                        pending.append((path, depth + 1))
                    else:
                        doc['sd_scan'] = dict(current_path=path, completed_files=len(hashes))
                        save()
                        try:
                            raw, _ = read_file_burst(client, path)
                        except Exception as error:
                            doc['sd_scan']['error'] = repr(error)
                            doc['sd_scan']['transfer'] = getattr(error, 'ftp_evidence', None)
                            save()
                            raise
                        assert len(raw) == entry['bytes']
                        hashes[path] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                        doc['sd_scan_completed'] = dict(hashes)
                        save()
            return hashes
        finally:
            client.close()

    def snapshot(label):
        client = connect()
        try:
            raw, evidence = read_file(client, '@PARAM/param.pck', maximum_bytes=1024 * 1024)
            (output / (label + '.pck')).write_bytes(raw)
            doc[label] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), transfer=evidence,
                              port=client.port, uid2=bytes(client.version.uid2).hex())
        finally:
            client.close()
        save()

    native = ['--port', args.port, '--uid', args.uid]
    hardware = [*native, '--stlink-sn', args.stlink_sn, '--unloaded-bench']
    try:
        step('check_source.py', ['--source', root / 'source', '--manifest', root / release['source_manifest']])
        snapshot('before_test_parameters')
        if args.flash_factory_image:
            command = ['--image', image, '--sha256', firmware['sha256'], '--uid', args.uid,
                       '--stlink-sn', args.stlink_sn, '--backup', output / 'before-flash-full.bin',
                       '--out', output / 'flash.json']
            if args.cube:
                command += ['--cube', args.cube]
            step('flash_swd.py', command)
            # The native bootloader and application use the same USB identity.
            time.sleep(18)
        client = connect()
        try:
            names = ['BRD_OPTIONS', 'BRD_ALT_CONFIG', 'BATT_MONITOR', 'LOG_DISARMED', 'MOT_PWM_TYPE',
                     'NTF_LED_OVERRIDE', 'SERVO_BLH_MASK', 'SERVO_BLH_OTYPE', 'SERVO_BLH_BDMASK',
                     'CAN_D1_PROTOCOL', 'CAN_D2_PROTOCOL']
            names += [f'SERIAL{i}_{field}' for i in (1, 3, 4, 5, 6, 7, 8) for field in ('PROTOCOL', 'BAUD')]
            names += [f'CAN_P{i}_{field}' for i in (1, 2) for field in ('DRIVER', 'BITRATE')]
            names += [f'SERVO{i}_FUNCTION' for i in range(5, 9)]
            baseline = client.params(names)
            assert baseline['BRD_OPTIONS'] == 1 and baseline['LOG_DISARMED'] == 0
            assert baseline['BRD_ALT_CONFIG'] == 1 and baseline['BATT_MONITOR'] == 4
            doc['baseline_controls'] = baseline
            if previous:
                assert baseline == previous['baseline_controls'], 'Controls differ from the previous verified baseline'
        finally:
            client.close()
        doc['sd_files_before'] = previous['sd_files_before'] if previous else sd_hashes()
        if previous:
            doc['continued_from'] = dict(record=str(args.continue_record),
                result_sha256=hashlib.sha256((args.continue_record / 'result.json').read_bytes()).hexdigest())
        save()
        doc['debug_enable'] = apply({'BRD_OPTIONS': 9})
        command = ['--image', image, '--sha256', firmware['sha256'], '--uid', args.uid,
                   '--stlink-sn', args.stlink_sn, '--backup', output / 'readback-full.bin',
                   '--out', output / 'readback.json', '--configuration-start', '0x081c0000',
                   '--configuration-end', '0x08200000']
        if args.cube:
            command += ['--cube', args.cube]
        step('readback_verify.py', command)
        for script, filename in [('verify_arducopter_serial.py', 'serial.json'),
                                 ('verify_arducopter_shared.py', 'shared.json'),
                                 ('verify_arducopter_can.py', 'can.json'),
                                 ('verify_arducopter_outputs.py', 'outputs.json')]:
            arguments = [*hardware, '--out', output / filename]
            if filename == 'outputs.json':
                arguments += ['--require-native-bidir-state']
            step(script, arguments)
        doc['restore_controls'] = apply(baseline)
        step('verify_arducopter_storage.py', [*native, '--out', output / 'storage.json'])
        step('capture_arducopter_log.py', [*native, '--unloaded-bench', '--out', output / 'native-log.json'])
        step('validate_arducopter_log.py', ['--input', output / 'native-log.BIN',
                                          '--out', output / 'log-validation.json', '--negative-control'])
        step('verify_arducopter_reboots.py', [*native, '--out', output / 'ordinary-reboots.json'])
        command = ['--uid', args.uid, '--stlink-sn', args.stlink_sn, '--apj', apj,
                   '--out', output / 'recovery.json']
        if args.cube:
            command += ['--cube', args.cube]
        step('verify_arducopter_recovery.py', command)
        step('verify_arducopter_runtime.py', [*native, '--out', output / 'runtime-120s.json'])
        after = sd_hashes()
        doc['sd_files_after'] = after
        doc['sd_existing_files_unchanged'] = all(after.get(path) == value
                                               for path, value in doc['sd_files_before'].items())
        assert doc['sd_existing_files_unchanged'], 'An existing SD payload changed'
        snapshot('after_test_parameters')
        doc['passed'] = True
    except Exception as error:
        doc['error'] = repr(error)
        raise
    finally:
        if baseline is not None and not doc['passed']:
            try:
                doc['failure_cleanup'] = restore_available(baseline)
            except Exception as cleanup_error:
                doc['failure_cleanup_error'] = repr(cleanup_error)
        doc['finished_at'] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps(dict(passed=doc['passed'], output=str(output), steps=len(doc['steps']))), flush=True)


if __name__ == '__main__':
    main()
