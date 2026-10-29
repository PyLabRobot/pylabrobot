"""Structural lifecycle policy checks for the closed notebook inventory."""

import ast
import builtins
from dataclasses import dataclass
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
from typing import Any, Literal, Optional
import urllib.request

import pytest


ROOT = Path(__file__).resolve().parents[1]

RECOGNIZED_IPYTHON_LINES = frozenset(
  {
    "%load_ext autoreload",
    "%autoreload 2",
    '%pip install "pylabrobot[ftdi]"',
    "!head -n15 validation.json",
  }
)

DEPRECATED_INTERACTIVE_LABEL = (
  "Deprecated asyncio/Jupyter compatibility: this cross-cell session uses `setup()` and must "
  "run the matching `stop()` cell before re-running setup, switching event loops, or closing "
  "the notebook."
)

RuntimeClass = Literal[
  "structural-only",
  "software-double-runnable",
  "loopback-only-runnable",
  "hardware-prohibited",
]


@dataclass(frozen=True)
class NotebookPolicy:
  """Safety and lifecycle policy for one notebook."""

  runtime: RuntimeClass
  deprecated_cross_cell: bool
  setup_targets: tuple[str, ...]
  stop_targets: tuple[str, ...]


LIFECYCLE_NOTEBOOKS: dict[str, NotebookPolicy] = {
  "docs/cookbook/manual_operator_jupyter.ipynb": NotebookPolicy(
    "software-double-runnable",
    False,
    (),
    (),
  ),
  "docs/cookbook/slack_notifications.ipynb": NotebookPolicy(
    "software-double-runnable", False, (), ()
  ),
  "docs/cookbook/star_movement_plate_to_alpaqua_core.ipynb": NotebookPolicy(
    "hardware-prohibited", True, ("star", "viewer"), ("viewer", "star")
  ),
  "docs/resources/carrier/plate-carrier/plate_carrier.ipynb": NotebookPolicy(
    "software-double-runnable", True, ("lh",), ("lh",)
  ),
  "docs/user_guide/00_liquid-handling/container_no_go_zones.ipynb": NotebookPolicy(
    "software-double-runnable", True, ("lh",), ("lh",)
  ),
  "docs/user_guide/00_liquid-handling/hamilton-prep/prep_basic_demo.ipynb": NotebookPolicy(
    "hardware-prohibited", True, ("prep", "declared"), ("declared", "prep")
  ),
  "docs/user_guide/00_liquid-handling/mixing.ipynb": NotebookPolicy(
    "software-double-runnable", False, (), ()
  ),
  "docs/user_guide/00_liquid-handling/moving-channels-around.ipynb": NotebookPolicy(
    "hardware-prohibited", True, ("lh",), ("lh",)
  ),
  "docs/user_guide/00_liquid-handling/tutorial_tip_inventory_consolidation.ipynb": NotebookPolicy(
    "loopback-only-runnable", True, ("lh", "visualizer"), ("visualizer", "lh")
  ),
  "docs/user_guide/agilent/benchcel/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("benchcel",), ("benchcel",)
  ),
  "docs/user_guide/agilent/plateloc/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("plateloc",), ("plateloc",)
  ),
  "docs/user_guide/agilent/synergy_ht/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("reader",), ("reader",)
  ),
  "docs/user_guide/agilent/vspin/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("vspin",), ("vspin",)
  ),
  "docs/user_guide/azenta/fluidx/intellixcap96/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("decapper",), ("decapper",)
  ),
  "docs/user_guide/big_bear/orbital-shaker/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("shaker",), ("shaker",)
  ),
  "docs/user_guide/byonoy/absorbance_96/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("reader",), ("reader",)
  ),
  "docs/user_guide/byonoy/luminescence_96/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("reader",), ("reader",)
  ),
  "docs/user_guide/byonoy/luminescence_96/led_bar.ipynb": NotebookPolicy(
    "structural-only", True, ("reader",), ("reader",)
  ),
  "docs/user_guide/cole_parmer/genogrinder/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("grinder",), ("grinder",)
  ),
  "docs/user_guide/curiox/curiox-ht2000/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("washer",), ("washer",)
  ),
  "docs/user_guide/generic/line-barcode-scanner/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("scanner",), ("scanner",)
  ),
  "docs/user_guide/high_res/lid-valet/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("valet",), ("valet",)
  ),
  "docs/user_guide/high_res/sample-storage/ambistore/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("store",), ("store",)
  ),
  "docs/user_guide/high_res/sample-storage/steristore/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("store",), ("store",)
  ),
  "docs/user_guide/high_res/sample-storage/tundrastore/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("store",), ("store",)
  ),
  "docs/user_guide/inheco/cpac/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("control_box",), ("control_box",)
  ),
  "docs/user_guide/inheco/incubator_shaker/hello-world.ipynb": NotebookPolicy(
    "structural-only",
    True,
    ("stack", "stack_a", "stack_b"),
    ("stack", "stack_b", "stack_a"),
  ),
  "docs/user_guide/inheco/scila/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("scila",), ("scila",)
  ),
  "docs/user_guide/inheco/thermoshake/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("control_box",), ("control_box",)
  ),
  "docs/user_guide/kbioscience/kube/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("sealer",), ("sealer",)
  ),
  "docs/user_guide/kbiosystems/ultraseal-epro/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("sealer",), ("sealer",)
  ),
  "docs/user_guide/kbiosystems/ultraseal-pro/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("sealer",), ("sealer",)
  ),
  "docs/user_guide/kbiosystems/ultraseal-xt-pro/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("sealer",), ("sealer",)
  ),
  "docs/user_guide/li_cor/odyssey/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("odyssey",), ("odyssey",)
  ),
  "docs/user_guide/machine-agnostic-features/logging-and-validation/validation.ipynb": NotebookPolicy(
    "structural-only", False, (), ()
  ),
  "docs/user_guide/machine-agnostic-features/using-the-visualizer.ipynb": NotebookPolicy(
    "loopback-only-runnable", True, ("lh", "vis"), ("vis", "lh")
  ),
  "docs/user_guide/machine-agnostic-features/using-trackers.ipynb": NotebookPolicy(
    "software-double-runnable", True, ("lh",), ("lh",)
  ),
  "docs/user_guide/mettler_toledo/scales/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("scale",), ("scale",)
  ),
  "docs/user_guide/micronic/code_reader/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("reader",), ("reader",)
  ),
  "docs/user_guide/molecular_devices/imageXpress/pico.ipynb": NotebookPolicy(
    "structural-only", True, ("pico",), ("pico",)
  ),
  "docs/user_guide/molecular_devices/spectramax/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("reader",), ("reader",)
  ),
  "docs/user_guide/opentrons/flex/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("flex",), ("flex",)
  ),
  "docs/user_guide/opentrons/ot2/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("ot2",), ("ot2",)
  ),
  "docs/user_guide/qinstruments/bioshake/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("bs",), ("bs",)
  ),
  "docs/user_guide/revvity/celigo/advanced-imaging.ipynb": NotebookPolicy(
    "structural-only", True, ("celigo",), ("celigo",)
  ),
  "docs/user_guide/revvity/celigo/components-and-diagnostics.ipynb": NotebookPolicy(
    "structural-only", True, ("celigo",), ("celigo",)
  ),
  "docs/user_guide/revvity/celigo/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("celigo",), ("celigo",)
  ),
  "docs/user_guide/revvity/celigo/scan-planning.ipynb": NotebookPolicy(
    "structural-only", False, ("celigo",), ("celigo",)
  ),
  "docs/user_guide/sartorius/entris2/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("scale",), ("scale",)
  ),
  "docs/user_guide/thermo_fisher/alps/alps300/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("sealer",), ("sealer",)
  ),
  "docs/user_guide/thermo_fisher/alps/alps3000/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("sealer",), ("sealer",)
  ),
  "docs/user_guide/thermo_fisher/alps/alps5000/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("sealer",), ("sealer",)
  ),
  "docs/user_guide/thermo_fisher/btx/gemini/X2/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("gemini",), ("gemini",)
  ),
  "docs/user_guide/thermo_fisher/nanodrop_1000/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("nanodrop",), ("nanodrop",)
  ),
  "docs/user_guide/ufactory/xarm6/hello-world.ipynb": NotebookPolicy(
    "structural-only", True, ("xarm",), ("xarm",)
  ),
}

