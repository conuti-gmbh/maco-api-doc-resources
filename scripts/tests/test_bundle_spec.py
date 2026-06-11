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
