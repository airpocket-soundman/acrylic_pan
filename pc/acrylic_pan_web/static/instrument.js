const $ = id => document.getElementById(id);
const STORAGE_KEY = 'acrylicPanInstrumentV1';
const DEFAULT_NOTES = ['C4', 'D4', 'E4', 'G4', 'A4', 'C5', 'D5', 'E5'];
const MARIO_NOTES = ['E4', 'G4', 'A4', 'A#4', 'B4', 'C5', 'E5', 'G5'];
const DEFAULTS = {instrument:'steel_drum',masterVolume:.70,transpose:0,brightness:.65,attack:.005,decay:.35,sustain:.18,release:.90,echoMix:.18,echoDelay:.18,echoFeedback:.24,velocity:.70,retriggerGuardMs:80,notes:DEFAULT_NOTES};
const NOTE_NAMES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
const STEEL_PARTIALS = [[1,.82,1],[2,.22,.72],[3.01,.09,.44],[4.08,.042,.28],[5.16,.018,.18]];
let settings = loadSettings();
let lastPlayedSequence = null;
let performanceEnabled = false;
let pollBusy = false;
let inferenceLoopRunning = false;
let audio = null;
let hitClearTimer = null;
let cameraStream = null;
const CAMERA_STORAGE_KEY = 'acrylicPanCameraDevice';

