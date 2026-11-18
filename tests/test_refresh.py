"""refresh-due: each sourcer counts what is newer than a watermark, sources.md supplies the watermarks, and the CLI exits 0 only when a refresh is due."""

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone

import pytest

from acquaint import tools
from acquaint.refresh import (
    json_folder_sourcer,
    jsonl_sourcer,
    last_refresh,
    mtime_sourcer,
    refresh_status,
    watermark_rows,
)

WATERMARK = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)

SOURCES = """# Sources

## Watermarks at a glance

| Corpus | Where | Watermark | Next refresh reads |
|---|---|---|---|
| question store | laptop, `questions/` | 2026-01-10T12:00Z | later exchanges |
| prompt history | laptop | 2026-01-10 (nothing later) | later entries |
| prompt history | server | 2026-01-01 | later entries |
| old essays | archive | 24 of 513 read | the rest |

## Refresh log

- 2026-01-03, v1: first pass.
- 2026-01-10, v2: second pass.
"""


def _jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------- sourcers


def test_jsonl_counts_records_newer_than_the_watermark(tmp_path):
    path = _jsonl(
        tmp_path / "log.jsonl",
        [{"at": "2026-01-09T00:00:00Z"}, {"at": "2026-01-11T00:00:00Z"}, {"at": "2026-01-12T08:00:00+02:00"}, {"other": 1}],
    )
    (tmp_path / "log.jsonl").open("a").write("not json\n")
    count = jsonl_sourcer({"path": str(path), "field": "at"})
    assert count(WATERMARK) == 2
    assert count(None) == 3  # no watermark: every dated record


def test_jsonl_reads_epoch_milliseconds_and_a_missing_file_is_nothing_new(tmp_path):
    ms = int(datetime(2026, 1, 11, tzinfo=timezone.utc).timestamp() * 1000)
    path = _jsonl(tmp_path / "history.jsonl", [{"timestamp": ms}, {"timestamp": ms - 10**10}])
    assert jsonl_sourcer({"path": str(path), "field": "timestamp"})(WATERMARK) == 1
    assert jsonl_sourcer({"path": str(tmp_path / "absent.jsonl"), "field": "at"})(WATERMARK) == 0


def test_json_folder_counts_items_inside_each_file(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"exchanges": [{"asked_at": "2026-01-11T00:00:00Z"}, {"asked_at": "2026-01-01T00:00:00Z"}]}))
    (tmp_path / "b.json").write_text(json.dumps({"exchanges": [{"asked_at": "2026-01-12T00:00:00Z"}]}))
    (tmp_path / "broken.json").write_text("{")
    (tmp_path / "notes.txt").write_text("ignored")
    count = json_folder_sourcer({"path": str(tmp_path), "items": "exchanges", "field": "asked_at"})
    assert count(WATERMARK) == 2


def test_json_folder_takes_each_file_as_one_record_without_items(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"meta": {"at": "2026-01-11T00:00:00Z"}}))
    (tmp_path / "b.json").write_text(json.dumps({"meta": {"at": "2026-01-01T00:00:00Z"}}))
    assert json_folder_sourcer({"path": str(tmp_path), "field": "meta.at"})(WATERMARK) == 1


def test_mtime_counts_files_modified_after_the_watermark(tmp_path):
    for project, stamp in (("p1", WATERMARK.timestamp() + 60), ("p2", WATERMARK.timestamp() - 60)):
        note = tmp_path / project / "memory" / "note.md"
        note.parent.mkdir(parents=True)
        note.write_text("x")
        os.utime(note, (stamp, stamp))
    (tmp_path / "p1" / "other.md").write_text("not a memory note")
    assert mtime_sourcer({"path": str(tmp_path), "pattern": "*/memory/*.md"})(WATERMARK) == 1


# ------------------------------------------------------------------ sources.md


def test_watermark_rows_and_the_last_refresh_come_from_sources_md():
    rows = watermark_rows(SOURCES)
    assert [r["corpus"] for r in rows] == ["question store", "prompt history", "prompt history", "old essays"]
    assert rows[0]["watermark"] == WATERMARK
    assert rows[1]["watermark"] == datetime(2026, 1, 10, 23, 59, 59, 999999, tzinfo=timezone.utc)  # a date is the whole day
    assert rows[3]["watermark"] is None
    assert last_refresh(SOURCES) == date(2026, 1, 10)


