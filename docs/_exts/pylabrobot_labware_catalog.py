from __future__ import annotations

import importlib
import inspect
import json
import re
from html import escape
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from pylabrobot.resources import generate_geometry_catalog


LIBRARY_RELATIVE_ROOT = Path("resources") / "library"
GEOMETRY_INDEX_FILENAME = "labware_geometry_index.json"
RESOURCE_ALIASES = {
  "Azenta4titudeFrameStar_96_wellplate_skirted": "Azenta4titudeFrameStar_96_wellplate_200ul_Vb",
  "BioRad_384_DWP_50uL_Vb": "BioRad_384_wellplate_50uL_Vb",
  "CellTreat_6_DWP_16300ul_Fb": "CellTreat_6_wellplate_16300ul_Fb",
  "Cor_12_wellplate_6900ul_Fb": "Cor_Cos_12_wellplate_6900ul_Fb",
  "Cor_24_wellplate_3470ul_Fb": "Cor_Cos_24_wellplate_3470ul_Fb",
  "Cor_48_wellplate_1620ul_Fb": "Cor_Cos_48_wellplate_1620ul_Fb",
  "Cos_96_wellplate_2mL_Vb": "Cor_96_wellplate_2mL_Vb",
  "Cos_6_wellplate_16800ul_Fb": "Cor_Cos_6_wellplate_16800ul_Fb",
  "Hamilton_mfx_plateholder_DWP_metal_tapped": "hamilton_mfx_plateholder_DWP_metal_tapped",
  "PLT_CAR_P3AC": "PLT_CAR_P3AC_A00",
}


def _library_doc_paths(srcdir: str) -> List[Path]:
  library_root = Path(srcdir) / LIBRARY_RELATIVE_ROOT
  return sorted(path for path in library_root.rglob("*.md") if path.is_file())


# Structural inline tags that pre-PR Sphinx/MyST rendered from these cells.
# Re-enabled here so the catalog doesn't regress files that used inline HTML.
_ALLOWED_TAGS = ("br", "p", "ul", "ol", "li", "b", "strong", "i", "em", "sub", "sup")
_SAFE_HREF = re.compile(r"(?i)^(https?:|mailto:)")


def _render_cell_html(text: str) -> str:
  """Safe-by-construction: escape everything, then re-enable only a fixed
  allowlist of attribute-less structural tags plus sanitised <a href>. Anything
  else (scripts, on* handlers, unsafe schemes) stays escaped as inert text."""
  out = escape(text, quote=True)

  def _md_link(match: "re.Match") -> str:
    label, href = match.group(1), match.group(2)
    if not re.match(r"(?i)^(https?:|mailto:|/|#)", href):
      return label
    return f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'

  out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _md_link, out)

  tags = "|".join(_ALLOWED_TAGS)
  out = re.sub(
    rf"&lt;(/?)({tags})\s*/?&gt;",
    lambda m: f"<{m.group(1)}{m.group(2).lower()}>",
    out,
    flags=re.IGNORECASE,
  )

  def _anchor(match: "re.Match") -> str:
    href = match.group(1)
    if _SAFE_HREF.match(href):
      return f'<a href="{href}" target="_blank" rel="noopener noreferrer">'
    return ""

  out = re.sub(r"&lt;a\s+href=&quot;(.*?)&quot;\s*&gt;", _anchor, out, flags=re.IGNORECASE)
  out = re.sub(r"&lt;/a&gt;", "</a>", out, flags=re.IGNORECASE)
  return out


def _description_to_html(description: str, definition_name: str) -> str:
  parts = [
    part.strip()
    for part in re.split(r"<br\s*/?>", description, flags=re.IGNORECASE)
    if part.strip()
  ]
  if parts and parts[0].strip("'` ") == definition_name:
    parts = parts[1:]
  return "<br>".join(_render_cell_html(part) for part in parts)


def _page_title(markdown: str, fallback: str) -> str:
  for line in markdown.splitlines():
    match = re.match(r"^#\s+(.+?)\s*$", line)
    if match:
      return match.group(1)
  return fallback


def _company_url(markdown: str) -> Optional[str]:
  # Manufacturer pages lead with a reference link whose label varies
  # ("Company Page", "Company page:", "Wikipedia page:", ...), so take the
  # first markdown link in the preamble (before the first ## heading).
  preamble = re.split(r"^#{2,4}\s", markdown, maxsplit=1, flags=re.MULTILINE)[0]
  match = re.search(r"\[[^\]]+\]\((https?://[^)]+)\)", preamble)
  return match.group(1).strip() if match else None


