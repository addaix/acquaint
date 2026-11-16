# acquaint.digests

Digests: what a conversation with an entity said, written up through a watermark.

A digest is one Markdown file per conversation, in the entity’s folder
(`people/<id>/digests/<name>.md`, and the same under projects, orgs and groups). Its
frontmatter is the state of the processing, and its body is the notes:

```yaml
schema: acquaint/digest/v1
ref: whatsapp:ada-lovelace        # the correspond conversation reference: the SSOT
archive: correspond               # where the raw messages live (never this store)
through:                          # the watermark, per scope: processed up to this time
  content: '2026-11-03T19:37:47+01:00'
  style: '2026-06-21T19:27:45+02:00'
checked: '2026-11-09T08:00:00+01:00'   # when the conversation was last read, even if nothing new was in it
updated: '2026-11-04'
```

`checked` is not `through`: a channel read on a quiet day has no new message, so
`through` cannot move, yet the next sweep must know it was looked at (`checked`).
[`swept()`](#acquaint.digests.swept) gives, per channel, the oldest `checked` of its digests: “when did we last
read Slack for this entity?” without reading each file.

`through` answers “from which date and time is this conversation new?”: hand it to
`correspond read <ref> --since <through>` to get what has not been processed. Two scopes
exist because a conversation can be mined for how someone writes (`style`, the
**acquaint-profile** skill) separately from what it said (`content`, the
**acquaint-digest** skill).

The body has five sections: *Orientation* (who is in it, what it is for, its span),
*Takeaways* (decisions, agreements, asks, facts the operator will need), *Themes* (the
recurring subjects, each with where it comes up), *Index* (the conversation cut into
dated sections so an agent can go straight to one) and *Open threads* (what is waiting on
whom). Every item in those sections carries an anchor, `[at: <ISO time>]` (a message time
in the archive: `rg -n '^2026-07-03T14:02' <transcript>`) or a `[source: …]`; the
messages themselves stay in the archive, never here (`POLICY.md`: no whole message
bodies).

```pycon
>>> digest_name("whatsapp:ada-lovelace"), digest_name("github:example/demo#12")
('digests/whatsapp-ada-lovelace.md', 'digests/github-example-demo-12.md')
```

### Module Attributes

| [`SCOPES`](#acquaint.digests.SCOPES)            | what the conversation said, and how its author writes.                |
|--------------------------------------------------------------------|-----------------------------------------------------------------------|
| [`ANCHORED_SECTIONS`](#acquaint.digests.ANCHORED_SECTIONS) | Sections whose every item needs an `[at: …]` or `[source: …]` anchor. |

### Functions

| [`append_batch`](#acquaint.digests.append_batch)(entity, ref, \*, heading, rows)       | Add a dated batch (`### heading`) at the end of each named section of a digest, in one write.                                     |
|-----------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| [`digest_name`](#acquaint.digests.digest_name)(ref)                                   | The digest's file name inside the entity's folder, derived from the reference.                                                    |
| [`digest_problems`](#acquaint.digests.digest_problems)(text)                              | `(severity, line, rule, message)` for one digest file: frontmatter shape, unanchored items, long quotes.                          |
| [`digests`](#acquaint.digests.digests)(entity)                                    | Every digest of the entity: its reference, file and watermarks.                                                                   |
| [`find_digest`](#acquaint.digests.find_digest)(entity, ref)                           | The digest of one conversation, found by the `ref` in its frontmatter (else by file name).                                        |
| [`mark_digest`](#acquaint.digests.mark_digest)(entity, ref[, through, checked, ...])  | Move the watermark of `scope` to `through`, and/or record when the conversation was last read (`checked`).                        |
| [`sweep_digests`](#acquaint.digests.sweep_digests)(entity, channel, checked, \*[, ...]) | Stamp `checked` on the digests of `channel` that a sweep read, creating those that do not exist yet.                              |
| [`swept`](#acquaint.digests.swept)(entity)                                      | `{channel: oldest checked time}` over the entity's digests; `None` when one of the channel's digests was never (validly) checked. |
| [`new_digest`](#acquaint.digests.new_digest)(entity, ref, \*[, archive, today])      | Create the digest of `ref` from its template, with no watermark yet.                                                              |
| [`parse_rows`](#acquaint.digests.parse_rows)(rows)                                   | `{section: [line, ...]}` from a mapping, JSON text, or `section | line | anchor` rows (one per line; the anchor is optional).     |

### acquaint.digests.ANCHORED_SECTIONS *= ('Takeaways', 'Themes', 'Index', 'Open threads')*

Sections whose every item needs an `[at: …]` or `[source: …]` anchor.

### acquaint.digests.SCOPES *= ('content', 'style')*

what the conversation said, and how its author writes.

* **Type:**
  What a watermark can cover

### acquaint.digests.append_batch(entity, ref, , heading, rows, through=None, scope='content', force=False, today=None)

Add a dated batch (`### heading`) at the end of each named section of a digest, in one write.

`rows` is `{section: [line, ...]}`, JSON text of that, or `section | line | anchor`
rows. Each line becomes one bullet and must be one line. Anything that would add an item
[`digest_problems()`](#acquaint.digests.digest_problems) flags as unanchored under Takeaways, Themes, Index or Open threads
is refused, and then nothing is written. With `through` the watermark of `scope` moves
in the same write (`scope` and `force` mean nothing without it).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### acquaint.digests.digest_name(ref)

The digest’s file name inside the entity’s folder, derived from the reference.

* **Return type:**
  [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)

### acquaint.digests.digest_problems(text)

`(severity, line, rule, message)` for one digest file: frontmatter shape, unanchored items, long quotes.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`tuple`](https://docs.python.org/3/builtins/stdtypes.html#tuple)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`int`](https://docs.python.org/3/builtins/functions.html#int) | [`None`](https://docs.python.org/3/builtins/constants.html#None), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

### acquaint.digests.digests(entity)

Every digest of the entity: its reference, file and watermarks.

* **Return type:**
  [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)]

### acquaint.digests.find_digest(entity, ref)

The digest of one conversation, found by the `ref` in its frontmatter (else by file name).

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict) | [`None`](https://docs.python.org/3/builtins/constants.html#None)

### acquaint.digests.mark_digest(entity, ref, through=None, , checked=None, scope='content', force=False, today=None)

Move the watermark of `scope` to `through`, and/or record when the conversation was last read (`checked`). Moving either back needs `force`.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### acquaint.digests.new_digest(entity, ref, , archive='correspond', today=None)

Create the digest of `ref` from its template, with no watermark yet. Refuses when one exists.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### acquaint.digests.parse_rows(rows)

`{section: [line, ...]}` from a mapping, JSON text, or `section | line | anchor` rows (one per line; the anchor is optional).

```pycon
>>> parse_rows("takeaways | Agreed to ship | [at: 2026-11-03T10:00:00Z]")
{'Takeaways': ['Agreed to ship [at: 2026-11-03T10:00:00Z]']}
>>> parse_rows("Index | Kickoff | 2026-11-03T10:00:00Z")
{'Index': ['Kickoff [at: 2026-11-03T10:00:00Z]']}
>>> parse_rows('{"Themes": ["Pricing [source: thread-1]"]}')
{'Themes': ['Pricing [source: thread-1]']}
```

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`list`](https://docs.python.org/3/builtins/stdtypes.html#list)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str)]]

### acquaint.digests.sweep_digests(entity, channel, checked, , refs=(), archive='correspond', force=False, today=None)

Stamp `checked` on the digests of `channel` that a sweep read, creating those that do not exist yet.

With `refs` (conversation references `<channel>:…`) exactly those are stamped, each
created first when missing; without, every digest of the channel is (the sweep read the
whole channel kind). A digest already checked later keeps that later time (unless
`force`), so a sweep never fails halfway: every check is made before anything is written.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)

### acquaint.digests.swept(entity)

`{channel: oldest checked time}` over the entity’s digests; `None` when one of the channel’s digests was never (validly) checked.

The oldest, because a sweep is only as complete as its least-recently-read conversation.
Digests without a `<channel>:<id>` reference are not counted.

* **Return type:**
  [`dict`](https://docs.python.org/3/builtins/stdtypes.html#dict)[[`str`](https://docs.python.org/3/builtins/stdtypes.html#str), [`str`](https://docs.python.org/3/builtins/stdtypes.html#str) | [`None`](https://docs.python.org/3/builtins/constants.html#None)]
