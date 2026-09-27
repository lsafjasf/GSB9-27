'use strict';
// 演示：加密落库 → 等值查询 → 跨记录复制检测 → 密钥轮换
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { FieldCrypto, KeyRing } = require('./src/fieldcrypto');
const { EncryptedStore } = require('./src/store');

const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'fc-demo-')), 'db.json');
const ring = new KeyRing({ 1: KeyRing.generate() }, 1);
const fc = new FieldCrypto(ring);
const store = new EncryptedStore(file, fc);

(async () => {
  await store.put('u1', { email: 'alice@example.com', ssn: '110101199001011234' });
  await store.put('u2', { email: 'bob@example.com', ssn: null });
  await store.put('u3', { email: 'alice@example.com', ssn: '310101198505054321' });

  console.log('== 落库内容（无 plaintext）==');
  console.log(fs.readFileSync(file, 'utf8').slice(0, 300), '...\n');

  console.log('== 等值查询 email = alice@example.com ==');
  console.log(store.findByField('email', 'alice@example.com'), '\n');

  console.log('== 跨记录复制密文检测 ==');
  const stolen = store.records.get('u1').fields.ssn;
  try {
    fc.decryptField('u2', 'ssn', stolen);
  } catch (e) {
    console.log('检测到复制攻击:', e.message, '\n');
  }

  console.log('== 密钥轮换 v1 -> v2 ==');
  ring.keys.set(2, KeyRing.generate());
  ring.activeVersion = 2;
  await store.rotateKeys();
  ring.removeVersion(1);
  console.log('轮换后查询 alice:', store.findByField('email', 'alice@example.com').map(r => r.id));
  console.log('轮换后解密 u3.ssn:', store.getDecrypted('u3').fields.ssn);
})();
