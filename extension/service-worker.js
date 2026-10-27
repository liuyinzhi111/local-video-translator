const BASE = 'http://127.0.0.1:18765';
const DEFAULTS = {token: '', language: 'auto', showOriginal: false, fontSize: 25, bottom: 7, left: 50};
const languageConfig = fetch(chrome.runtime.getURL('languages.json')).then(response => response.json());
let operation = false;
let eventChain = Promise.resolve();
let lastCaptionStateAt = 0;

async function settings() {
  return {...DEFAULTS, ...await chrome.storage.local.get(Object.keys(DEFAULTS))};
}

async function state() {
  return (await chrome.storage.session.get('run')).run || {running: false, status: 'idle'};
}

async function setState(patch) {
  const run = {...await state(), ...patch};
  await chrome.storage.session.set({run});
  await chrome.action.setBadgeText({text: run.running ? '译' : ''});
  if (run.running) await chrome.action.setBadgeBackgroundColor({color: '#227462'});
  chrome.runtime.sendMessage({target: 'popup', type: 'state', run}).catch(() => {});
  return run;
}

async function backendStatus() {
  const options = await settings();
  if (!options.token) throw new Error('先在本地控制台复制连接码，粘贴到这里保存。');
  let response;
  try {
    response = await fetch(BASE + '/api/status', {
      headers: {Authorization: 'Bearer ' + options.token}, signal: AbortSignal.timeout(2500),
    });
  } catch {
    throw new Error('本地服务未连接。请先双击桌面的“本地视频翻译”。');
  }
  if (response.status === 401) throw new Error('连接码无效，请从本地控制台重新复制。');
  if (!response.ok) throw new Error('本地服务暂时不可用。');
  return response.json();
}

async function ensureOffscreen() {
  const contexts = await chrome.runtime.getContexts({contextTypes: ['OFFSCREEN_DOCUMENT']});
  if (!contexts.length) {
    await chrome.offscreen.createDocument({
      url: 'offscreen.html', reasons: ['USER_MEDIA', 'AUDIO_PLAYBACK'],
      justification: '持续获取用户指定标签页的音频，在本机生成字幕，并保留网页原声。',
    });
  }
}

async function toOffscreen(message) {
  return chrome.runtime.sendMessage({target: 'offscreen', ...message});
}

async function toPage(tabId, message) {
  if (tabId == null) return;
  try {
    await chrome.tabs.sendMessage(tabId, {target: 'local-captions', ...message});
  } catch (error) {
    const run = await state();
    if (run.running && run.tabId === tabId) {
      await stop('页面字幕连接中断，请在当前页面重新开始。');
    }
  }
}

async function start() {
  if (operation) throw new Error('正在切换，请稍等。');
  operation = true;
  try {
    const run = await state();
    if (run.running) throw new Error('已有网页在翻译，请先停止。');
    const options = await settings();
    const status = await backendStatus();
    if (status.phase !== 'ready') throw new Error(status.error || '模型还在加载，请等待本地控制台显示就绪。');
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id || !/^https?:\/\//.test(tab.url || '')) {
      throw new Error('请在 X 或其他有视频的普通网页上打开插件。');
    }
    await ensureOffscreen();
    await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ['captions.js']});
    const streamId = await chrome.tabCapture.getMediaStreamId({targetTabId: tab.id});
    await setState({running: true, status: 'starting', tabId: tab.id, title: tab.title, error: '', lastCaption: null});
    await toPage(tab.id, {type: 'settings', settings: options});
    const result = await toOffscreen({type: 'start', streamId, token: options.token, language: options.language});
    if (!result?.ok) throw new Error(result?.error || '无法启动音频采集。');
    await setState({status: 'listening'});
    await toPage(tab.id, {type: 'notice', text: '正在收听当前网页，播放视频后会出现中文字幕。'});
  } catch (error) {
    await stop(error.message);
    throw error;
  } finally {
    operation = false;
  }
}

