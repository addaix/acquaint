"""Digests: one file per conversation, a watermark per scope, anchored items, and the tools and brief that read them."""

import re

import pytest

from acquaint import tools
from acquaint.digests import append_batch, parse_rows, digest_problems, digests, find_digest, mark_digest, new_digest
from acquaint.lint import lint_store
from acquaint.store import AcquaintError

REF = "whatsapp:ada-lovelace"


def test_create_mark_and_find_by_ref(store, put):
    ada = put(store, "people/ada-lovelace", meta={"name": "Ada Lovelace"})
    made = new_digest(ada, REF, today="2026-11-04")
    assert made["file"] == "digests/whatsapp-ada-lovelace.md" and made["through"] == {}
    with pytest.raises(AcquaintError):
        new_digest(ada, REF)
    marked = mark_digest(ada, REF, "2026-11-03T19:37:47+01:00", today="2026-11-04")
    assert marked["through"] == {"content": "2026-11-03T19:37:47+01:00"} and marked["previous"] is None
    mark_digest(ada, REF, "2026-06-21T19:27:45+02:00", scope="style")
    assert find_digest(ada, REF)["through"]["style"] == "2026-06-21T19:27:45+02:00"
    assert [d["ref"] for d in digests(ada)] == [REF]


def test_the_watermark_does_not_move_back_without_force(store, put):
    ada = put(store, "people/ada-lovelace", meta={"name": "Ada Lovelace"})
    new_digest(ada, REF)
    mark_digest(ada, REF, "2026-11-03T19:00:00+01:00")
    with pytest.raises(AcquaintError, match="needs force"):
        mark_digest(ada, REF, "2026-10-01T00:00:00Z")
    assert mark_digest(ada, REF, "2026-10-01T00:00:00Z", force=True)["previous"] == "2026-11-03T19:00:00+01:00"
    with pytest.raises(AcquaintError, match="ISO 8601"):
        mark_digest(ada, REF, "last Tuesday")
    with pytest.raises(AcquaintError, match="scope"):
        mark_digest(ada, REF, "2026-10-01T00:00:00Z", scope="mood")


def test_items_need_anchors_and_quotes_stay_short():
    text = (
        "---\nref: whatsapp:ada-lovelace\nthrough:\n  content: '2026-11-03T19:00:00+01:00'\n---\n"
        "## Orientation\nTwo people, about the engine.\n"
        "## Takeaways\n- Agreed to ship the loom on Friday. [at: 2026-07-03T14:02:11+02:00]\n"
        "- Agreed to something unanchored.\n"
        '## Themes\n- Cost: "' + "word " * 30 + '" [at: 2026-07-04T09:00:00+02:00]\n'
    )
    found = {(severity, rule) for severity, _, rule, _ in digest_problems(text)}
    assert found == {("error", "unanchored"), ("warning", "long-quote")}
    bad = "---\nref: nonsense\nthrough:\n  content: soon\n  mood: '2026-01-01'\n---\n"
    rules = [rule for _, _, rule, _ in digest_problems(bad)]
    assert rules.count("digest-through") == 2 and "digest-ref" in rules


def test_lint_reports_digest_problems(store, put):
    put(
        store,
        "people/ada-lovelace",
        meta={"name": "Ada Lovelace"},
        files={"digests/whatsapp-ada-lovelace.md": "---\nref: whatsapp:ada-lovelace\nthrough: {}\n---\n## Takeaways\n- No anchor here.\n"},
    )
    found = [f for f in lint_store(store)["errors"] if f["file"].startswith("digests/")]
    assert [f["rule"] for f in found] == ["unanchored"]


def test_the_tools_and_the_brief(tmp_path, monkeypatch):
    monkeypatch.setenv("ACQUAINT_DATA_DIR", str(tmp_path / "data"))
    tools.new("person", "Ada Lovelace")
    missing = tools.digest("ada-lovelace", REF)
    assert missing["ok"] is False and missing["since"] is None
    assert tools.digest_new("ada-lovelace", REF)["ok"]
    assert tools.digest_mark("ada-lovelace", REF, through="2026-11-03T19:37:47+01:00")["ok"]
    one = tools.digest("ada-lovelace", REF)
    assert one["since"] == "2026-11-03T19:37:47+01:00"
    assert tools.digest("ada-lovelace")["digests"][0]["ref"] == REF
    text = tools.brief("ada-lovelace")["text"]
    assert "## Conversations on record" in text and "content through 2026-11-03T19:37:47+01:00" in text


BATCH = {
    "Takeaways": ["Agreed to meet again [at: 2026-11-05T10:00:00+01:00]"],
    "Open threads": ["Ada owes the draft [source: thread-4]"],
    "Orientation": ["Now spans two weeks"],
}


