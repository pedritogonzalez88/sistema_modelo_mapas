/* =========================================================================
   scripts/totp.mjs
   TOTP (RFC 6238: SHA-1, 30 s, 6 dígitos) sin dependencias, para los scripts
   E2E y de accesibilidad. No se ejecuta solo.
   ========================================================================= */
import crypto from "node:crypto";

const ALFABETO_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

export function base32Decode(texto) {
  const limpio = String(texto || "").toUpperCase().replace(/[\s=]/g, "");
  let bits = 0;
  let valor = 0;
  const bytes = [];
  for (const ch of limpio) {
    const idx = ALFABETO_B32.indexOf(ch);
    if (idx < 0) continue;
    valor = (valor << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      bytes.push((valor >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(bytes);
}

export function totpEnPaso(secreto, step) {
  const clave = base32Decode(secreto);
  const contador = Buffer.alloc(8);
  contador.writeUInt32BE(Math.floor(step / 2 ** 32), 0);
  contador.writeUInt32BE(step % 2 ** 32, 4);
  const hmac = crypto.createHmac("sha1", clave).update(contador).digest();
  const offset = hmac[hmac.length - 1] & 0x0f;
  const binario =
    ((hmac[offset] & 0x7f) << 24) |
    ((hmac[offset + 1] & 0xff) << 16) |
    ((hmac[offset + 2] & 0xff) << 8) |
    (hmac[offset + 3] & 0xff);
  return String(binario % 1e6).padStart(6, "0");
}

export const pasoActual = () => Math.floor(Date.now() / 30000);

/**
 * Generador con memoria del último paso emitido: el servidor rechaza un código
 * de un paso ya usado (antirreplay), así que cada llamada avanza uno y, si el
 * paso cae fuera de la ventana aceptada, espera al siguiente ciclo de 30 s.
 */
export function crearGenerador(secreto, { log = () => {} } = {}) {
  let ultimo = null;
  return {
    get ultimoPaso() {
      return ultimo;
    },
    async codigo({ esperar = true } = {}) {
      const ahora = pasoActual();
      const step = ultimo == null ? ahora : Math.max(ahora, ultimo + 1);
      if (esperar && step > ahora + 1) {
        const objetivo = (step - 1) * 30000;
        const falta = Math.max(0, objetivo - Date.now()) + 200;
        log(`esperando ${Math.ceil(falta / 1000)}s para no reutilizar el paso TOTP`);
        await new Promise((r) => setTimeout(r, falta));
        return this.codigo({ esperar: false });
      }
      ultimo = step;
      return totpEnPaso(secreto, step);
    },
  };
}
