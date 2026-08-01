from __future__ import annotations

from pylabrobot.agilent.biotek.cytation.base import _CytationBase


class Cytation1(_CytationBase):
  """Agilent BioTek Cytation 1. No camera; imaging lives on the Cytation 5."""

  _model_name = "Agilent BioTek Cytation 1"
