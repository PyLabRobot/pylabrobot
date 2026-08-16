"""Sphinx directives that render the device registry (``devices.json``).

``device-table`` renders a searchable table of every device in the registry, or of
the subset selected by its options::

    ```{device-table}
    ```

    ```{device-table}
    :kind: plate reader
    :capabilities: absorbance, luminescence
    ```

``device-card`` renders one device as a card, by registry id::

    ```{device-card} curiox-ht2000
    ```

Both are usable anywhere MyST is parsed, including markdown cells of the notebooks
under ``docs/user_guide``.
"""

from pathlib import Path

from docutils import nodes
from docutils.parsers.rst import Directive, directives
from sphinx.errors import ExtensionError
from sphinx.util import logging

from .data import (
  DeviceRegistryError,
  filter_devices,
  get_device,
  get_devices,
  registry_path,
)
from .html import render_card, render_table

logger = logging.getLogger(__name__)


class device_placeholder(nodes.General, nodes.Element):
  """Replaced with rendered HTML once docnames can be resolved to URIs."""


def _note_registry_dependency(directive):
  """Rebuild pages that use a directive whenever devices.json changes."""
  env = directive.state.document.settings.env
  env.note_dependency(str(registry_path(env)))


def _flag(raw):
  if raw is None or raw.strip() == "":
    return True
  return raw.strip().lower() not in ("false", "no", "off", "0")


class DeviceTable(Directive):
  """Render a searchable table of registry devices."""

  has_content = False
  required_arguments = 0
  optional_arguments = 0
  option_spec = {
    "capabilities": directives.unchanged,
    "vendor": directives.unchanged,
    "kind": directives.unchanged,
    "status": directives.unchanged,
    "search": directives.unchanged,
    "filters": directives.unchanged,
  }

  def run(self):
    _note_registry_dependency(self)
    node = device_placeholder("")
    node["kind"] = "table"
    node["filters"] = {
      field: self.options.get(field, "").strip()
      for field in ("capabilities", "vendor", "kind", "status")
    }
    node["search"] = _flag(self.options.get("search"))
    node["filters_ui"] = _flag(self.options.get("filters"))
    return [node]


class DeviceCard(Directive):
  """Render one device from the registry as a card."""

  has_content = False
  required_arguments = 1
  optional_arguments = 0
  final_argument_whitespace = True
  option_spec: dict = {}

  def run(self):
    _note_registry_dependency(self)
    node = device_placeholder("")
    node["kind"] = "card"
    node["device_id"] = self.arguments[0].strip()
    node.line = self.lineno
    return [node]


def _doc_uri_factory(app, fromdocname):
  """Resolve a device's ``doc_slug`` to a URI relative to the page being rendered."""

  env = app.builder.env
  prefix = app.config.plr_devices_doc_prefix

  def doc_uri(doc_slug):
    docname = prefix + doc_slug
    if docname not in env.found_docs:
      logger.warning(
        "device registry: doc_slug %r does not name a page (looked for %r)",
        doc_slug,
        docname,
        location=fromdocname,
      )
      return None
    try:
      return app.builder.get_relative_uri(fromdocname, docname)
    except Exception:  # builders without relative URIs, e.g. `dummy`
      return None

  return doc_uri


def _code_base_from_context(app):
  """Point code links at the same repository and branch as the theme's "edit this page" links."""
  context = app.config.html_context
  return "https://github.com/{github_user}/{github_repo}/blob/{github_version}".format(**context)


def _code_uri_factory(app, fromdocname):
  """Resolve a device's ``code_slug`` to a URL for its driver's source."""

  base = (app.config.plr_devices_code_base or _code_base_from_context(app)).rstrip("/")
  source_root = Path(app.confdir).parent / app.config.plr_devices_code_root

  def code_uri(code_slug):
    target = source_root / code_slug
    if not (target.is_dir() or target.with_suffix(".py").is_file()):
      logger.warning(
        "device registry: code_slug %r is not a module or package under %s",
        code_slug,
        source_root,
        location=fromdocname,
      )
      return None
    return f"{base}/{app.config.plr_devices_code_root}/{code_slug}"

  return code_uri


def _resolve_card_device(app, node, fromdocname):
  device = get_device(app, node["device_id"])
  if device is None:
    logger.warning(
      "device-card: no device with id %r in the device registry",
      node["device_id"],
      location=(fromdocname, node.line),
    )
  return device


def _render(app, doctree, fromdocname):
  doc_uri = _doc_uri_factory(app, fromdocname)
  code_uri = _code_uri_factory(app, fromdocname)

  for index, node in enumerate(list(doctree.findall(device_placeholder))):
    if node["kind"] == "table":
      devices = filter_devices(get_devices(app), node["filters"])
      html = render_table(
        devices,
        doc_uri,
        code_uri,
        table_id=f"plr-devices-{index}",
        search=node["search"],
        filters=node["filters_ui"],
      )
    else:
      device = _resolve_card_device(app, node, fromdocname)
      if device is None:
        node.parent.remove(node)
        continue
      html = render_card(device, doc_uri, code_uri)

    node.replace_self(nodes.raw("", html, format="html"))


def _load_registry(app):
  """Load and validate the registry up front, so errors surface before any page is read."""
  app.plr_devices = None
  try:
    get_devices(app)
  except DeviceRegistryError as e:
    raise ExtensionError(str(e)) from e


def setup(app):
  app.add_config_value("plr_devices_json", "_static/devices.json", "env")
  # doc_slug is relative to this docname prefix; code_slug to this directory of the repository.
  app.add_config_value("plr_devices_doc_prefix", "user_guide/", "env")
  app.add_config_value("plr_devices_code_root", "pylabrobot", "env")
  # Empty means: the repository and branch in html_context.
  app.add_config_value("plr_devices_code_base", "", "env")

  app.add_node(device_placeholder)
  app.add_directive("device-table", DeviceTable)
  app.add_directive("device-card", DeviceCard)

  app.add_css_file("plr_devices.css")
  app.add_js_file("plr_devices.js")

  app.connect("builder-inited", _load_registry)
  app.connect("doctree-resolved", _render)

  return {"version": "1.0", "parallel_read_safe": True, "parallel_write_safe": True}
