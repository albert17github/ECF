"""Check a release's source, image, provenance and shared-interface metadata."""
import argparse
import hashlib
import json
from pathlib import Path
import re

def check(root):
    root = root.resolve()
    meta = json.loads((root/'release.json').read_text(encoding='utf-8-sig'))
    manifest = root/meta['source_manifest']
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    assert digest(manifest) == meta['source_manifest_sha256'], 'Manifest digest'
    entries = dict((name, sha) for sha, name in (line.split('  ',1) for line in manifest.read_text(encoding='utf-8-sig').splitlines()))
    assert len(entries) == meta['source_files'], 'Source file count'
    for kind in ('image','elf','apj'):
        if kind in meta['firmware']:
            key = 'sha256' if kind == 'image' else kind+'_sha256'
            assert digest(root/meta['firmware'][kind]) == meta['firmware'][key], kind
    assert meta['firmware']['source_manifest_sha256'] == meta['source_manifest_sha256'], 'Image/source binding'
    report = {'project':meta['project'], 'revision':meta['package_revision'], 'passed':False,
              'source_files':len(entries), 'source_manifest_sha256':meta['source_manifest_sha256'],
              'image_sha256':meta['firmware']['sha256']}
    provenance = root/'verification/source-provenance.json'
    if provenance.exists():
        doc = json.loads(provenance.read_text(encoding='utf-8-sig'))
        current = {}
        for values in doc.values():
            if isinstance(values, list):
                for value in values:
                    if isinstance(value,dict) and value.get('path') and value.get('sha256'):
                        current[value['path']] = value['sha256']
        for name, sha in current.items():
            assert entries.get(name) == sha, 'Stale provenance: '+name
        assert doc['selected_source_files'] == len(entries)
        assert doc['selected_source_bytes'] == sum((root/'source'/name).stat().st_size for name in entries)
        target = 'src/main/target/ECFH743/'
        assert all(name in current for name in entries if name.startswith(target)), 'Incomplete board provenance'
        report['provenance_entries_checked'] = len(current)
    interfaces = json.loads((root/'verification/interfaces.json').read_text(encoding='utf-8-sig'))
    assert interfaces['shared_groups']['factory'] == meta['default_power_profile']['mode'] == 'ADC_UART3'
    if meta['project'] == 'PX4':
        defaults = (root/'source/boards/ecf/core-h743/init/rc.board_defaults').read_text()
        source = (root/'source/src/drivers/ecf_interfaces/parameters.c').read_text()
        mode = int(re.search(r'param set-default ECF_IO_MODE (\d+)',defaults)[1])
        declared = int(re.search(r'PARAM_DEFINE_INT32\(ECF_IO_MODE, (\d+)\)',source)[1])
        assert interfaces['default_shared_mode']['ECF_IO_MODE'] == mode == declared == 1
        assert set(interfaces['default_shared_mode']['disabled']) == {'UART8','I2C4'}
        assert interfaces['default_shared_mode']['uart'] == 'UART3'
        report['shared_mode_source_metadata_match'] = True
    report['passed'] = True
    return report

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--out',type=Path)
    args = parser.parse_args()
    report = check(args.root)
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
