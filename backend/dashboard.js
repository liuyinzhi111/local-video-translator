const $ = id => document.getElementById(id);
let token = location.hash.slice(1) || sessionStorage.getItem('local-translation-token') || '';
if (location.hash) {
  sessionStorage.setItem('local-translation-token', token);
  history.replaceState(null, '', location.pathname);
}
let exited = false;
async function api(path, method='GET', data) {
  const response = await fetch('/api/' + path, {method, headers:{Authorization:'Bearer ' + token, 'Content-Type':'application/json'}, body:data?JSON.stringify(data):undefined, signal:AbortSignal.timeout(4000)});
  if (!response.ok) { let data = await response.json(); throw new Error(data.detail || '请求失败'); }
  return response.json();
}
function toast(text) { $('toast').textContent = text; $('toast').className = 'show'; setTimeout(()=>$('toast').className='',2200); }
async function refresh() {
  if (exited) return;
  if (!token) {
    $('phase').textContent='请从桌面入口打开控制台';
    $('phase-detail').textContent='双击“本地视频翻译”会自动连接本机服务。';
    $('copy-token').disabled=true;
    return;
  }
  try {
    const data = await api('status');
    const names={idle:'模型尚未加载',loading_asr:'正在加载语音识别模型…',loading_translation:'正在加载中文翻译模型…',warming_up:'正在预热模型…',ready:data.running?'正在翻译网页声音':'本地模型已就绪',unloading:'正在释放显存…',error:'模型加载失败'};
    $('phase').textContent=names[data.phase] || data.phase;
    $('dot').className=data.phase==='ready'?'ready':data.phase==='error'?'error':'';
    $('phase-detail').textContent=data.phase==='ready' ? data.running ? `已完成 ${data.completed_segments} 段字幕。请在 Chrome 插件中停止当前翻译。` : '现在可以打开 X 视频，在 Chrome 插件中点击“开始翻译”。' : '首次加载可能需要一些时间，模型就绪后会自动更新。';
    $('extension-path').textContent=data.extension_path;
    $('asr-name').textContent=data.asr;
    $('asr-detail').textContent=data.asr_backend==='nemotron'?'持续识别 · NVIDIA 独显':'分段识别 · 更多语言';
    if(document.activeElement!==$('mode'))$('mode').value=data.asr_backend;
    $('mode').disabled=data.running||!['ready','idle','error'].includes(data.phase);
    $('mode-apply').disabled=$('mode').disabled;
    $('load').disabled=!['idle','error'].includes(data.phase);
    $('unload').disabled=data.phase!=='ready'||data.running;
    $('copy-token').disabled=false;
    $('error').textContent=data.error || '';
  } catch (error) {
    $('phase').textContent='本地服务未连接'; $('dot').className='error';
    $('phase-detail').textContent='请双击桌面的“本地视频翻译”重新打开。';
    $('error').textContent=error.message;
  }
}
$('copy-token').onclick=async()=>{try{await navigator.clipboard.writeText(token);toast('连接码已复制，粘贴到 Chrome 插件即可');}catch{$('error').textContent='浏览器阻止了复制，请允许此本地页面使用剪贴板。';}};
$('copy-path').onclick=async()=>{await navigator.clipboard.writeText($('extension-path').textContent);toast('插件文件夹路径已复制');};
$('load').onclick=async()=>{try{await api('load','POST');refresh();}catch(e){$('error').textContent=e.message;}};
$('unload').onclick=async()=>{try{await api('unload','POST');refresh();}catch(e){$('error').textContent=e.message;}};
$('mode-apply').onclick=async()=>{try{await api('mode','POST',{backend:$('mode').value});refresh();}catch(e){$('error').textContent=e.message;}};
$('shutdown').onclick=async()=>{try{await api('shutdown','POST');exited=true;$('phase').textContent='服务已退出';$('phase-detail').textContent='模型将从显存释放，下次双击桌面入口即可启动。';$('dot').className='';for(const b of document.querySelectorAll('.controls button'))b.disabled=true;}catch(e){$('error').textContent=e.message;}};
refresh();setInterval(refresh,1800);
