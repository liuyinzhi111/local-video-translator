let stream = null, context = null, recorder = null, source = null, socket = null;
let running = false, resetting = 0, paused = false, heartbeat = null;

function forward(event) {
  chrome.runtime.sendMessage({target: 'sw', type: 'event', event}).catch(() => {});
}

async function stop() {
  running = false;
  clearInterval(heartbeat);
  heartbeat = null;
  if (socket) { socket.onclose = null; socket.onerror = null; socket.close(); socket = null; }
  if (recorder) { recorder.port.onmessage = null; recorder.disconnect(); recorder = null; }
  if (source) { source.disconnect(); source = null; }
  if (stream) { stream.getTracks().forEach(track => track.stop()); stream = null; }
  if (context) { await context.close().catch(() => {}); context = null; }
  resetting = 0;
  paused = false;
}

async function start(msg) {
  await stop();
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {mandatory: {chromeMediaSource: 'tab', chromeMediaSourceId: msg.streamId}}, video: false,
    });
    context = new AudioContext({latencyHint: 'interactive'});
    source = context.createMediaStreamSource(stream);
    // tabCapture suppresses the tab's direct output: restore it at the native rate.
    source.connect(context.destination);
    await context.resume();
    await context.audioWorklet.addModule('pcm-worklet.js');
    recorder = new AudioWorkletNode(context, 'local-pcm-recorder');
    source.connect(recorder);
    const silent = context.createGain();
    silent.gain.value = 0;
    recorder.connect(silent).connect(context.destination);
    socket = new WebSocket('ws://127.0.0.1:18765/stream');
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('本地音频连接超时。')), 6000);
      socket.onopen = () => socket.send(JSON.stringify({token: msg.token, language: msg.language}));
      socket.onerror = () => { clearTimeout(timeout); reject(new Error('无法连接本地模型服务。')); };
      socket.onclose = event => { clearTimeout(timeout); reject(new Error(event.reason || '本地连接已关闭。')); };
      socket.onmessage = event => {
        const data = JSON.parse(event.data);
        if (data.type === 'ready') { clearTimeout(timeout); resolve(); }
        else if (data.type === 'error') { clearTimeout(timeout); reject(new Error(data.message)); }
      };
    });
    running = true;
    recorder.port.onmessage = event => {
      if (!running || paused || socket?.readyState !== WebSocket.OPEN) return;
      if (socket.bufferedAmount > 16000 * 2 * 3) {
        forward({type: 'error', message: '本地连接积压，已停止采集。请重新开始。'});
        stop();
        return;
      }
      socket.send(event.data);
    };
    socket.onmessage = event => {
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      if (data.type === 'ping') { socket.send(JSON.stringify({type: 'pong'})); return; }
      if (data.type === 'clear') resetting = Math.max(0, resetting - 1);
      if (data.type === 'caption' && resetting) return;
      forward(data);
      if (data.type === 'error') stop();
    };
    socket.onclose = () => {
      if (running) { forward({type: 'error', message: '本地服务连接中断，采集已停止。'}); stop(); }
    };
    socket.onerror = () => { if (running) forward({type: 'error', message: '本地连接发生错误。'}); };
    stream.getAudioTracks()[0].onended = () => {
      if (running) { forward({type: 'error', message: '网页音频采集已结束。'}); stop(); }
    };
    heartbeat = setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: 'pong'}));
    }, 5000);
  } catch (error) {
    await stop();
    throw error;
  }
}

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  if (msg?.target !== 'offscreen') return false;
  (async () => {
    if (msg.type === 'start') await start(msg);
    else if (msg.type === 'stop') await stop();
    else if (msg.type === 'media' && running && socket?.readyState === WebSocket.OPEN) {
      if (msg.action === 'reset') {
        resetting++;
        paused = false;
        recorder.port.postMessage({type: 'clear'});
        socket.send(JSON.stringify({type: 'reset', reason: msg.reason || '切换视频'}));
        socket.send(JSON.stringify({type: 'resume'}));
      } else if (msg.action === 'pause') {
        paused = true;
        recorder.port.postMessage({type: 'clear'});
        socket.send(JSON.stringify({type: 'pause'}));
      } else if (msg.action === 'resume') {
        paused = false;
        recorder.port.postMessage({type: 'clear'});
        socket.send(JSON.stringify({type: 'resume'}));
      }
    }
    return {ok: true};
  })().then(respond).catch(error => respond({ok: false, error: error.message}));
  return true;
});
