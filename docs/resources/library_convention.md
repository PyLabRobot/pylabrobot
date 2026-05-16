# Resource Library file convention (PROPOSAL — for review)

Status: **proposal, not yet enforced or wired.** This describes the target
structure for `docs/resources/library/<vendor>.md` so that (a) the full
manufacturer / OEM information is captured deterministically and (b) the
resources are organised into a consistent, machine-readable hierarchy that the
Resource Catalog can render and CI can validate.

It deliberately replaces heuristics in today's catalog extension ("first link
in the preamble", "first blockquote", "first fenced code block") with explicit,
reserved structure.

---

## 1. One file per manufacturer

- Exactly **one H1**, the first heading in the file: the canonical manufacturer
  name. This is both the displayed name and the de-duplication key.
  - `# Corning Inc.`
- The filename is the slug (`corning.md`); it is *not* the source of the name.

## 2. OEM metadata (explicit, labelled — never inferred)

Immediately after the H1, a bullet list of labelled links. Keys are fixed and
case-insensitive; each value is a single absolute URL. All keys are optional
but recognised keys must use exactly these labels:

```markdown
- **Website:** https://www.corning.com
- **Wikipedia:** https://en.wikipedia.org/wiki/Corning_Inc.
```

Rationale: today the catalog grabs "whichever link appears first", which is why
some manufacturers surface a Wikipedia link and others a homepage. With labelled
keys both can be captured and shown distinctly, with no ambiguity.

## 3. Reserved info sections

These headings are **reserved**: they hold manufacturer information and are
**excluded from the resource organisation tree**. Names are exact.

### `## About`

A single descriptive paragraph (plain prose or one blockquote). Replaces the
"first blockquote in the preamble" heuristic.

```markdown
## About

Corning Incorporated is an American multinational technology company that
specialises in specialty glass, ceramics, and related materials.
```

### `## Brand structure` (optional)

A human-curated overview of the manufacturer's brand hierarchy, as a fenced
code block (ASCII tree) and/or prose. This is narrative context for humans; it
is **not** the source of truth for how resources are organised (see §4).

```markdown
## Brand structure

​```
Thermo Fisher Scientific Inc.
├── Applied Biosystems
│   └── MicroAmp
└── Thermo Scientific
    ├── Nalgene
    └── Nunc
​```
```

## 4. Resource organisation = heading nesting

The organisational tree the catalog renders is derived **only** from the
`##` / `###` / `####` heading nesting of the non-reserved sections — not from
the `## Brand structure` art (which only a few files have today).

- `##` = top-level grouping. A manufacturer chooses one consistent axis:
  **brand-first** (`## Costar`, `## Axygen`) **or** **category-first**
  (`## Plates`, `## Tip Racks`) — not a mix.
- `###` / `####` = finer grouping under that (e.g. `## Costar` → `### Plates`).
- A section that directly contains resources must end in a definition table.

## 5. Definition table (fixed shape)

Every leaf section that lists resources contains exactly one table with this
exact header:

```markdown
| Description | Image | PLR definition |
|-|-|-|
| <human description><br>Part no.: <pn><br>[manufacturer website](<url>) | ![](img/<vendor>/<file>) | `Exact_PLR_Definition` |
```

Rules per row:

- **Description**: free text. Optional `Part no.: …` and a
  `[manufacturer website](<url>)` link, `<br>`-separated.
- **Image**: a markdown image whose path resolves to a real file under
  `docs/resources/library/img/<vendor>/…`, or an absolute URL.
- **PLR definition**: exactly **one** backticked identifier matching
  `^[A-Za-z][A-Za-z0-9_]*$`, and it must resolve to a callable in
  `pylabrobot.resources`. One definition per row.

## 6. Worked example

```markdown
# Corning Inc.

- **Website:** https://www.corning.com
- **Wikipedia:** https://en.wikipedia.org/wiki/Corning_Inc.

## About

> Corning Incorporated is an American multinational technology company
> specialising in specialty glass and ceramics.

## Costar

### Plates

| Description | Image | PLR definition |
|-|-|-|
| 96-well, 2 mL, V-bottom<br>Part no.: 3960<br>[manufacturer website](https://ecatalog.corning.com/3960) | ![](img/corning/Cor_96_wellplate_2mL_Vb.jpg) | `Cor_96_wellplate_2mL_Vb` |

## Axygen

### Plates

| Description | Image | PLR definition |
|-|-|-|
| 384-well PCR, V-bottom<br>Part no.: PCR-384 | ![](img/corning/Axy_384.jpg) | `Axy_384_wellplate_50uL_Vb` |
```

---

## What CI would enforce (objective)

These are mechanically checkable and proposed to **fail** CI on violation:

1. Exactly one H1.
2. OEM metadata list, if present, uses only recognised labels with valid
   absolute URLs.
3. Reserved section names spelled exactly (`About`, `Brand structure`).
4. No prose/tables placed where the parser expects structure (e.g. a resource
   table outside any non-reserved section).
5. Every definition table matches the fixed header.
6. Each PLR definition: single identifier, regex-valid, resolves to a real
   `pylabrobot.resources` factory.
7. Every image path resolves to an existing file.
8. No duplicate PLR definition names across the whole library.

## What is NOT in scope for CI (author / project decision)

These are real consistency issues but are **naming/structure policy**, not
mechanics, and tie into the open "Resource Library vs Catalog" /
plural-vs-singular discussion. The spec *recommends* a rule but the project
owner decides; CI should not impose it:

- Heading **naming**: Title Case, singular vs plural ("Tip Rack" vs
  "TipRacks" vs "Tip racks"), and the typo class ("Plate Adapterrs").
  *Recommendation:* Title Case, singular category nouns.
- Whether organisation is **brand-first or category-first** per manufacturer.

## Migration note

Adopting this fully requires: updating the catalog extension to parse the
explicit metadata + reserved sections + heading-derived tree, migrating the
~23 existing vendor files, then enabling the CI. That is a separate,
author-coordinated effort; this document is the spec to agree on first.
