#!/usr/bin/env python3
"""Sama Bulletin — serveur web + API + base SQLite (bibliothèque standard uniquement).

Lancer :  python3 server.py            (http://localhost:8000)
Variables d'environnement : voir README.md
"""
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import smtplib
import sqlite3
import sys
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import calc
import pdf as pdfgen

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("SAMA_DATA_DIR", BASE / "data"))
DB_PATH = Path(os.environ.get("SAMA_DB", DATA_DIR / "sama_bulletin.db"))
STATIC = BASE / "static"
BASE_URL = os.environ.get("SAMA_BASE_URL", "http://localhost:8000").rstrip("/")
SESSION_DAYS = 30
RESET_MINUTES = 60
MAX_BODY = 1_000_000
CURRICULUM = json.loads((BASE / "curriculum.json").read_text("utf-8"))
LEVELS = {l["id"]: l for l in CURRICULUM["levels"]}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  photo TEXT,
  active_year TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reset_tokens(
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS enrollments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  school_year TEXT NOT NULL,
  school TEXT NOT NULL,
  level TEXT NOT NULL,
  serie TEXT,
  wd REAL NOT NULL DEFAULT 1,
  wc REAL NOT NULL DEFAULT 2,
  keep INTEGER NOT NULL DEFAULT 2,
  objective REAL,
  UNIQUE(user_id, school_year)
);
CREATE TABLE IF NOT EXISTS subjects(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  enrollment_id INTEGER NOT NULL REFERENCES enrollments(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  coef REAL NOT NULL DEFAULT 1,
  position INTEGER NOT NULL DEFAULT 0,
  placeholder INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS grades(
  subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
  semester INTEGER NOT NULL CHECK(semester IN (1,2)),
  d1 REAL, d2 REAL, d3 REAL, compo REAL,
  updated_at TEXT,
  PRIMARY KEY(subject_id, semester)
);
CREATE TABLE IF NOT EXISTS note_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  enrollment_id INTEGER NOT NULL REFERENCES enrollments(id) ON DELETE CASCADE,
  subject_name TEXT NOT NULL,
  semester INTEGER NOT NULL,
  field TEXT NOT NULL,
  old_value REAL,
  new_value REAL,
  at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS semester_meta(
  enrollment_id INTEGER NOT NULL REFERENCES enrollments(id) ON DELETE CASCADE,
  semester INTEGER NOT NULL CHECK(semester IN (1,2)),
  class_rank INTEGER,
  class_size INTEGER,
  appreciation TEXT,
  PRIMARY KEY(enrollment_id, semester)
);
CREATE TABLE IF NOT EXISTS bulletins(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  school_year TEXT NOT NULL,
  semester INTEGER NOT NULL,
  snapshot TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subjects_enr ON subjects(enrollment_id);
CREATE INDEX IF NOT EXISTS idx_log_user ON note_log(user_id, id);
CREATE INDEX IF NOT EXISTS idx_bull_user ON bulletins(user_id, id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ----------------------------------------------------------------- base de données
def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def in_future(**kw):
    return (datetime.now(timezone.utc) + timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------- sécurité
def hash_password(pw):
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${h.hex()}"


def verify_password(pw, stored):
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc_h = hashlib.scrypt(pw.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc_h, bytes.fromhex(h))
    except Exception:
        return False


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def sha256(s):
    return hashlib.sha256(s.encode()).hexdigest()


class RateLimiter:
    def __init__(self):
        self.hits, self.lock = {}, threading.Lock()

    def check(self, key, limit, window):
        now = time.time()
        with self.lock:
            q = [t for t in self.hits.get(key, []) if now - t < window]
            if len(q) >= limit:
                self.hits[key] = q
                return False
            q.append(now)
            self.hits[key] = q
            if len(self.hits) > 5000:
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < window}
            return True

    def reset(self, key):
        with self.lock:
            self.hits.pop(key, None)


LIMITER = RateLimiter()
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
YEAR_RE = re.compile(r"^(\d{4})-(\d{4})$")


def send_mail(to, subject, body):
    host = os.environ.get("SMTP_HOST")
    if host:
        def _send():
            try:
                msg = EmailMessage()
                msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "no-reply@sama-bulletin.local"))
                msg["To"], msg["Subject"] = to, subject
                msg.set_content(body)
                port = int(os.environ.get("SMTP_PORT", "587"))
                if port == 465:
                    s = smtplib.SMTP_SSL(host, port, timeout=20)
                else:
                    s = smtplib.SMTP(host, port, timeout=20)
                    s.starttls()
                if os.environ.get("SMTP_USER"):
                    s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
                s.send_message(msg)
                s.quit()
            except Exception as e:  # pragma: no cover
                print(f"[mail] échec d'envoi : {e}", file=sys.stderr)
        threading.Thread(target=_send, daemon=True).start()
    else:
        out = DATA_DIR / "outbox"
        out.mkdir(parents=True, exist_ok=True)
        f = out / f"{int(time.time() * 1000)}-{secrets.token_hex(3)}.txt"
        f.write_text(f"À : {to}\nObjet : {subject}\n\n{body}\n", "utf-8")
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
        print(f"[mail] SMTP non configuré : message écrit dans {f}", file=sys.stderr)


# ----------------------------------------------------------------- validation
def s_field(body, key, label, maxlen=80, required=True):
    v = body.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise ApiError(400, f"{label} est obligatoire.")
        return None
    if not isinstance(v, str):
        raise ApiError(400, f"{label} est invalide.")
    v = " ".join(v.split())
    if len(v) > maxlen:
        raise ApiError(400, f"{label} est trop long ({maxlen} caractères maximum).")
    return v


def num_field(v, label, lo, hi, allow_none=False, integer=False):
    if v is None or v == "":
        if allow_none:
            return None
        raise ApiError(400, f"{label} est obligatoire.")
    if isinstance(v, bool):
        raise ApiError(400, f"{label} est invalide.")
    try:
        x = float(str(v).replace(",", ".")) if isinstance(v, str) else float(v)
    except ValueError:
        raise ApiError(400, f"{label} doit être un nombre.")
    if x != x or x in (float("inf"), float("-inf")):
        raise ApiError(400, f"{label} doit être un nombre.")
    if x < lo or x > hi:
        raise ApiError(400, f"{label} doit être compris entre {lo:g} et {hi:g}.")
    if integer and x != int(x):
        raise ApiError(400, f"{label} doit être un nombre entier.")
    return int(x) if integer else round(x, 2)


def check_year(y):
    m = YEAR_RE.match(y or "")
    if not m or int(m.group(2)) != int(m.group(1)) + 1 or not (2000 <= int(m.group(1)) <= 2100):
        raise ApiError(400, "Année scolaire invalide (format attendu : 2026-2027).")
    return y


def check_password_strength(pw, label="Le mot de passe"):
    if not isinstance(pw, str) or len(pw) < 8:
        raise ApiError(400, f"{label} doit contenir au moins 8 caractères.")
    if len(pw) > 128:
        raise ApiError(400, f"{label} est trop long.")


def check_photo(photo):
    if photo in (None, ""):
        return None
    if not isinstance(photo, str) or not re.match(r"^data:image/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+$", photo):
        raise ApiError(400, "Photo invalide (JPEG, PNG ou WebP).")
    if len(photo) > 400_000:
        raise ApiError(400, "Photo trop lourde.")
    return photo


# ----------------------------------------------------------------- accès aux données
def level_series(level):
    l = LEVELS.get(level)
    if not l:
        raise ApiError(400, "Classe inconnue.")
    return l


def curriculum_key(level, serie):
    l = level_series(level)
    if l["series"]:
        if serie not in l["series"]:
            raise ApiError(400, "Série obligatoire pour cette classe.")
        return f"{level}:{serie}"
    return level


def seed_subjects(conn, enr_id, level, serie, only_missing=False):
    items = CURRICULUM["subjects"][curriculum_key(level, serie)]
    have = {r["name"].lower() for r in conn.execute("SELECT name FROM subjects WHERE enrollment_id=?", (enr_id,))}
    pos = conn.execute("SELECT COALESCE(MAX(position),-1)+1 FROM subjects WHERE enrollment_id=?", (enr_id,)).fetchone()[0]
    for it in items:
        if only_missing and it["name"].lower() in have:
            continue
        conn.execute("INSERT INTO subjects(enrollment_id,name,coef,position,placeholder) VALUES(?,?,1,?,?)",
                     (enr_id, it["name"], pos, 1 if it.get("placeholder") else 0))
        pos += 1


def get_enrollment(conn, user_id, year):
    return conn.execute("SELECT * FROM enrollments WHERE user_id=? AND school_year=?", (user_id, year)).fetchone()


def owned_subject(conn, user_id, sid):
    row = conn.execute("""SELECT s.*, e.user_id, e.id AS enr_id FROM subjects s
                          JOIN enrollments e ON e.id=s.enrollment_id WHERE s.id=? AND e.user_id=?""", (sid, user_id)).fetchone()
    if not row:
        raise ApiError(404, "Matière introuvable.")
    return row


def load_subjects(conn, enr_id):
    subs = []
    rows = conn.execute("SELECT * FROM subjects WHERE enrollment_id=? ORDER BY position, id", (enr_id,)).fetchall()
    for s in rows:
        grades = {1: {"d1": None, "d2": None, "d3": None, "compo": None}, 2: {"d1": None, "d2": None, "d3": None, "compo": None}}
        for g in conn.execute("SELECT * FROM grades WHERE subject_id=?", (s["id"],)):
            grades[g["semester"]] = {k: g[k] for k in ("d1", "d2", "d3", "compo")}
        subs.append({"id": s["id"], "name": s["name"], "coef": s["coef"], "placeholder": bool(s["placeholder"]), "position": s["position"], "grades": grades})
    return subs


def rules_of(enr):
    return {"wd": enr["wd"], "wc": enr["wc"], "keep": enr["keep"]}


def enr_public(enr):
    lv = LEVELS[enr["level"]]
    return {"id": enr["id"], "school_year": enr["school_year"], "school": enr["school"], "level": enr["level"], "serie": enr["serie"],
            "class_label": calc.class_label(lv["label"], enr["serie"]), "rules": rules_of(enr), "objective": enr["objective"]}


def user_public(u):
    return {"id": u["id"], "email": u["email"], "first_name": u["first_name"], "last_name": u["last_name"],
            "photo": u["photo"], "active_year": u["active_year"]}


def meta_of(conn, enr_id):
    out = {1: {"class_rank": None, "class_size": None, "appreciation": None}, 2: {"class_rank": None, "class_size": None, "appreciation": None}}
    for m in conn.execute("SELECT * FROM semester_meta WHERE enrollment_id=?", (enr_id,)):
        out[m["semester"]] = {"class_rank": m["class_rank"], "class_size": m["class_size"], "appreciation": m["appreciation"]}
    return out


def build_state(conn, user):
    u = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    years = [r["school_year"] for r in conn.execute("SELECT school_year FROM enrollments WHERE user_id=? ORDER BY school_year DESC", (u["id"],))]
    st = {"user": user_public(u), "years": years, "enrollment": None, "subjects": [], "meta": {}, "log": [], "bulletins": []}
    enr = get_enrollment(conn, u["id"], u["active_year"]) if u["active_year"] else None
    if enr:
        st["enrollment"] = enr_public(enr)
        st["subjects"] = load_subjects(conn, enr["id"])
        st["meta"] = meta_of(conn, enr["id"])
        st["log"] = [dict(r) for r in conn.execute(
            "SELECT subject_name,semester,field,old_value,new_value,at FROM note_log WHERE user_id=? AND enrollment_id=? ORDER BY id DESC LIMIT 8", (u["id"], enr["id"]))]
    st["bulletins"] = [dict(r) for r in conn.execute(
        "SELECT id,school_year,semester,created_at FROM bulletins WHERE user_id=? ORDER BY id DESC", (u["id"],))]
    return st


# ----------------------------------------------------------------- bulletin
def semester_label(sem):
    return "1er semestre" if sem == 1 else "2e semestre"


def build_bulletin(conn, user, year, sem):
    enr = get_enrollment(conn, user["id"], year)
    if not enr:
        raise ApiError(404, "Aucune donnée pour cette année scolaire.")
    subs = load_subjects(conn, enr["id"])
    r = rules_of(enr)
    ys = calc.year_summary(subs, r["wd"], r["wc"], r["keep"])
    summ = ys["s1"] if sem == 1 else ys["s2"]
    meta = meta_of(conn, enr["id"])[sem]
    rank_label = None
    if meta["class_rank"] and meta["class_size"]:
        rk = meta["class_rank"]
        rank_label = f"{rk}{'er' if rk == 1 else 'e'} sur {meta['class_size']}"
    custom = meta["appreciation"]
    now = now_iso()
    lv = LEVELS[enr["level"]]
    return {
        "student": {"first_name": user["first_name"], "last_name": user["last_name"], "full_name": f"{user['last_name'].upper()} {user['first_name']}"},
        "school": enr["school"], "class_label": calc.class_label(lv["label"], enr["serie"]), "school_year": year,
        "semester": sem, "semester_label": semester_label(sem), "rules": r,
        "rows": [{k: row[k] for k in ("id", "name", "coef", "d", "compo", "kept", "dropped", "dev_avg", "avg", "complete", "points")} for row in summ["rows"]],
        "general": summ["general"], "sum_coef": summ["sum_coef"], "sum_points": summ["sum_points"],
        "rank_label": rank_label, "class_rank": meta["class_rank"], "class_size": meta["class_size"],
        "s1_general": ys["s1"]["general"] if sem == 2 else None,
        "annual": ys["annual"] if sem == 2 else None,
        "appreciation": custom if custom else calc.appreciation(summ["general"]),
        "appreciation_custom": bool(custom),
        "generated_at": now, "generated_label": calc.fr_date(now),
    }


# ----------------------------------------------------------------- API
class Ctx:
    def __init__(self, handler, conn, user, body, query):
        self.h, self.conn, self.user, self.body, self.query = handler, conn, user, body, query

    def q(self, key, default=None):
        v = self.query.get(key)
        return v[0] if v else default


def require_user(ctx):
    if not ctx.user:
        raise ApiError(401, "Non connecté.")
    return ctx.user


def start_session(ctx, user_id):
    token = secrets.token_urlsafe(32)
    ctx.conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)", (sha256(token), user_id, in_future(days=SESSION_DAYS)))
    ctx.conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
    ctx.h.set_cookie = token


def api_register(ctx):
    b = ctx.body
    first, last = s_field(b, "first_name", "Le prénom", 60), s_field(b, "last_name", "Le nom", 60)
    email = s_field(b, "email", "L'adresse e-mail", 254).lower()
    if not EMAIL_RE.match(email):
        raise ApiError(400, "Adresse e-mail invalide.")
    check_password_strength(b.get("password"))
    if not LIMITER.check(("reg", ctx.h.ip), 20, 3600):
        raise ApiError(429, "Trop de tentatives. Réessaie plus tard.")
    if ctx.conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise ApiError(409, "Un compte existe déjà avec cette adresse e-mail.")
    cur = ctx.conn.execute("INSERT INTO users(email,password_hash,first_name,last_name,created_at) VALUES(?,?,?,?,?)",
                           (email, hash_password(b["password"]), first, last, now_iso()))
    start_session(ctx, cur.lastrowid)
    ctx.user = ctx.conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return 201, build_state(ctx.conn, ctx.user)


def api_login(ctx):
    email = (ctx.body.get("email") or "").strip().lower()
    pw = ctx.body.get("password") or ""
    if not isinstance(email, str) or not isinstance(pw, str) or not email or not pw:
        raise ApiError(400, "E-mail et mot de passe obligatoires.")
    key = ("login", ctx.h.ip, email)
    if not LIMITER.check(key, 8, 600):
        raise ApiError(429, "Trop de tentatives de connexion. Réessaie dans quelques minutes.")
    u = ctx.conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    ok = verify_password(pw, u["password_hash"] if u else _DUMMY_HASH)
    if not u or not ok:
        raise ApiError(401, "E-mail ou mot de passe incorrect.")
    LIMITER.reset(key)
    start_session(ctx, u["id"])
    ctx.user = u
    return 200, build_state(ctx.conn, u)


def api_logout(ctx):
    token = ctx.h.session_token()
    if token:
        ctx.conn.execute("DELETE FROM sessions WHERE token_hash=?", (sha256(token),))
    ctx.h.clear_cookie = True
    return 200, {"ok": True}


def api_state(ctx):
    require_user(ctx)
    return 200, build_state(ctx.conn, ctx.user)


def api_forgot(ctx):
    email = (ctx.body.get("email") or "").strip().lower() if isinstance(ctx.body.get("email"), str) else ""
    if not EMAIL_RE.match(email):
        raise ApiError(400, "Adresse e-mail invalide.")
    if not LIMITER.check(("forgot", ctx.h.ip), 5, 3600):
        raise ApiError(429, "Trop de demandes. Réessaie plus tard.")
    u = ctx.conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if u:
        token = secrets.token_urlsafe(32)
        ctx.conn.execute("DELETE FROM reset_tokens WHERE user_id=?", (u["id"],))
        ctx.conn.execute("INSERT INTO reset_tokens(token_hash,user_id,expires_at) VALUES(?,?,?)", (sha256(token), u["id"], in_future(minutes=RESET_MINUTES)))
        link = f"{BASE_URL}/#/reset?token={token}"
        ctx.conn.commit()
        send_mail(email, "Sama Bulletin — réinitialisation du mot de passe",
                  f"Bonjour {u['first_name']},\n\nPour choisir un nouveau mot de passe, ouvre ce lien (valable {RESET_MINUTES} minutes) :\n{link}\n\n"
                  "Si tu n'es pas à l'origine de cette demande, ignore ce message : ton mot de passe ne changera pas.\n\nSama Bulletin")
    return 200, {"ok": True, "message": "Si un compte existe avec cette adresse, un e-mail contenant un lien de réinitialisation vient d'être envoyé."}


def api_reset(ctx):
    token, pw = ctx.body.get("token"), ctx.body.get("password")
    check_password_strength(pw, "Le nouveau mot de passe")
    if not isinstance(token, str) or not token:
        raise ApiError(400, "Lien invalide.")
    if not LIMITER.check(("reset", ctx.h.ip), 20, 3600):
        raise ApiError(429, "Trop de tentatives.")
    row = ctx.conn.execute("SELECT * FROM reset_tokens WHERE token_hash=?", (sha256(token),)).fetchone()
    if not row or row["used"] or row["expires_at"] < now_iso():
        raise ApiError(400, "Ce lien est invalide ou a expiré. Refais une demande de réinitialisation.")
    ctx.conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(pw), row["user_id"]))
    ctx.conn.execute("UPDATE reset_tokens SET used=1 WHERE token_hash=?", (row["token_hash"],))
    ctx.conn.execute("DELETE FROM sessions WHERE user_id=?", (row["user_id"],))
    return 200, {"ok": True}


