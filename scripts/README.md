# Generator Scripts

Generator-Skripte für die Doku-Pipeline. Konsumieren `pruefi/` (Templater-Output, vom Sync-Workflow gemirrort) und Camunda-BPMN aus den drei Marktrollen-Prozess-Repos, produzieren `event-bauteil/` und `event/`.

| Skript | Story | Status |
|---|---|---|
| `filter_event_bauteile.py` | MACO-13040 | ✓ implementiert — strip transaktionsdaten aus pruefi/ → event-bauteil/ |
| `parse_bpmn_events.py` | MACO-13040 | ✓ implementiert — BPMN-Parser für Event→Pruefi-Map aus T_*.bpmn |
| `extract_required_from_dmn.py` | MACO-13040 | ✓ implementiert — DMN-Parser für Event→Required-Fields aus S_EVENT_VARIABLEN.dmn |
| `compose_event_specs.py` | MACO-13040 | ✓ implementiert — Event-Specs aus event-bauteil/ + event-mapping.json + event-required-fields.json komponieren |
| `bundle_spec.py` | MACO-13229 | ✓ implementiert — atomare Specs zu einer Single-Spec je Format bündeln (Apidog-Importartefakt) |
| `check_refs.py` | MACO-13087 | ✓ implementiert — externe `$ref` aus pruefi/ event-bauteil/ event/ gegen vorhandene Files prüfen (CI-Gate) |
| `translate_specs.py` | MACO-13088 | ✓ implementiert — Specs per Translator-Endpoint übersetzen (pruefi/ → pruefi-en/, JSON→YAML), Refs auf `bo4e-en/` |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r scripts/requirements.txt
```

Erfordert Python 3.9+ (Skripte nutzen `from __future__ import annotations`; getestet gegen 3.9.6).

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

- Topic-Name aus der Prozess-**id** `<ROLE>-<format>-T_<eventName>` (z.B. `LF-202604-T_START_LIEFERBEGINN` → `START_LIEFERBEGINN`). Die id ist die kanonische Quelle (MACO-13123: Camunda korreliert darüber, `zusatzdaten.eventname` löst via `T_${eventName}` dagegen auf); Rolle-Token offen (`[A-Za-z]+`, kein `LF|NB|MSB`-Hardcode → neue Marktrollen ohne Code-Änderung). `<bpmn:process name>` ist nur ein lesbares Label (darf driften) und wird ignoriert. Fallback auf den Filename-Stamm bei non-konventioneller id. Cross-Check-Warnungen bei id-Rolle/Format ≠ Verzeichnis und id↔Filename-Drift.
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

## `extract_required_from_dmn.py`

Walks `<processes-root>/maco-{lf,nb,msb}-processes/<format>/S_TABELLEN/S_EVENT_VARIABLEN.dmn` (jede Camunda-DMN ermittelt vor dem T_-Aufruf in `G_EVENT_EINGANG.bpmn` die Body→Variable-Mappings pro Event). Pro Rule:

- Input-Spalte `eventName` → Event-Name
- ~37 Output-Spalten mit `FN:GetDataFromInbound(jsonPath=$.xxx)`-Strings
- JSONPath-Reads werden klassifiziert in `transaktionsdaten` / `stammdaten` / `zusatzdaten` / `other`
- Sonderfall `pruefidentifikator`-Spalte: erkennt ob `$.transaktionsdaten.pruefidentifikator` (Body-Required, NNA-Antwort-Logik) oder `$.zusatzdaten.erpEvent.eventName` (Korrelations-Variable, nicht PI-ID)

Output: deterministisches `event-required-fields.json` mit:
- `_provenance` — Repo→Format-Versionen→SHA-Map
- `_aggregate` — `common_core_transaktionsdaten` (Felder ≥ threshold) + Häufigkeitstabelle
- `events.<format>.<ROLE>.<eventName>` — `required_transaktionsdaten[]`, `required_zusatzdaten[]`, `stammdaten_reads[]`, `pruefidentifikator_source`, `description`, `jsonpaths{}`

```bash
# Voller Lauf
python -m scripts.extract_required_from_dmn --processes-root /path/to/checkouts

# Nur eine Rolle
python -m scripts.extract_required_from_dmn --processes-root /path --filter-role nb -v

