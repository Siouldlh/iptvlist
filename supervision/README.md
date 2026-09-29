# Supervision externe de cartouchemalin.fr

Sondes HTTP toutes les 5 minutes, exécutées par GitHub Actions (infrastructure GitHub, donc **hors du NAS** et hors de la box internet). En cas de panne du site (tunnel Cloudflare, NAS éteint, coupure internet), une alerte est émise automatiquement (run rouge → e-mail « Run failed » GitHub + issue d'incident publique), puis une alerte de rétablissement quand le site revient.

Un second moniteur, `supervision-heartbeat-backups`, vérifie **la fraîcheur des sauvegardes** du NAS (battement de cœur ping-à-l'absence, voir plus bas).

## Ce qui est surveillé

| Sonde | URL | OK si |
|---|---|---|
| Accueil | `https://cartouchemalin.fr/` | HTTP 2xx |
| Fiche produit | `https://cartouchemalin.fr/cartouche/047b00ff-d2c1-421b-a6b8-09464c3042b2` | HTTP 2xx |
| Sitemap | `https://cartouchemalin.fr/sitemap.xml` | HTTP 2xx |

- Intervalle : toutes les 5 minutes (cron `*/5`), timeout 20 s par sonde.
- Alerte au **2e échec consécutif** (~10 min), re-alerte toutes les **6 h** si la panne dure, alerte de rétablissement dès le premier succès.
- La fiche produit détecte une base morte même si l'accueil répond (l'accueil peut servir du cache).

## Battement de cœur des sauvegardes (`supervision-heartbeat-backups`)

La chaîne de sauvegarde du NAS (dépôt privé `Siouldlh/cartouchemalin`, SER-80) ping en GET un topic `ntfy.sh` après chaque cycle complet réussi. Ce moniteur tourne **toutes les 2 h** (cron `7 */2`) :

- lit le cache public du topic (rétention 12 h) et met à jour `supervision/heartbeat-etat.json` (committé) ;
- si le dernier ping vu a plus de **26 h** (cycle quotidien + marge) : alerte — run rouge (e-mail GitHub), issue d'incident `panne-sauvegardes`, fermée automatiquement au retour des pings ;
- détection constatée : 26 à 28 h après le dernier cycle réussi.

*Limite connue* : le topic ntfy est « secret par obscurité » (valeur dans le workflow). N'importe qui connaissant le topic peut publier un ping et masquer une panne de sauvegarde — acceptable au niveau de menace de ce site ; durcissement possible plus tard (endpoint authentifié type healthchecks.io).

## Comment ça marche

1. Le workflow `.github/workflows/supervision-cartouchemalin.yml` tourne sur `schedule`, `push` (uniquement sur ce dossier) et manuellement (`workflow_dispatch` avec une `probe_url` de test).
2. `supervision/check.py` sonde les URLs, puis lit/écrit l'état dans une boîte e-mail dédiée (AgentMail, si les secrets existent) et de toute façon dans `supervision/etat.json` (committé à chaque run avec `[skip ci]`) : l'état courant est lisible publiquement.
3. À l'alerte : le run passe en rouge (e-mail « Run failed » GitHub) et `supervision/incident.py` **ouvre une issue publique** `panne-cartouchemalin` ; elle est fermée automatiquement au rétablissement. Idempotent (pas de doublon), silencieux pendant une pause.
4. Les canaux e-mail dédié (AgentMail) et push téléphone (ntfy.sh) s'activent automatiquement si les secrets `AGENTMAIL_API_KEY`, `AGENTMAIL_INBOX`, `ALERT_EMAIL`, `NTFY_TOPIC` sont posés sur ce dépôt.

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

- Voir l'état : page [Actions](https://github.com/Siouldlh/iptvlist/actions/workflows/supervision-cartouchemalin.yml), `etat.json` et `heartbeat-etat.json` en tête de branche, issues labellisées `panne-*`.
- Tester l'alerte du site sans couper la prod : committer un fichier `supervision/test-probe-url.txt` contenant l'URL à sonder (ex. une URL volontairement 404) — 1er run = échec enregistré sans alerte, 2e run = alerte (run rouge + issue `[TEST]`) ; supprimer le fichier pour revenir en mode prod. (Le déclenchement `workflow_dispatch` est refusé au jeton des agents : c'est la méthode par fichier qui est utilisée.)
- Tester l'alerte heartbeat : même principe — modifier temporairement `BACKUP_HB_TOPIC` dans le workflow heartbeat vers un topic vide, ou attendre 26 h+ sans ping.
- Mettre en pause pendant une maintenance : variable `PAUSED` à `1` (Settings → Secrets and variables → Actions → Variables) ou committer un fichier vide `supervision/PAUSED`, supprimer pour reprendre.
- Tout retirer : supprimer `.github/workflows/supervision-*.yml` et `supervision/`, puis les secrets/variables associés.