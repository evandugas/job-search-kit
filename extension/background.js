// Relays requests from application pages to the local helper (fill_server.py). Pages can't reach
// the helper directly: only this extension has the token and host permission.
importScripts("config.js");

const PATHS = {
  match: (m) => `/match?url=${encodeURIComponent(m.url)}`,
  job: (m) => `/job?id=${encodeURIComponent(m.id)}`,
  done: (m) => `/done?id=${encodeURIComponent(m.id)}`,
};

chrome.runtime.onMessage.addListener((msg, sender, send) => {
  if (!PATHS[msg.type]) return false;
  fetch(`http://127.0.0.1:${JS_FILL.PORT}${PATHS[msg.type](msg)}`, { headers: { "X-JS-Token": JS_FILL.TOKEN } })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`helper returned ${r.status}`))))
    .then((data) => send({ ok: true, data }))
    .catch((e) => send({ ok: false, error: `Job Search helper not reachable (${e.message}). Is fill_server.py running?` }));
  return true; // async response
});
