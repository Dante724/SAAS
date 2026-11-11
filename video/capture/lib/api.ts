// Tiny JSON client for the Tekly API (bearer token).
export class Api {
  constructor(public base: string, public token = "") {}
  async call<T = any>(method: string, path: string, body?: unknown): Promise<T> {
    const r = await fetch(this.base + "/api" + path, {
      method,
      headers: { "Content-Type": "application/json", ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}) },
      body: body == null ? undefined : JSON.stringify(body),
    });
    const text = await r.text();
    if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}: ${text.slice(0, 400)}`);
    return (text ? JSON.parse(text) : null) as T;
  }
  get<T = any>(p: string) { return this.call<T>("GET", p); }
  post<T = any>(p: string, b: unknown = {}) { return this.call<T>("POST", p, b); }
  put<T = any>(p: string, b: unknown) { return this.call<T>("PUT", p, b); }
  patch<T = any>(p: string, b: unknown) { return this.call<T>("PATCH", p, b); }
  async bytes(p: string): Promise<{ buf: Buffer; mime: string }> {
    const r = await fetch(this.base + "/api" + p, { headers: { Authorization: `Bearer ${this.token}` } });
    if (!r.ok) throw new Error(`GET ${p} -> ${r.status}`);
    return { buf: Buffer.from(await r.arrayBuffer()), mime: r.headers.get("content-type") || "image/jpeg" };
  }
}