def api_profile(ctx):
    u = require_user(ctx)
    b = ctx.body
    first, last = s_field(b, "first_name", "Le prénom", 60), s_field(b, "last_name", "Le nom", 60)
    photo = u["photo"]
    if "photo" in b:
        photo = check_photo(b["photo"])
    ctx.conn.execute("UPDATE users SET first_name=?, last_name=?, photo=? WHERE id=?", (first, last, photo, u["id"]))
    return 200, {"ok": True}


def api_password(ctx):
    u = require_user(ctx)
    if not verify_password(ctx.body.get("current") or "", u["password_hash"]):
        raise ApiError(400, "Le mot de passe actuel est incorrect.")
    check_password_strength(ctx.body.get("new"), "Le nouveau mot de passe")
    ctx.conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(ctx.body["new"]), u["id"]))
    tok = ctx.h.session_token()
    ctx.conn.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (u["id"], sha256(tok) if tok else ""))
    return 200, {"ok": True}


def api_enrollment(ctx):
    """Crée ou modifie la scolarité d'une année (onboarding, changement de classe, nouvelle année)."""
    u = require_user(ctx)
    b = ctx.body
    school = s_field(b, "school", "Le nom de l'établissement", 100)
    level = b.get("level")
    level_series(level)
    serie = b.get("serie") or None
    curriculum_key(level, serie if LEVELS[level]["series"] else None)
    if not LEVELS[level]["series"]:
        serie = None
    year = check_year(b.get("school_year"))
    mode = b.get("mode") or "merge"
    if mode not in ("merge", "replace", "keep"):
        raise ApiError(400, "Mode invalide.")
    enr = get_enrollment(ctx.conn, u["id"], year)
    if not enr:
        cur = ctx.conn.execute("INSERT INTO enrollments(user_id,school_year,school,level,serie) VALUES(?,?,?,?,?)", (u["id"], year, school, level, serie))
        seed_subjects(ctx.conn, cur.lastrowid, level, serie)
    else:
        ctx.conn.execute("UPDATE enrollments SET school=?, level=?, serie=? WHERE id=?", (school, level, serie, enr["id"]))
        changed = (enr["level"], enr["serie"]) != (level, serie)
        if changed and mode == "replace":
            ctx.conn.execute("DELETE FROM subjects WHERE enrollment_id=?", (enr["id"],))
            seed_subjects(ctx.conn, enr["id"], level, serie)
        elif changed and mode == "merge":
            seed_subjects(ctx.conn, enr["id"], level, serie, only_missing=True)
    ctx.conn.execute("UPDATE users SET active_year=? WHERE id=?", (year, u["id"]))
    return 200, build_state(ctx.conn, ctx.conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone())


