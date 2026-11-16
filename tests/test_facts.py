"""``facts.yaml``: typed, labelled, sourced facts on any record; the lint, the brief block and ``who -f``."""

import pytest

from acquaint import tools
from acquaint.brief import compose_brief
from acquaint.facts import brief_lines, fact_value, facts, facts_problems
from acquaint.lint import lint_store
from acquaint.store import AcquaintError, Entity

GOOD = """\
schema: acquaint/facts/v1
facts:
  name.full: {value: Ada Lovelace, label: clear, source: 'self: "I write it Ada Lovelace"', recorded: 2026-11-09}
  titles.current:
    value: Analyst
    label: clear
    source: https://example.org/about
    recorded: 2026-11-09
    valid_from: 2023-01-01
    review_by: 2027-05-09
  speakToAs.pronouns: {value: she/her, label: green, source: operator, recorded: 2026-11-09}
  contact.phone: {value: "+00 000 0000", label: amber, source: operator, recorded: 2026-11-09}
  residence.city: {value: Nowhere, label: red, source: operator, recorded: 2026-11-09}
  titles.old: {value: Clerk, label: clear, source: operator, recorded: 2026-11-09, status: proposed}
to_confirm: ["time zone is inferred"]
"""


def _rules(text, today="2026-11-10"):
    return sorted(rule for _, rule, _ in facts_problems(text, today=today))


@pytest.fixture
def ada(store, put):
    return put(store, "people/ada-lovelace", meta={"name": "Ada Lovelace"}, files={"facts.yaml": GOOD})


def test_a_labelled_sourced_dated_file_lints_clean():
    assert facts_problems(GOOD, today="2026-11-10") == []


@pytest.mark.parametrize(
    "fact, rule",
    [
        ("{value: x, label: clear, recorded: 2026-11-09}", "facts-unsourced"),
        ("{value: x, label: clear, source: 2026-11-09, recorded: 2026-11-09}", "facts-unsourced"),
        ("{value: x, label: secret, source: operator, recorded: 2026-11-09}", "facts-label"),
        ("{value: x, label: clear, source: operator}", "facts-date"),
        ("{value: x, label: clear, source: operator, recorded: yesterday}", "facts-date"),
        ("{label: clear, source: operator, recorded: 2026-11-09}", "facts-shape"),
        ("{value: x, label: clear, source: operator, recorded: 2026-11-09, status: maybe}", "facts-shape"),
        ("{value: x, label: clear, source: operator, recorded: 2026-11-09, review_by: 2026-01-01}", "facts-review"),
    ],
)
def test_each_missing_piece_is_reported(fact, rule):
    assert _rules(f"schema: acquaint/facts/v1\nfacts:\n  name.full: {fact}\n") == [rule]


def test_shape_errors():
    assert _rules("facts: {}\n") == ["facts-shape"]
    assert _rules("schema: acquaint/facts/v1\nfacts: [1]\n") == ["facts-shape"]
    assert _rules("schema: acquaint/facts/v1\nfacts:\n  Bad Name: {value: x, label: clear, source: operator, recorded: 2026-11-09}\n") == ["facts-shape"]
    assert _rules("a: [1\n") == ["unparseable"]


def test_lint_reports_a_broken_facts_file_on_its_record(store, put):
    put(store, "people/ada-lovelace", meta={"name": "Ada Lovelace"}, files={"facts.yaml": "schema: acquaint/facts/v1\nfacts:\n  name.full: {value: x, label: clear, recorded: 2026-11-09}\n"})
    found = [f for f in lint_store(store)["errors"] if f["file"] == "facts.yaml"]
    assert [f["rule"] for f in found] == ["facts-unsourced"]


def test_the_brief_shows_clear_current_facts_only(store, ada):
    text = compose_brief(store, ada.key)["text"]
    assert "name.full: Ada Lovelace" in text and "titles.current: Analyst" in text
    for hidden in ("she/her", "+00 000 0000", "Nowhere", "Clerk"):
        assert hidden not in text, hidden


def test_the_facts_block_stays_within_its_budget(store, ada):
    lines = brief_lines(ada, max_chars=40)
    assert sum(len(x) + 1 for x in lines[:-1]) <= 40 and "more" in lines[-1]
    assert brief_lines(ada, max_chars=10_000) == ["- name.full: Ada Lovelace", "- titles.current: Analyst"]


def test_who_reads_a_dotted_fact_and_refuses_the_stricter_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("ACQUAINT_DATA_DIR", str(tmp_path / "data"))
    tools.new("person", "Ada Lovelace")
    (tmp_path / "data" / "people" / "ada-lovelace" / "facts.yaml").write_text(GOOD)
    got = tools.who("ada-lovelace", field="name.full")
    assert got["ok"] and got["value"] == "Ada Lovelace"
    assert tools.who("ada-lovelace", field="titles.current")["value"] == "Analyst"
    for name in ("speakToAs.pronouns", "contact.phone", "titles.old"):
        with pytest.raises(AcquaintError, match="not readable without knowing the reader"):
            tools.who("ada-lovelace", field=name)
    assert tools.who("ada-lovelace", field="no.such")["ok"] is False


def test_facts_of_a_record_without_the_file_or_with_a_broken_one(store, put):
    plain = put(store, "people/grace-example", meta={"name": "Grace Example"})
    assert facts(plain) == {} and fact_value(plain, "name.full") == (None, None)
    broken = put(store, "people/bob-example", meta={"name": "Bob Example"}, files={"facts.yaml": "a: [1\n"})
    assert facts(broken) == {} and isinstance(broken, Entity)
