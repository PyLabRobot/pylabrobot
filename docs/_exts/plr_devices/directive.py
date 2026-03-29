"""Sphinx directive that renders a 'Supported hardware' table from devices.json.

Usage in MyST markdown::

    ```{supported-devices} shaking
    ```

Or with multiple capabilities::

    ```{supported-devices} heating, shaking
    ```

The directive filters devices.json to rows where the device's ``capabilities``
list intersects with the requested set, then renders a native docutils table
styled by the active Sphinx theme.
"""

import json
from pathlib import Path

from docutils import nodes
from docutils.parsers.rst import Directive


_DEVICES = None


def _load_devices():
    global _DEVICES
    if _DEVICES is None:
        json_path = Path(__file__).resolve().parents[2] / "_static" / "devices.json"
        with open(json_path, encoding="utf-8") as f:
            _DEVICES = json.load(f)
    return _DEVICES


class SupportedDevices(Directive):
    """Render a table of devices that have the requested capabilities."""

    has_content = False
    required_arguments = 1
    optional_arguments = 0
    final_argument_whitespace = True

    def run(self):
        requested = {c.strip() for c in self.arguments[0].split(",")}
        devices = _load_devices()
        matches = [
            d for d in devices if requested & set(d.get("capabilities", []))
        ]

        if not matches:
            para = nodes.paragraph(
                text=f"No supported devices found for: {', '.join(requested)}"
            )
            return [para]

        matches.sort(key=lambda d: (d["vendor"], d["name"]))

        # Build a native docutils table
        table = nodes.table()
        table["classes"].append("table")

        tgroup = nodes.tgroup(cols=4)
        table += tgroup
        for _ in range(4):
            tgroup += nodes.colspec()

        # Header
        thead = nodes.thead()
        tgroup += thead
        header_row = nodes.row()
        thead += header_row
        for title in ("Device", "Vendor", "Status", "Links"):
            entry = nodes.entry()
            entry += nodes.paragraph(text=title)
            header_row += entry

        # Body
        tbody = nodes.tbody()
        tgroup += tbody
        for d in matches:
            row = nodes.row()
            tbody += row

            # Device name (bold)
            name_entry = nodes.entry()
            name_entry += nodes.strong(text=d["name"])
            row += name_entry

            # Vendor
            vendor_entry = nodes.entry()
            vendor_entry += nodes.paragraph(text=d["vendor"])
            row += vendor_entry

            # Status
            status_entry = nodes.entry()
            status_entry += nodes.paragraph(text=d.get("status", ""))
            row += status_entry

            # Links
            links_entry = nodes.entry()
            link_nodes = []
            if d.get("docs"):
                ref = nodes.reference("", "docs", refuri=d["docs"])
                link_nodes.append(ref)
            if d.get("oem"):
                if link_nodes:
                    link_nodes.append(nodes.Text(" · "))
                ref = nodes.reference("", "oem", refuri=d["oem"])
                link_nodes.append(ref)
            if link_nodes:
                para = nodes.paragraph()
                for n in link_nodes:
                    para += n
                links_entry += para
            row += links_entry

        return [table]


def setup(app):
    app.add_directive("supported-devices", SupportedDevices)
    return {"version": "0.2", "parallel_read_safe": True}
