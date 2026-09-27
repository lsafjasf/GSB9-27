'use strict';
/**
 * fieldcrypto — 可等值查询的字段级加密（仅 Node.js 标准库）
 *
 * 设计：
 *  - 盲索引标签 tag = "v{ver}." + base64url( HMAC-SHA256(idxKey_v, "idx"|field|plaintext)[:16] )
 *    用于等值查询的分组标签；单向、带密钥版本、轮换后可按明文重建。
 *  - 可逆密文 = AES-256-GCM(encKey_v, randomNonce, plaintext, AAD)
 *    AAD 绑定 (keyVersion, recordId, field, tag)，跨记录复制密文会认证失败。
 *  - 密钥版本化：每个版本独立 HKDF 派生 enc/idx 子密钥，信封自描述版本。
 */
const crypto = require('node:crypto');

const TAG_BYTES = 16;          // 128-bit 盲索引
const NONCE_BYTES = 12;        // GCM 标准 96-bit nonce
const GCM_TAG_BYTES = 16;
const DEFAULT_MAX_PLAINTEXT_BYTES = 8192;

class FieldCryptoError extends Error {}
class FieldTooLargeError extends FieldCryptoError {}
class UnknownKeyVersionError extends FieldCryptoError {}
class IntegrityError extends FieldCryptoError {} // 密文被篡改 / 跨记录复制 / AAD 不匹配