EXPECTED_RUNTIME_CLASSIFICATIONS: dict[str, RuntimeClass] = {
  "docs/cookbook/manual_operator_jupyter.ipynb": "software-double-runnable",
  "docs/cookbook/slack_notifications.ipynb": "software-double-runnable",
  "docs/cookbook/star_movement_plate_to_alpaqua_core.ipynb": "hardware-prohibited",
  "docs/resources/carrier/plate-carrier/plate_carrier.ipynb": "software-double-runnable",
  "docs/user_guide/00_liquid-handling/container_no_go_zones.ipynb": "software-double-runnable",
  "docs/user_guide/00_liquid-handling/hamilton-prep/prep_basic_demo.ipynb": "hardware-prohibited",
  "docs/user_guide/00_liquid-handling/mixing.ipynb": "software-double-runnable",
  "docs/user_guide/00_liquid-handling/moving-channels-around.ipynb": "hardware-prohibited",
  "docs/user_guide/00_liquid-handling/tutorial_tip_inventory_consolidation.ipynb": (
    "loopback-only-runnable"
  ),
  "docs/user_guide/agilent/benchcel/hello-world.ipynb": "structural-only",
  "docs/user_guide/agilent/plateloc/hello-world.ipynb": "structural-only",
  "docs/user_guide/agilent/synergy_ht/hello-world.ipynb": "structural-only",
  "docs/user_guide/agilent/vspin/hello-world.ipynb": "structural-only",
  "docs/user_guide/azenta/fluidx/intellixcap96/hello-world.ipynb": "structural-only",
  "docs/user_guide/big_bear/orbital-shaker/hello-world.ipynb": "structural-only",
  "docs/user_guide/byonoy/absorbance_96/hello-world.ipynb": "structural-only",
  "docs/user_guide/byonoy/luminescence_96/hello-world.ipynb": "structural-only",
  "docs/user_guide/byonoy/luminescence_96/led_bar.ipynb": "structural-only",
  "docs/user_guide/cole_parmer/genogrinder/hello-world.ipynb": "structural-only",
  "docs/user_guide/curiox/curiox-ht2000/hello-world.ipynb": "structural-only",
  "docs/user_guide/generic/line-barcode-scanner/hello-world.ipynb": "structural-only",
  "docs/user_guide/high_res/lid-valet/hello-world.ipynb": "structural-only",
  "docs/user_guide/high_res/sample-storage/ambistore/hello-world.ipynb": "structural-only",
  "docs/user_guide/high_res/sample-storage/steristore/hello-world.ipynb": "structural-only",
  "docs/user_guide/high_res/sample-storage/tundrastore/hello-world.ipynb": "structural-only",
  "docs/user_guide/inheco/cpac/hello-world.ipynb": "structural-only",
  "docs/user_guide/inheco/incubator_shaker/hello-world.ipynb": "structural-only",
  "docs/user_guide/inheco/scila/hello-world.ipynb": "structural-only",
  "docs/user_guide/inheco/thermoshake/hello-world.ipynb": "structural-only",
  "docs/user_guide/kbioscience/kube/hello-world.ipynb": "structural-only",
  "docs/user_guide/kbiosystems/ultraseal-epro/hello-world.ipynb": "structural-only",
  "docs/user_guide/kbiosystems/ultraseal-pro/hello-world.ipynb": "structural-only",
  "docs/user_guide/kbiosystems/ultraseal-xt-pro/hello-world.ipynb": "structural-only",
  "docs/user_guide/li_cor/odyssey/hello-world.ipynb": "structural-only",
  "docs/user_guide/machine-agnostic-features/logging-and-validation/validation.ipynb": (
    "structural-only"
  ),
  "docs/user_guide/machine-agnostic-features/using-the-visualizer.ipynb": (
    "loopback-only-runnable"
  ),
  "docs/user_guide/machine-agnostic-features/using-trackers.ipynb": "software-double-runnable",
  "docs/user_guide/mettler_toledo/scales/hello-world.ipynb": "structural-only",
  "docs/user_guide/micronic/code_reader/hello-world.ipynb": "structural-only",
  "docs/user_guide/molecular_devices/imageXpress/pico.ipynb": "structural-only",
  "docs/user_guide/molecular_devices/spectramax/hello-world.ipynb": "structural-only",
  "docs/user_guide/opentrons/flex/hello-world.ipynb": "structural-only",
  "docs/user_guide/opentrons/ot2/hello-world.ipynb": "structural-only",
  "docs/user_guide/qinstruments/bioshake/hello-world.ipynb": "structural-only",
  "docs/user_guide/revvity/celigo/advanced-imaging.ipynb": "structural-only",
  "docs/user_guide/revvity/celigo/components-and-diagnostics.ipynb": "structural-only",
  "docs/user_guide/revvity/celigo/hello-world.ipynb": "structural-only",
  "docs/user_guide/revvity/celigo/scan-planning.ipynb": "structural-only",
  "docs/user_guide/sartorius/entris2/hello-world.ipynb": "structural-only",
  "docs/user_guide/thermo_fisher/alps/alps300/hello-world.ipynb": "structural-only",
  "docs/user_guide/thermo_fisher/alps/alps3000/hello-world.ipynb": "structural-only",
  "docs/user_guide/thermo_fisher/alps/alps5000/hello-world.ipynb": "structural-only",
  "docs/user_guide/thermo_fisher/btx/gemini/X2/hello-world.ipynb": "structural-only",
  "docs/user_guide/thermo_fisher/nanodrop_1000/hello-world.ipynb": "structural-only",
  "docs/user_guide/ufactory/xarm6/hello-world.ipynb": "structural-only",
}

TASK2_LIFECYCLE_CELL_IDS: dict[str, tuple[str, ...]] = {
  "docs/cookbook/star_movement_plate_to_alpaqua_core.ipynb": (
    "star-alpaqua-interactive-setup",
    "star-alpaqua-interactive-stop",
  ),
  "docs/resources/carrier/plate-carrier/plate_carrier.ipynb": (
    "plate-carrier-interactive-setup",
    "plate-carrier-interactive-stop",
  ),
  "docs/user_guide/00_liquid-handling/container_no_go_zones.ipynb": (
    "wnozu8h6wt8",
    "mvpra250mkk",
  ),
  "docs/user_guide/00_liquid-handling/hamilton-prep/prep_basic_demo.ipynb": (
    "21b22518",
    "d7d0d253",
    "prep-declared-stop",
    "8578a9bc",
  ),
  "docs/user_guide/00_liquid-handling/moving-channels-around.ipynb": (
    "moving-channels-interactive-setup",
    "moving-channels-interactive-stop",
  ),
  "docs/user_guide/00_liquid-handling/tutorial_tip_inventory_consolidation.ipynb": (
    "21204f35",
    "282d5ad4",
    "tip-inventory-interactive-stop",
  ),
  "docs/user_guide/agilent/benchcel/hello-world.ipynb": (
    "benchcel-connect-code",
    "benchcel-disconnect-code",
  ),
  "docs/user_guide/agilent/plateloc/hello-world.ipynb": ("plateloc-setup", "plateloc-stop"),
  "docs/user_guide/agilent/synergy_ht/hello-world.ipynb": ("8b6489d1", "e9cd0896"),
  "docs/user_guide/agilent/vspin/hello-world.ipynb": (
    "vspin-connect-code",
    "vspin-disconnect-code",
  ),
  "docs/user_guide/azenta/fluidx/intellixcap96/hello-world.ipynb": (
    "729f73e2",
    "a0d6806f",
  ),
  "docs/user_guide/big_bear/orbital-shaker/hello-world.ipynb": (
    "bigbear-connect-code",
    "bigbear-teardown-code",
  ),
  "docs/user_guide/byonoy/absorbance_96/hello-world.ipynb": ("w1m3gjaser", "2bhu26jg5gt"),
  "docs/user_guide/byonoy/luminescence_96/hello-world.ipynb": ("s1-code", "s11-code"),
  "docs/user_guide/byonoy/luminescence_96/led_bar.ipynb": ("connect-code", "teardown-code"),
  "docs/user_guide/cole_parmer/genogrinder/hello-world.ipynb": ("gg-connect", "gg-teardown"),
  "docs/user_guide/curiox/curiox-ht2000/hello-world.ipynb": (
    "ht2000-connect-code",
    "ht2000-disconnect-code",
  ),
  "docs/user_guide/generic/line-barcode-scanner/hello-world.ipynb": (
    "serial-barcode-setup-code",
    "serial-barcode-stop-code",
  ),
  "docs/user_guide/high_res/lid-valet/hello-world.ipynb": ("connect-code", "teardown-code"),
  "docs/user_guide/high_res/sample-storage/ambistore/hello-world.ipynb": (
    "connect-code",
    "stop-code",
  ),
  "docs/user_guide/high_res/sample-storage/steristore/hello-world.ipynb": (
    "connect-code",
    "stop-code",
  ),
  "docs/user_guide/high_res/sample-storage/tundrastore/hello-world.ipynb": (
    "connect-code",
    "stop-code",
  ),
  "docs/user_guide/inheco/cpac/hello-world.ipynb": (
    "cpac-interactive-setup",
    "cpac-interactive-stop",
  ),
  "docs/user_guide/inheco/incubator_shaker/hello-world.ipynb": (
    "incubator-shaker-interactive-setup",
    "incubator-shaker-multi-interactive-setup",
    "incubator-shaker-interactive-stop",
  ),
  "docs/user_guide/inheco/scila/hello-world.ipynb": ("z5fcugljbwk", "ebejgue4wyn"),
  "docs/user_guide/inheco/thermoshake/hello-world.ipynb": ("jdb9stxlizk", "18p0yuw8kd3"),
  "docs/user_guide/kbioscience/kube/hello-world.ipynb": (
    "kube-connect-code",
    "kube-teardown-code",
  ),
  "docs/user_guide/kbiosystems/ultraseal-epro/hello-world.ipynb": (
    "ultraseal-epro-connect-code",
    "ultraseal-epro-teardown-code",
  ),
  "docs/user_guide/kbiosystems/ultraseal-pro/hello-world.ipynb": (
    "ultraseal-pro-connect-code",
    "ultraseal-pro-teardown-code",
  ),
  "docs/user_guide/kbiosystems/ultraseal-xt-pro/hello-world.ipynb": (
    "ultraseal-xt-pro-connect-code",
    "ultraseal-xt-pro-teardown-code",
  ),
  "docs/user_guide/li_cor/odyssey/hello-world.ipynb": ("4ddbaf39", "e70a0d68"),
  "docs/user_guide/machine-agnostic-features/using-the-visualizer.ipynb": (
    "1419f2b3",
    "165c9de4",
    "44a61431",
    "visualizer-interactive-stop",
  ),
  "docs/user_guide/machine-agnostic-features/using-trackers.ipynb": (
    "trackers-interactive-setup",
    "trackers-interactive-stop",
  ),
  "docs/user_guide/mettler_toledo/scales/hello-world.ipynb": (
    "connect-code",
    "disconnect-code",
  ),
  "docs/user_guide/micronic/code_reader/hello-world.ipynb": (
    "micronic-connect-code",
    "micronic-disconnect-code",
  ),
  "docs/user_guide/molecular_devices/imageXpress/pico.ipynb": (
    "pico-interactive-setup",
    "pico-interactive-stop",
  ),
  "docs/user_guide/molecular_devices/spectramax/hello-world.ipynb": (
    "spectramax-setup",
    "spectramax-stop",
  ),
  "docs/user_guide/opentrons/flex/hello-world.ipynb": ("e436da0d", "20068ed9"),
  "docs/user_guide/opentrons/ot2/hello-world.ipynb": ("ot2-setup", "ot2-stop"),
  "docs/user_guide/qinstruments/bioshake/hello-world.ipynb": (
    "bioshake-interactive-setup",
    "bioshake-interactive-stop",
  ),
  "docs/user_guide/revvity/celigo/advanced-imaging.ipynb": ("connect", "cleanup"),
  "docs/user_guide/revvity/celigo/components-and-diagnostics.ipynb": ("connect", "cleanup"),
  "docs/user_guide/revvity/celigo/hello-world.ipynb": ("setup", "teardown"),
  "docs/user_guide/sartorius/entris2/hello-world.ipynb": (
    "entris2-connect-code",
    "entris2-teardown-code",
  ),
  "docs/user_guide/thermo_fisher/alps/alps300/hello-world.ipynb": (
    "alps300-connect-code",
    "alps300-teardown-code",
  ),
  "docs/user_guide/thermo_fisher/alps/alps3000/hello-world.ipynb": (
    "alps3000-connect-code",
    "alps3000-teardown-code",
  ),
  "docs/user_guide/thermo_fisher/alps/alps5000/hello-world.ipynb": (
    "alps5000-connect-code",
    "alps5000-teardown-code",
  ),
  "docs/user_guide/thermo_fisher/btx/gemini/X2/hello-world.ipynb": (
    "setup-code",
    "teardown-code",
  ),
  "docs/user_guide/thermo_fisher/nanodrop_1000/hello-world.ipynb": (
    "nanodrop-connect",
    "nanodrop-stop",
  ),
  "docs/user_guide/ufactory/xarm6/hello-world.ipynb": (
    "xarm6-setup-code",
    "xarm6-teardown-code",
  ),
}

