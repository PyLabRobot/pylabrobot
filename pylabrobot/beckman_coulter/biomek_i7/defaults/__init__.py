"""A generic Biomek i7's cold start: what a simulated instrument answers when given no instrument's
own recording.

Made from one instrument's cold-start recording (the requests the Master Controller sent at a
start and the boards' replies), with every serial number replaced by a placeholder of the same
length.
"""

from pathlib import Path

HERE = Path(__file__).parent

GENERIC_RECORDING = HERE / "biomek_i7_generic_coldstart.json"
