"""Is a record built from someone's own writing due for a refresh? Per-corpus watermarks against what is newer.

A record mined from a corpus (an operator's positions from what they typed to their
agents, say) goes stale as the person keeps writing. Its ``sources.md`` holds one
**watermark** per corpus (the newest item mined), and its **refresh log**. This module
counts, per corpus, how many items are newer than the watermark, and says whether a
refresh is due: the last refresh is older than ``max_age_days``, or a corpus count has
reached its ``threshold``. It only reads; the refresh itself stays a reviewed pass.

Which corpora a record uses is per-record configuration, ``refresh.toml`` beside
``sources.md`` (it lives in the private store, so paths there may be machine paths)::

    max_age_days = 7                      # default 7

    [corpus.questions]
    kind = "json-folder"                  # a sourcer name, see SOURCERS
    path = "~/.local/share/example/questions"
    items = "exchanges"                   # the list of records inside each JSON file
    field = "asked_at"                    # each record's timestamp
    watermark_row = "question store"      # the row of the watermark table in sources.md
    threshold = 100                       # default 100; 0 reports the count, never triggers

    [corpus.overrides]
    kind = "jsonl"
    path = "~/.local/share/example/overrides.jsonl"
    field = "at"
    threshold = 10                        # no watermark_row: counted since the last refresh

Why a separate TOML file rather than a block inside ``sources.md``: ``sources.md`` is
rewritten by every refresh (its candidate replaces the installed one), so a machine block
in it would be at the mercy of that rewrite; the corpus list changes rarely and is edited
by hand. The watermarks themselves stay in ``sources.md``, the one place a refresh moves
them, read from its watermark table (a Markdown table under a heading containing
"Watermark", with columns ``Corpus``, ``Where`` and ``Watermark``; the first ISO date or
time in the ``Watermark`` cell is the watermark). The last refresh is the latest date
that opens an item of the list under the "Refresh log" heading.

A watermark that is a date only means "everything through that day was read"; a time is
taken as given. A corpus is a **sourcer**: a factory taking the corpus's TOML table and
returning ``count_since(watermark) -> int``. :data:`SOURCERS` holds the generic ones (a
JSONL file, a folder of JSON files, a folder of files by modification time); a host adds
its own through the ``sourcers`` argument of :func:`refresh_status`.

>>> from datetime import datetime, timezone
>>> parse_time("2026-01-02T03:04Z"), parse_time(1767225600000)
(datetime.datetime(2026, 1, 2, 3, 4, tzinfo=datetime.timezone.utc), datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.timezone.utc))
>>> watermark_of("2026-01-02 (nothing later)")
datetime.datetime(2026, 1, 2, 23, 59, 59, 999999, tzinfo=datetime.timezone.utc)
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from acquaint.store import AcquaintError

__all__ = [
    "CONFIG_FILE",
    "DFLT_MAX_AGE_DAYS",
    "DFLT_THRESHOLD",
    "SOURCERS",
    "SOURCES_FILE",
    "jsonl_sourcer",
    "json_folder_sourcer",
    "last_refresh",
    "mtime_sourcer",
    "parse_time",
    "refresh_status",
    "watermark_of",
    "watermark_rows",
]

CONFIG_FILE = "refresh.toml"
SOURCES_FILE = "sources.md"
DFLT_MAX_AGE_DAYS = 7
DFLT_THRESHOLD = 100

_ISO_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)
#: An epoch number above this is in milliseconds (1e11 seconds is the year 5138).
_EPOCH_MS_ABOVE = 1e11

Counter = Callable[[datetime | None], int]
Sourcer = Callable[[Mapping[str, Any]], Counter]


# ------------------------------------------------------------------------- times


def parse_time(value: Any) -> datetime | None:
    """A timezone-aware UTC datetime from an ISO string (``Z`` or an offset; naive is UTC) or an epoch number (seconds or milliseconds); ``None`` when it is neither."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > _EPOCH_MS_ABOVE else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def watermark_of(text: str) -> datetime | None:
    """The first ISO date or time in ``text``; a date alone counts as the end of that day (everything through it was read)."""
    found = _ISO_RE.search(text or "")
    if not found:
        return None
    stamp = found.group(0)
    if len(stamp) == 10:
        day = date.fromisoformat(stamp)
        return datetime.combine(day, time.max, tzinfo=timezone.utc)
    return parse_time(stamp)


# --------------------------------------------------------------------- sources.md


