"""Sama Bulletin — génération de vrais fichiers PDF (A4), bibliothèque standard uniquement."""
import unicodedata
import zlib
from datetime import datetime, timezone

# Largeurs Helvetica (unités 1/1000 em), caractères ASCII 32..126
_REG = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
        556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
        1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
        667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
        333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
        556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
         556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
         975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
         667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
         333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
         611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]
_SPECIAL = {"’": (222, 278), "‘": (222, 278), "–": (556, 556), "—": (1000, 1000), "·": (278, 278),
            "…": (1000, 1000), "•": (350, 350), "œ": (944, 944), "Œ": (1000, 1000), "°": (400, 400),
            "«": (556, 556), "»": (556, 556), "€": (556, 556), "÷": (584, 584), "×": (584, 584)}


def _char_w(ch, bold):
    if ch in _SPECIAL:
        return _SPECIAL[ch][1 if bold else 0]
    o = ord(ch)
    if 32 <= o <= 126:
        return (_BOLD if bold else _REG)[o - 32]
    base = unicodedata.normalize("NFD", ch)[0]
    o = ord(base)
    if 32 <= o <= 126:
        return (_BOLD if bold else _REG)[o - 32]
    return 556


def text_width(text, size, bold=False):
    return sum(_char_w(c, bold) for c in text) * size / 1000.0


def fit(text, size, max_w, bold=False):
    """Tronque avec « … » pour tenir dans max_w."""
    if text_width(text, size, bold) <= max_w:
        return text
    while text and text_width(text + "…", size, bold) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(text, size, max_w, bold=False):
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for word in para.split():
            trial = (cur + " " + word).strip()
            if text_width(trial, size, bold) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def _hex(c):
    c = c.lstrip("#")
    return " ".join(f"{int(c[i:i + 2], 16) / 255:.4f}" for i in (0, 2, 4))


def _esc(b):
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


class Canvas:
    """Coordonnées en points, origine en haut à gauche."""

    def __init__(self, w=595.28, h=841.89):
        self.w, self.h = w, h
        self.pages = []
        self.ops = None

    def new_page(self):
        self.ops = []
        self.pages.append(self.ops)

    def _o(self, s):
        self.ops.append(s)

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=0.6):
        py = self.h - y - h
        if fill:
            self._o(f"{_hex(fill)} rg")
        if stroke:
            self._o(f"{_hex(stroke)} RG {lw} w")
        op = "B" if fill and stroke else "f" if fill else "S"
        self._o(f"{x:.2f} {py:.2f} {w:.2f} {h:.2f} re {op}")

    def rrect(self, x, y, w, h, r, fill=None, stroke=None, lw=0.6):
        k = 0.5523 * r
        X0, X1, Y1, Y0 = x, x + w, self.h - y, self.h - y - h
        p = [f"{X0 + r:.2f} {Y1:.2f} m", f"{X1 - r:.2f} {Y1:.2f} l",
             f"{X1 - r + k:.2f} {Y1:.2f} {X1:.2f} {Y1 - r + k:.2f} {X1:.2f} {Y1 - r:.2f} c",
             f"{X1:.2f} {Y0 + r:.2f} l",
             f"{X1:.2f} {Y0 + r - k:.2f} {X1 - r + k:.2f} {Y0:.2f} {X1 - r:.2f} {Y0:.2f} c",
             f"{X0 + r:.2f} {Y0:.2f} l",
             f"{X0 + r - k:.2f} {Y0:.2f} {X0:.2f} {Y0 + r - k:.2f} {X0:.2f} {Y0 + r:.2f} c",
             f"{X0:.2f} {Y1 - r:.2f} l",
             f"{X0:.2f} {Y1 - r + k:.2f} {X0 + r - k:.2f} {Y1:.2f} {X0 + r:.2f} {Y1:.2f} c h"]
        if fill:
            self._o(f"{_hex(fill)} rg")
        if stroke:
            self._o(f"{_hex(stroke)} RG {lw} w")
        op = "B" if fill and stroke else "f" if fill else "S"
        self._o(" ".join(p) + f" {op}")

    def line(self, x1, y1, x2, y2, color="#CBD9EA", lw=0.5):
        self._o(f"{_hex(color)} RG {lw} w {x1:.2f} {self.h - y1:.2f} m {x2:.2f} {self.h - y2:.2f} l S")

    def text(self, x, y, s, size=9, bold=False, italic=False, color="#0B1F3A", align="left", w=None):
        """y = ligne de base. align left/center/right dans une boîte de largeur w."""
        s = str(s)
        font = "F2" if bold else "F3" if italic else "F1"
        tw = text_width(s, size, bold)
        if align == "center" and w is not None:
            x = x + (w - tw) / 2
        elif align == "right" and w is not None:
            x = x + w - tw
        enc = _esc(s.encode("cp1252", errors="replace"))
        self.ops.append(b"BT " + f"{_hex(color)} rg /{font} {size} Tf {x:.2f} {self.h - y:.2f} Td (".encode() + enc + b") Tj ET")

    # ---- assemblage ----
    def build(self, title="", author="Sama Bulletin", subject=""):
        objs = []

        def add(b):
            objs.append(b if isinstance(b, bytes) else b.encode("latin-1"))
            return len(objs)

        add("<< /Type /Catalog /Pages 2 0 R >>")
        add("PAGES")  # remplacé plus bas
        fonts = {}
        for name, base in (("F1", "Helvetica"), ("F2", "Helvetica-Bold"), ("F3", "Helvetica-Oblique")):
            fonts[name] = add(f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} /Encoding /WinAnsiEncoding >>")
        res = "<< /Font << " + " ".join(f"/{k} {v} 0 R" for k, v in fonts.items()) + " >> >>"
        kids = []
        for ops in self.pages:
            body = b"\n".join(o if isinstance(o, bytes) else o.encode("latin-1") for o in ops)
            comp = zlib.compress(body, 9)
            cid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(comp) + comp + b"\nendstream")
            pid = add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.w:.2f} {self.h:.2f}] /Resources {res} /Contents {cid} 0 R >>")
            kids.append(pid)
        objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()

        def pdf_str(s):
            return "(" + s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"

        def u16(s):  # titre en UTF-16BE pour les accents
            return "<FEFF" + s.encode("utf-16-be").hex().upper() + ">"

        now = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
        info = add(f"<< /Title {u16(title)} /Author {u16(author)} /Subject {u16(subject)} /Creator (Sama Bulletin) /Producer (Sama Bulletin) /CreationDate ({now}) >>")
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, o in enumerate(objs, 1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)


