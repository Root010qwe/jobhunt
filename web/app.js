(function () {
  'use strict';

  var ST = [
    { k: 'ready', t: 'Готово к подаче' }, { k: 'applied', t: 'Подано' }, { k: 'screening', t: 'Скрининг' },
    { k: 'interview', t: 'Интервью' }, { k: 'test', t: 'Тестовое' }, { k: 'waiting', t: 'Жду ответа' },
    { k: 'offer', t: 'Оффер' }, { k: 'rejected', t: 'Отказ' }, { k: 'skipped', t: 'Не подаю' }
  ];
  var STMAP = {}; ST.forEach(function (s) { STMAP[s.k] = s; });
  var WEIGHT = { offer: 0, interview: 1, test: 2, waiting: 3, screening: 4, applied: 5, ready: 6, rejected: 7, skipped: 8 };
  var ACTIVE = { applied: 1, screening: 1, interview: 1, test: 1, waiting: 1 };

  var $ = function (s) { return document.querySelector(s); };
  var apps = [], resumes = [], profile = {};
  var filter = 'all', sortBy = 'act', query = '';
  var editing = null, delArmed = false;
  try { filter = localStorage.getItem('jh.filter') || 'all'; sortBy = localStorage.getItem('jh.sort') || 'act'; } catch (e) {}
  $('#sort').value = sortBy;

  /* ---------- утилиты ---------- */
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function safeUrl(u) { return /^https?:\/\/[^\s"<>]+$/i.test(u || '') ? u : ''; }
  function pad(n) { return String(n).padStart(2, '0'); }
  function todayISO() { var d = new Date(); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function isISO(s) { return /^\d{4}-\d{2}-\d{2}$/.test(s || ''); }
  function dayNum(iso) { var p = iso.split('-').map(Number); return Math.round(Date.UTC(p[0], p[1] - 1, p[2]) / 86400000); }
  function diffDays(iso) { return dayNum(iso) - dayNum(todayISO()); }
  function fmt(iso) { if (!isISO(iso)) return ''; var p = iso.split('-'); return p[2] + '.' + p[1] + (p[0] !== String(new Date().getFullYear()) ? '.' + p[0].slice(2) : ''); }
  function plural(n, f) { var a = Math.abs(n) % 100, b = a % 10; if (a > 10 && a < 20) return f[2]; if (b > 1 && b < 5) return f[1]; if (b === 1) return f[0]; return f[2]; }
  function dueInfo(iso) {
    if (!isISO(iso)) return null;
    var d = diffDays(iso);
    if (d < 0) return { cls: 'late', t: 'просрочено на ' + (-d) + ' дн.' };
    if (d === 0) return { cls: 'soon-d', t: 'сегодня' };
    if (d === 1) return { cls: 'soon-d', t: 'завтра' };
    if (d <= 3) return { cls: 'soon-d', t: 'через ' + d + ' дн.' };
    return { cls: '', t: 'через ' + d + ' дн.' };
  }
  function prettyName(n) {
    var s = String(n).replace(/^(Резюме|Resume|CV)_/i, '');
    var drop = [profile.last_name, profile.first_name, profile.middle_name, 'PM'].filter(Boolean);
    for (var i = 0; i < 3; i++) drop.forEach(function (w) { s = s.replace(new RegExp('^' + w + '_', 'i'), ''); });
    return s.replace(/_/g, ' ').trim() || n;
  }
  function stKey(a) { return STMAP[a.status] ? a.status : 'ready'; }

  function api(method, url, body) {
    var opt = { method: method, headers: {} };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    return fetch(url, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.detail || ('Ошибка ' + r.status));
        return d;
      });
    });
  }

  var toastTimer = null;
  function toast(msg, undo) {
    var t = $('#toast'); t.textContent = '';
    var span = document.createElement('span'); span.textContent = msg; t.appendChild(span);
    if (undo) {
      var b = document.createElement('button'); b.type = 'button'; b.textContent = 'Отменить';
      b.addEventListener('click', function () { t.hidden = true; undo(); }); t.appendChild(b);
    }
    t.hidden = false; clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 6500);
  }

  /* ---------- вкладки ---------- */
  function showTab(name) {
    document.querySelectorAll('.tabs button').forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.tab === name)); });
    document.querySelectorAll('.view').forEach(function (v) { v.hidden = v.id !== 'v-' + name; });
    try { localStorage.setItem('jh.tab', name); } catch (e) {}
    if (name === 'vac') loadVac();
    if (name === 'res') { loadResumes(); loadMaster(); }
    if (name === 'prof') loadProfile();
  }
  document.querySelectorAll('.tabs button').forEach(function (b) { b.addEventListener('click', function () { showTab(b.dataset.tab); }); });

  /* =================== ОТКЛИКИ =================== */
  function loadApps() {
    return api('GET', '/api/apps').then(function (d) { apps = d; renderApps(); });
  }
  function byId(id) { return apps.find(function (a) { return a.id === id; }); }

  function changeStatus(id, status) {
    var a = byId(id); if (!a || a.status === status) return;
    var prev = a.status;
    api('PATCH', '/api/apps/' + id, { status: status }).then(function (n) {
      Object.assign(a, n); renderApps();
      toast('Статус: ' + STMAP[status].t, function () {
        api('PATCH', '/api/apps/' + id, { status: prev }).then(function (m) { Object.assign(a, m); renderApps(); });
      });
    }).catch(function (e) { toast('Не сохранилось: ' + e.message); });
  }

  function visibleApps() {
    var arr = apps.slice(), q = query.trim().toLowerCase();
    if (filter !== 'all') arr = arr.filter(function (a) { return stKey(a) === filter; });
    if (q) arr = arr.filter(function (a) { return [a.company, a.role, a.resume, a.test, a.notes, a.next, a.track].join(' ').toLowerCase().indexOf(q) !== -1; });
    var key = function (a) { return a.applied_date || a.prep_date || ''; };
    if (sortBy === 'co') arr.sort(function (a, b) { return String(a.company).localeCompare(String(b.company), 'ru') || key(b).localeCompare(key(a)); });
    else if (sortBy === 'new') arr.sort(function (a, b) { return key(b).localeCompare(key(a)) || b.id - a.id; });
    else arr.sort(function (a, b) { return WEIGHT[stKey(a)] - WEIGHT[stKey(b)] || key(b).localeCompare(key(a)); });
    return arr;
  }

  function rowHtml(a) {
    var st = stKey(a), id = a.id, url = safeUrl(a.link);
    var link = url ? '<a class="lnk" href="' + esc(url) + '" target="_blank" rel="noopener">Вакансия ↗</a>'
      : '<button type="button" class="lnk ghost" data-act="edit" data-id="' + id + '">Добавить ссылку</button>';
    var track = a.track ? '<span class="track">' + esc(a.track) + '</span>' : '';
    var resume = '<span class="lbl m">Резюме</span>' + (a.resume
      ? (a.resume_id ? '<a class="rlink" href="/api/resumes/' + a.resume_id + '/file" target="_blank" title="' + esc(a.resume) + '">' + esc(prettyName(a.resume)) + '</a>' : '<span class="rlink">' + esc(prettyName(a.resume)) + '</span>')
      : '<span class="role">Не указано</span>');
    var tags = '';
    if (a.cover || a.cover_text) tags += '<span class="tag">сопроводительное</span>';
    if (a.test) tags += '<span class="tag" title="' + esc(a.test) + '">тестовое</span>';
    if (tags) resume += '<div class="tags">' + tags + '</div>';

    var date = a.applied_date ? '<span class="dt strong">' + fmt(a.applied_date) + '</span><span class="dt-l">подано</span>'
      : a.prep_date ? '<span class="dt">' + fmt(a.prep_date) + '</span><span class="dt-l">материалы готовы</span>'
      : '<span class="dt-l">дата не указана</span>';

    var pill = '<label class="pill"><span class="dot" aria-hidden="true"></span><select data-act="status" data-id="' + id + '" aria-label="Статус: ' + esc(a.company) + '">' +
      ST.map(function (s) { return '<option value="' + s.k + '"' + (s.k === st ? ' selected' : '') + '>' + s.t + '</option>'; }).join('') + '</select></label>';
    var quick = st === 'ready' ? (url ? '<button type="button" class="mini" data-act="go" data-id="' + id + '">Открыть и заполнить</button>' : '') +
      '<button type="button" class="mini" data-act="applied" data-id="' + id + '">Подал сегодня</button>' : '';

    var due = (st !== 'rejected' && st !== 'skipped') ? dueInfo(a.next_at) : null;
    var next = '<span class="lbl m">Дальше</span>';
    if (a.next) next += '<div class="nx">' + esc(a.next) + (a.next_at ? ' <span class="file">· ' + fmt(a.next_at) + '</span>' : '') + '</div>';
    if (due) next += '<span class="due ' + due.cls + '">' + due.t + '</span>';
    if (a.notes) next += '<p class="nt">' + esc(a.notes) + '</p>';

    return '<article class="row st-' + st + (st === 'skipped' ? ' is-skipped' : '') + '" data-open="' + id + '" tabindex="0" aria-label="' + esc(a.company + ' — ' + a.role) + '">' +
      '<div class="c-main"><div class="co">' + esc(a.company) + '</div><div class="role">' + esc(a.role) + '</div><div class="meta">' + track + link + '</div></div>' +
      '<div class="c-resume">' + resume + '</div>' +
      '<div class="c-date"><span class="lbl m">Дата</span>' + date + '</div>' +
      '<div class="c-status">' + pill + quick + '</div>' +
      '<div class="c-next">' + next + '</div></article>';
  }

  function renderApps() {
    var counts = {}, total = apps.length;
    apps.forEach(function (a) { var k = stKey(a); counts[k] = (counts[k] || 0) + 1; });
    $('#bar').innerHTML = ST.filter(function (s) { return counts[s.k]; }).map(function (s) { return '<span class="seg st-' + s.k + '" style="flex:' + counts[s.k] + ' 1 0"></span>'; }).join('');
    var chips = '<button type="button" class="chip" data-f="all" aria-pressed="' + (filter === 'all') + '"><span class="n">' + total + '</span> Все</button>';
    ST.forEach(function (s) {
      if (!counts[s.k] && filter !== s.k) return;
      chips += '<button type="button" class="chip st-' + s.k + '" data-f="' + s.k + '" aria-pressed="' + (filter === s.k) + '"><span class="dot"></span>' + s.t + ' <span class="n">' + (counts[s.k] || 0) + '</span></button>';
    });
    $('#chips').innerHTML = chips;
    var active = 0; ST.forEach(function (s) { if (ACTIVE[s.k]) active += counts[s.k] || 0; });
    $('#lede').textContent = total + ' ' + plural(total, ['отклик', 'отклика', 'откликов']) + '. В процессе ' + active +
      ', ждут подачи ' + (counts.ready || 0) + ', отказов ' + (counts.rejected || 0) + (counts.offer ? ', офферов ' + counts.offer : '') + '.';
    $('#n-apps').textContent = total || '';

    var soon = apps.filter(function (a) { var s = stKey(a); return isISO(a.next_at) && s !== 'rejected' && s !== 'skipped' && s !== 'offer'; })
      .sort(function (a, b) { return a.next_at.localeCompare(b.next_at); }).slice(0, 4);
    $('#soon').hidden = !soon.length;
    $('#soon-list').innerHTML = soon.map(function (a) {
      var d = dueInfo(a.next_at);
      return '<li><span class="when">' + fmt(a.next_at) + '</span><strong>' + esc(a.company) + '</strong><span>' + esc(a.next || a.role) + '</span>' + (d ? '<span class="due ' + d.cls + '">' + d.t + '</span>' : '') + '</li>';
    }).join('');

    var arr = visibleApps();
    $('#list').innerHTML = !total ? '<div class="state"><strong>Откликов пока нет</strong>Нажмите «Добавить отклик» или откликнитесь на вакансию из вкладки «Куда откликнуться».</div>'
      : arr.length ? arr.map(rowHtml).join('') : '<div class="state"><strong>Ничего не найдено</strong>Сбросьте фильтр по статусу или очистите поиск.</div>';
  }

  $('#chips').addEventListener('click', function (e) {
    var b = e.target.closest('[data-f]'); if (!b) return;
    filter = b.dataset.f === filter && filter !== 'all' ? 'all' : b.dataset.f;
    try { localStorage.setItem('jh.filter', filter); } catch (x) {}
    renderApps();
  });
  $('#q').addEventListener('input', function (e) { query = e.target.value; renderApps(); });
  $('#sort').addEventListener('change', function (e) { sortBy = e.target.value; try { localStorage.setItem('jh.sort', sortBy); } catch (x) {} renderApps(); });
  $('#list').addEventListener('change', function (e) { var s = e.target.closest('select[data-act="status"]'); if (s) changeStatus(+s.dataset.id, s.value); });
  $('#list').addEventListener('click', function (e) {
    var b = e.target.closest('button[data-act]');
    if (!b) {  // клик по строке (не по ссылке, статусу или кнопке) открывает отклик
      var row = e.target.closest('[data-open]');
      if (row && !e.target.closest('a, select, label, button')) openEditor(+row.dataset.open);
      return;
    }
    var id = +b.dataset.id;
    if (b.dataset.act === 'edit') openEditor(id);
    if (b.dataset.act === 'applied') changeStatus(id, 'applied');
    if (b.dataset.act === 'go') goApply(byId(id));
  });
  $('#list').addEventListener('keydown', function (e) {
    var row = e.target.closest('[data-open]');
    if (row && e.target === row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openEditor(+row.dataset.open); }
  });
  $('#add').addEventListener('click', function () { openEditor(null); });

  /* ---------- отклик в один клик ---------- */
  function goApply(a) {
    if (!a || !safeUrl(a.link)) { toast('У отклика нет ссылки на вакансию'); return; }
    // расширение узнает отклик по адресу страницы и заполнит форму
    window.open(a.link, '_blank', 'noopener');
    toast('Открыл вакансию. Расширение заполнит форму — проверьте и отправьте.');
  }

  /* ---------- панель отклика ---------- */
  var F = function (n) { return $('#f-' + n); };
  F('status').innerHTML = ST.map(function (s) { return '<option value="' + s.k + '">' + s.t + '</option>'; }).join('');
  var FIELDS = ['company', 'role', 'track', 'status', 'link', 'cover_text', 'test', 'prep_date', 'applied_date', 'next', 'next_at', 'notes'];

  function fillResumeSelect(sel) {
    var opts = '<option value="">Не указано</option>';
    var groups = { base: 'Основные', tailored: 'Адаптированные' };
    Object.keys(groups).forEach(function (k) {
      var list = resumes.filter(function (r) { return r.kind === k; });
      if (list.length) opts += '<optgroup label="' + groups[k] + '">' + list.map(function (r) { return '<option value="' + r.id + '">' + esc(k === 'tailored' ? prettyName(r.name) : r.name) + (r.is_main ? ' — основное' : '') + '</option>'; }).join('') + '</optgroup>';
    });
    F('resume').innerHTML = opts;
    F('resume').value = sel || '';
  }

  function openEditor(id) {
    var a = id ? byId(id) : { status: 'ready', track: 'Работа', prep_date: todayISO() };
    editing = id ? a : null;
    FIELDS.forEach(function (k) { F(k).value = a[k] || ''; });
    F('status').value = stKey(a);
    var ensure = resumes.length ? Promise.resolve() : loadResumesData();
    ensure.then(function () {
      var main = resumes.find(function (r) { return r.is_main; });
      fillResumeSelect(a.resume_id || (!id && main ? main.id : ''));
    });
    $('#f-hist').textContent = (a.history && a.history.length) ? a.history.map(function (l) { return (STMAP[l.s] ? STMAP[l.s].t : l.s) + ' ' + fmt(l.at); }).join(' → ') : 'Пока пусто';
    $('#form-err').textContent = ''; $('#tailor-st').textContent = '';
    delArmed = false; $('#del').textContent = 'Удалить'; $('#del').hidden = !id;
    $('#go').hidden = !(id && safeUrl(a.link));
    $('#tailor').disabled = $('#cover-ai').disabled = !id;
    $('#tailor').title = id ? '' : 'Сначала сохраните отклик';
    $('#sheet-title').textContent = id ? a.company + ' — отклик' : 'Новый отклик';
    $('#scrim').hidden = false; $('#sheet').hidden = false;
    F('company').focus();
  }
  function closeEditor() { if (!$('#vsheet').hidden) { closeVacancy(); } $('#sheet').hidden = true; $('#rsheet').hidden = true; $('#scrim').hidden = true; editing = null; }

  function formData() {
    var d = {}; FIELDS.forEach(function (k) { d[k] = F(k).value.trim(); });
    var rid = F('resume').value;
    var r = resumes.find(function (x) { return String(x.id) === rid; });
    d.resume_id = rid ? +rid : null; d.resume = r ? r.name : '';
    d.cover = d.cover_text ? 1 : 0;
    return d;
  }

  function saveEditor(keepOpen) {
    var d = formData();
    if (!d.company) { $('#form-err').textContent = 'Укажите компанию.'; F('company').focus(); return Promise.reject(); }
    if (!d.role) { $('#form-err').textContent = 'Укажите вакансию.'; F('role').focus(); return Promise.reject(); }
    if (d.link && !safeUrl(d.link)) { $('#form-err').textContent = 'Ссылка должна начинаться с http:// или https://'; return Promise.reject(); }
    var p = editing ? api('PATCH', '/api/apps/' + editing.id, d) : api('POST', '/api/apps', d);
    return p.then(function (n) {
      var ex = byId(n.id); if (ex) Object.assign(ex, n); else apps.unshift(n);
      renderApps();
      if (!keepOpen) { closeEditor(); toast(editing ? 'Сохранено' : 'Отклик добавлен'); }
      return n;
    }).catch(function (e) { if (e) $('#form-err').textContent = e.message; throw e; });
  }
  $('#form').addEventListener('submit', function (e) { e.preventDefault(); saveEditor(false).catch(function () {}); });
  $('#cancel').addEventListener('click', closeEditor);
  $('#scrim').addEventListener('click', closeEditor);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeEditor(); });
  $('#del').addEventListener('click', function () {
    if (!editing) return;
    if (!delArmed) { delArmed = true; $('#del').textContent = 'Точно удалить? Нажмите ещё раз'; return; }
    var a = editing;
    api('DELETE', '/api/apps/' + a.id).then(function () {
      apps = apps.filter(function (x) { return x.id !== a.id; }); renderApps(); closeEditor();
      toast('Отклик удалён', function () { api('POST', '/api/apps', a).then(loadApps); });
    });
  });
  $('#go').addEventListener('click', function () { saveEditor(true).then(function (n) { goApply(n); }).catch(function () {}); });
  $('#open-resume').addEventListener('click', function () { var r = F('resume').value; if (r) window.open('/api/resumes/' + r + '/file', '_blank'); });
  $('#cover-copy').addEventListener('click', function () { navigator.clipboard.writeText(F('cover_text').value).then(function () { toast('Письмо скопировано'); }); });
  $('#cover-tpl').addEventListener('click', function () {
    var t = (profile.cover_template || '').replace(/\{company\}/g, F('company').value).replace(/\{role\}/g, F('role').value)
      .replace(/\{name\}/g, [profile.first_name, profile.last_name].filter(Boolean).join(' '));
    F('cover_text').value = t;
  });

  function openFromApp() {
    if (!editing) return;
    saveEditor(true).then(function (n) {
      return api('POST', '/api/apps/' + n.id + '/vacancy');
    }).then(function (r) { closeEditor(); openVacancy(r.vacancy_id); })
      .catch(function (e) { $('#tailor-st').textContent = (e && e.message) || 'Не получилось открыть вакансию'; });
  }
  $('#tailor').addEventListener('click', openFromApp);
  $('#cover-ai').addEventListener('click', openFromApp);

  /* =================== ВАКАНСИИ =================== */
  var vacTimer = null, pollTimer = null, vacShown = 120;
  function vacParams() {
    return '?state=' + $('#vstate').value + '&q=' + encodeURIComponent($('#vq').value.trim()) +
      '&watch=' + ($('#vwatch').checked ? 1 : 0) + '&company=' + encodeURIComponent($('#vcompany').value);
  }
  function loadVacStats() {
    return api('GET', '/api/vacancies/stats').then(function (s) {
      var n = (s.states && s.states.new) || 0;
      $('#n-vac').textContent = n || '';
      var sel = $('#vcompany'), cur = sel.value;
      sel.innerHTML = '<option value="">Все компании</option>' + (s.companies || []).map(function (x) { return '<option value="' + esc(x.brand) + '">' + esc(x.brand) + ' · ' + x.n + '</option>'; }).join('');
      sel.value = cur;
      var lr = s.last_run;
      $('#run-st').textContent = s.running ? 'Бот обходит сайты…' : lr ? 'Последний обход ' + lr.finished_at.slice(8, 10) + '.' + lr.finished_at.slice(5, 7) + ' в ' + lr.finished_at.slice(11, 16) + ' · новых ' + lr.new : 'Бот ещё не запускался';
      $('#crawl').disabled = s.running;
      if (s.running && !pollTimer) pollTimer = setInterval(function () {
        loadVacStats().then(function (x) { if (!x.running) { clearInterval(pollTimer); pollTimer = null; loadVac(); toast('Бот закончил обход'); } });
      }, 5000);
      return s;
    });
  }
  function loadVac() {
    loadVacStats(); refreshQueue();
    return api('GET', '/api/vacancies' + vacParams()).then(renderVac);
  }
  function ago(iso) {
    var d = diffDays(iso.slice(0, 10));
    return d === 0 ? 'Сегодня' : d === -1 ? 'Вчера' : fmt(iso.slice(0, 10));
  }
  var picked = {}, lastVac = [];
  function renderVac(list) {
    lastVac = list;
    var box = $('#vlist');
    if (!list.length) { box.innerHTML = '<div class="state"><strong>Пусто</strong>Новых вакансий нет. Нажмите «Обновить сейчас» или смените фильтр.</div>'; return; }
    var html = '', last = '';
    list.slice(0, vacShown).forEach(function (v) {
      var g = ago(v.found_at);
      if (g !== last) { html += '<div class="vgroup">Найдено: ' + g + '</div>'; last = g; }
      var applied = v.state === 'applied', saved = v.state === 'saved';
      var brandName = v.brand || v.company;
      // подразделение без повтора бренда: «ОТП Банк. Бизнес» → «Бизнес»
      var rest = String(v.company || '').replace(/[«»"]/g, '');
      if (rest.toLowerCase().indexOf(brandName.toLowerCase()) === 0) rest = rest.slice(brandName.length).replace(/^[\s.,:;–—-]+/, '');
      var unit = rest && v.company !== brandName ? '<span class="unit">' + esc(rest) + '</span>' : '';
      var src = v.source === 'hh.ru' ? '<span class="src">через hh.ru</span>' : (v.source && v.source !== 'трекер' ? '<span class="src">сайт компании</span>' : '');
      var meta = v.meta && v.source === 'hh.ru' ? '<span>' + esc(v.meta.split(' · ').slice(0, 3).join(' · ')) + '</span>' : '';
      var fit = v.score >= 35 ? '<span class="fit">подходит</span>' : '';
      var mine = v.watch && v.source === 'hh.ru' ? '<span class="mine">ваш список</span>' : '';
      var pick = applied ? '<span class="vpick"></span>' : '<label class="vpick" title="Выбрать для массового отклика"><input type="checkbox" data-pick="' + v.id + '"' + (picked[v.id] ? ' checked' : '') + '></label>';
      html += '<div class="vrow' + (picked[v.id] ? ' is-picked' : '') + '" data-id="' + v.id + '" tabindex="-1">' + pick +
        '<div class="vtext"><div class="vt"><a href="' + esc(safeUrl(v.url)) + '" target="_blank" rel="noopener">' + esc(v.title) + '</a>' + fit + '</div>' +
        '<div class="vm"><span class="vc" title="' + esc(v.company) + '">' + esc(brandName) + '</span>' + unit + meta + src + mine + '</div></div>' +
        '<div class="va">' +
        (applied ? '<span class="tag">отклик есть</span>' :
          '<button class="mini ghost vhide" data-v="' + (v.state === 'hidden' ? 'unhide' : 'hide') + '">' + (v.state === 'hidden' ? 'Вернуть' : 'Скрыть') + '</button>' +
          '<button class="btn vgo" data-v="open">' + (saved ? 'Открыть отклик' : 'Подготовить отклик') + '</button>') +
        '</div></div>';
    });
    if (list.length > vacShown) html += '<button class="btn more" id="vmore">Показать ещё ' + Math.min(120, list.length - vacShown) + ' из ' + (list.length - vacShown) + '</button>';
    box.innerHTML = html;
    renderBulk();
  }

  /* ---------- массовый отклик ---------- */
  function pickedIds() { return Object.keys(picked).filter(function (k) { return picked[k]; }).map(Number); }
  function renderBulk() {
    var n = pickedIds().length;
    $('#bulk').hidden = n === 0;
    $('#bulk-n').textContent = n + ' ' + plural(n, ['вакансия', 'вакансии', 'вакансий']);
  }
  function refreshQueue() {
    return api('GET', '/api/queue').then(function (q) {
      var n = q.items.length, t = q.tailoring;
      $('#queue-go').hidden = n === 0;
      $('#queue-go').textContent = 'Продолжить очередь · ' + n;
      $('#queue-clear').hidden = n === 0;
      $('#queue-st').textContent = t.running ? 'ИИ пишет письма: ' + t.done + ' из ' + t.total : '';
      if (t.running) setTimeout(refreshQueue, 5000);
      return q;
    });
  }
  $('#vlist').addEventListener('change', function (e) {
    var c = e.target.closest('[data-pick]'); if (!c) return;
    picked[c.dataset.pick] = c.checked;
    c.closest('.vrow').classList.toggle('is-picked', c.checked);
    renderBulk();
  });
  $('#pick-fit').addEventListener('click', function () {
    lastVac.forEach(function (v) { if (v.state !== 'applied' && v.score >= 30) picked[v.id] = true; });
    renderVac(lastVac);
  });
  $('#pick-none').addEventListener('click', function () { picked = {}; renderVac(lastVac); });
  function openQueue(first) {
    if (!first) { toast('Очередь пуста'); return; }
    window.open(first.link, '_blank');
    toast('Открыл первую вакансию. В плашке расширения жмите «Отправить и дальше».');
  }
  $('#bulk-go').addEventListener('click', function () {
    var ids = pickedIds(); if (!ids.length) return;
    var tailor = $('#bulk-ai').checked;
    var win = tailor ? null : window.open('about:blank', '_blank');
    api('POST', '/api/vacancies/bulk-apply', { ids: ids, tailor: tailor }).then(function (r) {
      picked = {}; loadApps(); loadVac(); refreshQueue();
      if (!r.queued) { if (win) win.close(); toast('Эти вакансии уже в работе'); return; }
      if (tailor) { toast('В очереди ' + r.queued + '. ИИ пишет письма — откройте очередь, когда закончит.'); return; }
      if (win && r.first) { win.location = r.first.link; toast('В очереди ' + r.queued + '. Жмите «Отправить и дальше» в плашке расширения.'); }
    }).catch(function (e) { if (win) win.close(); toast(e.message); });
  });
  $('#queue-go').addEventListener('click', function () {
    api('GET', '/api/queue/next').then(function (r) { openQueue(r.next); });
  });
  $('#queue-clear').addEventListener('click', function () {
    api('DELETE', '/api/queue').then(function () { refreshQueue(); toast('Очередь очищена, отклики остались в трекере'); });
  });

  $('#vlist').addEventListener('click', function (e) {
    if (e.target.id === 'vmore') { vacShown += 120; loadVac(); return; }
    var b = e.target.closest('[data-v]');
    if (!b) {  // клик по тексту строки — карточка вакансии (ссылка на сайт остаётся ссылкой)
      var r0 = e.target.closest('.vrow');
      if (r0 && !e.target.closest('a, input, label')) openVacancy(+r0.dataset.id);
      return;
    }
    var row = b.closest('.vrow'), id = +row.dataset.id, act = b.dataset.v;
    if (act === 'open') { openVacancy(id); return; }
    if (act === 'hide' || act === 'unhide') {
      api('PATCH', '/api/vacancies/' + id, { state: act === 'hide' ? 'hidden' : 'new' }).then(function () {
        row.remove(); loadVacStats();
        if (act === 'hide') toast('Скрыто', function () { api('PATCH', '/api/vacancies/' + id, { state: 'new' }).then(loadVac); });
      });
      return;
    }
    var win = act === 'apply' ? window.open('about:blank', '_blank') : null; // открыть вкладку сразу, пока клик «свежий»
    api('POST', '/api/vacancies/' + id + '/apply').then(function (a) {
      var ex = byId(a.id); if (ex) Object.assign(ex, a); else apps.unshift(a);
      renderApps();
      if (act === 'apply') {
        if (win) win.location = a.link; else window.open(a.link, '_blank');
        toast('Отклик заведён. Расширение заполнит форму — проверьте и отправьте.');
        loadVac();
      } else {
        loadVac(); showTab('apps'); openEditor(a.id);
        setTimeout(function () { $('#tailor').click(); }, 300);
      }
    }).catch(function (err) { if (win) win.close(); toast(err.message); });
  });
  ['#vstate', '#vcompany', '#vwatch'].forEach(function (s) { $(s).addEventListener('change', function () { vacShown = 120; loadVac(); }); });
  $('#vq').addEventListener('input', function () { clearTimeout(vacTimer); vacTimer = setTimeout(loadVac, 250); });
  $('#crawl').addEventListener('click', function () {
    api('POST', '/api/crawl', {}).then(function () { toast('Бот пошёл по сайтам — это 2–3 минуты'); loadVacStats(); });
  });


  /* =================== КАРТОЧКА ВАКАНСИИ =================== */
  var vs = { id: null, data: null, draft: null, tab: 'resume' };

  function fitPaper(box) {
    var fr = box.querySelector('iframe'); if (!fr) return;
    fr.style.transform = 'scale(' + (box.clientWidth / 816) + ')';
  }
  var paperRO = new ResizeObserver(function (es) { es.forEach(function (e) { fitPaper(e.target); }); });
  paperRO.observe($('#vs-paper')); paperRO.observe($('#m-paper'));

  // Текст вакансии → абзацы, подзаголовки и списки
  var HEAD = /^(требования|задачи|обязанности|условия|ключевые навыки|будет плюсом|плюсом будет|бонусы|преимущества|описание|ожидания|наши ожидания|требуемый опыт|занятость|о (компании|команде|проекте|нас|вакансии)|(что|чем) (нужно|предстоит|мы ждём|мы ждем|мы предлагаем|важно|заниматься|мы ожидаем)|(мы|что мы) (предлагаем|ожидаем|ждём|ждем|ищем)|какие задачи|кого мы ищем|почему мы|(тебе|вам) предстоит|(ваши|твои|наши) (задачи|ожидания)|с чем (предстоит|придётся|придется) работать|наш стек|стек)/i;
  function formatDesc(text) {
    var lines = String(text || '').split('\n').map(function (s) { return s.trim(); });
    var out = [], list = [];
    function flush() { if (list.length) { out.push('<ul>' + list.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>'); list = []; } }
    for (var i = 0; i < lines.length; i++) {
      var s = lines[i], next = lines[i + 1] || '';
      if (!s) { flush(); continue; }
      if (/^[•·\-–—*▪●]\s*/.test(s)) { list.push(s.replace(/^[•·\-–—*▪●]\s*/, '')); continue; }
      if ((s.length < 80 && /:$/.test(s)) || (s.length < 60 && HEAD.test(s))) { flush(); out.push('<h4>' + esc(s.replace(/:$/, '')) + '</h4>'); continue; }
      // короткие строки подряд без точки — это пункты списка (так их отдают многие сайты)
      if (s.length < 160 && !/[.!?]$/.test(s) && (list.length || (next && !HEAD.test(next) && next.length < 160))) { list.push(s); continue; }
      flush(); out.push('<p>' + esc(s) + '</p>');
    }
    flush();
    return out.join('');
  }

  // меню сайта и хлебные крошки над заголовком вакансии не нужны: режем всё до строки с названием
  function trimDesc(text, title) {
    var lines = String(text).split('\n'), t = String(title || '').trim().toLowerCase();
    for (var i = 0; i < Math.min(lines.length, 25); i++) {
      if (lines[i].trim().toLowerCase() === t) return lines.slice(i + 1).join('\n').replace(/^\s+/, '');
    }
    return text;
  }

  function openVacancy(id) {
    vs = { id: id, data: null, draft: null, tab: vs.tab || 'resume' };
    $('#vs-title').textContent = 'Загружаю…'; $('#vs-meta').textContent = ''; $('#vs-text').innerHTML = '';
    showStep(null);
    $('#scrim').hidden = false; $('#vsheet').hidden = false;
    busy(true, 'Читаю вакансию…');
    reloadVacancy().catch(function (e) { busy(false); $('#vs-title').textContent = 'Ошибка'; $('#vs-text').textContent = e.message; });
  }
  function reloadVacancy(keepDraftId) {
    return api('GET', '/api/vacancies/' + vs.id + '/detail').then(function (d) {
      busy(false);
      vs.data = d;
      var v = d.vacancy;
      $('#vs-title').textContent = v.title;
      $('#vs-meta').innerHTML = '<b>' + esc(v.brand || v.company) + '</b>' + (v.brand && v.company !== v.brand ? ' · ' + esc(v.company) : '') + (v.meta && v.source === 'hh.ru' ? ' · ' + esc(v.meta.split(' · ').slice(0, 4).join(' · ')) : '');
      $('#vs-open').href = safeUrl(v.url) || '#';
      $('#vs-text').innerHTML = v.description ? formatDesc(trimDesc(v.description, v.title))
        : '<p class="hint">Не удалось прочитать текст вакансии' + (v.fetch_error ? ': ' + esc(v.fetch_error) : '') + '. Откройте её на сайте.</p>';
      renderBanner();
      if (d.drafts.length) {
        var keep = keepDraftId && d.drafts.find(function (x) { return x.id === keepDraftId; });
        showDraft(keep || d.drafts.find(function (x) { return x.approved; }) || d.drafts[0]);
      } else if (v.analysis && v.analysis.length) { renderQA(); showStep('qa'); }
      else { showStep('empty'); $('#vs-analyze').disabled = !v.description; }
    });
  }
  function showStep(s) {
    $('#vs-empty').hidden = s !== 'empty'; $('#vs-qa').hidden = s !== 'qa'; $('#vs-draft').hidden = s !== 'draft';
  }
  function closeVacancy() { $('#vsheet').hidden = true; $('#scrim').hidden = true; vs.id = null; if (!$('#v-vac').hidden) loadVac(); }
  $('#vs-close').addEventListener('click', closeVacancy);

  // отклик уже отмечен «Подано» (в том числе по ошибке) — показываем и даём вернуть
  function renderBanner() {
    var app = vs.data && vs.data.app;
    var sent = app && app.status !== 'ready';
    $('#vs-banner').hidden = !sent;
    if (sent) {
      $('#vs-banner-t').textContent = 'Отклик отмечен как «' + (STMAP[app.status] ? STMAP[app.status].t : app.status) + '»' +
        (app.applied_date ? ' ' + fmt(app.applied_date) : '') + '. Если вы его ещё не отправляли — верните, и можно будет откликнуться с новой версией.';
    }
  }
  $('#vs-reopen').addEventListener('click', function () {
    api('POST', '/api/apps/' + vs.data.app.id + '/reopen').then(function (a) {
      vs.data.app = a; var ex = byId(a.id); if (ex) Object.assign(ex, a);
      renderApps(); renderBanner(); if (vs.draft) showDraft(vs.draft);
      toast('Отклик снова «Готово к подаче»');
    });
  });

  var busyTimer = null;
  function busy(on, text) {
    clearInterval(busyTimer);
    $('#vs-busy').hidden = !on;
    if (!on) return;
    var t0 = Date.now();
    var tick = function () { var s = Math.round((Date.now() - t0) / 1000); $('#vs-busy-t').textContent = text + (s > 2 ? ' ' + Math.floor(s / 60) + ':' + pad(s % 60) : ''); };
    tick(); busyTimer = setInterval(tick, 1000);
  }

  /* --- шаг 1: требования и уточнения --- */
  var LV = { strong: 'есть', partial: 'частично', gap: 'нет в резюме' };
  $('#vs-analyze').addEventListener('click', function () {
    busy(true, 'Разбираю требования вакансии…');
    api('POST', '/api/vacancies/' + vs.id + '/analyze').then(function (r) {
      busy(false);
      vs.data.vacancy.analysis = r.requirements; vs.data.vacancy.answers = r.answers || {};
      renderQA(); showStep('qa');
    }).catch(function (e) { busy(false); toast('Не получилось: ' + e.message); });
  });
  function renderQA() {
    var v = vs.data.vacancy, ans = v.answers || {};
    var reqs = v.analysis || [];
    var ask = reqs.filter(function (r) { return r.level !== 'strong'; });
    var ok = reqs.filter(function (r) { return r.level === 'strong'; });
    var html = ask.map(function (r, i) {
      var k = r.req, a = ans[k] || {};
      var lv = LV[r.level] ? r.level : 'partial';
      return '<li data-k="' + esc(k) + '"><div class="qh"><span class="lv ' + lv + '">' + LV[lv] + '</span><span class="rq">' + esc(r.req) + '</span></div>' +
        (r.evidence ? '<div class="ev">В резюме: ' + esc(r.evidence) + '</div>' : '') +
        '<div class="q">' + esc(r.question || 'Есть ли у вас такой опыт?') + '</div>' +
        '<div class="yn"><button type="button" class="yes" aria-pressed="' + (a.has === true) + '">Да, есть</button><button type="button" class="no" aria-pressed="' + (a.has === false) + '">Нет</button></div>' +
        '<textarea class="field-in" rows="2" placeholder="Коротко: что знаете или делали, где и с каким результатом"' + (a.has ? '' : ' hidden') + '>' + esc(a.text || '') + '</textarea></li>';
    }).join('');
    if (ok.length) html += '<li><details><summary>Уже есть в резюме: ' + ok.length + '</summary><div class="ev">' +
      ok.map(function (r) { return '• ' + esc(r.req) + (r.evidence ? ' — ' + esc(r.evidence) : ''); }).join('<br>') + '</div></details></li>';
    $('#vs-qa-list').innerHTML = html || '<li>Все требования закрыты резюме — уточнять нечего.</li>';
    $('#vs-qa-back').hidden = !vs.data.drafts.length;
  }
  $('#vs-qa-list').addEventListener('click', function (e) {
    var b = e.target.closest('.yn button'); if (!b) return;
    var li = b.closest('li');
    li.querySelectorAll('.yn button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
    var ta = li.querySelector('textarea'); ta.hidden = !b.classList.contains('yes');
    if (!ta.hidden) ta.focus();
  });
  function collectAnswers() {
    var out = {};
    document.querySelectorAll('#vs-qa-list li[data-k]').forEach(function (li) {
      var yes = li.querySelector('.yes').getAttribute('aria-pressed') === 'true';
      var no = li.querySelector('.no').getAttribute('aria-pressed') === 'true';
      if (yes || no) out[li.dataset.k] = { req: li.dataset.k, has: yes, text: yes ? li.querySelector('textarea').value.trim() : '' };
    });
    return out;
  }
  $('#vs-qa-back').addEventListener('click', function () { if (vs.draft) showDraft(vs.draft); });
  $('#vs-to-qa').addEventListener('click', function () {
    if (vs.data.vacancy.analysis && vs.data.vacancy.analysis.length) { renderQA(); showStep('qa'); }
    else $('#vs-analyze').click();
  });

  /* --- шаг 2: версии резюме и письма --- */
  function counter(el, out) { out.textContent = el.value.length + ' зн.'; }
  ['letter', 'short'].forEach(function (k) {
    var el = $('#vs-' + k), out = $(k === 'letter' ? '#vs-lc' : '#vs-sc');
    el.addEventListener('input', function () { counter(el, out); });
  });

  function showDraft(dr) {
    vs.draft = dr;
    showStep('draft');
    var drafts = vs.data.drafts;
    $('#vs-ver').innerHTML = drafts.map(function (x) {
      return '<option value="' + x.id + '"' + (x.id === dr.id ? ' selected' : '') + '>Версия ' + x.version + (x.approved ? ' ✓ выбрана' : '') +
        (x.request ? ' · «' + esc(x.request.slice(0, 40)) + (x.request.length > 40 ? '…' : '') + '»' : '') + '</option>';
    }).join('');
    $('#vs-changes').textContent = dr.changes ? 'Что подстроено: ' + dr.changes : '';
    $('#vs-frame').src = '/api/drafts/' + dr.id + '/html';
    $('#vs-frame').onload = function () { fitPaper($('#vs-paper')); };
    $('#vs-letter').value = dr.letter || ''; $('#vs-short').value = dr.letter_short || '';
    counter($('#vs-letter'), $('#vs-lc')); counter($('#vs-short'), $('#vs-sc'));
    $('#vs-match').innerHTML = (dr.match || []).map(function (m) {
      var lv = LV[m.level] ? m.level : 'partial';
      return '<li><span class="lv ' + lv + '">' + LV[lv] + '</span><span class="rq">' + esc(m.req) + '</span><span class="ev">' + esc(m.evidence) + '</span></li>';
    }).join('') || '<li>Разбор требований не получен.</li>';
    renderChosen();
    setTab(vs.tab);
  }
  // что сейчас уйдёт в отклик
  function renderChosen() {
    var chosen = vs.data.drafts.find(function (x) { return x.approved; });
    var app = vs.data.app, sent = app && app.status !== 'ready';
    var st = $('#vs-st');
    st.className = 'vs-chosen' + (chosen ? ' ok' : '');
    if (sent) st.textContent = 'Отклик уже отправлен — новая версия в него не попадёт.';
    else if (chosen && chosen.id === vs.draft.id) st.textContent = '✓ Для отклика выбрана эта версия: её PDF и письмо подставит расширение.';
    else if (chosen) st.textContent = 'Для отклика выбрана версия ' + chosen.version + '. Эта пока не выбрана.';
    else st.textContent = 'Версия для отклика ещё не выбрана.';
    $('#vs-keep').hidden = sent || (chosen && chosen.id === vs.draft.id);
    $('#vs-apply').disabled = !!sent;
  }
  function setTab(tab) {
    vs.tab = tab;
    document.querySelectorAll('#vs-tabs button').forEach(function (b) { b.setAttribute('aria-selected', String(b.dataset.t === tab)); });
    document.querySelectorAll('.vs-pane').forEach(function (p) { p.hidden = p.dataset.p !== tab; });
    if (tab === 'resume') fitPaper($('#vs-paper'));
  }
  $('#vs-tabs').addEventListener('click', function (e) { var b = e.target.closest('[data-t]'); if (b) setTab(b.dataset.t); });
  $('#vs-ver').addEventListener('change', function () {
    var id = +this.value; showDraft(vs.data.drafts.find(function (x) { return x.id === id; }));
  });

  function makeDraft(body, label) {
    busy(true, label);
    return api('POST', '/api/vacancies/' + vs.id + '/draft', body).then(function (dr) {
      busy(false);
      if (body.answers) vs.data.vacancy.answers = body.answers;
      vs.data.drafts.unshift(dr); vs.tab = 'resume'; showDraft(dr);
      $('#vs-req').value = '';
    }).catch(function (e) { busy(false); toast('Не получилось: ' + e.message); });
  }
  $('#vs-make').addEventListener('click', function () {
    makeDraft({ request: $('#vs-wish').value.trim(), answers: collectAnswers() }, 'Пишу резюме и письмо под вакансию — обычно до минуты…');
  });
  $('#vs-redo').addEventListener('click', function () {
    var req = $('#vs-req').value.trim();
    if (!req) { $('#vs-req').focus(); toast('Напишите, что изменить'); return; }
    makeDraft({ request: req, from_id: vs.draft.id }, 'Переделываю по вашей просьбе…');
  });
  $('#vs-req').addEventListener('keydown', function (e) { if (e.key === 'Enter') $('#vs-redo').click(); });
  $('#vs-save-l').addEventListener('click', function () {
    api('PATCH', '/api/drafts/' + vs.draft.id, { letter: $('#vs-letter').value, letter_short: $('#vs-short').value }).then(function (dr) {
      Object.assign(vs.draft, dr); toast('Текст письма сохранён');
    });
  });

  /* --- шаг 3: выбрать версию и откликнуться --- */
  function approve(open) {
    var win = open ? window.open('about:blank', '_blank') : null;
    var st = $('#vs-st'); st.className = 'vs-chosen'; st.textContent = 'Собираю PDF…';
    var id = vs.draft.id;
    api('PATCH', '/api/drafts/' + id, { letter: $('#vs-letter').value, letter_short: $('#vs-short').value })
      .then(function () { return api('POST', '/api/drafts/' + id + '/approve'); })
      .then(function (a) {
        var ex = byId(a.id); if (ex) Object.assign(ex, a); else apps.unshift(a);
        renderApps(); loadResumesData();
        if (win) { win.location = a.link; toast('Открыл вакансию. Расширение заполнит форму выбранным резюме и письмом.'); }
        else toast('Версия ' + vs.draft.version + ' выбрана для отклика');
        return reloadVacancy(id);
      }).catch(function (e) { if (win) win.close(); renderChosen(); toast(e.message); });
  }
  $('#vs-apply').addEventListener('click', function () { approve(true); });
  $('#vs-keep').addEventListener('click', function () { approve(false); });

  /* ---------- мастер-резюме ---------- */
  function loadMaster() {
    return api('GET', '/api/master').then(function (r) {
      var has = !!r.master;
      $('#m-build').textContent = has ? 'Пересобрать из основного резюме' : 'Собрать из основного резюме';
      $('#m-pdf').hidden = !has; $('#m-edit').disabled = !has;
      $('#m-frame').src = '/api/master/html?ts=' + Date.now();
      $('#m-frame').onload = function () { fitPaper($('#m-paper')); syncMasterHeight(); };
    });
  }
  function syncMasterHeight() {
    // мастер может быть длиннее страницы: тянем лист по высоте содержимого
    try {
      var fr = $('#m-frame'), h = fr.contentDocument.documentElement.scrollHeight;
      fr.style.height = h + 'px';
      $('#m-paper').style.aspectRatio = '816 / ' + Math.max(1056, h);
    } catch (e) {}
  }
  function masterAction(btn, url, body, label) {
    btn.classList.add('busy'); $('#m-st').textContent = label;
    api('POST', url, body).then(function () { $('#m-st').textContent = 'Готово.'; $('#m-req').value = ''; loadMaster(); })
      .catch(function (e) { $('#m-st').textContent = 'Не получилось: ' + e.message; })
      .finally(function () { btn.classList.remove('busy'); });
  }
  $('#m-build').addEventListener('click', function () {
    if (this.textContent.indexOf('Пересобрать') === 0 && !confirm('Пересобрать мастер-резюме из основного файла? Правки, сделанные запросами, пропадут.')) return;
    masterAction(this, '/api/master/build', {}, 'Собираю из основного резюме — секунд 20…');
  });
  $('#m-edit').addEventListener('click', function () {
    var req = $('#m-req').value.trim(); if (!req) { $('#m-req').focus(); return; }
    masterAction(this, '/api/master/edit', { request: req }, 'Вношу правку…');
  });
  $('#m-pdf').addEventListener('click', function () {
    var b = this; b.classList.add('busy');
    api('POST', '/api/master/pdf').then(function () { toast('PDF готов и стал основным резюме'); loadResumes(); })
      .catch(function (e) { toast(e.message); }).finally(function () { b.classList.remove('busy'); });
  });

  /* =================== РЕЗЮМЕ =================== */
  function loadResumesData() { return api('GET', '/api/resumes').then(function (d) { resumes = d; $('#n-res').textContent = d.length || ''; }); }
  function loadResumes() { return loadResumesData().then(renderResumes); }
  function renderResumes() {
    var html = '';
    [['base', 'Основные'], ['tailored', 'Адаптированные под вакансии']].forEach(function (g) {
      var list = resumes.filter(function (r) { return r.kind === g[0]; });
      if (!list.length) return;
      html += '<div class="rsec">' + g[1] + ' · ' + list.length + '</div>';
      html += list.map(function (r) {
        return '<button class="rcard' + (r.is_main ? ' main' : '') + '" data-r="' + r.id + '"><span class="rn">' + esc(r.kind === 'tailored' ? prettyName(r.name) : r.name) + '</span>' +
          '<span class="rm">' + (r.is_main ? '<span class="main-tag">основное</span> · ' : '') + (r.used ? 'в ' + r.used + ' ' + plural(r.used, ['отклике', 'откликах', 'откликах']) + ' · ' : '') + fmt(r.created_at.slice(0, 10)) + '</span></button>';
      }).join('');
    });
    $('#rlist').innerHTML = html || '<div class="state"><strong>Резюме нет</strong>Загрузите PDF или DOCX.</div>';
  }
  var curRes = null;
  $('#rlist').addEventListener('click', function (e) {
    var c = e.target.closest('[data-r]'); if (!c) return;
    api('GET', '/api/resumes/' + c.dataset.r).then(function (r) {
      curRes = r;
      $('#r-title').textContent = r.name;
      $('#r-text').value = r.text || '';
      $('#r-file').href = '/api/resumes/' + r.id + '/file';
      $('#r-main').hidden = !!r.is_main;
      $('#r-hint').textContent = r.is_main ? 'Это основное резюме: оно уходит в отклики по умолчанию и служит базой для адаптации.' : '';
      $('#r-html-wrap').hidden = $('#r-rebuild').hidden = !r.html;
      $('#r-html').value = r.html || '';
      $('#scrim').hidden = false; $('#rsheet').hidden = false;
    });
  });
  $('#r-close').addEventListener('click', closeEditor);
  $('#r-save').addEventListener('click', function () {
    api('PATCH', '/api/resumes/' + curRes.id, { text: $('#r-text').value }).then(function () { toast('Сохранено'); });
  });
  $('#r-main').addEventListener('click', function () {
    api('PATCH', '/api/resumes/' + curRes.id, { is_main: true }).then(function () { closeEditor(); loadResumes(); toast('Теперь это основное резюме'); });
  });
  $('#r-rebuild').addEventListener('click', function () {
    var b = this; b.classList.add('busy');
    api('POST', '/api/resumes/' + curRes.id + '/rebuild', { html: $('#r-html').value }).then(function () { toast('PDF пересобран'); })
      .catch(function (e) { toast(e.message); }).finally(function () { b.classList.remove('busy'); });
  });
  $('#r-del').addEventListener('click', function () {
    if (!confirm('Удалить «' + curRes.name + '» из списка? Файл останется в папке data/resumes.')) return;
    api('DELETE', '/api/resumes/' + curRes.id).then(function () { closeEditor(); loadResumes(); });
  });
  $('#upload').addEventListener('change', function (e) {
    var f = e.target.files[0]; if (!f) return;
    var fd = new FormData(); fd.append('file', f);
    fetch('/api/resumes/upload', { method: 'POST', body: fd }).then(function (r) { return r.json(); })
      .then(function () { toast('Резюме загружено'); loadResumes(); e.target.value = ''; });
  });

  /* =================== ПРОФИЛЬ =================== */
  var PF = [
    ['last_name', 'Фамилия'], ['first_name', 'Имя'], ['middle_name', 'Отчество'], ['birth_date', 'Дата рождения', 'date'],
    ['email', 'Email', 'email'], ['phone', 'Телефон', 'tel'], ['telegram', 'Telegram'], ['city', 'Город'],
    ['citizenship', 'Гражданство'], ['relocation', 'Переезд'], ['position', 'Желаемая должность'], ['salary', 'Ожидания по зарплате'],
    ['english', 'Английский'], ['university', 'Образование', 'wide'], ['hh_url', 'Ссылка на резюме hh.ru', 'url'],
    ['linkedin', 'LinkedIn', 'url'], ['github', 'GitHub', 'url'], ['portfolio', 'Портфолио / сайт', 'url'],
    ['cover_template', 'Шаблон сопроводительного письма — {company}, {role}, {name} подставятся сами', 'area']
  ];
  function loadProfileData() { return api('GET', '/api/profile').then(function (p) { profile = p; }); }
  function loadProfile() {
    Promise.all([loadProfileData(), api('GET', '/api/settings'), api('GET', '/api/sources')]).then(function (r) {
      var s = r[1], src = r[2];
      $('#pform').innerHTML = PF.map(function (f) {
        var type = f[2] && f[2] !== 'wide' && f[2] !== 'area' ? f[2] : 'text';
        var wide = f[2] === 'wide' || f[2] === 'area' ? ' wide' : '';
        var input = f[2] === 'area' ? '<textarea id="p-' + f[0] + '" rows="9">' + esc(profile[f[0]] || '') + '</textarea>'
          : '<input type="' + type + '" id="p-' + f[0] + '" value="' + esc(profile[f[0]] || '') + '">';
        return '<div class="f' + wide + '"><label for="p-' + f[0] + '">' + f[1] + '</label>' + input + '</div>';
      }).join('') + '<div class="actions"><button class="btn primary" type="submit">Сохранить профиль</button></div>';
      $('#auto-on').checked = !!s.auto_crawl.enabled;
      $('#auto-h').value = s.auto_crawl.hours || 6;
      $('#src-list').innerHTML = '<span class="tag">hh.ru: ' + src.hh.queries.length + ' запросов</span>' + src.companies.map(function (c) { return '<span class="tag">' + esc(c.name) + '</span>'; }).join('');
      $('#src-json').value = JSON.stringify(src, null, 1);
    });
  }
  $('#pform').addEventListener('submit', function (e) {
    e.preventDefault();
    var d = Object.assign({}, profile);
    PF.forEach(function (f) { d[f[0]] = $('#p-' + f[0]).value; });
    api('PUT', '/api/profile', d).then(function (p) { profile = p; toast('Профиль сохранён'); });
  });
  $('#save-auto').addEventListener('click', function () {
    var src;
    try { src = JSON.parse($('#src-json').value); } catch (x) { toast('В sources.json ошибка: ' + x.message); return; }
    Promise.all([
      api('PUT', '/api/settings', { auto_crawl: { enabled: $('#auto-on').checked, hours: +$('#auto-h').value || 6 } }),
      api('PUT', '/api/sources', src)
    ]).then(function () { toast('Настройки бота сохранены'); loadProfile(); }).catch(function (e) { toast(e.message); });
  });

  /* ---------- старт ---------- */
  Promise.all([loadProfileData(), loadResumesData(), loadVacStats()]).then(loadApps).catch(function (e) {
    $('#lede').textContent = 'Сервер не отвечает: ' + e.message;
  });
  var tab = 'apps'; try { tab = localStorage.getItem('jh.tab') || 'apps'; } catch (e) {}
  var fromHash = location.hash.replace('#', '');
  if (['apps', 'vac', 'res', 'prof'].indexOf(fromHash) !== -1) tab = fromHash;
  showTab(tab);
  // данные могли поменяться из расширения
  window.addEventListener('focus', function () { loadApps(); if (!$('#v-vac').hidden) loadVac(); });
})();
