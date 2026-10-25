/* Two workflows for the single 60-output Solist-AI ODL model. */
(() => {
  const el=id=>document.getElementById(id), sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const edge=[];
  for(const x of [25,75,125,175,225,275,325,375]){
    const ys=(x===225||x===275)?[35,75,125,175,225,275]:[25,75,125,175,225,275];
    for(const y of ys)edge.push([x,y]);
  }
  const support=edge.concat([50,150,250,350].flatMap(x=>[50,150,250].map(y=>[x,y]))).sort((a,b)=>a[0]-b[0]||a[1]-b[1]);
  const centres=[6,21,36,51,7,22,37,52,8,23,38,53];
  let running=false,cancelled=false,marker=null;
  const areaOf=position=>{const [x,y]=support[position];return Math.floor(y/100)*4+Math.floor(x/100);};
  const statusKind=value=>value==='quick'?'高速':value==='precise'?'精密':'—';

  function message(text){el('odlCalibrationProgress').textContent=text;}
  function busy(value){
    running=value;
    for(const id of ['odlCalibrationStart','odlCalibrationStatusButton','odlCalibrationFactory','odlCalibrationKind','odlCalibrationRounds'])el(id).disabled=value;
    el('odlCalibrationCancel').disabled=!value;
  }
  function showStatus(status){
    if(!status)return;
    const saved=status.saved_valid?`保存済み ${status.saved_count}打・${statusKind(status.saved_kind)}`:'保存なし（工場モデル）';
    el('odlCalibrationModel').textContent=`使用中: ${status.source==='working'?'校正中':saved}`;
  }
  function mark(position){
    if(marker)marker.remove();marker=null;
    if(position<0)return;
    const [x,y]=support[position],panel=el('positionPanel');
    marker=document.createElement('div');marker.className='position-calibration-target';
    marker.style.left=`${100*x/400}%`;marker.style.top=`${100*y/300}%`;panel.appendChild(marker);
  }
  async function waitResult(before){
    for(let waited=0;waited<120000;waited+=150){
      if(cancelled)throw new Error('中止しました');
      const state=await api('/api/calibration/state');
      if(state.error)throw new Error(state.error);
      if(state.result_count>before)return state.result;
      await sleep(150);
    }
    throw new Error('打撃が検出されませんでした（タイムアウト）');
  }
  async function trainHit(target,kind){
    const state=await api('/api/calibration/state');
    await api('/api/calibration/arm',{target,kind});
    return waitResult(state.result_count);
  }
  async function enter(){
    const current=await api('/api/status');
    if(!current.connected)throw new Error('先にCOMポートへ接続してください。');
    if(current.inference_active)await stopPerformance();
    return api('/api/calibration/enter',{});
  }
  async function leave(returnMode){mark(-1);try{await api('/api/device/mode',{mode:returnMode});}catch(_){}if(typeof refreshStatus==='function')await refreshStatus();}
  async function run(){
    if(running)return;
    const kind=el('odlCalibrationKind').value,rounds=Number(el('odlCalibrationRounds').value)||1,returnMode=modeForSource();
    const targets=kind==='quick'?centres.map((_,area)=>area):support.map((_,position)=>position);
    cancelled=false;busy(true);el('error').textContent='';let began=false;
    try{
      showStatus(await enter());
      showStatus(await api('/api/calibration/begin',{kind}));began=true;
      let completed=0;
      for(let round=0;round<rounds;round++)for(const target of targets){
        const position=kind==='quick'?centres[target]:target,[x,y]=support[position];mark(position);
        message(`[${statusKind(kind)} ${round+1}/${rounds}] X${x} / Y${y} mm を叩いてください（${completed+1}/${targets.length*rounds}）`);
        const result=await trainHit(target,kind);
        if(!result.trained)throw new Error('ファームウェアが打撃を学習できませんでした。');
        completed++;
        try{playArea(areaOf(result.predicted_class),1,true);}catch(_){}
      }
      const status=await api('/api/calibration/commit',{});began=false;showStatus(status);
      message(`${statusKind(kind)}校正を${status.saved_count}打で保存しました。以前の校正結果は置き換えられました。`);
    }catch(error){el('error').textContent=error.message;if(began){try{showStatus(await api('/api/calibration/discard',{}));}catch(_){}}message('校正を終了しました（未保存）。');}
    finally{busy(false);await leave(returnMode);}
  }
  async function status(){const returnMode=modeForSource();if(running)return;busy(true);try{showStatus(await enter());message('保存状態を取得しました。');}catch(error){el('error').textContent=error.message;}finally{busy(false);await leave(returnMode);}}
  async function factoryReset(){const returnMode=modeForSource();if(running||!confirm('保存済みの60位置校正を消去して工場モデルへ戻しますか？'))return;busy(true);try{await enter();showStatus(await api('/api/calibration/factory_reset',{}));message('工場モデルへ戻しました。');}catch(error){el('error').textContent=error.message;}finally{busy(false);await leave(returnMode);}}
  el('odlCalibrationStart').onclick=run;el('odlCalibrationCancel').onclick=()=>{cancelled=true;};el('odlCalibrationStatusButton').onclick=status;el('odlCalibrationFactory').onclick=factoryReset;
})();
