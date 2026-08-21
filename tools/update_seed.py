#!/usr/bin/env python3
"""Einstiegspunkt der GitHub Action: Quellen abfragen, in den Seed mergen,
validieren, Artefakte schreiben.

Grundregeln (siehe PLAN):
  1. Nie neu generieren, nur ergaenzen. Der Seed enthaelt Daten aus
     eintracht-archiv.de (sourceUrl, Torschuetzennamen, Notizen), die keine
     API liefert.
  2. Upsert-Semantik der App exakt spiegeln (seedkit.upsert).
  3. Bestehende IDs niemals umschreiben.
  4. Abbruch statt Notfallreparatur - lieber veraltet als kaputt.
  5. Kein Commit ohne inhaltliche Aenderung.
  6. gender aus dem Liga-Kuerzel, nie aus der API raten.
"""
import argparse, json, gzip, hashlib, os, sys, time
from datetime import datetime, timezone, timedelta, date

from seedkit import (merge, validate, content_hash, canon_dump, MATCH_FIELDS,
                     spieltag_plausibilitaet)
import providers

# gender ergibt sich allein aus dem abgefragten Liga-Kuerzel.
#
# "dfb" ist der DFB-Pokal der Maenner. Erst ab 2026 abgefragt, aus demselben
# Grund wie bei bl1: Abgeschlossene Runden stehen kuratiert im Seed, und
# OpenLigaDB weicht bei alten Spielzeiten in Terminen und Vereinsschreibweisen
# ab - was ueber die Match-ID Dubletten erzeugt. Optional, weil die Eintracht
# nach einem Pokal-Aus in der Saison gar nicht mehr vorkommt; eine leere
# Antwort ist dann der Normalfall.
LIGEN = [
    {"shortcut": "bl1",  "gender": "men",   "first": 2003, "optional": False},
    {"shortcut": "dfb",  "gender": "men",   "first": 2026, "optional": True},
    {"shortcut": "ffb1", "gender": "women", "first": 2026, "optional": True},
    {"shortcut": "wsc",  "gender": "women", "first": 2026, "optional": True},
]
ESPN_SLUGS = ["uefa.wchampions_qual", "uefa.wchampions"]


def aktuelle_saison(heute=None):
    heute = heute or date.today()
    return heute.year if heute.month >= 8 else heute.year - 1


