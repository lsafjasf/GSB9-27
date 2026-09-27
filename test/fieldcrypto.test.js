'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');

const {
  FieldCrypto, KeyRing,
  FieldTooLargeError, UnknownKeyVersionError, IntegrityError,
} = require('../src/fieldcrypto');
const { EncryptedStore } = require('../src/store');

function tmpFile() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'fc-')), 'store.json');
}
function makeCrypto(keys, active, opts) {
  return new FieldCrypto(new KeyRing(keys, active), opts);
}
const K1 = KeyRing.generate();
const K2 = KeyRing.generate();

// ---------- 基本加解密 / 等值标签 ----------

test('可逆密文：加密后可解密还原', () => {
  const fc = makeCrypto({ 1: K1 }, 1);
  const env = fc.encryptField('r1', 'email', 'alice@example.com');
  assert.equal(fc.decryptField('r1', 'email', env), 'alice@example.com');
});

test('等值标签：同明文同标签，不同明文不同标签，标签带密钥版本', () => {
  const fc = makeCrypto({ 1: K1 }, 1);
  const t1 = fc.tagFor('email', 'alice@example.com');
  const t2 = fc.tagFor('email', 'alice@example.com');
  const t3 = fc.tagFor('email', 'bob@example.com');
  assert.equal(t1, t2);
  assert.notEqual(t1, t3);
  assert.match(t1, /^v1\./);
  // 不同字段的相同明文标签隔离（不泄露跨字段等值关系）
  assert.notEqual(fc.tagFor('email', 'x'), fc.tagFor('name', 'x'));
});

test('标签不可反推：HMAC 单向，且不等于明文的任何朴素哈希', () => {
  const fc = makeCrypto({ 1: K1 }, 1);
  const pt = 'alice@example.com';
  const tag = fc.tagFor('email', pt);
  const sha = crypto.createHash('sha256').update(pt).digest('hex');
  assert.ok(!tag.includes(sha));
  assert.ok(!tag.includes(Buffer.from(pt).toString('base64')));
});

test('相同明文加密两次密文不同（随机 nonce），但标签相同', () => {
  const fc = makeCrypto({ 1: K1 }, 1);
  const a = fc.encryptField('r1', 'email', 'alice@example.com');
  const b = fc.encryptField('r1', 'email', 'alice@example.com');
  assert.notEqual(a.c, b.c);
  assert.notEqual(a.n, b.n);
  assert.equal(a.t, b.t);
});

// ---------- 完整性与关联绑定 ----------

test('跨记录复制密文可被检测（AAD 绑定 recordId）', () => {
  const fc = makeCrypto({ 1: K1 }, 1);
  const envA = fc.encryptField('record-A', 'id_card', '110101199001011234');
  // 攻击者把 A 的密文整体复制到 B 的记录里
  assert.throws(() => fc.decryptField('record-B', 'id_card', envA), IntegrityError);
});

test('同记录跨字段复制密文可被检测（AAD 绑定 field）', () => {
  const fc = makeCrypto({ 1: K1 }, 1);
  const env = fc.encryptField('r1', 'email', 'a@b.c');
  assert.throws(() => fc.decryptField('r1', 'phone', env), IntegrityError);
});

test('篡改密文/标签任意字节可被检测（GCM 完整性）', () => {
  const fc = makeCrypto({ 1: K1 }, 1);
  const env = fc.encryptField('r1', 'email', 'alice@example.com');
  const raw = Buffer.from(env.c.replace(/-/g, '+').replace(/_/g, '/'), 'base64');
  raw[0] ^= 0xff;
  const forged = { ...env, c: raw.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '') };
  assert.throws(() => fc.decryptField('r1', 'email', forged), IntegrityError);
  // 换掉盲索引标签（AAD 含 tag）同样失败
  const other = fc.encryptField('r1', 'email', 'mallory@example.com');
  assert.throws(() => fc.decryptField('r1', 'email', { ...env, t: other.t }), IntegrityError);
});

// ---------- 空值 / 超长 ----------

test('空值：null 不加密集文与标签，解密还原为 null', () => {
  const fc = makeCrypto({ 1: K1 }, 1);
  const env = fc.encryptField('r1', 'email', null);
  assert.deepEqual(env, { v: 1, n: null, c: null, t: null });
  assert.equal(fc.decryptField('r1', 'email', env), null);
  assert.equal(fc.tagFor('email', null), null);
});

test('超长字段：超过限制抛错，边界值可用', () => {
  const fc = makeCrypto({ 1: K1 }, 1, { maxPlaintextBytes: 1024 });
  const ok = 'x'.repeat(1024);
  assert.equal(fc.decryptField('r1', 'bio', fc.encryptField('r1', 'bio', ok)), ok);
  assert.throws(() => fc.encryptField('r1', 'bio', 'x'.repeat(1025)), FieldTooLargeError);
  // 多字节字符按字节计数
  assert.throws(() => fc.encryptField('r1', 'bio', '汉'.repeat(400)), FieldTooLargeError);
});

// ---------- 密钥轮换 ----------

