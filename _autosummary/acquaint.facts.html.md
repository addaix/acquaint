# acquaint.facts

Facts: the small exact things agents keep asking for, as a typed file on the record they belong to.

An optional `facts.yaml` in any entity folder holds one current value per field, each
with the disclosure label acquaint already uses, its source and its dates:

```yaml
schema: acquaint/facts/v1
facts:
  name.full: {value: "Ada Lovelace", label: clear, source: "self: \"I write it Ada Lovelace\"", recorded: 2026-11-09}
  titles.current:
    value: "Analyst"
    label: clear
    source: https://example.org/about
    recorded: 2026-11-09
    valid_from: 2023-01-01
    review_by: 2027-05-09
    status: current            # current | proposed | conflicting
    supersedes: titles.current@2025-02-01
to_confirm: ["inferred time zone"]   # inferred items awaiting the operator
```

Field names follow JSContact (RFC 9553) where one exists (`name.full`, `titles.*`,
`speakToAs.pronouns`, …), so vCard or schema.org exports are mappings later. `label`
is one of [`acquaint.trust.LABELS`](acquaint.trust.html.md#acquaint.trust.LABELS). A `source` follows the rules of a preference
line ([`acquaint.records.source_problem()`](acquaint.records.html.md#acquaint.records.source_problem)): a date alone is not a source.

What reads them: [`facts_problems()`](#acquaint.facts.facts_problems) (`lint`), [`brief_lines()`](#acquaint.facts.brief_lines) (the brief’s
“Facts” block: `clear` facts that are current, never `amber` or `red`) and
[`fact_value()`](#acquaint.facts.fact_value) (`who <id> -f name.full`, `clear` only: a fact with a stricter label
is refused, since `who` knows nothing about its reader). A fact that fails its own lint, is
duplicated, is not `current` or is outside its `valid_from`/`valid_to` is never shown.

```pycon
>>> facts_problems("schema: acquaint/facts/v1\nfacts:\n  name.full: {value: Ada, label: clear, source: operator, recorded: 2026-11-09}\n", today="2026-11-10")
[]
```

### Module Attributes

| [`OPEN_LABELS`](#acquaint.facts.OPEN_LABELS)   | Labels a fact may be read at without knowing who reads it.   |
|----------------------------------------------------------------|--------------------------------------------------------------|

### Functions

| [`brief_lines`](#acquaint.facts.brief_lines)(entity, \*, max_chars[, today])   | `- name: value` for each readable fact, within `max_chars` (the overflow note included); what does not fit is counted, not cut mid-line.                  |
|------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------|
| [`fact_value`](#acquaint.facts.fact_value)(entity, name, \*[, today])         | `(value, refusal)` of a fact by dotted name (any case): the value when it is readable, else `None` and why not (`None, None` when there is no such fact). |
| [`facts`](#acquaint.facts.facts)(entity)                                 | The entity's facts by name; `{}` when there is no file, it does not parse, or the file as a whole has an error (lint reports that).                       |
| [`facts_problems`](#acquaint.facts.facts_problems)(text, \*, today)               | `(severity, rule, message)` for one `facts.yaml`: shape, label, source, dates, repeated keys, a passed `review_by`.                                       |

### acquaint.facts.OPEN_LABELS *= ('clear',)*

Labels a fact may be read at without knowing who reads it.

### acquaint.facts.brief_lines(entity, , max_chars, today=None)

`- name: value` for each readable fact, within `max_chars` (the overflow note included); what does not fit is counted, not cut mid-line.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]

### acquaint.facts.fact_value(entity, name, , today=None)

`(value, refusal)` of a fact by dotted name (any case): the value when it is readable, else `None` and why not (`None, None` when there is no such fact).

* **Return type:**
  [`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`Any`](https://docs.python.org/3/library/typing.html#typing.Any), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)]

### acquaint.facts.facts(entity)

The entity’s facts by name; `{}` when there is no file, it does not parse, or the file as a whole has an error (lint reports that).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### acquaint.facts.facts_problems(text, , today)

`(severity, rule, message)` for one `facts.yaml`: shape, label, source, dates, repeated keys, a passed `review_by`. A message never echoes what was written.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]