def _sections(markdown: str, word: str) -> Iterator[list[str]]:
    """The lines under each heading whose text contains ``word`` (case-insensitive), up to the next heading of the same or a higher level."""
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        heading = re.match(r"^(#+)\s+(.*)$", line)
        if not heading or word.lower() not in heading.group(2).lower():
            continue
        level, body = len(heading.group(1)), []
        for following in lines[i + 1 :]:
            nxt = re.match(r"^(#+)\s", following)
            if nxt and len(nxt.group(1)) <= level:
                break
            body.append(following)
        yield body


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def watermark_rows(sources_md: str) -> list[dict]:
    """The rows of the watermark table: ``{"corpus", "where", "watermark", "text"}``, with ``watermark`` parsed (``None`` when the cell holds no date)."""
    rows: list[dict] = []
    for body in _sections(sources_md, "watermark"):
        table = [line for line in body if line.lstrip().startswith("|")]
        if len(table) < 2:
            continue
        header = [c.lower() for c in _cells(table[0])]
        if "watermark" not in header:
            continue
        col = {name: header.index(name) if name in header else None for name in ("corpus", "where", "watermark")}
        for line in table[1:]:
            cells = _cells(line)
            if all(set(c) <= set("-: ") for c in cells):
                continue  # the separator row
            get = lambda name: cells[col[name]] if col[name] is not None and col[name] < len(cells) else ""  # noqa: E731
            rows.append(
                {
                    "corpus": get("corpus"),
                    "where": get("where"),
                    "text": get("watermark"),
                    "watermark": watermark_of(get("watermark")),
                }
            )
    return rows


def last_refresh(sources_md: str) -> date | None:
    """The latest date opening an item of the list under the "Refresh log" heading."""
    dates = [
        date.fromisoformat(found.group(1))
        for body in _sections(sources_md, "refresh log")
        for line in body
        if (found := re.match(r"^\s*[-*]\s+\**(\d{4}-\d{2}-\d{2})", line))
    ]
    return max(dates, default=None)


def _find_row(rows: list[dict], corpus: str, where: str | None) -> dict:
    """The one row whose Corpus cell contains ``corpus`` and Where cell contains ``where`` (case-insensitive)."""
    found = [
        r
        for r in rows
        if corpus.lower() in r["corpus"].lower() and (where is None or where.lower() in r["where"].lower())
    ]
    wanted = repr(corpus) + (f" where {where!r}" if where else "")
    if not found:
        raise AcquaintError(f"no row of the watermark table matches {wanted}")
    if len(found) > 1:
        raise AcquaintError(f"{len(found)} rows of the watermark table match {wanted}; add watermark_where")
    return found[0]


# ------------------------------------------------------------------------ sourcers


def _path(spec: Mapping[str, Any]) -> Path:
    if not spec.get("path"):
        raise AcquaintError("the corpus has no path")
    return Path(os.path.expandvars(str(spec["path"]))).expanduser()


def _field(spec: Mapping[str, Any]) -> str:
    if not spec.get("field"):
        raise AcquaintError("the corpus has no field (the timestamp of each item)")
    return str(spec["field"])


def _dig(document: Any, dotted: str | None) -> Any:
    for part in (dotted or "").split(".") if dotted else []:
        document = document.get(part) if isinstance(document, Mapping) else None
    return document


def _newer(moments: Iterable[datetime | None], since: datetime | None) -> int:
    return sum(1 for m in moments if m is not None and (since is None or m > since))


def jsonl_sourcer(spec: Mapping[str, Any]) -> Counter:
    """A JSON Lines file (``path``), one record per line, timestamp in ``field``. A missing file counts nothing new; an unparseable line is skipped."""
    path, field = _path(spec), _field(spec)

    def count_since(watermark: datetime | None) -> int:
        if not path.exists():
            return 0

        def moments() -> Iterator[datetime | None]:
            with path.open(encoding="utf-8", errors="replace") as lines:
                for line in lines:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    yield parse_time(_dig(record, field)) if isinstance(record, Mapping) else None

        return _newer(moments(), watermark)

    return count_since


def json_folder_sourcer(spec: Mapping[str, Any]) -> Counter:
    """A folder (``path``) of JSON files matching ``pattern`` (default ``*.json``); each file is one record, or holds a list of them under ``items`` (a dotted key); timestamp in ``field``."""
    path, field = _path(spec), _field(spec)
    pattern, items = str(spec.get("pattern", "*.json")), spec.get("items")

    def count_since(watermark: datetime | None) -> int:
        if not path.is_dir():
            raise AcquaintError(f"{path.name}: not a folder")

        def moments() -> Iterator[datetime | None]:
            for file in sorted(path.glob(pattern)):
                try:
                    document = json.loads(file.read_text(encoding="utf-8", errors="replace"))
                except (OSError, ValueError):
                    continue
                found = _dig(document, items) if items else document
                records = found if isinstance(found, list) else [found]
                yield from (parse_time(_dig(r, field)) for r in records if isinstance(r, Mapping))

        return _newer(moments(), watermark)

    return count_since