test('轮换：标签随版本重建，旧版本密钥移除后仍可查询解密', async () => {
  const file = tmpFile();
  const ring = new KeyRing({ 1: K1 }, 1);
  const fc = new FieldCrypto(ring);
  const store = new EncryptedStore(file, fc);
  await store.put('r1', { email: 'alice@example.com' });
  await store.put('r2', { email: 'alice@example.com' });
  await store.put('r3', { email: 'bob@example.com' });

  // 轮换到 v2
  ring.keys.set(2, K2);
  ring.activeVersion = 2;
  const res = await store.rotateKeys();
  assert.equal(res.total, 3);

  // 所有信封已是 v2，标签前缀 v2.
  for (const rec of store.records.values()) {
    assert.equal(rec.fields.email.v, 2);
    assert.match(rec.fields.email.t, /^v2\./);
  }
  // 等值分组在轮换后保持：r1/r2 同标签，r3 不同
  const tags = [...store.records.values()].map(r => r.fields.email.t);
  assert.equal(tags[0], tags[1]);
  assert.notEqual(tags[0], tags[2]);

  // 移除旧密钥后：新数据可查可解，旧版本信封无法解密
  ring.removeVersion(1);
  assert.equal(store.findByField('email', 'alice@example.com').length, 2);
  assert.equal(store.getDecrypted('r3').fields.email, 'bob@example.com');
  const oldEnv = fc.encryptField('x', 'email', 'y', 2); // 构造一个伪造旧版本信封
  oldEnv.v = 1;
  assert.throws(() => fc.decryptField('x', 'email', oldEnv), UnknownKeyVersionError);
});

test('轮换中断：崩溃后可续跑，混合版本期间读写均正常', async () => {
  const file = tmpFile();
  const ring = new KeyRing({ 1: K1 }, 1);
  const fc = new FieldCrypto(ring);
  const store = new EncryptedStore(file, fc);
  for (let i = 0; i < 10; i++) await store.put(`r${i}`, { email: `u${i}@x.com` });

  ring.keys.set(2, K2);
  ring.activeVersion = 2;

  // 第一次轮换：处理 4 条后模拟崩溃
  await assert.rejects(
    store.rotateKeys((id, done) => { if (done === 4) throw new Error('crash!'); }),
    /crash!/);

  // 模拟进程重启：从磁盘重新加载
  const store2 = new EncryptedStore(file, fc);
  const versions = [...store2.records.values()].map(r => r.fields.email.v);
  assert.ok(versions.includes(1) && versions.includes(2), '中断后应为混合版本');
  // 混合版本期间：旧记录照常解密；新写入用 v2
  assert.equal(store2.getDecrypted('r0').fields.email, 'u0@x.com');
  await store2.put('r9', { email: 'u9@x.com' });
  assert.equal(store2.records.get('r9').fields.email.v, 2);

  // 续跑轮换：幂等完成
  await store2.rotateKeys();
  for (const rec of store2.records.values()) assert.equal(rec.fields.email.v, 2);
  assert.equal(store2.meta.rotation, null);
  // 全部可解密
  for (let i = 0; i < 10; i++) {
    assert.equal(store2.getDecrypted(`r${i}`).fields.email, `u${i}@x.com`);
  }
});

// ---------- 并发写入 ----------

test('并发写入：50 个并发 put 不丢数据、文件不撕裂、nonce 无重复', async () => {
  const file = tmpFile();
  const fc = makeCrypto({ 1: K1 }, 1);
  const store = new EncryptedStore(file, fc);
  const N = 50;
  await Promise.all(Array.from({ length: N }, (_, i) =>
    store.put(`r${i}`, { email: `user${i}@example.com`, name: `用户${i}` })));

  // 从磁盘冷加载验证持久化完整
  const store2 = new EncryptedStore(file, fc);
  assert.equal(store2.records.size, N);
  const nonces = new Set();
  for (let i = 0; i < N; i++) {
    const dec = store2.getDecrypted(`r${i}`);
    assert.equal(dec.fields.email, `user${i}@example.com`);
    assert.equal(dec.fields.name, `用户${i}`);
    for (const env of Object.values(store2.records.get(`r${i}`).fields)) {
      assert.ok(!nonces.has(env.n), 'nonce 不得重复');
      nonces.add(env.n);
    }
  }
});

// ---------- 落库内容检查 ----------

test('落库数据不含明文，也不含明文的朴素哈希', async () => {
  const file = tmpFile();
  const fc = makeCrypto({ 1: K1 }, 1);
  const store = new EncryptedStore(file, fc);
  const secret = 'alice.secret@example.com';
  await store.put('r1', { email: secret });

  const onDisk = fs.readFileSync(file, 'utf8');
  assert.ok(!onDisk.includes(secret), '明文不得落库');
  assert.ok(!onDisk.includes(Buffer.from(secret).toString('base64')), '明文 base64 不得落库');
  const sha = crypto.createHash('sha256').update(secret).digest();
  assert.ok(!onDisk.includes(sha.toString('hex')), '可反推的 SHA-256(hex) 不得落库');
  assert.ok(!onDisk.includes(sha.toString('base64')), '可反推的 SHA-256(base64) 不得落库');
});

test('等值查询端到端：findByField 只命中同值记录', async () => {
  const file = tmpFile();
  const fc = makeCrypto({ 1: K1 }, 1);
  const store = new EncryptedStore(file, fc);
  await store.put('r1', { email: 'a@x.com' });
  await store.put('r2', { email: 'b@x.com' });
  await store.put('r3', { email: 'a@x.com' });
  const hits = store.findByField('email', 'a@x.com');
  assert.deepEqual(hits.map(h => h.id).sort(), ['r1', 'r3']);
  assert.equal(hits[0].email, 'a@x.com');
});
