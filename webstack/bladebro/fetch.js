// HTTP fetch service: GET /fetch?url=<url> -> {ok,text,chars}. Runs against a persistent bladebro
// daemon (one Xvfb + one Chrome, started by entrypoint) and SERIALIZES requests, because the
// daemon holds a single page — this avoids the per-call Xvfb/Chrome port race one-shots hit.
const http = require('http');
const { execFile } = require('child_process');
const PORT = parseInt(process.env.PORT || '8000', 10);

let queue = Promise.resolve();

function fetchOne(target) {
  return new Promise(resolve => {
    execFile('bladebro', ['see', 'content', target, '--json'],
      { timeout: 75000, maxBuffer: 24 * 1024 * 1024 },
      (err, stdout) => {
        let out = { ok: false, text: '', chars: 0, error: '' };
        try {
          const m = (stdout || '').match(/\{[\s\S]*"ok"[\s\S]*\}/);
          const d = m ? JSON.parse(m[0]) : {};
          const t = d.text || '';
          out = { ok: !!d.ok && !d.is_error && !!t, text: t, chars: t.length, error: '' };
        } catch (e) { out.error = String(e).slice(0, 150); }
        if (!out.text && err) out.error = (err.message || String(err)).slice(0, 150);
        resolve(out);
      });
  });
}

http.createServer((req, res) => {
  let u;
  try { u = new URL(req.url, 'http://localhost'); } catch (e) { res.writeHead(400); return res.end('bad'); }
  if (u.pathname === '/health') { res.writeHead(200); return res.end('ok'); }
  if (u.pathname !== '/fetch') { res.writeHead(404); return res.end('not found'); }
  const target = u.searchParams.get('url');
  if (!target) { res.writeHead(400, {'Content-Type':'application/json'}); return res.end('{"ok":false,"error":"url required"}'); }
  const send = out => { res.writeHead(200, {'Content-Type':'application/json'}); res.end(JSON.stringify(out)); };
  queue = queue.then(() => fetchOne(target)).then(send).catch(e => send({ ok:false, error:String(e).slice(0,150) }));
}).listen(PORT, () => console.log('bladebro fetch service on :' + PORT + ' (GET /fetch?url=)'));
