"""HTML fragments for device tables and device cards."""

from html import escape
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .data import (
  API_VERSION_DESCRIPTIONS,
  STATUS_DESCRIPTIONS,
  STATUS_LABELS,
  Device,
  capability_hue,
)

# Turns a device's doc_slug into a URI relative to the page being rendered, and its code_slug
# into a source URL. Either may return None, in which case that link is left out.
DocURI = Callable[[str], Optional[str]]
CodeURI = Callable[[str], Optional[str]]


# Inline styles for a card that has to stand on its own, outside a page that loads
# plr_devices.css: a notebook opened in VS Code, JupyterLab, nbviewer or GitHub. Those renderers
# strip <style> blocks, so there is no way to ask a media query which theme is in use. Instead
# nothing here commits to a light or a dark palette: text is inherited from the host, and every
# surface is a translucent tint that darkens a light background and lightens a dark one. The card
# therefore reads correctly in either theme without knowing which one it is in.
SURFACE = "rgba(127,127,127,0.09)"
HAIRLINE = "rgba(127,127,127,0.35)"

INLINE = {
  "card": f"margin:1.25rem 0;padding:0.9rem 1.1rem;border:1px solid {HAIRLINE};"
  f"border-radius:10px;background:{SURFACE};color:inherit;",
  "head": "display:flex;align-items:flex-start;justify-content:space-between;gap:1rem;",
  "vendor": "font-size:0.8rem;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;opacity:0.65;",
  "name": "font-size:1.25rem;font-weight:700;line-height:1.25;",
  "status_wrap": "flex:0 0 auto;white-space:nowrap;",
  "kind": "margin-top:0.15rem;font-size:0.85rem;opacity:0.65;",
  "badges": "margin-top:0.6rem;",
  "row": "display:flex;gap:0.6rem;margin-top:0.35rem;font-size:0.85rem;",
  "key": "flex:0 0 4.6rem;font-weight:600;opacity:0.65;",
  "value": "min-width:0;overflow-wrap:anywhere;",
  "api": "font-size:0.82rem;background:transparent;padding:0;overflow-wrap:anywhere;",
  "links": "margin-top:0.75rem;font-size:0.9rem;",
  "link": "margin-right:0.5rem;",
  "badge": "display:inline-block;margin:0 0.25rem 0.2rem 0;padding:0.05rem 0.5rem;border-radius:8px;"
  "font-size:0.78rem;line-height:1.5;white-space:nowrap;color:inherit;",
  "status": "display:inline-block;padding:0.05rem 0.5rem;border-radius:8px;font-size:0.78rem;"
  "line-height:1.5;font-weight:600;white-space:nowrap;color:inherit;",
  "api_version": f"display:inline-block;margin-left:0.3rem;padding:0.05rem 0.35rem;"
  f"border:1px solid {HAIRLINE};border-radius:8px;font-size:0.7rem;line-height:1.5;opacity:0.65;",
}

# Hue per support level, tinted the same way as capability badges.
STATUS_HUES = {"full": 145, "mostly": 215, "basic": 45, "wip": None}


def _tint(hue: Optional[int]) -> str:
  """A translucent background and border in one hue, legible over light and dark alike."""
  if hue is None:
    return f"background:{SURFACE};border:1px solid {HAIRLINE};"
  return f"background:hsl({hue} 70% 50% / 0.22);border:1px solid hsl({hue} 70% 50% / 0.45);"


def _style(styles: Optional[Dict[str, str]], key: str, extra: str = "") -> str:
  """The style attribute for an element, empty when a stylesheet is doing the work."""
  if styles is None:
    return f' style="{extra}"' if extra else ""
  return f' style="{styles.get(key, "")}{extra}"'


# Icons are inline SVG rather than an icon font: a card rendered in a notebook has no stylesheet
# and no webfont to draw on. They stroke in currentColor, so they follow the surrounding text in
# either theme, and every icon sits next to its own text label so nothing is lost if a notebook
# renderer strips the markup.
ICONS = {
  "docs": '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/>',
  "code": '<path d="M9 7l-5 5 5 5M15 7l5 5-5 5"/>',
  "oem": '<path d="M14 4h6v6M20 4l-8 8"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  "manager": '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>',
}

_ICON_STYLE = "width:1em;height:1em;vertical-align:-0.13em;margin-right:0.3em;"


def _icon(name: str) -> str:
  return (
    f'<svg class="plr-device-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" '
    f'style="{_ICON_STYLE}">{ICONS[name]}</svg>'
  )


