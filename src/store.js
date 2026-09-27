'use strict';
/**
 * store — 演示用加密记录存储
 *  - 记录: { id, fields: { [fieldName]: envelope } }
 *  - 写入串行化（promise 队列）+ tmp 文件原子 rename，保证并发写不撕裂文件
 *  - 轮换按记录逐个提交并持久化游标，中断后可续跑，且幂等
 */
const fs = require('node:fs');
const path = require('node:path');

class EncryptedStore {
  /**
   * @param {string} file  持久化文件路径
   * @param {import('./fieldcrypto').FieldCrypto} cryptoBox
   */
  constructor(file, cryptoBox) {
    this.file = file;
    this.crypto = cryptoBox;
    this.records = new Map(); // id -> { id, fields }
    this.meta = { rotation: null }; // { toVersion, doneIds }
    this._queue = Promise.resolve();
    this._load();
  }

  _load() {
    if (!fs.existsSync(this.file)) return;
    const data = JSON.parse(fs.readFileSync(this.file, 'utf8'));
    for (const r of data.records) this.records.set(r.id, r);
    this.meta = data.meta ?? { rotation: null };
  }

  /** 原子持久化：写临时文件 + fsync + rename */
  _persistSync() {
    const data = JSON.stringify({
      records: [...this.records.values()],
      meta: this.meta,
    });
    const tmp = this.file + '.tmp.' + process.pid;
    const fd = fs.openSync(tmp, 'w');
    try {
      fs.writeSync(fd, data);
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    fs.renameSync(tmp, this.file); // 同目录 rename 是原子的
  }

  /** 串行化写操作，避免并发写互相覆盖 */
  _enqueue(fn) {
    const run = this._queue.then(fn);
    this._queue = run.catch(() => {});
    return run;
  }

  /** 写入/更新一条记录的若干字段（明文在此加密，不落盘） */
  put(id, plaintextFields) {
    return this._enqueue(async () => {
      const rec = this.records.get(id) ?? { id, fields: {} };
      for (const [field, value] of Object.entries(plaintextFields)) {
        rec.fields[field] = this.crypto.encryptField(id, field, value);
      }
      this.records.set(id, rec);
      this._persistSync();
      return rec;
    });
  }

  /** 按盲索引标签做等值查询，返回解密后的记录 */
  findByField(field, plaintext) {
    const tag = this.crypto.tagFor(field, plaintext);
    const out = [];
    for (const rec of this.records.values()) {
      const env = rec.fields[field];
      if (env && env.t === tag) {
        out.push({ id: rec.id, [field]: this.crypto.decryptField(rec.id, field, env) });
      }
    }
    return out;
  }

  getDecrypted(id) {
    const rec = this.records.get(id);
    if (!rec) return null;
    const fields = {};
    for (const [field, env] of Object.entries(rec.fields)) {
      fields[field] = this.crypto.decryptField(id, field, env);
    }
    return { id, fields };
  }

  /**
   * 密钥轮换：把所有记录重加密到 keyRing.activeVersion。
   * 可中断：每处理完一条记录即持久化游标；再次调用自动续跑。
   * 幂等：已是目标版本的信封由 rotateEnvelope 直接跳过。
   * @param {(recId:string, done:number, total:number)=>void} [onRecord] 每条记录处理前回调（测试可在此注入故障）
   */
  rotateKeys(onRecord) {
    return this._enqueue(async () => {
      const target = this.crypto.keyRing.activeVersion;
      if (!this.meta.rotation || this.meta.rotation.toVersion !== target) {
        this.meta.rotation = { toVersion: target, doneIds: [] };
      }
      const done = new Set(this.meta.rotation.doneIds);
      const all = [...this.records.values()];
      let n = 0;
      for (const rec of all) {
        if (done.has(rec.id)) { n++; continue; }
        if (onRecord) onRecord(rec.id, n, all.length); // 故障注入点
        for (const [field, env] of Object.entries(rec.fields)) {
          rec.fields[field] = this.crypto.rotateEnvelope(rec.id, field, env, target);
        }
        this.meta.rotation.doneIds.push(rec.id);
        this._persistSync(); // 每条记录提交一次，崩溃最多丢失当前记录
        n++;
      }
      this.meta.rotation = null;
      this._persistSync();
      return { rotated: n, total: all.length };
    });
  }
}

module.exports = { EncryptedStore };
