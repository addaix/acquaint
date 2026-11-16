"""The operator's own record: ``operator: true`` in its frontmatter, one per store, reached as ``self``."""

import pytest

from acquaint import tools
from acquaint.lint import lint_store
from acquaint.lookup import find_entity, operator_keys
from acquaint.store import AcquaintError


def _rules(store):
    found = lint_store(store)
    return sorted((f["rule"], f["severity"]) for f in found["errors"] + found["warnings"] if f["rule"] == "operator-marker")


def test_self_names_the_one_marked_person(store, put):
    put(store, "people/ada-lovelace", meta={"name": "Ada Lovelace", "operator": True})
    put(store, "people/grace-example", meta={"name": "Grace Example"})
    assert operator_keys(store) == ["people/ada-lovelace"]
    assert find_entity(store, "self") == find_entity(store, "SELF") == "people/ada-lovelace"
    assert _rules(store) == []


def test_self_without_a_marker_or_with_two_says_why(store, put):
    put(store, "people/ada-lovelace", meta={"name": "Ada Lovelace"})
    with pytest.raises(AcquaintError, match="operator: true"):
        find_entity(store, "self")
    put(store, "people/grace-example", meta={"name": "Grace", "operator": True})
    put(store, "people/bob-example", meta={"name": "Bob", "operator": True})
    with pytest.raises(AcquaintError, match="ambiguous: people/bob-example, people/grace-example|ambiguous: people/grace-example, people/bob-example"):
        find_entity(store, "self")
    assert _rules(store) == [("operator-marker", "error")]


def test_the_marker_is_only_valid_as_true_on_a_person(store, put):
    put(store, "people/ada-lovelace", meta={"name": "Ada", "operator": "yes"})
    put(store, "orgs/example-org", meta={"name": "Example Org", "operator": True})
    assert operator_keys(store) == []
    assert _rules(store) == [("operator-marker", "error")] * 2


def test_a_record_literally_named_self_wins(store, put):
    put(store, "people/self", meta={"name": "Self Example"})
    put(store, "people/ada-lovelace", meta={"name": "Ada", "operator": True})
    assert find_entity(store, "self") == "people/self"


def test_who_and_brief_accept_self(tmp_path, monkeypatch):
    monkeypatch.setenv("ACQUAINT_DATA_DIR", str(tmp_path / "data"))
    tools.new("person", "Ada Lovelace")
    assert tools.who("self")["ok"] is False
    profile = tmp_path / "data" / "people" / "ada-lovelace" / "PROFILE.md"
    profile.write_text(profile.read_text().replace("---\n", "---\noperator: true\n", 1))
    assert tools.who("self")["id"] == "ada-lovelace"
    assert tools.who("self", field="operator")["value"] is True
    assert tools.brief("self")["id"] == "ada-lovelace"