async function api(path, body) {
  const options = body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  const response = await fetch(path, options);
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const error = new Error(`${data.error || response.statusText} (${path})`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    const legacyNotes=validNotes(saved.notes)?[...saved.notes]:[...DEFAULT_NOTES];
    const profiles=Array.isArray(saved.mappingProfiles)?saved.mappingProfiles.filter(profile=>profile&&typeof profile.id==='string'&&validNotes(profile.notes)).map(profile=>({id:profile.id,name:String(profile.name||'名称未設定').slice(0,40),notes:[...profile.notes]})):[];
    if(!profiles.length)profiles.push({id:'mapping-default',name:'標準プロファイル',notes:legacyNotes});
    if(!profiles.some(profile=>profile.id==='mapping-mario'||profile.name.toLowerCase()==='mario'))profiles.push({id:'mapping-mario',name:'mario',notes:[...MARIO_NOTES]});
    const activeId=profiles.some(profile=>profile.id===saved.activeMappingProfileId)?saved.activeMappingProfileId:profiles[0].id;
    const active=profiles.find(profile=>profile.id===activeId);
    return {...DEFAULTS,...saved,notes:[...active.notes],mappingProfiles:profiles,activeMappingProfileId:activeId};
  } catch (_) { return {...DEFAULTS,notes:[...DEFAULT_NOTES],mappingProfiles:[{id:'mapping-default',name:'標準プロファイル',notes:[...DEFAULT_NOTES]},{id:'mapping-mario',name:'mario',notes:[...MARIO_NOTES]}],activeMappingProfileId:'mapping-default'}; }
}
function validNotes(notes){return Array.isArray(notes)&&notes.length===8&&notes.every(note=>typeof note==='string');}
function activeProfile(){return settings.mappingProfiles.find(profile=>profile.id===settings.activeMappingProfileId)||settings.mappingProfiles[0];}
function syncActiveProfile(){const profile=activeProfile();if(profile)profile.notes=[...settings.notes];}
function saveSettings() { syncActiveProfile();localStorage.setItem(STORAGE_KEY, JSON.stringify(settings)); }
function newProfileId(){return `mapping-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,7)}`;}
function noteOptions() {
  const notes=[];
  for(let octave=2;octave<=6;octave++) for(const name of NOTE_NAMES) notes.push(`${name}${octave}`);
  return notes;
}
function noteFrequency(note, octaveShift=0) {
  const match=/^([A-G])(#?)(\d)$/.exec(note);
  if(!match) return 440;
  const semitone={C:-9,D:-7,E:-5,F:-4,G:-2,A:0,B:2}[match[1]]+(match[2]?1:0)+(Number(match[3])+octaveShift-4)*12;
  return 440*Math.pow(2,semitone/12);
}

class AudioEngine {
  constructor() {
    const AudioContext=window.AudioContext||window.webkitAudioContext;
    if(!AudioContext) throw new Error('このブラウザはWeb Audioに対応していません。');
    this.context=new AudioContext({latencyHint:'interactive'});
    this.bus=this.context.createGain();
    this.dry=this.context.createGain();
    this.delay=this.context.createDelay(1.0);
    this.feedback=this.context.createGain();
    this.wet=this.context.createGain();
    this.master=this.context.createGain();
    this.compressor=this.context.createDynamicsCompressor();
    this.compressor.threshold.value=-14; this.compressor.knee.value=18; this.compressor.ratio.value=5;
    this.bus.connect(this.dry).connect(this.master);
    this.bus.connect(this.delay); this.delay.connect(this.wet).connect(this.master);
    this.delay.connect(this.feedback).connect(this.delay);
    this.master.connect(this.compressor).connect(this.context.destination);
    this.update();
  }
  async resume(){if(this.context.state!=='running') await this.context.resume();}
  update(){const t=this.context.currentTime;this.master.gain.setTargetAtTime(settings.masterVolume,t,.015);this.dry.gain.setTargetAtTime(1-settings.echoMix*.3,t,.015);this.wet.gain.setTargetAtTime(settings.echoMix,t,.015);this.delay.delayTime.setTargetAtTime(settings.echoDelay,t,.015);this.feedback.gain.setTargetAtTime(settings.echoFeedback,t,.015);}
  envelope(gain, now, level, durationScale=1){
    const attack=Math.max(.001,settings.attack), decay=Math.max(.01,settings.decay*durationScale), release=Math.max(.03,settings.release*durationScale);
    gain.gain.cancelScheduledValues(now);gain.gain.setValueAtTime(.0001,now);gain.gain.exponentialRampToValueAtTime(Math.max(.001,level),now+attack);gain.gain.exponentialRampToValueAtTime(Math.max(.0005,level*settings.sustain),now+attack+decay);gain.gain.exponentialRampToValueAtTime(.0001,now+attack+decay+release);
    return now+attack+decay+release+.05;
  }
  oscillatorVoice(freq, velocity, type, ratios, weights, durationScale=1){
    const now=this.context.currentTime, voice=this.context.createGain(), filter=this.context.createBiquadFilter();
    filter.type='lowpass'; filter.frequency.value=500+settings.brightness*9500; filter.Q.value=.7; voice.connect(filter).connect(this.bus);
    const stop=this.envelope(voice,now,velocity,durationScale);
    ratios.forEach((ratio,index)=>{const osc=this.context.createOscillator(),part=this.context.createGain();osc.type=type;osc.frequency.value=freq*ratio;part.gain.value=weights[index];osc.connect(part).connect(voice);osc.start(now);osc.stop(stop);});
  }
  steel(freq,velocity){
    const now=this.context.currentTime,voice=this.context.createGain(),filter=this.context.createBiquadFilter();
    filter.type='lowpass';filter.frequency.value=Math.min(12000,Math.max(3200,freq*(7+settings.brightness*7)));filter.Q.value=.55;voice.connect(filter).connect(this.bus);const stop=this.envelope(voice,now,velocity,.92);
    const highNoteDamping=Math.max(.48,Math.min(1,700/freq));
    STEEL_PARTIALS.forEach(([ratio,weight,decayScale],index)=>{const osc=this.context.createOscillator(),g=this.context.createGain();const brightnessGain=index===0?1:(.55+settings.brightness*.55);const damping=index===0?1:Math.pow(highNoteDamping,index*.55);const level=Math.max(.0001,weight*brightnessGain*damping);osc.type='sine';osc.frequency.value=freq*ratio;g.gain.setValueAtTime(level,now);if(index>0)g.gain.exponentialRampToValueAtTime(.0001,now+Math.max(.05,settings.decay*decayScale+settings.attack));osc.connect(g).connect(voice);osc.start(now);osc.stop(stop);});
  }
  guitar(freq,velocity){
    const now=this.context.currentTime, length=Math.max(2,Math.floor(this.context.sampleRate/freq)), buffer=this.context.createBuffer(1,length,this.context.sampleRate), data=buffer.getChannelData(0);for(let i=0;i<length;i++)data[i]=Math.random()*2-1;
    const source=this.context.createBufferSource(),filter=this.context.createBiquadFilter(),gain=this.context.createGain();source.buffer=buffer;source.loop=true;filter.type='lowpass';filter.frequency.value=800+settings.brightness*6500;source.connect(filter).connect(gain).connect(this.bus);const stop=this.envelope(gain,now,velocity,.65);source.start(now);source.stop(stop);
  }
  drums(freq,velocity,area){
    const now=this.context.currentTime, gain=this.context.createGain(), osc=this.context.createOscillator();osc.type=area%3===2?'square':'sine';osc.frequency.setValueAtTime(freq*(area<2?.5:1),now);osc.frequency.exponentialRampToValueAtTime(Math.max(45,freq*.35),now+.18);osc.connect(gain).connect(this.bus);gain.gain.setValueAtTime(Math.max(.001,velocity),now);gain.gain.exponentialRampToValueAtTime(.0001,now+.12+settings.release*.35);osc.start(now);osc.stop(now+.18+settings.release*.35);
    if(area%3!==0){const b=this.context.createBuffer(1,Math.floor(this.context.sampleRate*.14),this.context.sampleRate),d=b.getChannelData(0);for(let i=0;i<d.length;i++)d[i]=Math.random()*2-1;const n=this.context.createBufferSource(),f=this.context.createBiquadFilter(),ng=this.context.createGain();n.buffer=b;f.type='highpass';f.frequency.value=900+settings.brightness*5000;ng.gain.setValueAtTime(velocity*.35,now);ng.gain.exponentialRampToValueAtTime(.0001,now+.12);n.connect(f).connect(ng).connect(this.bus);n.start(now);}
  }
  play(note,velocity,area){this.update();const freq=noteFrequency(note,Number(settings.transpose));const v=Math.min(.95,Math.max(.08,velocity));if(settings.instrument==='steel_drum')this.steel(freq,v);else if(settings.instrument==='harpsichord')this.oscillatorVoice(freq,v,'sawtooth',[1,2,3,4],[.55,.22,.12,.06],.35);else if(settings.instrument==='piano')this.oscillatorVoice(freq,v,'triangle',[1,2,3.01],[.72,.20,.08],1.05);else if(settings.instrument==='guitar')this.guitar(freq,v);else this.drums(freq,v,area);}
}

async function ensureAudio(){if(!audio)audio=new AudioEngine();await audio.resume();audio.update();}
function outputValue(id,value){const el=$(id);if(el)el.value=value;}
function renderSettings(){
  $('instrumentSelect').value=settings.instrument;
  for(const id of ['masterVolume','transpose','brightness','attack','decay','sustain','release','echoMix','echoDelay','echoFeedback','velocity','retriggerGuardMs']) $(id).value=settings[id];
  settings.notes.forEach((note,index)=>{$(`areaNote${index}`).value=note;});
  renderProfileControls();updateLabels(); updateGridNotes(); if(audio)audio.update();
}
function renderProfileControls(){
  const profile=activeProfile();
  for(const id of ['mappingProfileSelect','mappingProfileEditSelect']){
    const select=$(id);
    select.replaceChildren(...settings.mappingProfiles.map(item=>{const option=document.createElement('option');option.value=item.id;option.textContent=item.name;return option;}));
    select.value=profile.id;
  }
  $('mappingProfileName').value=profile.name;$('mappingProfileDelete').disabled=settings.mappingProfiles.length===1;
  $('mappingSummary').textContent=settings.notes.map((note,index)=>`エリア${index+1}: ${note}`).join(' / ');
}
function updateLabels(){
  outputValue('masterVolumeValue',`${Math.round(settings.masterVolume*100)}%`);outputValue('transposeValue',settings.transpose>0?`+${settings.transpose}`:String(settings.transpose));outputValue('brightnessValue',`${Math.round(settings.brightness*100)}%`);
  outputValue('attackValue',`${Math.round(settings.attack*1000)} ms`);outputValue('decayValue',`${Math.round(settings.decay*1000)} ms`);outputValue('sustainValue',`${Math.round(settings.sustain*100)}%`);outputValue('releaseValue',`${Math.round(settings.release*1000)} ms`);
  outputValue('echoMixValue',`${Math.round(settings.echoMix*100)}%`);outputValue('echoDelayValue',`${Math.round(settings.echoDelay*1000)} ms`);outputValue('echoFeedbackValue',`${Math.round(settings.echoFeedback*100)}%`);outputValue('velocityValue',`${Math.round(settings.velocity*100)}%`);
  outputValue('retriggerGuardMsValue',`${Math.round(settings.retriggerGuardMs)} ms`);
}
function updateGridNotes(){document.querySelectorAll('#hitGrid [data-class]').forEach(cell=>cell.querySelector('span').textContent=settings.notes[Number(cell.dataset.class)]);}
function setupControls(){
  const options=noteOptions().map(note=>`<option value="${note}">${note}</option>`).join('');for(let i=0;i<8;i++)$(`areaNote${i}`).innerHTML=options;
  $('instrumentSelect').onchange=e=>{settings.instrument=e.target.value;saveSettings();};
  for(const id of ['masterVolume','transpose','brightness','attack','decay','sustain','release','echoMix','echoDelay','echoFeedback','velocity','retriggerGuardMs']) $(id).oninput=e=>{settings[id]=id==='transpose'?Number.parseInt(e.target.value,10):Number(e.target.value);updateLabels();saveSettings();if(audio)audio.update();};
  $('retriggerGuardMs').onchange=()=>api('/api/inference/retrigger',{milliseconds:Number(settings.retriggerGuardMs)}).catch(error=>$('error').textContent=error.message);
  for(let i=0;i<8;i++)$(`areaNote${i}`).onchange=e=>{settings.notes[i]=e.target.value;updateGridNotes();saveSettings();};
  const selectProfile=e=>{syncActiveProfile();settings.activeMappingProfileId=e.target.value;settings.notes=[...activeProfile().notes];renderSettings();saveSettings();};
  $('mappingProfileSelect').onchange=selectProfile;
  $('mappingProfileEditSelect').onchange=selectProfile;
  $('mappingProfileName').oninput=e=>{const profile=activeProfile();profile.name=e.target.value.slice(0,40)||'名称未設定';for(const id of ['mappingProfileSelect','mappingProfileEditSelect'])$(id).selectedOptions[0].textContent=profile.name;saveSettings();};
  $('mappingProfileAdd').onclick=()=>{syncActiveProfile();const profile={id:newProfileId(),name:`プロファイル ${settings.mappingProfiles.length+1}`,notes:[...settings.notes]};settings.mappingProfiles.push(profile);settings.activeMappingProfileId=profile.id;settings.notes=[...profile.notes];renderSettings();saveSettings();};
  $('mappingProfileDelete').onclick=()=>{if(settings.mappingProfiles.length<=1)return;const index=settings.mappingProfiles.findIndex(profile=>profile.id===settings.activeMappingProfileId);settings.mappingProfiles.splice(index,1);const next=settings.mappingProfiles[Math.min(index,settings.mappingProfiles.length-1)];settings.activeMappingProfileId=next.id;settings.notes=[...next.notes];renderSettings();saveSettings();};
  document.querySelectorAll('#hitGrid [data-class]').forEach(cell=>cell.onclick=async()=>{await ensureAudio();playArea(Number(cell.dataset.class),.8,true);});
  $('ascendingPreset').onclick=()=>{settings.notes=[...DEFAULT_NOTES];renderSettings();saveSettings();};
  $('resetSettings').onclick=()=>{const profiles=settings.mappingProfiles,activeMappingProfileId=settings.activeMappingProfileId;settings={...DEFAULTS,notes:[...DEFAULT_NOTES],mappingProfiles:profiles,activeMappingProfileId};renderSettings();saveSettings();};
  $('soundSettingsOpen').onclick=()=>$('soundSettingsDialog').showModal();
  $('mappingSettingsOpen').onclick=()=>$('mappingSettingsDialog').showModal();
  renderSettings();
}

function cameraErrorMessage(error){
  if(error&&error.name==='NotAllowedError')return 'カメラの使用が許可されていません。ブラウザのカメラ権限を確認してください。';
  if(error&&error.name==='NotFoundError')return '使用できるUSBカメラが見つかりません。';
  if(error&&error.name==='NotReadableError')return 'カメラを開始できません。他のアプリが使用していないか確認してください。';
  return `カメラを開始できません: ${error&&error.message?error.message:String(error)}`;
}
function releaseCamera(){
  if(cameraStream)cameraStream.getTracks().forEach(track=>track.stop());
  cameraStream=null;$('usbCamera').srcObject=null;$('cameraPlaceholder').hidden=false;
  $('cameraStart').disabled=false;$('cameraStop').disabled=true;$('cameraState').classList.remove('is-running');
}
function stopCamera(){releaseCamera();$('cameraState').textContent='停止中';}
async function refreshCameras(preferredId=''){
  const select=$('cameraDevice');
  if(!navigator.mediaDevices||!navigator.mediaDevices.enumerateDevices){
    select.replaceChildren(new Option('このブラウザでは利用できません',''));select.disabled=true;$('cameraStart').disabled=true;$('cameraState').textContent='非対応';return [];
  }
  const devices=(await navigator.mediaDevices.enumerateDevices()).filter(device=>device.kind==='videoinput');
  const activeId=cameraStream?.getVideoTracks()[0]?.getSettings().deviceId||'';
  const savedId=localStorage.getItem(CAMERA_STORAGE_KEY)||'';
  const current=preferredId||activeId||select.value||savedId;
  select.replaceChildren(...(devices.length?devices.map((device,index)=>new Option(device.label||`USBカメラ ${index+1}`,device.deviceId)):[new Option('USBカメラが見つかりません','')]));
  if(devices.some(device=>device.deviceId===current))select.value=current;
  select.disabled=!devices.length;$('cameraStart').disabled=!devices.length||Boolean(cameraStream);
  if(!devices.length&&!cameraStream)$('cameraState').textContent='未検出';
  return devices;
}
async function startCamera(){
  if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia)throw new Error('このブラウザはカメラ入力に対応していません。');
  const selectedId=$('cameraDevice').value;
  releaseCamera();$('cameraState').textContent='接続中…';
  const video={width:{ideal:1280},height:{ideal:720},frameRate:{ideal:30}};
  if(selectedId)video.deviceId={exact:selectedId};
  try{
    cameraStream=await navigator.mediaDevices.getUserMedia({audio:false,video});
    $('usbCamera').srcObject=cameraStream;await $('usbCamera').play().catch(()=>{});
    const actualId=cameraStream.getVideoTracks()[0]?.getSettings().deviceId||selectedId;
    if(actualId)localStorage.setItem(CAMERA_STORAGE_KEY,actualId);
    $('cameraPlaceholder').hidden=true;$('cameraStart').disabled=true;$('cameraStop').disabled=false;
    $('cameraState').textContent='映像表示中';$('cameraState').classList.add('is-running');
    await refreshCameras(actualId);
  }catch(error){releaseCamera();$('cameraState').textContent='開始できません';$('cameraPlaceholder').textContent=cameraErrorMessage(error);throw error;}
}
async function setupCamera(){
  try{const devices=await refreshCameras();if(devices.length)$('cameraState').textContent='開始待ち';}
  catch(_){/* The camera card already explains permission and device errors. */}
  $('cameraStart').onclick=()=>startCamera().catch(()=>{});
  $('cameraStop').onclick=stopCamera;
  $('cameraDevice').onchange=async event=>{localStorage.setItem(CAMERA_STORAGE_KEY,event.target.value);if(cameraStream)await startCamera().catch(()=>{});};
  if(navigator.mediaDevices?.addEventListener)navigator.mediaDevices.addEventListener('devicechange',()=>refreshCameras().catch(()=>{}));
  window.addEventListener('pagehide',releaseCamera);
}
function renderScores(outputs=[]){$('scoreBars').innerHTML=Array.from({length:8},(_,i)=>{const raw=Number(outputs[i]||0),height=Math.max(3,Math.min(100,raw*100));return `<div class="score-bar"><i style="height:${height}%"></i><span>${i+1}</span></div>`;}).join('');}
function displayArea(area){
  const note=settings.notes[area]||DEFAULT_NOTES[area],bits=area.toString(2).padStart(3,'0');
  $('lastNote').textContent=`A${area+1} ${note} · ${bits}`;
  document.querySelectorAll('#hitGrid [data-class]').forEach(cell=>cell.classList.toggle('active',Number(cell.dataset.class)===area));
  if(hitClearTimer)clearTimeout(hitClearTimer);
  hitClearTimer=setTimeout(()=>{const cell=document.querySelector(`#hitGrid [data-class="${area}"]`);if(cell)cell.classList.remove('active');},260);
}
function playArea(area,confidence=.8,isPreview=false){
  const note=settings.notes[area]||DEFAULT_NOTES[area],velocity=(1-settings.velocity)*.72+settings.velocity*Math.max(.15,Math.min(1,confidence));
  audio.play(note,velocity,area);displayArea(area);
  $('instrumentStatus').textContent=`${isPreview?'試聴':'演奏'}: エリア${area+1} / ${note} / ${$('instrumentSelect').selectedOptions[0].textContent}`;
}
async function ports(){const data=await api('/api/ports');$('port').innerHTML=data.ports.map(port=>`<option>${port}</option>`).join('');if(data.ports.includes('COM3'))$('port').value='COM3';}
function setButtonState(id,disabled,running=false){const button=$(id);button.disabled=disabled;button.classList.toggle('is-running',running);button.setAttribute('aria-pressed',running?'true':'false');}
function updateActionState(data){const connected=Boolean(data.connected),deviceRunning=Boolean(data.inference_active),playing=deviceRunning&&performanceEnabled;setButtonState('connect',connected,connected);setButtonState('disconnect',!connected);setButtonState('instrumentStart',!connected||playing,playing);setButtonState('instrumentStop',!connected||!deviceRunning);$('port').disabled=connected;}
async function refreshStatus(){
  if(pollBusy)return;pollBusy=true;
  try{const data=await api('/api/status');if(!data.inference_active)performanceEnabled=false;const audible=Boolean(data.inference_active&&performanceEnabled);$('connection').textContent=data.connected?`接続中 ${data.port}`:'未接続';$('connection').classList.toggle('online',data.connected);$('firmwareMode').textContent=data.device_mode==='instrument'?(audible?'高速演奏中':(data.inference_active?'演奏再開待ち':'楽器高速モード')):(data.device_mode==='inference'?'通常推論モード':(data.device_mode==='collection'?'データ採取モード':'モード不明'));$('firmwareMode').classList.toggle('online',audible);
    // latest_ai remains part of /api/status for the inference and collector pages.
    if(data.latest_ai&&!performanceEnabled&&data.latest_ai.sequence!==lastPlayedSequence){lastPlayedSequence=data.latest_ai.sequence;renderScores(data.latest_ai.outputs);displayArea(Number(data.latest_ai.predicted_class));}
    updateActionState(data);if(data.last_error)$('error').textContent=data.last_error;
  }catch(error){$('error').textContent=error.message;}finally{pollBusy=false;}
}
async function inferenceLoop(){
  if(inferenceLoopRunning)return;inferenceLoopRunning=true;
  while(inferenceLoopRunning){
    if(!performanceEnabled){await new Promise(resolve=>setTimeout(resolve,50));continue;}
    try{const result=await api('/api/ai/latest');if(result.sequence!==undefined&&result.sequence!==lastPlayedSequence){lastPlayedSequence=result.sequence;const area=Number(result.predicted_class),score=Math.max(...result.outputs.map(Number));renderScores(result.outputs);playArea(area,score,false);$('error').textContent='';}}
    catch(error){$('error').textContent=error.message;}
    await new Promise(resolve=>setTimeout(resolve,20));
  }
}
async function startPerformance(){try{await ensureAudio();const current=await api('/api/status');lastPlayedSequence=current.latest_ai?current.latest_ai.sequence:null;await api('/api/inference/retrigger',{milliseconds:Number(settings.retriggerGuardMs)});await api('/api/inference/start',{mode:'instrument'});performanceEnabled=true;$('instrumentStatus').textContent='高速演奏中です。アクリル板を連続してたたけます。';$('instrumentStatus').classList.add('playing');$('error').textContent='';await refreshStatus();}catch(error){$('error').textContent=error.message;}}
async function stopPerformance(){try{performanceEnabled=false;await api('/api/inference/stop',{});$('instrumentStatus').textContent='演奏を停止しました。';$('instrumentStatus').classList.remove('playing');await refreshStatus();}catch(error){$('error').textContent=error.message;}}
async function synchronizeStartupState(){try{const current=await api('/api/status');if(current.connected&&current.device_mode==='instrument'&&current.inference_active)await api('/api/inference/stop',{});await refreshStatus();}catch(error){$('error').textContent=error.message;}}