TASK2_REQUIRED_NON_LIFECYCLE_CELL_IDS: dict[str, tuple[str, ...]] = {
  "docs/user_guide/machine-agnostic-features/logging-and-validation/validation.ipynb": (
    "validation-protocol",
  ),
  "docs/user_guide/molecular_devices/spectramax/hello-world.ipynb": ("spectramax-alternative",),
}

SELECTED_PROTOCOL_CELLS = {
  "docs/cookbook/manual_operator_jupyter.ipynb": ("manual-transfer-protocol",),
  "docs/cookbook/slack_notifications.ipynb": ("489da2a0",),
  "docs/user_guide/00_liquid-handling/mixing.ipynb": ("9bc0da6c",),
  "docs/user_guide/machine-agnostic-features/using-the-visualizer.ipynb": (
    "visualizer-loopback-protocol",
  ),
}

RUNTIME_CELL_IMPORTS = {
  ("docs/cookbook/manual_operator_jupyter.ipynb", "manual-transfer-protocol"): (),
  ("docs/cookbook/slack_notifications.ipynb", "489da2a0"): (
    "from collections.abc import Awaitable, Callable",
    "from pylabrobot.liquid_handling import LiquidHandler",
  ),
  ("docs/user_guide/00_liquid-handling/mixing.ipynb", "9bc0da6c"): (
    "from collections.abc import Awaitable, Callable",
    "from typing import Optional",
    "from pylabrobot.liquid_handling import LiquidHandler, LiquidHandlerChatterboxBackend",
    "from pylabrobot.liquid_handling.standard import Mix",
    "from pylabrobot.resources import Cor_96_wellplate_360ul_Fb, PLT_CAR_L5AC_A00, STARDeck, TIP_CAR_480_A00, hamilton_96_tiprack_1000uL_filter",
  ),
  (
    "docs/user_guide/machine-agnostic-features/using-the-visualizer.ipynb",
    "visualizer-loopback-protocol",
  ): (
    "from collections.abc import Awaitable, Callable",
    "from pylabrobot.liquid_handling import LiquidHandler, LiquidHandlerChatterboxBackend",
    "from pylabrobot.resources.hamilton import STARLetDeck",
    "from pylabrobot.visualizer import Visualizer",
  ),
}

_SELECTED_PROTOCOL_FUNCTIONS = {
  (
    "docs/cookbook/manual_operator_jupyter.ipynb",
    "manual-transfer-protocol",
  ): "run_manual_transfer_and_read",
  ("docs/cookbook/slack_notifications.ipynb", "489da2a0"): "run_notified_protocol",
  (
    "docs/user_guide/00_liquid-handling/mixing.ipynb",
    "9bc0da6c",
  ): "run_chatterbox_mixing_protocol",
  (
    "docs/user_guide/machine-agnostic-features/using-the-visualizer.ipynb",
    "visualizer-loopback-protocol",
  ): "run_visualizer_loopback_protocol",
}

