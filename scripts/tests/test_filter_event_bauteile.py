"""Tests for filter_event_bauteile.py."""

from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

# Import the script as a module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import filter_event_bauteile as fil  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures"


def _make_corpus(tmp_path: Path, fixture_name: str) -> Path:
    """Copy a fixture into a pruefi/<format>/<scope>/ layout under tmp_path."""
    source = tmp_path / "pruefi" / "202604" / "UTILMD"
    source.mkdir(parents=True)
    shutil.copy(FIXTURES / fixture_name, source / fixture_name)
    return tmp_path / "pruefi"


def _load_yaml(path: Path) -> dict:
    yaml = YAML(typ="rt")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.load(fh)


def test_filter_drops_transaktionsdaten_property_and_required(tmp_path: Path) -> None:
    source = _make_corpus(tmp_path, "PI_99001.yaml")
    target = tmp_path / "event-bauteil"

    seen, written, skipped = fil.process(
        source,
        target,
        filter_format=None,
        filter_scope=None,
        filter_pruefi=None,
        verbose=False,
    )

    assert (seen, written, skipped) == (1, 1, 0)
    output = _load_yaml(target / "202604" / "UTILMD" / "PI_99001.yaml")
    top = output["components"]["schemas"]["PI_99001"]
    assert "transaktionsdaten" not in top["properties"]
    assert "stammdaten" in top["properties"]
    assert top["required"] == ["stammdaten"]


def test_filter_drops_transaktionsdaten_container_subset_schemas(
    tmp_path: Path,
) -> None:
    source = _make_corpus(tmp_path, "PI_99001.yaml")
    target = tmp_path / "event-bauteil"
    fil.process(
        source, target, filter_format=None, filter_scope=None,
        filter_pruefi=None, verbose=False,
    )
    output = _load_yaml(target / "202604" / "UTILMD" / "PI_99001.yaml")
    schemas = output["components"]["schemas"]
    assert "PI_99001__transaktionsdaten" not in schemas
    assert "PI_99001__transaktionsdaten__absender" not in schemas
    assert "PI_99001__stammdaten" in schemas
    assert "PI_99001__stammdaten__MARKTLOKATION" in schemas


def test_filter_skips_specs_that_become_empty(tmp_path: Path) -> None:
    """A spec with only transaktionsdaten at root has no event-bauteil form."""
    source = _make_corpus(tmp_path, "PI_99002.yaml")
    target = tmp_path / "event-bauteil"

    seen, written, skipped = fil.process(
        source, target, filter_format=None, filter_scope=None,
        filter_pruefi=None, verbose=False,
    )

    assert (seen, written, skipped) == (1, 0, 1)
    assert not (target / "202604" / "UTILMD" / "PI_99002.yaml").exists()


def test_output_is_deterministic_across_runs(tmp_path: Path) -> None:
    source = _make_corpus(tmp_path, "PI_99001.yaml")
    target_a = tmp_path / "out_a"
    target_b = tmp_path / "out_b"
    for target in (target_a, target_b):
        fil.process(
            source, target, filter_format=None, filter_scope=None,
            filter_pruefi=None, verbose=False,
        )
    file_a = (target_a / "202604" / "UTILMD" / "PI_99001.yaml").read_bytes()
    file_b = (target_b / "202604" / "UTILMD" / "PI_99001.yaml").read_bytes()
    assert file_a == file_b


def test_filter_pruefi_predicate_selects_one_spec(tmp_path: Path) -> None:
    source_root = tmp_path / "pruefi" / "202604" / "UTILMD"
    source_root.mkdir(parents=True)
    shutil.copy(FIXTURES / "PI_99001.yaml", source_root / "PI_99001.yaml")
    shutil.copy(FIXTURES / "PI_99002.yaml", source_root / "PI_99002.yaml")
    target = tmp_path / "event-bauteil"

    seen, written, _ = fil.process(
        tmp_path / "pruefi", target,
        filter_format=None, filter_scope=None,
        filter_pruefi="99001", verbose=False,
    )

    assert (seen, written) == (1, 1)
    assert (target / "202604" / "UTILMD" / "PI_99001.yaml").exists()
    assert not (target / "202604" / "UTILMD" / "PI_99002.yaml").exists()


def test_main_returns_no_specs_exit_when_source_is_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = fil.main(["--source", str(empty), "--target", str(tmp_path / "out")])
    assert rc == fil.EXIT_NO_SPECS


def test_main_returns_error_when_source_does_not_exist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = fil.main([
        "--source", str(tmp_path / "does-not-exist"),
        "--target", str(tmp_path / "out"),
    ])
    assert rc == fil.EXIT_ERROR
