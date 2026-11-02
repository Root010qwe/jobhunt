// JobHunt: находит форму отклика и подставляет данные профиля, письмо и файл резюме.
(() => {
  if (window.__jobhunt) return;
  window.__jobhunt = true;

  const send = (msg) => new Promise((res) => {
    try { chrome.runtime.sendMessage(msg, (r) => res(r || { ok: false })); } catch (e) { res({ ok: false }); }
  });

  // [ключ данных, регулярка по подписи поля]. Порядок важен: первое совпадение побеждает.
  const RULES = [
    ['cover_letter', /сопровод|cover|letter|о себе|расскажите|почему|мотивац|motivation|why .*(us|join|company)|комментар|сообщени|additional info|anything else/i, 'textarea'],
    ['email', /e-?mail|почт/i],
    ['phone', /phone|телефон|моб|mobile|^tel/i],
    ['telegram', /telegram|телеграм|\btg\b|ник в тг/i],
    ['full_name', /фио|имя и фамилия|фамилия.{0,3}имя|full.?name|first and last|ваше имя|your name|^name\b/i],
    ['last_name', /фамил|last.?name|surname|family.?name/i],
    ['middle_name', /отчеств|middle.?name|patronym/i],
    ['first_name', /(^|[^а-яё])имя|first.?name|given.?name/i],
    ['linkedin', /linkedin/i],
    ['github', /github/i],
    ['hh_url', /hh\.ru|ссылк.{0,20}резюме|резюме.{0,20}ссылк|resume.{0,10}(url|link)|cv.{0,10}(url|link)/i],
    ['portfolio', /portfolio|портфолио|website|личный сайт|сайт/i],
    ['birth_date', /рожден|birth/i],
    ['city', /город|city|location|местополож|где вы (живёте|находитесь)/i],
    ['citizenship', /гражданств|citizenship/i],
    ['salary', /зарплат|salary|доход|compensation|ожидани/i],
    ['position', /должност|position|желаемая/i],
    ['university', /вуз|университет|universit|образован|education|учебн/i],
    ['english', /english|английск/i],
    ['cover_letter', /о себе|расскажи|сопровод|cover.?letter|мотивац|description/i],
  ];

  let data = null;
  let filledOnce = false;
  let reported = false;
  let badge = null;

  // Подпись поля: сначала собственные атрибуты и <label>, затем текст контейнера,
  // но только если в контейнере одно поле (иначе подхватим подпись соседа).
  function describe(el, wide) {
    const bits = [el.name, el.id, el.placeholder, el.getAttribute('aria-label'), el.getAttribute('autocomplete'), el.getAttribute('data-qa'), el.title];
    if (el.id) {
      try { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) bits.push(l.innerText); } catch (e) {}
    }
    const wrap = el.closest('label');
    if (wrap) bits.push(wrap.innerText);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) lb.split(/\s+/).forEach((id) => { const n = document.getElementById(id); if (n) bits.push(n.innerText); });
    if (wide) {
      let p = el.parentElement;
      for (let i = 0; p && i < 4; i++, p = p.parentElement) {
        if (p.querySelectorAll('input:not([type=hidden]), textarea, select').length > 1) break;
        const t = (p.innerText || '').trim();
        if (t && t.length < 160) { bits.push(t); break; }
      }
      // подпись прямо перед полем (div с текстом, затем поле)
      const prev = el.previousElementSibling;
      if (prev && !prev.matches('input,textarea,select') && (prev.innerText || '').length < 120) bits.push(prev.innerText);
    }
    return bits.filter(Boolean).join(' | ').replace(/\s+/g, ' ').slice(0, 400);
  }

  function match(el) {
    for (const wide of [false, true]) {
      const d = describe(el, wide);
      for (const [k, re, only] of RULES) {
        if (only && el.tagName.toLowerCase() !== only) continue;
        if (re.test(d)) return k;
      }
      if (el.tagName === 'TEXTAREA' && /письм|letter|message|текст/i.test(d)) return 'cover_letter';
    }
    return null;
  }

  function visible(el) {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  }

  function setValue(el, value) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    el.focus();
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    el.style.outline = '2px solid #1FA457';
    el.style.outlineOffset = '1px';
    el.dataset.jobhunt = '1';
  }

  function valueFor(key, el) {
    const p = data.profile || {};
    if (key === 'cover_letter') return data.cover_letter;
    if (key === 'birth_date' && p.birth_date && el.type !== 'date') return p.birth_date.split('-').reverse().join('.');
    if (key === 'phone' && p.phone) return p.phone;
    return p[key] || '';
  }

  async function attachResume(input) {
    if (!data.resume || input.dataset.jobhunt) return false;
    const r = await send({ type: 'file', path: data.resume.url });
    if (!r.ok) return false;
    const bin = atob(r.b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], r.name, { type: r.type }));
    input.files = dt.files;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dataset.jobhunt = '1';
    return true;
  }

  async function fill(force) {
    if (!data) return 0;
    let n = 0;
    const fields = [...document.querySelectorAll('input, textarea')];
    for (const el of fields) {
      if (el.dataset.jobhunt || el.disabled || el.readOnly) continue;
      const type = (el.type || 'text').toLowerCase();
      if (type === 'file') {
        const d = describe(el, true) + ' ' + (el.accept || '');
        if (/резюме|cv|resume|pdf|файл|attach|upload|прикреп/i.test(d) && await attachResume(el)) n++;
        continue;
      }
      if (['hidden', 'password', 'checkbox', 'radio', 'submit', 'button', 'search', 'range', 'color'].includes(type)) continue;
      if (!visible(el) || (el.value && !force)) continue;
      const key = type === 'email' ? 'email' : type === 'tel' ? 'phone' : match(el);
      if (!key) continue;
      const v = valueFor(key, el);
      if (!v) continue;
      setValue(el, v);
      n++;
    }
    if (n) { filledOnce = true; showBadge(n); }
    return n;
  }

  function looksLikeForm() {
    const els = [...document.querySelectorAll('input, textarea')].filter((e) => visible(e) && !['hidden', 'search', 'checkbox', 'radio', 'submit'].includes((e.type || '').toLowerCase()));
    if (!els.length) return false;
    const hits = els.filter((e) => /e-?mail|почт|телефон|phone|фамил|имя|name|сопровод|cover|резюме|resume|cv/i.test(describe(e, true) + ' ' + e.type)).length;
    return hits >= 2;
  }

  function showBadge(n) {
    if (window.top !== window && !n) return;
    if (!badge) {
      badge = document.createElement('div');
      badge.setAttribute('style', 'position:fixed;z-index:2147483647;right:16px;bottom:16px;background:#17201B;color:#fff;font:500 13px/1.35 -apple-system,system-ui,sans-serif;padding:10px 12px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.25);display:flex;gap:10px;align-items:center;max-width:360px');
      document.documentElement.appendChild(badge);
    }
    const app = data && data.app;
    const head = app ? `${app.company} — ${app.role}` : 'JobHunt';
    badge.innerHTML = '';
    const txt = document.createElement('span');
    txt.textContent = n ? `${head}: заполнено полей — ${n}. Проверьте и отправьте.` : `${head}: заполнить форму?`;
    badge.appendChild(txt);
    const btn = (label, fn) => {
      const b = document.createElement('button');
      b.textContent = label;
      b.setAttribute('style', 'all:unset;cursor:pointer;background:#2F45C5;color:#fff;padding:5px 9px;border-radius:6px;white-space:nowrap');
      b.addEventListener('click', fn);
      badge.appendChild(b);
    };
    if (!n) btn('Заполнить', () => fill(false));
    else btn('Подал', () => report(true));
    const x = document.createElement('button');
    x.textContent = '×';
    x.setAttribute('style', 'all:unset;cursor:pointer;opacity:.7;padding:0 4px');
    x.addEventListener('click', () => { badge.remove(); badge = null; });
    badge.appendChild(x);
  }

  function pageTitle() {
    const h = document.querySelector('h1');
    return (h && h.innerText.trim().slice(0, 150)) || document.title.slice(0, 150);
  }

  async function report(manual) {
    if (reported) return;
    reported = true;
    const r = await send({ type: 'submitted', payload: { url: location.href, app_id: data && data.app && data.app.id, title: pageTitle(), company: '' } });
    if (badge) {
      badge.firstChild.textContent = r.ok ? 'Отмечено в JobHunt как «Подано».' : 'JobHunt недоступен — отметьте отклик вручную.';
      [...badge.querySelectorAll('button')].slice(0, -1).forEach((b) => b.remove());
      setTimeout(() => { if (badge) { badge.remove(); badge = null; } }, 5000);
    }
    if (!manual) return;
  }

  // отправка формы после автозаполнения → «Подано»
  document.addEventListener('submit', () => { if (filledOnce) report(false); }, true);
  document.addEventListener('click', (e) => {
    if (!filledOnce) return;
    const b = e.target.closest('button, input[type="submit"], [role="button"]');
    if (!b) return;
    const t = (b.innerText || b.value || '').trim();
    if (/^(отправить|откликнуться|подать|submit|apply|send|отправить отклик|отправить заявку)/i.test(t)) setTimeout(() => report(false), 800);
  }, true);

  chrome.runtime.onMessage.addListener((msg, _s, reply) => {
    if (msg.type === 'fill-now') {
      (async () => {
        if (!data) await load(msg.app_id);
        reply({ n: await fill(!!msg.force), app: data && data.app });
      })();
      return true;
    }
    if (msg.type === 'mark-applied') { report(true).then(() => reply({ ok: true })); return true; }
  });

  async function load(appId) {
    const r = await send({ type: 'autofill', url: location.href, ref: document.referrer || '', app_id: appId });
    if (r.ok) data = r.data;
    return data;
  }

  // Автозапуск: если эта вакансия есть в трекере со статусом «Готово к подаче» — заполняем сами,
  // иначе показываем кнопку «Заполнить», когда на странице есть форма.
  const JOBPAGE = /vacanc|vacancy|job|career|ваканс|карьер|hh\.ru|apply|position|hiring|talent|recruit|стаж|intern|ashbyhq|greenhouse|lever\.co|huntflow|potok|friend\.work/i;
  let tries = 0;
  let timer = null;
  async function tick() {
    tries++;
    if (!data) await load();
    if (!data) return;
    const ready = data.app && data.app.status === 'ready';
    if (ready) {
      await fill(false);
    } else if (!badge && JOBPAGE.test(location.href + ' ' + document.title) && looksLikeForm()) {
      showBadge(0);
    }
  }
  // Формы часто появляются позже (модалка после «Откликнуться») — проверяем страницу 2 минуты.
  const started = Date.now();
  let busy = false;
  const iv = setInterval(async () => {
    if (Date.now() - started > 120000) { clearInterval(iv); return; }
    if (busy) return;
    busy = true;
    try { await tick(); } finally { busy = false; }
  }, 1500);
})();