def _ada(store, put):
    ada = put(store, "people/ada-lovelace", meta={"name": "Ada Lovelace"})
    new_digest(ada, REF)
    return ada


def test_append_batch_adds_a_dated_block_under_each_section_and_moves_the_watermark(store, put):
    ada = _ada(store, put)
    result = append_batch(ada, REF, heading="2026-11-05 to 2026-11-12", rows=BATCH, through="2026-11-12T09:00:00+01:00", today="2026-11-13")
    assert result["through"]["content"] == "2026-11-12T09:00:00+01:00" and result["lines"] == 3
    text = ada[find_digest(ada, REF)["file"]]
    for section in ("Orientation", "Takeaways", "Open threads"):
        part = text.split(f"## {section}")[1].split("\n## ")[0]
        assert "### 2026-11-05 to 2026-11-12" in part
    assert "- Agreed to meet again [at: 2026-11-05T10:00:00+01:00]" in text
    assert text.index("## Themes") < text.index("## Index") < text.index("## Open threads")
    assert not [p for p in digest_problems(text) if p[0] == "error"]
    with pytest.raises(AcquaintError, match="already has"):
        append_batch(ada, REF, heading="2026-11-05 to 2026-11-12", rows=BATCH)


def _unchanged_after(ada, **kwargs):
    name = find_digest(ada, REF)["file"]
    before = ada[name]
    with pytest.raises(AcquaintError) as error:
        append_batch(ada, REF, **kwargs)
    assert ada[name] == before, "nothing is written when the call is refused"
    return str(error.value)


@pytest.mark.parametrize(
    "rows, match",
    [
        ({"Orientation": ["fine"], "Index": ["no anchor"]}, "needs \\[at"),
        ({"Index": ["ok [at: 2026-11-05T10:00:00Z]\n- bare item"]}, "one bullet"),
        ({"Index": ["x <!-- [at: 2026-11-05T10:00:00Z] -->"]}, "comment or code fence"),
        ({"Orientation": "hello"}, "list of lines"),
        ({"Orientation": None}, "list of lines"),
        ({"Index": ["x [at: 2026-11-05T10:00:00Z] <!--"], "Open threads": ["new bare -->"]}, "comment or code fence"),
        ({"Index": ["```x [at: 2026-11-05T10:00:00Z]"]}, "comment or code fence"),
        ({"Mood": ["x"]}, "unknown section"),
        ({}, "no lines"),
    ],
)
def test_append_batch_refuses_bad_rows_and_writes_nothing(store, put, rows, match):
    assert re.search(match, _unchanged_after(_ada(store, put), heading="w1", rows=rows, through="2026-11-12T09:00:00Z"))


def test_a_failing_watermark_leaves_the_digest_untouched_and_the_call_retryable(store, put):
    ada = _ada(store, put)
    mark_digest(ada, REF, "2026-11-12T09:00:00Z")
    for bad in ("nonsense", "2026-01-01T00:00:00Z"):
        _unchanged_after(ada, heading="w1", rows=BATCH, through=bad)
    _unchanged_after(ada, heading="w1", rows=BATCH, through="2026-11-13T00:00:00Z", scope="mood")
    assert append_batch(ada, REF, heading="w1", rows=BATCH, through="2026-11-13T00:00:00Z")["lines"] == 3


def test_append_batch_heading_and_missing_pieces(store, put):
    ada = _ada(store, put)
    assert "one-line heading" in _unchanged_after(ada, heading="a\nb", rows=BATCH)
    append_batch(ada, REF, heading="### w1", rows={"Orientation": ["x"]})
    assert "\n### w1\n" in ada[find_digest(ada, REF)["file"]]
    with pytest.raises(AcquaintError, match="no digest"):
        append_batch(ada, "slack:nobody", heading="w1", rows=BATCH)


def test_parse_rows_reads_json_and_pipes():
    piped = "takeaways | Agreed | [at: 2026-11-05T10:00:00Z]\n\nIndex | cost | 48 msgs | 2026-11-05T10:00:00Z\nThemes | Kickoff [source: x]"
    assert parse_rows(piped) == {
        "Takeaways": ["Agreed [at: 2026-11-05T10:00:00Z]"],
        "Index": ["cost|48 msgs [at: 2026-11-05T10:00:00Z]"],
        "Themes": ["Kickoff [source: x]"],
    }
    assert parse_rows('{"Index": ["a [source: b]"]}') == {"Index": ["a [source: b]"]}
    with pytest.raises(AcquaintError, match="expected"):
        parse_rows("just a line")
    with pytest.raises(AcquaintError, match="not valid JSON"):
        parse_rows("{nope")


