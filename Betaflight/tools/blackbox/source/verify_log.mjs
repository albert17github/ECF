import fs from 'node:fs';
import crypto from 'node:crypto';
import { FlightLogParser } from './parser/src/flightlog_parser.js';
const [input, prefix] = process.argv.slice(2);
if (!input || !prefix) throw new Error('INPUT OUTPUT_PREFIX required');
const raw = fs.readFileSync(input);
const parser = new FlightLogParser(new Uint8Array(raw));
const frames = [];
parser.onFrameReady = (valid, values, kind, offset, size) => {
  frames.push({valid,kind,offset,size,
    ...(['I','P'].includes(kind) && values ? {iteration:values[0],time_us:values[1],values:[...values]} : {}),
    ...(kind === 'E' ? {event:values} : {})});
};
parser.parseHeader(0,raw.length);
parser.parseLogData(false);
// Upstream calls complete() after reading the next frame's marker.
// Its callback size therefore includes one lookahead byte except at EOF.
for(let j=0;j<frames.length;j++) {
  const f=frames[j];
  f.parser_reported_size=f.size;
  if(j<frames.length-1)f.size--;
}
let boundary=0;
while(raw.subarray(boundary,boundary+2).toString()==='H ') boundary=raw.indexOf(10,boundary)+1;
const header=raw.subarray(0,boundary).toString('ascii');
fs.writeFileSync(prefix+'-header.txt',header);
const mains=frames.filter(f=>['I','P'].includes(f.kind)&&f.valid);
const cfg=parser.sysConfig;
const I=cfg.frameIntervalI,P=cfg.frameIntervalPDenom,N=cfg.frameIntervalPNum;
const shouldLog=i=>i%I===0 || ((i%I+N-1)%P<N);
const gaps=[];
for(let j=1;j<mains.length;j++){
  let next=mains[j-1].iteration+1;
  while(!shouldLog(next))next++;
  if(mains[j].iteration!==next) gaps.push({before:mains[j-1].iteration,expected:next,actual:mains[j].iteration});
}
let offset=boundary;
const byteGaps=[];
for(const frame of frames) {
  if(frame.offset!==offset)byteGaps.push({expected:offset,actual:frame.offset});
  offset=frame.offset+frame.size;
}
const names=parser.frameDefs.I.name;
const interesting=names.map((n,i)=>({n,i})).filter(({n})=>/magADC|baroAlt|gyroADC|accSmooth/.test(n));
const ranges=Object.fromEntries(interesting.map(({n,i})=>[n,[Math.min(...mains.map(f=>f.values[i])),Math.max(...mains.map(f=>f.values[i]))]]));
const summary={
  official_parser:'betaflight/blackbox-log-viewer',
  source_commit:'a84755c5e897c1a3a580424b64c0df066c12c6b4',
  input,bytes:raw.length,sha256:crypto.createHash('sha256').update(raw).digest('hex'),
  header_bytes:boundary,firmware:cfg.firmwareRevision,
  intervals:{I,P,N},main_frames:mains.length,
  duration_seconds:(mains.at(-1).time_us-mains[0].time_us)/1e6,
  invalid_frames:frames.filter(f=>!f.valid).map(({values,...f})=>f),
  sample_schedule_gaps:gaps,byte_gaps:byteGaps,body_bytes_covered:offset===raw.length,
  normal_end_marker:raw.subarray(-13).equals(Buffer.from('E\xffEnd of log\0','latin1')),
  frame_size_note:'Official parser callback includes the next marker as lookahead except at EOF; coverage subtracts that byte.',
  sensor_ranges:ranges,
  parser_corrupt_frames:parser.stats.totalCorruptFrames,
  intentionally_absent_iterations:parser.stats.intentionallyAbsentIterations,
  frame_counts:Object.fromEntries(Object.entries(parser.stats.frame).map(([k,v])=>[k,{valid:v.validCount,corrupt:v.corruptCount,desync:v.desyncCount}]))};
summary.passed=mains.length>0&&summary.invalid_frames.length===0&&gaps.length===0&&byteGaps.length===0&&summary.body_bytes_covered&&summary.normal_end_marker;
fs.writeFileSync(prefix+'-frames.jsonl',frames.map(f=>JSON.stringify(f)).join('\n')+'\n');
fs.writeFileSync(prefix+'-summary.json',JSON.stringify(summary,null,2)+'\n');
fs.writeFileSync(prefix+'-parser-stats.json',JSON.stringify(parser.stats,null,2)+'\n');
console.log(JSON.stringify(summary));
if(!summary.passed)process.exitCode=1;