def api_active_year(ctx):
    u = require_user(ctx)
    year = check_year(ctx.body.get("school_year"))
    if not get_enrollment(ctx.conn, u["id"], year):
        raise ApiError(404, "Cette année scolaire n'existe pas encore.")
    ctx.conn.execute("UPDATE users SET active_year=? WHERE id=?", (year, u["id"]))
    return 200, build_state(ctx.conn, ctx.conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone())


def active_enr(ctx):
    u = require_user(ctx)
    enr = get_enrollment(ctx.conn, u["id"], u["active_year"]) if u["active_year"] else None
    if not enr:
        raise ApiError(409, "Termine d'abord la configuration de ton profil.")
    return enr


def api_rules(ctx):
    enr = active_enr(ctx)
    b = ctx.body
    wd = num_field(b.get("wd"), "Le poids des devoirs", 0, 10)
    wc = num_field(b.get("wc"), "Le poids de la composition", 0, 10)
    if wd + wc <= 0:
        raise ApiError(400, "Au moins un des deux poids doit être supérieur à 0.")
    keep = num_field(b.get("keep"), "Le nombre de devoirs retenus", 1, 3, integer=True)
    obj = num_field(b.get("objective"), "L'objectif", 0, 20, allow_none=True)
    ctx.conn.execute("UPDATE enrollments SET wd=?, wc=?, keep=?, objective=? WHERE id=?", (wd, wc, keep, obj, enr["id"]))
    return 200, {"rules": {"wd": wd, "wc": wc, "keep": keep}, "objective": obj}


