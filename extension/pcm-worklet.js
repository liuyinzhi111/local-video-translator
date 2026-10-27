class LocalPCMRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.sum = 0;
    this.weight = 0;
    this.frame = new Int16Array(1280); // 80 ms per message.
    this.offset = 0;
    this.port.onmessage = event => {
      if (event.data?.type === 'clear') { this.offset = 0; this.sum = 0; this.weight = 0; }
    };
  }
  process(inputs) {
    const channels = inputs[0];
    if (!channels?.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let sample = 0;
      for (const channel of channels) sample += channel[i];
      sample /= channels.length;
      // Area averaging handles the usual 48 kHz as well as 44.1 kHz output.
      let remaining = 1;
      while (remaining > 1e-8) {
        const take = Math.min(remaining, this.ratio - this.weight);
        this.sum += sample * take;
        this.weight += take;
        remaining -= take;
        if (this.weight >= this.ratio - 1e-8) {
          const value = Math.max(-1, Math.min(1, this.sum / this.ratio));
          this.frame[this.offset++] = Math.round(value * (value < 0 ? 32768 : 32767));
          this.sum = 0;
          this.weight = 0;
          if (this.offset === this.frame.length) {
            this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
            this.frame = new Int16Array(1280);
            this.offset = 0;
          }
        }
      }
    }
    return true;
  }
}
registerProcessor('local-pcm-recorder', LocalPCMRecorder);
