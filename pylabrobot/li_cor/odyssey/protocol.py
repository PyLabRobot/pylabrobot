"""CGI paths, XML queries, and HTML parsers for the Odyssey Classic."""

import re
from html.parser import HTMLParser
from typing import Optional
from xml.sax.saxutils import escape

_SCAN_BASE = "/scanapp/scan/nonjava"

_CONFIGURE_URL_PATH = f"{_SCAN_BASE}/configure.pl"

_COMMAND_URL_PATH = f"{_SCAN_BASE}/command.pl"

_INITIALIZING_URL_PATH = f"{_SCAN_BASE}/initializing.pl"

_TIME_URL_PATH = f"{_SCAN_BASE}/time.pl"

_INFO_URL_PATH = "/scanapp/imaging/nonjava/info.pl"


def _parse_error_block(body: str) -> Optional[str]:
  """Extract <Error shorterror="X">message</Error> if present."""
  m = re.search(
    r'<Error\s+shorterror="([^"]+)"\s*>\s*(.*?)\s*</Error>',
    body,
    re.IGNORECASE | re.DOTALL,
  )
  if m:
    short = m.group(1).strip()
    detail = re.sub(r"\s+", " ", m.group(2)).strip()
    return f"{short}: {detail}"
  return None


def _parse_info_html(html: str) -> dict[str, str]:
  """Parse the imaging info panel HTML."""

  def _extract(label: str) -> str:
    pattern = rf"{label}\s*[:]\s*(?:<[^>]+>)*\s*([^<\n]+)"
    match = re.search(pattern, html, re.IGNORECASE)
    return match.group(1).strip() if match else ""

  return {
    "dimensions": _extract("Dimensions"),
    "file_size": _extract("File Size"),
    "time_left": _extract("Time Left"),
  }


_IMAGE_BASE = "/scanapp/imaging/nonjava"

_SCAN_LIST_PATH = "/scanapp/scan/nonjava/"

_SCAN_IMAGE_PATH = "/scan/image"

_SAVELOG_URL_PATH = f"{_IMAGE_BASE}/savelog.pl"


def _tiff_xml(group: str, scan_name: str, channel: int) -> str:
  """Build the XML query string for a TIFF download."""
  return (
    f"<image><in>"
    f"<scangroup>{escape(group)}</scangroup>"
    f"<scan>{escape(scan_name)}</scan>"
    f"<format>tiff</format>"
    f"<channel>{channel}</channel>"
    f"<clip><x0>0</x0><x1>0</x1><y0>0</y0><y1>0</y1></clip>"
    f"</in></image>"
  )


def _jpeg_xml(
  group: str,
  scan_name: str,
  contrast_700: int = 5,
  contrast_800: int = 5,
  channels: str = "700 800",
  background: str = "black",
  clip: tuple[int, int, int, int] = (0, 0, 0, 0),
  vflip: bool = True,
  hflip: bool = True,
  zoom: int = 1,
) -> str:
  """Build the XML query string for a JPEG preview."""
  x0, x1, y0, y1 = clip
  return (
    f"<image><in>"
    f"<scangroup>{escape(group)}</scangroup>"
    f"<scan>{escape(scan_name)}</scan>"
    f"<zoom>{zoom}</zoom>"
    f"<contrast700>{contrast_700}</contrast700>"
    f"<contrast800>{contrast_800}</contrast800>"
    f"<channel>{escape(channels)}</channel>"
    f"<background>{escape(background)}</background>"
    f"<clip><x0>{x0}</x0><x1>{x1}</x1><y0>{y0}</y0><y1>{y1}</y1></clip>"
    f"<vflip>{'true' if vflip else 'false'}</vflip>"
    f"<hflip>{'true' if hflip else 'false'}</hflip>"
    f"</in></image>"
  )


class _SelectParser(HTMLParser):
  """Collect decoded option values from one named HTML select element."""

  def __init__(self, name: str) -> None:
    super().__init__(convert_charrefs=True)
    self.name = name
    self.selected = False
    self.options: list[str] = []

  def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
    """Track the selected dropdown and preserve spaces in quoted option values."""
    attributes = dict(attrs)
    if tag == "select":
      self.selected = attributes.get("name") == self.name
    elif tag == "option" and self.selected:
      value = attributes.get("value")
      if value is not None:
        self.options.append(value)

  def handle_endtag(self, tag: str) -> None:
    """Finish collecting when the select element closes."""
    if tag == "select":
      self.selected = False


def _parse_select_options(html: str, select_name: str) -> list[str]:
  """Extract complete, HTML-decoded option values from a named select."""
  parser = _SelectParser(select_name)
  parser.feed(html)
  return parser.options


_STATUS_BASE = "/scanapp/util/status"

_STATUS_URL_PATH = f"{_STATUS_BASE}/"

_STOP_FROM_STATUS_PATH = f"{_STATUS_BASE}/status"


def _parse_status_html(html: str) -> dict[str, str]:
  """Parse the instrument status page HTML.

  Robust to tag layout: finds the label anywhere in the HTML
  (case-insensitive), then walks forward skipping any tags and
  whitespace until it hits the first non-empty text run. Handles
  plain text, tags between label and colon, and table layouts.
  """

  def _extract(label: str) -> str:
    idx = html.lower().find(label.lower())
    if idx < 0:
      return ""
    rest = html[idx + len(label) :]
    rest = re.sub(r"^(?:\s|<[^>]+>)*:?", "", rest, count=1)
    text = re.sub(r"<[^>]+>", "|", rest)
    for fragment in text.split("|"):
      trimmed = fragment.strip()
      if trimmed:
        return trimmed.split("\n")[0].strip()
    return ""

  return {
    "state": _extract("Scanner Status") or "Unknown",
    "current_user": _extract("Current User"),
    "progress": _extract("Percent Complete"),
    "time_remaining": _extract("Time Remaining"),
    "lid_status": _extract("Lid Status"),
  }