def api_subject_create(ctx):
    enr = active_enr(ctx)
    name = s_field(ctx.body, "name", "Le nom de la matière", 80)
    coef = num_field(ctx.body.get("coef", 1), "Le coefficient", 0.25, 20)
    if ctx.conn.execute("SELECT 1 FROM subjects WHERE enrollment_id=? AND lower(name)=lower(?)", (enr["id"], name)).fetchone():
        raise ApiError(409, "Cette matière existe déjà.")
    pos = ctx.conn.execute("SELECT COALESCE(MAX(position),-1)+1 FROM subjects WHERE enrollment_id=?", (enr["id"],)).fetchone()[0]
    cur = ctx.conn.execute("INSERT INTO subjects(enrollment_id,name,coef,position) VALUES(?,?,?,?)", (enr["id"], name, coef, pos))
    return 201, {"subject": {"id": cur.lastrowid, "name": name, "coef": coef, "placeholder": False, "position": pos,
                             "grades": {1: {"d1": None, "d2": None, "d3": None, "compo": None}, 2: {"d1": None, "d2": None, "d3": None, "compo": None}}}}


def api_subject_update(ctx, sid):
    u = require_user(ctx)
    s = owned_subject(ctx.conn, u["id"], sid)
    name = s_field(ctx.body, "name", "Le nom de la matière", 80)
    coef = num_field(ctx.body.get("coef"), "Le coefficient", 0.25, 20)
    if ctx.conn.execute("SELECT 1 FROM subjects WHERE enrollment_id=? AND lower(name)=lower(?) AND id<>?", (s["enrollment_id"], name, sid)).fetchone():
        raise ApiError(409, "Cette matière existe déjà.")
    ctx.conn.execute("UPDATE subjects SET name=?, coef=?, placeholder=0 WHERE id=?", (name, coef, sid))
    return 200, {"ok": True, "name": name, "coef": coef}


