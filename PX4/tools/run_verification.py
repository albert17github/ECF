"""Run standalone disarmed ECFH743 PX4 source, configuration and storage acceptance."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import struct
import shutil
import subprocess
import sys
import time

from px4_client import PX4Client
from mavftp_files import list_directory,read_file_burst


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--uid',required=True);p.add_argument('--port',default='auto')
    p.add_argument('--stlink-sn',required=True);p.add_argument('--cube');p.add_argument('--out',type=Path)
    p.add_argument('--unloaded-bench',action='store_true',required=True);p.add_argument('--flash-factory-image',action='store_true')
    p.add_argument('--factory-reset-parameters',action='store_true',help='Back up native parameters before resetting their SD defaults')
    p.add_argument('--continue-record',type=Path)
    p.add_argument('--sd-baseline-record',type=Path,help='Prior complete SD SHA inventory for the same MCU; all firmware phases still run')
    p.add_argument('--completed-log-record',type=Path,help='Recovered closed log from this exact continuation record; revalidate all records')
    p.add_argument('--reuse-completed-tail',action='store_true',help='Reuse passed recovery/runtime records only if the image and all tested parameters still match')
    a=p.parse_args();root=Path(__file__).resolve().parents[1];tools=root/'tools'
    release=json.loads((root/'release.json').read_text());assert release['project']=='PX4' and release['tag']=='v1.17.0'
    assert not release['firmware_build_pending']
    assert release['firmware']['source_manifest_sha256']==release['source_manifest_sha256']
    for key in ['image','elf']:
        expected=release['firmware']['sha256' if key=='image' else 'elf_sha256']
        assert hashlib.sha256((root/release['firmware'][key]).read_bytes()).hexdigest()==expected
    output=a.out or root/'.build/verification'/datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    assert not output.exists();output.mkdir(parents=True)
    doc=dict(at=datetime.now(timezone.utc).isoformat(),passed=False,uid=a.uid,steps=[],
        image_sha256=release['firmware']['sha256'],source_manifest_sha256=release['source_manifest_sha256'],
        physical_rgb_tested=False,gcs_ui_tested=False,external_peripheral_interoperability_tested=False,flight_tested=False)
    previous=None
    sd_baseline=None
    if a.sd_baseline_record:
        baseline_path=a.sd_baseline_record/'result.json'
        baseline=json.loads(baseline_path.read_text())
        assert baseline['uid']==a.uid and baseline.get('sd_files_before')
        sd_baseline=baseline['sd_files_before']
        assert all(v['bytes']>=0 and len(v['sha256'])==64 for v in sd_baseline.values())
        doc['sd_baseline_provenance']=dict(result_sha256=hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
            firmware_steps_imported=False,baseline_image_sha256=baseline['image_sha256'])
    if a.continue_record:
        assert not a.flash_factory_image and not a.factory_reset_parameters
        previous=json.loads((a.continue_record/'result.json').read_text())
        assert previous['uid']==a.uid and previous['image_sha256']==doc['image_sha256']
        assert previous['source_manifest_sha256']==doc['source_manifest_sha256'] and previous.get('sd_files_before')
        doc['continued_from']=dict(result_sha256=hashlib.sha256((a.continue_record/'result.json').read_bytes()).hexdigest())
        doc['imported_steps']={}
    if a.completed_log_record:
        assert previous is not None,'A completed log must belong to the exact-image continuation'
        completed_log=json.loads(a.completed_log_record.read_text())
        assert completed_log['passed'] and len(completed_log['cycles'])==3 and all(v['passed'] for v in completed_log['cycles'])
        if a.completed_log_record.resolve()!=(a.continue_record/'native-log.json').resolve():
            assert completed_log['recovered_from_record_sha256']==hashlib.sha256((a.continue_record/'native-log.json').read_bytes()).hexdigest()
        log_file=a.completed_log_record.with_suffix('.ulg')
        assert log_file.stat().st_size==completed_log['log']['bytes']
        assert hashlib.sha256(log_file.read_bytes()).hexdigest()==completed_log['log']['sha256']
    native=['--port',a.port,'--uid',a.uid];hardware=[*native,'--stlink-sn',a.stlink_sn,'--unloaded-bench']
    if a.cube:hardware+=['--cube',a.cube]
    def save():(output/'result.json').write_text(json.dumps(doc,indent=2)+'\n')
    def step(name,args):
        print(json.dumps(dict(phase=name)),flush=True);save()
        subprocess.run([sys.executable,str(tools/name),*map(str,args)],check=True,timeout=2400)
        doc['steps'].append(name);save()
    def hashes(expected_paths=None):
        c=PX4Client(a.port,a.uid);result={};pending=[('/fs/microsd',0)]
        try:
            while pending:
                folder,depth=pending.pop();assert depth<8
                entries,_=list_directory(c,folder)
                for entry in entries:
                    path=folder.rstrip('/')+'/'+entry['name']
                    if entry['name'].casefold() in ['system volume information','$recycle.bin','freespac.e']:continue
                    if path.casefold().startswith('/fs/microsd/ecf-px4/parameters'):continue
                    if path=='/fs/microsd/parameters_backup.bson':continue
                    if entry['directory']:pending.append((path,depth+1))
                    else:
                        if expected_paths is not None and path not in expected_paths:continue
                        doc['sd_scan']=dict(path=path,completed_files=len(result));save()
                        assert entry['bytes'] <= 512*1024*1024, 'File exceeds the declared onboard storage bound'
                        raw,_=read_file_burst(c,path,maximum_bytes=max(64*1024*1024,entry['bytes']));assert len(raw)==entry['bytes']
                        result[path]=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
            return result
        finally:c.close()
    def full_parameters():
        c=PX4Client(a.port,a.uid);result={};indices=set();count=None;end=time.monotonic()+90
        try:
            c.link.mav.param_request_list_send(c.system,c.component)
            while time.monotonic()<end:
                try:m=c.wait('PARAM_VALUE',timeout=2)
                except TimeoutError:
                    if count is None:continue
                    missing=sorted(set(range(count))-indices)
                    if not missing:break
                    for index in missing[:50]:c.link.mav.param_request_read_send(c.system,c.component,b'',index)
                    continue
                count=m.param_count
                if not 0<=m.param_index<count:continue
                indices.add(m.param_index)
                result[m.param_id]=dict(type=m.param_type,value=c.parameter_value(m),raw_f32=struct.pack('<f',m.param_value).hex())
                if len(indices)==count:break
            assert count and len(indices)==count,(count,len(indices))
            (output/'before-test-parameters.json').write_text(json.dumps(result,indent=2)+'\n')
            return dict(count=count,sha256=hashlib.sha256((output/'before-test-parameters.json').read_bytes()).hexdigest())
        finally:c.close()
    try:
        step('check_source.py',['--source',root/'source','--manifest',root/release['source_manifest']])
        if a.flash_factory_image:
            args=['--uid',a.uid,'--stlink-sn',a.stlink_sn,'--image',root/release['firmware']['image'],
                  '--sha256',release['firmware']['sha256'],'--backup',output/'before-flash-full.bin','--out',output/'flash.json']
            if a.cube:args+=['--cube',a.cube]
            step('flash_swd.py',args)
        doc['native_parameter_backup']=full_parameters();save()
        if a.factory_reset_parameters:
            c=PX4Client(a.port,a.uid)
            try:doc['factory_parameter_reset']=[c.shell('param reset_all'),c.shell('param save')];c.reboot()
            finally:c.close()
        args=['--uid',a.uid,'--stlink-sn',a.stlink_sn,'--image',root/release['firmware']['image'],
              '--sha256',release['firmware']['sha256'],'--backup',output/'readback-full.bin','--out',output/'readback.json','--no-mutable-configuration']
        if a.cube:args+=['--cube',a.cube]
        step('readback_verify.py',args)
        doc['sd_files_before']=dict(previous['sd_files_before'] if previous else sd_baseline if sd_baseline is not None else hashes())
        mutable_backup=doc['sd_files_before'].pop('/fs/microsd/parameters_backup.bson',None)
        if mutable_backup is not None:doc['native_mutable_backup_baseline']=mutable_backup
        doc['sd_hash_excludes']=['FREESPAC.E allocation bookkeeping','ecf-px4/parameters* active parameters','/fs/microsd/parameters_backup.bson native mutable backup','Windows system directories'];save()
        for phase in ['serial','shared','can','outputs']:
            if previous:
                original=a.continue_record/(phase+'.json')
                if original.exists() and json.loads(original.read_text()).get('passed') is True:
                    shutil.copyfile(original,output/(phase+'.json'))
                    doc['imported_steps'][phase]=dict(sha256=hashlib.sha256(original.read_bytes()).hexdigest(),executed_in_this_run=False)
                    doc['steps'].append('verify_configuration.py:'+phase);save()
                    print(json.dumps(dict(phase=phase,reused_passed_record=True)),flush=True)
                    continue
            step('verify_configuration.py',[*hardware,'--phase',phase,'--out',output/(phase+'.json')])
        if a.completed_log_record:
            shutil.copyfile(a.completed_log_record,output/'native-log.json')
            shutil.copyfile(a.completed_log_record.with_suffix('.ulg'),output/'native-log.ulg')
            doc['imported_steps']['storage_log']=dict(sha256=hashlib.sha256(a.completed_log_record.read_bytes()).hexdigest(),
                captured_in_matching_image_run=True,recovered_without_new_recording=True)
            doc['steps'].append('verify_storage_log.py:recovered');save()
        else:step('verify_storage_log.py',[*native,'--unloaded-bench','--out',output/'native-log.json'])
        step('validate_ulog.py',['--input',output/'native-log.ulg','--out',output/'log-validation.json','--negative-control'])
        recovery=[*native,'--stlink-sn',a.stlink_sn,'--out',output/'recovery.json']
        if a.cube:recovery+=['--cube',a.cube]
        if a.reuse_completed_tail:
            assert previous is not None
            current=json.loads((output/'before-test-parameters.json').read_text())
            for filename in ['recovery.json','runtime-120s.json']:
                original=a.continue_record/filename;accepted=json.loads(original.read_text());assert accepted['passed']
                tested=accepted['parameters'] if filename.startswith('runtime') else accepted['before']['parameters']
                assert all(k in current and math.isclose(current[k]['value'],v,rel_tol=1e-6,abs_tol=1e-7) for k,v in tested.items())
                shutil.copyfile(original,output/filename)
                doc['imported_steps'][filename]=dict(sha256=hashlib.sha256(original.read_bytes()).hexdigest(),tested_parameters_still_match=True,executed_in_this_run=False)
            doc['steps']+=['verify_recovery.py:imported','verify_runtime.py:imported'];save()
        else:
            step('verify_recovery.py',recovery)
            step('verify_runtime.py',[*native,'--seconds','120','--out',output/'runtime-120s.json'])
        after=hashes(doc['sd_files_before']);doc['sd_files_after']=after
        doc['sd_existing_files_unchanged']=all(after.get(k)==v for k,v in doc['sd_files_before'].items())
        assert doc['sd_existing_files_unchanged'],'An existing SD payload changed'
        c=PX4Client(a.port,a.uid)
        try:
            raw,_=read_file_burst(c,'/fs/microsd/parameters_backup.bson',maximum_bytes=1024*1024)
            doc['native_mutable_backup_after']=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),purpose='Native PX4 parameter backup, updated by param save')
            doc['native_parameter_status']=c.shell('param status')
        finally:c.close()
        doc['passed']=True
    except Exception as error:doc['error']=repr(error);raise
    finally:doc['finished_at']=datetime.now(timezone.utc).isoformat();save()
    print(json.dumps(dict(passed=doc['passed'],steps=len(doc['steps']),output=str(output))),flush=True)


if __name__=='__main__':main()