function b64u(buf) {
  return Buffer.from(buf).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function fromB64u(s) {
  s = s.replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  return Buffer.from(s, 'base64');
}

/** 长度前缀编码，避免 AAD 拼接歧义 */
function lengthPrefixed(parts) {
  const chunks = [];
  for (const p of parts) {
    const b = Buffer.isBuffer(p) ? p : Buffer.from(String(p), 'utf8');
    const len = Buffer.alloc(4);
    len.writeUInt32BE(b.length, 0);
    chunks.push(len, b);
  }
  return Buffer.concat(chunks);
}

class KeyRing {
  /**
   * @param {Object<number, Buffer|string>} keys  version -> 32字节主密钥(或base64)
   * @param {number} activeVersion  当前写入版本
   */
  constructor(keys, activeVersion) {
    this.keys = new Map();
    for (const [v, k] of Object.entries(keys)) {
      const buf = Buffer.isBuffer(k) ? k : Buffer.from(k, 'base64');
      if (buf.length !== 32) throw new FieldCryptoError(`key v${v} must be 32 bytes`);
      this.keys.set(Number(v), buf);
    }
    if (!this.keys.has(activeVersion)) throw new FieldCryptoError('activeVersion not in keyring');
    this.activeVersion = activeVersion;
    this._derived = new Map();
  }

  static generate() {
    return crypto.randomBytes(32);
  }

  _derive(version) {
    if (this._derived.has(version)) return this._derived.get(version);
    const master = this.keys.get(version);
    if (!master) throw new UnknownKeyVersionError(`no key for version ${version}`);
    const ikm = master;
    const salt = Buffer.from('fieldcrypto-v1');
    const enc = crypto.hkdfSync('sha256', ikm, salt, `enc/v${version}`, 32);
    const idx = crypto.hkdfSync('sha256', ikm, salt, `idx/v${version}`, 32);
    const pair = { enc: Buffer.from(enc), idx: Buffer.from(idx) };
    this._derived.set(version, pair);
    return pair;
  }

  encKey(v) { return this._derive(v).enc; }
  idxKey(v) { return this._derive(v).idx; }
  hasVersion(v) { return this.keys.has(v); }
  removeVersion(v) {
    if (v === this.activeVersion) throw new FieldCryptoError('cannot remove active version');
    this.keys.delete(v);
    this._derived.delete(v);
  }
}

class FieldCrypto {
  /**
   * @param {KeyRing} keyRing
   * @param {{maxPlaintextBytes?: number}} [opts]
   */
  constructor(keyRing, opts = {}) {
    this.keyRing = keyRing;
    this.maxPlaintextBytes = opts.maxPlaintextBytes ?? DEFAULT_MAX_PLAINTEXT_BYTES;
  }

  /** 等值查询用的分组标签（确定性、单向、带版本） */
  tagFor(field, plaintext, version = this.keyRing.activeVersion) {
    if (plaintext === null || plaintext === undefined) return null;
    const h = crypto.createHmac('sha256', this.keyRing.idxKey(version));
    h.update(lengthPrefixed(['idx', field, Buffer.from(String(plaintext), 'utf8')]));
    return `v${version}.${b64u(h.digest().subarray(0, TAG_BYTES))}`;
  }

  /**
   * 加密一个字段。
   * @returns {{v:number, n:string|null, c:string|null, t:string|null}}
   *   v=密钥版本, n=nonce, c=密文(含GCM tag), t=盲索引标签；明文为 null 时 n/c/t 均为 null
   */
  encryptField(recordId, field, plaintext, version = this.keyRing.activeVersion) {
    if (plaintext === null || plaintext === undefined) {
      return { v: version, n: null, c: null, t: null };
    }
    const pt = Buffer.from(String(plaintext), 'utf8');
    if (pt.length > this.maxPlaintextBytes) {
      throw new FieldTooLargeError(
        `field "${field}" is ${pt.length} bytes, limit ${this.maxPlaintextBytes}`);
    }
    const tag = this.tagFor(field, plaintext, version);
    const aad = lengthPrefixed(['aad', `v${version}`, String(recordId), field, tag]);
    const nonce = crypto.randomBytes(NONCE_BYTES);
    const cipher = crypto.createCipheriv('aes-256-gcm', this.keyRing.encKey(version), nonce,
      { authTagLength: GCM_TAG_BYTES });
    cipher.setAAD(aad);
    const ct = Buffer.concat([cipher.update(pt), cipher.final()]);
    const authTag = cipher.getAuthTag();
    return {
      v: version,
      n: b64u(nonce),
      c: b64u(Buffer.concat([ct, authTag])),
      t: tag,
    };
  }

  /** 解密；AAD 不匹配（篡改/跨记录复制）时抛 IntegrityError */
  decryptField(recordId, field, envelope) {
    if (envelope === null || envelope === undefined) return null;
    const { v, n, c, t } = envelope;
    if (c === null || c === undefined) return null;
    if (!this.keyRing.hasVersion(v)) throw new UnknownKeyVersionError(`no key for version ${v}`);
    const aad = lengthPrefixed(['aad', `v${v}`, String(recordId), field, t]);
    const raw = fromB64u(c);
    const ct = raw.subarray(0, raw.length - GCM_TAG_BYTES);
    const authTag = raw.subarray(raw.length - GCM_TAG_BYTES);
    const decipher = crypto.createDecipheriv('aes-256-gcm', this.keyRing.encKey(v), fromB64u(n),
      { authTagLength: GCM_TAG_BYTES });
    decipher.setAAD(aad);
    decipher.setAuthTag(authTag);
    try {
      return Buffer.concat([decipher.update(ct), decipher.final()]).toString('utf8');
    } catch {
      throw new IntegrityError(
        `integrity check failed for record "${recordId}" field "${field}" ` +
        '(tampered, wrong record/field, or wrong key version)');
    }
  }

  /** 用新版本重加密（轮换）；明文不落地，仅内存中转换 */
  rotateEnvelope(recordId, field, envelope, newVersion = this.keyRing.activeVersion) {
    if (envelope === null || envelope === undefined) return envelope;
    if (envelope.v === newVersion) return envelope; // 幂等：已是目标版本
    const pt = this.decryptField(recordId, field, envelope);
    return this.encryptField(recordId, field, pt, newVersion);
  }
}

module.exports = {
  FieldCrypto,
  KeyRing,
  FieldCryptoError,
  FieldTooLargeError,
  UnknownKeyVersionError,
  IntegrityError,
};