def api_subject_delete(ctx, sid):
    u = require_user(ctx)
    owned_subject(ctx.conn, u["id"], sid)
    ctx.conn.execute("DELETE FROM subjects WHERE id=?", (sid,))
    return 200, {"ok": True}


def api_subjects_reset(ctx):
    enr = active_enr(ctx)
    ctx.conn.execute("DELETE FROM subjects WHERE enrollment_id=?", (enr["id"],))
    seed_subjects(ctx.conn, enr["id"], enr["level"], enr["serie"])
    return 200, build_state(ctx.conn, ctx.user)


def api_grade(ctx):
    u = require_user(ctx)
    b = ctx.body
    s = owned_subject(ctx.conn, u["id"], b.get("subject_id"))
    sem = b.get("semester")
    if sem not in (1, 2):
        raise ApiError(400, "Semestre invalide.")
    field = b.get("field")
    if field not in ("d1", "d2", "d3", "compo"):
        raise ApiError(400, "Champ invalide.")
    label = {"d1": "Le devoir 1", "d2": "Le devoir 2", "d3": "Le devoir 3", "compo": "La composition"}[field]
    val = num_field(b.get("value"), label, 0, 20, allow_none=True)
    ctx.conn.execute("INSERT OR IGNORE INTO grades(subject_id,semester) VALUES(?,?)", (s["id"], sem))
    old = ctx.conn.execute(f"SELECT {field} FROM grades WHERE subject_id=? AND semester=?", (s["id"], sem)).fetchone()[0]
    if old != val:
        ctx.conn.execute(f"UPDATE grades SET {field}=?, updated_at=? WHERE subject_id=? AND semester=?", (val, now_iso(), s["id"], sem))
        ctx.conn.execute("INSERT INTO note_log(user_id,enrollment_id,subject_name,semester,field,old_value,new_value,at) VALUES(?,?,?,?,?,?,?,?)",
                         (u["id"], s["enr_id"], s["name"], sem, field, old, val, now_iso()))
        ctx.conn.execute("DELETE FROM note_log WHERE user_id=? AND id NOT IN (SELECT id FROM note_log WHERE user_id=? ORDER BY id DESC LIMIT 500)", (u["id"], u["id"]))
    return 200, {"ok": True, "value": val}


