import enum


class LiquidClass(enum.Enum):
  """ A liquid class. """

  ACETONITRIL80WATER20 = "Acetonitril/Water 80:20" # TODO: need a better way to represent this.
  WATER = "Water"
  ETHANOL = "Ethanol 100%"
  GLYCERIN = "Glycerin"
  GLYCERIN80 = "Glycerin 80%" # TODO: need a better way to represent this.
  DMSO = "DMSO"
  PLASMA = "Plasma 100%"
  SERUM = "Serum 100%"
  ACETONITRILE = "Acetonitrile 100%"
  DIMETHYLSULFOXID = "Dimethylsulfoxid 100%"
  BLOOD = "Blood"
  BRAINHOMOGENATE = "Brain-Homogenate"
  CHLOROFORM = "Chloroform 100%"
  METHANOL = "Methanol 100%"
  OCTANOL = "Octanol 100%"
  DNA_TRIS_EDTA = "100µg/ml DNA in Tris-EDTA Puffer" # TODO: need a better way to represent this.
  PBS_BUFFER = "PBS Buffer"
  METHANOL70WATER030 = "Methanol/Water 70:30" # TODO: need a better way to represent this.

  @staticmethod
  def from_str(s: str) -> "LiquidClass":
    """ Some liquid classes have more than one name. This is a little Hamilton specific, will
    probably refactor in the future. """

    s = s.strip()

    if s.endswith(" for aliquot"):
      s = s[:-len(" for aliquot")] # I don't think this is needed.

    if s in {"EtOH", "Ethanol", "EtOH 100%"}:
      s = "Ethanol 100%"
    elif s == "Serum":
      s = "Serum 100%"
    elif s in {"Acetonitril", "Acetonitril 100%", "Acetonitrile"}:
      s = "Acetonitrile 100%"
    elif s == "Blood (completely)":
      s = "Blood"
    elif s == "Glycerin80":
      s = "Glycerin 80%"
    elif s == "SysFlWater":
      s = "Water" # TODO: ?
    elif s == "Plasma":
      s = "Plasma 100%"

    return LiquidClass(s)
