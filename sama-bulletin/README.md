# Sama Bulletin 🇸🇳
*Tes notes. Ta progression. Ton bulletin.*

Application web complète pour les élèves du collège à la Terminale : comptes, notes, moyennes automatiques
(2 meilleurs devoirs sur 3), deux semestres indépendants, tableau de bord, historique et vrais bulletins PDF.

- **Frontend** : HTML / CSS / JavaScript natif (aucune bibliothèque externe, fonctionne hors ligne une fois chargé), installable sur téléphone (PWA).
- **Backend** : Python 3.9+ (bibliothèque standard uniquement : **aucune installation nécessaire**).
- **Base de données** : SQLite (`data/sama_bulletin.db`, créée automatiquement).
- **PDF** : généré côté serveur (`pdf.py`), vrai PDF A4 avec texte sélectionnable.

## Lancer en local

```bash
python3 server.py          # puis ouvrir http://localhost:8000
python3 -m unittest discover -s tests -v    # 26 tests automatiques
```

## Mettre en ligne (production)

Il faut un hébergeur capable de lancer Python avec un **disque persistant** (Render, Railway, Fly.io, un VPS…).
Commande de démarrage : `python3 server.py`. Placez-le derrière HTTPS (le plus simple : l'HTTPS de l'hébergeur).

| Variable | Rôle |
|---|---|
| `PORT` | Port d'écoute (fourni par la plupart des hébergeurs) |
| `SAMA_DATA_DIR` | Dossier de la base SQLite : **à placer sur le disque persistant** |
| `SAMA_BASE_URL` | Adresse publique, ex. `https://sama-bulletin.example.com` (sert aux liens d'e-mail) |
| `SAMA_SECURE_COOKIES=1` | Cookies de session « Secure » (à activer derrière HTTPS) |
| `SAMA_TRUST_PROXY=1` | Lire l'IP réelle dans `X-Forwarded-For` (limitation des tentatives) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Envoi réel des e-mails « mot de passe oublié » |

**Sans `SMTP_HOST`**, les e-mails ne partent pas : ils sont écrits dans `data/outbox/` (mode développement).
Configurez un compte SMTP (Brevo, Mailjet, Gmail…) avant d'ouvrir l'application au public.

**Sauvegarde** : copiez régulièrement `data/sama_bulletin.db`.

## Règles de calcul

- Par semestre et par matière : on garde les 2 meilleurs devoirs → `moyenne des devoirs = (meilleur + 2e meilleur) ÷ 2`.
- `moyenne de la matière = (moyenne des devoirs × 1 + composition × 2) ÷ 3` (pondérations modifiables dans Réglages ;
  nombre de devoirs retenus modifiable : 1, 2 ou 3).
- Une note manquante n'est **jamais** comptée comme zéro.
- `moyenne générale = Σ(moyenne × coefficient) ÷ Σ coefficients`, uniquement sur les matières notées.
- Moyenne annuelle = moyenne des deux semestres (affichée seulement si les deux existent).
- Arrondi à 2 décimales ; la moyenne de chaque matière est arrondie avant le calcul de la moyenne générale
  (c'est la valeur imprimée sur le bulletin).

Les coefficients par défaut valent 1 : les coefficients officiels varient selon la série et l'établissement, ils ne sont pas inventés.
L'élève les règle dans **Réglages → Matières et coefficients**.

## Sécurité

Mots de passe hachés (scrypt) · sessions par cookie `HttpOnly` · protection CSRF (en-tête + contrôle d'origine) ·
limitation des tentatives de connexion · réinitialisation par lien à usage unique (1 h) sans révéler si un compte existe ·
isolation stricte des données entre élèves · en-têtes CSP · export et suppression de compte.

## Structure

```
server.py       serveur, API, base SQLite, authentification
calc.py         moteur de calcul (miroir exact de static/calc.js)
pdf.py          génération des bulletins PDF
curriculum.json classes et matières (ajouter une classe ou une matière = modifier ce fichier)
static/         interface (index.html, app.js, calc.js, style.css, PWA)
tests/          tests automatiques
```

Le bulletin PDF porte la mention « Document personnel — non officiel » : il n'est jamais présenté comme un bulletin délivré par un établissement.
