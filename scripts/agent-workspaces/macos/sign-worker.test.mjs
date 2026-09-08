import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const desktop = resolve(here, '../../../desktop/package.json');
const desktopRequire = createRequire(desktop);
const { generateSecretKey, getPublicKey } = desktopRequire('nostr-tools/pure');
const { nsecEncode } = desktopRequire('nostr-tools/nip19');
const cryptoRequire = createRequire(desktopRequire.resolve('nostr-tools/pure'));
const { schnorr } = await import(cryptoRequire.resolve('@noble/curves/secp256k1.js'));

test('legacy signer accepts hex/nsec, verifies identity and signs the worker delegation', () => {
  const root = mkdtempSync(join(tmpdir(), 'buzz-manager-sign-'));
  try {
    const key = generateSecretKey();
    const manager = getPublicKey(key);
    const worker = getPublicKey(generateSecretKey());
    writeFileSync(join(root, '.buzz-pub'), manager);
    const run = (pub = worker) => execFileSync(process.execPath,
      [join(here, 'sign-worker.mjs'), root, desktop, pub],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
    for (const encoded of [Buffer.from(key).toString('hex'), nsecEncode(key)]) {
      writeFileSync(join(root, '.buzz-key'), encoded, { mode: 0o600 });
      const tag = JSON.parse(run());
      assert.deepEqual(tag.slice(0, 3), ['auth', manager, '']);
      const hash = createHash('sha256').update(`nostr:agent-auth:${worker}:`).digest();
      assert.equal(schnorr.verify(Buffer.from(tag[3], 'hex'), hash, Buffer.from(manager, 'hex')), true);
    }
    assert.throws(() => run(manager));
    assert.throws(() => run('invalid-key'));
    writeFileSync(join(root, '.buzz-pub'), worker);
    assert.throws(() => run());
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