def _blurb(markdown: str) -> Optional[str]:
  # The "about" text is the first blockquote in the preamble.
  preamble = re.split(r"^#{2,4}\s", markdown, maxsplit=1, flags=re.MULTILINE)[0]
  lines: List[str] = []
  for line in preamble.splitlines():
    quoted = re.match(r"^>\s?(.*)$", line)
    if quoted is not None:
      lines.append(quoted.group(1).strip())
    elif lines:
      break
  text = " ".join(part for part in lines if part).strip()
  return text or None


def _brand_tree(markdown: str) -> Optional[str]:
  # Some manufacturer pages (e.g. Thermo Fisher) hand-curate the brand
  # hierarchy as an ASCII tree in a fenced code block in the preamble.
  preamble = re.split(r"^#{2,4}\s", markdown, maxsplit=1, flags=re.MULTILINE)[0]
  match = re.search(r"```[^\n]*\n(.*?)\n```", preamble, flags=re.DOTALL)
  return match.group(1).rstrip("\n") if match else None


def _image_path_from_cell(cell: str, doc_relative_path: Path) -> Optional[str]:
  match = re.search(r"!\[[^\]]*]\(([^)]+)\)", cell)
  if match is None:
    return None
  image_path = match.group(1).strip()
  if image_path.startswith(("http://", "https://", "/")):
    return image_path
  resolved = doc_relative_path.parent / image_path
  if "img" in resolved.parts:
    img_index = resolved.parts.index("img")
    return ("_static/" + "/".join(resolved.parts[img_index + 1:])).replace("//", "/")
  return resolved.as_posix()


def _extract_labware_entries_from_markdown(
  markdown: str,
  doc_relative_path: Path,
) -> List[Dict[str, Any]]:
  entries: List[Dict[str, Any]] = []
  manufacturer = _page_title(markdown, doc_relative_path.stem.replace("_", " ").title())
  heading_stack: List[Any] = []  # of (level, title), level in 2..4

  for line in markdown.splitlines():
    stripped = line.strip()
    heading_match = re.match(r"^(#{2,4})\s+(.+?)\s*$", stripped)
    if heading_match:
      level = len(heading_match.group(1))
      while heading_stack and heading_stack[-1][0] >= level:
        heading_stack.pop()
      heading_stack.append((level, heading_match.group(2)))
      continue

    if not stripped.startswith("|"):
      continue

    cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
    if len(cells) < 3:
      continue

    matches = re.findall(r"`([A-Za-z][A-Za-z0-9_]*)`", cells[-1])
    if len(matches) == 0:
      continue

    section_path = [title for _level, title in heading_stack]
    current_section = section_path[-1] if section_path else ""
    image_path = _image_path_from_cell(cells[1], doc_relative_path)
    for definition_name in matches:
      entries.append({
        "definition": definition_name,
        "manufacturer": manufacturer,
        "section": current_section,
        "section_path": section_path,
        "description_html": _description_to_html(cells[0], definition_name),
        "image": image_path,
        "page": doc_relative_path.with_suffix(".html").as_posix(),
      })

  return entries


def _iter_unique_resource_names(srcdir: str) -> Iterable[str]:
  seen: Set[str] = set()

  for doc_path in _library_doc_paths(srcdir):
    doc_relative_path = doc_path.relative_to(srcdir)
    entries = _extract_labware_entries_from_markdown(
      doc_path.read_text(encoding="utf-8"),
      doc_relative_path,
    )
    for entry in entries:
      name = entry["definition"]
      if name not in seen:
        seen.add(name)
        yield name


def _catalog_entries(srcdir: str) -> List[Dict[str, Any]]:
  entries: List[Dict[str, Any]] = []
  seen: Set[str] = set()

  for doc_path in _library_doc_paths(srcdir):
    doc_relative_path = doc_path.relative_to(srcdir)
    for entry in _extract_labware_entries_from_markdown(
      doc_path.read_text(encoding="utf-8"),
      doc_relative_path,
    ):
      definition_name = entry["definition"]
      if definition_name in seen:
        continue
      seen.add(definition_name)
      entries.append(entry)

  return entries