def _tooltip(trigger: str, text: str) -> str:
  """Wrap an element so hovering it shows ``text``. Styled by plr_devices.css, no script."""
  return f'<span class="plr-tip" data-tip="{escape(text)}" tabindex="0">{trigger}</span>'


def _capability_badge(capability: str, styles: Optional[Dict[str, str]] = None) -> str:
  hue = capability_hue(capability)
  if styles is None:
    # The stylesheet turns the hue into a background and a border.
    attr = f' style="--plr-badge-hue: {hue}"'
  else:
    attr = f' style="{styles["badge"]}{_tint(hue)}"'
  return f'<span class="plr-device-badge"{attr}>{escape(capability)}</span>'


def _status_badge(device: Device, styles: Optional[Dict[str, str]] = None) -> str:
  status = str(device["status"])
  label = STATUS_LABELS.get(status, status)
  extra = _tint(STATUS_HUES.get(status)) if styles else ""
  return (
    f'<span class="plr-device-status plr-device-status--{escape(status)}"'
    f"{_style(styles, 'status', extra)}>{escape(label)}</span>"
  )


def _api_version_badge(device: Device, styles: Optional[Dict[str, str]] = None) -> str:
  api_version = device.get("api_version")
  if not api_version:
    return ""
  return f'<span class="plr-device-api-version"{_style(styles, "api_version")}>{escape(api_version)}</span>'


def _manager(device: Device, styles: Optional[Dict[str, str]] = None) -> str:
  """The forum profile of whoever looks after this driver, shown as their handle."""
  url = device.get("manager")
  if not url:
    return ""
  handle = str(url).rstrip("/").rsplit("/", 1)[-1]
  return (
    f'<a class="plr-device-link" href="{escape(str(url))}">{_icon("manager")}@{escape(handle)}</a>'
  )


def _links(
  device: Device, doc_uri: DocURI, code_uri: CodeURI, styles: Optional[Dict[str, str]] = None
) -> List[str]:
  attrs = f'class="plr-device-link"{_style(styles, "link")}'
  links = []
  if device.get("doc_slug"):
    uri = doc_uri(str(device["doc_slug"]))
    if uri:
      links.append(f'<a {attrs} href="{escape(uri)}">{_icon("docs")}docs</a>')
  if device.get("code_slug"):
    uri = code_uri(str(device["code_slug"]))
    if uri:
      links.append(f'<a {attrs} href="{escape(uri)}">{_icon("code")}code</a>')
  if device.get("oem"):
    links.append(
      f'<a {attrs} href="{escape(str(device["oem"]))}" rel="nofollow noopener">'
      f'{_icon("oem")}OEM</a>'
    )
  return links


def _cell_link(text: str, uri: Optional[str]) -> str:
  """Link a table cell's text when there is somewhere to send it, otherwise leave it as text."""
  if not uri:
    return escape(text)
  return f'<a href="{escape(uri)}">{escape(text)}</a>'


def _status_cell(device: Device) -> str:
  """The support level and API generation, each explaining itself on hover."""
  status = str(device["status"])
  cell = _tooltip(_status_badge(device), STATUS_DESCRIPTIONS.get(status, ""))
  api_version = device.get("api_version")
  if api_version:
    cell += _tooltip(_api_version_badge(device), API_VERSION_DESCRIPTIONS.get(str(api_version), ""))
  return cell


def _link_slots(device: Device, doc_uri: DocURI, code_uri: CodeURI) -> str:
  """The table's links, one fixed slot per kind, so they line up down the column.

  A device without a documentation page still occupies the docs slot, so "code" is never sitting
  under "docs" one row above it.
  """
  slots = [
    ("docs", "docs", doc_uri(str(device["doc_slug"])) if device.get("doc_slug") else None),
    ("code", "code", code_uri(str(device["code_slug"])) if device.get("code_slug") else None),
    ("oem", "OEM", str(device["oem"]) if device.get("oem") else None),
  ]
  rendered = []
  for icon, label, uri in slots:
    if not uri:
      rendered.append('<span class="plr-device-slot"></span>')
      continue
    rel = ' rel="nofollow noopener"' if icon == "oem" else ""
    rendered.append(
      f'<span class="plr-device-slot"><a href="{escape(uri)}"{rel}>{_icon(icon)}{label}</a></span>'
    )
  return "".join(rendered)


