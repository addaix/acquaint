# acquaint.refresh

Is a record built from someone’s own writing due for a refresh? Per-corpus watermarks against what is newer.

A record mined from a corpus (an operator’s positions from what they typed to their
agents, say) goes stale as the person keeps writing. Its `sources.md` holds one
**watermark** per corpus (the newest item mined), and its **refresh log**. This module
counts, per corpus, how many items are newer than the watermark, and says whether a
refresh is due: the last refresh is older than `max_age_days`, or a corpus count has
reached its `threshold`. It only reads; the refresh itself stays a reviewed pass.

Which corpora a record uses is per-record configuration, `refresh.toml` beside
`sources.md` (it lives in the private store, so paths there may be machine paths):

```default
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
```

Other keys: `watermark_where` (text the row’s `Where` cell must contain, when
several rows share a corpus name), `pattern` (a glob, for the folder sourcers).
Paths take `~` and `$VARS` and must be absolute. Times without a zone are UTC, and
so is a date-only watermark’s day.

Why a separate TOML file rather than a block inside `sources.md`: `sources.md` is
rewritten by every refresh (its candidate replaces the installed one), so a machine block
in it would be at the mercy of that rewrite; the corpus list changes rarely and is edited
by hand. The watermarks themselves stay in `sources.md`, the one place a refresh moves
them, read from its watermark table (a Markdown table under a heading containing
“Watermark”, with columns `Corpus`, `Where` and `Watermark`; the first ISO date or
time in the `Watermark` cell is the watermark). The last refresh is the latest date
that opens an item of the list under the “Refresh log” heading.