def sammle(saisons, espn_tage, log):
    gefunden = []
    for cfg in LIGEN:
        for s in saisons:
            if s < cfg["first"]:
                continue
            try:
                roh = providers.openligadb(cfg["shortcut"], s, cfg["gender"])
            except Exception as e:
                stufe = "Hinweis" if cfg["optional"] else "FEHLER"
                log(f"  {stufe}: {cfg['shortcut']}/{s} nicht abrufbar – {e}")
                if not cfg["optional"]:
                    raise
                continue
            ok, verworfen, verdaechtig = spieltag_plausibilitaet(roh)
            if verworfen:
                log(f"  {cfg['shortcut']}/{s}: {len(verworfen)} Spiele mit vertauschtem "
                    f"Tag/Monat verworfen (Spieltage {verdaechtig})")
            log(f"  {cfg['shortcut']}/{s}: {len(ok)} Spiele")
            gefunden += ok
            time.sleep(0.5)

    for tag in espn_tage:
        for sl in ESPN_SLUGS:
            try:
                treffer = providers.espn(sl, tag, "women")
            except Exception as e:
                log(f"  Hinweis: ESPN {sl}/{tag} – {e}")
                continue
            if treffer:
                log(f"  ESPN {sl} {tag}: {len(treffer)} Spiele")
            gefunden += treffer
            time.sleep(0.3)
    return gefunden


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default="data/seed_matches.json")
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--window-days", type=int, default=120)
    ap.add_argument("--espn-back", type=int, default=3)
    ap.add_argument("--espn-forward", type=int, default=2)
    ap.add_argument("--full-backfill", action="store_true",
                    help="alle Saisons ab first statt nur der laufenden")
    ap.add_argument("--offline-fixture",
                    help="statt der APIs diese JSON-Datei als Quelle nutzen (Tests)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    zeilen = []
    def log(s):
        print(s)
        zeilen.append(s)

    doc = json.load(open(args.seed, encoding="utf-8"))
    seed = doc["matches"]
    log(f"Seed geladen: {len(seed)} Spiele, Hash {content_hash(seed)}")

    vorher_fehler, _ = validate(seed)
    if vorher_fehler:
        log(f"ABBRUCH: Der vorhandene Seed ist bereits fehlerhaft ({len(vorher_fehler)}).")
        for e in vorher_fehler[:5]:
            log(f"  {e}")
        return 1

    if args.offline_fixture:
        gefunden = json.load(open(args.offline_fixture, encoding="utf-8"))
        log(f"Fixture: {len(gefunden)} Spiele (keine Netzabfrage)")
    else:
        laufend = aktuelle_saison()
        saisons = (list(range(2003, laufend + 1)) if args.full_backfill else [laufend])
        heute = date.today()
        espn_tage = [heute + timedelta(days=d)
                     for d in range(-args.espn_back, args.espn_forward + 1)]
        log(f"Quellen: Saisons {saisons[0]}–{saisons[-1]}, "
            f"ESPN {espn_tage[0]}–{espn_tage[-1]}")
        gefunden = sammle(saisons, espn_tage, log)

    log(f"Insgesamt {len(gefunden)} Datensaetze von den Quellen")

    neu, stats = merge(seed, gefunden)
    log(f"Merge: {dict(stats)}")

    # --- Schutzschranken (Regel 4) ---
    fehler, warnungen = validate(neu)
    if fehler:
        log(f"ABBRUCH: {len(fehler)} Validierungsfehler nach dem Merge.")
        for e in fehler[:10]:
            log(f"  {e}")
        return 1
    if len(neu) < len(seed) - 5:
        log(f"ABBRUCH: Spielzahl faellt von {len(seed)} auf {len(neu)}.")
        return 1

    # Vergleich ueber die vollstaendig serialisierte Datei, NICHT ueber
    # content_hash: Der deckt nur id, Ergebnis, Toranzahl, Notiz und
    # Geschlecht ab (so ist er in MatchStore definiert und muss es bleiben,
    # damit die App denselben Wert berechnet). Terminverschiebungen,
    # praezisierte Anstosszeiten, Spieltagsnummern und Halbzeitstaende
    # aendern ihn nicht - genau die Faelle also, die zu Saisonbeginn am
    # haeufigsten sind. Mit content_hash als Abbruchkriterium haette die
    # Action solche Aktualisierungen nie committet.
    vorher_datei = canon_dump(seed)
    nachher_datei = canon_dump(neu)
    if vorher_datei == nachher_datei:
        log("Keine inhaltliche Aenderung – kein Commit.")
        _setze_output(changed="false", summary="keine Aenderung")
        return 0
    if content_hash(neu) == content_hash(seed):
        log("Hinweis: Aenderungen betreffen nur Felder ausserhalb des "
            "contentHash (Termine, Spieltage, Halbzeit). Die App loest damit "
            "keinen Seed-Re-Import aus – das Fenster liefert sie trotzdem aus.")

    if args.dry_run:
        log("--dry-run: nichts geschrieben.")
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    version = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")

    with open(os.path.join(args.out_dir, "seed_matches.json"), "w", encoding="utf-8") as f:
        f.write(canon_dump(neu) + "\n")

    kompakt = json.dumps({"matches": [{k: m.get(k) for k in MATCH_FIELDS} for m in neu]},
                         ensure_ascii=False, separators=(",", ":")).encode()
    _gz(os.path.join(args.out_dir, "seed_matches.json.gz"), kompakt)

    grenze = (date.today() - timedelta(days=args.window_days)).isoformat()
    fenster = [m for m in neu if m["date"][:10] >= grenze]
    fenster_bytes = json.dumps(
        {"schema": 1, "version": version, "windowFrom": grenze,
         "matches": [{k: m.get(k) for k in MATCH_FIELDS} for m in fenster]},
        ensure_ascii=False, separators=(",", ":")).encode()
    _gz(os.path.join(args.out_dir, "window.json.gz"), fenster_bytes)

    manifest = {
        "schema": 1,
        "version": version,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "matchCount": len(neu),
        "contentHash": content_hash(neu),
        "window": {"url": "window.json.gz", "from": grenze,
                   "bytes": _size(os.path.join(args.out_dir, "window.json.gz")),
                   "sha256": hashlib.sha256(fenster_bytes).hexdigest()},
        "full": {"url": "seed_matches.json.gz",
                 "bytes": _size(os.path.join(args.out_dir, "seed_matches.json.gz")),
                 "sha256": hashlib.sha256(kompakt).hexdigest()},
        "minSupportedBundle": "2026-01-01",
    }
    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    log(f"Artefakte: seed_matches.json ({len(neu)} Spiele), "
        f"window.json.gz ({manifest['window']['bytes']} B, {len(fenster)} Spiele), "
        f"seed_matches.json.gz ({manifest['full']['bytes']} B)")
    if warnungen:
        log(f"{len(warnungen)} Warnungen (kein Abbruch)")

    teile = [f"{v} {k}" for k, v in sorted(stats.items()) if k != "unveraendert"]
    _setze_output(changed="true", summary=", ".join(teile) or "aktualisiert")
    return 0


def _gz(pfad, daten):
    with gzip.GzipFile(pfad, "wb", compresslevel=9, mtime=0) as f:
        f.write(daten)


def _size(p):
    return os.path.getsize(p)


def _setze_output(**kv):
    ziel = os.environ.get("GITHUB_OUTPUT")
    if not ziel:
        return
    with open(ziel, "a", encoding="utf-8") as f:
        for k, v in kv.items():
            f.write(f"{k}={v}\n")


if __name__ == "__main__":
    sys.exit(main())