def _manufacturers_index(srcdir: str) -> Dict[str, Dict[str, Any]]:
  manufacturers: Dict[str, Dict[str, Any]] = {}
  for doc_path in _library_doc_paths(srcdir):
    markdown = doc_path.read_text(encoding="utf-8")
    fallback = doc_path.relative_to(srcdir).stem.replace("_", " ").title()
    name = _page_title(markdown, fallback)
    manufacturers.setdefault(
      name,
      {
        "company_url": _company_url(markdown),
        "blurb": _blurb(markdown),
        "brand_tree": _brand_tree(markdown),
      },
    )
  return manufacturers


def _resource_factory_registry() -> Dict[str, Callable[..., Any]]:
  resources_module = importlib.import_module("pylabrobot.resources")
  registry: Dict[str, Callable[..., Any]] = {}

  for name in dir(resources_module):
    if name.startswith("_"):
      continue
    value = getattr(resources_module, name)
    if callable(value):
      registry[name] = value

  resources_root = Path(resources_module.__file__).resolve().parent
  for module_path in resources_root.rglob("*.py"):
    if module_path.name == "__init__.py" or module_path.name.endswith("_tests.py"):
      continue
    relative_path = module_path.relative_to(resources_root).with_suffix("")
    if "falcon" in relative_path.parts:
      continue
    module_name = "pylabrobot.resources." + ".".join(relative_path.parts)
    try:
      module = importlib.import_module(module_name)
    except Exception:
      continue
    for name, value in vars(module).items():
      if name.startswith("_") or not callable(value):
        continue
      registry.setdefault(name, value)

  return registry


def _resolve_definition_callable(
  definition_name: str,
  registry: Dict[str, Callable[..., Any]],
) -> tuple[Optional[str], Optional[Callable[..., Any]]]:
  candidate_name = RESOURCE_ALIASES.get(definition_name, definition_name)
  if candidate_name in registry:
    return candidate_name, registry[candidate_name]

  lowered = candidate_name.lower()
  matches = [name for name in registry if name.lower() == lowered]
  if len(matches) == 1:
    match = matches[0]
    return match, registry[match]

  return None, None


def _build_resource_definition(
  definition_name: str,
  registry: Dict[str, Callable[..., Any]],
):
  resolved_name, definition = _resolve_definition_callable(definition_name, registry)
  if definition is None or resolved_name is None:
    return None

  signature = inspect.signature(definition)
  kwargs: Dict[str, Any] = {}
  args: List[Any] = []

  parameters = list(signature.parameters.values())
  if any(parameter.name == "name" for parameter in parameters):
    kwargs["name"] = definition_name
  if any(parameter.name == "modules" for parameter in parameters):
    kwargs["modules"] = {}
  elif len(parameters) > 0 and "name" not in signature.parameters:
    first = parameters[0]
    if first.kind in (
      inspect.Parameter.POSITIONAL_ONLY,
      inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
      args.append(definition_name)

  try:
    return definition(*args, **kwargs)
  except Exception:
    return None


def build_labware_geometry_index(srcdir: str) -> Dict[str, Any]:
  resources: Dict[str, Any] = {}
  entries = _catalog_entries(srcdir)
  registry = _resource_factory_registry()

  for definition_name in [entry["definition"] for entry in entries]:
    resource = _build_resource_definition(definition_name, registry)
    if resource is None:
      continue

    try:
      resources[definition_name] = generate_geometry_catalog(resource)
    except Exception:
      continue

  for entry in entries:
    entry["has_geometry"] = entry["definition"] in resources

  return {
    "items": entries,
    "resources": resources,
    "manufacturers": _manufacturers_index(srcdir),
  }


def _write_geometry_index(app) -> None:
  if app.builder.format != "html":
    return

  geometry_index = build_labware_geometry_index(app.srcdir)
  target_dir = Path(app.outdir) / "_static"
  target_dir.mkdir(parents=True, exist_ok=True)
  target_path = target_dir / GEOMETRY_INDEX_FILENAME
  target_path.write_text(
    json.dumps(geometry_index, separators=(",", ":"), ensure_ascii=True),
    encoding="utf-8",
  )


def _build_finished(app, exception: Optional[Exception]) -> None:
  if exception is not None:
    return
  _write_geometry_index(app)


def setup(app):
  app.connect("build-finished", _build_finished)

  return {
    "version": "1.0",
    "parallel_read_safe": True,
    "parallel_write_safe": True,
  }
