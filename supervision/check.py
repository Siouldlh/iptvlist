#!/usr/bin/env python3
"""Sonde externe de cartouchemalin.fr — GitHub Actions, toutes les 5 min.

Modes :
- standard (schedule / push) : sonde les URLs de production.
- test (PROBE_URL defini) : sonde une seule URL ponctuelle, etat isole "sonde-test".

Canal d'alerte :
- toujours : le run passe en rouge au declenchement de l'alerte -> GitHub envoie
  un e-mail "Run failed" au owner du depot.
- si les secrets AgentMail sont disponibles : e-mail dedie en plus,
  et push ntfy.sh optionnel si NTFY_TOPIC est defini.

Etat persistant :
- si AgentMail est disponible : messages labellises cm:<id> dans la boite de supervision
  (autorite), etat miroir dans etat.json ;
- sinon : fichier supervision/etat.json (committ a chaque run avec [skip ci]).

Pause pendant une maintenance : variable d'env PAUSED=1 ou fichier supervision/PAUSED.
"""
import calendar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.agentmail.to/v0"
BASE = "https://cartouchemalin.fr"
ALERT_AFTER_FAILURES = 2
RE_ALERT_HOURS = 6
CHECK_TIMEOUT_S = 20

INBOX = os.environ.get("AGENTMAIL_INBOX", "").strip()
KEY = os.environ.get("AGENTMAIL_API_KEY", "").strip()
EMAIL_TO = os.environ.get("ALERT_EMAIL", "").strip()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
AGENTMAIL_READY = bool(INBOX and KEY and EMAIL_TO)

PROD_CHECKS = [
    {"id": "accueil", "label": "Accueil", "url": BASE + "/"},
    {"id": "fiche-produit", "label": "Fiche produit",
     "url": BASE + "/cartouche/047b00ff-d2c1-421b-a6b8-09464c3042b2"},
    {"id": "sitemap", "label": "Sitemap", "url": BASE + "/sitemap.xml"},
]


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def age_hours(iso):
    if not iso:
        return float("inf")
    base = iso.split(".")[0].rstrip("Z")
    try:
        ts = calendar.timegm(time.strptime(base, "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return float("inf")
    return (time.time() - ts) / 3600.0


def http_json(method, path, body=None, extra_headers=None):
    headers = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        API + path, method=method, headers=headers,
        data=json.dumps(body).encode("utf-8") if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def probe(url):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "supervision-cartouchemalin/1.0",
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache"})
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT_S) as resp:
            code = resp.getcode()
        return {"ok": 200 <= code < 300, "status": code,
                "ms": int((time.time() - t0) * 1000), "err": ""}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code,
                "ms": int((time.time() - t0) * 1000), "err": ""}
    except Exception as e:
        return {"ok": False, "status": 0,
                "ms": int((time.time() - t0) * 1000), "err": type(e).__name__}


def agentmail_prev(check_id):
    try:
        data = http_json("GET", "/inboxes/%s/messages?labels=%s&limit=1" % (
            urllib.parse.quote(INBOX, safe=""),
            urllib.parse.quote("cm:" + check_id, safe="")))
    except Exception as e:
        print("WARN etat AgentMail %s: %s" % (check_id, e))
        return None
    msgs = data.get("messages") or []
    if not msgs:
        return "up", None
    state = "up"
    for lab in (msgs[0].get("labels") or []):
        if lab.startswith("state:"):
            state = lab.split(":", 1)[1]
    return state, msgs[0].get("timestamp")


