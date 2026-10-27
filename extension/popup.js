const $ = id => document.getElementById(id);
let initialized = false;
async function call(type, extra = {}) {
  const result = await chrome.runtime.sendMessage({target: 'sw', type, ...extra});
  if (!result?.ok) throw new Error(result?.error || '插件连接失败。');
  return result;
}
function showError(error) { $('error').textContent = error?.message || ''; }
async function refresh() {
  const run = (await chrome.storage.session.get('run')).run || {};
  $('start').disabled = !!run.running;
  $('stop').disabled = !run.running;
  $('language').disabled = !!run.running;
  try {
    const data = await call('state');
    if (!initialized) {
      $('language').value = data.settings.language;
      $('original').checked = data.settings.showOriginal;
      $('size').value = data.settings.fontSize;
      $('size-label').textContent = data.settings.fontSize;
      initialized = true;
    }
    const phases = {idle:'模型未加载', loading_asr:'正在加载识别模型', loading_translation:'正在加载翻译模型', warming_up:'模型预热中', unloading:'正在释放显存', error:'模型加载失败'};
    $('status').textContent = run.running ? '正在翻译当前网页' : data.backend.phase === 'ready' ? '本地模型已就绪' : phases[data.backend.phase] || '连接中';
    $('dot').className = data.backend.phase === 'ready' ? 'ready' : '';
    $('start').disabled = !!run.running || data.backend.phase !== 'ready';
    $('detail').textContent = run.running ? (run.title || '当前网页') : '自动识别视频语言，翻译成中文。支持西班牙语、意大利语、英语、法语等。';
    if (run.error) $('detail').textContent = run.error;
    if (data.backend.error) showError(new Error(data.backend.error));
    const supported = data.backend.supported_languages;
    if (supported) for (const option of $('language').options) {
      option.disabled = option.value !== 'auto' && !supported.includes(option.value);
      option.title = option.disabled ? '该语言请在本地控制台切换兼容模式' : '';
    }
    if (supported && $('language').value!=='auto' && !supported.includes($('language').value)) {
      $('start').disabled=true;
      $('detail').textContent='当前语言需要在本地控制台切换兼容模式，或改选自动识别支持的语言。';
    }
  } catch (error) {
    $('status').textContent = '本地服务未连接'; $('dot').className = 'error';
    $('detail').textContent = error.message; $('start').disabled = true;
    const stored = await chrome.storage.local.get(['token', 'language', 'showOriginal', 'fontSize']);
    if (!stored.token) $('connection').open = true;
    if (!initialized) {
      $('language').value = stored.language || 'auto';
      $('original').checked = !!stored.showOriginal;
      $('size').value = stored.fontSize || 25; $('size-label').textContent = stored.fontSize || 25;
      initialized = true;
    }
  }
}
$('save').onclick = async () => {
  try { await call('save', {settings: {token: $('token').value}}); $('token').value=''; showError(); await refresh(); }
  catch(error) { showError(error); }
};
$('start').onclick = async () => {
  $('start').disabled = true; showError();
  try { await call('save', {settings: {language: $('language').value}}); await call('start'); }
  catch(error) { showError(error); }
  await refresh();
};
$('stop').onclick = async () => { try { await call('stop'); showError(); } catch(error) { showError(error); } await refresh(); };
$('language').onchange = async () => { try { await call('save', {settings:{language:$('language').value}}); } catch(error) { showError(error); } };
$('original').onchange = async () => { try { await call('save', {settings:{showOriginal:$('original').checked}}); } catch(error) { showError(error); } };
$('size').oninput = () => { $('size-label').textContent = $('size').value; };
$('size').onchange = async () => { try { await call('save', {settings:{fontSize:Number($('size').value)}}); } catch(error) { showError(error); } };
fetch('languages.json').then(response=>response.json()).then(languages=>{
  for (const [code, entry] of Object.entries(languages)) {
    const option=document.createElement('option');option.value=code;option.textContent=entry.label;
    $('language').appendChild(option);
  }
  refresh(); setInterval(refresh,1800);
});
