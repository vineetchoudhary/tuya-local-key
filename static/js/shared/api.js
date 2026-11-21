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

// The app's own errors are JSON with an "error" code. A proxy in front of it
// can fail a request that never reached it. For example, Home Assistant
// answers 401 in plain text while its ingress session is expired.
function appError(body) { return typeof body.error === "string"; }

// Only the app's own 401 means the Tuya login is gone.
function loggedOut(status, body) { return status === 401 && appError(body); }

export { api, appError, loggedOut };
