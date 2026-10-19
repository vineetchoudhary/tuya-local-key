// Dom.


const $ = (id) => document.getElementById(id);

const views = {};
function initDom() {
  for (const name of ["login", "qr", "loading", "devices"]) views[name] = $(name);
}

export { $, initDom, views };