DEPRECATED_INTERACTIVE_START_CELLS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
  "docs/cookbook/star_movement_plate_to_alpaqua_core.ipynb": (
    ("star-alpaqua-interactive-setup", ("star", "viewer")),
  ),
  "docs/resources/carrier/plate-carrier/plate_carrier.ipynb": (
    ("plate-carrier-interactive-setup", ("lh",)),
  ),
  "docs/user_guide/00_liquid-handling/container_no_go_zones.ipynb": (("wnozu8h6wt8", ("lh",)),),
  "docs/user_guide/00_liquid-handling/hamilton-prep/prep_basic_demo.ipynb": (
    ("21b22518", ("prep",)),
    ("d7d0d253", ("declared",)),
  ),
  "docs/user_guide/00_liquid-handling/moving-channels-around.ipynb": (
    ("moving-channels-interactive-setup", ("lh",)),
  ),
  "docs/user_guide/00_liquid-handling/tutorial_tip_inventory_consolidation.ipynb": (
    ("21204f35", ("lh",)),
  ),
  "docs/user_guide/agilent/benchcel/hello-world.ipynb": (("benchcel-connect-code", ("benchcel",)),),
  "docs/user_guide/agilent/plateloc/hello-world.ipynb": (("plateloc-setup", ("plateloc",)),),
  "docs/user_guide/agilent/synergy_ht/hello-world.ipynb": (("8b6489d1", ("reader",)),),
  "docs/user_guide/agilent/vspin/hello-world.ipynb": (("vspin-connect-code", ("vspin",)),),
  "docs/user_guide/azenta/fluidx/intellixcap96/hello-world.ipynb": (("729f73e2", ("decapper",)),),
  "docs/user_guide/big_bear/orbital-shaker/hello-world.ipynb": (
    ("bigbear-connect-code", ("shaker",)),
  ),
  "docs/user_guide/byonoy/absorbance_96/hello-world.ipynb": (("w1m3gjaser", ("reader",)),),
  "docs/user_guide/byonoy/luminescence_96/hello-world.ipynb": (("s1-code", ("reader",)),),
  "docs/user_guide/byonoy/luminescence_96/led_bar.ipynb": (("connect-code", ("reader",)),),
  "docs/user_guide/cole_parmer/genogrinder/hello-world.ipynb": (("gg-connect", ("grinder",)),),
  "docs/user_guide/curiox/curiox-ht2000/hello-world.ipynb": (("ht2000-connect-code", ("washer",)),),
  "docs/user_guide/generic/line-barcode-scanner/hello-world.ipynb": (
    ("serial-barcode-setup-code", ("scanner",)),
  ),
  "docs/user_guide/high_res/lid-valet/hello-world.ipynb": (("connect-code", ("valet",)),),
  "docs/user_guide/high_res/sample-storage/ambistore/hello-world.ipynb": (
    ("connect-code", ("store",)),
  ),
  "docs/user_guide/high_res/sample-storage/steristore/hello-world.ipynb": (
    ("connect-code", ("store",)),
  ),
  "docs/user_guide/high_res/sample-storage/tundrastore/hello-world.ipynb": (
    ("connect-code", ("store",)),
  ),
  "docs/user_guide/inheco/cpac/hello-world.ipynb": (("cpac-interactive-setup", ("control_box",)),),
  "docs/user_guide/inheco/incubator_shaker/hello-world.ipynb": (
    ("incubator-shaker-interactive-setup", ("stack",)),
    ("incubator-shaker-multi-interactive-setup", ("stack_a", "stack_b")),
  ),
  "docs/user_guide/inheco/scila/hello-world.ipynb": (("z5fcugljbwk", ("scila",)),),
  "docs/user_guide/inheco/thermoshake/hello-world.ipynb": (("jdb9stxlizk", ("control_box",)),),
  "docs/user_guide/kbioscience/kube/hello-world.ipynb": (("kube-connect-code", ("sealer",)),),
  "docs/user_guide/kbiosystems/ultraseal-epro/hello-world.ipynb": (
    ("ultraseal-epro-connect-code", ("sealer",)),
  ),
  "docs/user_guide/kbiosystems/ultraseal-pro/hello-world.ipynb": (
    ("ultraseal-pro-connect-code", ("sealer",)),
  ),
  "docs/user_guide/kbiosystems/ultraseal-xt-pro/hello-world.ipynb": (
    ("ultraseal-xt-pro-connect-code", ("sealer",)),
  ),
  "docs/user_guide/li_cor/odyssey/hello-world.ipynb": (("4ddbaf39", ("odyssey",)),),
  "docs/user_guide/machine-agnostic-features/using-the-visualizer.ipynb": (("1419f2b3", ("lh",)),),
  "docs/user_guide/machine-agnostic-features/using-trackers.ipynb": (
    ("trackers-interactive-setup", ("lh",)),
  ),
  "docs/user_guide/mettler_toledo/scales/hello-world.ipynb": (("connect-code", ("scale",)),),
  "docs/user_guide/micronic/code_reader/hello-world.ipynb": (
    ("micronic-connect-code", ("reader",)),
  ),
  "docs/user_guide/molecular_devices/imageXpress/pico.ipynb": (
    ("pico-interactive-setup", ("pico",)),
  ),
  "docs/user_guide/molecular_devices/spectramax/hello-world.ipynb": (
    ("spectramax-setup", ("reader",)),
  ),
  "docs/user_guide/opentrons/flex/hello-world.ipynb": (("e436da0d", ("flex",)),),
  "docs/user_guide/opentrons/ot2/hello-world.ipynb": (("ot2-setup", ("ot2",)),),
  "docs/user_guide/qinstruments/bioshake/hello-world.ipynb": (
    ("bioshake-interactive-setup", ("bs",)),
  ),
  "docs/user_guide/revvity/celigo/advanced-imaging.ipynb": (("connect", ("celigo",)),),
  "docs/user_guide/revvity/celigo/components-and-diagnostics.ipynb": (
    ("connect", ("celigo",)),
  ),
  "docs/user_guide/revvity/celigo/hello-world.ipynb": (("setup", ("celigo",)),),
  "docs/user_guide/sartorius/entris2/hello-world.ipynb": (("entris2-connect-code", ("scale",)),),
  "docs/user_guide/thermo_fisher/alps/alps300/hello-world.ipynb": (
    ("alps300-connect-code", ("sealer",)),
  ),
  "docs/user_guide/thermo_fisher/alps/alps3000/hello-world.ipynb": (
    ("alps3000-connect-code", ("sealer",)),
  ),
  "docs/user_guide/thermo_fisher/alps/alps5000/hello-world.ipynb": (
    ("alps5000-connect-code", ("sealer",)),
  ),
  "docs/user_guide/thermo_fisher/btx/gemini/X2/hello-world.ipynb": (
    ("setup-code", ("gemini",)),
  ),
  "docs/user_guide/thermo_fisher/nanodrop_1000/hello-world.ipynb": (
    ("nanodrop-connect", ("nanodrop",)),
  ),
  "docs/user_guide/ufactory/xarm6/hello-world.ipynb": (("xarm6-setup-code", ("xarm",)),),
}

STRUCTURAL_ONLY_HELPER_CELLS: dict[str, tuple[str, ...]] = {
  "docs/cookbook/slack_notifications.ipynb": ("45765d87", "d764ddbd", "f7e35d45"),
}

ALLOWED_RUNNABLE_BACKEND_EXPORTS: set[tuple[str, str]] = {
  ("pylabrobot.legacy.plate_reading", "PlateReaderChatterboxBackend"),
  ("pylabrobot.legacy.storage", "IncubatorChatterboxBackend"),
  ("pylabrobot.liquid_handling", "LiquidHandlerChatterboxBackend"),
  ("pylabrobot.liquid_handling.backends", "LiquidHandlerChatterboxBackend"),
  (
    "pylabrobot.liquid_handling.backends.chatterbox",
    "LiquidHandlerChatterboxBackend",
  ),
  (
    "pylabrobot.liquid_handling.backends.hamilton.STAR_chatterbox",
    "STARChatterboxBackend",
  ),
}


def load_notebook(path: str) -> dict[str, Any]:
  """Load notebook JSON without importing or executing notebook code."""

  with (ROOT / path).open(encoding="utf-8") as notebook_file:
    notebook: dict[str, Any] = json.load(notebook_file)
  return notebook


def normalize_code_source(source: str, path: str, cell_reference: str) -> str:
  """Replace recognized IPython line syntax while preserving source layout."""

  normalized_lines: list[str] = []
  for line_number, line in enumerate(source.splitlines(keepends=True), start=1):
    stripped = line.strip()
    if stripped.startswith("%%"):
      raise AssertionError(
        f"{path} cell {cell_reference} line {line_number}: cell magics are not supported: {stripped}"
      )
    if stripped.startswith(("%", "!")):
      if stripped not in RECOGNIZED_IPYTHON_LINES:
        raise AssertionError(
          f"{path} cell {cell_reference} line {line_number}: unrecognized IPython syntax: {stripped}"
        )
      indentation = line[: len(line) - len(line.lstrip())]
      newline = "\n" if line.endswith("\n") else ""
      line = f"{indentation}pass{newline}"
    normalized_lines.append(line)
  return "".join(normalized_lines)


def code_tree(cell: dict[str, Any], path: str, cell_reference: str) -> ast.Module:
  """Parse one notebook code cell without compiling or executing it."""

  source = cell["source"]
  if isinstance(source, list):
    source = "".join(source)
  return ast.parse(normalize_code_source(source, path, cell_reference), filename=path)


def validate_selected_runtime_cell(
  path: str, cell_id: str, function_name: str
) -> ast.AsyncFunctionDef:
  """Validate and return the sole function in a selected runtime cell."""

  matching_cells = [cell for cell in load_notebook(path)["cells"] if cell.get("id") == cell_id]
  assert len(matching_cells) == 1, (path, cell_id)
  cell = matching_cells[0]
  assert cell["cell_type"] == "code", (path, cell_id)
  tree = code_tree(cell, path, cell_id)
  imports: list[str] = []
  remaining: list[ast.stmt] = []
  reading_imports = True
  for node in tree.body:
    if reading_imports and isinstance(node, (ast.Import, ast.ImportFrom)):
      imports.append(ast.unparse(node))
    else:
      reading_imports = False
      remaining.append(node)
  assert tuple(imports) == RUNTIME_CELL_IMPORTS[(path, cell_id)], (path, cell_id)
  assert len(remaining) == 1 and isinstance(remaining[0], ast.AsyncFunctionDef), (path, cell_id)
  function = remaining[0]
  assert function.name == function_name, (path, cell_id)
  assert function.decorator_list == [], (path, cell_id)
  defaults = (*function.args.defaults, *function.args.kw_defaults)
  assert not any(
    isinstance(node, ast.Call)
    for default in defaults
    if default is not None
    for node in _walk_nodes(default)
  ), (path, cell_id)
  return function


def dotted_name(node: ast.expr) -> Optional[str]:
  """Return a static dotted name for a name or attribute chain."""

  if isinstance(node, ast.Name):
    return node.id
  if isinstance(node, ast.Attribute):
    parent = dotted_name(node.value)
    return None if parent is None else f"{parent}.{node.attr}"
  return None


def is_path_constructor(node: ast.expr) -> bool:
  """Return whether an expression is a pathlib Path constructor call."""

  return isinstance(node, ast.Call) and dotted_name(node.func) in {"Path", "pathlib.Path"}


