#!/usr/bin/env python3
"""Moniteur de fraicheur des sauvegardes cartouchemalin — dead man's switch.

La chaine de sauvegarde du NAS (SER-80) ping un topic ntfy.sh en GET apres
chaque cycle complet reussi (variable BACKUP_ALERT_HEARTBEAT_URL cote NAS,
jamais loggee). Ce moniteur, execute par GitHub Actions (hors NAS) :

- lit le cache public du topic ntfy (retention 12 h) ;
- met a jour supervision/heartbeat-etat.json (committé) avec la date du
  dernier ping vu ;
- si ce dernier ping a plus de BACKUP_MAX_AGE_H heures (defaut 26 h pour un
  cycle quotidien + marge) : ALERTE — exit 1 (run rouge -> e-mail GitHub) ;
- PAUSED (variable repo) ou fichier supervision/PAUSED : execution sans
  alerte ni changement d'etat (maintenance).

Seuil : cycle quotidien ~24 h -> alerte au-dela de 26 h sans ping ; le
moniteur tourne toutes les 2 h, donc detection 26-28 h apres le dernier
cycle reussi.
"""
import calendar
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_iso(iso):
    if not iso:
        return None
    try:
        return calendar.timegm(time.strptime(iso.split(".")[0].rstrip("Z"),
                                             "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def iso_of(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def poll_cache(topic):
    """Messages du cache public ntfy.sh pour ce topic (retention 12 h)."""
    url = "https://ntfy.sh/%s/json?poll=1&since=24h" % urllib.parse.quote(topic, safe="")
    req = urllib.request.Request(url, headers={
        "User-Agent": "supervision-heartbeat-cartouchemalin/1.0"})
    out = []
    with urllib.request.urlopen(req, timeout=30) as resp:
        for line in resp.read().decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("event") == "message" and d.get("topic"):
                ts = int(d.get("time") or 0)
                if ts > 0:
                    out.append(ts)
    return out


def load_etat(path):
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def main():
    topic = os.environ.get("BACKUP_HB_TOPIC", "").strip()
    try:
        max_age_h = float(os.environ.get("BACKUP_MAX_AGE_H", "26"))
    except ValueError:
        max_age_h = 26.0
    paused = os.environ.get("PAUSED", "").strip() == "1" or \
        os.path.exists(os.path.join("supervision", "PAUSED"))
    run_url = os.environ.get("RUN_URL", "").strip()
    etat_path = os.path.join("supervision", "heartbeat-etat.json")
    etat = load_etat(etat_path)
    hb = dict(etat.get("heartbeat") or {})
    stamps = now_iso()

    msgs_ts = poll_cache(topic) if topic else []
    newest = max(msgs_ts) if msgs_ts else None
    last_seen_ts = parse_iso(hb.get("last_seen_at"))
    bootstrap = False
    if newest is not None and (last_seen_ts is None or newest > last_seen_ts):
        last_seen_ts = newest
        source = "cache ntfy"
    elif last_seen_ts is None:
        # Premiere execution sans ping visible : on amorce l'etat a maintenant
        # pour ne pas alerter avant d'avoir observe au moins un cycle.
        last_seen_ts = int(time.time())
        bootstrap = True
        source = "amorcage (aucun ping vu)"
    else:
        source = "etat committé"

    age_h = (time.time() - last_seen_ts) / 3600.0
    down = age_h > max_age_h
    print("topic=%s dernier_ping=%s age=%.1fh seuil=%.0fh source=%s" % (
        hashlib.sha256(topic.encode()).hexdigest()[:12] if topic else "?",
        iso_of(last_seen_ts), age_h, max_age_h, source))
    print("nb_messages_cache=%d (retention ntfy.sh 12 h)" % len(msgs_ts))

    if paused:
        print("PAUSE ACTIVE : sondes executees sans alerte ni changement d'etat")
        down = False
    if down:
        print("ALERTE : aucun ping de sauvegarde depuis %.1f h (seuil %.0f h)"
              % (age_h, max_age_h))
        hb["last_alert_at"] = stamps

    hb.update({
        "state": "down" if down else "up",
        "last_seen_at": iso_of(last_seen_ts),
        "age_hours": round(age_h, 2),
        "max_age_h": max_age_h,
        "bootstrap": bootstrap,
        "last_checked_at": stamps,
        "run_url": run_url,
    })
    etat.update({
        "generated_at": stamps,
        "paused": paused,
        "run_url": run_url,
        "heartbeat": hb,
    })
    os.makedirs("supervision", exist_ok=True)
    with open(etat_path, "w") as f:
        json.dump(etat, f, indent=2, sort_keys=True)
        f.write("\n")
    return 1 if down else 0


if __name__ == "__main__":
    sys.exit(main())