# ------------------------------------------------------------------ bulletin
BLUE, BLUE2, LIGHT, NIGHT, GREY, LINE = "#1456A0", "#287BC7", "#EAF3FC", "#0B1F3A", "#6B7C93", "#C9D9EC"


def _n(v, d=None):
    """Format français. d=None → décimales utiles ; d=2 → toujours 2."""
    if v is None:
        return "—"
    s = f"{v:.{d}f}" if d is not None else f"{round(v, 2):g}"
    return s.replace(".", ",")


def _logo(c, x, y, s=34):
    c.rrect(x, y, s, s, 8, fill=BLUE)
    bw = s * 0.15
    for i, hh in enumerate((0.28, 0.46, 0.64)):
        bx = x + s * 0.2 + i * (bw + s * 0.09)
        c.rect(bx, y + s * 0.8 - s * hh, bw, s * hh, fill="#FFFFFF")
    c.rect(x + s * 0.2, y + s * 0.84, s * 0.6, s * 0.045, fill=BLUE2)


def render_bulletin(p):
    """p : charge utile construite par server.build_bulletin()."""
    c = Canvas()
    W, H, M = c.w, c.h, 36.0
    CW = W - 2 * M
    page = [0]

    def footer():
        c.line(M, H - 40, W - M, H - 40, LINE)
        c.text(M, H - 28, "Sama Bulletin — Tes notes. Ta progression. Ton bulletin.", 7, color=GREY)
        c.text(M, H - 28, f"Généré le {p['generated_label']} · Page {page[0]}", 7, color=GREY, align="right", w=CW)

    def new_page():
        if page[0]:
            footer()
        page[0] += 1
        c.new_page()
        return M

    y = new_page()
    # ---- en-tête
    _logo(c, M, y)
    c.text(M + 44, y + 15, "Sama Bulletin", 15, bold=True, color=BLUE)
    c.text(M + 44, y + 28, "Tes notes. Ta progression. Ton bulletin.", 7.5, italic=True, color=GREY)
    c.text(M, y + 12, "BULLETIN DE NOTES", 17, bold=True, color=NIGHT, align="right", w=CW)
    c.text(M, y + 27, f"{p['semester_label']} — Année scolaire {p['school_year']}", 9.5, color=BLUE2, bold=True, align="right", w=CW)
    y += 44
    c.rect(M, y, CW, 2.2, fill=BLUE)
    y += 12

    # ---- identité
    bh = 58
    c.rrect(M, y, CW, bh, 6, fill=LIGHT, stroke=LINE)
    half = CW / 2
    def kv(x, yy, k, v, mw):
        c.text(x, yy, k, 7, color=GREY)
        c.text(x, yy + 11, fit(v, 10, mw, True), 10, bold=True)
    kv(M + 12, y + 14, "NOM ET PRÉNOM", p["student"]["full_name"], half - 24)
    kv(M + 12, y + 40, "ÉTABLISSEMENT", p["school"], half - 24)
    kv(M + half + 8, y + 14, "CLASSE / SÉRIE", p["class_label"], half - 20)
    rank_txt = p["rank_label"] if p.get("rank_label") else "Non renseigné"
    kv(M + half + 8, y + 40, "RANG", rank_txt, half - 20)
    y += bh + 14

    # ---- tableau
    cols = [("Matière", 142, "l"), ("Dev. 1", 31, "c"), ("Dev. 2", 31, "c"), ("Dev. 3", 31, "c"),
            ("Retenues", 56, "c"), ("Moy. dev.", 42, "c"), ("Compo.", 40, "c"), ("Moyenne", 48, "c"),
            ("Coef.", 30, "c"), ("Points", 64, "c")]
    scale = CW / sum(w for _, w, _ in cols)
    cols = [(n, w * scale, a) for n, w, a in cols]

    def table_header(yy):
        c.rect(M, yy, CW, 24, fill=BLUE)
        x = M
        for n, w, a in cols:
            if a == "l":
                c.text(x + 6, yy + 15, n, 7.5, bold=True, color="#FFFFFF")
            else:
                c.text(x, yy + 15, n, 7.5, bold=True, color="#FFFFFF", align="center", w=w)
            x += w
        return yy + 24

    y = table_header(y)
    RH = 21
    for i, r in enumerate(p["rows"]):
        if y + RH > H - 150:
            y = new_page()
            y = table_header(y)
        if i % 2 == 1:
            c.rect(M, y, CW, RH, fill=LIGHT)
        c.line(M, y + RH, M + CW, y + RH, LINE, 0.4)
        x = M
        name = r["name"]
        c.text(x + 6, y + 14, fit(name, 8.5, cols[0][1] - 10, True), 8.5, bold=True)
        x += cols[0][1]
        for k in range(3):
            w = cols[1 + k][1]
            v = r["d"][k]
            if v is None:
                c.text(x, y + 14, "—", 8.5, color=GREY, align="center", w=w)
            elif k in r["dropped"]:
                c.text(x, y + 14, f"({_n(v)})", 8.5, color=GREY, italic=True, align="center", w=w)
            else:
                c.text(x, y + 14, _n(v), 8.5, align="center", w=w)
            x += w
        w = cols[4][1]
        kept_vals = [_n(r["d"][k]) for k in r["kept"]]
        c.text(x, y + 14, " · ".join(kept_vals) if kept_vals else "—", 8, color=BLUE, align="center", w=w)
        x += w
        w = cols[5][1]
        c.text(x, y + 14, _n(r["dev_avg"], 2), 8.5, align="center", w=w)
        x += w
        w = cols[6][1]
        c.text(x, y + 14, _n(r["compo"]), 8.5, align="center", w=w)
        x += w
        w = cols[7][1]
        avg_txt = _n(r["avg"], 2) + ("*" if r["avg"] is not None and not r["complete"] else "")
        c.text(x, y + 14, avg_txt, 9, bold=True, color=BLUE if r["avg"] is not None else GREY, align="center", w=w)
        x += w
        w = cols[8][1]
        c.text(x, y + 14, _n(r["coef"]), 8.5, align="center", w=w)
        x += w
        w = cols[9][1]
        c.text(x, y + 14, _n(r["points"], 2), 8.5, align="center", w=w)
        y += RH
    # total
    if y + 30 > H - 140:
        y = new_page()
    c.rect(M, y, CW, 24, fill=LIGHT, stroke=BLUE, lw=0.8)
    xcoef = M + sum(w for _, w, _ in cols[:8])
    c.text(M + 6, y + 15.5, "TOTAL", 8.5, bold=True, color=BLUE)
    c.text(xcoef, y + 15.5, _n(p["sum_coef"]), 8.5, bold=True, align="center", w=cols[8][1])
    c.text(xcoef + cols[8][1], y + 15.5, _n(p["sum_points"], 2), 8.5, bold=True, align="center", w=cols[9][1])
    y += 36

    # ---- synthèse
    need = 150
    if y + need > H - 60:
        y = new_page()
    boxes = [("MOYENNE GÉNÉRALE", _n(p["general"], 2) + " / 20" if p["general"] is not None else "—", True)]
    if p["semester"] == 2:
        boxes.append(("1er SEMESTRE", _n(p["s1_general"], 2) if p.get("s1_general") is not None else "—", False))
        boxes.append(("MOYENNE ANNUELLE", _n(p["annual"], 2) if p.get("annual") is not None else "—", False))
    elif p.get("rank_label"):
        boxes.append(("RANG", p["rank_label"], False))
    gap = 10
    bw = (CW - gap * (len(boxes) - 1)) / len(boxes)
    for i, (k, v, main) in enumerate(boxes):
        bx = M + i * (bw + gap)
        if main:
            c.rrect(bx, y, bw, 50, 7, fill=BLUE)
            c.text(bx + 12, y + 17, k, 7.5, bold=True, color="#CFE3F8")
            c.text(bx + 12, y + 40, v, 20, bold=True, color="#FFFFFF")
        else:
            c.rrect(bx, y, bw, 50, 7, fill=LIGHT, stroke=LINE)
            c.text(bx + 12, y + 17, k, 7.5, bold=True, color=GREY)
            c.text(bx + 12, y + 40, v, 18, bold=True, color=BLUE)
    y += 62

    # ---- appréciation
    apr = p.get("appreciation") or "—"
    lines = wrap(apr, 9.5, CW - 24)
    ah = 30 + 13 * max(1, len(lines))
    c.rrect(M, y, CW, ah, 6, stroke=LINE)
    c.text(M + 12, y + 14, "APPRÉCIATION GÉNÉRALE", 7.5, bold=True, color=GREY)
    for i, ln in enumerate(lines):
        c.text(M + 12, y + 30 + 13 * i, ln, 9.5)
    y += ah + 10

    # ---- légende
    r = p["rules"]
    legend = [
        "Les notes entre parenthèses ne sont pas retenues dans la moyenne des devoirs ; « Retenues » indique les notes prises en compte.",
        f"Moyenne de la matière = (moyenne des devoirs × {_n(r['wd'])} + composition × {_n(r['wc'])}) ÷ {_n(r['wd'] + r['wc'])}. "
        "Moyenne générale = somme des (moyenne × coefficient) ÷ somme des coefficients.",
    ]
    if any(x["avg"] is not None and not x["complete"] for x in p["rows"]):
        legend.append("* Moyenne provisoire : calculée avec les notes saisies (une note manquante n'est jamais comptée comme zéro).")
    for t in legend:
        for ln in wrap(t, 7, CW):
            c.text(M, y + 7, ln, 7, color=GREY)
            y += 9
    y += 6

    # ---- mention
    msg = "Ce bulletin a été généré par l'élève avec Sama Bulletin à partir des notes qu'il a saisies. Il n'est pas un bulletin officiel délivré par un établissement scolaire et n'a aucune valeur administrative."
    mlines = wrap(msg, 7.5, CW - 24)
    mh = 26 + 9 * len(mlines)
    if y + mh > H - 50:
        y = new_page()
    c.rrect(M, y, CW, mh, 6, fill="#FFF7E6", stroke="#F2C879")
    c.text(M + 12, y + 14, "Document personnel — non officiel", 8.5, bold=True, color="#8A5A00")
    for i, ln in enumerate(mlines):
        c.text(M + 12, y + 25 + 9 * i, ln, 7.5, color="#6B4A00")
    footer()
    return c.build(title=f"Bulletin {p['semester_label']} — {p['student']['full_name']}",
                   subject=f"Année scolaire {p['school_year']}")