def test_the_tool_reads_rows_from_a_file_only_with_an_at_sign(tmp_path, monkeypatch):
    monkeypatch.setenv("ACQUAINT_DATA_DIR", str(tmp_path / "data"))
    tools.new("person", "Ada Lovelace")
    tools.digest_new("ada-lovelace", REF)
    rows = tmp_path / "rows.txt"
    rows.write_text("Takeaways | Agreed | [at: 2026-11-05T10:00:00Z]\n")
    done = tools.digest_append("ada-lovelace", REF, heading="w1", rows=f"@{rows}", through="2026-11-05T10:00:00Z")
    assert done["ok"] and tools.digest("ada-lovelace", REF)["since"] == "2026-11-05T10:00:00Z"
    with pytest.raises(AcquaintError, match="cannot read"):
        tools.digest_append("ada-lovelace", REF, heading="w2", rows=f"@{tmp_path / 'missing.txt'}")
    with pytest.raises(AcquaintError, match="expected"):
        tools.digest_append("ada-lovelace", REF, heading="w2", rows=str(rows))
    assert tools.digest_append("ada-lovelace", REF, heading="w2", rows='{"Themes": ["x [at: 2026-11-06T00:00:00Z]"]}')["ok"]


def test_a_carriage_return_heading_cannot_smuggle_a_section(store, put):
    ada = _ada(store, put)
    assert "one-line heading" in _unchanged_after(ada, heading="w1\r## Notes", rows={"Index": ["x [source: y]"]})


def test_existing_unanchored_items_do_not_block_an_anchored_batch(store, put):
    ada = _ada(store, put)
    name = find_digest(ada, REF)["file"]
    ada[name] = ada[name].replace("## Open threads", "## Open threads\n\n- old bare item", 1)
    append_batch(ada, REF, heading="w1", rows=BATCH)
    assert "### w1" in ada[name]


def test_duplicate_section_keys_are_merged():
    assert parse_rows({"Index": ["a [source: x]"], "index:": ["b [source: y]"]}) == {"Index": ["a [source: x]", "b [source: y]"]}


def test_the_splice_ignores_section_lines_inside_fences_and_comments(store, put):
    ada = _ada(store, put)
    name = find_digest(ada, REF)["file"]
    ada[name] = ada[name].replace(
        "## Takeaways",
        "<!-- ## Takeaways -->\n```\n## Takeaways\n### w1\n## Index\n```\n## Takeaways",
        1,
    )
    append_batch(ada, REF, heading="w1", rows={"Takeaways": ["x [source: y]"]})
    text = ada[name]
    assert text.rindex("- x [source: y]") > text.rindex("## Takeaways")
    assert text.index("- x [source: y]") < text.index("## Themes")
    assert text.index("```\n## Takeaways\n### w1\n## Index\n```") < text.index("- x [source: y]")
    assert not [p for p in digest_problems(text) if p[0] == "error"]


def test_the_file_ends_with_one_newline_and_sections_stay_separated(store, put):
    ada = _ada(store, put)
    append_batch(ada, REF, heading="w1", rows=BATCH)
    text = ada[find_digest(ada, REF)["file"]]
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "\n\n## Themes" in text and "\n\n\n" not in text.split("---", 2)[2]


def test_the_splice_reads_sections_as_the_lint_does(store, put):
    ada = _ada(store, put)
    name = find_digest(ada, REF)["file"]
    ada[name] = ada[name].replace("## Themes", "  ## Themes:", 1).replace("## Index", "# Aside\n\n## Index", 1)
    append_batch(ada, REF, heading="w1", rows={"Themes": ["t [source: y]"], "Index": ["i [source: y]"]})
    text = ada[name]
    assert text.index("- t [source: y]") < text.index("# Aside") < text.index("- i [source: y]")
    assert text.index("- i [source: y]") < text.index("## Open threads")
    ada[name] = text.replace("### w1", "### w1 ###", 1)  # the same heading, as the lint reads it
    with pytest.raises(AcquaintError, match="already has"):
        append_batch(ada, REF, heading="w1", rows={"Themes": ["again [source: y]"]})


def test_a_section_ending_in_an_unclosed_fence_refuses_the_batch(store, put):
    ada = _ada(store, put)
    name = find_digest(ada, REF)["file"]
    ada[name] = ada[name].rstrip("\n") + "\n\n```\nnever closed\n"
    with pytest.raises(AcquaintError, match="unclosed code fence"):
        append_batch(ada, REF, heading="w1", rows={"Open threads": ["x [source: y]"]})
