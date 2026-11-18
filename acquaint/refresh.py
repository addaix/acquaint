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
    missing_ok = true                     # absent file = nothing yet (else it is unchecked)

Other keys: ``watermark_where`` (text the row's ``Where`` cell must contain, when
several rows share a corpus name), ``pattern`` (a glob, for the folder sourcers).
Paths take ``~`` and ``$VARS`` and must be absolute. Times without a zone are UTC, and
so is a date-only watermark's day.

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
    "empty_status",
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
    """A timezone-aware UTC datetime from an ISO string (``Z`` or an offset; naive is UTC) or an epoch number, or a string of one (seconds or milliseconds); ``None`` for anything else, out-of-range values included."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", value):
        value = float(value)
    try:
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value > _EPOCH_MS_ABOVE else value
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        if not isinstance(value, str):
            return None
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):  # NaN, 1e300, a 13th month
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def watermark_of(text: str) -> datetime | None:
    """The first ISO date or time in ``text``; a date alone counts as the end of that day, UTC (everything through it was read). ``None`` when there is none, or it is no real date."""
    found = _ISO_RE.search(text or "")
    if not found:
        return None
    stamp = found.group(0)
    if len(stamp) == 10:
        try:
            return datetime.combine(date.fromisoformat(stamp), time.max, tzinfo=timezone.utc)
        except ValueError:
            return None
    return parse_time(stamp)


# --------------------------------------------------------------------- sources.md


def _sections(markdown: str, word: str) -> Iterator[list[str]]:
    """The lines under each heading whose text contains ``word`` (case-insensitive), up to the next heading of the same or a higher level."""
    lines, fenced = [], False
    for line in markdown.splitlines():  # a heading inside a code fence is not a heading
        fenced ^= line.lstrip().startswith(("```", "~~~"))
        lines.append("" if fenced else line)
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
    """A table row's cells, split on unescaped pipes only."""
    inner = re.sub(r"^\s*\|", "", re.sub(r"(?<!\\)\|\s*$", "", line))
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]


def watermark_rows(sources_md: str) -> list[dict]:
    """The rows of the watermark table (the first table with a ``Watermark`` column under the first heading containing "watermark" that has one): ``{"corpus", "where", "watermark", "text"}``, ``watermark`` parsed (``None`` when the cell holds no date)."""
    for body in _sections(sources_md, "watermark"):
        table: list[str] = []
        for line in body:  # the first contiguous table only
            if line.lstrip().startswith("|"):
                table.append(line)
            elif table:
                break
        if len(table) < 2:
            continue
        header = [c.lower() for c in _cells(table[0])]
        if "watermark" not in header:
            continue
        col = {name: header.index(name) if name in header else None for name in ("corpus", "where", "watermark")}
        rows: list[dict] = []
        for line in table[1:]:
            cells = _cells(line)
            if all(set(c) <= set("-: ") for c in cells):
                continue  # the separator row

            def get(name: str) -> str:
                return cells[col[name]] if col[name] is not None and col[name] < len(cells) else ""

            rows.append(
                {
                    "corpus": get("corpus"),
                    "where": get("where"),
                    "text": get("watermark"),
                    "watermark": watermark_of(get("watermark")),
                }
            )
        return rows
    return []


def last_refresh(sources_md: str) -> date | None:
    """The latest date opening an item of the list under the "Refresh log" heading (a line whose date is no real date is skipped)."""

    def day(text: str) -> date | None:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    dates = [
        found_day
        for body in _sections(sources_md, "refresh log")
        for line in body
        if (found := re.match(r"^\s*[-*]\s+\**(\d{4}-\d{2}-\d{2})", line)) and (found_day := day(found.group(1)))
    ]
    return max(dates, default=None)


def _find_row(rows: list[dict], corpus: str, where: str | None) -> dict:
    """The one row whose Corpus cell is ``corpus`` (or, failing that, contains it) and whose Where cell contains ``where``; case-insensitive."""

    def at(r: dict) -> bool:
        return where is None or where.lower() in r["where"].lower()

    found = [r for r in rows if r["corpus"].lower() == corpus.lower() and at(r)] or [
        r for r in rows if corpus.lower() in r["corpus"].lower() and at(r)
    ]
    wanted = repr(corpus) + (f" where {where!r}" if where else "")
    if not found:
        raise AcquaintError(f"no row of the watermark table matches {wanted}")
    if len(found) > 1:
        raise AcquaintError(f"{len(found)} rows of the watermark table match {wanted}; add watermark_where")
    return found[0]


