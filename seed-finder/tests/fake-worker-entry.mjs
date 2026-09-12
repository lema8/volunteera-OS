/* Entry shim: runs the REAL js/worker.js inside a node:worker_threads
 * thread with a `self` shim, so SearchRunner can be tested end-to-end. */
import { parentPort } from 'node:worker_threads';
import fs from 'node:fs';

globalThis.self = {
  postMessage: (m) => parentPort.postMessage(m),
};

/* worker.js fetch()es the wasm — serve it from disk */
const realFetch = globalThis.fetch;
globalThis.fetch = async (url) => {
  const u = String(url);
  if (u.startsWith('file://')) {
    const bytes = fs.readFileSync(new URL(u));
    return {
      ok: true, status: 200,
      arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
    };
  }
  return realFetch(url);
};

parentPort.on('message', (m) => globalThis.self.onmessage?.({ data: m }));

await import(new URL('../js/worker.js', import.meta.url));
