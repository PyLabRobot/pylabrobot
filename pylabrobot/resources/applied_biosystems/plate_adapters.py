"""Applied Biosystems plate adapters"""

from pylabrobot.resources.plate_adapter import PlateAdapter


def AppliedBiosystems_96_Well_Base(name: str) -> PlateAdapter:
  """
  Applied Biosystems™ MicroAmp™ Splash-Free 96-Well Base
  Item No.: 4312063
  Spec: https://assets.fishersci.com/TFS-Assets/LSG/manuals/cms_042431.pdf
  """

  return PlateAdapter(
    name=name,
    size_x=127.76,  # from spec
    size_y=85.85,  # from spec
    size_z=22.86,  # from spec
    dx=10.25,  # from spec
    dy=7.34,  # from spec
    dz=0,  # from spec, just an open hole to the deck
    adapter_hole_size_x=8.0,  # from spec
    adapter_hole_size_y=8.0,  # from spec
    adapter_hole_size_z=22.86,  # from spec, just an open hole to the deck
    model="AppliedBiosystems_96_Well_Base",
  )
