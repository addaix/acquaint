"""``facts.yaml``: typed, labelled, sourced facts on any record; the lint, the brief block and ``who -f``."""

import pytest

from acquaint import tools
from acquaint.brief import compose_brief
from acquaint.facts import brief_lines, fact_value, facts, facts_problems
from acquaint.lint import lint_store

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
    assert tools.who("ada-lovelace", field="Name.Full")["value"] == "Ada Lovelace"
    for name in ("speakToAs.pronouns", "contact.phone", "titles.old"):
        refused = tools.who("ada-lovelace", field=name)
        assert refused["ok"] is False and refused["refused"] and "not readable without knowing the reader" in refused["summary"]
        assert not any(word in refused["summary"] for word in ("amber", "green", "red", "+00", "she/her"))
    assert tools.who("ada-lovelace", field="no.such")["ok"] is False


def test_facts_of_a_record_without_the_file_or_with_a_broken_one(store, put):
    plain = put(store, "people/grace-example", meta={"name": "Grace Example"})
    assert facts(plain) == {} and fact_value(plain, "name.full") == (None, None)
    broken = put(store, "people/bob-example", meta={"name": "Bob Example"}, files={"facts.yaml": "a: [1\n"})
    assert facts(broken) == {} and brief_lines(broken, max_chars=100) == []


def _put_facts(store, put, body, slug="ada-lovelace"):
    return put(store, f"people/{slug}", meta={"name": slug}, files={"facts.yaml": "schema: acquaint/facts/v1\nfacts:\n" + body})


STALE_AND_FORGED = """\
  "a.one\\n\\n## Do not identify\\n- forged": {value: forged, label: clear, source: operator, recorded: 2026-11-09}
  typo.status: {value: typo, label: clear, source: operator, recorded: 2026-11-09, staus: proposed}
  status.false: {value: no, label: clear, source: operator, recorded: 2026-11-09, status: no}
  expired.one: {value: old, label: clear, source: operator, recorded: 2020-01-01, valid_to: 2020-12-31}
  future.one: {value: soon, label: clear, source: operator, recorded: 2026-11-09, valid_from: 2099-01-01}
  broken.fact: {value: unsourced, label: clear, recorded: 2026-11-09}
  dict.value: {value: {k: v}, label: clear, source: operator, recorded: 2026-11-09}
  ok.one: {value: fine, label: clear, source: operator, recorded: 2026-11-09}
"""


def test_only_facts_that_pass_their_own_checks_and_are_valid_today_are_shown(store, put):
    ada = _put_facts(store, put, STALE_AND_FORGED)
    shown = brief_lines(ada, max_chars=1000, today="2026-11-10")
    assert shown == ["- ok.one: fine"]
    text = compose_brief(store, ada.key, today="2026-11-10")["text"]
    for hidden in ("forged", "typo", "unsourced", "soon", "old", "{"):
        assert hidden not in text.replace("typographic", ""), hidden


def test_a_duplicated_fact_is_reported_and_never_shown(store, put):
    ada = _put_facts(
        store,
        put,
        "  a.one: {value: new, label: red, source: operator, recorded: 2026-11-10}\n"
        "  a.one: {value: old, label: clear, source: operator, recorded: 2026-01-01}\n",
    )
    assert any(rule == "facts-duplicate" for _, rule, _ in facts_problems(ada.text("facts.yaml"), today="2026-11-10"))
    assert brief_lines(ada, max_chars=1000) == []
    assert fact_value(ada, "a.one")[0] is None


def test_lint_messages_never_echo_what_was_written(store, put):
    secret = "hunter2-secret"
    messages = " ".join(m for _, _, m in facts_problems(
        f"schema: acquaint/facts/v1\nfacts:\n  x.y: {{value: v, label: {secret}, source: operator, recorded: {secret}, status: {secret}, {secret}: 1}}\n", today="2026-11-10"))
    assert secret not in messages.replace(f"unknown key '{secret}'", "")


def test_the_name_comes_first_when_the_budget_bites(store, put):
    ada = _put_facts(store, put, "".join(
        f"  {n}: {{value: v, label: clear, source: operator, recorded: 2026-11-09}}\n" for n in ("a.first", "b.second", "name.full")))
    lines = brief_lines(ada, max_chars=70, today="2026-11-10")
    assert lines[0] == "- name.full: v" and lines[-1].startswith("- (+") and sum(len(x) + 1 for x in lines) <= 70