def is_forbidden_runtime_call(call: ast.Call) -> bool:
  """Return whether a call can open a file or external connection."""

  function = call.func
  if isinstance(function, ast.Name):
    return function.id in {"open", "Path"}
  if not isinstance(function, ast.Attribute):
    return False
  dotted = dotted_name(function)
  if dotted in {"builtins.open", "io.open", "urllib.request.urlopen", "socket.create_connection"}:
    return True
  if function.attr in {"read_text", "read_bytes", "write_text", "write_bytes"}:
    return True
  if function.attr == "open" and is_path_constructor(function.value):
    return True
  return function.attr == "connect" and dotted is not None and dotted.startswith("socket.")


def _has_forbidden_endpoint_literal(tree: ast.AST) -> bool:
  for node in ast.walk(tree):
    if (
      isinstance(node, ast.Name)
      and node.id in {"device_id", "serial_number"}
      or isinstance(node, ast.keyword)
      and node.arg in {"device_id", "serial_number"}
    ):
      return True
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
      continue
    value = node.value
    if "/dev/" in value or re.search(r"\bCOM\d+\b", value, re.IGNORECASE):
      return True
    if re.search(r"https?://", value, re.IGNORECASE):
      return True
    for candidate in re.findall(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", value):
      if not ipaddress.ip_address(candidate).is_loopback:
        return True
    if re.search(r"(?<![\w.])(?:[a-z0-9-]+\.)+[a-z]{2,63}(?![\w.])", value, re.IGNORECASE):
      return True
  return False


def _tracked_notebooks() -> tuple[str, ...]:
  result = subprocess.run(
    ["git", "ls-files", "docs"], cwd=ROOT, check=True, capture_output=True, text=True
  )
  return tuple(path for path in result.stdout.splitlines() if path.endswith(".ipynb"))


def _cell_reference(cell: dict[str, Any], index: int) -> str:
  return str(cell.get("id", f"index {index}"))


def _simple_target(node: ast.expr, path: str, cell_reference: str) -> str:
  if isinstance(node, ast.Name):
    return node.id
  if isinstance(node, ast.Attribute):
    return f"{_simple_target(node.value, path, cell_reference)}.{node.attr}"
  raise AssertionError(f"{path} cell {cell_reference} has a dynamic lifecycle receiver")


def _walk_nodes(tree: ast.AST) -> tuple[ast.AST, ...]:
  nodes: list[ast.AST] = []
  pending = [tree]
  while pending:
    node = pending.pop()
    nodes.append(node)
    pending.extend(ast.iter_child_nodes(node))
  return tuple(nodes)


def _lifecycle_calls(path: str) -> tuple[tuple[int, str, str], ...]:
  calls: list[tuple[int, str, str]] = []
  for index, cell in enumerate(load_notebook(path)["cells"]):
    if cell["cell_type"] != "code":
      continue
    reference = _cell_reference(cell, index)
    tree = code_tree(cell, path, reference)
    cell_calls = sorted(
      (
        node
        for node in _walk_nodes(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"setup", "stop"}
      ),
      key=lambda node: (node.lineno, node.col_offset),
    )
    for call in cell_calls:
      assert isinstance(call.func, ast.Attribute)
      calls.append((index, call.func.attr, _simple_target(call.func.value, path, reference)))
  return tuple(calls)


def _lifecycle_targets(path: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
  """Return setup and stop receivers in cell/source order, retaining repeated local sessions."""

  calls = _lifecycle_calls(path)
  setup_targets = [target for _, operation, target in calls if operation == "setup"]
  stop_targets = [target for _, operation, target in calls if operation == "stop"]
  return tuple(setup_targets), tuple(stop_targets)


def _lifecycle_cell_ids(path: str) -> tuple[str, ...]:
  lifecycle_cell_ids: list[str] = []
  for index, cell in enumerate(load_notebook(path)["cells"]):
    if cell["cell_type"] != "code":
      continue
    tree = code_tree(cell, path, _cell_reference(cell, index))
    if any(
      isinstance(node, ast.Call)
      and isinstance(node.func, ast.Attribute)
      and node.func.attr in {"setup", "stop"}
      for node in _walk_nodes(tree)
    ):
      lifecycle_cell_ids.append(cell["id"])
  return tuple(lifecycle_cell_ids)


def _evaluated_annotation_unions(tree: ast.Module) -> tuple[ast.BinOp, ...]:
  annotations: list[ast.expr] = []
  for node in ast.walk(tree):
    if isinstance(node, (ast.AnnAssign, ast.arg)) and node.annotation is not None:
      annotations.append(node.annotation)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
      annotations.append(node.returns)
  return tuple(
    node
    for annotation in annotations
    for node in ast.walk(annotation)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
  )


def _is_allowed_backend_constructor(node: ast.expr, constructor_names: set[str]) -> bool:
  return (
    isinstance(node, ast.Call)
    and isinstance(node.func, ast.Name)
    and node.func.id in constructor_names
  )


def _definition_names(tree: ast.AST) -> set[str]:
  names = {
    node.id
    for node in ast.walk(tree)
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
  }
  names.update(
    node.name
    for node in ast.walk(tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
  )
  names.update(node.arg for node in ast.walk(tree) if isinstance(node, ast.arg))
  names.update(
    node.name
    for node in ast.walk(tree)
    if isinstance(node, ast.ExceptHandler) and node.name is not None
  )
  return names


def test_lifecycle_notebook_inventory_has_55_explicit_paths() -> None:
  assert len(LIFECYCLE_NOTEBOOKS) == 55
  tracked = set(_tracked_notebooks())
  assert all(path.endswith(".ipynb") for path in LIFECYCLE_NOTEBOOKS)
  assert set(LIFECYCLE_NOTEBOOKS) <= tracked


def test_permanent_gate_uses_python_3_9_compatible_evaluated_annotations() -> None:
  source = Path(__file__).read_text(encoding="utf-8")
  tree = ast.parse(source, filename=__file__, feature_version=(3, 9))
  assert _evaluated_annotation_unions(tree) == ()
  compile(tree, __file__, "exec")


def _connection_example_source() -> str:
  guide = (ROOT / "docs/contributor_guide/structured-concurrency.md").read_text(encoding="utf-8")
  examples = [
    source
    for source in re.findall(r"```python\n(.*?)\n```", guide, flags=re.DOTALL)
    if "class Connection(HasLifespan):" in source
  ]
  assert len(examples) == 1
  return examples[0]


def _assert_connection_example_valid(source: str) -> None:
  tree = ast.parse(source, filename="structured-concurrency.md", feature_version=(3, 9))
  compile(tree, "structured-concurrency.md", "exec")
  assert any(
    isinstance(node, ast.Import)
    and any(alias.name == "anyio" and alias.asname is None for alias in node.names)
    for node in tree.body
  )
  connections = [
    node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Connection"
  ]
  assert len(connections) == 1
  methods = {
    node.name: node for node in connections[0].body if isinstance(node, ast.AsyncFunctionDef)
  }
  assert "_goodbye_message" in methods
  assert "_enter_lifespan" in methods
  registrations = [
    node
    for node in ast.walk(methods["_enter_lifespan"])
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and isinstance(node.func.value, ast.Name)
    and node.func.value.id == "stack"
    and node.func.attr == "push_shielded_async_callback"
  ]
  assert len(registrations) == 1
  assert len(registrations[0].args) == 1
  callback = registrations[0].args[0]
  assert (
    isinstance(callback, ast.Attribute)
    and isinstance(callback.value, ast.Name)
    and callback.value.id == "self"
    and callback.attr == "_goodbye_message"
  )


def test_connection_example_imports_anyio_and_registers_defined_goodbye() -> None:
  _assert_connection_example_valid(_connection_example_source())


@pytest.mark.parametrize(
  ("original", "broken"),
  [
    ("import anyio\n", ""),
    ("self._goodbye_message, shield_timeout", "self._goodbye_messagae, shield_timeout"),
  ],
  ids=["missing-anyio-import", "undefined-goodbye-callback"],
)
def test_structured_concurrency_example_check_rejects_broken_snippets(
  original: str, broken: str
) -> None:
  source = _connection_example_source()
  assert original in source
  with pytest.raises(AssertionError):
    _assert_connection_example_valid(source.replace(original, broken, 1))


def test_permanent_gate_import_does_not_load_notebooks(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  original_open = Path.open

  def reject_notebook_open(path: Path, *args: Any, **kwargs: Any) -> Any:
    assert path.suffix != ".ipynb", path
    return original_open(path, *args, **kwargs)

  module_name = "wave13_notebook_lifecycle_import_check"
  spec = importlib.util.spec_from_file_location(module_name, Path(__file__))
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  monkeypatch.setattr(Path, "open", reject_notebook_open)
  monkeypatch.setitem(sys.modules, module_name, module)
  spec.loader.exec_module(module)


@pytest.mark.parametrize(
  "path",
  [
    path
    for path, policy in LIFECYCLE_NOTEBOOKS.items()
    if policy.runtime in {"software-double-runnable", "loopback-only-runnable"}
  ],
)
def test_runnable_notebook_lifecycle_resources_use_only_software_backends(path: str) -> None:
  _validate_runnable_notebook_lifecycle_resources(path, load_notebook(path))


def _validate_runnable_notebook_lifecycle_resources(path: str, notebook: dict[str, Any]) -> None:
  assert STRUCTURAL_ONLY_HELPER_CELLS == {
    "docs/cookbook/slack_notifications.ipynb": ("45765d87", "d764ddbd", "f7e35d45"),
  }
  helper_ids = set(STRUCTURAL_ONLY_HELPER_CELLS.get(path, ()))
  assert helper_ids <= {cell["id"] for cell in notebook["cells"]}
  constructor_names: set[str] = set()
  backend_names: set[str] = set()
  backend_bindings = 0

  for index, cell in enumerate(notebook["cells"]):
    if cell["cell_type"] != "code" or cell["id"] in helper_ids:
      continue
    tree = code_tree(cell, path, _cell_reference(cell, index))

    for node in ast.walk(tree):
      if isinstance(node, ast.ImportFrom):
        assert node.module is not None, (path, cell["id"])
        for alias in node.names:
          assert alias.name != "*", (path, cell["id"], node.module)
          local_name = alias.asname or alias.name
          export = (node.module, alias.name)
          if alias.name.endswith("Backend") or local_name.endswith("Backend"):
            assert export in ALLOWED_RUNNABLE_BACKEND_EXPORTS, (
              path,
              cell["id"],
              export,
              local_name,
            )
          if export in ALLOWED_RUNNABLE_BACKEND_EXPORTS:
            constructor_names.add(local_name)
      elif isinstance(node, ast.Import):
        for alias in node.names:
          local_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
          assert not local_name.endswith("Backend"), (path, cell["id"], alias.name, local_name)

    shadowed_names = _definition_names(tree) & constructor_names
    assert shadowed_names == set(), (path, cell["id"], shadowed_names)

    assignment_values: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
      if isinstance(node, ast.Assign):
        for target in node.targets:
          if isinstance(target, ast.Name):
            assignment_values.setdefault(target.id, []).append(node.value)
      elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        if node.value is not None:
          assignment_values.setdefault(node.target.id, []).append(node.value)
    for name, values in assignment_values.items():
      if all(_is_allowed_backend_constructor(value, constructor_names) for value in values):
        backend_names.add(name)
      else:
        backend_names.discard(name)

    for node in ast.walk(tree):
      if not isinstance(node, ast.Call):
        continue
      constructor = dotted_name(node.func)
      if constructor is not None and constructor.endswith("Backend"):
        assert isinstance(node.func, ast.Name) and node.func.id in constructor_names, (
          path,
          cell["id"],
          constructor,
        )
      backend_keywords = [keyword.value for keyword in node.keywords if keyword.arg == "backend"]
      for backend in backend_keywords:
        backend_bindings += 1
        assert _is_allowed_backend_constructor(backend, constructor_names) or (
          isinstance(backend, ast.Name) and backend.id in backend_names
        ), (path, cell["id"])

  assert constructor_names, path
  assert backend_bindings > 0, path


@pytest.mark.parametrize(
  "source",
  [
    """from pylabrobot.liquid_handling import STARBackend as LiquidHandlerChatterboxBackend
backend = LiquidHandlerChatterboxBackend()
lh = LiquidHandler(backend=backend)
""",
    """import pylabrobot.liquid_handling.backends.hamilton as LiquidHandlerChatterboxBackend
backend = LiquidHandlerChatterboxBackend()
lh = LiquidHandler(backend=backend)
""",
    """from unknown.backends import LiquidHandlerChatterboxBackend
backend = LiquidHandlerChatterboxBackend()
lh = LiquidHandler(backend=backend)
""",
    """from pylabrobot.liquid_handling import *
backend = LiquidHandlerChatterboxBackend()
lh = LiquidHandler(backend=backend)
""",
    """from pylabrobot.liquid_handling import LiquidHandlerChatterboxBackend
class LiquidHandlerChatterboxBackend:
  pass
backend = LiquidHandlerChatterboxBackend()
lh = LiquidHandler(backend=backend)
""",
    """from pylabrobot.liquid_handling import LiquidHandlerChatterboxBackend, STARBackend as Hardware
LiquidHandlerChatterboxBackend = Hardware
backend = LiquidHandlerChatterboxBackend()
lh = LiquidHandler(backend=backend)
""",
  ],
  ids=[
    "hardware-class-hidden-by-allowed-local-name",
    "hardware-module-hidden-by-allowed-local-name",
    "unknown-module",
    "wildcard",
    "shadowed-by-class",
    "rebound-to-hardware",
  ],
)
def test_runnable_backend_validator_rejects_untrusted_symbol_resolution(source: str) -> None:
  notebook = {
    "cells": [{"cell_type": "code", "id": "adversarial-backend", "metadata": {}, "source": source}]
  }
  with pytest.raises(AssertionError):
    _validate_runnable_notebook_lifecycle_resources("adversarial.ipynb", notebook)


@pytest.mark.parametrize(
  ("path", "cell_id", "expected_source", "expected_imports", "expected_backend"),
  [
    (
      "docs/user_guide/machine-agnostic-features/logging-and-validation/validation.ipynb",
      "3fb4f9fcb286",
      """import pylabrobot
from pylabrobot.liquid_handling import LiquidHandler, STARBackend
from pylabrobot.resources.hamilton import STARDeck

backend = STARBackend()
lh = LiquidHandler(backend=backend, deck=STARDeck())""",
      (
        "import pylabrobot",
        "from pylabrobot.liquid_handling import LiquidHandler, STARBackend",
        "from pylabrobot.resources.hamilton import STARDeck",
      ),
      "STARBackend",
    ),
    (
      "docs/user_guide/00_liquid-handling/tutorial_tip_inventory_consolidation.ipynb",
      "de15ed3c-99da-4e4b-8501-a2471d63874a",
      """from pylabrobot.liquid_handling import LiquidHandler, LiquidHandlerChatterboxBackend
from pylabrobot.resources import STARDeck

backend = LiquidHandlerChatterboxBackend()
deck = STARDeck()""",
      (
        "from pylabrobot.liquid_handling import LiquidHandler, LiquidHandlerChatterboxBackend",
        "from pylabrobot.resources import STARDeck",
      ),
      "LiquidHandlerChatterboxBackend",
    ),
  ],
)
def test_notebooks_pin_expected_backend_source_and_constructor(
  path: str,
  cell_id: str,
  expected_source: str,
  expected_imports: tuple[str, ...],
  expected_backend: str,
) -> None:
  matching_cells = [cell for cell in load_notebook(path)["cells"] if cell.get("id") == cell_id]
  assert len(matching_cells) == 1
  source = matching_cells[0]["source"]
  if isinstance(source, list):
    source = "".join(source)
  assert source == expected_source
  tree = code_tree(matching_cells[0], path, cell_id)
  assert (
    tuple(ast.unparse(node) for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom)))
    == expected_imports
  )
  backend_assignments = [
    node
    for node in tree.body
    if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "backend" for target in node.targets)
  ]
  assert len(backend_assignments) == 1
  backend_value = backend_assignments[0].value
  assert isinstance(backend_value, ast.Call)
  assert dotted_name(backend_value.func) == expected_backend


def test_runtime_classifications_match_closed_inventory() -> None:
  assert len(EXPECTED_RUNTIME_CLASSIFICATIONS) == 55
  assert {
    path: policy.runtime for path, policy in LIFECYCLE_NOTEBOOKS.items()
  } == EXPECTED_RUNTIME_CLASSIFICATIONS


def test_task2_lifecycle_cell_ids_match_closed_inventory() -> None:
  deprecated_paths = {
    path for path, policy in LIFECYCLE_NOTEBOOKS.items() if policy.deprecated_cross_cell
  }
  assert set(TASK2_LIFECYCLE_CELL_IDS) == deprecated_paths
  for path, expected_cell_ids in TASK2_LIFECYCLE_CELL_IDS.items():
    assert _lifecycle_cell_ids(path) == expected_cell_ids, path
  for path, expected_cell_ids in TASK2_REQUIRED_NON_LIFECYCLE_CELL_IDS.items():
    actual_cell_ids = tuple(cell["id"] for cell in load_notebook(path)["cells"])
    assert all(actual_cell_ids.count(cell_id) == 1 for cell_id in expected_cell_ids), path


def test_lifecycle_policy_targets_match_current_ast_receivers() -> None:
  for path, policy in LIFECYCLE_NOTEBOOKS.items():
    assert _lifecycle_targets(path) == (policy.setup_targets, policy.stop_targets), path


def test_deprecated_interactive_bridges_have_exact_label() -> None:
  expected_labelled_cells = {
    (path, cell_id)
    for path, starts in DEPRECATED_INTERACTIVE_START_CELLS.items()
    for cell_id, _ in starts
  }
  labelled_cells: set[tuple[str, str]] = set()
  for path, policy in LIFECYCLE_NOTEBOOKS.items():
    assert (path in DEPRECATED_INTERACTIVE_START_CELLS) == policy.deprecated_cross_cell
    notebook = load_notebook(path)
    for index, cell in enumerate(notebook["cells"]):
      source = cell["source"]
      if isinstance(source, list):
        source = "".join(source)
      if cell["cell_type"] == "markdown" and source == DEPRECATED_INTERACTIVE_LABEL:
        assert index + 1 < len(notebook["cells"]), path
        setup_cell_id = notebook["cells"][index + 1]["id"]
        assert cell["id"] == f"{setup_cell_id}-compatibility", path
        labelled_cells.add((path, setup_cell_id))
    for cell_id, expected_targets in DEPRECATED_INTERACTIVE_START_CELLS.get(path, ()):
      matching_cells = [cell for cell in notebook["cells"] if cell.get("id") == cell_id]
      assert len(matching_cells) == 1, (path, cell_id)
      cell = matching_cells[0]
      assert cell["cell_type"] == "code", (path, cell_id)
      setup_calls = sorted(
        (
          node
          for node in _walk_nodes(code_tree(cell, path, cell_id))
          if isinstance(node, ast.Call)
          and isinstance(node.func, ast.Attribute)
          and node.func.attr == "setup"
        ),
        key=lambda node: (node.lineno, node.col_offset),
      )
      actual_targets = tuple(
        _simple_target(call.func.value, path, cell_id)
        for call in setup_calls
        if isinstance(call.func, ast.Attribute)
      )
      assert actual_targets == expected_targets, (path, cell_id)
  assert labelled_cells == expected_labelled_cells


def _assert_lifecycle_calls_balanced(path: str, calls: tuple[tuple[int, str, str], ...]) -> None:
  consumed_stops: set[int] = set()
  for setup_index, operation, setup_target in calls:
    if operation != "setup":
      continue
    matching_stop = next(
      (
        call_index
        for call_index, (stop_index, stop_operation, stop_target) in enumerate(calls)
        if call_index not in consumed_stops
        and stop_index > setup_index
        and stop_operation == "stop"
        and stop_target == setup_target
      ),
      None,
    )
    assert matching_stop is not None, (
      f"{path} setup target {setup_target} has no unused later matching stop"
    )
    consumed_stops.add(matching_stop)


def test_deprecated_interactive_setup_targets_have_later_matching_stops() -> None:
  for path, policy in LIFECYCLE_NOTEBOOKS.items():
    if not policy.deprecated_cross_cell:
      continue
    _assert_lifecycle_calls_balanced(path, _lifecycle_calls(path))


def test_deprecated_setup_stop_balance_rejects_duplicate_setup_single_stop() -> None:
  calls = (
    (1, "setup", "lh"),
    (2, "setup", "lh"),
    (3, "stop", "lh"),
  )
  with pytest.raises(AssertionError, match=r"setup target lh has no unused later matching stop"):
    _assert_lifecycle_calls_balanced("synthetic.ipynb", calls)


def test_deprecated_lifecycle_ordering_matches_closed_policy() -> None:
  for path, policy in LIFECYCLE_NOTEBOOKS.items():
    assert _lifecycle_targets(path) == (policy.setup_targets, policy.stop_targets), path


def test_nonselected_primary_protocol_cells_use_async_with_not_setup_stop() -> None:
  path = "docs/user_guide/machine-agnostic-features/logging-and-validation/validation.ipynb"
  cells = [cell for cell in load_notebook(path)["cells"] if cell.get("id") == "validation-protocol"]
  assert len(cells) == 1
  tree = code_tree(cells[0], path, "validation-protocol")
  scoped_lh_blocks = [
    node
    for node in _walk_nodes(tree)
    if isinstance(node, ast.AsyncWith)
    and len(node.items) == 1
    and isinstance(node.items[0].context_expr, ast.Name)
    and node.items[0].context_expr.id == "lh"
  ]
  assert len(scoped_lh_blocks) == 1
  scoped_operations = {
    node.func.attr
    for statement in scoped_lh_blocks[0].body
    for node in _walk_nodes(statement)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and isinstance(node.func.value, ast.Name)
    and node.func.value.id == "lh"
  }
  assert {"pick_up_tips", "return_tips"} <= scoped_operations
  assert not any(
    isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr in {"setup", "stop"}
    for node in _walk_nodes(tree)
  )


def test_hardware_hello_world_cells_are_structural_only() -> None:
  hardware_hello_worlds = {
    path for path in LIFECYCLE_NOTEBOOKS if path.endswith("/hello-world.ipynb")
  }
  assert hardware_hello_worlds
  assert {
    path for path in hardware_hello_worlds if LIFECYCLE_NOTEBOOKS[path].runtime != "structural-only"
  } == set()


def test_no_unexpected_setup_stop_notebooks() -> None:
  discovered_paths: set[str] = set()
  for path in _tracked_notebooks():
    notebook = load_notebook(path)
    for index, cell in enumerate(notebook["cells"]):
      if cell["cell_type"] != "code":
        continue
      tree = code_tree(cell, path, _cell_reference(cell, index))
      if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"setup", "stop"}
        for node in ast.walk(tree)
      ):
        discovered_paths.add(path)
  assert discovered_paths - set(LIFECYCLE_NOTEBOOKS) == set()


