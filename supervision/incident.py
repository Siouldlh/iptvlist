#!/usr/bin/env python3
"""Issue GitHub d'incident : ouverture a l'alerte, cloture au retablissement.

Usage (dans GitHub Actions, GH_TOKEN fourni par le workflow) :
  python3 supervision/incident.py <fichier-etat> <label-issue> <prefixe-titre>

Lit le fichier d'etat committé par le moniteur :
- site (check.py)  : {"mode": "prod|test", "paused": bool,
                      "checks": {id: {"state": "up|failing|down", ...}}}
- heartbeat        : {"paused": bool, "heartbeat": {"state": "up|down", ...}}

Comportement :
- au moins un etat "down" et aucune issue ouverte pour ce label -> creation
  d'une issue dedupliquee (titre prefixe [TEST] si mode test) ;
- tout est remonte et une issue est ouverte -> cloture avec commentaire ;
- PAUSED (variable repo) ou fichier supervision/PAUSED -> aucune action ;
- idempotent : une seule issue ouverte par label.
"""
import json
import os
import subprocess
import sys


def gh(*args):
    return subprocess.run(["gh"] + list(args), capture_output=True, text=True)


def main():
    etat_file, label, prefix = sys.argv[1], sys.argv[2], sys.argv[3]
    repo = os.environ.get("GITHUB_REPOSITORY", "Siouldlh/iptvlist")
    with open(etat_file) as f:
        data = json.load(f)
    paused = bool(data.get("paused")) or os.path.exists("supervision/PAUSED")
    test = data.get("mode") == "test"

    down = []
    for v in (data.get("checks") or {}).values():
        if isinstance(v, dict) and v.get("state") == "down":
            down.append(v)
    hb = data.get("heartbeat") or {}
    if hb.get("state") == "down":
        down.append(hb)

    # Issue ouverte pour ce moniteur (label = cle de deduplication)
    r = gh("issue", "list", "-R", repo, "--state", "open", "--label", label,
           "--json", "number", "--jq", ".[0].number // empty")
    open_n = (r.stdout or "").strip()

    if paused:
        print("PAUSE ACTIVE : aucune action sur l'issue d'incident")
        return 0

    if down:
        names = []
        for d in down:
            names.append(d.get("label") or d.get("id") or "moniteur")
        title = "[%s] %s : %s" % (
            "TEST" if test else "PANNE", prefix, ", ".join(names))
        lines = []
        for d in down:
            lines.append("- %s — dernier etat connu : HTTP %s %s, depuis %s" % (
                d.get("label") or d.get("id") or "moniteur",
                d.get("last_http", "?"), d.get("last_err", "") or "-",
                d.get("since") or hb.get("last_seen_at") or "?"))
        body = ("La supervision externe detecte une panne.\n\n%s\n\n"
                "Detection : GitHub Actions (hors NAS). Etat courant : "
                "`supervision/etat.json` sur main. Procedure : docs/supervision.md "
                "(depot prive Siouldlh/cartouchemalin).\n"
                "Cette issue se ferme automatiquement au retablissement.\n"
                % "\n".join(lines))
        if open_n:
            print("incident deja ouvert : issue #%s (pas de doublon)" % open_n)
            return 0
        r2 = gh("issue", "create", "-R", repo, "--title", title,
                "--body", body, "--label", label)
        if r2.returncode != 0:
            print("ERREUR creation issue :", (r2.stderr or r2.stdout).strip())
            return 0
        print("ISSUE D'INCIDENT CREEE :", (r2.stdout or "").strip())
        return 0

    if open_n:
        r2 = gh("issue", "close", "-R", repo, open_n,
                "--comment", "Retablissement detecte par la supervision "
                "externe — issue fermee automatiquement.")
        print("issue #%s fermee (retablissement)" % open_n)
        return 0
    print("aucune panne, aucune issue ouverte")
    return 0


if __name__ == "__main__":
    sys.exit(main())