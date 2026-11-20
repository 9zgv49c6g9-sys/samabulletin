/* Sama Bulletin — moteur de calcul côté navigateur (miroir exact de calc.py). */
(function (root) {
  'use strict';
  var DEV = ['d1', 'd2', 'd3'];

  function round2(x) {
    if (x === null || x === undefined) return null;
    return Math.floor(parseFloat((x * 100).toPrecision(12)) + 0.5) / 100;
  }

  function subjectResult(g, wd, wc, keep) {
    g = g || {};
    var d = DEV.map(function (k) { return g[k] === undefined ? null : g[k]; });
    var compo = g.compo === undefined ? null : g.compo;
    var present = [];
    d.forEach(function (v, i) { if (v !== null) present.push([i, v]); });
    var ordered = present.slice().sort(function (a, b) { return b[1] - a[1]; }); // tri stable
    var kept = ordered.slice(0, keep), dropped = ordered.slice(keep);
    var devRaw = kept.length ? kept.reduce(function (s, t) { return s + t[1]; }, 0) / kept.length : null;
    var parts = [];
    if (devRaw !== null) parts.push([devRaw, wd]);
    if (compo !== null) parts.push([compo, wc]);
    var tw = parts.reduce(function (s, p) { return s + p[1]; }, 0);
    var avg = tw > 0 ? round2(parts.reduce(function (s, p) { return s + p[0] * p[1]; }, 0) / tw) : null;
    return {
      d: d, compo: compo,
      kept: kept.map(function (t) { return t[0]; }).sort(),
      dropped: dropped.map(function (t) { return t[0]; }).sort(),
      devAvg: round2(devRaw), avg: avg,
      complete: present.length === 3 && compo !== null
    };
  }

  function gradesFor(subject, sem) {
    var g = subject.grades || {};
    return g[sem] || g[String(sem)] || {};
  }

  function semesterSummary(subjects, sem, rules) {
    var rows = subjects.map(function (s) {
      var r = subjectResult(gradesFor(s, sem), rules.wd, rules.wc, rules.keep);
      r.id = s.id; r.name = s.name; r.coef = s.coef;
      r.points = r.avg !== null ? round2(r.avg * s.coef) : null;
      return r;
    });
    var scored = rows.filter(function (r) { return r.avg !== null; });
    var sumCoef = scored.reduce(function (s, r) { return s + r.coef; }, 0);
    var general = sumCoef > 0 ? round2(scored.reduce(function (s, r) { return s + r.avg * r.coef; }, 0) / sumCoef) : null;
    var best = null, worst = null;
    if (scored.length) {
      var bi = 0, wi = 0;
      scored.forEach(function (r, i) {
        if (r.avg > scored[bi].avg) bi = i;
        if (r.avg < scored[wi].avg) wi = i;
      });
      best = scored[bi].id;
      worst = scored.length > 1 ? scored[wi].id : null;
    }
    return { rows: rows, general: general, sumCoef: sumCoef, scored: scored.length, total: rows.length, bestId: best, worstId: worst };
  }

  function yearSummary(subjects, rules) {
    var s1 = semesterSummary(subjects, 1, rules), s2 = semesterSummary(subjects, 2, rules);
    var annual = (s1.general !== null && s2.general !== null) ? round2((s1.general + s2.general) / 2) : null;
    return { s1: s1, s2: s2, annual: annual };
  }

  function appreciation(g) {
    if (g === null || g === undefined) return '';
    if (g >= 16) return 'Excellent semestre. Résultats remarquables, à poursuivre avec la même rigueur.';
    if (g >= 14) return 'Très bon semestre. Les résultats sont très satisfaisants.';
    if (g >= 12) return 'Bon semestre. Les efforts portent leurs fruits, il faut continuer.';
    if (g >= 10) return 'Résultats satisfaisants. Davantage de régularité permettrait de progresser.';
    if (g >= 8) return 'Résultats fragiles. Un travail plus soutenu est nécessaire.';
    return "Résultats insuffisants. Il faut redoubler d'efforts et demander de l'aide dans les matières difficiles.";
  }

  /* Saisie : accepte "12,5" ou "12.5". Retourne {ok, value} ; value null = case vide. */
  function parseGrade(text) {
    var t = String(text === null || text === undefined ? '' : text).trim().replace(',', '.');
    if (t === '') return { ok: true, value: null };
    if (!/^\d{1,2}(\.\d{1,2})?$/.test(t)) return { ok: false, error: 'Saisis un nombre entre 0 et 20 (ex. 14,5).' };
    var v = parseFloat(t);
    if (v < 0 || v > 20) return { ok: false, error: 'La note doit être comprise entre 0 et 20.' };
    return { ok: true, value: v };
  }

  function fmt(x, digits) {
    if (x === null || x === undefined || isNaN(x)) return '—';
    var s = digits === undefined ? String(Math.round(x * 100) / 100) : x.toFixed(digits);
    return s.replace('.', ',');
  }

  var api = { round2: round2, subjectResult: subjectResult, semesterSummary: semesterSummary, yearSummary: yearSummary, appreciation: appreciation, parseGrade: parseGrade, fmt: fmt };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.SBCalc = api;
})(typeof window !== 'undefined' ? window : globalThis);
