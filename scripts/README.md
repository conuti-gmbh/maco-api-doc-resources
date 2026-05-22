# Generator Scripts

Generator-Skripte für die Doku-Pipeline. Konsumieren `pruefi/` (Templater-Output, vom Sync-Workflow gemirrort) und Camunda-BPMN aus den drei Marktrollen-Prozess-Repos, produzieren `event-bauteil/` und `event/`.

| Skript | Story | Status |
|---|---|---|
| `filter_event_bauteile.py` | MACO-13040 | in Entwicklung — strip transaktionsdaten aus pruefi/ → event-bauteil/ |
| `parse_bpmn_events.py` | MACO-13040 | offen — BPMN-Parser für Event→Pruefi-Map |
| `compose_event_specs.py` | MACO-13040 | offen — Event-Specs aus event-bauteil/ + event-map komponieren |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r scripts/requirements.txt
```

Erfordert Python 3.11+.

## `filter_event_bauteile.py`

Liest `pruefi/<format>/<scope>/PI_<id>.yaml`, droppt die `transaktionsdaten`-Property aus dem Top-Container (samt `required`-Eintrag) und alle Container-Subset-Schemas, die unterhalb von `transaktionsdaten` lagen. Schreibt das Ergebnis nach `event-bauteil/<format>/<scope>/PI_<id>.yaml`.

```bash
# Voller Lauf
python scripts/filter_event_bauteile.py

# Subset für Dev-Loop
python scripts/filter_event_bauteile.py --filter-format 202604 --filter-scope UTILMD
python scripts/filter_event_bauteile.py --filter-pruefi 55001 -v
```

**Edge case:** Pruefis, die im Templater-Output nur `transaktionsdaten` als Root tragen (laut Pre-Flight-Inventur ~67 von 1010), würden nach Strip leer. Diese werden mit einer Warnung übersprungen, nicht emittiert — eine Event-Bauteil-Variante ist dort fachlich nicht sinnvoll.

**Exit-Codes:**
- `0` clean
- `1` Source-Verzeichnis fehlt oder ungültig
- `2` Source-Verzeichnis existiert, aber keine Specs gefunden

## Tests

```bash
pytest scripts/tests
```

Fixtures unter `scripts/tests/fixtures/`:
- `PI_99001.yaml` — Mini-PI mit `stammdaten` + `transaktionsdaten`, beide nicht-leer
- `PI_99002.yaml` — Mini-PI nur mit `transaktionsdaten` (Empty-After-Filter-Fall)

## Determinismus

Output ist deterministisch: zwei aufeinanderfolgende Läufe mit identischen Inputs produzieren byte-identische Files. Verifiziert per Test `test_output_is_deterministic_across_runs` und im Sync-Workflow als CI-Check.
