# Supervision externe de cartouchemalin.fr

Sondes HTTP toutes les 5 minutes, exécutées par GitHub Actions (infrastructure GitHub, donc **hors du NAS** et hors de la box internet). En cas de panne du site (tunnel Cloudflare, NAS éteint, coupure internet), une alerte e-mail est envoyée automatiquement, puis une alerte de rétablissement quand le site revient.

## Ce qui est surveillé

| Sonde | URL | OK si |
|---|---|---|
| Accueil | `https://cartouchemalin.fr/` | HTTP 2xx |
| Fiche produit | `https://cartouchemalin.fr/cartouche/047b00ff-d2c1-421b-a6b8-09464c3042b2` | HTTP 2xx |
| Sitemap | `https://cartouchemalin.fr/sitemap.xml` | HTTP 2xx |

- Intervalle : toutes les 5 minutes (cron `*/5`), timeout 20 s par sonde.
- Alerte au **2e échec consécutif** (~10 min), re-alerte toutes les **6 h** si la panne dure, e-mail de rétablissement dès le premier succès.
- La fiche produit détecte une base morte même si l'accueil répond (l'accueil peut servir du cache).

## Comment ça marche

1. Le workflow `.github/workflows/supervision-cartouchemalin.yml` tourne sur `schedule`, `push` (uniquement sur ce dossier) et manuellement (`workflow_dispatch` avec une `probe_url` de test).
2. `supervision/check.py` sonde les URLs, puis lit/écrit l'état dans une boîte e-mail dédiée (AgentMail) : chaque changement d'état est un message labellisé `cm:<id>` avec un label `state:failing|down|up`.
3. Alerte = e-mail envoyé depuis la boîte de supervision vers le destinataire configuré, plus une notification push `ntfy.sh` si le topic est configuré.
4. Un snapshot `supervision/etat.json` est committé à chaque run (avec `[skip ci]`) : l'état courant est lisible publiquement.

## Configuration (secrets du dépôt, visibles uniquement aux mainteneurs)

| Nom | Rôle |
|---|---|
| `AGENTMAIL_API_KEY` | Clé API du compte AgentMail qui envoie les alertes |
| `AGENTMAIL_INBOX` | Adresse de la boîte de supervision (expéditeur + magasin d'état) |
| `ALERT_EMAIL` | Destinataire des alertes |
| `NTFY_TOPIC` | Topic `ntfy.sh` pour les push sur téléphone (vide = désactivé) |
| `PAUSED` (variable) | `1` = sondes exécutées sans alerte ni changement d'état (maintenance) |

La documentation opérationnelle complète (destinataire réel, topic ntfy, procédures) vit dans `docs/supervision.md` du dépôt privé `Siouldlh/cartouchemalin`.

## Utilisation

- Voir l'état : page [Actions](https://github.com/Siouldlh/iptvlist/actions/workflows/supervision-cartouchemalin.yml) ou `etat.json` en tête de branche.
- Tester manuellement : `Run workflow` avec `probe_url` (par ex. une URL volontairement fausse pour vérifier l'alerte), ou pousser un commit modifiant ce dossier.
- Mettre en pause pendant une maintenance : variable `PAUSED` à `1` (Settings → Secrets and variables → Actions → Variables), remettre `0` après.
- Tout retirer : supprimer `.github/workflows/supervision-cartouchemalin.yml` et `supervision/`, puis les secrets/variables associés.