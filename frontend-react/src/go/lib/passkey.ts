// Face ID / Touch ID as a faster way past the PIN, on this phone only. It's a local presence
// check: the phone's own authenticator confirms a person it knows is holding it (user
// verification required). Only the credential id is kept here (not a secret); the PIN stays the
// fallback and is still what the server knows. Turning it on needs the PIN first.
const KEY = (uid: string) => `go_passkey:${uid}`;

const b64u = (buf: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64u = (s: string) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=")), (c) => c.charCodeAt(0));
const challenge = () => crypto.getRandomValues(new Uint8Array(32));

export async function passkeySupported(): Promise<boolean> {
  try {
    return !!window.PublicKeyCredential && await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch { return false; }
}

export function passkeyOn(uid?: string | null): boolean {
  if (!uid) return false;
  try { return !!localStorage.getItem(KEY(uid)); } catch { return false; }
}

export function passkeyOff(uid?: string | null) {
  if (!uid) return;
  try { localStorage.removeItem(KEY(uid)); } catch { /* ignore */ }
}

export async function passkeyEnroll(me: { id?: string; name?: string; email?: string }): Promise<boolean> {
  if (!me.id) return false;
  const cred = await navigator.credentials.create({
    publicKey: {
      rp: { name: "Tekly Go", id: location.hostname },
      user: { id: new TextEncoder().encode(me.id), name: me.email || me.id, displayName: me.name || me.email || "Tekly" },
      challenge: challenge(),
      pubKeyCredParams: [{ type: "public-key", alg: -7 }, { type: "public-key", alg: -257 }],
      authenticatorSelection: { authenticatorAttachment: "platform", userVerification: "required", residentKey: "discouraged" },
      timeout: 60_000,
    },
  }) as PublicKeyCredential | null;
  if (!cred) return false;
  try { localStorage.setItem(KEY(me.id), b64u(cred.rawId)); } catch { return false; }
  return true;
}

/** authenticatorData flags byte: bit 0 = user present, bit 2 = user verified (biometric / passcode). */
export function userVerified(authData: ArrayBuffer): boolean {
  const b = new Uint8Array(authData);
  return b.length > 32 && (b[32] & 0x05) === 0x05;
}

export async function passkeyCheck(uid?: string | null): Promise<boolean> {
  if (!uid) return false;
  let id: string | null = null;
  try { id = localStorage.getItem(KEY(uid)); } catch { /* ignore */ }
  if (!id) return false;
  try {
    const a = await navigator.credentials.get({
      publicKey: { challenge: challenge(), allowCredentials: [{ type: "public-key", id: unb64u(id) }], userVerification: "required", timeout: 60_000 },
    }) as PublicKeyCredential | null;
    const r = a?.response as AuthenticatorAssertionResponse | undefined;
    return !!r && userVerified(r.authenticatorData);
  } catch { return false; }
}