def api_grades_clear(ctx):
    u = require_user(ctx)
    s = owned_subject(ctx.conn, u["id"], ctx.body.get("subject_id"))
    sem = ctx.body.get("semester")
    if sem not in (1, 2):
        raise ApiError(400, "Semestre invalide.")
    ctx.conn.execute("UPDATE grades SET d1=NULL,d2=NULL,d3=NULL,compo=NULL,updated_at=? WHERE subject_id=? AND semester=?", (now_iso(), s["id"], sem))
    return 200, {"ok": True}


def api_meta(ctx):
    enr = active_enr(ctx)
    b = ctx.body
    sem = b.get("semester")
    if sem not in (1, 2):
        raise ApiError(400, "Semestre invalide.")
    rank = num_field(b.get("class_rank"), "Le rang", 1, 500, allow_none=True, integer=True)
    size = num_field(b.get("class_size"), "L'effectif", 1, 500, allow_none=True, integer=True)
    if (rank is None) != (size is None):
        raise ApiError(400, "Renseigne à la fois le rang et l'effectif de la classe (ou laisse les deux vides).")
    if rank and size and rank > size:
        raise ApiError(400, "Le rang ne peut pas dépasser l'effectif de la classe.")
    appr = b.get("appreciation")
    if appr is not None and not isinstance(appr, str):
        raise ApiError(400, "Appréciation invalide.")
    appr = appr.strip()[:400] if appr and appr.strip() else None
    ctx.conn.execute("""INSERT INTO semester_meta(enrollment_id,semester,class_rank,class_size,appreciation) VALUES(?,?,?,?,?)
                        ON CONFLICT(enrollment_id,semester) DO UPDATE SET class_rank=excluded.class_rank, class_size=excluded.class_size, appreciation=excluded.appreciation""",
                     (enr["id"], sem, rank, size, appr))
    return 200, {"ok": True}


def _year_sem(ctx):
    u = require_user(ctx)
    year = ctx.q("year") or u["active_year"]
    if not year:
        raise ApiError(409, "Termine d'abord la configuration de ton profil.")
    sem = ctx.q("semester", "1")
    if sem not in ("1", "2"):
        raise ApiError(400, "Semestre invalide.")
    return u, check_year(year), int(sem)


def api_bulletin(ctx):
    u, year, sem = _year_sem(ctx)
    return 200, build_bulletin(ctx.conn, u, year, sem)


def api_bulletin_pdf(ctx):
    u, year, sem = _year_sem(ctx)
    payload = build_bulletin(ctx.conn, u, year, sem)
    return "pdf", (pdfgen.render_bulletin(payload), pdf_name(payload))


def pdf_name(p):
    base = f"Bulletin_{p['semester_label'].replace(' ', '_')}_{p['school_year']}_{p['student']['last_name']}_{p['student']['first_name']}"
    ascii_base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_.-]", "", ascii_base.replace(" ", "_")) + ".pdf"