def _search_index(device: Device, model: Optional[str] = None) -> str:
  """Everything the search box matches against, lowercased.

  A device row indexes every model. A model sub-row indexes only itself, so a specific model
  search can hide its potentially hundreds of siblings.
  """
  parts = [device["vendor"], device["name"], device["kind"]]
  if model is None:
    parts.extend(
      [
        STATUS_LABELS.get(str(device["status"]), str(device["status"])),
        device.get("api", ""),
        device.get("notes", ""),
        str(device.get("manager", "")).rstrip("/").rsplit("/", 1)[-1],
        *device.get("capabilities", []),
        *device.get("models", []),
      ]
    )
  else:
    parts.extend([*device.get("capabilities", []), model])
  return " ".join(str(p) for p in parts if p).lower()


def render_card(
  device: Device, doc_uri: DocURI, code_uri: CodeURI, styles: Optional[Dict[str, str]] = None
) -> str:
  """A card describing one device.

  Args:
    styles: pass :data:`INLINE` to inline every rule, for a card that has to render where
      ``plr_devices.css`` is not loaded. ``None`` leaves styling to the stylesheet.
  """

  capabilities = "".join(_capability_badge(c, styles) for c in device.get("capabilities", []))
  links = _links(device, doc_uri, code_uri, styles)

  rows = []
  if device.get("models"):
    rows.append(("Models", ", ".join(escape(str(model)) for model in device["models"])))
  if device.get("api"):
    api = escape(str(device["api"]))
    rows.append(("API", f'<code class="plr-device-api"{_style(styles, "api")}>{api}</code>'))
  if device.get("manager"):
    rows.append(("Manager", _manager(device, styles)))
  if device.get("notes"):
    rows.append(("Notes", escape(str(device["notes"]))))

  meta = "".join(
    f'<div class="plr-device-card__row"{_style(styles, "row")}>'
    f'<span class="plr-device-card__key"{_style(styles, "key")}>{escape(key)}</span>'
    f'<span class="plr-device-card__value"{_style(styles, "value")}>{value}</span></div>'
    for key, value in rows
  )

  return f"""<div class="plr-device-card" id="device-{escape(str(device["id"]))}"{_style(styles, "card")}>
  <div class="plr-device-card__head"{_style(styles, "head")}>
    <div>
      <div class="plr-device-card__vendor"{_style(styles, "vendor")}>{escape(str(device["vendor"]))}</div>
      <div class="plr-device-card__name"{_style(styles, "name")}>{escape(str(device["name"]))}</div>
    </div>
    <div class="plr-device-card__status"{_style(styles, "status_wrap")}>\
{_status_badge(device, styles)}{_api_version_badge(device, styles)}</div>
  </div>
  <div class="plr-device-card__kind"{_style(styles, "kind")}>{escape(str(device["kind"]))}</div>
  <div class="plr-device-card__badges"{_style(styles, "badges")}>{capabilities}</div>
  {meta}
  <div class="plr-device-card__links"{_style(styles, "links")}>{" ".join(links)}</div>
</div>"""


def render_card_markdown(device: Device, doc_uri: DocURI, code_uri: CodeURI) -> str:
  """A card as plain markdown, for notebooks.

  Notebook source is read and edited by hand, so it gets markdown rather than the generated HTML
  the documentation pages use. It styles itself out of whatever theme the notebook is opened in,
  its links stay clickable, and its text stays selectable.
  """

  head = f"**{device['vendor']} {device['name']}** — {device['kind']} · {STATUS_LABELS[str(device['status'])]}"
  if device.get("api_version"):
    head += f" ({device['api_version']})"

  lines = [head]
  if device.get("models"):
    lines.append(f"Models: {', '.join(device['models'])}")
  if device.get("capabilities"):
    lines.append(f"Capabilities: {', '.join(device['capabilities'])}")

  facts = []
  if device.get("api"):
    facts.append(f"API: `{device['api']}`")
  if device.get("manager"):
    url = str(device["manager"])
    facts.append(f"Manager: [@{url.rstrip('/').rsplit('/', 1)[-1]}]({url})")
  if facts:
    lines.append(" · ".join(facts))

  links = []
  if device.get("doc_slug"):
    uri = doc_uri(str(device["doc_slug"]))
    if uri:
      links.append(f"[docs]({uri})")
  if device.get("code_slug"):
    uri = code_uri(str(device["code_slug"]))
    if uri:
      links.append(f"[code]({uri})")
  if device.get("oem"):
    links.append(f"[OEM]({device['oem']})")
  if links:
    lines.append(" · ".join(links))

  if device.get("notes"):
    lines.append(f"_{device['notes']}_")

  return "\n\n".join(lines)


