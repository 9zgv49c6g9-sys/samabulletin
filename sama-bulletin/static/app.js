/* Sama Bulletin — application web (SPA, JavaScript natif, sans dépendance). */
(function () {
  'use strict';
  const C = window.SBCalc;
  const $app = document.getElementById('app');
  const S = { user: null, years: [], enrollment: null, subjects: [], meta: {}, log: [], bulletins: [], curriculum: null, semester: 1, history: null };

  /* ====================================================== utilitaires */
  const esc = (s) => String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (n, cls) => `<svg class="i ${cls || ''}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
  const fmt = C.fmt;
  const f2 = (x) => (x === null || x === undefined ? '—' : C.fmt(x, 2));
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const fmtDate = (iso) => new Date(iso).toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
  const fmtDT = (iso) => new Date(iso).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const FIELD_LABEL = { d1: 'Devoir 1', d2: 'Devoir 2', d3: 'Devoir 3', compo: 'Composition' };
  const SEM_LABEL = (n) => (n === 1 ? '1er semestre' : '2e semestre');
  const LOGO = '<svg viewBox="0 0 40 40" aria-hidden="true"><rect x="8" y="22" width="5" height="10" rx="1.2" fill="#fff"/><rect x="17.5" y="15" width="5" height="17" rx="1.2" fill="#fff"/><rect x="27" y="8" width="5" height="24" rx="1.2" fill="#fff"/><rect x="7" y="34.5" width="26" height="2" rx="1" fill="#8FC0EA"/></svg>';
  const SLOGAN = 'Tes notes. Ta progression. Ton bulletin.';

  function defaultSchoolYear() {
    const d = new Date(), y = d.getFullYear(), start = d.getMonth() + 1 >= 9 ? y : y - 1;
    return `${start}-${start + 1}`;
  }
  function yearOptions(selected, exclude) {
    const cur = parseInt(defaultSchoolYear(), 10);
    let out = '';
    for (let y = cur - 6; y <= cur + 2; y++) {
      const v = `${y}-${y + 1}`;
      if (exclude && exclude.includes(v)) continue;
      out += `<option value="${v}" ${v === selected ? 'selected' : ''}>${v}</option>`;
    }
    return out;
  }
  const initials = (u) => ((u.first_name || '?')[0] + (u.last_name || '')[0]).toUpperCase();
  const avatar = (u, cls) => (u.photo ? `<img class="avatar ${cls || ''}" src="${esc(u.photo)}" alt="">` : `<div class="avatar ${cls || ''}">${esc(initials(u))}</div>`);
  const rules = () => S.enrollment.rules;
  const summary = (sem) => C.semesterSummary(S.subjects, sem, rules());

  function toast(msg, kind) {
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = msg;
    $('#toasts').appendChild(el);
    setTimeout(() => el.remove(), kind === 'err' ? 5000 : 2600);
  }

  async function api(method, url, body, opts) {
    const o = { method, headers: { 'X-Requested-With': 'SamaBulletin' }, credentials: 'same-origin' };
    if (body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
    if (opts && opts.keepalive) o.keepalive = true;
    let res;
    try { res = await fetch(url, o); } catch (e) { const err = new Error('Connexion impossible. Vérifie ta connexion Internet.'); err.network = true; throw err; }
    let data = null;
    try { data = await res.json(); } catch (e) { /* pas de JSON */ }
    if (res.status === 401 && !/\/api\/(login|register)$/.test(url) && S.user) {
      S.user = null;
      toast('Ta session a expiré. Reconnecte-toi.', 'err');
      location.hash = '#/login';
    }
    if (!res.ok) { const err = new Error((data && data.error) || 'Une erreur est survenue.'); err.status = res.status; throw err; }
    return data;
  }

  function applyState(st) {
    S.user = st.user; S.years = st.years; S.enrollment = st.enrollment; S.subjects = st.subjects || [];
    S.meta = st.meta || {}; S.log = st.log || []; S.bulletins = st.bulletins || []; S.history = null;
  }

  /* ---------- fenêtres modales */
  function closeModal() { $('#modal-root').innerHTML = ''; }
  function modal(html, bind) {
    const root = $('#modal-root');
    root.innerHTML = `<div class="overlay"><div class="sheetm" role="dialog" aria-modal="true">${html}</div></div>`;
    const ov = root.firstElementChild;
    ov.addEventListener('mousedown', (e) => { if (e.target === ov) closeModal(); });
    $$('[data-close]', root).forEach((b) => b.addEventListener('click', closeModal));
    const first = $('input,select,textarea', root);
    if (first && window.innerWidth > 700) first.focus();
    if (bind) bind($('.sheetm', root));
  }
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
  function confirmBox(o) {
    return new Promise((resolve) => {
      modal(`<h2>${esc(o.title)}</h2><p class="muted" style="margin-bottom:18px">${esc(o.message)}</p>
        <div class="row"><button class="btn ghost grow" data-no>Annuler</button><button class="btn ${o.danger ? 'danger' : ''} grow" data-yes>${esc(o.confirm || 'Confirmer')}</button></div>`, (m) => {
        $('[data-no]', m).addEventListener('click', () => { closeModal(); resolve(false); });
        $('[data-yes]', m).addEventListener('click', () => { closeModal(); resolve(true); });
      });
    });
  }

  /* ====================================================== enregistrement automatique des notes */
  const timers = new Map(), pendingVals = new Map();
  let inflight = 0, failed = false;
  function setStatus() {
    const el = $('#savestatus');
    if (!el) return;
    if (failed) { el.className = 'status err'; el.innerHTML = 'Non enregistré — nouvelle tentative…'; }
    else if (pendingVals.size || inflight) { el.className = 'status'; el.innerHTML = 'Enregistrement…'; }
    else { el.className = 'status ok'; el.innerHTML = icon('check', 'sm') + ' Enregistré automatiquement'; }
  }
  function queueGrade(subjectId, sem, field, value) {
    const key = `${subjectId}:${sem}:${field}`;
    clearTimeout(timers.get(key));
    pendingVals.set(key, { subjectId, sem, field, value });
    timers.set(key, setTimeout(() => flushKey(key), 500));
    setStatus();
  }
  async function flushKey(key) {
    const p = pendingVals.get(key);
    if (!p) return;
    pendingVals.delete(key); timers.delete(key); inflight++;
    try {
      await api('PUT', '/api/grades', { subject_id: p.subjectId, semester: p.sem, field: p.field, value: p.value }, { keepalive: true });
      failed = false;
    } catch (e) {
      if (e.network) {
        failed = true;
        if (!pendingVals.has(key)) { pendingVals.set(key, p); timers.set(key, setTimeout(() => flushKey(key), 4000)); }
      } else { toast(e.message, 'err'); }
    } finally { inflight--; setStatus(); }
  }
  async function flushAll() {
    Array.from(pendingVals.keys()).forEach((k) => { clearTimeout(timers.get(k)); });
    await Promise.all(Array.from(pendingVals.keys()).map(flushKey));
    while (inflight > 0) await sleep(40);
  }
  window.addEventListener('beforeunload', (e) => { if (pendingVals.size) { e.preventDefault(); e.returnValue = ''; } });
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flushAll(); });

  function setGrade(sid, sem, field, value) {
    const s = S.subjects.find((x) => x.id === sid);
    if (!s) return;
    if (!s.grades[sem]) s.grades[sem] = { d1: null, d2: null, d3: null, compo: null };
    const old = s.grades[sem][field];
    if (old === value) return;
    s.grades[sem][field] = value;
    S.history = null;
    S.log.unshift({ subject_name: s.name, semester: sem, field, old_value: old, new_value: value, at: new Date().toISOString() });
    S.log = S.log.slice(0, 8);
    queueGrade(sid, sem, field, value);
  }

  /* ====================================================== graphiques */
  function lineChart(pts, objective) {
    const W = 340, H = 190, L = 28, R = 14, T = 16, B = 38;
    const xs = (i) => (pts.length === 1 ? L + (W - L - R) / 2 : L + (i * (W - L - R)) / (pts.length - 1));
    const ys = (v) => T + (1 - v / 20) * (H - T - B);
    let g = '';
    [0, 5, 10, 15, 20].forEach((v) => { g += `<line x1="${L}" x2="${W - R}" y1="${ys(v)}" y2="${ys(v)}" stroke="#D6E4F4" ${v === 10 ? 'stroke-dasharray="4 4"' : ''}/><text x="${L - 6}" y="${ys(v) + 4}" text-anchor="end">${v}</text>`; });
    if (objective !== null && objective !== undefined) g += `<line x1="${L}" x2="${W - R}" y1="${ys(objective)}" y2="${ys(objective)}" stroke="#E08A1E" stroke-width="1.6" stroke-dasharray="6 4"/><text x="${W - R}" y="${ys(objective) - 5}" text-anchor="end" style="fill:#B26A00;font-weight:700">Objectif ${fmt(objective)}</text>`;
    const path = pts.map((p, i) => `${i ? 'L' : 'M'}${xs(i).toFixed(1)} ${ys(p.value).toFixed(1)}`).join(' ');
    if (pts.length > 1) g += `<path d="${path} L${xs(pts.length - 1).toFixed(1)} ${ys(0)} L${xs(0).toFixed(1)} ${ys(0)} Z" fill="rgba(40,123,199,.12)"/><path d="${path}" fill="none" stroke="#1456A0" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"/>`;
    pts.forEach((p, i) => {
      const last = i === pts.length - 1;
      g += `<circle cx="${xs(i).toFixed(1)}" cy="${ys(p.value).toFixed(1)}" r="${last ? 5 : 4}" fill="${last ? '#1456A0' : '#fff'}" stroke="#1456A0" stroke-width="2.2"/>`;
      g += `<text x="${xs(i).toFixed(1)}" y="${(ys(p.value) - 10).toFixed(1)}" text-anchor="middle" style="fill:#0B1F3A;font-weight:700">${fmt(p.value, 2)}</text>`;
      g += `<text x="${xs(i).toFixed(1)}" y="${H - 20}" text-anchor="middle" style="font-size:10px">${esc(p.l1)}</text><text x="${xs(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" style="font-size:9px">${esc(p.l2)}</text>`;
    });
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Évolution de la moyenne générale">${g}</svg>`;
  }

  /* ====================================================== coque (navigation) */
  const NAV = [['dashboard', 'Accueil', 'home'], ['notes', 'Notes', 'edit'], ['bulletin', 'Bulletin', 'file'], ['history', 'Historique', 'clock'], ['settings', 'Réglages', 'sliders']];
  function shell(active, inner) {
    const e = S.enrollment, u = S.user;
    return `<div class="shell">
      <header class="topbar"><a class="brand-mini" href="#/dashboard"><span class="logo">${LOGO}</span>Sama Bulletin</a>
        <div class="row gap-s"><span class="chip">${esc(e.school_year)} · ${esc(e.class_label)}</span><a href="#/settings" aria-label="Mon profil">${avatar(u, 'sm')}</a></div></header>
      <nav class="nav" aria-label="Navigation principale">
        <div class="side-brand"><span class="logo">${LOGO}</span>Sama Bulletin</div>
        ${NAV.map(([k, l, i]) => `<a href="#/${k}" class="${k === active ? 'active' : ''}" ${k === active ? 'aria-current="page"' : ''}>${icon(i)}<span>${l}</span></a>`).join('')}
        <div class="spacer"></div>
        <div class="side-user">${avatar(u, 'sm')}<div><div style="font-weight:700;font-size:.9rem">${esc(u.first_name)} ${esc(u.last_name)}</div><div class="small muted">${esc(e.school_year)} · ${esc(e.class_label)}</div></div></div>
      </nav>
      <main class="view">${inner}</main></div>`;
  }
  const authShell = (inner) => `<div class="auth"><div class="auth-card"><div class="brand"><div class="logo">${LOGO}</div><h1>Sama Bulletin</h1><p>${SLOGAN}</p></div>${inner}</div></div>`;
  const errBox = (id) => `<div id="${id}" class="err hidden" role="alert"></div>`;
  function showErr(id, msg) { const el = document.getElementById(id); if (el) { el.textContent = msg; el.classList.remove('hidden'); } }
  function pwField(id, label, auto) {
    return `<div class="field"><label for="${id}">${label}</label><div class="input-wrap"><input class="input" id="${id}" type="password" autocomplete="${auto || 'current-password'}" required>
      <button type="button" class="icon-btn" data-toggle="${id}" aria-label="Afficher ou masquer le mot de passe">${icon('eye')}</button></div></div>`;
  }
  function bindToggles(root) {
    $$('[data-toggle]', root).forEach((b) => b.addEventListener('click', () => { const i = document.getElementById(b.dataset.toggle); i.type = i.type === 'password' ? 'text' : 'password'; }));
  }
  async function busy(btn, fn) {
    const label = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spinner" style="width:18px;height:18px;border-width:3px"></span>';
    try { return await fn(); } finally { btn.disabled = false; btn.innerHTML = label; }
  }

  /* ====================================================== pages d'authentification */
  function viewLogin() {
    $app.innerHTML = authShell(`<h2>Connexion</h2>${errBox('e')}
      <form id="f" novalidate>
        <div class="field"><label for="email">Adresse e-mail</label><input class="input" id="email" type="email" autocomplete="email" inputmode="email" required></div>
        ${pwField('pw', 'Mot de passe')}
        <button class="btn block" type="submit">Se connecter</button>
      </form>
      <div class="links"><a href="#/forgot">Mot de passe oublié ?</a><a href="#/register">Créer un compte</a></div>`);
    bindToggles($app);
    $('#f').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const btn = $('button[type=submit]');
      $('#e').classList.add('hidden');
      try {
        await busy(btn, async () => { applyState(await api('POST', '/api/login', { email: $('#email').value, password: $('#pw').value })); });
        route(true);
      } catch (e) { showErr('e', e.message); }
    });
  }

  function viewRegister() {
    $app.innerHTML = authShell(`<h2>Créer mon compte</h2>${errBox('e')}
      <form id="f" novalidate>
        <div class="cols2 stack-xs"><div class="field"><label for="fn">Prénom</label><input class="input" id="fn" autocomplete="given-name" maxlength="60" required></div>
        <div class="field"><label for="ln">Nom</label><input class="input" id="ln" autocomplete="family-name" maxlength="60" required></div></div>
        <div class="field"><label for="email">Adresse e-mail</label><input class="input" id="email" type="email" autocomplete="email" inputmode="email" required></div>
        ${pwField('pw', 'Mot de passe (8 caractères minimum)', 'new-password')}
        ${pwField('pw2', 'Confirmer le mot de passe', 'new-password')}
        <button class="btn block" type="submit">Créer mon compte</button>
      </form>
      <p class="hint center" style="margin-top:12px">Tes notes restent privées : seul ton compte y a accès.</p>
      <div class="links"><span>Déjà inscrit ?</span><a href="#/login">Se connecter</a></div>`);
    bindToggles($app);
    $('#f').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      $('#e').classList.add('hidden');
      if ($('#pw').value !== $('#pw2').value) return showErr('e', 'Les deux mots de passe ne sont pas identiques.');
      try {
        await busy($('button[type=submit]'), async () => {
          applyState(await api('POST', '/api/register', { first_name: $('#fn').value, last_name: $('#ln').value, email: $('#email').value, password: $('#pw').value }));
        });
        route(true);
      } catch (e) { showErr('e', e.message); }
    });
  }

  function viewForgot() {
    $app.innerHTML = authShell(`<h2>Mot de passe oublié</h2><p class="muted" style="margin-bottom:14px">Saisis ton adresse e-mail : si un compte existe, tu recevras un lien pour choisir un nouveau mot de passe (valable 1 heure).</p>
      ${errBox('e')}<div id="ok" class="okmsg hidden"></div>
      <form id="f" novalidate><div class="field"><label for="email">Adresse e-mail</label><input class="input" id="email" type="email" autocomplete="email" inputmode="email" required></div>
      <button class="btn block" type="submit">Envoyer le lien</button></form>
      <div class="links"><a href="#/login">Retour à la connexion</a></div>`);
    $('#f').addEventListener('submit', async (ev) => {
      ev.preventDefault(); $('#e').classList.add('hidden');
      try {
        const r = await busy($('button[type=submit]'), () => api('POST', '/api/forgot', { email: $('#email').value }));
        $('#ok').textContent = r.message; $('#ok').classList.remove('hidden');
      } catch (e) { showErr('e', e.message); }
    });
  }

  function viewReset(query) {
    const token = new URLSearchParams(query || '').get('token') || '';
    $app.innerHTML = authShell(`<h2>Nouveau mot de passe</h2>${errBox('e')}<div id="ok" class="okmsg hidden"></div>
      <form id="f" novalidate>${pwField('pw', 'Nouveau mot de passe (8 caractères minimum)', 'new-password')}${pwField('pw2', 'Confirmer le mot de passe', 'new-password')}
      <button class="btn block" type="submit">Enregistrer</button></form>
      <div class="links"><a href="#/login">Retour à la connexion</a></div>`);
    bindToggles($app);
    $('#f').addEventListener('submit', async (ev) => {
      ev.preventDefault(); $('#e').classList.add('hidden');
      if (!token) return showErr('e', 'Lien invalide. Refais une demande depuis « Mot de passe oublié ».');
      if ($('#pw').value !== $('#pw2').value) return showErr('e', 'Les deux mots de passe ne sont pas identiques.');
      try {
        await busy($('button[type=submit]'), () => api('POST', '/api/reset', { token, password: $('#pw').value }));
        $('#f').classList.add('hidden');
        $('#ok').innerHTML = 'Mot de passe modifié ! <a href="#/login">Se connecter</a>'; $('#ok').classList.remove('hidden');
      } catch (e) { showErr('e', e.message); }
    });
  }

  /* ---------- sélecteurs de classe (partagés) */
  function levelOptions(sel) {
    const groups = {};
    S.curriculum.levels.forEach((l) => { (groups[l.cycle] = groups[l.cycle] || []).push(l); });
    return Object.entries(groups).map(([cy, ls]) => `<optgroup label="${esc(cy)}">${ls.map((l) => `<option value="${l.id}" ${l.id === sel ? 'selected' : ''}>${esc(l.label)}</option>`).join('')}</optgroup>`).join('');
  }
  function bindClassPicker(root, level, serie) {
    const lv = $('[name=level]', root), sr = $('[name=serie]', root), wrap = $('[data-serie-wrap]', root);
    const refresh = (keep) => {
      const l = S.curriculum.levels.find((x) => x.id === lv.value);
      if (l && l.series.length) { wrap.classList.remove('hidden'); sr.innerHTML = '<option value="">Choisir…</option>' + l.series.map((s) => `<option ${s === keep ? 'selected' : ''}>${esc(s)}</option>`).join(''); sr.required = true; }
      else { wrap.classList.add('hidden'); sr.innerHTML = ''; sr.required = false; }
    };
    lv.addEventListener('change', () => refresh(null)); refresh(serie);
  }
  const classPickerHtml = (level, serie) => `<div class="cols2 stack-xs"><div class="field"><label>Classe</label><select class="input" name="level" required>${levelOptions(level)}</select></div>
    <div class="field hidden" data-serie-wrap><label>Série</label><select class="input" name="serie"></select></div></div>`;

  function resizePhoto(file) {
    return new Promise((resolve, reject) => {
      if (!/^image\/(jpeg|png|webp|heic|heif|gif|bmp)/.test(file.type) && !file.type.startsWith('image/')) return reject(new Error('Choisis un fichier image.'));
      const img = new Image(), url = URL.createObjectURL(file);
      img.onload = () => {
        const s = 256, c = document.createElement('canvas'); c.width = c.height = s;
        const m = Math.min(img.width, img.height), sx = (img.width - m) / 2, sy = (img.height - m) / 2;
        c.getContext('2d').drawImage(img, sx, sy, m, m, 0, 0, s, s); URL.revokeObjectURL(url);
        resolve(c.toDataURL('image/jpeg', 0.82));
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Impossible de lire cette image.")); };
      img.src = url;
    });
  }
  function bindPhoto(root, onChange) {
    const input = $('[data-photo-input]', root);
    $('[data-photo-btn]', root).addEventListener('click', () => input.click());
    input.addEventListener('change', async () => {
      if (!input.files[0]) return;
      try { const data = await resizePhoto(input.files[0]); onChange(data); const prev = $('[data-photo-prev]', root); prev.outerHTML = `<img class="avatar" data-photo-prev src="${data}" alt="">`; }
      catch (e) { toast(e.message, 'err'); }
    });
  }
  const photoHtml = (u, photo) => `<div class="photo-pick">${photo ? `<img class="avatar" data-photo-prev src="${esc(photo)}" alt="">` : `<div class="avatar" data-photo-prev>${esc(initials(u))}</div>`}
    <div><button type="button" class="btn secondary sm" data-photo-btn>${icon('camera', 'sm')} Photo de profil</button><div class="hint">Facultatif</div></div><input type="file" accept="image/*" data-photo-input class="hidden"></div>`;

  /* ---------- première connexion */
  function viewOnboarding() {
    let photo = S.user.photo || null, photoChanged = false;
    $app.innerHTML = authShell(`<h2>Bienvenue ! Parle-nous de toi</h2><p class="muted" style="margin-bottom:14px">Ces informations servent à préparer tes matières et ton bulletin. Tu pourras tout modifier plus tard.</p>
      ${errBox('e')}<form id="f" novalidate>${photoHtml(S.user, photo)}
      <div class="cols2 stack-xs"><div class="field"><label>Prénom</label><input class="input" name="fn" maxlength="60" value="${esc(S.user.first_name)}" required></div>
      <div class="field"><label>Nom</label><input class="input" name="ln" maxlength="60" value="${esc(S.user.last_name)}" required></div></div>
      <div class="field"><label>Établissement</label><input class="input" name="school" maxlength="100" placeholder="Ex. Lycée Blaise Diagne" required></div>
      ${classPickerHtml('terminale', null)}
      <div class="field"><label>Année scolaire</label><select class="input" name="year">${yearOptions(defaultSchoolYear())}</select></div>
      <button class="btn block" type="submit">Commencer</button></form>
      <div class="links"><a href="#" id="lo">Se déconnecter</a></div>`);
    bindClassPicker($app, 'terminale', null);
    bindPhoto($app, (d) => { photo = d; photoChanged = true; });
    $('#lo').addEventListener('click', async (e) => { e.preventDefault(); await logout(); });
    $('#f').addEventListener('submit', async (ev) => {
      ev.preventDefault(); $('#e').classList.add('hidden');
      const f = ev.target, level = f.level.value, serie = f.serie.value;
      if (!f.fn.value.trim() || !f.ln.value.trim()) return showErr('e', 'Prénom et nom sont obligatoires.');
      if (!f.school.value.trim()) return showErr('e', "Indique le nom de ton établissement.");
      const lv = S.curriculum.levels.find((l) => l.id === level);
      if (lv.series.length && !serie) return showErr('e', 'Choisis ta série.');
      try {
        await busy($('button[type=submit]'), async () => {
          const prof = { first_name: f.fn.value, last_name: f.ln.value }; if (photoChanged) prof.photo = photo;
          await api('PUT', '/api/profile', prof);
          applyState(await api('PUT', '/api/enrollment', { school: f.school.value, level, serie: serie || null, school_year: f.year.value }));
        });
        toast('Ton espace est prêt !', 'ok'); location.hash = '#/dashboard'; route(true);
      } catch (e) { showErr('e', e.message); }
    });
  }

  async function logout() {
    await flushAll();
    try { await api('POST', '/api/logout', {}); } catch (e) { /* déjà déconnecté */ }
    S.user = null; S.enrollment = null; location.hash = '#/login'; route(true);
  }

  /* ====================================================== tableau de bord */
  async function loadHistory(force) {
    if (S.history && !force) return S.history;
    S.history = await api('GET', '/api/history');
    return S.history;
  }

  function addNoteModal() {
    const sem0 = S.semester;
    const optsSubj = S.subjects.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
    modal(`<h2>Ajouter une note <button class="icon-btn" data-close aria-label="Fermer">${icon('x')}</button></h2><div id="e" class="err hidden"></div>
      <form id="nf" novalidate>
        <div class="field"><label>Semestre</label><select class="input" name="sem"><option value="1" ${sem0 === 1 ? 'selected' : ''}>1er semestre</option><option value="2" ${sem0 === 2 ? 'selected' : ''}>2e semestre</option></select></div>
        <div class="field"><label>Matière</label><select class="input" name="sid">${optsSubj}</select></div>
        <div class="cols2 stack-xs"><div class="field"><label>Type de note</label><select class="input" name="field"><option value="d1">Devoir 1</option><option value="d2">Devoir 2</option><option value="d3">Devoir 3</option><option value="compo">Composition</option></select></div>
        <div class="field"><label>Note sur 20</label><input class="input" name="val" inputmode="decimal" autocomplete="off" placeholder="Ex. 14,5" required></div></div>
        <p class="hint" id="exist" style="margin:-4px 0 12px"></p>
        <button class="btn block" type="submit">Enregistrer la note</button></form>`, (m) => {
      const f = $('#nf', m);
      const guess = () => {
        const s = S.subjects.find((x) => x.id === +f.sid.value), g = (s && s.grades[f.sem.value]) || {};
        const first = ['d1', 'd2', 'd3', 'compo'].find((k) => g[k] === null || g[k] === undefined) || 'compo';
        f.field.value = first;
        $('#exist', m).textContent = g[f.field.value] !== null && g[f.field.value] !== undefined ? `Cette case contient déjà ${fmt(g[f.field.value])} : elle sera remplacée.` : '';
      };
      f.sid.addEventListener('change', guess); f.sem.addEventListener('change', guess); f.field.addEventListener('change', () => {
        const s = S.subjects.find((x) => x.id === +f.sid.value), g = (s && s.grades[f.sem.value]) || {};
        $('#exist', m).textContent = g[f.field.value] !== null && g[f.field.value] !== undefined ? `Cette case contient déjà ${fmt(g[f.field.value])} : elle sera remplacée.` : '';
      });
      guess();
      f.addEventListener('submit', (ev) => {
        ev.preventDefault();
        const p = C.parseGrade(f.val.value);
        if (!p.ok || p.value === null) { const e = $('#e', m); e.textContent = p.error || 'Saisis une note.'; e.classList.remove('hidden'); return; }
        S.semester = +f.sem.value; localStorage.setItem('sb_sem', S.semester);
        setGrade(+f.sid.value, +f.sem.value, f.field.value, p.value);
        closeModal(); toast('Note enregistrée', 'ok'); route(true);
      });
    });
  }

  function objectiveModal() {
    modal(`<h2>Mon objectif de moyenne <button class="icon-btn" data-close aria-label="Fermer">${icon('x')}</button></h2><div id="e" class="err hidden"></div>
      <form id="of"><div class="field"><label>Moyenne générale visée (sur 20)</label><input class="input" name="o" inputmode="decimal" placeholder="Ex. 14" value="${S.enrollment.objective !== null ? esc(fmt(S.enrollment.objective)) : ''}"></div>
      <div class="row"><button type="button" class="btn ghost grow" id="clr">Supprimer</button><button class="btn grow" type="submit">Enregistrer</button></div></form>`, (m) => {
      const save = async (val) => {
        try { const r = rules(); const res = await api('PUT', '/api/rules', { wd: r.wd, wc: r.wc, keep: r.keep, objective: val }); S.enrollment.objective = res.objective; closeModal(); route(true); }
        catch (e) { const el = $('#e', m); el.textContent = e.message; el.classList.remove('hidden'); }
      };
      $('#clr', m).addEventListener('click', () => save(null));
      $('#of', m).addEventListener('submit', (ev) => { ev.preventDefault(); const p = C.parseGrade(ev.target.o.value); if (!p.ok) { const el = $('#e', m); el.textContent = p.error; el.classList.remove('hidden'); } else save(p.value); });
    });
  }

  function viewDashboard() {
    const sem = S.semester, sum = summary(sem), ys = C.yearSummary(S.subjects, rules());
    const e = S.enrollment, g = sum.general, obj = e.objective;
    const best = sum.rows.find((r) => r.id === sum.bestId), worst = sum.rows.find((r) => r.id === sum.worstId);
    const hasNotes = sum.scored > 0;
    let heroExtra = '';
    if (hasNotes && obj !== null) {
      const pct = Math.max(0, Math.min(100, (g / obj) * 100));
      heroExtra = `<div class="bar"><i style="width:${pct}%"></i></div><div class="small" style="margin-top:6px;opacity:.9">${g >= obj ? 'Objectif atteint, bravo !' : `Objectif ${fmt(obj)} — il te manque ${fmt(C.round2(obj - g), 2)} point${obj - g >= 2 ? 's' : ''}`}</div>`;
    } else if (obj === null) heroExtra = `<div class="small" style="margin-top:8px;opacity:.9">Définis un objectif pour suivre ta progression.</div>`;
    else heroExtra = '';
    const apprWord = !hasNotes ? '' : g >= 16 ? 'Excellent' : g >= 14 ? 'Très bien' : g >= 12 ? 'Bien' : g >= 10 ? 'Assez bien' : 'À renforcer';
    const barsHtml = sum.rows.filter((r) => r.avg !== null).map((r) => `<div class="hbar ${r.avg < 8 ? 'fail' : r.avg < 10 ? 'low' : ''}"><span class="nm" title="${esc(r.name)}">${esc(r.name)}</span><span class="tr"><i style="width:${(r.avg / 20) * 100}%"></i></span><b>${f2(r.avg)}</b></div>`).join('');
    const logs = S.log.slice(0, 5).map((l) => `<li><div class="grow"><div class="t">${esc(l.subject_name)}</div><div class="d">${FIELD_LABEL[l.field]} · ${l.semester === 1 ? 'S1' : 'S2'} · ${fmtDT(l.at)}</div></div>${l.new_value !== null ? `<span class="badge-note">${fmt(l.new_value)}</span>` : '<span class="chip grey">supprimée</span>'}</li>`).join('');
    $app.innerHTML = shell('dashboard', `
      <h1 class="page-title">Bonjour ${esc(S.user.first_name)} 👋</h1><p class="page-sub">${esc(SLOGAN)}</p>
      <div class="seg" role="tablist"><button data-sem="1" class="${sem === 1 ? 'on' : ''}">Semestre 1</button><button data-sem="2" class="${sem === 2 ? 'on' : ''}">Semestre 2</button></div>
      <section class="card hero"><div class="lab">Moyenne générale — ${SEM_LABEL(sem)}</div>
        <div class="big">${g === null ? '—' : f2(g)}<small> / 20</small></div>
        <div class="row wrap gap-s">${apprWord ? `<span class="chip">${apprWord}</span>` : ''}<button class="chip" id="obj" style="border:0;cursor:pointer">${icon('target', 'sm')} ${obj !== null ? 'Objectif ' + fmt(obj) : 'Définir un objectif'}</button></div>${heroExtra}</section>
      <div class="cta"><button class="btn" id="addnote">${icon('plus')} Ajouter une note</button><a class="btn secondary" href="#/bulletin">${icon('file')} Voir mon bulletin</a></div>
      <div class="stats">
        <div class="stat"><div class="ic">${icon('book')}</div><div class="k">Matières</div><div class="v">${sum.total}</div><div class="s">${sum.scored} avec des notes</div></div>
        <div class="stat"><div class="ic">${icon('star')}</div><div class="k">Meilleure matière</div><div class="v">${best ? esc(best.name) : '—'}</div><div class="s">${best ? f2(best.avg) + ' / 20' : 'Pas encore de note'}</div></div>
        <div class="stat"><div class="ic">${icon('alert')}</div><div class="k">À améliorer</div><div class="v">${worst ? esc(worst.name) : '—'}</div><div class="s">${worst ? f2(worst.avg) + ' / 20' : (hasNotes ? 'Une seule matière notée' : 'Pas encore de note')}</div></div>
        <div class="stat"><div class="ic">${icon('up')}</div><div class="k">Progression</div><div class="v" id="prog">…</div><div class="s" id="progs">vs semestre précédent</div></div>
      </div>
      <section class="card"><h2>Évolution de la moyenne générale</h2><div id="chart"><div class="empty small">Chargement…</div></div></section>
      <section class="card"><h2>Moyennes par matière — ${SEM_LABEL(sem)}</h2>${barsHtml || `<div class="empty"><div class="ic">${icon('bars')}</div>Aucune note pour ce semestre.<br><a href="#/notes">Saisir mes notes</a></div>`}</section>
      <section class="card"><h2>Dernières notes saisies</h2>${logs ? `<ul class="list">${logs}</ul>` : `<div class="empty small">Tes dernières saisies apparaîtront ici.</div>`}</section>`);
    $$('[data-sem]').forEach((b) => b.addEventListener('click', () => { S.semester = +b.dataset.sem; localStorage.setItem('sb_sem', S.semester); viewDashboard(); }));
    $('#addnote').addEventListener('click', addNoteModal);
    $('#obj').addEventListener('click', objectiveModal);
    // progression + graphique (nécessitent l'historique)
    loadHistory().then((h) => {
      if (!$('#chart')) return;
      const cur = e.school_year;
      const pts = [];
      h.years.slice().reverse().forEach((y) => {
        const s1 = y.school_year === cur ? ys.s1.general : y.s1, s2 = y.school_year === cur ? ys.s2.general : y.s2;
        const short = y.school_year.slice(2, 4) + '-' + y.school_year.slice(7);
        if (s1 !== null) pts.push({ value: s1, l1: 'S1', l2: short });
        if (s2 !== null) pts.push({ value: s2, l1: 'S2', l2: short });
      });
      const shown = pts.slice(-6);
      $('#chart').innerHTML = shown.length ? lineChart(shown, obj) + (shown.length < 2 ? '<p class="hint center">Le graphique se complétera avec tes prochains semestres.</p>' : '') : `<div class="empty"><div class="ic">${icon('up')}</div>Ajoute des notes pour voir ta courbe de progression.</div>`;
      // progression par rapport au semestre précédent
      let prev = null, label = '';
      if (sem === 2) { prev = ys.s1.general; label = 'vs 1er semestre'; }
      else {
        const py = `${parseInt(cur, 10) - 1}-${parseInt(cur, 10)}`, y = h.years.find((x) => x.school_year === py);
        if (y && y.s2 !== null) { prev = y.s2; label = `vs 2e semestre ${py}`; }
      }
      if (g !== null && prev !== null) {
        const d = C.round2(g - prev);
        $('#prog').innerHTML = `<span style="color:${d > 0 ? 'var(--ok)' : d < 0 ? 'var(--bad)' : 'inherit'}">${d > 0 ? '+' : ''}${fmt(d, 2)} pt</span>`; $('#progs').textContent = label;
      } else { $('#prog').textContent = '—'; $('#progs').textContent = sem === 1 ? 'Dès que tu as un semestre de référence' : 'Il manque des notes au S1'; }
    }).catch(() => { const c = $('#chart'); if (c) c.innerHTML = '<div class="empty small">Graphique indisponible.</div>'; });
  }

  /* ====================================================== saisie des notes */
  const noteFormula = () => { const r = rules(); return `Moyenne de la matière = (moyenne des ${r.keep === 3 ? '3 devoirs' : r.keep === 1 ? 'meilleur devoir' : `${r.keep} meilleurs devoirs`} × ${fmt(r.wd)} + composition × ${fmt(r.wc)}) ÷ ${fmt(r.wd + r.wc)}`; };

  function subjectCardHtml(s, sem) {
    const g = s.grades[sem] || {};
    const cells = ['d1', 'd2', 'd3', 'compo'].map((k) => `<div class="gcell"><label for="g-${s.id}-${k}">${k === 'compo' ? 'Compo.' : 'Dev. ' + k[1]}</label>
      <input class="gin" id="g-${s.id}-${k}" data-sid="${s.id}" data-field="${k}" type="text" inputmode="decimal" autocomplete="off" maxlength="5" placeholder="—" value="${g[k] === null || g[k] === undefined ? '' : esc(fmt(g[k]))}" aria-label="${esc(s.name)} — ${FIELD_LABEL[k]}"><div class="tag" data-tag="${k}"></div></div>`).join('');
    return `<article class="card subj" data-card="${s.id}">
      <div class="subj-head"><div><h3>${esc(s.name)}<span class="coef">coef ${fmt(s.coef)}</span>${s.placeholder ? '<span class="ph">à personnaliser</span>' : ''}</h3></div>
        <div class="row gap-s"><div class="subj-avg"><b data-role="avg">—</b><small data-role="avgn">moyenne /20</small></div>
        <div class="subj-actions"><button class="icon-btn" data-act="edit" aria-label="Modifier la matière ${esc(s.name)}">${icon('edit')}</button><button class="icon-btn" data-act="clear" aria-label="Vider les notes de ${esc(s.name)}">${icon('trash')}</button></div></div></div>
      <div class="grid4">${cells}</div><div class="subj-foot" data-role="foot"></div></article>`;
  }

  function refreshCard(card, sem) {
    const s = S.subjects.find((x) => x.id === +card.dataset.card);
    const r = C.subjectResult(s.grades[sem] || {}, rules().wd, rules().wc, rules().keep);
    ['d1', 'd2', 'd3'].forEach((k, i) => {
      const inp = $(`[data-field=${k}]`, card), tag = $(`[data-tag=${k}]`, card);
      const isKept = r.kept.includes(i), isDropped = r.dropped.includes(i);
      inp.classList.toggle('kept', isKept); inp.classList.toggle('dropped', isDropped);
      tag.className = 'tag' + (isKept ? ' kept' : isDropped ? ' dropped' : ''); tag.textContent = isKept ? 'retenue' : isDropped ? 'écartée' : '';
    });
    $('[data-role=avg]', card).textContent = r.avg === null ? '—' : f2(r.avg);
    const foot = $('[data-role=foot]', card);
    const keptTxt = r.kept.map((i) => `<b>${fmt(r.d[i])}</b>`).join(' et '), dropTxt = r.dropped.map((i) => `<b>${fmt(r.d[i])}</b>`).join(', ');
    let t = '';
    if (r.kept.length) t += `<span>Retenue${r.kept.length > 1 ? 's' : ''} : ${keptTxt}</span>`;
    if (r.dropped.length) t += `<span>Écartée : ${dropTxt}</span>`;
    if (r.devAvg !== null) t += `<span>Moyenne des devoirs : <b>${f2(r.devAvg)}</b></span>`;
    if (r.avg !== null && !r.complete) t += '<span class="chip warn">provisoire</span>';
    foot.innerHTML = t || '<span>Saisis tes notes : elles sont enregistrées automatiquement.</span>';
  }

  function refreshSummaryStrip(sem) {
    const sum = summary(sem), el = $('#strip');
    if (el) el.innerHTML = `<div><div class="small" style="opacity:.85">Moyenne générale — ${SEM_LABEL(sem)}</div><b>${sum.general === null ? '—' : f2(sum.general)}</b><span> / 20</span></div><div class="small" style="text-align:right;opacity:.9">${sum.scored} matière${sum.scored > 1 ? 's' : ''} notée${sum.scored > 1 ? 's' : ''}<br>sur ${sum.total}</div>`;
  }

  function subjectModal(subject) {
    const isNew = !subject;
    modal(`<h2>${isNew ? 'Ajouter une matière' : 'Modifier la matière'} <button class="icon-btn" data-close aria-label="Fermer">${icon('x')}</button></h2><div id="e" class="err hidden"></div>
      <form id="sf" novalidate><div class="field"><label>Nom de la matière</label><input class="input" name="name" maxlength="80" value="${isNew ? '' : esc(subject.name)}" placeholder="Ex. Wolof" required></div>
      <div class="field"><label>Coefficient</label><input class="input" name="coef" inputmode="decimal" value="${isNew ? '1' : esc(fmt(subject.coef))}" required><span class="hint">Entre 0,25 et 20. Le coefficient pèse dans la moyenne générale.</span></div>
      <div class="row">${isNew ? '' : '<button type="button" class="btn danger ghost" id="del">Supprimer</button>'}<button class="btn grow" type="submit">${isNew ? 'Ajouter' : 'Enregistrer'}</button></div></form>`, (m) => {
      const err = (msg) => { const el = $('#e', m); el.textContent = msg; el.classList.remove('hidden'); };
      $('#sf', m).addEventListener('submit', async (ev) => {
        ev.preventDefault();
        const coef = parseFloat(String(ev.target.coef.value).replace(',', '.'));
        if (!(coef >= 0.25 && coef <= 20)) return err('Le coefficient doit être compris entre 0,25 et 20.');
        try {
          if (isNew) { const r = await api('POST', '/api/subjects', { name: ev.target.name.value, coef }); S.subjects.push(r.subject); }
          else { const r = await api('PUT', `/api/subjects/${subject.id}`, { name: ev.target.name.value, coef }); subject.name = r.name; subject.coef = r.coef; subject.placeholder = false; }
          S.history = null; closeModal(); toast(isNew ? 'Matière ajoutée' : 'Matière modifiée', 'ok'); route(true);
        } catch (e) { err(e.message); }
      });
      if (!isNew) $('#del', m).addEventListener('click', async () => {
        if (!(await confirmBox({ title: `Supprimer « ${subject.name} » ?`, message: 'Les notes de cette matière (1er et 2e semestre) seront définitivement supprimées.', confirm: 'Supprimer', danger: true }))) return;
        try { await flushAll(); await api('DELETE', `/api/subjects/${subject.id}`); S.subjects = S.subjects.filter((x) => x.id !== subject.id); S.history = null; toast('Matière supprimée', 'ok'); route(true); } catch (e) { toast(e.message, 'err'); }
      });
    });
  }

  function viewNotes() {
    const sem = S.semester;
    $app.innerHTML = shell('notes', `
      <h1 class="page-title">Mes notes</h1><p class="page-sub">Les 2 meilleurs devoirs sont retenus automatiquement. Une case vide n'est jamais comptée comme zéro.</p>
      <div class="seg"><button data-sem="1" class="${sem === 1 ? 'on' : ''}">Semestre 1</button><button data-sem="2" class="${sem === 2 ? 'on' : ''}">Semestre 2</button></div>
      <div class="sumstrip" id="strip"></div>
      <div class="savebar"><span class="status ok" id="savestatus"></span><button class="btn sm secondary" id="addsubj">${icon('plus', 'sm')} Matière</button></div>
      <div class="formula small">${esc(noteFormula())}</div>
      <div id="cards">${S.subjects.map((s) => subjectCardHtml(s, sem)).join('') || `<div class="empty card">Aucune matière. Ajoute-en une avec le bouton « Matière ».</div>`}</div>`);
    $$('[data-sem]').forEach((b) => b.addEventListener('click', () => { S.semester = +b.dataset.sem; localStorage.setItem('sb_sem', S.semester); viewNotes(); }));
    $('#addsubj').addEventListener('click', () => subjectModal(null));
    $$('.subj').forEach((c) => refreshCard(c, sem)); refreshSummaryStrip(sem); setStatus();
    const cards = $('#cards');
    cards.addEventListener('input', (ev) => {
      const inp = ev.target.closest('.gin'); if (!inp) return;
      const p = C.parseGrade(inp.value), card = inp.closest('.subj');
      inp.classList.toggle('invalid', !p.ok); inp.title = p.ok ? '' : p.error;
      let hint = $('.err-inline', card);
      if (!p.ok) { if (!hint) { hint = document.createElement('div'); hint.className = 'err-inline small'; hint.style.cssText = 'color:var(--bad);margin-top:8px'; card.appendChild(hint); } hint.textContent = p.error; return; }
      if (hint) hint.remove();
      setGrade(+inp.dataset.sid, sem, inp.dataset.field, p.value);
      refreshCard(card, sem); refreshSummaryStrip(sem);
    });
    cards.addEventListener('focusout', (ev) => {
      const inp = ev.target.closest('.gin'); if (!inp) return;
      const p = C.parseGrade(inp.value);
      if (p.ok) { inp.value = p.value === null ? '' : fmt(p.value); inp.classList.remove('invalid'); const h = $('.err-inline', inp.closest('.subj')); if (h) h.remove(); }
    });
    cards.addEventListener('click', async (ev) => {
      const b = ev.target.closest('[data-act]'); if (!b) return;
      const card = b.closest('.subj'), s = S.subjects.find((x) => x.id === +card.dataset.card);
      if (b.dataset.act === 'edit') subjectModal(s);
      if (b.dataset.act === 'clear') {
        if (!(await confirmBox({ title: `Vider « ${s.name} » ?`, message: `Les 4 notes de ${SEM_LABEL(sem)} seront effacées.`, confirm: 'Vider', danger: true }))) return;
        try { await flushAll(); await api('POST', '/api/grades/clear', { subject_id: s.id, semester: sem }); s.grades[sem] = { d1: null, d2: null, d3: null, compo: null }; S.history = null; toast('Notes effacées', 'ok'); viewNotes(); } catch (e) { toast(e.message, 'err'); }
      }
    });
  }

  /* ====================================================== bulletin */
  function sheetHtml(p) {
    const n = (v) => (v === null || v === undefined ? '—' : fmt(v));
    const rows = p.rows.map((r) => `<tr><td>${esc(r.name)}</td>${[0, 1, 2].map((k) => `<td class="${r.dropped.includes(k) ? 'drop' : ''}">${r.d[k] === null ? '—' : r.dropped.includes(k) ? '(' + n(r.d[k]) + ')' : n(r.d[k])}</td>`).join('')}
      <td class="ret">${r.kept.length ? r.kept.map((k) => n(r.d[k])).join(' · ') : '—'}</td><td>${f2(r.dev_avg)}</td><td>${n(r.compo)}</td><td class="mv">${f2(r.avg)}${r.avg !== null && !r.complete ? '*' : ''}</td><td>${n(r.coef)}</td><td>${f2(r.points)}</td></tr>`).join('');
    const prov = p.rows.some((r) => r.avg !== null && !r.complete);
    const r = p.rules;
    return `<div class="sheet" id="sheet">
      <div class="sh-head"><div class="sh-logo"><span class="logo">${LOGO}</span><div><b style="font-size:1.1rem;color:var(--blue)">Sama Bulletin</b><div class="small muted" style="font-style:italic">${esc(SLOGAN)}</div></div></div>
        <div><h1>BULLETIN DE NOTES</h1><div class="sub">${esc(p.semester_label)} — Année scolaire ${esc(p.school_year)}</div></div></div>
      <div class="idbox"><div><small>Nom et prénom</small><b>${esc(p.student.full_name)}</b></div><div><small>Classe / Série</small><b>${esc(p.class_label)}</b></div>
        <div><small>Établissement</small><b>${esc(p.school)}</b></div><div><small>Rang</small><b>${p.rank_label ? esc(p.rank_label) : 'Non renseigné'}</b></div></div>
      <table><thead><tr><th>Matière</th><th>Dev. 1</th><th>Dev. 2</th><th>Dev. 3</th><th>Retenues</th><th>Moy. dev.</th><th>Compo.</th><th>Moyenne</th><th>Coef.</th><th>Points</th></tr></thead>
        <tbody>${rows}</tbody><tfoot><tr><td colspan="8" style="text-align:left;color:var(--blue)">TOTAL</td><td>${n(p.sum_coef)}</td><td>${f2(p.sum_points)}</td></tr></tfoot></table>
      <div class="sumrow"><div class="sbox main"><small>Moyenne générale</small><b>${p.general === null ? '—' : f2(p.general) + ' / 20'}</b></div>
        ${p.semester === 2 ? `<div class="sbox"><small>1er semestre</small><b style="color:var(--blue)">${f2(p.s1_general)}</b></div><div class="sbox"><small>Moyenne annuelle</small><b style="color:var(--blue)">${f2(p.annual)}</b></div>` : (p.rank_label ? `<div class="sbox"><small>Rang</small><b style="color:var(--blue)">${esc(p.rank_label)}</b></div>` : '')}</div>
      <div class="appr"><small>Appréciation générale</small>${esc(p.appreciation || '—')}</div>
      <div class="legend">Les notes entre parenthèses ne sont pas retenues dans la moyenne des devoirs. Moyenne de la matière = (moyenne des devoirs × ${fmt(r.wd)} + composition × ${fmt(r.wc)}) ÷ ${fmt(r.wd + r.wc)}. Moyenne générale = somme des (moyenne × coefficient) ÷ somme des coefficients.${prov ? ' * Moyenne provisoire : calculée avec les notes saisies.' : ''}</div>
      <div class="warnbox"><b>Document personnel — non officiel</b>Ce bulletin a été généré par l'élève avec Sama Bulletin à partir des notes qu'il a saisies. Il n'est pas un bulletin officiel délivré par un établissement scolaire et n'a aucune valeur administrative.</div>
      <div class="sh-foot"><span>Sama Bulletin — ${esc(SLOGAN)}</span><span>Généré le ${esc(p.generated_label)}</span></div></div>`;
  }

  function download(url) { const a = document.createElement('a'); a.href = url; a.download = ''; document.body.appendChild(a); a.click(); a.remove(); }

  async function viewBulletin() {
    const sem = S.semester, year = S.enrollment.school_year;
    $app.innerHTML = shell('bulletin', `<h1 class="page-title">Mon bulletin</h1><p class="page-sub">Génère ton bulletin du semestre en PDF, avec les devoirs retenus et tes moyennes.</p>
      <div class="seg"><button data-sem="1" class="${sem === 1 ? 'on' : ''}">Semestre 1</button><button data-sem="2" class="${sem === 2 ? 'on' : ''}">Semestre 2</button></div>
      <div id="bul"><div class="empty"><div class="spinner" style="margin:auto"></div></div></div>`);
    $$('[data-sem]').forEach((b) => b.addEventListener('click', () => { S.semester = +b.dataset.sem; localStorage.setItem('sb_sem', S.semester); viewBulletin(); }));
    let p;
    try { await flushAll(); p = await api('GET', `/api/bulletin?year=${year}&semester=${sem}`); }
    catch (e) { $('#bul').innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
    const m = (S.meta && S.meta[sem]) || {};
    const ok = p.general !== null;
    $('#bul').innerHTML = `
      ${ok ? '' : `<div class="card empty"><div class="ic">${icon('file')}</div>Aucune note pour le ${SEM_LABEL(sem)}.<br><a href="#/notes">Saisir mes notes</a> pour générer ton bulletin.</div>`}
      <div class="btnrow" style="margin-bottom:16px">
        <button class="btn" id="pdf" ${ok ? '' : 'disabled'}>${icon('download')} Télécharger PDF</button>
        <button class="btn secondary" id="view" ${ok ? '' : 'disabled'}>${icon('eye')} Consulter le PDF</button>
        <button class="btn secondary" id="print" ${ok ? '' : 'disabled'}>${icon('printer')} Imprimer</button>
        <button class="btn ghost" id="save" ${ok ? '' : 'disabled'}>${icon('save')} Conserver dans l'historique</button></div>
      <p class="scrollhint">Fais glisser l'aperçu de gauche à droite pour voir tout le tableau. Le PDF, lui, est en pleine page.</p>
      <div class="sheet-wrap" id="sheetbox">${sheetHtml(p)}</div>
      <section class="card" style="margin-top:16px"><h2>Informations facultatives du bulletin</h2>
        <div id="e" class="err hidden"></div>
        <div class="cols2"><div class="field"><label>Rang dans la classe</label><input class="input" id="rank" inputmode="numeric" placeholder="Ex. 5" value="${m.class_rank || ''}"></div>
        <div class="field"><label>Effectif de la classe</label><input class="input" id="size" inputmode="numeric" placeholder="Ex. 42" value="${m.class_size || ''}"></div></div>
        <p class="hint" style="margin:-6px 0 12px">Le rang n'apparaît sur le bulletin que si tu renseignes le rang et l'effectif.</p>
        <div class="field"><label>Appréciation générale</label><textarea class="input" id="appr" maxlength="400" placeholder="Laisse vide pour utiliser l'appréciation automatique">${esc(m.appreciation || '')}</textarea></div>
        <div class="row wrap"><button class="btn secondary sm" id="auto">Utiliser l'appréciation automatique</button><span class="status" id="metastatus"></span></div></section>`;
    const q = `year=${year}&semester=${sem}`;
    const refreshSheet = async () => { try { const np = await api('GET', `/api/bulletin?${q}`); $('#sheetbox').innerHTML = sheetHtml(np); p = np; } catch (e) { /* ignoré */ } };
    $('#pdf').addEventListener('click', async () => { await flushAll(); download(`/api/bulletin.pdf?${q}`); });
    $('#view').addEventListener('click', async () => { await flushAll(); window.open(`/api/bulletin.pdf?${q}&inline=1`, '_blank', 'noopener'); });
    $('#print').addEventListener('click', () => window.print());
    $('#save').addEventListener('click', async (ev) => {
      try { await busy(ev.currentTarget, async () => { await flushAll(); const r = await api('POST', '/api/bulletins', { school_year: year, semester: sem }); S.bulletins.unshift({ id: r.id, school_year: year, semester: sem, created_at: r.created_at }); S.history = null; }); toast('Bulletin conservé dans l\'historique', 'ok'); }
      catch (e) { toast(e.message, 'err'); }
    });
    let t;
    const saveMeta = () => {
      clearTimeout(t); const st = $('#metastatus'); st.textContent = 'Enregistrement…'; st.className = 'status';
      t = setTimeout(async () => {
        const er = $('#e'); er.classList.add('hidden');
        const rk = $('#rank').value.trim(), sz = $('#size').value.trim();
        const body = { semester: sem, class_rank: rk === '' ? null : Number(rk), class_size: sz === '' ? null : Number(sz), appreciation: $('#appr').value };
        if ((rk === '') !== (sz === '')) { st.textContent = ''; er.textContent = 'Renseigne à la fois le rang et l\'effectif (ou laisse les deux vides).'; er.classList.remove('hidden'); return; }
        try {
          await api('PUT', '/api/meta', body); S.meta[sem] = { class_rank: body.class_rank, class_size: body.class_size, appreciation: body.appreciation || null };
          st.className = 'status ok'; st.innerHTML = icon('check', 'sm') + ' Enregistré'; await refreshSheet();
        } catch (e) { st.textContent = ''; er.textContent = e.message; er.classList.remove('hidden'); }
      }, 700);
    };
    ['rank', 'size', 'appr'].forEach((id) => $('#' + id).addEventListener('input', saveMeta));
    $('#auto').addEventListener('click', () => { $('#appr').value = ''; saveMeta(); });
  }

  async function viewSavedBulletin(id) {
    $app.innerHTML = shell('history', `<a href="#/history" class="small">← Historique</a><h1 class="page-title" style="margin-top:6px">Bulletin conservé</h1><div id="bul"><div class="empty"><div class="spinner" style="margin:auto"></div></div></div>`);
    try {
      const p = await api('GET', `/api/bulletins/${id}`);
      $('#bul').innerHTML = `<p class="page-sub">Instantané enregistré le ${esc(p.generated_label)} : les chiffres ne changent pas même si tu modifies tes notes ensuite.</p>
        <div class="btnrow" style="margin-bottom:16px"><button class="btn" id="pdf">${icon('download')} Télécharger PDF</button><button class="btn secondary" id="print">${icon('printer')} Imprimer</button><button class="btn danger ghost" id="del">${icon('trash')} Supprimer</button></div>
        <div class="sheet-wrap">${sheetHtml(p)}</div>`;
      $('#pdf').addEventListener('click', () => download(`/api/bulletins/${id}.pdf`));
      $('#print').addEventListener('click', () => window.print());
      $('#del').addEventListener('click', async () => {
        if (!(await confirmBox({ title: 'Supprimer ce bulletin ?', message: 'Il sera retiré de ton historique. Tes notes ne sont pas affectées.', confirm: 'Supprimer', danger: true }))) return;
        try { await api('DELETE', `/api/bulletins/${id}`); S.bulletins = S.bulletins.filter((b) => b.id !== id); S.history = null; toast('Bulletin supprimé', 'ok'); location.hash = '#/history'; } catch (e) { toast(e.message, 'err'); }
      });
    } catch (e) { $('#bul').innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  }

  /* ====================================================== historique */
  function newYearModal() {
    const e = S.enrollment, used = S.years;
    const next = (() => { const y = parseInt(S.years[0], 10) + 1; return `${y}-${y + 1}`; })();
    modal(`<h2>Nouvelle année scolaire <button class="icon-btn" data-close aria-label="Fermer">${icon('x')}</button></h2><div id="e" class="err hidden"></div>
      <form id="nf" novalidate><div class="field"><label>Année scolaire</label><select class="input" name="year">${yearOptions(next, used)}</select></div>
      <div class="field"><label>Établissement</label><input class="input" name="school" maxlength="100" value="${esc(e.school)}" required></div>
      ${classPickerHtml(e.level, e.serie)}<button class="btn block" type="submit">Créer cette année</button></form>`, (m) => {
      bindClassPicker(m, e.level, e.serie);
      $('#nf', m).addEventListener('submit', async (ev) => {
        ev.preventDefault(); const f = ev.target;
        const lv = S.curriculum.levels.find((l) => l.id === f.level.value);
        const er = (msg) => { const el = $('#e', m); el.textContent = msg; el.classList.remove('hidden'); };
        if (lv.series.length && !f.serie.value) return er('Choisis ta série.');
        try { await flushAll(); applyState(await api('PUT', '/api/enrollment', { school: f.school.value, level: f.level.value, serie: f.serie.value || null, school_year: f.year.value })); closeModal(); toast('Nouvelle année créée', 'ok'); location.hash = '#/notes'; route(true); }
        catch (x) { er(x.message); }
      });
    });
  }

  async function viewHistory() {
    $app.innerHTML = shell('history', `<h1 class="page-title">Historique</h1><p class="page-sub">Retrouve tes années, tes moyennes, tes bulletins conservés et tes anciennes notes.</p><div id="hist"><div class="empty"><div class="spinner" style="margin:auto"></div></div></div>`);
    let h;
    try { await flushAll(); h = await loadHistory(true); } catch (e) { $('#hist').innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
    const cur = S.enrollment.school_year;
    const years = h.years.map((y) => `<section class="card year-card"><h3><span>${esc(y.school_year)} · ${esc(y.class_label)}<br><span class="small muted" style="font-weight:500">${esc(y.school)}</span></span>${y.school_year === cur ? '<span class="chip ok">Année en cours</span>' : ''}</h3>
      <div class="avgs"><div><small>1er semestre</small><b>${f2(y.s1)}</b></div><div><small>2e semestre</small><b>${f2(y.s2)}</b></div><div><small>Annuelle</small><b>${f2(y.annual)}</b></div></div>
      ${y.per_subject.some((s) => s.s1 !== null || s.s2 !== null) ? `<details><summary class="small" style="cursor:pointer;color:var(--blue);font-weight:700">Moyennes par matière</summary><div class="sheet-wrap"><table class="small" style="width:100%;border-collapse:collapse;margin-top:8px"><thead><tr style="text-align:left;color:var(--muted)"><th style="padding:6px 0">Matière</th><th>S1</th><th>S2</th><th>Année</th></tr></thead><tbody>${y.per_subject.map((s) => `<tr style="border-top:1px solid var(--line)"><td style="padding:7px 8px 7px 0;font-weight:600">${esc(s.name)}</td><td>${f2(s.s1)}</td><td>${f2(s.s2)}</td><td><b>${f2(s.annual)}</b></td></tr>`).join('')}</tbody></table></div></details>` : ''}
      <div class="row wrap" style="margin-top:12px">${y.school_year === cur ? `<a class="btn secondary sm" href="#/notes">Voir mes notes</a>` : `<button class="btn secondary sm" data-open="${esc(y.school_year)}">Ouvrir cette année</button>`}</div></section>`).join('');
    const buls = h.bulletins.map((b) => `<li><div class="grow"><div class="t">${SEM_LABEL(b.semester)} · ${esc(b.school_year)}</div><div class="d">${esc(b.class_label)} · moyenne ${f2(b.general)} · conservé le ${fmtDate(b.created_at)}</div></div>
      <a class="btn secondary sm" href="#/bulletin/saved/${b.id}">Consulter</a><button class="icon-btn" data-pdf="${b.id}" aria-label="Télécharger le PDF">${icon('download')}</button></li>`).join('');
    const log = h.log.map((l) => `<li><div class="grow"><div class="t">${esc(l.subject_name)} <span class="muted" style="font-weight:500">· ${FIELD_LABEL[l.field]} · S${l.semester} · ${esc(l.school_year)}</span></div><div class="d">${fmtDT(l.at)}</div></div>
      <span class="small">${l.old_value === null ? '—' : fmt(l.old_value)} → <b>${l.new_value === null ? 'supprimée' : fmt(l.new_value)}</b></span></li>`).join('');
    $('#hist').innerHTML = `<div class="btnrow" style="margin-bottom:16px"><button class="btn secondary" id="newyear">${icon('plus')} Nouvelle année scolaire</button></div>${years}
      <section class="card"><h2>Bulletins conservés</h2>${buls ? `<ul class="list">${buls}</ul>` : `<div class="empty small">Aucun bulletin conservé pour l'instant.<br>Depuis « Bulletin », utilise « Conserver dans l'historique ».</div>`}</section>
      <details class="card"><summary>Journal des notes (${h.log.length} dernières modifications) ${icon('chev', 'sm')}</summary>${log ? `<ul class="list">${log}</ul>` : '<div class="empty small">Aucune note saisie.</div>'}</details>`;
    $('#newyear').addEventListener('click', newYearModal);
    $$('[data-pdf]').forEach((b) => b.addEventListener('click', () => download(`/api/bulletins/${b.dataset.pdf}.pdf`)));
    $$('[data-open]').forEach((b) => b.addEventListener('click', async () => {
      try { applyState(await api('POST', '/api/active-year', { school_year: b.dataset.open })); toast(`Année ${b.dataset.open} ouverte`, 'ok'); location.hash = '#/notes'; route(true); } catch (e) { toast(e.message, 'err'); }
    }));
  }

  /* ====================================================== réglages */
  function classChangeModal(payload) {
    modal(`<h2>Changer de classe <button class="icon-btn" data-close aria-label="Fermer">${icon('x')}</button></h2><p class="muted" style="margin-bottom:12px">Que faire de tes matières actuelles ?</p>
      <div class="field"><label style="display:flex;gap:10px;align-items:flex-start;font-weight:500;color:inherit"><input type="radio" name="mode" value="merge" checked style="margin-top:4px"><span><b>Ajouter les matières manquantes</b> (recommandé)<br><span class="hint">Tu gardes tes matières et tes notes ; celles de la nouvelle classe sont ajoutées.</span></span></label></div>
      <div class="field"><label style="display:flex;gap:10px;align-items:flex-start;font-weight:500;color:inherit"><input type="radio" name="mode" value="keep" style="margin-top:4px"><span><b>Ne rien changer</b><br><span class="hint">Seul le nom de la classe est mis à jour.</span></span></label></div>
      <div class="field"><label style="display:flex;gap:10px;align-items:flex-start;font-weight:500;color:inherit"><input type="radio" name="mode" value="replace" style="margin-top:4px"><span><b>Remplacer toutes les matières</b><br><span class="hint" style="color:var(--bad)">Efface aussi toutes les notes de cette année.</span></span></label></div>
      <div class="row"><button class="btn ghost grow" data-close>Annuler</button><button class="btn grow" id="go">Valider</button></div>`, (m) => {
      $('#go', m).addEventListener('click', async () => {
        const mode = $('[name=mode]:checked', m).value;
        if (mode === 'replace' && !(await confirmBox({ title: 'Tout remplacer ?', message: 'Toutes les matières et toutes les notes de cette année seront supprimées.', confirm: 'Remplacer', danger: true }))) return;
        try { await flushAll(); applyState(await api('PUT', '/api/enrollment', { ...payload, mode })); closeModal(); toast('Classe modifiée', 'ok'); route(true); } catch (e) { toast(e.message, 'err'); }
      });
    });
  }

  function viewSettings() {
    const u = S.user, e = S.enrollment, r = e.rules;
    let photo = u.photo, photoChanged = false;
    const subjRows = S.subjects.map((s) => `<div class="sub-row"><div class="nm">${esc(s.name)}${s.placeholder ? '<span class="ph">à personnaliser</span>' : ''}</div><span class="coef">coef ${fmt(s.coef)}</span>
      <button class="icon-btn" data-edit="${s.id}" aria-label="Modifier ${esc(s.name)}">${icon('edit')}</button></div>`).join('');
    $app.innerHTML = shell('settings', `
      <h1 class="page-title">Réglages</h1><p class="page-sub">${esc(u.email)}</p>
      <section class="card set-sec"><h2>${icon('user')} Mon profil</h2><div id="e1" class="err hidden"></div>
        <form id="fp" novalidate>${photoHtml(u, photo)}
        <div class="cols2 stack-xs"><div class="field"><label>Prénom</label><input class="input" name="fn" maxlength="60" value="${esc(u.first_name)}" required></div><div class="field"><label>Nom</label><input class="input" name="ln" maxlength="60" value="${esc(u.last_name)}" required></div></div>
        <div class="field"><label>Établissement</label><input class="input" name="school" maxlength="100" value="${esc(e.school)}" required></div>
        <button class="btn" type="submit">Enregistrer mon profil</button></form></section>
      <section class="card set-sec"><h2>${icon('book')} Classe et année scolaire</h2><div id="e2" class="err hidden"></div>
        <form id="fc" novalidate>${classPickerHtml(e.level, e.serie)}<button class="btn" type="submit">Changer de classe</button></form>
        <hr style="border:0;border-top:1px solid var(--line);margin:16px 0">
        <div class="field"><label>Année scolaire affichée</label><select class="input" id="yr">${S.years.map((y) => `<option ${y === e.school_year ? 'selected' : ''}>${esc(y)}</option>`).join('')}</select><span class="hint">Chaque année a ses propres matières et notes. <a href="#" id="ny">Ajouter une nouvelle année</a></span></div></section>
      <section class="card set-sec"><h2>${icon('list')} Matières et coefficients</h2><p class="hint" style="margin-bottom:8px">Ajoute, renomme ou supprime des matières selon ton établissement. Les matières « à personnaliser » dépendent de ta filière.</p>
        ${subjRows || '<div class="empty small">Aucune matière.</div>'}
        <div class="row wrap" style="margin-top:12px"><button class="btn secondary sm" id="addsubj">${icon('plus', 'sm')} Ajouter une matière</button><button class="btn ghost sm" id="resetsubj">Rétablir les matières de la classe</button></div></section>
      <section class="card set-sec"><h2>${icon('sliders')} Règles de calcul</h2><div id="e3" class="err hidden"></div>
        <div class="formula" id="formula"></div>
        <form id="fr" novalidate><div class="cols2 stack-xs"><div class="field"><label>Poids de la moyenne des devoirs</label><input class="input" name="wd" inputmode="decimal" value="${esc(fmt(r.wd))}"></div>
        <div class="field"><label>Poids de la composition</label><input class="input" name="wc" inputmode="decimal" value="${esc(fmt(r.wc))}"></div></div>
        <div class="cols2 stack-xs"><div class="field"><label>Devoirs retenus (les meilleurs)</label><select class="input" name="keep"><option value="1" ${r.keep === 1 ? 'selected' : ''}>Le meilleur devoir</option><option value="2" ${r.keep === 2 ? 'selected' : ''}>Les 2 meilleurs (par défaut)</option><option value="3" ${r.keep === 3 ? 'selected' : ''}>Les 3 devoirs</option></select></div>
        <div class="field"><label>Objectif de moyenne (facultatif)</label><input class="input" name="obj" inputmode="decimal" placeholder="Ex. 14" value="${e.objective !== null ? esc(fmt(e.objective)) : ''}"></div></div>
        <div class="row wrap"><button class="btn" type="submit">Enregistrer les règles</button><button type="button" class="btn ghost" id="defrules">Valeurs par défaut</button></div></form></section>
      <section class="card set-sec"><h2>${icon('lock')} Mot de passe</h2><div id="e4" class="err hidden"></div>
        <form id="fw" novalidate>${pwField('cur', 'Mot de passe actuel')}${pwField('new1', 'Nouveau mot de passe (8 caractères minimum)', 'new-password')}${pwField('new2', 'Confirmer le nouveau mot de passe', 'new-password')}<button class="btn" type="submit">Changer mon mot de passe</button></form></section>
      <section class="card set-sec"><h2>${icon('download')} Mes données</h2><p class="hint" style="margin-bottom:12px">Tes notes sont privées et liées à ton compte. Tu peux les exporter ou supprimer ton compte à tout moment.</p>
        <div class="row wrap"><a class="btn secondary sm" href="/api/export" download>Exporter mes données</a><button class="btn danger ghost sm" id="delacc">Supprimer mon compte</button></div></section>
      <button class="btn ghost block" id="lo">${icon('logout')} Se déconnecter</button>`);
    bindToggles($app); bindPhoto($app, (d) => { photo = d; photoChanged = true; });
    bindClassPicker($('#fc'), e.level, e.serie);
    const err = (id, msg) => { const el = $('#' + id); if (msg) { el.textContent = msg; el.classList.remove('hidden'); } else el.classList.add('hidden'); };

    $('#fp').addEventListener('submit', async (ev) => {
      ev.preventDefault(); err('e1');
      const f = ev.target;
      try {
        await busy($('button[type=submit]', f), async () => {
          const body = { first_name: f.fn.value, last_name: f.ln.value }; if (photoChanged) body.photo = photo;
          await api('PUT', '/api/profile', body);
          applyState(await api('PUT', '/api/enrollment', { school: f.school.value, level: e.level, serie: e.serie, school_year: e.school_year, mode: 'keep' }));
        });
        toast('Profil enregistré', 'ok'); route(true);
      } catch (x) { err('e1', x.message); }
    });
    $('#fc').addEventListener('submit', async (ev) => {
      ev.preventDefault(); err('e2');
      const f = ev.target, lv = S.curriculum.levels.find((l) => l.id === f.level.value), serie = f.serie.value || null;
      if (lv.series.length && !serie) return err('e2', 'Choisis ta série.');
      if (lv.id === e.level && (serie || null) === (e.serie || null)) return toast('La classe est déjà celle-ci.');
      classChangeModal({ school: e.school, level: lv.id, serie: lv.series.length ? serie : null, school_year: e.school_year });
    });
    $('#yr').addEventListener('change', async (ev) => {
      try { await flushAll(); applyState(await api('POST', '/api/active-year', { school_year: ev.target.value })); toast('Année scolaire modifiée', 'ok'); route(true); } catch (x) { toast(x.message, 'err'); }
    });
    $('#ny').addEventListener('click', (ev) => { ev.preventDefault(); newYearModal(); });
    $$('[data-edit]').forEach((b) => b.addEventListener('click', () => subjectModal(S.subjects.find((s) => s.id === +b.dataset.edit))));
    $('#addsubj').addEventListener('click', () => subjectModal(null));
    $('#resetsubj').addEventListener('click', async () => {
      if (!(await confirmBox({ title: 'Rétablir les matières ?', message: `Tes matières seront remplacées par celles de ${e.class_label}. Toutes les notes de cette année seront effacées.`, confirm: 'Rétablir', danger: true }))) return;
      try { await flushAll(); applyState(await api('POST', '/api/subjects/reset', {})); toast('Matières rétablies', 'ok'); route(true); } catch (x) { toast(x.message, 'err'); }
    });
    const fr = $('#fr');
    const readRules = () => ({ wd: parseFloat(String(fr.wd.value).replace(',', '.')), wc: parseFloat(String(fr.wc.value).replace(',', '.')), keep: +fr.keep.value });
    const paintFormula = () => { const x = readRules(); $('#formula').textContent = (x.wd >= 0 && x.wc >= 0 && x.wd + x.wc > 0) ? `Moyenne de la matière = (moyenne des devoirs × ${fmt(x.wd)} + composition × ${fmt(x.wc)}) ÷ ${fmt(C.round2(x.wd + x.wc))}` : 'Règle invalide'; };
    paintFormula(); ['wd', 'wc', 'keep'].forEach((n) => fr[n].addEventListener('input', paintFormula));
    fr.addEventListener('submit', async (ev) => {
      ev.preventDefault(); err('e3');
      const x = readRules(), o = C.parseGrade(fr.obj.value);
      if (!(x.wd >= 0 && x.wd <= 10 && x.wc >= 0 && x.wc <= 10 && x.wd + x.wc > 0)) return err('e3', 'Les poids doivent être compris entre 0 et 10 (au moins un supérieur à 0).');
      if (!o.ok) return err('e3', 'Objectif : ' + o.error);
      try { await flushAll(); const res = await api('PUT', '/api/rules', { ...x, objective: o.value }); S.enrollment.rules = res.rules; S.enrollment.objective = res.objective; S.history = null; toast('Règles enregistrées', 'ok'); route(true); } catch (z) { err('e3', z.message); }
    });
    $('#defrules').addEventListener('click', () => { fr.wd.value = '1'; fr.wc.value = '2'; fr.keep.value = '2'; paintFormula(); });
    $('#fw').addEventListener('submit', async (ev) => {
      ev.preventDefault(); err('e4');
      if ($('#new1').value !== $('#new2').value) return err('e4', 'Les deux nouveaux mots de passe ne sont pas identiques.');
      try { await api('POST', '/api/password', { current: $('#cur').value, new: $('#new1').value }); ev.target.reset(); toast('Mot de passe modifié', 'ok'); } catch (z) { err('e4', z.message); }
    });
    $('#delacc').addEventListener('click', () => {
      modal(`<h2>Supprimer mon compte <button class="icon-btn" data-close aria-label="Fermer">${icon('x')}</button></h2><p class="muted" style="margin-bottom:12px">Toutes tes notes, tes années et tes bulletins seront définitivement supprimés. Cette action est irréversible.</p><div id="e" class="err hidden"></div>
        <form id="df">${pwField('dpw', 'Confirme avec ton mot de passe')}<button class="btn danger block" type="submit">Supprimer définitivement</button></form>`, (m) => {
        bindToggles(m);
        $('#df', m).addEventListener('submit', async (ev) => {
          ev.preventDefault();
          try { await api('DELETE', '/api/account', { password: $('#dpw', m).value }); closeModal(); S.user = null; S.enrollment = null; toast('Compte supprimé'); location.hash = '#/register'; route(true); }
          catch (z) { const el = $('#e', m); el.textContent = z.message; el.classList.remove('hidden'); }
        });
      });
    });
    $('#lo').addEventListener('click', logout);
  }

  /* ====================================================== routeur */
  const PUBLIC = ['/login', '/register', '/forgot', '/reset'];
  const VIEWS = { '/login': viewLogin, '/register': viewRegister, '/forgot': viewForgot, '/reset': viewReset, '/onboarding': viewOnboarding, '/dashboard': viewDashboard, '/notes': viewNotes, '/bulletin': viewBulletin, '/history': viewHistory, '/settings': viewSettings };
  let lastRendered = null;
  function route(force) {
    closeModal();
    const raw = location.hash.replace(/^#/, '') || '/';
    const [path, query] = raw.split('?');
    if (!S.user) { if (!PUBLIC.includes(path)) { location.hash = '#/login'; return; } }
    else if (!S.enrollment) { if (path !== '/onboarding') { location.hash = '#/onboarding'; return; } }
    else if (PUBLIC.includes(path) || path === '/onboarding' || path === '/') { location.hash = '#/dashboard'; return; }
    const key = path + '?' + (query || '');
    if (!force && key === lastRendered) return;
    lastRendered = key;
    window.scrollTo(0, 0);
    const saved = path.match(/^\/bulletin\/saved\/(\d+)$/);
    if (saved) return viewSavedBulletin(+saved[1]);
    const v = VIEWS[path];
    if (!v) { location.hash = S.user ? '#/dashboard' : '#/login'; return; }
    v(query);
  }
  window.addEventListener('hashchange', () => route(false));

  async function boot() {
    try {
      S.curriculum = await api('GET', '/api/curriculum');
      try { applyState(await api('GET', '/api/state')); } catch (e) { if (e.status !== 401) throw e; }
      const saved = parseInt(localStorage.getItem('sb_sem') || '', 10);
      if (saved === 1 || saved === 2) S.semester = saved;
      else if (S.enrollment && S.subjects.some((s) => s.grades[2] && Object.values(s.grades[2]).some((v) => v !== null))) S.semester = 2;
      route(true);
    } catch (e) {
      $app.innerHTML = authShell(`<div class="err">${esc(e.message)}</div><button class="btn block" onclick="location.reload()">Réessayer</button>`);
    }
  }
  boot();
})();
