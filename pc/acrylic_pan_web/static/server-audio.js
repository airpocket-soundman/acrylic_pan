/* Plays notes rendered by the server (GeneralUser GS via FluidSynth).
   Callers fall back to their own oscillator voices while a note is not loaded. */
class ServerSampler {
  constructor(context){this.context=context;this.buffers=new Map();this.loads=new Map();this.failed=false;}
  static url(note,area,options={}){
    const query=new URLSearchParams({note,area:String(area),instrument:options.instrument||'steel_drum',transpose:String(options.transpose||0),velocity:'1',volume:'1',brightness:String(options.brightness??.65),attack:String(options.attack??.005),decay:String(options.decay??.35),sustain:String(options.sustain??.18),release:String(options.release??.9),echo_mix:String(options.echoMix??.18),echo_delay:String(options.echoDelay??.18),echo_feedback:String(options.echoFeedback??.24),length:String(options.length??1),v:'sf4'});
    return `/api/audio/note.wav?${query}`;
  }
  cached(url){return this.buffers.get(url)||null;}
  load(url){
    if(this.buffers.has(url))return Promise.resolve(this.buffers.get(url));
    if(!this.loads.has(url))this.loads.set(url,fetch(url,{cache:'force-cache'}).then(response=>{if(!response.ok)throw new Error(`${response.status} ${response.statusText}`);return response.arrayBuffer();}).then(bytes=>this.context.decodeAudioData(bytes)).then(buffer=>{this.buffers.set(url,buffer);this.loads.delete(url);return buffer;}).catch(error=>{this.loads.delete(url);throw error;}));
    return this.loads.get(url);
  }
  async prime(urls){await Promise.all(urls.map(url=>this.load(url).catch(()=>null)));}
  start(buffer,velocity,destination){
    const source=this.context.createBufferSource(),gain=this.context.createGain();
    source.buffer=buffer;gain.gain.value=Math.min(.95,Math.max(.08,velocity));
    source.connect(gain).connect(destination);source.start();return source;
  }
}
/* Soft clipper: protects against clipping when notes overlap, without a compressor's look-ahead delay. */
ServerSampler.softClipper = function (context) {
  const shaper = context.createWaveShaper(), curve = new Float32Array(2049);
  for (let i = 0; i < curve.length; i++) { const x = i / 1024 - 1; curve[i] = Math.tanh(x * 1.2) / Math.tanh(1.2); }
  shaper.curve = curve;
  return shaper;
};
ServerSampler.contextOptions = {latencyHint: 0};
window.ServerSampler = ServerSampler;