def _filter_chips(label: str, field: str, values: Sequence[Tuple[str, str]]) -> str:
  """``values`` is a sequence of (filter value, display text) pairs."""
  if len(values) < 2:
    return ""
  chips = "".join(
    f'<button type="button" class="plr-device-chip" data-field="{escape(field)}" '
    f'data-value="{escape(value)}" aria-pressed="false">{escape(text)}</button>'
    for value, text in values
  )
  return (
    f'<div class="plr-device-chips" role="group" aria-label="Filter by {escape(label)}">'
    f'<span class="plr-device-chips__label">{escape(label)}</span>{chips}</div>'
  )


def render_table(
  devices: Sequence[Device],
  doc_uri: DocURI,
  code_uri: CodeURI,
  table_id: str,
  search: bool = True,
  filters: bool = True,
) -> str:
  """A device table, optionally preceded by a search box and filter chips."""

  if not devices:
    return '<p class="plr-device-empty">No devices match this selection.</p>'

  rows = []
  for device in devices:
    capabilities = "".join(_capability_badge(c) for c in device.get("capabilities", []))
    device_id = str(device["id"])

    name_cell = _cell_link(
      str(device["name"]),
      doc_uri(str(device["doc_slug"])) if device.get("doc_slug") else None,
    )
    if device.get("notes"):
      name_cell += _tooltip(
        '<span class="plr-device-note">*</span>', str(device["notes"])
      )
    rows.append(
      f'<tr class="plr-device-row" id="device-{escape(device_id)}"'
      f' data-device-id="{escape(device_id)}"'
      f' data-search="{escape(_search_index(device))}"'
      f' data-vendor="{escape(str(device["vendor"]))}"'
      f' data-status="{escape(str(device["status"]))}"'
      f' data-capabilities="{escape("|".join(device.get("capabilities", [])))}">'
      f"<td>{escape(str(device['vendor']))}</td>"
      f'<td class="plr-device-name">{name_cell}</td>'
      f'<td class="plr-device-capabilities">{capabilities}</td>'
      f"<td>{_status_cell(device)}</td>"
      f'<td class="plr-device-links">{_link_slots(device, doc_uri, code_uri)}</td>'
      f"<td>{_manager(device)}</td>"
      "</tr>"
    )
    for model in device.get("models", []):
      model = str(model)
      rows.append(
        f'<tr class="plr-device-model-row" data-device-id="{escape(device_id)}"'
        f' data-search="{escape(_search_index(device, model))}" hidden>'
        '<td class="plr-device-model-name" colspan="6"><span class="plr-device-model">'
        '<span class="plr-device-model__arrow" aria-hidden="true">↳</span>'
        f'<span class="plr-device-model__label">{escape(model)}</span></span></td>'
        "</tr>"
      )

  controls = ""
  has_models = any(device.get("models") for device in devices)
  model_count = sum(max(1, len(device.get("models", []))) for device in devices)
  if search or has_models:
    controls = '<div class="plr-device-search">'
    if search:
      controls += (
        f'<input type="search" id="{escape(table_id)}-search" class="plr-device-search__input" '
        f'placeholder="Search {model_count} models by vendor, model, type or capability…" '
        f'aria-label="Search models" autocomplete="off">'
      )
    if has_models:
      controls += (
        f'<button type="button" class="plr-device-model-toggle" aria-pressed="false" '
        f'aria-controls="{escape(table_id)}-table">Show models</button>'
      )
    controls += "</div>"
  if filters:
    capabilities_all = [
      (c, c) for c in sorted({c for d in devices for c in d.get("capabilities", [])})
    ]
    controls += (
      '<div class="plr-device-filters">'
      + _filter_chips("Capability", "capabilities", capabilities_all)
      + "</div>"
    )

  return f"""<div class="plr-devices" id="{escape(table_id)}" data-plr-devices>
{controls}
<div class="plr-device-table-wrapper">
<table class="plr-device-table" id="{escape(table_id)}-table">
  <thead><tr>
    <th>Vendor</th><th>Device</th><th>Capabilities</th><th>Support</th>
    <th>Links</th><th>Manager</th>
  </tr></thead>
  <tbody>
{chr(10).join(rows)}
  </tbody>
</table>
</div>
<p class="plr-device-empty" hidden>No devices match your search.</p>
</div>"""
