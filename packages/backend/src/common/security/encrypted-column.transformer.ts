import { createCipheriv, createDecipheriv, createHash, randomBytes } from 'crypto';
import { Logger } from '@nestjs/common';
import type { ValueTransformer } from 'typeorm';

/**
 * AES-256-GCM column encryption for secrets at rest (broker API tokens).
 *
 * Wire format: `enc:v1:<iv_b64>:<authTag_b64>:<ciphertext_b64>`
 *
 * Design notes:
 * - The key comes from TOKEN_ENCRYPTION_KEY (any string; it's SHA-256'd to
 *   32 bytes). Rotating it requires re-encrypting existing rows — decrypt
 *   with the old key, save with the new.
 * - Legacy plaintext rows (written before encryption existed) don't carry
 *   the `enc:` prefix and are returned unchanged; they become encrypted the
 *   next time the row is saved. This makes the rollout zero-migration.
 * - If TOKEN_ENCRYPTION_KEY is unset, the transformer passes values through
 *   unchanged and logs a warning once — the app still works, just without
 *   at-rest protection. Set the key in production.
 * - GCM (vs CBC) gives tamper detection for free via the auth tag.
 */

const PREFIX = 'enc:v1:';
const logger = new Logger('EncryptedColumn');
let warnedMissingKey = false;

function getKey(): Buffer | null {
  const raw = process.env.TOKEN_ENCRYPTION_KEY;
  if (!raw) {
    if (!warnedMissingKey) {
      warnedMissingKey = true;
      logger.warn(
        'TOKEN_ENCRYPTION_KEY is not set — broker tokens are stored in PLAINTEXT. ' +
        'Set it in .env before running with real-money accounts.',
      );
    }
    return null;
  }
  return createHash('sha256').update(raw).digest();
}

export function encryptSecret(plaintext: string): string {
  const key = getKey();
  if (!key) return plaintext;
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  const ciphertext = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `${PREFIX}${iv.toString('base64')}:${tag.toString('base64')}:${ciphertext.toString('base64')}`;
}

export function decryptSecret(stored: string): string {
  if (!stored.startsWith(PREFIX)) return stored; // legacy plaintext row
  const key = getKey();
  if (!key) {
    throw new Error(
      'Encrypted value found but TOKEN_ENCRYPTION_KEY is not set — cannot decrypt broker token.',
    );
  }
  const [ivB64, tagB64, dataB64] = stored.slice(PREFIX.length).split(':');
  const decipher = createDecipheriv('aes-256-gcm', key, Buffer.from(ivB64, 'base64'));
  decipher.setAuthTag(Buffer.from(tagB64, 'base64'));
  return Buffer.concat([
    decipher.update(Buffer.from(dataB64, 'base64')),
    decipher.final(),
  ]).toString('utf8');
}

/** TypeORM transformer: encrypt on write, decrypt on read, null-safe. */
export const encryptedColumn: ValueTransformer = {
  to: (value: string | null | undefined): string | null =>
    value == null ? null : encryptSecret(value),
  from: (value: string | null): string | null =>
    value == null ? null : decryptSecret(value),
};
