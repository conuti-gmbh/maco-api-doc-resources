# maco-api-doc-resources

Source of Truth für die in Apidog gerenderten API-Doku-Ressourcen rund um das BO4E-Datenmodell. Apidog konsumiert die Files aus diesem Repo (kein manuelles Apidog-Pflege-Bearbeiten mehr). Generierung erfolgt zukünftig automatisiert über einen GitHub-Workflow (siehe [MACO-13040](https://conuti.atlassian.net/browse/MACO-13040)).

## Status: POC-Befüllung (manuell, einmalig)

Diese Befüllung ist ein **POC-Snapshot** für ein Erst-Review durch die Apidog-Owner. Der reguläre Befüllungspfad — ein GitHub-Workflow im Stil von [`bo4e-generator/sync_bo4e_resources.yaml`](https://github.com/conuti-gmbh/bo4e-generator) — ist Teil von Story [MACO-13040](https://conuti.atlassian.net/browse/MACO-13040) und wird diesen Snapshot später byte-deterministisch reproduzieren bzw. überschreiben.

## Provenance

| Komponente | Quelle | Stand |
|---|---|---|
| `bo4e/` | `conuti/bo4e-schema` Tag `1.6.5` | `95f07d974cf2dc738ca248b258d6589025614cb0` |
| `pruefi/` | `conuti/maco-templater-app` Branch `feature/MACO-13039@CU_templater_openapi_spec_output` ([MACO-13072](https://conuti.atlassian.net/browse/MACO-13072), PR offen) | `6e98aaa8818d7b01b16786b130d743ea57fa960a` |

Die Provenance-SHAs sind zusätzlich pro Prüfi-Spec im `info`-Block als `x-bo4e-schema-sha` + `x-templater-sha` mitgeführt.

## Inhalt

```
bo4e/                   Atom-Tree aus bo4e-schema/docs/openapi-model/ (Tag 1.6.5)
  bo/                   Business-Objekte (Container-Top-Schemas + per-Property-Atome unter fields/bo/)
  cdoc/                 CDOC-Top-Schemas (ProcessData, Message, Stammdaten, Transaktionsdaten, ...)
  com/                  Komponenten (Adresse, Rufnummer, Produktpaket, ...)
  enum/                 Enum-Definitionen
  fields/               Atomare Property-Files (1 yaml = 1 Property mit type + description + x-descriptions.EN)
pruefi/                 Prüfi-OpenAPI-Specs (PI_<id>.yaml), pro Format-Version und Message-Scope
  202504/  202510/  202604/
    UTILMD/ MSCONS/ ORDERS/ ORDRSP/ ORDCHG/ INVOIC/ COMDIS/ APERAK/ IFTSTA/
    INSRPT/ PARTIN/ PRICAT/ QUOTES/ REMADV/ REQOTE/ UTILTS/ UTILMD_GAS/
```

**Specs-Inventar (POC-Snapshot):** 1010 Prüfi-Files über drei Format-Versionen × 17 Scopes; 28 598 externe `$ref`-Auflösungen gegen `bo4e/`, vollständig (0 missing).

## Spec-Format (Prüfi)

Pro Prüfi eine OpenAPI-3.1.0-Spec mit:

- `info.title = PI_<id>`, `info.version = <formatversion>`
- Provenance via `info.x-bo4e-schema-version`, `info.x-bo4e-schema-sha`, `info.x-templater-sha`
- `components.schemas` mit Container-Subset-Schemas pro Tiefenebene (`PI_<id>__<segment1>__<segment2>__...`)
- Skalare Leaf-Properties als externer `$ref` auf `bo4e/fields/<cdoc|bo|com>/<Object>/<property>.yaml` (atomares Single-Source-Pattern)
- `x-edifact-segment` als Liste pro Leaf (Multi-Segment-Fähigkeit)
- `required` pro Container (aggregiert aus den Templater-Pfad-Annotationen)

Kein `paths`-Block — die Specs sind reine Schema-Libraries für Composition durch Event-Bauteile (siehe unten).

## Noch nicht im Repo (kommt mit Stories 04 / 08)

| Sub-Tree | Quelle | Zugehörige Story |
|---|---|---|
| `bo4e-en/` | EN-gespiegelter Atom-Tree (aus `conuti/bo4e-schema-en` oder via Translator-OpenAPI-Endpoint) | [MACO-13036](https://conuti.atlassian.net/browse/MACO-13036) |
| `pruefi-en/` | EN-Variante der Prüfi-Specs (Container-Property-Names übersetzt, `$ref` auf `bo4e-en/`) | [MACO-13036](https://conuti.atlassian.net/browse/MACO-13036) + [MACO-13040](https://conuti.atlassian.net/browse/MACO-13040) |
| `event-bauteil/` | Composition aus `pruefi/` über `PI_*`-Refs, Mapping aus Camunda-BPMN (`E_*.bpmn`) | [MACO-13040](https://conuti.atlassian.net/browse/MACO-13040) |
| `event/` | optional: gebündelte Event-Specs pro Marktrolle | [MACO-13040](https://conuti.atlassian.net/browse/MACO-13040) |

## Hinweise für Apidog-Owner-Review

- **Cross-File-`$ref`-Auflösung verifizieren:** Apidog-Import muss die relativen `$ref`-Pfade (`../../../bo4e/fields/...`) gegen den `bo4e/`-Tree auflösen. Bei Apidog-Cloud-Sync aus einem Git-Repo sollte das standardmäßig funktionieren — bei Bedarf Sample-Spec (z.B. `pruefi/202604/UTILMD/PI_55001.yaml`) als Smoke-Test importieren.
- **Drift-Reparaturen sichtbar in PI_55001:** Heutiger Apidog-Stand hat hier strukturelle Drifts (extra `produkt`/`produktpaket`-Wrapper, fehlendes `rufnummern[].rufnummer`-Feld) — beide in dieser Spec behoben. Vollständiger Drift-Bericht: [MACO-13068](https://conuti.atlassian.net/browse/MACO-13068).
- **`x-edifact-segment` ist neu:** ersetzt die heutige HTML-`<TipInfo>…</TipInfo>`-Description-Hack-Lösung; als Custom-Extension von Apidog im Raw-Render sichtbar.
- **Connector-BO4E-Form-Specs (heute 362 numeric `<id>_<Beschreibung>`-Schemas) sind nicht enthalten** — separater Scope ([MACO-13036](https://conuti.atlassian.net/browse/MACO-13036) / [MACO-13040](https://conuti.atlassian.net/browse/MACO-13040)).

## Reproduzieren

```bash
# bo4e/ aus bo4e-schema Tag 1.6.5
git -C path/to/bo4e-schema archive 1.6.5 docs/openapi-model \
  | tar -x --strip-components=2 -C bo4e/

# pruefi/ aus Templater-Branch feature/MACO-13039@CU_templater_openapi_spec_output
rsync -a --delete \
  path/to/maco-templater-app/docs/api/openapi-pruefi/ \
  pruefi/
```