async function stop(error = '') {
  const run = await state();
  // Mark stopped first: content-disconnect cleanup must not recurse.
  await setState({running: false, status: error ? 'error' : 'idle', error, tabId: null});
  await toOffscreen({type: 'stop'}).catch(() => {});
  if (run.tabId != null) {
    await chrome.tabs.sendMessage(run.tabId, {target: 'local-captions', type: 'stop', error}).catch(() => {});
  }
  const contexts = await chrome.runtime.getContexts({contextTypes: ['OFFSCREEN_DOCUMENT']});
  if (contexts.length) await chrome.offscreen.closeDocument().catch(() => {});
}

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  if (msg?.target !== 'sw') return false;
  const own = sender.id === chrome.runtime.id;
  const popup = own && sender.url === chrome.runtime.getURL('popup.html');
  const offscreen = own && sender.url === chrome.runtime.getURL('offscreen.html');
  (async () => {
    if (popup) {
      if (msg.type === 'state') return {run: await state(), settings: await settings(), backend: await backendStatus()};
      if (msg.type === 'start') { await start(); return {}; }
      if (msg.type === 'stop') { await stop(); return {}; }
      if (msg.type === 'save') {
        const patch = {};
        if (typeof msg.settings?.token === 'string') patch.token = msg.settings.token.trim().slice(0, 100);
        if (msg.settings?.language === 'auto' || Object.hasOwn(await languageConfig, msg.settings?.language || '')) patch.language = msg.settings.language;
        if (typeof msg.settings?.showOriginal === 'boolean') patch.showOriginal = msg.settings.showOriginal;
        if (Number.isFinite(msg.settings?.fontSize)) patch.fontSize = Math.min(42, Math.max(16, msg.settings.fontSize));
        const run = await state();
        if (run.running && (patch.language !== undefined || patch.token !== undefined)) {
          const before = await settings();
          if ((patch.language !== undefined && patch.language !== before.language) ||
              (patch.token !== undefined && patch.token !== before.token)) {
            throw new Error('请先停止翻译，再修改原语言或连接码。');
          }
        }
        await chrome.storage.local.set(patch);
        await toPage(run.tabId, {type: 'settings', settings: await settings()});
        return {};
      }
    }
    if (offscreen && msg.type === 'event') {
      eventChain = eventChain.catch(()=>{}).then(async()=>{
        const run = await state();
        if (!run.running) return {};
        const event = msg.event;
        if (event.type === 'error') { await stop(event.message); return {}; }
        await toPage(run.tabId, event);
        if (event.type === 'caption' && (event.final || !event.translation_streaming || Date.now()-lastCaptionStateAt>=250)) {
          lastCaptionStateAt=Date.now();
          await setState({status: 'translating', lastCaption: event});
        }
        if (event.type === 'clear') await setState({lastCaption: null});
        return {};
      });
      return await eventChain;
    }
    if (own && sender.tab?.id != null && msg.type === 'media') {
      const run = await state();
      if (run.running && run.tabId === sender.tab.id) {
        await toOffscreen({type: 'media', action: msg.action, reason: msg.reason});
      }
      return {};
    }
    throw new Error('不支持的请求。');
  })().then(value => respond({ok: true, ...value})).catch(error => respond({ok: false, error: error.message}));
  return true;
});

chrome.commands.onCommand.addListener(async command => {
  if (command !== 'toggle-translation') return;
  try {
    if ((await state()).running) await stop();
    else await start();
  } catch (error) {
    await setState({running: false, status: 'error', error: error.message});
  }
});

chrome.tabs.onRemoved.addListener(async tabId => {
  if ((await state()).tabId === tabId) await stop();
});
chrome.tabs.onUpdated.addListener(async (tabId, change) => {
  if (change.status === 'loading' && (await state()).tabId === tabId) {
    await stop('页面已重新加载，请点击开始翻译。');
  }
});

chrome.runtime.onStartup.addListener(() => setState({running: false, status: 'idle', tabId: null, error: ''}));
