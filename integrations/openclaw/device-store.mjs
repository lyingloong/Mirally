/** Host-owned identity and scoped token storage for the official Gateway client. */
import { createHash, createPublicKey, generateKeyPairSync, sign } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join, basename } from "node:path";
import { gatewayOriginScope } from "@openclaw/gateway-client";

function readJson(path, fallback) {
  if (!existsSync(path)) return fallback;
  try { return JSON.parse(readFileSync(path, "utf8")); }
  catch { throw new Error(`Native client storage is unreadable: ${basename(path)}`); }
}

function writeJson(path, value) {
  const temporary = `${path}.${process.pid}.tmp`;
  writeFileSync(temporary, JSON.stringify(value, null, 2), { mode: 0o600 });
  renameSync(temporary, path);
}

export function publicKeyRaw(publicKeyPem) {
  const key = createPublicKey(publicKeyPem);
  if (key.asymmetricKeyType !== "ed25519") throw new Error("Expected an Ed25519 device key.");
  return key.export({ type: "spki", format: "der" }).subarray(-32);
}

export function createDeviceHost(directory, gatewayUrl, onSecret = () => {}) {
  mkdirSync(directory, { recursive: true });
  const identityPath = join(directory, "device-identity.json");
  const tokensPath = join(directory, "device-tokens.json");
  let identity = readJson(identityPath, null);
  if (!identity) {
    const keys = generateKeyPairSync("ed25519");
    const publicKeyPem = keys.publicKey.export({ type: "spki", format: "pem" });
    identity = {
      deviceId: createHash("sha256").update(publicKeyRaw(publicKeyPem)).digest("hex"),
      publicKeyPem,
      privateKeyPem: keys.privateKey.export({ type: "pkcs8", format: "pem" }),
    };
    writeJson(identityPath, identity);
  }
  if (identity.deviceId !== createHash("sha256").update(publicKeyRaw(identity.publicKeyPem)).digest("hex")) {
    throw new Error("Device identity is damaged; retain it and repair through native device management.");
  }
  const scope = gatewayOriginScope(gatewayUrl);
  function key(params) { return `${scope}|${params.deviceId}|${params.role}`; }
  function check(params) {
    params.signal?.throwIfAborted();
    params.assertCurrent?.();
  }
  return {
    identity,
    hostDeps: {
      signDevicePayload: (privateKeyPem, payload) => sign(null, Buffer.from(payload), privateKeyPem).toString("base64url"),
      publicKeyRawBase64UrlFromPem: pem => publicKeyRaw(pem).toString("base64url"),
      loadDeviceAuthToken(params) {
        check(params);
        const token = readJson(tokensPath, {})[key(params)] ?? null;
        if (token?.token) onSecret(token.token);
        return token;
      },
      storeDeviceAuthToken(params) {
        check(params);
        const tokens = readJson(tokensPath, {});
        const previous = tokens[key(params)]?.token ?? null;
        if (params.expectedToken !== undefined && params.expectedToken !== previous) return;
        check(params);
        tokens[key(params)] = { token: params.token, scopes: params.scopes };
        writeJson(tokensPath, tokens);
        onSecret(params.token);
      },
      clearDeviceAuthToken(params) {
        check(params);
        const tokens = readJson(tokensPath, {});
        if (params.expectedToken !== undefined && tokens[key(params)]?.token !== params.expectedToken) return;
        check(params);
        delete tokens[key(params)];
        writeJson(tokensPath, tokens);
      },
    },
  };
}
