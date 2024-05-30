from pylabrobot.resources.container import Container


def StanleyCup_QUENCHER_FLOWSTATE_TUMBLER(name: str) -> Container:
  """ QUENCHER H2.0 FLOWSTATE (TM) TUMBLER

  https://www.stanley1913.com/products/adventure-quencher-travel-tumbler-40-oz
  """

  MM_PER_INCH = 25.4
  ML_PER_OZ = 29.5735

  return Container(
    name=name,
    size_x=3.86 * MM_PER_INCH,
    size_y=3.86 * MM_PER_INCH,
    size_z=12.3 * MM_PER_INCH,
    max_volume=40 * ML_PER_OZ,
    category="cups"
  )
