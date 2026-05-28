# maco-api-doc-resources

Source of Truth für die in Apidog gerenderten API-Doku-Ressourcen rund um das BO4E-Datenmodell. Apidog konsumiert die Files aus diesem Repo (kein manuelles Apidog-Pflege-Bearbeiten mehr). Generiert wird über die Skripte unter `scripts/` ([MACO-13040](https://conuti.atlassian.net/browse/MACO-13040), Fertig 2026-05-27).

## Status: POC-Befüllung (vollständig)

Dieser Snapshot ist der **erste vollständige POC**: Atom-Tree (`bo4e/`), Prüfi-Specs (`pruefi/`), Event-Bauteile (`event-bauteil/`), Event-Specs (`event/`) und die Generator-Pipeline (`scripts/`) sind alle drin. Coverage- und Lücken-Übersicht visualisiert: **[`docs/event-spec-coverage-gaps.pdf`](docs/event-spec-coverage-gaps.pdf)** (8 Seiten).

Folge-Pfade (nicht Teil dieses POC):
- GitHub-Workflow zum automatisierten Refresh — Story [MACO-13087](https://conuti.atlassian.net/browse/MACO-13087).
- EN-Variante (`bo4e-en/`, EN-Event-Specs) — Story [MACO-13088](https://conuti.atlassian.net/browse/MACO-13088).

## Provenance

| Komponente | Quelle | Stand |
|---|---|---|
| `bo4e/` | `conuti/bo4e-schema` Tag `1.6.5` | `95f07d974cf2dc738ca248b258d6589025614cb0` |
| `pruefi/` | `conuti/maco-templater-app` Branch `feature/MACO-13039@CU_templater_openapi_spec_output` (Story 07 / [MACO-13039](https://conuti.atlassian.net/browse/MACO-13039) Fertig) | `6e98aaa8818d7b01b16786b130d743ea57fa960a` |
| `event-bauteil/` | `pruefi/` via `scripts/filter_event_bauteile.py` (Skript 1) | derived from Templater-SHA oben |
| `event/` | `event-bauteil/` + Camunda-BPMN (`T_*.bpmn`) + DMN (`S_EVENT_VARIABLEN.dmn`) via `scripts/parse_bpmn_events.py` + `extract_required_from_dmn.py` + `compose_event_specs.py` | Process-Repos auf `origin/dev`: lf `36717036`, nb `6b115946`, msb `5d34e665` |

Provenance-SHAs werden zusätzlich pro Spec im `info`-Block mitgeführt: `x-bo4e-schema-version` / `x-bo4e-schema-sha` / `x-templater-sha` (`pruefi/`, `event-bauteil/`) und `x-bpmn-source-sha` (`event/`).

## Inhalt

```
bo4e/                  Atom-Tree aus bo4e-schema/docs/openapi-model/ (Tag 1.6.5)   1497 yaml
  bo/                  Business-Objekte (Container-Top-Schemas + Atome unter fields/bo/)
  cdoc/                CDOC-Top-Schemas (ProcessData, Message, Stammdaten, Transaktionsdaten, ...)
  com/                 Komponenten (Adresse, Rufnummer, Produktpaket, ...)
  enum/                Enum-Definitionen
  fields/              Atomare Property-Files (1 yaml = 1 Property)

pruefi/                Prüfi-OpenAPI-Specs (PI_<id>.yaml) pro Format × Scope        1010 yaml
  202504/ 202510/ 202604/
    UTILMD/ MSCONS/ ORDERS/ ORDRSP/ ORDCHG/ INVOIC/ COMDIS/ APERAK/ IFTSTA/
    INSRPT/ PARTIN/ PRICAT/ QUOTES/ REMADV/ REQOTE/ UTILTS/ UTILMD_GAS/

event-bauteil/         pruefi/ minus `transaktionsdaten` (Stammdaten-Bauteile pro PI)  943 yaml
  <gleiche Format × Scope-Struktur wie pruefi/>

event/                 Event-Specs pro (Rolle, Topic) × Format                       555 yaml
  202504/ 202510/ 202604/ 202610/
    [LF]_<Topic>.yaml  [NB]_<Topic>.yaml  [MSB]_<Topic>.yaml

scripts/               Generator-Pipeline (Python 3.9+, ruamel.yaml + pytest, 53/53 Tests)
  filter_event_bauteile.py        (Skript 1: pruefi/ → event-bauteil/)
  parse_bpmn_events.py            (Skript 2: T_*.bpmn → event-mapping.json)
  extract_required_from_dmn.py    (Skript 4: S_EVENT_VARIABLEN.dmn → event-required-fields.json)
  compose_event_specs.py          (Skript 3: event-bauteil + JSONs → event/)

docs/event-spec-coverage-gaps.pdf  Visueller Coverage- + Lücken-Bericht (8 Seiten)
```

**Inventar:** 1497 + 1010 + 943 + 555 = **4 005 yaml-Specs**, 28 598 externe `$ref`-Auflösungen aus `pruefi/` gegen `bo4e/` (vollständig, 0 missing).

## Spec-Format (Prüfi)

Pro Prüfi eine OpenAPI-3.1.0-Spec mit:
- `info.title = PI_<id>`, `info.version = <formatversion>`
- Provenance via `info.x-bo4e-schema-version`, `info.x-bo4e-schema-sha`, `info.x-templater-sha`
- `components.schemas` mit Container-Subset-Schemas pro Tiefenebene (`PI_<id>__<segment1>__<segment2>__...`)
- Skalare Leaf-Properties als externer `$ref` auf `bo4e/fields/<cdoc|bo|com>/<Object>/<property>.yaml` (atomares Single-Source-Pattern)
- `x-edifact-segment` als Liste pro Leaf (Multi-Segment-Fähigkeit)
- `required` pro Container (aggregiert aus den Templater-Pfad-Annotationen)

Kein `paths`-Block — die Specs sind reine Schema-Libraries für Composition durch Event-Bauteile / Events.

## Spec-Format (Event-Bauteil)

Identisch zur Prüfi-Spec, **minus** dem `transaktionsdaten`-Teilbaum. Eine event-bauteil-Spec repräsentiert nur die **Stammdaten-Anforderungen** eines PI; die transaktionsdaten kommt aus dem Event-Wrapper. Wird von `event/`-Specs via `oneOf`-`$ref` referenziert.

## Spec-Format (Event)

Pro `(Rolle, Topic, Format)` eine OpenAPI-3.1.0-Spec mit:
- `info.title = [<ROLLE>] <Topic>` (mit optional ` GAS`-Suffix wenn alle Prüfis im 44xxx-Range)
- Provenance via `info.x-bpmn-source-sha`, `info.x-bo4e-schema-version`, `info.x-templater-sha`
- Topic = Suffix der Camunda-Prozess-`id` (`<ROLLE>-<format>-T_<eventName>` → `<eventName>`); kanonisch, weil Camunda darüber routet (siehe [MACO-13123](https://conuti.atlassian.net/browse/MACO-13123))
- `required: [stammdaten, transaktionsdaten, zusatzdaten]`
- `transaktionsdaten` = `allOf`(`$ref` auf `bo4e/cdoc/Transaktionsdaten.yaml` + lokaler Override mit Event-spezifischen Required-Feldern aus DMN)
- `transaktionsdaten.pruefidentifikator` regulär **optional**, mit `description`-Pool-Liste + `examples` (Beauskunftung statt Constraint — Camunda ermittelt den tatsächlich gesendeten Prüfi via Sparte + Transaktionsgrund + Empfänger-Marktrolle). **NNA-Sonderfall** (`[LF] START_VERSAND_ANTWORT_NNA`): `required` + `enum`, weil der Body-Wert dort das T_-Gateway routet.
- `zusatzdaten.eventname.const + .default` = Topic-Name
- `allOf: [oneOf: [...]]` über alle Event-Bauteil-`$ref`s im Topic-Pool — Semantik = Union-of-Required-Coverage, **kein** XOR-Branch, **kein** `discriminator`, **kein** `x-condition`, **kein** leeres `{}`.

### `x-pending-pruefis` + Stub

Wenn Prüfis im Event-BPMN sind, aber (noch) kein event-bauteil dazu existiert:

- **Partial:** `x-pending-pruefis: [<ids>]`-Extension + reduzierter `oneOf` (nur Prüfis mit Bauteil)
- **Stub:** kein `oneOf` (alle Prüfis pending) — nur Envelope + `x-pending-pruefis`

Macht die Coverage-Lücke im Artefakt sichtbar; ein Re-Run nach Templater-Nachzug entfernt den Marker automatisch. Aktueller POC-Stand: **354 voll / 15 partial / 186 Stub** (davon 136 in 202610, FUM noch nicht produziert; Rest in aktiven Formaten). Details + Cluster-Übersicht: [`docs/event-spec-coverage-gaps.pdf`](docs/event-spec-coverage-gaps.pdf).

## Hinweise für Apidog-Owner-Review

- **Cross-File-`$ref`-Auflösung verifizieren:** Apidog-Import muss die relativen `$ref`-Pfade
  - `pruefi/`/`event-bauteil/` → `../../../bo4e/fields/...` (Atome)
  - `event/` → `../../bo4e/cdoc/Transaktionsdaten.yaml#/components/schemas/Transaktionsdaten`
  - `event/` → `../../event-bauteil/<fmt>/<scope>/PI_<id>.yaml#/components/schemas/PI_<id>`
  
  gegen die jeweiligen Trees auflösen. Bei Apidog-Cloud-Sync aus dem Git-Repo sollte das standardmäßig funktionieren. Sample-Smoke-Tests:
  - `pruefi/202604/UTILMD/PI_55001.yaml` (typische Prüfi-Spec)
  - `event/202604/[LF]_START_LIEFERBEGINN.yaml` (typische Event-Spec, voll abgedeckt)
  - `event/202604/[LF]_START_VERSAND_ANTWORT_NNA.yaml` (NNA-Sonderfall mit required + enum)
  - `event/202610/[LF]_AENDERUNG_SD.yaml` (Stub mit `x-pending-pruefis`, 202610 noch ohne Templater-Output)
- **`examples`-Array-Rendering empirisch prüfen:** in den Event-Specs steht das im Topic mögliche Prüfi-Set als `pruefidentifikator.examples` zusätzlich zur `description`. Falls Apidog `examples` als Liste rendert → nettes Bonus-Feature; falls nicht → die `description`-Prosa-Liste trägt die Beauskunftung ohnehin.
- **`x-edifact-segment` ist eine Custom-Extension** (ersetzt die alte HTML-`<TipInfo>…</TipInfo>`-Description-Lösung). Sichtbar im Raw-Render.
- **Drift-Reparaturen sichtbar in `PI_55001`** gegenüber dem alten Apidog-Stand: extra `produkt`/`produktpaket`-Wrapper entfernt, fehlendes `rufnummern[].rufnummer`-Feld ergänzt (Detail [MACO-13068](https://conuti.atlassian.net/browse/MACO-13068)).
- **Connector-BO4E-Form-Specs** (heute 362 numeric `<id>_<Beschreibung>`-Schemas in Apidog) sind **nicht** Teil dieses POC — separater Scope, [MACO-13036](https://conuti.atlassian.net/browse/MACO-13036).

## Offene Klärungspunkte (Fachlichkeit / Camunda)

Diese betreffen Process-Repo-Pflege bzw. fachliche Klärungen, **nicht** den Generator-Code. Der Generator macht alle Lücken transparent (Stubs, `x-pending-pruefis`, Common-Core-Fallback) — externe Reviewer können den POC unabhängig davon prüfen.

| Ticket | Inhalt |
|---|---|
| [MACO-13123](https://conuti.atlassian.net/browse/MACO-13123) | BPMN-Drifts (Filename / `<bpmn:process name>` / `id`) — Celine geantwortet 2026-05-26, Korrektur-Scope pending |
| [MACO-13146](https://conuti.atlassian.net/browse/MACO-13146) | `martkrolle`-Typo Gateway-Routing-Bug in `T_AENDERUNG_SD.bpmn` (alle 4 Formate × 2 Conditions) |
| [MACO-13148](https://conuti.atlassian.net/browse/MACO-13148) | Handoff Fachlichkeit: 5 T_-Files ohne Event-Spec (`STORNO_PROZESS`/`STATUSMELDUNG`) · Klasse-2-Anomalie (80 Events mit `pruefidentifikator` aus `erpEvent.eventName`) · DMN-Fallback (26 Events ohne `S_EVENT_VARIABLEN`-Eintrag) |

## Reproduzieren

Voraussetzungen: Python 3.9+, Git, lokale Checkouts von `bo4e-schema`, `maco-templater-app`, `maco-{lf,nb,msb}-processes` (für die Event-Generierung).

```bash
# 1. bo4e/ aus bo4e-schema Tag 1.6.5
git -C path/to/bo4e-schema archive 1.6.5 docs/openapi-model \
  | tar -x --strip-components=2 -C bo4e/

# 2. pruefi/ aus dem Templater-POC-Snapshot (oder analog aus main, sobald MACO-13073 gemergt)
rsync -a --delete \
  path/to/maco-templater-app/docs/api/openapi-pruefi/ \
  pruefi/

# 3. Python-Env + Dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r scripts/requirements.txt

# 4. event-bauteil/ aus pruefi/  (Skript 1)
python -m scripts.filter_event_bauteile

# 5. Intermediate-JSONs aus den Process-Repos (Skript 2 + Skript 4)
#    PROCESSES_ROOT muss maco-{lf,nb,msb}-processes als Unterverzeichnisse enthalten,
#    jeweils auf origin/dev ausgecheckt (z.B. via `git worktree add`).
export PROCESSES_ROOT=/path/to/processes-root
python -m scripts.parse_bpmn_events       --processes-root "$PROCESSES_ROOT" --output event-mapping.json
python -m scripts.extract_required_from_dmn --processes-root "$PROCESSES_ROOT" --output event-required-fields.json

# 6. event/ aus event-bauteil/ + den beiden JSONs  (Skript 3)
python -m scripts.compose_event_specs
```

Output ist deterministisch — bei identischen Eingaben byte-identisch reproduzierbar. Tests via `pytest scripts/tests` (53/53 grün, Python 3.9.6 verifiziert).
