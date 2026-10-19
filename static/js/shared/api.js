// Api.


async function api(path, opts) {
  let r;
  try { r = await fetch(path.replace(/^\/+/, ""), opts); }
  catch (e) { return { ok: false, status: 0, body: {} }; }  // network down: don't throw, callers handle !ok
  let body = {};
  try { body = await r.json(); } catch (e) {}
  return { ok: r.ok, status: r.status, body };
}

export { api };
