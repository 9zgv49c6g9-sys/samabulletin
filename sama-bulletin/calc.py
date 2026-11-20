"""Sama Bulletin — moteur de calcul (miroir exact de static/calc.js).

Règles :
- 3 devoirs par semestre ; on retient les `keep` meilleurs (2 par défaut).
  Une note manquante n'est JAMAIS comptée comme zéro : on ne travaille que
  sur les notes saisies.
- Moyenne de la matière = (moy. devoirs × wd + composition × wc) ÷ (wd + wc),
  en ignorant la composante absente.
- La moyenne de chaque matière est arrondie à 2 décimales (c'est la valeur
  imprimée sur le bulletin), puis la moyenne générale = Σ(moy × coef) ÷ Σcoef,
  sur les seules matières qui ont une moyenne.
"""
import math

DEV_FIELDS = ("d1", "d2", "d3")
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]


def round2(x):
    """Arrondi à 2 décimales, demi vers le haut, sans artefact flottant.
    Même algorithme que le JavaScript (toPrecision(12))."""
    if x is None:
        return None
    return math.floor(float(format(x * 100, ".12g")) + 0.5) / 100


def _grades_for(subject, sem):
    g = subject.get("grades") or {}
    return g.get(sem) or g.get(str(sem)) or {}


def subject_result(g, wd=1.0, wc=2.0, keep=2):
    g = g or {}
    d = [g.get(k) for k in DEV_FIELDS]
    compo = g.get("compo")
    present = [(i, v) for i, v in enumerate(d) if v is not None]
    ordered = sorted(present, key=lambda t: -t[1])  # tri stable : à égalité, l'ordre de saisie décide
    kept = ordered[:keep]
    dropped = ordered[keep:]
    dev_raw = (sum(v for _, v in kept) / len(kept)) if kept else None
    parts = []
    if dev_raw is not None:
        parts.append((dev_raw, wd))
    if compo is not None:
        parts.append((compo, wc))
    tw = sum(w for _, w in parts)
    avg = round2(sum(v * w for v, w in parts) / tw) if tw > 0 else None
    return {
        "d": d,
        "compo": compo,
        "kept": sorted(i for i, _ in kept),
        "dropped": sorted(i for i, _ in dropped),
        "dev_avg": round2(dev_raw),
        "avg": avg,
        "complete": len(present) == 3 and compo is not None,
    }


def semester_summary(subjects, sem, wd=1.0, wc=2.0, keep=2):
    rows = []
    for s in subjects:
        r = subject_result(_grades_for(s, sem), wd, wc, keep)
        r["id"] = s.get("id")
        r["name"] = s["name"]
        r["coef"] = s["coef"]
        r["points"] = round2(r["avg"] * s["coef"]) if r["avg"] is not None else None
        rows.append(r)
    scored = [r for r in rows if r["avg"] is not None]
    sum_coef = sum(r["coef"] for r in scored)
    general = round2(sum(r["avg"] * r["coef"] for r in scored) / sum_coef) if sum_coef > 0 else None
    sum_points = round2(sum(r["avg"] * r["coef"] for r in scored)) if scored else None
    best = worst = None
    if scored:
        best = max(range(len(scored)), key=lambda i: (scored[i]["avg"], -i))
        worst = min(range(len(scored)), key=lambda i: (scored[i]["avg"], i))
        best = scored[best]["id"]
        worst = scored[worst]["id"] if len(scored) > 1 else None
    return {
        "rows": rows,
        "general": general,
        "sum_coef": sum_coef,
        "sum_points": sum_points,
        "scored": len(scored),
        "total": len(rows),
        "best_id": best,
        "worst_id": worst,
    }


def year_summary(subjects, wd=1.0, wc=2.0, keep=2):
    s1 = semester_summary(subjects, 1, wd, wc, keep)
    s2 = semester_summary(subjects, 2, wd, wc, keep)
    annual = round2((s1["general"] + s2["general"]) / 2) if s1["general"] is not None and s2["general"] is not None else None
    per_subject = []
    for a, b in zip(s1["rows"], s2["rows"]):
        ann = round2((a["avg"] + b["avg"]) / 2) if a["avg"] is not None and b["avg"] is not None else None
        per_subject.append({"id": a["id"], "name": a["name"], "coef": a["coef"], "s1": a["avg"], "s2": b["avg"], "annual": ann})
    return {"s1": s1, "s2": s2, "annual": annual, "per_subject": per_subject}


def appreciation(general):
    if general is None:
        return ""
    if general >= 16:
        return "Excellent semestre. Résultats remarquables, à poursuivre avec la même rigueur."
    if general >= 14:
        return "Très bon semestre. Les résultats sont très satisfaisants."
    if general >= 12:
        return "Bon semestre. Les efforts portent leurs fruits, il faut continuer."
    if general >= 10:
        return "Résultats satisfaisants. Davantage de régularité permettrait de progresser."
    if general >= 8:
        return "Résultats fragiles. Un travail plus soutenu est nécessaire."
    return "Résultats insuffisants. Il faut redoubler d'efforts et demander de l'aide dans les matières difficiles."


def class_label(level_label, serie):
    return f"{level_label} {serie}".strip() if serie else level_label


def fr_date(iso):
    y, m, d = int(iso[0:4]), int(iso[5:7]), int(iso[8:10])
    return f"{d}{'er' if d == 1 else ''} {MONTHS_FR[m - 1]} {y}"