def test_lifecycle_notebooks_are_nbformat_4_5() -> None:
  for path in LIFECYCLE_NOTEBOOKS:
    notebook = load_notebook(path)
    assert (notebook["nbformat"], notebook["nbformat_minor"]) == (4, 5), path


def test_lifecycle_notebook_cells_have_unique_ids() -> None:
  for path in LIFECYCLE_NOTEBOOKS:
    cells = load_notebook(path)["cells"]
    cell_ids = [cell.get("id") for cell in cells]
    assert all(cell_ids), path
    assert len(cell_ids) == len(set(cell_ids)), path


def test_lifecycle_notebook_code_cells_parse_as_python() -> None:
  for path in LIFECYCLE_NOTEBOOKS:
    for index, cell in enumerate(load_notebook(path)["cells"]):
      if cell["cell_type"] == "code":
        code_tree(cell, path, _cell_reference(cell, index))


@pytest.mark.parametrize(
  ("path", "cell_reference", "index"),
  [
    ("docs/cookbook/star_movement_plate_to_alpaqua_core.ipynb", None, 9),
    (
      "docs/user_guide/00_liquid-handling/tutorial_tip_inventory_consolidation.ipynb",
      "695a4e86",
      None,
    ),
    (
      "docs/user_guide/machine-agnostic-features/logging-and-validation/validation.ipynb",
      None,
      9,
    ),
    ("docs/user_guide/agilent/vspin/hello-world.ipynb", "vspin-install-code", None),
  ],
)
def test_structural_parser_accepts_recognized_line_syntax(
  path: str, cell_reference: Optional[str], index: Optional[int]
) -> None:
  cells = load_notebook(path)["cells"]
  if index is not None:
    cell = cells[index]
    reference = f"index {index}"
  else:
    cell = next(cell for cell in cells if cell.get("id") == cell_reference)
    reference = str(cell_reference)
  code_tree(cell, path, reference)


