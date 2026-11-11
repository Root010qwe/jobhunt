// JobHunt: заполняет форму отклика на сайте компании.
// Работает сам только на вакансии, подготовленной в дашборде (отклик «Готово к подаче»).
// На остальных страницах молчит, пока не нажать «Заполнить» в иконке расширения.
(() => {
  if (window.__jobhunt) return;
  window.__jobhunt = true;

  const send = (msg) => new Promise((res) => {
    try { chrome.runtime.sendMessage(msg, (r) => res(r || { ok: false })); } catch (e) { res({ ok: false }); }
  });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const TOP = window.top === window;

  let data = null;          // ответ /api/autofill
  let form = null;          // найденная форма отклика
  let filled = new Map();   // поле → ключ данных
  let revealed = false;     // уже нажимали «Откликнуться», чтобы открыть форму
  let reported = false;
  let badge = null;
  let advancing = false;

  // ---------- распознавание полей ----------
  // [ключ, регулярка по подписи]. Первое совпадение побеждает.
  const RULES = [
    ['email', /e-?mail|эл\.? ?почт|электронн/i],
    ['phone', /телефон|phone|мобильн|номер для связи|^tel/i],
    ['telegram', /telegram|телеграм|\btg\b/i],
    ['full_name', /фио|имя и фамилия|фамилия,? имя|full.?name|first and last|ваше имя|your name|^name\b/i],
    ['last_name', /фамил|last.?name|surname|family.?name/i],
    ['middle_name', /отчеств|middle.?name|patronym/i],
    ['first_name', /(^|[^а-яё])имя|first.?name|given.?name/i],
    ['linkedin', /linkedin/i],
    ['github', /github/i],
    ['hh_url', /hh\.ru|ссылк.{0,20}резюме|резюме.{0,20}ссылк|resume.{0,12}(url|link)|cv.{0,10}(url|link)/i],
    ['portfolio', /portfolio|портфолио|личный сайт/i],
    ['birth_date', /дата рожд|birth/i],
    ['city', /^город|город прожив|city|location|местоположение/i],
    ['citizenship', /гражданств|citizenship/i],
    ['salary', /зарплат|salary|доход|compensation|ожидан.{0,10}(зп|з\/п|оплат)/i],
    ['university', /вуз|университет|universit|учебн.{0,10}завед/i],
    ['cover_letter', /сопровод|cover.?letter|о себе|расскажите|почему (вы|именно)|мотивац|why .*(us|join)|комментарий|дополнительн.{0,12}(информац|сведен)|additional info|сообщение/i],
  ];
  const SKIP = /поиск|search|промокод|promo|captcha|капч|код из|sms|смс|пароль|password|логин|login|войти|подпис|рассылк|newsletter|subscribe/i;
  const NOT_FORM = /подпишитесь|подписаться на рассылку|newsletter|subscribe|поиск по сайту|войти в аккаунт|авторизац/i;
  const REVEAL = /^(откликнуться|отправить резюме|подать заявку|оставить отклик|хочу в команду|apply( now| for this job)?|respond)$/i;
  const SUBMIT = /^(отправить|откликнуться|подать|отправить отклик|отправить заявку|отправить резюме|submit|apply|send)/i;

  const visible = (el) => {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };

  // подпись поля: свои атрибуты и <label>; текст вокруг — только если в контейнере одно поле
  function describe(el, wide) {
    const bits = [el.name, el.id, el.placeholder, el.getAttribute('aria-label'), el.getAttribute('autocomplete'), el.getAttribute('data-qa'), el.title];
    if (el.id) { try { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) bits.push(l.innerText); } catch (e) {} }
    const wrap = el.closest('label'); if (wrap) bits.push(wrap.innerText);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) lb.split(/\s+/).forEach((id) => { const n = document.getElementById(id); if (n) bits.push(n.innerText); });
    if (wide) {
      let p = el.parentElement;
      for (let i = 0; p && i < 4; i++, p = p.parentElement) {
        if (p.querySelectorAll('input:not([type=hidden]), textarea, select').length > 1) break;
        const t = (p.innerText || '').trim();
        if (t && t.length < 200) { bits.push(t); break; }
      }
      const prev = el.previousElementSibling;
      if (prev && !prev.matches('input,textarea,select') && (prev.innerText || '').length < 120) bits.push(prev.innerText);
    }
    return bits.filter(Boolean).join(' | ').replace(/\s+/g, ' ').slice(0, 500);
  }

  function fieldKey(el) {
    const type = (el.type || 'text').toLowerCase();
    if (type === 'email') return 'email';
    if (type === 'tel') return 'phone';
    for (const wide of [false, true]) {
      const d = describe(el, wide);
      if (SKIP.test(d)) return null;
      for (const [k, re] of RULES) {
        if (k === 'cover_letter' && el.tagName !== 'TEXTAREA' && !/сопровод|о себе|cover|мотивац/i.test(d)) continue;
        if (re.test(d)) return k;
      }
      if (el.tagName === 'TEXTAREA' && /письм|letter|текст/i.test(d)) return 'cover_letter';
    }
    return null;
  }

  function fieldsIn(root) {
    return [...root.querySelectorAll('input, textarea')].filter((el) => {
      const t = (el.type || 'text').toLowerCase();
      return !['hidden', 'password', 'checkbox', 'radio', 'submit', 'button', 'search', 'range', 'color', 'image', 'reset'].includes(t) && !el.disabled;
    });
  }

  // форма отклика: контейнер, где есть хотя бы два «наших» поля (или файл + одно)
  function findForm(minScore = 2) {
    const roots = new Set([...document.querySelectorAll('form, [role="dialog"], [class*="modal" i], [class*="popup" i], [class*="response" i], [class*="apply" i], [class*="feedback" i]')]);
    let best = null, bestScore = 0;
    for (const root of roots) {
      if (!visible(root) || (badge && badge.contains(root))) continue;
      const text = (root.innerText || '').slice(0, 2000);
      const fields = fieldsIn(root).filter(visible);
      const files = [...root.querySelectorAll('input[type=file]')];
      const keys = new Set(fields.map(fieldKey).filter(Boolean));
      if (NOT_FORM.test(text) && keys.size <= 1) continue;                   // подписка, поиск, вход
      if (keys.size === 1 && keys.has('email') && !files.length) continue;   // одинокое поле email — это рассылка
      const score = keys.size + (files.length ? 1 : 0) + (keys.has('cover_letter') ? 1 : 0);
      // при равенстве — более узкий контейнер (форма внутри модалки, а не вся модалка)
      if (score > bestScore || (score === bestScore && best && best.contains(root))) { best = root; bestScore = score; }
    }
    if (!best && minScore <= 1) {
      // запасной вариант для кнопки «Заполнить»: вся страница
      const keys = new Set(fieldsIn(document).filter(visible).map(fieldKey).filter(Boolean));
      if (keys.size) return document.body;
    }
    return bestScore >= minScore ? best : null;
  }

  // ---------- значения ----------
  function limitOf(el) {
    if (el.maxLength > 0) return el.maxLength;
    const m = describe(el, true).match(/(?:до|не более|максимум|max\.?|maximum)?\s*(\d{2,5})\s*(?:символ|знак|characters|chars)/i);
    return m ? +m[1] : 0;
  }
  function fitText(full, short, limit) {
    if (!limit || full.length <= limit) return full;
    if (short && short.length <= limit) return short;
    const base = short || full;
    const cut = base.slice(0, limit);
    const end = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('! '), cut.lastIndexOf('.\n'));
    return end > limit * 0.5 ? cut.slice(0, end + 1) : cut.replace(/\s+\S*$/, '').slice(0, limit);
  }
  function valueFor(key, el) {
    const p = data.profile || {};
    if (key === 'cover_letter') return fitText(data.cover_letter || '', data.cover_short || '', limitOf(el));
    if (key === 'birth_date' && p.birth_date && el.type !== 'date') return p.birth_date.split('-').reverse().join('.');
    const v = p[key] || '';
    const lim = limitOf(el);
    return lim && v.length > lim ? v.slice(0, lim) : v;
  }

  function setValue(el, value) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dataset.jobhunt = '1';
    el.style.boxShadow = '0 0 0 2px rgba(31,164,87,.55)';
  }

  async function attachResume(input) {
    if (!data.resume || filled.has(input)) return false;
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
    filled.set(input, 'resume');
    return true;
  }

  const LABEL = { email: 'почта', phone: 'телефон', telegram: 'Telegram', full_name: 'ФИО', last_name: 'фамилия', first_name: 'имя',
    middle_name: 'отчество', linkedin: 'LinkedIn', github: 'GitHub', hh_url: 'ссылка на резюме', portfolio: 'портфолио', birth_date: 'дата рождения',
    city: 'город', citizenship: 'гражданство', salary: 'зарплата', university: 'вуз', cover_letter: 'письмо', resume: 'файл резюме' };

  // заполняет только пустые поля формы, каждое — один раз
  async function fillForm(root, overwrite) {
    let n = 0;
    for (const el of fieldsIn(root)) {
      if (filled.has(el) && !overwrite) continue;
      if ((el.type || '').toLowerCase() === 'file') {
        const d = describe(el, true) + ' ' + (el.accept || '');
        if (/резюме|cv|resume|pdf|файл|attach|upload|прикреп|документ/i.test(d) && await attachResume(el)) n++;
        continue;
      }
      if (!visible(el) || el.readOnly) continue;
      if (el.value && !overwrite) { filled.set(el, null); continue; }   // заполнено до нас — не трогаем
      const key = fieldKey(el);
      if (!key) continue;
      const v = valueFor(key, el);
      if (!v) continue;
      setValue(el, v);
      filled.set(el, key);
      n++;
    }
    return n;
  }

  function emptyRequired(root) {
    return fieldsIn(root).filter((el) => visible(el) && !el.value && (el.required || el.getAttribute('aria-required') === 'true'))
      .map((el) => (describe(el, true).split('|').map((s) => s.trim()).find((s) => s && s.length < 40 && !/^[a-z_\-\d\[\]]+$/i.test(s)) || 'поле'))
      .slice(0, 4);
  }

  // ---------- плашка ----------
  function ui(text, buttons) {
    if (!TOP && !form) return;
    if (!badge) {
      badge = document.createElement('div');
      badge.setAttribute('style', 'position:fixed;z-index:2147483647;right:16px;bottom:16px;width:340px;background:#17201B;color:#fff;font:400 13px/1.4 -apple-system,system-ui,sans-serif;padding:12px 14px;border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.28);display:grid;gap:8px');
      document.documentElement.appendChild(badge);
    }
    badge.innerHTML = '';
    const head = document.createElement('div');
    head.setAttribute('style', 'display:flex;gap:8px;align-items:baseline');
    const title = document.createElement('b');
    title.textContent = data && data.app ? `${data.app.company} — ${data.app.role}` : 'JobHunt';
    title.setAttribute('style', 'flex:1;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap');
    const x = document.createElement('button');
    x.textContent = '×';
    x.setAttribute('style', 'all:unset;cursor:pointer;opacity:.6;padding:0 2px;font-size:16px');
    x.onclick = () => { badge.remove(); badge = null; };
    head.append(title, x);
    const body = document.createElement('div');
    body.setAttribute('style', 'color:#d6ddd8;white-space:pre-line');
    body.textContent = text;
    badge.append(head, body);
    if (buttons && buttons.length) {
      const row = document.createElement('div');
      row.setAttribute('style', 'display:flex;gap:8px;flex-wrap:wrap');
      for (const [label, fn, quiet] of buttons) {
        const b = document.createElement('button');
        b.textContent = label;
        b.setAttribute('style', 'all:unset;cursor:pointer;padding:6px 10px;border-radius:7px;font-weight:500;' + (quiet ? 'color:#d6ddd8;box-shadow:inset 0 0 0 1px #4a5650' : 'background:#2F45C5;color:#fff'));
        b.onclick = fn;
        row.appendChild(b);
      }
      badge.appendChild(row);
    }
  }

  function summary() {
    const keys = [...new Set([...filled.values()].filter(Boolean))].map((k) => LABEL[k] || k);
    const q = data && data.queue;
    let t = q ? `Очередь ${q.pos} из ${q.total}. ` : '';
    t += keys.length ? `Заполнено: ${keys.join(', ')}.` : 'Форму не заполнял.';
    const empty = form ? emptyRequired(form) : [];
    if (empty.length) t += `\nЗаполните сами: ${empty.join(', ')}.`;
    t += '\nПроверьте и отправьте.';
    return t;
  }
  function showSummary() {
    const q = data && data.queue;
    ui(summary(), q && TOP ? [['Отправить и дальше', sendAndNext], ['Пропустить', skipToNext, true]] : [['Подал', () => report(true), true]]);
  }

  // ---------- отправка ----------
  function pageTitle() {
    const h = document.querySelector('h1');
    return (h && h.innerText.trim().slice(0, 150)) || document.title.slice(0, 150);
  }
  async function report(manual) {
    if (reported) return;
    reported = true;
    const r = await send({ type: 'submitted', payload: { url: location.href, app_id: data && data.app && data.app.id, title: pageTitle(), company: '' } });
    if (data && data.queue && TOP && !advancing) { advancing = true; return goNext(data.queue.next_url); }
    ui(r.ok ? 'Отмечено в трекере: «Подано».' : 'JobHunt не отвечает — отметьте отклик в трекере сами.');
    setTimeout(() => { if (badge) { badge.remove(); badge = null; } }, 4000);
  }
  function watchSubmit(root) {
    if (!root || root.__jhWatch) return;
    root.__jhWatch = true;
    root.addEventListener('submit', () => setTimeout(() => report(false), 600), true);
    root.addEventListener('click', (e) => {
      const b = e.target.closest('button, input[type="submit"], [role="button"]');
      if (b && SUBMIT.test((b.innerText || b.value || '').trim())) setTimeout(() => report(false), 1200);
    }, true);
  }

  function findSubmit() {
    const scope = form || document;
    const all = [...scope.querySelectorAll('button, input[type="submit"], [role="button"]')]
      .filter((b) => visible(b) && !(badge && badge.contains(b)) && SUBMIT.test((b.innerText || b.value || '').trim()));
    return all.find((b) => b.type === 'submit') || all[all.length - 1];
  }
  async function goNext(url) {
    if (url) { ui('Открываю следующую вакансию…'); await sleep(800); location.href = url; }
    else ui('Очередь закончилась. Отклики отмечены в трекере.');
  }
  async function sendAndNext() {
    if (advancing) return;
    advancing = true;
    ui('Отправляю…');
    const b = findSubmit();
    if (!b) { advancing = false; ui('Не нашёл кнопку отправки. Отправьте сами — следующая вакансия откроется после этого.'); return; }
    b.click();
    await sleep(2500);
    reported = false;
    advancing = false;
    await report(false);
  }
  async function skipToNext() {
    if (advancing) return;
    advancing = true;
    const r = await send({ type: 'queue-skip', app_id: data.app.id });
    await goNext(r.ok && r.data.next ? r.data.next.link : '');
  }


  // ---------- вопросы работодателя (анкета, тестовое) ----------
  const IDENTITY = new Set(['email', 'phone', 'telegram', 'full_name', 'last_name', 'first_name', 'middle_name', 'linkedin', 'github', 'hh_url', 'portfolio', 'birth_date', 'city', 'citizenship']);
  const CONSENT = /согласи|персональн|политик|оферт|правил|подпис|рассылк|newsletter|запомнить|remember/i;
  let qmap = {};

  function optionText(el) {
    const l = el.closest('label') || (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'));
    const t = (l && l.innerText) || el.getAttribute('aria-label') || (el.nextElementSibling && el.nextElementSibling.innerText) || el.value || '';
    return t.trim().replace(/\s+/g, ' ').slice(0, 200);
  }

  function extractQuestions() {
    const all = [...document.querySelectorAll('input, textarea')].filter((el) => !(badge && badge.contains(el)) && !el.disabled);
    const groups = [];
    const byName = {};
    for (const el of all) {
      const type = (el.type || 'text').toLowerCase();
      if (type === 'radio' || type === 'checkbox') {
        if (!visible(el) && !visible(el.closest('label') || el.parentElement)) continue;
        if (CONSENT.test(optionText(el))) continue;
        const key = type + ':' + (el.name || el.id);
        if (!byName[key]) { byName[key] = { type, els: [] }; groups.push(byName[key]); }
        byName[key].els.push(el);
      } else if (el.tagName === 'TEXTAREA' || ['text', ''].includes(type)) {
        if (!visible(el) || el.readOnly) continue;
        const k = fieldKey(el);
        if (k && (IDENTITY.has(k) || k === 'cover_letter' && /сопровод|cover/i.test(describe(el, true)))) continue;
        if (SKIP.test(describe(el, false))) continue;
        groups.push({ type: el.tagName === 'TEXTAREA' ? 'textarea' : 'text', els: [el] });
      }
    }
    const controls = new Set(all);
    const qs = [];
    qmap = {};
    groups.forEach((g, i) => {
      // текст вопроса: самый широкий контейнер, где нет полей других вопросов
      let box = g.els[0].parentElement;
      while (box && !g.els.every((e) => box.contains(e))) box = box.parentElement;
      for (let k = 0; box && box.parentElement && k < 6; k++) {
        const up = box.parentElement;
        const foreign = [...up.querySelectorAll('input, textarea')].some((e) => controls.has(e) && !g.els.includes(e) && visible(e));
        if (foreign || (up.innerText || '').length > 3000) break;
        box = up;
      }
      let text = (box ? box.innerText : '') || '';
      const opts = g.type === 'radio' || g.type === 'checkbox' ? g.els.map(optionText) : [];
      for (const o of opts) if (o) text = text.split(o).join(' ');
      text = text.replace(/\s+/g, ' ').trim().slice(0, 1500) || describe(g.els[0], true);
      const id = 'q' + (i + 1);
      qmap[id] = g;
      const lim = g.els[0].maxLength > 0 ? g.els[0].maxLength : 0;
      qs.push({ id, type: g.type, question: text, options: opts, max_length: lim || undefined });
    });
    return qs;
  }

  function pickOption(g, wanted) {
    const norm = (s) => String(s || '').toLowerCase().replace(/\s+/g, ' ').trim();
    let n = 0;
    for (const w of wanted || []) {
      const el = g.els.find((e) => norm(optionText(e)) === norm(w)) || g.els.find((e) => norm(optionText(e)).includes(norm(w)) || norm(w).includes(norm(optionText(e))));
      if (el && !el.checked) { (el.closest('label') || el).click(); if (!el.checked) el.click(); n++; }
      else if (el) n++;
    }
    return n;
  }

  async function answerQuestions() {
    const qs = extractQuestions();
    // поле письма: если готового письма под эту вакансию нет — попросим написать его вместе с ответами
    const letterEl = [...document.querySelectorAll('textarea')].find((t) => visible(t) && !t.value && /сопровод|cover|letter/i.test(describe(t, true)));
    if (letterEl && (!data || data.letter_is_template)) {
      qmap.letter = { type: 'letter', els: [letterEl] };
      qs.push({ id: 'letter', type: 'letter', question: 'Сопроводительное письмо к этой вакансии', options: [], max_length: letterEl.maxLength > 0 ? letterEl.maxLength : undefined });
    }
    if (!qs.length) { ui('На этой странице не нашёл вопросов, на которые нужно ответить.'); return 0; }
    ui(`Нашёл вопросов: ${qs.length}. Отвечаю — обычно до минуты…`);
    const h1 = document.querySelector('h1');
    const r = await send({ type: 'answer-questions', payload: {
      url: location.href, ref: document.referrer || '', title: (h1 && h1.innerText.trim()) || document.title,
      page_text: (document.body.innerText || '').slice(0, 6000), questions: qs } });
    if (!r.ok) { ui('Не получилось: ' + (r.error || 'JobHunt не ответил') + '.', [['Ещё раз', answerQuestions]]); return 0; }
    let n = 0, todo = 0;
    for (const a of r.data.answers || []) {
      const g = qmap[a.id];
      if (!g) continue;
      if (g.type === 'radio' || g.type === 'checkbox') { n += pickOption(g, a.choice) ? 1 : 0; continue; }
      let text = a.text || '';
      const el = g.els[0];
      const lim = limitOf(el);
      if (lim && text.length > lim) text = fitText(text, '', lim);
      if (/\[уточнить/i.test(text)) todo++;
      setValue(el, text);
      el.style.boxShadow = '0 0 0 2px rgba(214,145,0,.7)';   // жёлтая рамка: проверьте
      filled.set(el, 'answer');
      n++;
    }
    // готовое письмо под эту вакансию (выбранная версия в JobHunt) — подставим, если поле ещё пустое
    if (letterEl && !letterEl.value && data && !data.letter_is_template && data.cover_letter) {
      setValue(letterEl, fitText(data.cover_letter, data.cover_short, limitOf(letterEl)));
    }
    const asked = qs.filter((q) => q.type !== 'letter').length;
    const nq = Math.min(n, asked) - 0;
    ui(`Ответил на вопросы: ${Math.min(n, asked)} из ${asked}` + (qmap.letter ? ', письмо написал под вакансию' : '') + '. Всё подсвечено жёлтым — прочитайте и поправьте.' +
       (todo ? `\nПометок [уточнить]: ${todo} — впишите своё.` : '') + (r.data.notes ? '\n' + r.data.notes : ''),
       [['Ответить заново', answerQuestions, true]]);
    return n;
  }

  function offerQuestions() {
    if (!TOP || badge) return;
    const onHH = /hh\.ru$/.test(location.hostname) && /vacancy_response|negotiations|applicant/.test(location.pathname);
    const qs = extractQuestions();
    const real = qs.filter((q) => q.type !== 'text' || q.question.length > 25);
    if (!real.length || (!onHH && real.length < 2)) return;
    ui(`На странице вопросы работодателя: ${real.length}. Могу ответить по вашему резюме — вы потом поправите.`,
       [['Ответить на вопросы', answerQuestions]]);
  }

  // ---------- сценарий ----------
  function findReveal() {
    const hh = document.querySelector('[data-qa="vacancy-response-link-top"], [data-qa="vacancy-response-link-bottom"]');
    if (hh && visible(hh)) return hh;
    return [...document.querySelectorAll('button, a, [role="button"]')]
      .find((b) => visible(b) && !(badge && badge.contains(b)) && REVEAL.test((b.innerText || '').trim()) &&
        !(b.tagName === 'A' && b.href && new URL(b.href, location.href).host !== location.host));
  }

  async function step() {
    const f = findForm(2);
    if (f) {
      form = f;
      watchSubmit(f);
      const n = await fillForm(f, false);
      if (n || !badge) showSummary();
      return true;
    }
    if (!revealed && TOP) {
      const b = findReveal();
      if (b && /(^|\.)hh\.ru$/.test(location.hostname)) {
        // на hh.ru «Откликнуться» часто сразу отправляет отклик — нажимает только сам человек
        if (!badge) ui('Нажмите — откроется отклик на hh.ru, я вставлю письмо. Если письмо не требуется, hh отправит отклик сразу.',
          [['Откликнуться на hh.ru', async () => {
            revealed = true; b.click(); ui('Откликаюсь…');
            await sleep(1500);
            const reloc = document.querySelector('[data-qa="relocation-warning-confirm"]'); if (reloc) { reloc.click(); await sleep(1200); }
            const letter = document.querySelector('[data-qa="vacancy-response-letter-toggle"]'); if (letter) { letter.click(); await sleep(700); }
            if (!(await step())) setTimeout(() => { if (!form) report(false); }, 2000);
          }]]);
        return false;
      }
      if (b) {
        revealed = true;
        ui('Открываю форму отклика…');
        b.click();
        for (const ms of [700, 1500, 3000]) setTimeout(() => { if (!form) step(); }, ms);
        // hh.ru: предупреждение о другом городе и кнопка «Написать сопроводительное»
        await sleep(1500);
        const reloc = document.querySelector('[data-qa="relocation-warning-confirm"]'); if (reloc) reloc.click();
        const letter = document.querySelector('[data-qa="vacancy-response-letter-toggle"]'); if (letter) { await sleep(500); letter.click(); }
      }
    }
    return false;
  }

  async function run() {
    const r = await send({ type: 'autofill', url: location.href, ref: document.referrer || '' });
    if (!r.ok) return;
    data = r.data;
    setTimeout(offerQuestions, 1500);   // анкета работодателя (hh и др.) — предложим ответить
    if (!(data.app && data.app.status === 'ready')) return;   // не наша вакансия — формы не трогаем
    await step();
    // форма или новые поля появятся позже (модалка, шаги) — следим за страницей не дольше минуты
    let timer = null;
    const started = Date.now();
    const obs = new MutationObserver((muts) => {
      if (Date.now() - started > 60000) { obs.disconnect(); return; }
      if (muts.every((m) => m.type === 'attributes' && m.target.dataset && m.target.dataset.jobhunt)) return;
      clearTimeout(timer);
      timer = setTimeout(step, 600);
    });
    obs.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class', 'hidden', 'open', 'aria-hidden'] });
    if (TOP) setTimeout(() => { if (!form && data.queue) ui('Не нашёл форму отклика на этой странице.', [['Пропустить', skipToNext, true]]); }, 15000);
  }

  // ручной режим из иконки расширения
  chrome.runtime.onMessage.addListener((msg, _s, reply) => {
    if (msg.type === 'fill-now') {
      (async () => {
        if (!data) { const r = await send({ type: 'autofill', url: location.href, ref: document.referrer || '' }); if (r.ok) data = r.data; }
        if (!data) { reply({ n: 0 }); return; }
        form = findForm(1);
        if (!form && !revealed) { await step(); await sleep(2500); form = findForm(1); }
        if (!form) { reply({ n: 0 }); return; }
        watchSubmit(form);
        const n = await fillForm(form, !!msg.force);
        if (n) showSummary();
        reply({ n, app: data.app });
      })();
      return true;
    }
    if (msg.type === 'answer-now') {
      (async () => {
        if (!data) { const r = await send({ type: 'autofill', url: location.href, ref: document.referrer || '' }); if (r.ok) data = r.data; }
        reply({ n: await answerQuestions() });
      })();
      return true;
    }
    if (msg.type === 'mark-applied') { report(true).then(() => reply({ ok: true })); return true; }
  });

  if (document.readyState === 'complete') setTimeout(run, 800);
  else window.addEventListener('load', () => setTimeout(run, 800));
})();
