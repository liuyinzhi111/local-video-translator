(() => {
  const KEY = '__localVideoTranslation';
  window[KEY]?.destroy();
  const host = document.createElement('div');
  host.id = 'local-video-translation-overlay';
  host.style.cssText = 'all:initial;position:fixed;left:50%;bottom:7vh;transform:translateX(-50%);z-index:2147483647;max-width:88vw;width:max-content;pointer-events:auto;';
  const shadow = host.attachShadow({mode: 'closed'});
  shadow.innerHTML = `<style>
    :host{color-scheme:dark}*{box-sizing:border-box}.box{font-family:Arial,"Microsoft YaHei",sans-serif;background:rgba(13,19,26,.88);border:1px solid rgba(255,255,255,.16);border-radius:12px;padding:12px 20px 11px;box-shadow:0 6px 30px #0005;max-width:min(1000px,86vw);cursor:move;touch-action:none;color:#fff;line-height:1.5;font-size:25px;text-align:center;backdrop-filter:blur(5px)}
    .row+.row{margin-top:6px}.draft .zh{color:#dae8e3}.source{font-size:.65em;color:#b3c2c8;margin-top:3px}.meta{font-size:11px;color:#a5b6b8;display:flex;gap:16px;justify-content:center;margin-top:6px}.error{color:#ffb6a4}.notice{font-size:15px;color:#c7ddd6}.hidden{display:none}
  </style><div class="box" aria-live="polite"><div class="lines"></div><div class="notice">正在连接本地模型…</div><div class="meta"><span>本地翻译 · 可拖动</span><span class="phase"></span></div></div>`;
  const box = shadow.querySelector('.box'), linesEl = shadow.querySelector('.lines');
  const notice = shadow.querySelector('.notice'), phase = shadow.querySelector('.phase');
  const lines = new Map();
  const rowNodes = new Map();
  let options = {showOriginal: false, fontSize: 25, bottom: 7, left: 50};
  let epoch = 0, activeVideo = null, activeSource = '', destroyed = false, fadeTimer = null;
  const cleanups = [];
  const tracked = new WeakSet();

  function mount() {
    const target = document.fullscreenElement || document.documentElement;
    target.appendChild(host);
  }
  function resetDisplay() { lines.clear(); rowNodes.clear(); linesEl.replaceChildren(); phase.textContent = ''; }
  function showNotice(text, error = false) {
    notice.textContent = text;
    notice.className = error ? 'notice error' : 'notice';
    host.style.display = '';
  }
  function readableTail(text, maximum) {
    if(text.length<=maximum)return text;
    const earliest=text.length-maximum;
    // Older clauses have already appeared while the speaker was talking. Roll
    // forward at punctuation, retaining the full transcript in the event itself.
    for(let index=earliest;index<text.length-25;index++) {
      if(/[。！？；，]/.test(text[index]))return '…'+text.slice(index+1).trim();
    }
    return '…'+text.slice(earliest);
  }
  function render() {
    let visible = [...lines.values()].slice(-2);
    if (visible.reduce((count, entry) => count + entry.translation.length, 0) > 110) visible = visible.slice(-1);
    const visibleIds = new Set(visible.map(entry=>entry.id));
    for (const [id, node] of rowNodes) if (!visibleIds.has(id)) {node.row.remove();rowNodes.delete(id);}
    for (const entry of visible) {
      let nodes=rowNodes.get(entry.id);
      if (!nodes) {
        const row=document.createElement('div'), zh=document.createElement('div'), original=document.createElement('div');
        zh.className='zh';original.className='source';row.append(zh,original);linesEl.appendChild(row);
        nodes={row,zh,original};rowNodes.set(entry.id,nodes);
      }
      const {row,zh,original}=nodes;
      row.className = 'row' + (entry.final ? '' : ' draft');
      const displayed=readableTail(entry.translation,100);
      if(zh.textContent!==displayed)zh.textContent=displayed;
      original.hidden=!options.showOriginal;
      const originalText=entry.source.length>240?'…'+entry.source.slice(-240):entry.source;
      if(original.textContent!==originalText)original.textContent=originalText;
    }
    notice.className = lines.size ? 'notice hidden' : 'notice';
    host.style.display = '';
  }
  function applyOptions(patch) {
    options = {...options, ...patch};
    box.style.fontSize = Math.min(42, Math.max(16, options.fontSize)) + 'px';
    host.style.left = Math.min(90, Math.max(10, options.left || 50)) + 'vw';
    host.style.bottom = Math.min(85, Math.max(0, options.bottom ?? 7)) + 'vh';
    render();
  }
  function onMessage(msg) {
    if (msg?.target !== 'local-captions') return;
    if (msg.type === 'settings') applyOptions(msg.settings);
    else if (msg.type === 'notice') showNotice(msg.text);
    else if (msg.type === 'clear') { epoch = msg.epoch; resetDisplay(); showNotice('正在收听新的片段…'); }
    else if (msg.type === 'caption') {
      if (msg.epoch < epoch) return;
      if (msg.epoch > epoch) { epoch = msg.epoch; resetDisplay(); }
      clearTimeout(fadeTimer);
      if (msg.replace_id != null) lines.delete(msg.replace_id);
      if (!msg.translation) lines.delete(msg.id);
      else {
        const before=lines.get(msg.id);
        // During a replacement generation, keep the readable previous draft until
        // the new stream catches up; don't shrink a full subtitle to three letters.
        const translation=msg.translation_streaming&&before&&msg.translation.length<before.translation.length
          ?before.translation:msg.translation;
        lines.set(msg.id, {...msg,translation});
      }
      while (lines.size > 2) lines.delete(lines.keys().next().value);
      render();
      phase.textContent = msg.waiting > 1 ? '翻译跟进中' : msg.final ? '' : msg.streaming ? '实时字幕 · 当前短句可能修正' : '识别中，字幕可能修正';
      if (msg.final) fadeTimer = setTimeout(() => { host.style.display = 'none'; }, 9000);
    } else if (msg.type === 'stop') {
      if (msg.error) { resetDisplay(); showNotice(msg.error, true); setTimeout(destroy, 10000); }
      else destroy();
    }
  }
  function media(action, reason = '') {
    if (destroyed) return;
    chrome.runtime.sendMessage({target: 'sw', type: 'media', action, reason}).catch(() => destroy());
  }
  function watch(video) {
    if (tracked.has(video)) return;
    tracked.add(video);
    const onPlay = () => {
      const src = video.currentSrc || video.src;
      if (activeVideo !== video || activeSource !== src) {
        activeVideo = video; activeSource = src;
        resetDisplay(); showNotice('正在收听当前视频…');
        media('reset', '切换视频');
      } else media('resume');
    };
    const onPause = () => { if (activeVideo === video) media('pause'); };
    const onSeeking = () => {
      if (activeVideo !== video) return;
      resetDisplay(); showNotice('已跳转，等待新的字幕…');
      media('reset', '视频进度跳转');
    };
    const onSeeked = () => { if (activeVideo === video) media(video.paused ? 'pause' : 'resume'); };
    const onEmpty = () => { if (activeVideo === video) { resetDisplay(); media('reset', '视频内容更换'); } };
    const events = {play: onPlay, pause: onPause, ended: onPause, seeking: onSeeking, seeked: onSeeked, emptied: onEmpty};
    for (const [name, fn] of Object.entries(events)) video.addEventListener(name, fn);
    cleanups.push(() => { for (const [name, fn] of Object.entries(events)) video.removeEventListener(name, fn); });
    if (!video.paused && !video.ended && !video.muted) {
      activeVideo = video; activeSource = video.currentSrc || video.src;
    }
  }
  function scan() { document.querySelectorAll('video,audio').forEach(watch); }
  const observer = new MutationObserver(scan);
  observer.observe(document.documentElement, {childList: true, subtree: true});
  document.addEventListener('fullscreenchange', mount);
  chrome.runtime.onMessage.addListener(onMessage);
  chrome.storage.local.get(['showOriginal', 'fontSize', 'bottom', 'left']).then(applyOptions);
  let dragging = null;
  box.addEventListener('pointerdown', event => {
    dragging = {x: event.clientX, y: event.clientY, left: parseFloat(host.style.left), bottom: parseFloat(host.style.bottom)};
    box.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  box.addEventListener('pointermove', event => {
    if (!dragging) return;
    options.left = Math.min(90, Math.max(10, dragging.left + 100 * (event.clientX - dragging.x) / innerWidth));
    options.bottom = Math.min(85, Math.max(0, dragging.bottom - 100 * (event.clientY - dragging.y) / innerHeight));
    host.style.left = options.left + 'vw'; host.style.bottom = options.bottom + 'vh';
  });
  box.addEventListener('pointerup', () => {
    if (dragging) chrome.storage.local.set({left: options.left, bottom: options.bottom});
    dragging = null;
  });
  function destroy() {
    if (destroyed) return;
    destroyed = true; clearTimeout(fadeTimer); observer.disconnect();
    document.removeEventListener('fullscreenchange', mount);
    chrome.runtime.onMessage.removeListener(onMessage);
    cleanups.forEach(fn => fn());
    host.remove(); delete window[KEY];
  }
  window[KEY] = {destroy};
  scan(); mount();
})();
