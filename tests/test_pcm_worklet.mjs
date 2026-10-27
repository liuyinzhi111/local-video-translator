import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../extension/pcm-worklet.js',import.meta.url),'utf8');
for(const rate of [44100,48000]){
  let Recorder;
  const messages=[];
  class Processor{constructor(){this.port={postMessage:buffer=>messages.push(new Int16Array(buffer).slice())};}}
  vm.runInNewContext(source,{AudioWorkletProcessor:Processor,sampleRate:rate,Int16Array,Math,
    registerProcessor:(name,implementation)=>{Recorder=implementation;}});
  const recorder=new Recorder();
  for(let start=0;start<rate;start+=128){
    const frame=Float32Array.from({length:Math.min(128,rate-start)},(_,i)=>.4*Math.sin(2*Math.PI*1000*(start+i)/rate));
    recorder.process([[frame,frame]]);
  }
  const total=messages.reduce((n,frame)=>n+frame.length,0)+recorder.offset;
  assert.ok(Math.abs(total-16000)<=1,`Wrong sample count at ${rate}: ${total}`);
  assert.ok(messages.every(frame=>frame.length===1280));
  const samples=Array.from(messages[0]);
  const rms=Math.sqrt(samples.reduce((n,value)=>n+value*value,0)/samples.length)/32768;
  assert.ok(rms>.26&&rms<.29,`Unexpected level after resampling: ${rms}`);
  recorder.port.onmessage({data:{type:'clear'}});
  assert.equal(recorder.offset,0);
  assert.equal(recorder.weight,0);
  console.log(`PASS: ${rate} Hz stereo -> 16000 Hz PCM; timing, amplitude and reset`);
}
