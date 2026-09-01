"""Tests for the device registry and the HTML it renders.

These import :mod:`plr_devices.data` and :mod:`plr_devices.html` directly, which depend only on
the standard library, so they run in every test environment rather than only where Sphinx is
installed.
"""

import json
import tempfile
import unittest
from pathlib import Path

from plr_devices.data import (
  CAPABILITIES,
  KINDS,
  STATUSES,
  Device,
  DeviceRegistryError,
  load_devices,
)
from plr_devices.html import INLINE, render_card, render_card_markdown, render_table

DOCS_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = DOCS_ROOT.parent
REGISTRY = DOCS_ROOT / "_static" / "devices.json"
SOURCE_ROOT = REPO_ROOT / "pylabrobot"

MINIMAL = {"id": "x", "vendor": "v", "name": "n", "kind": "arm", "status": "full"}


def _no_link(_slug):
  return None


class TestRegistry(unittest.TestCase):
  def setUp(self):
    self.devices = load_devices(REGISTRY)
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.tmp = Path(directory.name)

  def write_registry(self, entries) -> Path:
    path = self.tmp / "devices.json"
    path.write_text(json.dumps(entries))
    return path

  def test_registry_loads(self):
    self.assertGreater(len(self.devices), 0)

  def test_ids_are_unique(self):
    ids = [d["id"] for d in self.devices]
    self.assertCountEqual(ids, set(ids))

  def test_sorted_by_vendor_then_name(self):
    keys = [(d["vendor"].lower(), d["name"].lower()) for d in self.devices]
    self.assertEqual(keys, sorted(keys))

  def test_vocabularies_are_respected(self):
    for device in self.devices:
      with self.subTest(device=device["id"]):
        self.assertIn(device["kind"], KINDS)
        self.assertIn(device["status"], STATUSES)
        for capability in device.get("capabilities", []):
          self.assertIn(capability, CAPABILITIES)

  def test_vocabularies_have_no_unused_entries(self):
    """A term nothing uses is a term that will drift. Drop it instead."""
    used_kinds = {d["kind"] for d in self.devices}
    used_capabilities = {c for d in self.devices for c in d.get("capabilities", [])}
    self.assertEqual(set(KINDS) - used_kinds, set())
    self.assertEqual(set(CAPABILITIES) - used_capabilities, set())

  def test_doc_slugs_name_real_pages(self):
    for device in self.devices:
      slug = device.get("doc_slug")
      if not slug:
        continue
      with self.subTest(device=device["id"]):
        base = DOCS_ROOT / "user_guide" / slug
        self.assertTrue(
          any(base.with_suffix(ext).is_file() for ext in (".md", ".ipynb", ".rst")),
          f"doc_slug {slug!r} does not name a page",
        )

  def test_code_slugs_name_real_modules(self):
    for device in self.devices:
      slug = device.get("code_slug")
      if not slug:
        continue
      with self.subTest(device=device["id"]):
        target = SOURCE_ROOT / slug
        self.assertTrue(
          target.is_dir() or target.with_suffix(".py").is_file(),
          f"code_slug {slug!r} is not a module or package",
        )

  def test_links_are_urls(self):
    for device in self.devices:
      for field in ("manager", "oem"):
        if device.get(field):
          with self.subTest(device=device["id"], field=field):
            self.assertTrue(str(device[field]).startswith("https://"))

  def test_malformed_entry_raises(self):
    for bad in (
      [{"id": "x"}],
      [{**MINIMAL, "kind": "gizmo"}],
      [{**MINIMAL, "status": "nope"}],
      [{**MINIMAL, "api_version": "v9"}],
      [{**MINIMAL, "capabilities": "shaking"}],
      [{**MINIMAL, "capabilities": ["teleportation"]}],
      [{**MINIMAL, "models": "model-a"}],
      [{**MINIMAL, "models": [1]}],
      [{**MINIMAL, "manager": "rickwierenga"}],
      [{**MINIMAL, "bogus": 1}],
      ["not an object"],
      {"not": "a list"},
    ):
      with self.subTest(bad=bad), self.assertRaises(DeviceRegistryError):
        load_devices(self.write_registry(bad))

  def test_duplicate_ids_raise(self):
    with self.assertRaises(DeviceRegistryError):
      load_devices(self.write_registry([MINIMAL, MINIMAL]))

  def test_missing_registry_raises(self):
    with self.assertRaises(DeviceRegistryError):
      load_devices(self.tmp / "nope.json")


