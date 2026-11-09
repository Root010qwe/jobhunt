// Посредник между страницами вакансий и локальным сервером JobHunt (обходит CORS и mixed content).
const API = 'http://localhost:8765';

async function call(path, opt) {
  const r = await fetch(API + path, opt);
  if (!r.ok) throw new Error('JobHunt ответил ' + r.status);
  return r.json();
}

function toBase64(buf) {
  let s = '';
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    try {
      if (msg.type === 'autofill') {
        const q = new URLSearchParams({ url: msg.url || '' });
        if (msg.app_id) q.set('app_id', msg.app_id);
        let d = await call('/api/autofill?' + q);
        if (!d.app && msg.ref) d = await call('/api/autofill?' + new URLSearchParams({ url: msg.ref }));
        reply({ ok: true, data: d });
      } else if (msg.type === 'file') {
        const r = await fetch(API + msg.path);
        if (!r.ok) throw new Error('нет файла резюме');
        const name = decodeURIComponent((r.headers.get('content-disposition') || '').match(/filename\*?=(?:UTF-8'')?"?([^";]+)/i)?.[1] || 'resume.pdf');
        reply({ ok: true, name, type: r.headers.get('content-type') || 'application/pdf', b64: toBase64(await r.arrayBuffer()) });
      } else if (msg.type === 'submitted') {
        const d = await call('/api/autofill/submitted', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(msg.payload) });
        reply({ ok: true, data: d });
      } else if (msg.type === 'queue-skip') {
        reply({ ok: true, data: await call('/api/queue/' + msg.app_id + '/skip', { method: 'POST' }) });
      } else if (msg.type === 'ping') {
        reply({ ok: true, data: await call('/api/settings') });
      }
    } catch (e) {
      reply({ ok: false, error: String(e.message || e) });
    }
  })();
  return true;
});