# ------------------------------------------------------------------------ sourcers


def _path(spec: Mapping[str, Any]) -> Path:
    """The corpus's ``path``: ``~`` and ``$VARS`` expanded, and absolute (a relative one would depend on where a scheduler starts)."""
    if not isinstance(spec.get("path"), str) or not spec["path"]:
        raise AcquaintError("the corpus has no path")
    path = Path(os.path.expandvars(spec["path"])).expanduser()
    if not path.is_absolute():
        raise AcquaintError(f"path {spec['path']!r} is relative; give it from ~ or /")
    return path


def _missing(path: Path, spec: Mapping[str, Any], *, folder: bool) -> bool:
    """True when the corpus is absent and ``missing_ok`` says that means "nothing yet"; raises when it is absent otherwise."""
    if path.is_dir() if folder else path.is_file():
        return False
    if spec.get("missing_ok") is True:
        return True
    raise AcquaintError(f"{path.name}: no such {'folder' if folder else 'file'} (set missing_ok = true if it is not created yet)")


def _field(spec: Mapping[str, Any]) -> str:
    if not spec.get("field"):
        raise AcquaintError("the corpus has no field (the timestamp of each item)")
    return str(spec["field"])


def _dig(document: Any, dotted: str | None) -> Any:
    for part in (dotted or "").split(".") if dotted else []:
        document = document.get(part) if isinstance(document, Mapping) else None
    return document


def _newer(moments: Iterable[datetime | None], since: datetime | None, *, field: str | None = None) -> int:
    """How many ``moments`` are later than ``since``; raises when there were records and none had a readable time (a misspelt ``field`` must not read as "nothing new")."""
    seen = dated = newer = 0
    for moment in moments:
        seen += 1
        if moment is not None:
            dated += 1
            newer += since is None or moment > since
    if seen and not dated:
        raise AcquaintError(f"{seen} record(s), none with a readable time in {field!r}")
    return newer


def jsonl_sourcer(spec: Mapping[str, Any]) -> Counter:
    """A JSON Lines file (``path``), one record per line, timestamp in ``field``. An unparseable line is skipped; a missing file is an error unless ``missing_ok = true``."""
    path, field = _path(spec), _field(spec)

    def count_since(watermark: datetime | None) -> int:
        if _missing(path, spec, folder=False):
            return 0

        def moments() -> Iterator[datetime | None]:
            with path.open(encoding="utf-8", errors="replace") as lines:
                for line in lines:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    yield parse_time(_dig(record, field)) if isinstance(record, Mapping) else None

        return _newer(moments(), watermark, field=field)

    return count_since


def json_folder_sourcer(spec: Mapping[str, Any]) -> Counter:
    """A folder (``path``) of JSON files matching ``pattern`` (default ``*.json``); each file is one record, or holds a list of them under ``items`` (a dotted key); timestamp in ``field``."""
    path, field = _path(spec), _field(spec)
    pattern, items = str(spec.get("pattern", "*.json")), spec.get("items")

    def count_since(watermark: datetime | None) -> int:
        if _missing(path, spec, folder=True):
            return 0

        def moments() -> Iterator[datetime | None]:
            for file in sorted(path.glob(pattern)):
                try:
                    document = json.loads(file.read_text(encoding="utf-8", errors="replace"))
                except (OSError, ValueError):
                    continue
                found = _dig(document, items) if items else document
                records = found if isinstance(found, list) else [found]
                yield from (parse_time(_dig(r, field)) for r in records if isinstance(r, Mapping))

        return _newer(moments(), watermark, field=field)

    return count_since


