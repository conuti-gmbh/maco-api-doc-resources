# Generator Scripts

Generator-Skripte für die Doku-Pipeline. Konsumieren `pruefi/` (Templater-Output, vom Sync-Workflow gemirrort) und Camunda-BPMN aus den drei Marktrollen-Prozess-Repos, produzieren `event-bauteil/` und `event/`.

| Skript | Story | Status |
|---|---|---|
| `filter_event_bauteile.py` | MACO-13040 | ✓ implementiert — strip transaktionsdaten aus pruefi/ → event-bauteil/ |
| `parse_bpmn_events.py` | MACO-13040 | ✓ implementiert — BPMN-Parser für Event→Pruefi-Map aus T_*.bpmn |
| `compose_event_specs.py` | MACO-13040 | offen — Event-Specs aus event-bauteil/ + event-mapping.json komponieren |

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

## `parse_bpmn_events.py`

Walks `<processes-root>/maco-{lf,nb,msb}-processes/<format>/T_PROZESSE/T_*.bpmn` und extrahiert pro `<bpmn:process>`:

- Topic-Name aus `<bpmn:process name="…">` (Token vor Doppelpunkt — z.B. `START_LIEFERBEGINN: Anmeldung …` → `START_LIEFERBEGINN`)
- alle `<camunda:inputParameter name="pruefidentifikator">` aus descendant ServiceTasks
- pro Pruefi: AND-konjunktive Bedingungspfade vom Start-Event zum ServiceTask via Backward-Walk durch `<bpmn:sequenceFlow>` mit `<bpmn:conditionExpression>`

Mehrere eingehende Pfade auf denselben ServiceTask → mehrere Pfade in der `paths`-Liste (OR von ANDs). Skript 3 entscheidet Encoding.

Output: deterministisches `event-mapping.json` mit `_provenance` (Repo-SHAs) + `events.<format>.<role>.<topic>.{process_id, process_name_raw, source, pruefis:[{id, paths:[[cond,...]]}]}`.

```bash
# Voller Lauf
python -m scripts.parse_bpmn_events --processes-root /path/to/checkouts

# Subset
python -m scripts.parse_bpmn_events --processes-root /path --filter-format 202604 --filter-role lf -v
```

### Working-Tree-Convention

Skript konsumiert das Filesystem, nicht Git. Caller ist verantwortlich, dass die drei Repos auf `dev` ausgecheckt sind:

- **GHA:** `actions/checkout` pro Repo mit `ref: dev`, `path: repos/maco-<role>-processes`. Aufruf `--processes-root $GITHUB_WORKSPACE/repos`.
- **Lokal:** Einmalig Worktrees anlegen, z.B.:
  ```bash
  for role in lf nb msb; do
    git -C ~/PhpstormProjects/conuti/maco-${role}-processes worktree add ~/maco-dev/maco-${role}-processes origin/dev
  done
  python -m scripts.parse_bpmn_events --processes-root ~/maco-dev
  ```

**Exit-Codes:**
- `0` clean
- `1` processes-root ist kein Verzeichnis
- `2` keine T_*.bpmn unter processes-root gefunden

## Tests

```bash
pytest scripts/tests
```

Fixtures unter `scripts/tests/fixtures/`:
- `PI_99001.yaml` — Mini-PI mit `stammdaten` + `transaktionsdaten`, beide nicht-leer
- `PI_99002.yaml` — Mini-PI nur mit `transaktionsdaten` (Empty-After-Filter-Fall)
- `bpmn/T_MINI_SIMPLE.bpmn` — 2 ServiceTasks linear, keine Gateways → 2 Pruefis ohne Conditions
- `bpmn/T_MINI_BRANCHED.bpmn` — Sparte + Energierichtung Gateways → 3 Pruefis mit AND-konjunktiven Pfaden

## Determinismus

Output ist deterministisch: zwei aufeinanderfolgende Läufe mit identischen Inputs produzieren byte-identische Files. Verifiziert per `test_output_is_deterministic_across_runs` (Filter) + `test_cli_output_is_deterministic_across_runs` (BPMN-Parser) und im Sync-Workflow als CI-Check.
