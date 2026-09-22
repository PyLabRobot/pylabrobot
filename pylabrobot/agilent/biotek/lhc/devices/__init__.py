"""The instruments, and the behaviour they share.

One class per model, and each is what a user builds: :class:`~.el406.EL406`,
:class:`~.washer_405ts.Washer405TS`, :class:`~.multiflo.MultiFlo` and
:class:`~.multiflo_fx.MultiFloFX`. There is no base class -- what the models have in common is held
rather than inherited: the link to the instrument, the record of what it has fitted, and the
functions in :mod:`.execution` and :mod:`.batch` that run a step, bracket a batch and check a
protocol before it runs. Each model spells out its own public methods over those, so what it can do
is readable in one file.

Only the plain records are re-exported here. A step is encoded against
:class:`~.instrument_settings.InstrumentSettings`, so this package is imported from below as well as
from above and must stay cheap to import; the instruments themselves are exported from the package
above, or imported from their own modules.
"""

from __future__ import annotations

from pylabrobot.agilent.biotek.lhc.devices.build_rules import BuildRules, basecode_for, rules_for
from pylabrobot.agilent.biotek.lhc.devices.instrument_settings import InstrumentSettings
from pylabrobot.agilent.biotek.lhc.devices.settings_comparison import (
  Difference,
  SettingsComparison,
  compare,
)

__all__ = [
  "BuildRules",
  "Difference",
  "InstrumentSettings",
  "SettingsComparison",
  "basecode_for",
  "compare",
  "rules_for",
]