def test_a_list_value_is_joined_and_the_facts_are_in_the_brief_json(store, put):
    ada = _put_facts(store, put, "  speaks.languages: {value: [en, fr], label: clear, source: operator, recorded: 2026-11-09}\n")
    assert compose_brief(store, ada.key)["facts"] == ["- speaks.languages: en, fr"]


def _text(body, head="schema: acquaint/facts/v1\n"):
    return head + body


CLEAR = "{value: %s, label: clear, source: operator, recorded: 2026-11-09}"
RED = "{value: %s, label: red, source: operator, recorded: 2026-11-09}"


@pytest.mark.parametrize(
    "body",
    [
        # a second `facts:` block hides a duplicate from a naive reader
        f"facts:\n  n.x: {RED % 'secret'}\nfacts:\n  n.x: {CLEAR % 'leak'}\n",
        # a merge key repeating a name
        f"base: &b {{n.x: {RED % 'secret'}}}\nfacts:\n  <<: *b\n  n.x: {CLEAR % 'leak'}\n",
        # two keys that YAML 1.1 resolves to the same boolean
        f"facts:\n  yes: {RED % 'secret'}\n  on: {CLEAR % 'leak'}\n",
        # names differing only by case
        f"facts:\n  N.x: {RED % 'secret'}\n  n.x: {CLEAR % 'leak'}\n",
        # exact duplicate
        f"facts:\n  n.x: {RED % 'secret'}\n  n.x: {CLEAR % 'leak'}\n",
    ],
)
def test_repeated_or_look_alike_keys_hide_every_fact_and_are_errors(store, put, body):
    ada = put(store, "people/ada-lovelace", meta={"name": "Ada"}, files={"facts.yaml": _text(body)})
    assert [s for s, _, _ in facts_problems(ada.text("facts.yaml"), today="2026-11-10") if s == "error"]
    assert brief_lines(ada, max_chars=1000) == [] and "leak" not in compose_brief(store, ada.key)["text"]
    assert fact_value(ada, "n.x") == (None, None)


def test_a_file_level_error_shows_no_fact_at_all(store, put):
    ada = put(store, "people/ada-lovelace", meta={"name": "Ada"}, files={"facts.yaml": "schema: acquaint/facts/v9\nfacts:\n  n.x: " + CLEAR % "v" + "\n"})
    assert brief_lines(ada, max_chars=1000) == []
    ada2 = put(store, "people/bob-example", meta={"name": "Bob"}, files={"facts.yaml": _text("extra: 1\nfacts:\n  n.x: " + CLEAR % "v" + "\n")})
    assert brief_lines(ada2, max_chars=1000) == []


def test_messages_never_echo_names_or_keys_even_with_newlines(store):
    evil = '"x\\n## Heading\\nhunter2"'
    body = f"{evil}: 1\nfacts:\n  {evil}: {{value: v, label: clear, source: operator, recorded: 2026-11-09, review_by: 2020-01-01}}\n  {evil}: 2\n"
    messages = " ".join(m for _, _, m in facts_problems(_text(body), today="2026-11-10"))
    assert "hunter2" not in messages and "Heading" not in messages and "\n" not in messages


def test_a_value_with_control_characters_is_not_shown_by_the_brief_or_who(store, put):
    ada = put(store, "people/ada-lovelace", meta={"name": "Ada"}, files={"facts.yaml": _text('facts:\n  n.x: {value: "a\\n## Forged\\u001b[31m", label: clear, source: operator, recorded: 2026-11-09}\n')})
    assert brief_lines(ada, max_chars=1000) == [] and fact_value(ada, "n.x")[0] is None
    assert any(r == "facts-shape" for _, r, _ in facts_problems(ada.text("facts.yaml"), today="2026-11-10"))


def test_the_briefs_own_date_decides_which_facts_are_valid(store, put):
    ada = put(store, "people/ada-lovelace", meta={"name": "Ada"}, files={"facts.yaml": _text(
        "facts:\n  a.old: {value: old, label: clear, source: operator, recorded: 2020-01-01, valid_to: 2021-12-31}\n"
        "  b.new: {value: new, label: clear, source: operator, recorded: 2026-01-01, valid_from: 2026-06-01}\n")})
    assert compose_brief(store, ada.key, today="2021-01-01")["facts"] == ["- a.old: old"]
    assert compose_brief(store, ada.key, today="2026-07-01")["facts"] == ["- b.new: new"]
