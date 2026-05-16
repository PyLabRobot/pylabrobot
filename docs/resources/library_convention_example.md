# Example vendor file (reference for library_convention.md)

This is a complete, conformant reference for the proposed
`docs/resources/library/<vendor>.md` convention. It is **not** a real vendor
page and is intentionally placed outside `resources/library/` so the catalog
extension does not scrape it. Copy this skeleton when adding a manufacturer.

---

# Acme Labware Inc.

- **Website:** https://www.acme-labware.example
- **Wikipedia:** https://en.wikipedia.org/wiki/Example

## About

> Acme Labware Inc. is a fictional manufacturer of plates, tip racks, and
> reservoirs, used here purely to demonstrate the file convention.

## Brand structure

```
Acme Labware Inc.
├── AcmePure (consumables brand)
│   ├── Plates
│   └── Reservoirs
└── AcmeTips (tips brand)
    └── Tip Racks
```

## AcmePure

### Plates

| Description | Image | PLR definition |
|-|-|-|
| 96-well, 2 mL, V-bottom<br>Part no.: AP-9620<br>[manufacturer website](https://www.acme-labware.example/p/AP-9620) | ![](img/acme/Acme_96_wellplate_2mL_Vb.jpg) | `Acme_96_wellplate_2mL_Vb` |
| 384-well, 120 uL, flat bottom<br>Part no.: AP-3841<br>[manufacturer website](https://www.acme-labware.example/p/AP-3841) | ![](img/acme/Acme_384_wellplate_120uL_Fb.jpg) | `Acme_384_wellplate_120uL_Fb` |

### Reservoirs

| Description | Image | PLR definition |
|-|-|-|
| Single-channel reservoir, 290 mL<br>Part no.: AP-RES290 | ![](img/acme/Acme_1_troughplate_290000uL_Vb.jpg) | `Acme_1_troughplate_290000uL_Vb` |

## AcmeTips

### Tip Racks

| Description | Image | PLR definition |
|-|-|-|
| 96 tips, 1000 uL, filtered<br>Part no.: AT-1000F<br>[manufacturer website](https://www.acme-labware.example/p/AT-1000F) | ![](img/acme/Acme_96_tiprack_1000uL_filtered.jpg) | `Acme_96_tiprack_1000uL_filtered` |

---

## Why this is conformant

- **One H1** (`# Acme Labware Inc.`) — the canonical name + dedup key.
- **OEM metadata** as a labelled list (`Website:`, `Wikipedia:`) — both
  captured unambiguously, no "first link" guessing.
- **`## About`** reserved section holds the description (not a scraped
  blockquote heuristic).
- **`## Brand structure`** reserved section holds the human-curated overview;
  it is *not* used to build the catalog tree.
- **Organisation = heading nesting**: brand-first here
  (`## AcmePure` → `### Plates`/`### Reservoirs`, `## AcmeTips` →
  `### Tip Racks`), one consistent axis, reserved sections excluded.
- **Definition tables**: exact `| Description | Image | PLR definition |`
  header; one backticked, regex-valid PLR definition per row; image paths
  under `img/<vendor>/…`; optional `Part no.` and `[manufacturer website]`.
- Headings use Title Case, singular category nouns ("Tip Rack", "Plate") —
  the *recommended* (not CI-enforced) naming policy.
