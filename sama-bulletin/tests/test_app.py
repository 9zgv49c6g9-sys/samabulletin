"""Tests de bout en bout de Sama Bulletin (serveur réel, base temporaire).

Lancer :  python3 -m unittest discover -s tests -v
"""
import http.cookiejar
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="sama_test_")
os.environ["SAMA_DATA_DIR"] = _tmp
os.environ["SAMA_DB"] = os.path.join(_tmp, "test.db")
os.environ.pop("SMTP_HOST", None)

import calc  # noqa: E402
import server  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


class Client:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None, headers=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json", "X-Requested-With": "SamaBulletin"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            r = self.op.open(req)
            payload = r.read()
            return r.status, (payload if raw else json.loads(payload)), r
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload), e
            except Exception:
                return e.code, payload, e


def make_student(c, email, first="Awa", last="Diop", level="terminale", serie="S2", year="2026-2027"):
    s, r, _ = c.call("POST", "/api/register", {"first_name": first, "last_name": last, "email": email, "password": "motdepasse1"})
    assert s == 201, r
    s, r, _ = c.call("PUT", "/api/enrollment", {"school": "Lycée Demba Diop de Mbour", "level": level, "serie": serie, "school_year": year})
    assert s == 200, r
    return r


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.init_db()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.srv.daemon_threads = True
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        server.LIMITER.hits.clear()

    # ---------- calculs (exemples du cahier des charges)
    def test_exemple_trois_devoirs(self):
        r = calc.subject_result({"d1": 12, "d2": 16, "d3": 18, "compo": None})
        self.assertEqual(r["dev_avg"], 17.0)
        self.assertEqual(r["kept"], [1, 2])
        self.assertEqual(r["dropped"], [0])

    def test_exemple_moyenne_matiere(self):
        r = calc.subject_result({"d1": 12, "d2": 16, "d3": 18, "compo": 15})
        self.assertEqual(r["avg"], 15.67)
        self.assertTrue(r["complete"])

    def test_note_manquante_pas_zero(self):
        r = calc.subject_result({"d1": 10, "d2": None, "d3": 16, "compo": None})
        self.assertEqual(r["dev_avg"], 13.0)
        r = calc.subject_result({"d1": 14, "d2": None, "d3": None, "compo": None})
        self.assertEqual(r["avg"], 14.0)
        r = calc.subject_result({"d1": None, "d2": None, "d3": None, "compo": None})
        self.assertIsNone(r["avg"])

    def test_zero_est_une_vraie_note(self):
        r = calc.subject_result({"d1": 0, "d2": 0, "d3": 10, "compo": 0})
        self.assertEqual(r["dev_avg"], 5.0)
        self.assertEqual(r["avg"], 1.67)  # (5 × 1 + 0 × 2) ÷ 3

    def test_moyenne_generale_ignore_matieres_vides(self):
        subs = [
            {"id": 1, "name": "A", "coef": 2, "grades": {1: {"d1": 10, "d2": 10, "d3": 10, "compo": 10}}},
            {"id": 2, "name": "B", "coef": 3, "grades": {1: {"d1": 16, "d2": 16, "d3": 16, "compo": 16}}},
            {"id": 3, "name": "C", "coef": 5, "grades": {}},
        ]
        s = calc.semester_summary(subs, 1)
        self.assertEqual(s["general"], 13.6)  # (10*2+16*3)/5
        self.assertEqual(s["scored"], 2)
        self.assertEqual(s["best_id"], 2)
        self.assertEqual(s["worst_id"], 1)

    def test_ponderation_modifiable(self):
        r = calc.subject_result({"d1": 12, "d2": 16, "d3": 18, "compo": 15}, wd=1, wc=1)
        self.assertEqual(r["avg"], 16.0)
        r = calc.subject_result({"d1": 12, "d2": 16, "d3": 18, "compo": 15}, wd=1, wc=2, keep=3)
        self.assertEqual(r["dev_avg"], 15.33)

    def test_arrondi_demi_vers_le_haut(self):
        self.assertEqual(calc.round2(2.675), 2.68)
        self.assertEqual(calc.round2(15.665), 15.67)
        self.assertEqual(calc.round2(15.6666667), 15.67)
        self.assertEqual(calc.round2(10.004), 10.0)

    def test_js_et_python_donnent_les_memes_resultats(self):
        rnd = random.Random(42)
        cases = []
        for _ in range(400):
            def n():
                return None if rnd.random() < 0.2 else round(rnd.uniform(0, 20), rnd.choice([0, 1, 2]))
            subs = [{"id": i, "name": f"M{i}", "coef": rnd.choice([0.5, 1, 1.5, 2, 3, 4]),
                     "grades": {"1": {"d1": n(), "d2": n(), "d3": n(), "compo": n()}, "2": {"d1": n(), "d2": n(), "d3": n(), "compo": n()}}} for i in range(rnd.randint(1, 8))]
            rules = {"wd": rnd.choice([1, 1, 2, 0.5]), "wc": rnd.choice([2, 1, 3, 0]), "keep": rnd.choice([1, 2, 2, 3])}
            if rules["wd"] + rules["wc"] == 0:
                rules["wc"] = 1
            cases.append({"subs": subs, "rules": rules})
        js = """const C=require(process.argv[1]+'/static/calc.js');const cases=JSON.parse(require('fs').readFileSync(0,'utf8'));
        console.log(JSON.stringify(cases.map(c=>{const y=C.yearSummary(c.subs,c.rules);return [y.s1.general,y.s2.general,y.annual,y.s1.rows.map(r=>[r.avg,r.devAvg,r.kept,r.dropped]),y.s2.rows.map(r=>r.points)]})))"""
        out = subprocess.run(["node", "-e", js, str(ROOT)], input=json.dumps(cases), capture_output=True, text=True, check=True).stdout
        js_res = json.loads(out)
        for c, jr in zip(cases, js_res):
            y = calc.year_summary(c["subs"], c["rules"]["wd"], c["rules"]["wc"], c["rules"]["keep"])
            py = [y["s1"]["general"], y["s2"]["general"], y["annual"],
                  [[r["avg"], r["dev_avg"], r["kept"], r["dropped"]] for r in y["s1"]["rows"]],
                  [r["points"] for r in y["s2"]["rows"]]]
            self.assertEqual(py, jr)

    # ---------- comptes et sécurité
    def test_inscription_connexion_deconnexion(self):
        c = Client(self.base)
        s, r, _ = c.call("GET", "/api/state")
        self.assertEqual(s, 401)
        st = make_student(c, "awa@example.com")
        self.assertEqual(st["enrollment"]["class_label"], "Terminale S2")
        self.assertEqual(len(st["subjects"]), 9)
        s, r, _ = c.call("POST", "/api/logout", {})
        self.assertEqual(s, 200)
        self.assertEqual(c.call("GET", "/api/state")[0], 401)
        s, r, _ = c.call("POST", "/api/login", {"email": "AWA@example.com", "password": "motdepasse1"})
        self.assertEqual(s, 200)
        self.assertEqual(r["enrollment"]["school"], "Lycée Demba Diop de Mbour")
        c2 = Client(self.base)
        self.assertEqual(c2.call("POST", "/api/login", {"email": "awa@example.com", "password": "mauvais"})[0], 401)

    def test_validation_inscription(self):
        c = Client(self.base)
        self.assertEqual(c.call("POST", "/api/register", {"first_name": "A", "last_name": "B", "email": "pasunemail", "password": "motdepasse1"})[0], 400)
        self.assertEqual(c.call("POST", "/api/register", {"first_name": "A", "last_name": "B", "email": "ok@example.com", "password": "court"})[0], 400)
        self.assertEqual(c.call("POST", "/api/register", {"first_name": "", "last_name": "B", "email": "ok@example.com", "password": "motdepasse1"})[0], 400)
        self.assertEqual(c.call("POST", "/api/register", {"first_name": "A", "last_name": "B", "email": "dup@example.com", "password": "motdepasse1"})[0], 201)
        self.assertEqual(Client(self.base).call("POST", "/api/register", {"first_name": "A", "last_name": "B", "email": "DUP@example.com", "password": "motdepasse1"})[0], 409)

    def test_mot_de_passe_stocke_hache(self):
        import sqlite3
        c = Client(self.base)
        make_student(c, "hash@example.com")
        row = sqlite3.connect(os.environ["SAMA_DB"]).execute("SELECT password_hash FROM users WHERE email='hash@example.com'").fetchone()[0]
        self.assertTrue(row.startswith("scrypt$"))
        self.assertNotIn("motdepasse1", row)

    def test_csrf_entete_obligatoire_et_origine(self):
        c = Client(self.base)
        req = urllib.request.Request(self.base + "/api/login", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 403)
        s, r, _ = c.call("POST", "/api/login", {"email": "x@y.zz", "password": "x"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(s, 403)

    def test_limitation_tentatives_connexion(self):
        c = Client(self.base)
        make_student(c, "brute@example.com")
        codes = [Client(self.base).call("POST", "/api/login", {"email": "brute@example.com", "password": "faux"})[0] for _ in range(10)]
        self.assertEqual(codes[:8], [401] * 8)
        self.assertEqual(codes[8], 429)

    def test_reinitialisation_mot_de_passe(self):
        c = Client(self.base)
        make_student(c, "oubli@example.com")
        s, r, _ = Client(self.base).call("POST", "/api/forgot", {"email": "oubli@example.com"})
        self.assertEqual(s, 200)
        s2, r2, _ = Client(self.base).call("POST", "/api/forgot", {"email": "inconnu@example.com"})
        self.assertEqual((s2, r2["message"]), (200, r["message"]))  # pas d'énumération de comptes
        outbox = sorted(Path(_tmp, "outbox").glob("*.txt"))
        mails = [p.read_text() for p in outbox if "oubli@example.com" in p.read_text()]
        self.assertEqual(len(mails), 1)
        token = re.search(r"token=([\w-]+)", mails[0]).group(1)
        self.assertEqual(Client(self.base).call("POST", "/api/reset", {"token": "faux", "password": "nouveaumdp1"})[0], 400)
        self.assertEqual(Client(self.base).call("POST", "/api/reset", {"token": token, "password": "court"})[0], 400)
        self.assertEqual(Client(self.base).call("POST", "/api/reset", {"token": token, "password": "nouveaumdp1"})[0], 200)
        self.assertEqual(Client(self.base).call("POST", "/api/reset", {"token": token, "password": "autremdp12"})[0], 400)  # usage unique
        self.assertEqual(c.call("GET", "/api/state")[0], 401)  # anciennes sessions révoquées
        self.assertEqual(Client(self.base).call("POST", "/api/login", {"email": "oubli@example.com", "password": "motdepasse1"})[0], 401)
        self.assertEqual(Client(self.base).call("POST", "/api/login", {"email": "oubli@example.com", "password": "nouveaumdp1"})[0], 200)

    def test_changement_mot_de_passe(self):
        c = Client(self.base)
        make_student(c, "chg@example.com")
        self.assertEqual(c.call("POST", "/api/password", {"current": "faux", "new": "nouveaumdp1"})[0], 400)
        self.assertEqual(c.call("POST", "/api/password", {"current": "motdepasse1", "new": "nouveaumdp1"})[0], 200)
        self.assertEqual(Client(self.base).call("POST", "/api/login", {"email": "chg@example.com", "password": "nouveaumdp1"})[0], 200)

    # ---------- notes
    def _grade(self, c, sid, sem, field, value):
        return c.call("PUT", "/api/grades", {"subject_id": sid, "semester": sem, "field": field, "value": value})

    def test_saisie_validation_et_persistance(self):
        c = Client(self.base)
        st = make_student(c, "notes@example.com")
        sid = st["subjects"][1]["id"]
        for bad in (21, -1, "abc", 20.5, float("nan")):
            if bad != bad:
                continue
            self.assertEqual(self._grade(c, sid, 1, "d1", bad)[0], 400, bad)
        self.assertEqual(self._grade(c, sid, 3, "d1", 10)[0], 400)
        self.assertEqual(self._grade(c, sid, 1, "d9", 10)[0], 400)
        for f, v in (("d1", 12), ("d2", "16,5"), ("d3", 18), ("compo", 15)):
            self.assertEqual(self._grade(c, sid, 1, f, v)[0], 200)
        # semestre 2 totalement indépendant
        self.assertEqual(self._grade(c, sid, 2, "d1", 5)[0], 200)
        st = c.call("GET", "/api/state")[1]
        g = {s["id"]: s for s in st["subjects"]}[sid]["grades"]
        self.assertEqual(g["1"], {"d1": 12.0, "d2": 16.5, "d3": 18.0, "compo": 15.0})
        self.assertEqual(g["2"], {"d1": 5.0, "d2": None, "d3": None, "compo": None})
        # suppression / correction
        self.assertEqual(self._grade(c, sid, 1, "d2", None)[0], 200)
        self.assertEqual(self._grade(c, sid, 1, "d1", 13)[0], 200)
        g = {s["id"]: s for s in c.call("GET", "/api/state")[1]["subjects"]}[sid]["grades"]["1"]
        self.assertEqual((g["d1"], g["d2"]), (13.0, None))
        self.assertTrue(c.call("GET", "/api/state")[1]["log"])  # journal des dernières notes

    def test_matieres_ajout_modification_suppression(self):
        c = Client(self.base)
        st = make_student(c, "mat@example.com", level="seconde", serie="L")
        self.assertTrue(any(s["placeholder"] for s in st["subjects"]))  # matière complémentaire à personnaliser
        s, r, _ = c.call("POST", "/api/subjects", {"name": "Wolof", "coef": 2})
        self.assertEqual(s, 201)
        self.assertEqual(c.call("POST", "/api/subjects", {"name": "wolof", "coef": 1})[0], 409)
        self.assertEqual(c.call("POST", "/api/subjects", {"name": "X", "coef": 0})[0], 400)
        sid = r["subject"]["id"]
        self.assertEqual(c.call("PUT", f"/api/subjects/{sid}", {"name": "Arabe", "coef": 1.5})[0], 200)
        ph = [s for s in st["subjects"] if s["placeholder"]][0]
        self.assertEqual(c.call("PUT", f"/api/subjects/{ph['id']}", {"name": "Éducation artistique", "coef": 1})[0], 200)
        self.assertEqual(c.call("DELETE", f"/api/subjects/{sid}")[0], 200)
        names = [s["name"] for s in c.call("GET", "/api/state")[1]["subjects"]]
        self.assertIn("Éducation artistique", names)
        self.assertNotIn("Arabe", names)

    def test_changement_de_classe_et_nouvelle_annee(self):
        c = Client(self.base)
        make_student(c, "classe@example.com", level="3e", serie=None)
        st = c.call("PUT", "/api/enrollment", {"school": "CEM Test", "level": "terminale", "serie": "L2", "school_year": "2026-2027", "mode": "merge"})[1]
        self.assertEqual(st["enrollment"]["class_label"], "Terminale L2")
        names = [s["name"] for s in st["subjects"]]
        self.assertIn("Philosophie", names)
        self.assertEqual(c.call("PUT", "/api/enrollment", {"school": "X", "level": "terminale", "school_year": "2026-2027"})[0], 400)  # série requise
        st = c.call("PUT", "/api/enrollment", {"school": "CEM Test", "level": "6e", "school_year": "2027-2028"})[1]
        self.assertEqual(st["years"], ["2027-2028", "2026-2027"])
        self.assertEqual(len(st["subjects"]), 8)
        self.assertEqual(c.call("POST", "/api/active-year", {"school_year": "2026-2027"})[1]["enrollment"]["level"], "terminale")
        self.assertEqual(c.call("POST", "/api/active-year", {"school_year": "2030-2031"})[0], 404)
        self.assertEqual(c.call("PUT", "/api/enrollment", {"school": "X", "level": "6e", "school_year": "2026-2028"})[0], 400)

    def test_regles_de_calcul(self):
        c = Client(self.base)
        make_student(c, "regles@example.com")
        self.assertEqual(c.call("PUT", "/api/rules", {"wd": 0, "wc": 0, "keep": 2})[0], 400)
        self.assertEqual(c.call("PUT", "/api/rules", {"wd": 1, "wc": 1, "keep": 5})[0], 400)
        self.assertEqual(c.call("PUT", "/api/rules", {"wd": 1, "wc": 3, "keep": 3, "objective": 14})[0], 200)
        e = c.call("GET", "/api/state")[1]["enrollment"]
        self.assertEqual((e["rules"], e["objective"]), ({"wd": 1.0, "wc": 3.0, "keep": 3}, 14.0))

    # ---------- bulletins PDF et historique
    def test_bulletin_pdf_et_historique(self):
        c = Client(self.base)
        st = make_student(c, "pdf@example.com", first="Aïssatou", last="Ndiaye")
        subs = st["subjects"]
        data = [(12, 16, 18, 15), (14, 14, 9, 12), (8, 11, 10, 9.5)]
        for s, row in zip(subs, data):
            for f, v in zip(("d1", "d2", "d3", "compo"), row):
                self._grade(c, s["id"], 1, f, v)
        self.assertEqual(c.call("PUT", "/api/meta", {"semester": 1, "class_rank": 5, "class_size": 42, "appreciation": "Élève sérieuse : félicitations."})[0], 200)
        self.assertEqual(c.call("PUT", "/api/meta", {"semester": 1, "class_rank": 50, "class_size": 42})[0], 400)
        self.assertEqual(c.call("PUT", "/api/meta", {"semester": 1, "class_rank": 5, "class_size": None})[0], 400)
        s, b, _ = c.call("GET", "/api/bulletin?semester=1")
        self.assertEqual(s, 200)
        self.assertEqual(b["rows"][0]["avg"], 15.67)
        self.assertEqual(b["rank_label"], "5e sur 42")
        self.assertEqual(b["student"]["full_name"], "NDIAYE Aïssatou")
        # moyenne générale vérifiée à la main
        avgs = [r["avg"] for r in b["rows"] if r["avg"] is not None]
        self.assertEqual(len(avgs), 3)
        self.assertAlmostEqual(b["general"], calc.round2(sum(avgs) / 3), places=2)
        s, pdf, r = c.call("GET", "/api/bulletin.pdf?semester=1", raw=True)
        self.assertEqual(s, 200)
        self.assertTrue(pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF"))
        self.assertIn("attachment", r.headers["Content-Disposition"])
        Path(_tmp, "bulletin_s1.pdf").write_bytes(pdf)
        # S2 : moyenne annuelle
        for f, v in zip(("d1", "d2", "d3", "compo"), (16, 17, 15, 18)):
            self._grade(c, subs[0]["id"], 2, f, v)
        b2 = c.call("GET", "/api/bulletin?semester=2")[1]
        self.assertEqual(b2["s1_general"], b["general"] if False else c.call("GET", "/api/bulletin?semester=1")[1]["general"])
        self.assertIsNotNone(b2["annual"])
        Path(_tmp, "bulletin_s2.pdf").write_bytes(c.call("GET", "/api/bulletin.pdf?semester=2", raw=True)[1])
        # conservation dans l'historique : instantané figé
        s, saved, _ = c.call("POST", "/api/bulletins", {"semester": 1})
        self.assertEqual(s, 201)
        self._grade(c, subs[0]["id"], 1, "compo", 0)  # modification après coup
        snap = c.call("GET", f"/api/bulletins/{saved['id']}")[1]
        self.assertEqual(snap["rows"][0]["avg"], 15.67)  # inchangé
        self.assertEqual(c.call("GET", f"/api/bulletins/{saved['id']}.pdf", raw=True)[1][:4], b"%PDF")
        h = c.call("GET", "/api/history")[1]
        self.assertEqual(len(h["bulletins"]), 1)
        self.assertEqual(h["years"][0]["school_year"], "2026-2027")
        self.assertTrue(h["log"])
        self.assertEqual(c.call("DELETE", f"/api/bulletins/{saved['id']}")[0], 200)
        self.assertEqual(c.call("GET", f"/api/bulletins/{saved['id']}")[0], 404)

    def test_bulletin_vide_refuse_a_la_conservation(self):
        c = Client(self.base)
        make_student(c, "vide@example.com")
        self.assertEqual(c.call("POST", "/api/bulletins", {"semester": 1})[0], 400)

    # ---------- confidentialité
    def test_isolation_entre_eleves(self):
        a, b = Client(self.base), Client(self.base)
        sa = make_student(a, "a-priv@example.com")
        make_student(b, "b-priv@example.com")
        sid = sa["subjects"][0]["id"]
        self._grade(a, sid, 1, "d1", 17)
        s, r, _ = a.call("POST", "/api/bulletins", {"semester": 1})
        bid = r["id"]
        self.assertEqual(self._grade(b, sid, 1, "d1", 1)[0], 404)
        self.assertEqual(b.call("PUT", f"/api/subjects/{sid}", {"name": "Piraté", "coef": 1})[0], 404)
        self.assertEqual(b.call("DELETE", f"/api/subjects/{sid}")[0], 404)
        self.assertEqual(b.call("GET", f"/api/bulletins/{bid}")[0], 404)
        self.assertEqual(b.call("GET", f"/api/bulletins/{bid}.pdf", raw=True)[0], 404)
        self.assertEqual(b.call("DELETE", f"/api/bulletins/{bid}")[0], 404)
        self.assertEqual(b.call("POST", "/api/grades/clear", {"subject_id": sid, "semester": 1})[0], 404)
        self.assertNotIn(sid, [s["id"] for s in b.call("GET", "/api/state")[1]["subjects"]])
        self.assertEqual(a.call("GET", "/api/state")[1]["subjects"][0]["grades"]["1"]["d1"], 17.0)

    def test_export_et_suppression_de_compte(self):
        c = Client(self.base)
        make_student(c, "gdpr@example.com")
        s, raw, _ = c.call("GET", "/api/export", raw=True)
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(raw)["user"]["email"], "gdpr@example.com")
        self.assertEqual(c.call("DELETE", "/api/account", {"password": "faux"})[0], 400)
        self.assertEqual(c.call("DELETE", "/api/account", {"password": "motdepasse1"})[0], 200)
        self.assertEqual(Client(self.base).call("POST", "/api/login", {"email": "gdpr@example.com", "password": "motdepasse1"})[0], 401)

    def test_photo_validation(self):
        c = Client(self.base)
        make_student(c, "photo@example.com")
        ok = "data:image/png;base64,iVBORw0KGgo="
        self.assertEqual(c.call("PUT", "/api/profile", {"first_name": "A", "last_name": "B", "photo": ok})[0], 200)
        self.assertEqual(c.call("PUT", "/api/profile", {"first_name": "A", "last_name": "B", "photo": "javascript:alert(1)"})[0], 400)
        self.assertEqual(c.call("PUT", "/api/profile", {"first_name": "A", "last_name": "B", "photo": "data:image/svg+xml;base64,AAAA"})[0], 400)

    # ---------- fichiers statiques
    def test_statique_et_entetes(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn("Sama Bulletin", r.read().decode())
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        for bad in ("/../server.py", "/..%2fserver.py", "/data/sama_bulletin.db"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(self.base + bad)
            self.assertEqual(cm.exception.code, 404)

    def test_curriculum_complet(self):
        c = Client(self.base)
        levels = c.call("GET", "/api/curriculum")[1]["levels"]
        total = sum(max(1, len(l["series"])) for l in levels)
        self.assertEqual(total, 22)
        for l in levels:
            for serie in (l["series"] or [None]):
                key = f"{l['id']}:{serie}" if serie else l["id"]
                self.assertIn(key, server.CURRICULUM["subjects"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