def test_structural_parser_rejects_cell_magics() -> None:
  with pytest.raises(AssertionError, match=r"synthetic.ipynb cell unsafe line 1: cell magics"):
    code_tree(
      {"cell_type": "code", "source": "%%bash\nprintf unsafe"},
      "synthetic.ipynb",
      "unsafe",
    )


@pytest.mark.parametrize("source", ["%time value", "!curl https://example.invalid"])
def test_structural_parser_rejects_unrecognized_line_syntax(source: str) -> None:
  with pytest.raises(
    AssertionError, match=r"synthetic.ipynb cell unsafe line 1: unrecognized IPython syntax"
  ):
    code_tree({"cell_type": "code", "source": source}, "synthetic.ipynb", "unsafe")


def test_structural_validator_does_not_import_or_execute_notebook_code(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  def prohibited(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("structural validation attempted execution or external access")

  with monkeypatch.context() as context:
    context.setattr(builtins, "exec", prohibited)
    context.setattr(builtins, "eval", prohibited)
    context.setattr(socket, "socket", prohibited)
    context.setattr(urllib.request, "urlopen", prohibited)
    context.setattr(builtins, "__import__", prohibited)

    validated_paths: list[str] = []
    for path, policy in LIFECYCLE_NOTEBOOKS.items():
      calls = _lifecycle_calls(path)
      setup_targets = tuple(target for _, operation, target in calls if operation == "setup")
      stop_targets = tuple(target for _, operation, target in calls if operation == "stop")
      assert (setup_targets, stop_targets) == (policy.setup_targets, policy.stop_targets), path
      if policy.deprecated_cross_cell:
        _assert_lifecycle_calls_balanced(path, calls)
      validated_paths.append(path)
    for path, cell_ids in SELECTED_PROTOCOL_CELLS.items():
      for cell_id in cell_ids:
        validate_selected_runtime_cell(path, cell_id, _SELECTED_PROTOCOL_FUNCTIONS[(path, cell_id)])
    normalized = normalize_code_source("  %autoreload 2\nvalue = 1", "safe.ipynb", "safe")
    tree = code_tree({"cell_type": "code", "source": "value = 1"}, "safe.ipynb", "safe")

  assert validated_paths == list(LIFECYCLE_NOTEBOOKS)
  assert normalized == "  pass\nvalue = 1"
  assert isinstance(tree, ast.Module)


def test_selected_runtime_cells_have_allowlisted_imports_and_one_async_function() -> None:
  for path, cell_ids in SELECTED_PROTOCOL_CELLS.items():
    for cell_id in cell_ids:
      validate_selected_runtime_cell(path, cell_id, _SELECTED_PROTOCOL_FUNCTIONS[(path, cell_id)])


def test_selected_protocol_registry_is_complete() -> None:
  assert SELECTED_PROTOCOL_CELLS == {
    "docs/cookbook/manual_operator_jupyter.ipynb": ("manual-transfer-protocol",),
    "docs/cookbook/slack_notifications.ipynb": ("489da2a0",),
    "docs/user_guide/00_liquid-handling/mixing.ipynb": ("9bc0da6c",),
    "docs/user_guide/machine-agnostic-features/using-the-visualizer.ipynb": (
      "visualizer-loopback-protocol",
    ),
  }
  assert set(RUNTIME_CELL_IMPORTS) == {
    (path, cell_id) for path, cell_ids in SELECTED_PROTOCOL_CELLS.items() for cell_id in cell_ids
  }


def test_manual_selected_function_annotations_are_available_in_notebook_order() -> None:
  path = "docs/cookbook/manual_operator_jupyter.ipynb"
  cell_id = "manual-transfer-protocol"
  cells = load_notebook(path)["cells"]
  selected_index = next(index for index, cell in enumerate(cells) if cell.get("id") == cell_id)
  function = validate_selected_runtime_cell(path, cell_id, "run_manual_transfer_and_read")

  imported_names: set[str] = set()
  for index, cell in enumerate(cells[: selected_index + 1]):
    if cell["cell_type"] != "code":
      continue
    for statement in code_tree(cell, path, _cell_reference(cell, index)).body:
      if isinstance(statement, ast.Import):
        imported_names.update(alias.asname or alias.name.split(".")[0] for alias in statement.names)
      elif isinstance(statement, ast.ImportFrom):
        imported_names.update(alias.asname or alias.name for alias in statement.names)

  arguments = (
    *function.args.posonlyargs,
    *function.args.args,
    *function.args.kwonlyargs,
  )
  annotations = [argument.annotation for argument in arguments if argument.annotation is not None]
  if function.args.vararg is not None and function.args.vararg.annotation is not None:
    annotations.append(function.args.vararg.annotation)
  if function.args.kwarg is not None and function.args.kwarg.annotation is not None:
    annotations.append(function.args.kwarg.annotation)
  if function.returns is not None:
    annotations.append(function.returns)
  annotation_names = {
    node.id
    for annotation in annotations
    for node in _walk_nodes(annotation)
    if isinstance(node, ast.Name)
  }
  builtin_annotation_names = {
    "bool",
    "bytes",
    "dict",
    "float",
    "frozenset",
    "int",
    "list",
    "object",
    "set",
    "str",
    "tuple",
    "type",
  }

  assert annotation_names <= imported_names | builtin_annotation_names


def test_selected_runtime_cells_have_no_hardware_or_external_endpoint_literals() -> None:
  for path, cell_ids in SELECTED_PROTOCOL_CELLS.items():
    for cell_id in cell_ids:
      function = validate_selected_runtime_cell(
        path, cell_id, _SELECTED_PROTOCOL_FUNCTIONS[(path, cell_id)]
      )
      assert not _has_forbidden_endpoint_literal(function), (path, cell_id)
      assert not any(
        is_forbidden_runtime_call(node) for node in ast.walk(function) if isinstance(node, ast.Call)
      ), (path, cell_id)


def test_runtime_endpoint_guard_rejects_builtin_and_file_open_calls() -> None:
  tree = ast.parse(
    "open('value')\nbuiltins.open('value')\nio.open('value')\nPath('value').open()\n"
  )
  calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
  assert [is_forbidden_runtime_call(call) for call in calls] == [True, True, True, True, True]


def test_runtime_endpoint_guard_allows_device_open_methods() -> None:
  tree = ast.parse("await plate_reader.open()\nawait incubator.open_door()\n")
  calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
  assert not any(is_forbidden_runtime_call(call) for call in calls)


def test_mixing_notebook_has_one_scoped_invocation_without_orphan_operations() -> None:
  path = "docs/user_guide/00_liquid-handling/mixing.ipynb"
  selected_cell_id = "9bc0da6c"
  notebook = load_notebook(path)
  assert tuple(cell["id"] for cell in notebook["cells"]) == (
    "788f6f66",
    selected_cell_id,
    "1268dd30",
    "aabc816d",
  )

  nonselected_trees = [
    code_tree(cell, path, cell["id"])
    for cell in notebook["cells"]
    if cell["cell_type"] == "code" and cell["id"] != selected_cell_id
  ]
  assert len(nonselected_trees) == 1
  invocation = nonselected_trees[0]
  assert len(invocation.body) == 1
  statement = invocation.body[0]
  assert isinstance(statement, ast.Expr)
  assert isinstance(statement.value, ast.Await)
  call = statement.value.value
  assert isinstance(call, ast.Call)
  assert isinstance(call.func, ast.Name) and call.func.id == "run_chatterbox_mixing_protocol"
  assert call.args == [] and call.keywords == []

  forbidden_names = {"lh", "tr0", "plate"}
  forbidden_operations = {"setup", "stop", "pick_up_tips", "aspirate", "dispense", "return_tips"}
  for tree in nonselected_trees:
    assert not any(
      isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in forbidden_names
      for node in ast.walk(tree)
    )
    assert not any(
      isinstance(node, ast.Call)
      and isinstance(node.func, ast.Attribute)
      and node.func.attr in forbidden_operations
      for node in ast.walk(tree)
    )


def test_slack_event_bus_lifecycle_target_is_expected_local_chatterbox() -> None:
  path = "docs/cookbook/slack_notifications.ipynb"
  notebook = load_notebook(path)
  cell = next(cell for cell in notebook["cells"] if cell.get("id") == "778c381d")
  tree = code_tree(cell, path, "778c381d")

  imports = {
    node.module: tuple((alias.name, alias.asname) for alias in node.names)
    for node in tree.body
    if isinstance(node, ast.ImportFrom)
  }
  assert imports["pylabrobot.liquid_handling"] == (
    ("LiquidHandler", None),
    ("LiquidHandlerChatterboxBackend", None),
  )
  assert imports["pylabrobot.resources.hamilton"] == (("STARLetDeck", None),)

  assignments = [
    node
    for node in tree.body
    if isinstance(node, ast.Assign)
    and len(node.targets) == 1
    and isinstance(node.targets[0], ast.Name)
    and node.targets[0].id == "lh"
  ]
  assert len(assignments) == 1, "Slack EventBus cell must construct exactly one local lh"
  assignment = assignments[0]
  constructor = assignment.value
  assert isinstance(constructor, ast.Call)
  assert isinstance(constructor.func, ast.Name) and constructor.func.id == "LiquidHandler"
  assert constructor.args == []
  keywords = {keyword.arg: keyword.value for keyword in constructor.keywords}
  assert set(keywords) == {"backend", "deck"}
  for keyword, expected_constructor in (
    ("backend", "LiquidHandlerChatterboxBackend"),
    ("deck", "STARLetDeck"),
  ):
    value = keywords[keyword]
    assert isinstance(value, ast.Call)
    assert isinstance(value.func, ast.Name) and value.func.id == expected_constructor
    assert value.args == [] and value.keywords == []

  lifecycle_uses: list[tuple[str, int]] = []

  class ModuleLifecycleVisitor(ast.NodeVisitor):
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
      return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
      return

    def visit_Lambda(self, node: ast.Lambda) -> None:
      return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
      return

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
      for item in node.items:
        assert isinstance(item.context_expr, ast.Name), "Slack lifecycle target must be a name"
        lifecycle_uses.append((item.context_expr.id, node.lineno))
      self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
      if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"setup", "stop"}
        and isinstance(node.func.value, ast.Name)
      ):
        lifecycle_uses.append((node.func.value.id, node.lineno))
      self.generic_visit(node)

  ModuleLifecycleVisitor().visit(tree)
  assert lifecycle_uses, "Slack EventBus cell must own a lifecycle scope"
  for target, use_line in lifecycle_uses:
    assert target == "lh", f"unexpected Slack lifecycle target: {target}"
    assert assignment.lineno < use_line, "lh must be constructed before lifecycle use"
