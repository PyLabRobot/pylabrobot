---
orphan: true
---

# Resource Library file convention (PROPOSAL — for review)

Status: **proposal, not yet enforced or wired.** This describes the target
structure for `docs/resources/library/<vendor>.md` so that (a) the full
manufacturer / OEM information is captured deterministically and (b) the
resources are organised into a consistent, machine-readable hierarchy that the
Resource Library can render and CI can validate.

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

Immediately after the H1, an **optional** bullet list of labelled links. The
list, any individual key, and the whole block are all optional — many
manufacturers (smaller OEMs, regional suppliers, the DIY entries) have no
external page at all, and that is normal, not a violation.

Recognised keys (case-insensitive, value = one absolute URL):

- **`Website:`** — the manufacturer's own page. This is the only key worth
  treating as the "primary" reference when present.
- Any number of *additional, optional* labelled links, e.g.
  **`Wikipedia:`**, **`Catalog:`**, **`Datasheet:`**. **None of these is
  expected** — `Wikipedia:` in particular must not be assumed, since most
  manufacturers have no Wikipedia page.

```markdown
- **Website:** https://www.corning.com
- **Wikipedia:** https://en.wikipedia.org/wiki/Corning_Inc.   (optional extra)
```

A file with no links is fully conformant. **Fallback:** no metadata → no
reference link in the panel; only `Website:` → a "Website ↗" link; a
`Wikipedia:` present → an additional "Wikipedia ↗" link. The panel never
implies a missing key should exist.

Rationale: today the catalog grabs "whichever link appears first", so some
manufacturers surface a Wikipedia link and others a homepage purely by
ordering accident. Explicit, optional labels remove the ambiguity without
making any particular source mandatory.

## 3. Reserved info sections (all optional)

These headings are **reserved** and **optional**. When present they hold
manufacturer information and are **excluded from the resource organisation
tree**; when absent the panel simply omits that part — never an error. The
names, *if used*, must be spelled exactly, but **no file is required to have
them**. The only mandatory content is §1 (one H1) and §5 (at least one
resource table whose definitions resolve); everything in this section is
optional enrichment.

### `## About` (optional)

A single descriptive paragraph (plain prose or one blockquote). Replaces the
"first blockquote in the preamble" heuristic.
**Fallback:** if absent, no description paragraph is shown — not an error.

```markdown
## About

Corning Incorporated is an American multinational technology company that
specialises in specialty glass, ceramics, and related materials.
```

### `## Brand structure` (optional)

A human-curated overview of the manufacturer's brand hierarchy, as a fenced
code block (ASCII tree) and/or prose. This is narrative context for humans; it
is **not** the source of truth for how resources are organised (see §4).
**Fallback:** if absent, the panel shows only the heading-derived breakdown
from §4 (which every file has) — not an error.

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

Mechanically checkable, proposed to **fail** CI on violation. Split into the
mandatory core and conditional checks that only apply to *optional* content
when it is present.

**Required (mandatory core — every file):**

1. Exactly one H1.
2. At least one resource table, and every PLR definition in it is a single
   regex-valid identifier that resolves to a real `pylabrobot.resources`
   factory.
3. Every definition table matches the fixed header.
4. Every image path resolves to an existing file.
5. No duplicate PLR definition names across the whole library.

**Conditional (optional content — checked only if present):**

6. OEM metadata list, *if present*, uses only recognised labels with valid
   absolute URLs.
7. Reserved sections are **not required**. A section titled exactly
   `## About` or `## Brand structure` is treated as reserved (excluded from
   the resource tree); near-miss spellings are flagged so they are not
   silently parsed as resource categories.
8. No resource table placed where the parser expects an info section, or
   vice-versa.

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
