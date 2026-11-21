// Api.


// Names the app on every request. Over plain HTTP, browsers send no
// Sec-Fetch-Site, and a reverse proxy that rewrites Host breaks the server's
// Origin check. Other sites cannot send this header without the app's
// permission, which it never gives, so it still proves the page is the app's.
const APP_HEADER = ["X-Requested-With", "tuya-local-key"];

async function api(path, opts = {}) {
  const headers = new Headers(opts.headers);
  headers.set(...APP_HEADER);
  let r;
  try { r = await fetch(path.replace(/^\/+/, ""), { ...opts, headers }); }
  catch (e) { return { ok: false, status: 0, body: {} }; }  // network down: don't throw, callers handle !ok
  let body = {};
  try { body = await r.json(); } catch (e) {}
  return { ok: r.ok, status: r.status, body };
}

export { api };