def api_bulletin_save(ctx):
    u = require_user(ctx)
    year = check_year(ctx.body.get("school_year") or u["active_year"])
    sem = ctx.body.get("semester")
    if sem not in (1, 2):
        raise ApiError(400, "Semestre invalide.")
    payload = build_bulletin(ctx.conn, u, year, sem)
    if payload["general"] is None:
        raise ApiError(400, "Saisis au moins une note avant de conserver un bulletin.")
    cur = ctx.conn.execute("INSERT INTO bulletins(user_id,school_year,semester,snapshot,created_at) VALUES(?,?,?,?,?)",
                           (u["id"], year, sem, json.dumps(payload, ensure_ascii=False), now_iso()))
    return 201, {"id": cur.lastrowid, "school_year": year, "semester": sem, "created_at": now_iso()}


def owned_bulletin(ctx, bid):
    u = require_user(ctx)
    row = ctx.conn.execute("SELECT * FROM bulletins WHERE id=? AND user_id=?", (bid, u["id"])).fetchone()
    if not row:
        raise ApiError(404, "Bulletin introuvable.")
    return row


def api_bulletin_get(ctx, bid):
    return 200, json.loads(owned_bulletin(ctx, bid)["snapshot"])


def api_bulletin_get_pdf(ctx, bid):
    p = json.loads(owned_bulletin(ctx, bid)["snapshot"])
    return "pdf", (pdfgen.render_bulletin(p), pdf_name(p))


def api_bulletin_delete(ctx, bid):
    owned_bulletin(ctx, bid)
    ctx.conn.execute("DELETE FROM bulletins WHERE id=?", (bid,))
    return 200, {"ok": True}


def api_history(ctx):
    u = require_user(ctx)
    years = []
    for enr in ctx.conn.execute("SELECT * FROM enrollments WHERE user_id=? ORDER BY school_year DESC", (u["id"],)).fetchall():
        subs = load_subjects(ctx.conn, enr["id"])
        r = rules_of(enr)
        ys = calc.year_summary(subs, r["wd"], r["wc"], r["keep"])
        e = enr_public(enr)
        years.append({**e, "subjects": len(subs), "s1": ys["s1"]["general"], "s2": ys["s2"]["general"], "annual": ys["annual"],
                      "per_subject": ys["per_subject"]})
    bulletins = [dict(r) for r in ctx.conn.execute("SELECT id,school_year,semester,created_at,snapshot FROM bulletins WHERE user_id=? ORDER BY id DESC", (u["id"],))]
    for b in bulletins:
        snap = json.loads(b.pop("snapshot"))
        b["general"], b["class_label"], b["school"] = snap["general"], snap["class_label"], snap["school"]
    log = [dict(r) for r in ctx.conn.execute(
        """SELECT l.subject_name,l.semester,l.field,l.old_value,l.new_value,l.at,e.school_year FROM note_log l
           JOIN enrollments e ON e.id=l.enrollment_id WHERE l.user_id=? ORDER BY l.id DESC LIMIT 60""", (u["id"],))]
    return 200, {"years": years, "bulletins": bulletins, "log": log}


def api_export(ctx):
    u = require_user(ctx)
    out = {"exported_at": now_iso(), "user": user_public(ctx.conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()), "years": []}
    out["user"].pop("photo", None)
    for enr in ctx.conn.execute("SELECT * FROM enrollments WHERE user_id=? ORDER BY school_year", (u["id"],)).fetchall():
        out["years"].append({**enr_public(enr), "subjects": load_subjects(ctx.conn, enr["id"]), "meta": meta_of(ctx.conn, enr["id"])})
    out["bulletins"] = [{"id": r["id"], "created_at": r["created_at"], "bulletin": json.loads(r["snapshot"])} for r in
                        ctx.conn.execute("SELECT * FROM bulletins WHERE user_id=? ORDER BY id", (u["id"],))]
    return "json_download", (json.dumps(out, ensure_ascii=False, indent=1).encode("utf-8"), "sama-bulletin-mes-donnees.json")


def api_delete_account(ctx):
    u = require_user(ctx)
    if not verify_password(ctx.body.get("password") or "", u["password_hash"]):
        raise ApiError(400, "Mot de passe incorrect.")
    ctx.conn.execute("DELETE FROM users WHERE id=?", (u["id"],))
    ctx.h.clear_cookie = True
    return 200, {"ok": True}


