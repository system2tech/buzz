// Legacy macOS runtime bridge: sign worker authorization using the manager key.
// Usage: node sign-worker.mjs RUNTIME DESKTOP_PACKAGE_JSON WORKER_PUBKEY
// Uses Buzz's installed crypto dependencies; no key or authorization is sent out.
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

const [runtime, desktopPackage, worker] = process.argv.slice(2);
if (!runtime || !desktopPackage || !/^[a-f0-9]{64}$/.test(worker ?? '')) {
  throw new Error('Expected runtime, desktop package.json, and worker public key');
}
const desktopRequire = createRequire(resolve(desktopPackage));
const nostrRequire = createRequire(desktopRequire.resolve('nostr-tools/pure'));
const { schnorr } = await import(nostrRequire.resolve('@noble/curves/secp256k1.js'));
const { decode } = desktopRequire('nostr-tools/nip19');
const raw = readFileSync(resolve(runtime, '.buzz-key'), 'utf8').trim();
let key;
if (/^[a-f0-9]{64}$/i.test(raw)) {
  key = Buffer.from(raw, 'hex');
} else {
  const decoded = decode(raw);
  if (decoded.type !== 'nsec') throw new Error('Expected a secret key');
  key = decoded.data;
}
const manager = Buffer.from(schnorr.getPublicKey(key)).toString('hex');
if (manager !== readFileSync(resolve(runtime, '.buzz-pub'), 'utf8').trim()) {
  throw new Error('Manager public and private identities disagree');
}
if (manager === worker) throw new Error('Manager and worker must differ');
const digest = createHash('sha256').update(`nostr:agent-auth:${worker}:`).digest();
const signature = Buffer.from(schnorr.sign(digest, key)).toString('hex');
console.log(JSON.stringify(['auth', manager, '', signature]));
