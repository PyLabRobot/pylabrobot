"""The numeric rate scales a step selects from.

The word-valued rates -- travel rate and peristaltic flow rate -- are vocabulary and live in
``enums.steps``. What is here is the scales that are plain numbers.
"""

from __future__ import annotations

FLOW_RATES = range(1, 12)
"""The flow rates the washer and syringe steps accept, fastest last."""

CELL_WASHING_FLOW_RATES = (1, 2)
"""The flow rates below the normal minimum, available only with the cell washing module fitted."""

PERI_WASH_DISPENSE_RATES = (10, 15, 20, 25, 30, 120, 140, 160)
"""Dispense rate in µL/s for each peristaltic wash dispense rate, in selection order."""

PERI_WASH_ASPIRATE_RATES = (10, 15, 20, 25, 50)
"""Aspirate rate in µL/s for each peristaltic wash aspirate rate, in selection order.

The scales are not prefixes of one another: the last aspirate rate is 50 µL/s where the dispense
scale reaches 30 at the same position.
"""