# Common-Core-Threshold anpassen (Default 0.80)
python -m scripts.extract_required_from_dmn --processes-root /path --common-core-threshold 0.75
```

Working-Tree-Konvention identisch zu `parse_bpmn_events.py` (Caller stellt sicher, dass Process-Repos auf `dev` ausgecheckt sind). Exit-Codes 0/1/2.

## `compose_event_specs.py`

Komponiert pro `(format, ROLE, topic)` eine OpenAPI-3.1-Event-Spec aus drei Inputs: `event-bauteil/` (Skript 1), `event-mapping.json` (Skript 2), `event-required-fields.json` (Skript 4). Schreibt nach `event/<format>/[<ROLE>]_<TOPIC>.yaml`.

Modell (Stand 2026-05-27):

- Event-Wrapper `required: [stammdaten, transaktionsdaten, zusatzdaten]`.
- `transaktionsdaten` als **ein** Objekt, das nur die vom Event tatsächlich genutzten Felder zeigt (Schicht 1, DMN-abgeleitet aus `transaktionsdaten_reads`/`required_transaktionsdaten` von Skript 4; Fallback Aggregat-Common-Core). Skalar gelesene Felder → `$ref` auf das `fields/cdoc/Transaktionsdaten/<feld>`-Atom; verschachtelt gelesene Felder (z.B. `absender.rollencodenummer`) → fokussiertes Sub-Objekt nur über die gelesenen Subfelder (Atome des referenzierten BO). Ersetzt das frühere `allOf`(volles Transaktionsdaten + Required-Override), das Apidog als zwei nicht-mergebare `0`/`1`-Zweige rendert. Felder, die die DMN liest, die es aber im BO4E-Modell nicht gibt (z.B. `lokationsTyp`), landen in `x-unresolved-transaktionsdaten` statt required-aber-undefiniert.
- `transaktionsdaten.pruefidentifikator` regulär **optional, kein `enum`**: Beauskunftung über `description` (listet die im Topic möglichen Prüfis) + `examples`-Array. Der Prüfi wird in Camunda über Sparte + Transaktionsgrund + Empfänger-Marktrolle ermittelt; ein Sender-Wert wird ignoriert.
- **NNA-Sonderfall** (`pruefidentifikator_source == "transaktionsdaten"`, z.B. `[LF] START_VERSAND_ANTWORT_NNA`): stattdessen **required + `enum`**, weil der Body-Wert das T_-Gateway routet.
- `oneOf` über die Prüfi-Bauteile = Union-of-Required-Coverage (Sender muss die Stammdaten liefern, die irgendein Pool-Mitglied braucht), **kein** Discriminator, **kein** `x-condition`, **kein** leeres `{}`.
- Schema-Name bekommt `` GAS``-Suffix, wenn alle Prüfis im Pool im 44xxx-Bereich liegen.
- Scope (UTILMD/UTILMD_GAS/…) wird aus dem `event-bauteil/`-Baum per Prüfi-Id aufgelöst; Prüfis ohne Bauteil werden mit Warnung verworfen.
- Provenance-Header: `info.x-bpmn-source-sha` (aus `event-mapping.json`), `info.x-bo4e-schema-version` + `info.x-templater-sha` (aus einem repräsentativen Bauteil).

```bash
# Voller Lauf
python -m scripts.compose_event_specs

# Subset für Dev-Loop
python -m scripts.compose_event_specs --filter-format 202604 --filter-role lf --filter-topic START_LIEFERBEGINN -v
```

**Exit-Codes:**
- `0` clean
- `1` `--bauteil-dir` ist kein Verzeichnis
- `2` ein Input-File fehlt, oder keine Events verarbeitet

## Tests

```bash
pytest scripts/tests
```

Fixtures unter `scripts/tests/fixtures/`:
- `PI_99001.yaml` — Mini-PI mit `stammdaten` + `transaktionsdaten`, beide nicht-leer
- `PI_99002.yaml` — Mini-PI nur mit `transaktionsdaten` (Empty-After-Filter-Fall)
- `bpmn/T_MINI_SIMPLE.bpmn` — 2 ServiceTasks linear, keine Gateways → 2 Pruefis ohne Conditions
- `bpmn/T_MINI_BRANCHED.bpmn` — Sparte + Energierichtung Gateways → 3 Pruefis mit AND-konjunktiven Pfaden
- `dmn/S_MINI.dmn` — 3 Rules: Common-Core / NNA-style mit pruefi-im-Body / minimal mit erpEvent.eventName-pruefi-Source

## Determinismus

Output ist deterministisch: zwei aufeinanderfolgende Läufe mit identischen Inputs produzieren byte-identische Files. Verifiziert per `test_output_is_deterministic_across_runs` (Filter) + `test_cli_output_is_deterministic_across_runs` (BPMN-Parser) + `test_output_is_deterministic` (Composer) und im Sync-Workflow als CI-Check.
