document.getElementById('ver').textContent = 'v' + chrome.runtime.getManifest().version;
const $ = (s) => document.querySelector(s);
const msg = (m) => new Promise((r) => chrome.runtime.sendMessage(m, (x) => r(x || {})));

async function tab() { return (await chrome.tabs.query({ active: true, currentWindow: true }))[0]; }

async function toTab(m) {
  const t = await tab();
  // в форме в iframe тоже: шлём во все фреймы, берём лучший ответ
  return new Promise((res) => chrome.tabs.sendMessage(t.id, m, (r) => res(r || null)));
}

(async () => {
  const ping = await msg({ type: 'ping' });
  if (!ping.ok) { $('#st').innerHTML = 'JobHunt не запущен. Запустите <b>~/JobHunt/run.sh</b>.'; return; }
  const t = await tab();
  const r = await msg({ type: 'autofill', url: t.url });
  if (r.ok && r.data.app) {
    const a = r.data.app;
    $('#app').hidden = false;
    $('#app').textContent = `${a.company} — ${a.role}`;
    $('#st').textContent = 'Эта вакансия есть в трекере.';
  } else {
    $('#st').textContent = 'Вакансии нет в трекере — заполню профилем и письмом по шаблону, а при отправке заведу отклик.';
  }
})();

$('#fill').onclick = async () => {
  const r = await toTab({ type: 'fill-now' });
  $('#st').textContent = r ? `Заполнено полей: ${r.n}. Проверьте и отправьте.` : 'На этой странице расширение не работает (обновите страницу).';
};
$('#force').onclick = async () => {
  const r = await toTab({ type: 'fill-now', force: true });
  $('#st').textContent = r ? `Перезаписано полей: ${r.n}.` : 'Не получилось — обновите страницу.';
};
$('#mark').onclick = async () => {
  const r = await toTab({ type: 'mark-applied' });
  $('#st').textContent = r ? 'Отмечено «Подано».' : 'Не получилось — обновите страницу.';
};

$('#answer').onclick = async () => {
  $('#st').textContent = 'Отвечаю на вопросы — обычно до минуты. Окно можно закрыть.';
  const r = await toTab({ type: 'answer-now' });
  $('#st').textContent = r ? `Ответов вписано: ${r.n}. Проверьте подсвеченные поля.` : 'Не получилось — обновите страницу.';
};
