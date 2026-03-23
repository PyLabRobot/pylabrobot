from .thermocycler import ThermoFisherThermocycler


def ProFlexSingleBlock(name: str, ip: str, **kwargs) -> ThermoFisherThermocycler:
  """Create a ProFlex with a single 6-zone block (BID 12)."""
  return ThermoFisherThermocycler(name=name, ip=ip, num_blocks=1, num_temp_zones=6, **kwargs)


def ProFlexThreeBlock(name: str, ip: str, **kwargs) -> ThermoFisherThermocycler:
  """Create a ProFlex with three 2-zone blocks (BID 13)."""
  return ThermoFisherThermocycler(name=name, ip=ip, num_blocks=3, num_temp_zones=2, **kwargs)
