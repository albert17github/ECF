"""Validate complete native ULog records, raw FIFO continuity and sensor coverage."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

import numpy as np
from pyulog import ULog


def validate(path):
    raw=path.read_bytes();assert raw[:7]==b'ULog\x01\x12\x35' and len(raw)>100000
    records=[];at=16
    while at<len(raw):
        assert at+3<=len(raw),'Partial ULog record header'
        size,kind=struct.unpack_from('<HB',raw,at);end=at+3+size
        assert end<=len(raw),'Partial ULog record body'
        records.append((at,end,kind));at=end
    assert at==len(raw)
    log=ULog(str(path));assert not log.dropouts,'Native ULog dropout records present'
    streams={}
    for name in ['sensor_gyro_fifo','sensor_accel_fifo','sensor_baro','sensor_mag']:
        dataset=next((d for d in log.data_list if d.name==name and d.multi_id==0),None)
        assert dataset is not None,'Missing native topic '+name
        d=dataset.data;t=np.asarray(d['timestamp_sample'] if 'timestamp_sample' in d else d['timestamp'],dtype=np.int64)
        assert len(t)>=250 and np.all(np.diff(t)>0),(name,'Non-monotonic or too few samples')
        span=(int(t[-1])-int(t[0]))/1e6;assert span>=30,(name,span)
        result=dict(messages=len(t),span_seconds=span,maximum_gap_us=int(np.max(np.diff(t))))
        if name.endswith('_fifo'):
            counts=np.asarray(d['samples'],dtype=np.int64);dt=np.asarray(d['dt'],dtype=np.float64)
            assert np.all((counts>0)&(counts<=32)) and np.all(np.isfinite(dt)&(dt>0))
            expected=counts[1:]*dt[1:];gaps=np.diff(t)
            missing=np.where(gaps>expected*1.55+100)[0]
            result.update(raw_samples=int(np.sum(counts)),dt_us_min=float(np.min(dt)),dt_us_max=float(np.max(dt)),
                maximum_gap_to_batch_period_ratio=float(np.max(gaps/expected)),interior_missing_batches=len(missing))
            assert len(missing)==0,(name,'Missing raw FIFO batch',missing[:8].tolist())
            assert all(np.all(np.isfinite(v)) for k,v in d.items() if k.startswith(('x[','y[','z[')))
        elif name=='sensor_baro':
            assert np.all(np.isfinite(d['pressure'])) and np.all((d['pressure']>30000)&(d['pressure']<125000))
            assert np.max(d['pressure'])>np.min(d['pressure'])
        else:
            assert all(np.all(np.isfinite(d[k])) for k in ['x','y','z'])
            assert np.any(np.asarray(d['x'])!=0)|np.any(np.asarray(d['y'])!=0)|np.any(np.asarray(d['z'])!=0)
            assert any(np.max(d[k])>np.min(d[k]) for k in ['x','y','z'])
        streams[name]=result
    return dict(passed=True,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),records=len(records),
        native_dropout_records=len(log.dropouts),streams=streams),records


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--negative-control',action='store_true');a=p.parse_args()
    assert not a.out.exists();doc=dict(passed=False)
    try:
        positive,records=validate(a.input);doc['positive']=positive
        if a.negative_control:
            raw=a.input.read_bytes();cases=[]
            for topic in ['sensor_gyro_fifo','sensor_accel_fifo']:
                ids=set();matches=[]
                for start,end,kind in records:
                    payload=raw[start+3:end]
                    if kind==65 and len(payload)>=4 and payload[3:].rstrip(b'\0')==topic.encode():ids.add(struct.unpack_from('<H',payload,1)[0])
                    if kind==68 and len(payload)>=2 and struct.unpack_from('<H',payload)[0] in ids:matches.append((start,end))
                assert len(matches)>250
                start,end=matches[len(matches)//2];fixture=a.out.with_suffix('.missing-'+topic+'.ulg');assert not fixture.exists()
                fixture.write_bytes(raw[:start]+raw[end:])
                try:validate(fixture)
                except AssertionError as error:
                    assert topic in str(error) and 'Missing raw FIFO batch' in str(error)
                    cases.append(dict(topic=topic,rejected=True,removed_whole_record_bytes=end-start,reason=str(error)))
                else:raise AssertionError('Missing whole raw FIFO record was accepted: '+topic)
            doc['negative_control']=dict(rejected=all(row['rejected'] for row in cases),cases=cases)
        doc['passed']=True
    except Exception as error:doc['error']=repr(error);raise
    finally:a.out.write_text(json.dumps(doc,indent=2)+'\n')
    print(json.dumps(doc),flush=True)


if __name__=='__main__':main()