$('refresh').onclick=ports;$('connect').onclick=async()=>{try{await api('/api/connect',{port:$('port').value});await api('/api/device/mode',{mode:'instrument'});$('error').textContent='';await refreshStatus();}catch(error){$('error').textContent=error.message;}};$('disconnect').onclick=async()=>{try{performanceEnabled=false;await api('/api/disconnect',{});$('instrumentStatus').classList.remove('playing');$('error').textContent='';await refreshStatus();}catch(error){$('error').textContent=error.message;}};$('instrumentStart').onclick=startPerformance;$('instrumentStop').onclick=stopPerformance;
document.querySelectorAll('.app-tabs a').forEach(link=>link.addEventListener('click',async event=>{event.preventDefault();try{const current=await api('/api/status');if(!current.connected){window.location.href=link.href;return;}const href=link.getAttribute('href'),mode=href==='/collector.html'?'collection':(href==='/instrument.html'?'instrument':'inference');if(current.collection&&current.collection.active)throw new Error('データ採取中はタブを切り替えられません。先に採取を停止してください。');if(current.inference_active&&current.device_mode!==mode)await api('/api/inference/stop',{});if(current.device_mode!==mode)await api('/api/device/mode',{mode});window.location.href=link.href;}catch(error){$('error').textContent=error.message;}}));
setupControls();setupCamera();renderScores();ports().catch(error=>$('error').textContent=error.message);synchronizeStartupState();setInterval(refreshStatus,500);inferenceLoop();
