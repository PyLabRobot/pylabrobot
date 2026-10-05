"""Rules about the client's source that no browser is needed to check.

The viewer draws only when something asks it to, which made a whole class of mistake invisible: a
function that changes the scene and does not ask is a silent freeze, not an error. The answer was to
stop asking at the point of change and ask at the edges instead - the few places anything can get
into the page at all. This keeps it that way.
"""

import pathlib
import re
import unittest

STATIC = pathlib.Path(__file__).parent / "static"


def page_sources():
  """Every module of the page, as (name, lines): the rule holds across all of them."""
  return [(path.name, path.read_text().splitlines()) for path in sorted(STATIC.glob("*.js"))]


# Every way into the viewer, and what comes in through it. A call to `invalidate` anywhere else is
# the scattering this replaced: correct on the day it is written, and one edit away from being
# forgotten somewhere it matters.
BOUNDARIES = {
  "invalidate": "its own definition",
  "connect": "a message arriving from the server",
  "resize": "the viewport changing shape",
  "atBoundary": "a call from outside the page",
  "hideSelectionLater": "a timer the page set for itself, to take the selection box down",
  None: "the input listeners - clicks and keys anywhere, the pointer over the viewport, a crossing"
  " into a row that raises a hover box - and the camera's own change event",
}


def enclosing_function(lines, index):
  """The top-level function containing this line, or None if it sits at module level."""
  for above in range(index, -1, -1):
    text = lines[above]
    opened = re.match(r"^(?:export )?(?:async )?function (\w+)", text)
    if opened is not None:
      return opened.group(1)
    if above != index and text.startswith("}"):
      return None  # a function closed before we reached one, so this is module level
  return None


def module_statement(lines, index):
  """The top-level statement holding a module-level line, out to the blank lines either side."""
  start, end = index, index
  while start > 0 and lines[start - 1].strip():
    start -= 1
  while end + 1 < len(lines) and lines[end + 1].strip():
    end += 1
  return "\n".join(lines[start : end + 1])


# What the module-level listeners are wired to: the inputs, and the camera's own change event.
LISTENED_FOR = ("pointerdown", "keydown", "click", "pointermove", "wheel", "mouseover", "change")


class InvalidationTests(unittest.TestCase):
  def test_only_the_edges_ask_for_a_frame(self):
    stray = []
    for name, lines in page_sources():
      for index, text in enumerate(lines):
        if "invalidate" not in text or text.lstrip().startswith(("import", "//", "*")):
          continue
        where = enclosing_function(lines, index)
        if where not in BOUNDARIES:
          stray.append(f"  {name}:{index + 1} in {where}(): {text.strip()}")

    self.assertEqual(
      stray,
      [],
      "invalidate() is called outside the edges of the viewer:\n"
      + "\n".join(stray)
      + "\n\nAsk for a frame where something enters the page, not where something changes. The"
      " places that may are:\n"
      + "\n".join(f"  {name or 'module level'} - {why}" for name, why in BOUNDARIES.items()),
    )

  def test_every_edge_is_still_wired(self):
    """The rule above only helps while the edges themselves still ask."""
    asked = set()
    statements = []
    for _, lines in page_sources():
      for index, text in enumerate(lines):
        if "invalidate" not in text or text.lstrip().startswith(("import", "//", "*")):
          continue
        where = enclosing_function(lines, index)
        asked.add(where)
        if where is None:
          statements.append(module_statement(lines, index))
    for name, why in BOUNDARIES.items():
      if name is not None:
        self.assertIn(name, asked, f"nothing asks for a frame on {why}")
    # At module level only a listener may ask: a call on load or a timer is a frame nobody wanted.
    loose = [s for s in statements if "addEventListener(" not in s and not s.startswith("import")]
    self.assertEqual(loose, [], "invalidate() at module level outside a listener")
    for event in LISTENED_FOR:
      self.assertTrue(
        any(f'"{event}"' in s for s in statements), f"no listener asks for a frame on {event}"
      )


if __name__ == "__main__":
  unittest.main()