def _config(tmp_path, *, threshold=2):
    folder = tmp_path / "questions"
    folder.mkdir(exist_ok=True)
    (folder / "s.json").write_text(json.dumps({"exchanges": [{"asked_at": f"2026-01-1{d}T00:00:00Z"} for d in (1, 2, 3)]}))
    _jsonl(tmp_path / "overrides.jsonl", [{"at": "2026-01-11T00:00:00Z"}])
    return f"""
max_age_days = 7

[corpus.questions]
kind = "json-folder"
path = "{folder.as_posix()}"
items = "exchanges"
field = "asked_at"
watermark_row = "question store"
threshold = {threshold}

[corpus.overrides]
kind = "jsonl"
path = "{(tmp_path / 'overrides.jsonl').as_posix()}"
field = "at"
threshold = 10

[corpus.server]
kind = "jsonl"
path = "{(tmp_path / 'absent.jsonl').as_posix()}"
field = "at"
watermark_row = "prompt history"
"""


def test_a_corpus_past_its_threshold_makes_a_refresh_due(tmp_path):
    status = refresh_status(SOURCES, _config(tmp_path), today=date(2026, 1, 11))
    assert status["due"] and status["age"]["triggers"] is False
    by_name = {c["corpus"]: c for c in status["corpora"]}
    assert by_name["questions"]["newer"] == 3 and by_name["questions"]["triggers"]
    assert by_name["overrides"]["watermark_from"] == "last refresh" and by_name["overrides"]["newer"] == 1
    # an ambiguous row is reported, never guessed and never counted as zero
    assert [u["corpus"] for u in status["unchecked"]] == ["server"] and "2 rows" in status["unchecked"][0]["reason"]


def test_age_alone_makes_a_refresh_due_and_threshold_zero_never_triggers(tmp_path):
    status = refresh_status(SOURCES, _config(tmp_path, threshold=0), today=date(2026, 1, 11))
    assert not status["due"]
    status = refresh_status(SOURCES, _config(tmp_path, threshold=0).replace("max_age_days = 7", "max_age_days = 1"), today=date(2026, 1, 11))
    assert status["due"] and status["age"] == {"last_refresh": "2026-01-10", "days": 1, "max_days": 1, "triggers": True}


def test_without_a_config_only_the_age_is_checked():
    assert refresh_status(SOURCES, "", today=date(2026, 1, 12))["due"] is False
    assert refresh_status(SOURCES, "", today=date(2026, 1, 17))["due"] is True
    assert refresh_status("# no log here", "", today=date(2026, 1, 12))["reasons"] == ["no dated refresh log in sources.md"]


def test_an_unknown_kind_is_unchecked_and_a_host_can_add_a_sourcer():
    config = '[corpus.custom]\nkind = "mine"\nthreshold = 1\n'
    assert refresh_status(SOURCES, config, today=date(2026, 1, 11))["unchecked"][0]["corpus"] == "custom"
    status = refresh_status(SOURCES, config, today=date(2026, 1, 11), sourcers={"mine": lambda spec: lambda since: 5})
    assert status["due"] and status["corpora"][0]["newer"] == 5


# ------------------------------------------------------------------------ tool + CLI


@pytest.fixture
def record(tmp_path):
    data = tmp_path / "data"
    tools.new("person", "Ada Lovelace", data_dir=str(data))
    folder = data / "people" / "ada-lovelace"
    (folder / "sources.md").write_text(SOURCES, encoding="utf-8")
    (folder / "refresh.toml").write_text(_config(tmp_path), encoding="utf-8")
    return data


def _cli(data, *args):
    env = {**os.environ, "ACQUAINT_DATA_DIR": str(data)}
    return subprocess.run([sys.executable, "-m", "acquaint", "refresh-due", *args], capture_output=True, text=True, env=env)


def test_the_tool_reports_each_corpus(record):
    result = tools.refresh_due("ada-lovelace", today="2026-01-11", data_dir=str(record))
    assert result["ok"] and result["outcome"] == "due" and "questions" in result["text"]
    json.dumps(result)


def test_the_cli_exits_0_when_due_1_when_not_and_4_when_it_cannot_tell(record):
    due = _cli(record, "ada-lovelace", "--today", "2026-01-11")
    assert due.returncode == 0 and "refresh due" in due.stdout and "questions" in due.stdout
    config = record / "people" / "ada-lovelace" / "refresh.toml"
    config.write_text(config.read_text().replace("threshold = 2", "threshold = 50"))
    assert _cli(record, "ada-lovelace", "--today", "2026-01-11").returncode == 1
    as_json = _cli(record, "ada-lovelace", "--today", "2026-01-11", "--json")
    assert as_json.returncode == 1 and json.loads(as_json.stdout)["outcome"] == "not_due"
    assert _cli(record, "nobody").returncode == 4
    (record / "people" / "ada-lovelace" / "sources.md").unlink()
    missing = _cli(record, "ada-lovelace")
    assert missing.returncode == 4 and "no sources.md" in missing.stderr
