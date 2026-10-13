// Batches mic samples into 50 ms int16 chunks (800 samples at 16 kHz) for the server.
class Mic extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunk = new Int16Array(800);
    this.filled = 0;
  }

  process(inputs) {
    const samples = inputs[0][0];
    if (!samples) return true;
    for (const sample of samples) {
      this.chunk[this.filled++] = Math.max(-1, Math.min(1, sample)) * 32767;
      if (this.filled === this.chunk.length) {
        this.port.postMessage(this.chunk.slice().buffer);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor("mic", Mic);