ROUTES = [
    ("POST", r"/api/register", api_register, False),
    ("POST", r"/api/login", api_login, False),
    ("POST", r"/api/logout", api_logout, False),
    ("POST", r"/api/forgot", api_forgot, False),
    ("POST", r"/api/reset", api_reset, False),
    ("GET", r"/api/state", api_state, False),
    ("GET", r"/api/curriculum", lambda ctx: (200, {"levels": CURRICULUM["levels"]}), False),
    ("PUT", r"/api/profile", api_profile, False),
    ("POST", r"/api/password", api_password, False),
    ("PUT", r"/api/enrollment", api_enrollment, False),
    ("POST", r"/api/active-year", api_active_year, False),
    ("PUT", r"/api/rules", api_rules, False),
    ("POST", r"/api/subjects", api_subject_create, False),
    ("POST", r"/api/subjects/reset", api_subjects_reset, False),
    ("PUT", r"/api/subjects/(\d+)", api_subject_update, True),
    ("DELETE", r"/api/subjects/(\d+)", api_subject_delete, True),
    ("PUT", r"/api/grades", api_grade, False),
    ("POST", r"/api/grades/clear", api_grades_clear, False),
    ("PUT", r"/api/meta", api_meta, False),
    ("GET", r"/api/bulletin", api_bulletin, False),
    ("GET", r"/api/bulletin\.pdf", api_bulletin_pdf, False),
    ("POST", r"/api/bulletins", api_bulletin_save, False),
    ("GET", r"/api/bulletins/(\d+)", api_bulletin_get, True),
    ("GET", r"/api/bulletins/(\d+)\.pdf", api_bulletin_get_pdf, True),
    ("DELETE", r"/api/bulletins/(\d+)", api_bulletin_delete, True),
    ("GET", r"/api/history", api_history, False),
    ("GET", r"/api/export", api_export, False),
    ("DELETE", r"/api/account", api_delete_account, False),
]
ROUTES = [(m, re.compile("^" + p + "$"), f, ident) for m, p, f, ident in ROUTES]

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                               "object-src 'self'; frame-src 'self' blob:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "SamaBulletin/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), (fmt % args).split("?")[0]))

    # --- helpers
    @property
    def ip(self):
        if os.environ.get("SAMA_TRUST_PROXY") == "1" and self.headers.get("X-Forwarded-For"):
            return self.headers["X-Forwarded-For"].split(",")[0].strip()
        return self.client_address[0]

    def session_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "sb_session":
                return v
        return None

    def _secure(self):
        return os.environ.get("SAMA_SECURE_COOKIES") == "1" or self.headers.get("X-Forwarded-Proto") == "https"

    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        if getattr(self, "set_cookie", None):
            c = f"sb_session={self.set_cookie}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_DAYS * 86400}"
            self.send_header("Set-Cookie", c + ("; Secure" if self._secure() else ""))
        elif getattr(self, "clear_cookie", False):
            self.send_header("Set-Cookie", "sb_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    # --- dispatch
    def do_GET(self): self._dispatch("GET")
    def do_POST(self): self._dispatch("POST")
    def do_PUT(self): self._dispatch("PUT")
    def do_DELETE(self): self._dispatch("DELETE")
    def do_HEAD(self): self._dispatch("GET")

    def _dispatch(self, method):
        self.set_cookie, self.clear_cookie = None, False
        url = urlparse(self.path)
        try:
            if url.path.startswith("/api/"):
                self._api(method, url)
            elif method == "GET":
                self._static(url.path)
            else:
                raise ApiError(405, "Méthode non autorisée.")
        except ApiError as e:
            self._json(e.status, {"error": e.message})
        except Exception as e:  # pragma: no cover
            sys.stderr.write(f"[erreur] {type(e).__name__}: {e}\n")
            try:
                self._json(500, {"error": "Erreur interne du serveur."})
            except Exception:
                pass

    def _api(self, method, url):
        body = {}
        if method != "GET":
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                raise ApiError(403, "Origine non autorisée.")
            if self.headers.get("X-Requested-With") != "SamaBulletin":
                raise ApiError(403, "En-tête de sécurité manquant.")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError(413, "Requête trop volumineuse.")
            raw = self.rfile.read(length) if length else b""
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    raise ApiError(400, "Corps JSON invalide.")
                if not isinstance(body, dict):
                    raise ApiError(400, "Corps JSON invalide.")
        for m, rx, fn, has_id in ROUTES:
            mt = rx.match(url.path)
            if m == method and mt:
                break
        else:
            raise ApiError(404, "Route introuvable.")
        conn = connect()
        try:
            user = None
            tok = self.session_token()
            if tok:
                user = conn.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                                       WHERE s.token_hash=? AND s.expires_at>?""", (sha256(tok), now_iso())).fetchone()
            ctx = Ctx(self, conn, user, body, parse_qs(url.query))
            res = fn(ctx, int(mt.group(1))) if has_id else fn(ctx)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        kind, data = res
        if kind == "pdf":
            pdf_bytes, name = data
            disp = "inline" if parse_qs(url.query).get("inline") else "attachment"
            self._send(200, pdf_bytes, "application/pdf", {"Content-Disposition": f'{disp}; filename="{name}"'})
        elif kind == "json_download":
            self._send(200, data[0], "application/json; charset=utf-8", {"Content-Disposition": f'attachment; filename="{data[1]}"'})
        else:
            self._json(kind, data)

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            raise ApiError(404, "Page introuvable.")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/manifest+json"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


def main():
    init_db()
    port = int(os.environ.get("PORT", os.environ.get("SAMA_PORT", "8000")))
    host = os.environ.get("SAMA_HOST", "0.0.0.0")
    if not os.environ.get("SMTP_HOST"):
        print("[info] SMTP_HOST non défini : les e-mails de réinitialisation seront écrits dans data/outbox/ (mode développement).", file=sys.stderr)
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print(f"Sama Bulletin → http://localhost:{port}", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