A watermark that is a date only means “everything through that day was read”; a time is
taken as given. A corpus is a **sourcer**: a factory taking the corpus’s TOML table and
returning `count_since(watermark) -> int`. [`SOURCERS`](#acquaint.refresh.SOURCERS) holds the generic ones (a
JSONL file, a folder of JSON files, a folder of files by modification time); a host adds
its own through the `sourcers` argument of [`refresh_status()`](#acquaint.refresh.refresh_status).

```pycon
>>> from datetime import datetime, timezone
>>> parse_time("2026-01-02T03:04Z"), parse_time(1767225600000)
(datetime.datetime(2026, 1, 2, 3, 4, tzinfo=datetime.timezone.utc), datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.timezone.utc))
>>> watermark_of("2026-01-02 (nothing later)")
datetime.datetime(2026, 1, 2, 23, 59, 59, 999999, tzinfo=datetime.timezone.utc)
```

### Module Attributes

| [`SOURCERS`](#acquaint.refresh.SOURCERS)   | The generic sourcers, by the `kind` a corpus names in `refresh.toml`.   |
|-------------------------------------------------------------|-------------------------------------------------------------------------|

### Functions

| [`empty_status`](#acquaint.refresh.empty_status)(reason, \*[, corpus])             | The shape [`refresh_status()`](#acquaint.refresh.refresh_status) returns, for a record that could not be checked at all (same keys, so a caller has one code path).                                                                |
|-------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`jsonl_sourcer`](#acquaint.refresh.jsonl_sourcer)(spec)                            | A JSON Lines file (`path`), one record per line, timestamp in `field`.                                                                                                                                                                        |
| [`json_folder_sourcer`](#acquaint.refresh.json_folder_sourcer)(spec)                      | A folder (`path`) of JSON files matching `pattern` (default `*.json`); each file is one record, or holds a list of them under `items` (a dotted key); timestamp in `field`.                                                                   |
| [`last_refresh`](#acquaint.refresh.last_refresh)(sources_md)                       | The latest date opening an item of the list under the "Refresh log" heading (a line whose date is no real date is skipped).                                                                                                                   |
| [`mtime_sourcer`](#acquaint.refresh.mtime_sourcer)(spec)                            | Files under `path` matching `pattern` (a glob, default `*`; `*/memory/*.md` reaches into subfolders), each dated by its modification time.                                                                                                    |
| [`parse_time`](#acquaint.refresh.parse_time)(value)                              | A timezone-aware UTC datetime from an ISO string (`Z` or an offset; naive is UTC) or an epoch number, or a string of one (seconds or milliseconds); `None` for anything else, out-of-range values included.                                   |
| [`refresh_status`](#acquaint.refresh.refresh_status)(sources_md[, config_toml, ...]) | Whether a refresh is due, from a record's `sources.md` and `refresh.toml` texts.                                                                                                                                                              |
| [`watermark_of`](#acquaint.refresh.watermark_of)(text)                             | The first ISO date or time in `text`; a date alone counts as the end of that day, UTC (everything through it was read).                                                                                                                       |
| [`watermark_rows`](#acquaint.refresh.watermark_rows)(sources_md)                     | The rows of the watermark table (the first table with a `Watermark` column under the first heading containing "watermark" that has one): `{"corpus", "where", "watermark", "text"}`, `watermark` parsed (`None` when the cell holds no date). |

### acquaint.refresh.SOURCERS *: [dict](https://docs.python.org/3/builtins/stdtypes.html#dict)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Callable](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[Mapping](https://docs.python.org/3/library/collections.abc.html#collections.abc.Mapping)[[str](https://docs.python.org/3/builtins/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any)]], [Callable](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[datetime](https://docs.python.org/3/library/datetime.html#datetime.datetime) | [None](https://docs.python.org/3/builtins/constants.html#None)], [int](https://docs.python.org/3/builtins/functions.html#int)]]]* *= {'json-folder': <function json_folder_sourcer>, 'jsonl': <function jsonl_sourcer>, 'mtime': <function mtime_sourcer>}*

The generic sourcers, by the `kind` a corpus names in `refresh.toml`.

### acquaint.refresh.empty_status(reason, , corpus='(record)')

The shape [`refresh_status()`](#acquaint.refresh.refresh_status) returns, for a record that could not be checked at all (same keys, so a caller has one code path).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### acquaint.refresh.json_folder_sourcer(spec)

A folder (`path`) of JSON files matching `pattern` (default `*.json`); each file is one record, or holds a list of them under `items` (a dotted key); timestamp in `field`.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`datetime`](https://docs.python.org/3/library/datetime.html#datetime.datetime) | [`None`](https://docs.python.org/3/builtins/constants.html#None)], [`int`](https://docs.python.org/3/builtins/functions.html#int)]

### acquaint.refresh.jsonl_sourcer(spec)

A JSON Lines file (`path`), one record per line, timestamp in `field`. An unparseable line is skipped; a missing file is an error unless `missing_ok = true`.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`datetime`](https://docs.python.org/3/library/datetime.html#datetime.datetime) | [`None`](https://docs.python.org/3/builtins/constants.html#None)], [`int`](https://docs.python.org/3/builtins/functions.html#int)]

### acquaint.refresh.last_refresh(sources_md)

The latest date opening an item of the list under the “Refresh log” heading (a line whose date is no real date is skipped).

* **Return type:**
  [`date`](https://docs.python.org/3/library/datetime.html#datetime.date) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### acquaint.refresh.mtime_sourcer(spec)

Files under `path` matching `pattern` (a glob, default `*`; `*/memory/*.md` reaches into subfolders), each dated by its modification time.

* **Return type:**
  [`Callable`](https://docs.python.org/3/library/collections.abc.html#collections.abc.Callable)[[[`datetime`](https://docs.python.org/3/library/datetime.html#datetime.datetime) | [`None`](https://docs.python.org/3/builtins/constants.html#None)], [`int`](https://docs.python.org/3/builtins/functions.html#int)]

### acquaint.refresh.parse_time(value)

A timezone-aware UTC datetime from an ISO string (`Z` or an offset; naive is UTC) or an epoch number, or a string of one (seconds or milliseconds); `None` for anything else, out-of-range values included.

* **Return type:**
  [`datetime`](https://docs.python.org/3/library/datetime.html#datetime.datetime) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### acquaint.refresh.refresh_status(sources_md, config_toml='', \*, today=None, sourcers={'json-folder': <function json_folder_sourcer>, 'jsonl': <function jsonl_sourcer>, 'mtime': <function mtime_sourcer>})

Whether a refresh is due, from a record’s `sources.md` and `refresh.toml` texts.

Returns `{"outcome", "due", "reasons", "age", "corpora", "unchecked", "configured"}`.
`outcome` is `due` (some trigger fired), `not_due` (none fired and everything
was checked) or `unchecked` (none fired, but something could not be checked, so
“not due” cannot be claimed). `age` is the last refresh against `max_age_days`;
each corpus row has its watermark (and where it came from), the count newer, its
threshold and whether it triggers. A corpus that could not be counted goes to
`unchecked` with the reason, never silently to zero, and never takes the others
down with it.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### acquaint.refresh.watermark_of(text)

The first ISO date or time in `text`; a date alone counts as the end of that day, UTC (everything through it was read). `None` when there is none, or it is no real date.

* **Return type:**
  [`datetime`](https://docs.python.org/3/library/datetime.html#datetime.datetime) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### acquaint.refresh.watermark_rows(sources_md)

The rows of the watermark table (the first table with a `Watermark` column under the first heading containing “watermark” that has one): `{"corpus", "where", "watermark", "text"}`, `watermark` parsed (`None` when the cell holds no date).

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]