def mtime_sourcer(spec: Mapping[str, Any]) -> Counter:
    """Files under ``path`` matching ``pattern`` (a glob, default ``*``; ``*/memory/*.md`` reaches into subfolders), each dated by its modification time."""
    path, pattern = _path(spec), str(spec.get("pattern", "*"))

    def count_since(watermark: datetime | None) -> int:
        if not path.is_dir():
            raise AcquaintError(f"{path.name}: not a folder")
        return _newer(
            (parse_time(f.stat().st_mtime) for f in path.glob(pattern) if f.is_file()),
            watermark,
        )

    return count_since


#: The generic sourcers, by the ``kind`` a corpus names in ``refresh.toml``.
SOURCERS: dict[str, Sourcer] = {
    "jsonl": jsonl_sourcer,
    "json-folder": json_folder_sourcer,
    "mtime": mtime_sourcer,
}


# ------------------------------------------------------------------------- status


def _config(text: str) -> dict:
    try:
        return tomllib.loads(text) if text.strip() else {}
    except tomllib.TOMLDecodeError as error:
        raise AcquaintError(f"{CONFIG_FILE} does not parse: {error}") from error


def refresh_status(
    sources_md: str,
    config_toml: str = "",
    *,
    today: date | None = None,
    sourcers: Mapping[str, Sourcer] = SOURCERS,
) -> dict:
    """Whether a refresh is due, from a record's ``sources.md`` and ``refresh.toml`` texts.

    Returns ``{"due", "reasons", "age", "corpora", "unchecked"}``. ``age`` is the last
    refresh against ``max_age_days``; each corpus row has its watermark (and where it
    came from), the count newer, its threshold and whether it triggers. A corpus that
    could not be counted goes to ``unchecked`` with the reason, never silently to zero.
    """
    config = _config(config_toml)
    today = today or datetime.now(timezone.utc).date()
    max_age = int(config.get("max_age_days", DFLT_MAX_AGE_DAYS))
    last = last_refresh(sources_md)
    age_days = (today - last).days if last else None
    age = {
        "last_refresh": last.isoformat() if last else None,
        "days": age_days,
        "max_days": max_age,
        "triggers": age_days is None or age_days >= max_age,
    }
    reasons = (
        [f"no dated refresh log in {SOURCES_FILE}"]
        if last is None
        else [f"last refresh {last.isoformat()} is {age_days} day(s) old (limit {max_age})"]
        if age["triggers"]
        else []
    )
    since_last = datetime.combine(last, time.max, tzinfo=timezone.utc) if last else None
    rows = watermark_rows(sources_md)
    corpora, unchecked = [], []
    corpus_specs = config.get("corpus") or {}
    if not isinstance(corpus_specs, Mapping):
        raise AcquaintError(f"{CONFIG_FILE}: [corpus.<name>] tables expected")
    for name, spec in corpus_specs.items():
        threshold = int(spec.get("threshold", DFLT_THRESHOLD))
        try:
            kind = spec.get("kind")
            if kind not in sourcers:
                raise AcquaintError(f"unknown kind {kind!r}; known: {', '.join(sorted(sourcers))}")
            if spec.get("watermark_row"):
                row = _find_row(rows, str(spec["watermark_row"]), spec.get("watermark_where"))
                if row["watermark"] is None:
                    raise AcquaintError(f"the watermark cell {row['text']!r} holds no date")
                watermark, stamp = row["watermark"], _ISO_RE.search(row["text"]).group(0)
                origin = f"{SOURCES_FILE}: {row['corpus']}" + (f" ({row['where']})" if row["where"] else "")
            else:
                watermark, stamp, origin = since_last, age["last_refresh"], "last refresh"
            newer = sourcers[kind](spec)(watermark)
        except (AcquaintError, OSError) as error:
            unchecked.append({"corpus": name, "reason": str(error)})
            continue
        triggers = threshold > 0 and newer >= threshold
        corpora.append(
            {
                "corpus": name,
                "kind": kind,
                "watermark": watermark.isoformat() if watermark else None,
                "watermark_stamp": stamp,
                "watermark_from": origin,
                "newer": newer,
                "threshold": threshold,
                "triggers": triggers,
            }
        )
        if triggers:
            reasons.append(f"{name}: {newer} new item(s) past the watermark (threshold {threshold})")
    return {
        "due": bool(reasons),
        "reasons": reasons,
        "age": age,
        "corpora": corpora,
        "unchecked": unchecked,
        "configured": bool(config_toml.strip()),
    }