class TestRendering(unittest.TestCase):
  def setUp(self):
    self.devices = load_devices(REGISTRY)
    self.device = next(d for d in self.devices if d["id"] == "curiox-ht2000")

  def test_card_contains_device_metadata(self):
    html = render_card(self.device, _no_link, _no_link)
    self.assertIn("Curiox", html)
    self.assertIn("HT2000", html)
    self.assertIn("plate washing", html)

  def test_card_contains_models(self):
    device = Device({**self.device, "models": ["HT2000", "HT2100"]})
    html = render_card(device, _no_link, _no_link)
    self.assertIn("Models", html)
    self.assertIn("HT2000, HT2100", html)

  def test_card_links_only_what_resolves(self):
    html = render_card(self.device, _no_link, _no_link)
    self.assertNotIn("docs</a>", html)
    self.assertNotIn("code</a>", html)

    html = render_card(self.device, lambda s: f"/{s}.html", lambda s: f"https://example/{s}")
    self.assertIn("docs</a>", html)
    self.assertIn("code</a>", html)

  def test_card_shows_manager_handle(self):
    self.assertIn("@rickwierenga", render_card(self.device, _no_link, _no_link))

  def test_icons_stroke_in_current_color(self):
    """Icons must follow the surrounding text, in a light or a dark theme alike."""
    html = render_card(self.device, lambda s: "/x.html", lambda s: "https://example")
    self.assertIn("<svg", html)
    self.assertEqual(html.count("<svg"), html.count('stroke="currentColor"'))

  def test_every_link_carries_its_own_label(self):
    """An icon alone would leave nothing behind if a notebook renderer strips the SVG."""
    import re

    html = render_card(self.device, lambda s: "/x.html", lambda s: "https://example")
    for anchor in re.findall(r"<a [^>]*>(.*?)</a>", html, re.S):
      self.assertTrue(re.sub(r"<svg.*?</svg>", "", anchor, flags=re.S).strip())

  def test_markdown_card_has_no_html(self):
    """Notebook source is read by hand; a wall of generated HTML in it is not acceptable."""
    md = render_card_markdown(self.device, lambda s: "x.md", lambda s: "https://example")
    self.assertNotIn("<", md)
    self.assertIn("**Curiox HT2000**", md)
    self.assertIn("[code](https://example)", md)
    self.assertIn("@rickwierenga", md)

  def test_markdown_card_omits_links_that_do_not_resolve(self):
    md = render_card_markdown(self.device, _no_link, _no_link)
    self.assertNotIn("[docs]", md)
    self.assertNotIn("[code]", md)

  def test_markdown_card_contains_models(self):
    device = Device({**self.device, "models": ["HT2000", "HT2100"]})
    md = render_card_markdown(device, _no_link, _no_link)
    self.assertIn("Models: HT2000, HT2100", md)

  def test_inline_styles_only_when_asked(self):
    self.assertNotIn("padding:0.9rem", render_card(self.device, _no_link, _no_link))
    self.assertIn("padding:0.9rem", render_card(self.device, _no_link, _no_link, INLINE))

  def test_inline_card_commits_to_no_theme(self):
    """It has to read correctly on a light and a dark editor background alike."""
    inline = render_card(self.device, _no_link, _no_link, INLINE)
    self.assertIn("color:inherit", inline)
    for light_only in ("#fff", "#fafafa", "#111", "#666"):
      self.assertNotIn(light_only, inline)

  def test_card_escapes_html(self):
    device = Device({**self.device, "name": "<script>alert(1)</script>"})
    self.assertNotIn("<script>", render_card(device, _no_link, _no_link))

  def test_device_name_links_to_its_page(self):
    html = render_table(self.devices, lambda s: f"{s}.html", _no_link, "t")
    self.assertIn('<a href="curiox/curiox-ht2000/hello-world.html">HT2000</a>', html)

  def test_device_without_a_page_stays_plain_text(self):
    html = render_table(self.devices, _no_link, _no_link, "t")
    self.assertIn('<td class="plr-device-name">HT2000</td>', html)

  def test_table_has_a_row_per_device(self):
    html = render_table(self.devices, _no_link, _no_link, "t")
    self.assertEqual(html.count('<tr class="plr-device-row"'), len(self.devices))

  def test_table_filter_chips_match_row_attributes(self):
    """Every chip must filter on an attribute the rows actually carry."""
    import re

    html = render_table(self.devices, _no_link, _no_link, "t")
    for field in set(re.findall(r'data-field="(\w+)"', html)):
      self.assertIn(f'data-{field}="', html)

  def test_table_models_are_searchable_and_toggleable(self):
    html = render_table(self.devices, _no_link, _no_link, "t")
    model_count = sum(max(1, len(device.get("models", []))) for device in self.devices)
    self.assertIn(f"Search {model_count} models", html)
    self.assertIn("Show models", html)
    self.assertIn('class="plr-device-row-toggle"', html)
    self.assertIn('aria-expanded="false"', html)
    self.assertIn('data-has-models="true"', html)
    self.assertIn('<tr class="plr-device-model-row" data-device-id="cole-parmer-masterflex"', html)
    self.assertIn(
      '<td class="plr-device-model-name" colspan="6"><span class="plr-device-model">'
      '<span class="plr-device-model__arrow" aria-hidden="true">↳</span>'
      '<span class="plr-device-model__label">07522-20</span>',
      html,
    )
    masterflex_row = next(
      line for line in html.splitlines() if 'id="device-cole-parmer-masterflex"' in line
    )
    self.assertIn("07522-20", masterflex_row.split('data-search="', 1)[1].split('"', 1)[0])

    model_row = next(
      line
      for line in html.splitlines()
      if 'data-device-id="cole-parmer-masterflex"' in line and ">07522-20</span>" in line
    )
    model_search = model_row.split('data-search="', 1)[1].split('"', 1)[0]
    self.assertIn("07522-20", model_search)
    self.assertNotIn("07522-30", model_search)

  def test_empty_table(self):
    self.assertIn("No devices match", render_table([], _no_link, _no_link, "t"))


if __name__ == "__main__":
  unittest.main()