def record(check_id, state, subject, text, to):
    idem = "%s-%s-%d" % (check_id, state, int(time.time() // 300))
    res = http_json(
        "POST", "/inboxes/%s/messages/send" % urllib.parse.quote(INBOX, safe=""),
        {"to": [to], "subject": subject, "text": text,
         "labels": ["cm:" + check_id, "state:" + state]},
        {"Idempotency-Key": idem})
    print("E-MAIL etat=%s a=%s message_id=%s" % (state, to, res.get("message_id")))


def notify_ntfy(subject, text):
    if not NTFY_TOPIC:
        return
    try:
        req = urllib.request.Request(
            "https://ntfy.sh/" + urllib.parse.quote(NTFY_TOPIC, safe=""),
            method="POST", data=text.encode("utf-8"),
            headers={"Title": subject[:200], "Priority": "high", "Tags": "warning"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            print("NTFY HTTP", resp.getcode())
    except Exception as e:
        print("WARN ntfy: %s" % e)


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
    etat_path = os.path.join("supervision", "etat.json")
    probe_url = os.environ.get("PROBE_URL", "").strip()
    # Déclencheur de test sans permission workflow_dispatch : si le fichier
    # supervision/test-probe-url.txt existe (commité), la sonde ponctuelle
    # part de son contenu. Retirer le fichier pour revenir en mode prod.
    test_file = os.path.join("supervision", "test-probe-url.txt")
    if not probe_url and os.path.exists(test_file):
        with open(test_file) as f:
            probe_url = f.read().strip()
    paused = os.environ.get("PAUSED", "").strip() == "1" or \
        os.path.exists(os.path.join("supervision", "PAUSED"))
    run_url = os.environ.get("RUN_URL", "").strip()
    if probe_url:
        checks = [{"id": "sonde-test", "label": "Sonde de test", "url": probe_url}]
        print("mode test : sonde ponctuelle %s" % probe_url)
    else:
        checks = PROD_CHECKS
    if paused:
        print("PAUSE ACTIVE : sondes executees sans alerte ni changement d'etat")
    etat = load_etat(etat_path)
    entries = dict(etat.get("checks") or {})
    exit_code = 0
    stamps = now_iso()
    for check in checks:
        r = probe(check["url"])
        desc = "%s (%s)" % (check["label"], check["url"])
        prev, prev_ts = None, None
        if AGENTMAIL_READY:
            res = agentmail_prev(check["id"])
            if res:
                prev, prev_ts = res
        if prev is None:
            old = entries.get(check["id"]) or {}
            prev = old.get("state", "up")
            prev_ts = old.get("last_alert_at")
        entry = dict(old if (old := entries.get(check["id"])) else {
            "label": check["label"], "url": check["url"], "state": "up",
            "since": stamps, "fail_streak": 0, "last_alert_at": None})
        entry.update({
            "label": check["label"], "url": check["url"],
            "last_seen_at": stamps, "last_http": r["status"],
            "last_ms": r["ms"], "last_err": r["err"]})
        if not paused:
            if r["ok"]:
                print("UP   %s -> HTTP %s (%sms) etat_avant=%s" % (
                    desc, r["status"], r["ms"], prev))
                if prev in ("failing", "down"):
                    subject = "[RETABLI] cartouchemalin.fr - %s (HTTP %s)" % (
                        check["label"], r["status"])
                    text = ("Le site est de nouveau joignable.\n\n"
                            "Sonde : %s\nResultat : HTTP %s\nVerifie a : %s\nRun : %s\n"
                            % (desc, r["status"], stamps, run_url or "n/a"))
                    if AGENTMAIL_READY:
                        try:
                            record(check["id"], "up", subject, text, EMAIL_TO)
                            notify_ntfy(subject, "cartouchemalin.fr retabli : %s (HTTP %s)"
                                        % (check["label"], r["status"]))
                        except Exception as e:
                            print("WARN envoi retablissement: %s" % e)
                entry["state"] = "up"
                entry["since"] = stamps
                entry["fail_streak"] = 0
            else:
                print("DOWN %s -> HTTP %s %s (%sms) etat_avant=%s" % (
                    desc, r["status"], r["err"], r["ms"], prev))
                entry["fail_streak"] = int(entry.get("fail_streak") or 0) + 1
                if prev in ("up", "unknown", None):
                    entry["state"] = "failing"
                    entry["since"] = stamps
                    if AGENTMAIL_READY:
                        try:
                            record(check["id"], "failing",
                                   "[supervision] %s : premier echec (HTTP %s)"
                                   % (check["label"], r["status"]),
                                   "Premier echec de la sonde, pas d'alerte avant %d echecs "
                                   "consecutifs.\n\nSonde : %s\nResultat : HTTP %s %s\nVu a : %s\n"
                                   % (ALERT_AFTER_FAILURES, desc, r["status"], r["err"], stamps),
                                   INBOX)
                        except Exception as e:
                            print("WARN enregistrement echec: %s" % e)
                elif prev == "failing":
                    entry["state"] = "down"
                    entry["last_alert_at"] = stamps
                    subject = "[PANNE] cartouchemalin.fr - %s (HTTP %s)" % (
                        check["label"], r["status"] or "injoignable")
                    text = ("La supervision externe detecte une panne du site.\n\n"
                            "Sonde : %s\nResultat : HTTP %s %s (%d echecs consecutifs)\n"
                            "Detecte a : %s\nRun : %s\n\n"
                            "Causes probables : tunnel Cloudflare, NAS eteint, box internet.\n"
                            "Verification : ouvrir https://cartouchemalin.fr/ en 4G (hors Wi-Fi).\n"
                            "Pause des alertes : fichier supervision/PAUSED (voir docs/supervision.md).\n"
                            % (desc, r["status"], r["err"], ALERT_AFTER_FAILURES, stamps,
                               run_url or "n/a"))
                    if AGENTMAIL_READY:
                        try:
                            record(check["id"], "down", subject, text, EMAIL_TO)
                        except Exception as e:
                            print("WARN envoi alerte: %s" % e)
                    notify_ntfy(subject, "cartouchemalin.fr EN PANNE : %s (HTTP %s)"
                                % (check["label"], r["status"]))
                    exit_code = 1
                else:
                    if age_hours(prev_ts) >= RE_ALERT_HOURS:
                        entry["last_alert_at"] = stamps
                        subject = "[PANNE] cartouchemalin.fr - %s toujours en panne (HTTP %s)" % (
                            check["label"], r["status"] or "injoignable")
                        text = ("La panne dure toujours (re-alerte toutes les %d h).\n\n"
                                "Sonde : %s\nResultat : HTTP %s %s\nVu a : %s\nRun : %s\n"
                                % (RE_ALERT_HOURS, desc, r["status"], r["err"], stamps,
                                   run_url or "n/a"))
                        if AGENTMAIL_READY:
                            try:
                                record(check["id"], "down", subject, text, EMAIL_TO)
                            except Exception as e:
                                print("WARN re-envoi alerte: %s" % e)
                        notify_ntfy(subject, "cartouchemalin.fr toujours EN PANNE : %s (HTTP %s)"
                                    % (check["label"], r["status"]))
                        exit_code = 1
                    else:
                        print("panne en cours, alerte deja envoyee il y a %.1f h : silence"
                              % age_hours(prev_ts))
        entries[check["id"]] = entry

    snapshot = {
        "generated_at": now_iso(),
        "mode": "test" if probe_url else "prod",
        "paused": paused,
        "run_url": run_url,
        "checks": entries,
    }
    os.makedirs("supervision", exist_ok=True)
    with open(etat_path, "w") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
        f.write("\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())