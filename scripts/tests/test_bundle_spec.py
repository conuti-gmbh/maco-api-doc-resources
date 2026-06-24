"""Unit tests for the readable-key scheme + collision fallback in bundle_spec."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bundle_spec as bs  # noqa: E402

ROOT = Path("/repo")


def rk(rel: str, name: str) -> str:
    return bs.readable_key(ROOT, ROOT / rel, name)


@pytest.mark.parametrize(
    "rel,name,expected",
    [
        ("bo4e/bo/Marktlokation.yaml", "Marktlokation", "bo.Marktlokation"),
        ("bo4e/enum/MarktlokationsTyp.yaml", "MarktlokationsTyp", "enum.MarktlokationsTyp"),
        ("bo4e/com/Adresse.yaml", "Adresse", "com.Adresse"),
        ("bo4e/cdoc/Transaktionsdaten.yaml", "Transaktionsdaten", "cdoc.Transaktionsdaten"),
        ("bo4e/fields/bo/Marktlokation/datenqualitaet.yaml", "datenqualitaet",
         "field.Marktlokation.datenqualitaet"),
        ("pruefi/202604/UTILMD/PI_55109.yaml", "PI_55109__stammdaten__MARKTLOKATION",
         "pruefi.PI_55109.stammdaten.MARKTLOKATION"),
        ("event-bauteil/202604/UTILMD/PI_55109.yaml", "PI_55109__stammdaten",
         "bauteil.PI_55109.stammdaten"),
        ("event/202604/[NB]_START_VERSAND_LIEFERSCHEIN.yaml", "[NB] START_VERSAND_LIEFERSCHEIN",
         "event.NB.START_VERSAND_LIEFERSCHEIN"),
    ],
)
def test_readable_key_patterns(rel, name, expected):
    assert rk(rel, name) == expected


def test_readable_key_drops_format_and_scope():
    # Same PI id, different format/scope -> same readable key (scope/format dropped).
    a = rk("pruefi/202604/UTILMD/PI_55109.yaml", "PI_55109")
    b = rk("pruefi/202610/UTILMD/PI_55109.yaml", "PI_55109")
    assert a == b == "pruefi.PI_55109"


def test_keymap_unique_uses_readable():
    pairs = [
        (ROOT / "bo4e/bo/Marktlokation.yaml", "Marktlokation"),
        (ROOT / "bo4e/fields/bo/Marktlokation/datenqualitaet.yaml", "datenqualitaet"),
    ]
    keymap, fallbacks = bs.build_keymap(ROOT, pairs)
    assert fallbacks == 0
    assert keymap[pairs[0]] == "bo.Marktlokation"
    assert keymap[pairs[1]] == "field.Marktlokation.datenqualitaet"


def test_keymap_collision_falls_back_to_verbose():
    # Two field atoms whose Owner+field collide across tiers -> same readable key.
    a = (ROOT / "bo4e/fields/bo/Marktlokation/datenqualitaet.yaml", "datenqualitaet")
    b = (ROOT / "bo4e/fields/com/Marktlokation/datenqualitaet.yaml", "datenqualitaet")
    assert rk("bo4e/fields/bo/Marktlokation/datenqualitaet.yaml", "datenqualitaet") == rk(
        "bo4e/fields/com/Marktlokation/datenqualitaet.yaml", "datenqualitaet"
    )
    keymap, fallbacks = bs.build_keymap(ROOT, [a, b])
    assert fallbacks == 2
    # Both fell back to the path-qualified verbose key -> distinct + injective.
    assert keymap[a] == bs.verbose_key(ROOT, *a)
    assert keymap[b] == bs.verbose_key(ROOT, *b)
    assert keymap[a] != keymap[b]
    assert len(set(keymap.values())) == 2


def test_rewrite_neutralizes_json_schema_meta_ref():
    """A $ref to a JSON-Schema meta-schema (BO4E object.meta's allOf) is dropped
    to a permissive empty schema; sibling keywords survive. Localizable component
    $refs and external example URLs are untouched."""
    node = {
        "allOf": [{"$ref": "https://json-schema.org/draft/2020-12/schema"}],
        "properties": {
            "x-descriptions": {"$ref": bs._FRAG + "field.foo"},
            "example": {"$ref": "https://raw.githubusercontent.com/x/y/bo/Foo.json"},
        },
    }
    keymap = {bs.resolve(ROOT / "bo4e/enum/object.meta.yaml",
                          bs._FRAG + "field.foo"): "field.foo"}
    out = bs.rewrite(node, ROOT / "bo4e/enum/object.meta.yaml", keymap)
    # meta-schema ref gone -> empty schema in the allOf branch
    assert out["allOf"] == [{}]
    # localizable component ref preserved (already a local key here)
    assert out["properties"]["x-descriptions"] == {"$ref": bs._FRAG + "field.foo"}
    # external example URL untouched
    assert out["properties"]["example"]["$ref"].startswith("https://raw.githubusercontent.com")


@pytest.mark.parametrize(
    "rel,name,expected",
    [
        ("bo4e-en/bo/BusinessPartner.yaml", "BusinessPartner", "bo.BusinessPartner"),
        ("bo4e-en/fields/bo/BusinessPartner/salutation.yaml", "salutation",
         "field.BusinessPartner.salutation"),
        ("event-bauteil-en/202604/UTILMD/PI_55001.yaml", "PI_55001__masterData",
         "bauteil.PI_55001.masterData"),
        ("pruefi-en/202604/UTILMD/PI_55001.yaml", "PI_55001__masterData__MARKET_LOCATION",
         "pruefi.PI_55001.masterData.MARKET_LOCATION"),
        ("event-en/202604/[LF]_START_ABR_NN.yaml", "[LF] START_ABR_NN",
         "event.LF.START_ABR_NN"),
    ],
)
def test_readable_key_en_trees_match_de_scheme(rel, name, expected):
    # EN trees (*-en/) get the same readable keys as their DE counterparts, not a
    # verbose bo4e-en.* / event-en.* fallback (MACO-13088 EN bundle parity).
    assert rk(rel, name) == expected