def mtime_sourcer(spec: Mapping[str, Any]) -> Counter:
    """Files under ``path`` matching ``pattern`` (a glob, default ``*``; ``*/memory/*.md`` reaches into subfolders), each dated by its modification time."""
    path, pattern = _path(spec), str(spec.get("pattern", "*"))

    def count_since(watermark: datetime | None) -> int:
        if _missing(path, spec, folder=True):
            return 0
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


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def empty_status(reason: str, *, corpus: str = "(record)") -> dict:
    """The shape :func:`refresh_status` returns, for a record that could not be checked at all (same keys, so a caller has one code path)."""
    return {
        "outcome": "unchecked",
        "due": False,
        "reasons": [],
        "age": {"last_refresh": None, "days": None, "max_days": None, "triggers": False},
        "corpora": [],
        "unchecked": [{"corpus": corpus, "reason": reason}],
        "configured": False,
    }


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

    Returns ``{"outcome", "due", "reasons", "age", "corpora", "unchecked", "configured"}``.
    ``outcome`` is ``due`` (some trigger fired), ``not_due`` (none fired and everything
    was checked) or ``unchecked`` (none fired, but something could not be checked, so
    "not due" cannot be claimed). ``age`` is the last refresh against ``max_age_days``;
    each corpus row has its watermark (and where it came from), the count newer, its
    threshold and whether it triggers. A corpus that could not be counted goes to
    ``unchecked`` with the reason, never silently to zero, and never takes the others
    down with it.
    """
    config = _config(config_toml)
    today = today or datetime.now(timezone.utc).date()
    unchecked: list[dict] = []
    max_age = config.get("max_age_days", DFLT_MAX_AGE_DAYS)
    if not _is_int(max_age) or max_age < 1:
        unchecked.append({"corpus": "(age)", "reason": f"max_age_days must be a whole number of days, not {max_age!r}"})
        max_age = DFLT_MAX_AGE_DAYS
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
    if age_days is not None and age_days < 0:
        unchecked.append({"corpus": "(age)", "reason": f"the last refresh-log date {last.isoformat()} is in the future"})
    since_last = datetime.combine(last, time.max, tzinfo=timezone.utc) if last else None
    rows = watermark_rows(sources_md)
    corpora = []
    corpus_specs = config.get("corpus", {})
    if not isinstance(corpus_specs, Mapping):
        corpus_specs = {}
        unchecked.append({"corpus": "(config)", "reason": f"{CONFIG_FILE}: [corpus.<name>] tables expected"})
    for name, spec in corpus_specs.items():
        try:
            if not isinstance(spec, Mapping):
                raise AcquaintError(f"[corpus.{name}] must be a table")
            threshold = spec.get("threshold", DFLT_THRESHOLD)
            if not _is_int(threshold) or threshold < 0:
                raise AcquaintError(f"threshold must be a whole number (0 reports only), not {threshold!r}")
            kind = spec.get("kind")
            if not isinstance(kind, str) or kind not in sourcers:
                raise AcquaintError(f"unknown kind {kind!r}; known: {', '.join(sorted(sourcers))}")
            if "watermark_row" in spec:
                where = spec.get("watermark_where")
                if not isinstance(spec["watermark_row"], str) or not (where is None or isinstance(where, str)):
                    raise AcquaintError("watermark_row and watermark_where must be text")
                row = _find_row(rows, spec["watermark_row"], where)
                if row["watermark"] is None:
                    raise AcquaintError(f"the watermark cell {row['text']!r} holds no date")
                watermark, stamp = row["watermark"], _ISO_RE.search(row["text"]).group(0)
                origin = f"{SOURCES_FILE}: {row['corpus']}" + (f" ({row['where']})" if row["where"] else "")
            else:
                watermark, stamp, origin = since_last, age["last_refresh"], "last refresh"
            newer = sourcers[kind](spec)(watermark)
            if not _is_int(newer) or newer < 0:
                raise AcquaintError(f"the {kind} sourcer returned {newer!r}, not a count")
        except Exception as error:  # noqa: BLE001 - one corpus that breaks must not hide the others
            reason = str(error) if isinstance(error, AcquaintError) else f"{type(error).__name__}: {error}"
            unchecked.append({"corpus": name, "reason": reason[:300]})
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
        "outcome": "due" if reasons else "unchecked" if unchecked else "not_due",
        "due": bool(reasons),
        "reasons": reasons,
        "age": age,
        "corpora": corpora,
        "unchecked": unchecked,
        "configured": bool(config_toml.strip()),
    }
