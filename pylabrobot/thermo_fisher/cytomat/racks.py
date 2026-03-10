from pylabrobot.resources import Coordinate
from pylabrobot.resources.carrier import PlateCarrier, PlateHolder


def _cytomat_rack(name: str, site_height: float, num_sites: int, model: str):
  start = 17.6  # roughly measured, not important right now
  return PlateCarrier(
    name=name,
    size_x=109,  # roughly measured, not important right now
    size_y=142,  # roughly measured, not important right now
    size_z=541,  # roughly measured, not important right now
    sites={
      i: PlateHolder(
        size_x=85.48,
        size_y=127.27,
        # the last site is always 50mm or taller.
        size_z=max(site_height, 50) if i == num_sites - 1 else site_height,
        name=f"{name}-{i + 1}",
        pedestal_size_z=0,
      ).at(
        Coordinate(
          x=11.76,  # estimate
          y=0,  # estimate
          z=start + site_height * i,
        )
      )
      for i in range(num_sites)
    },
    model=model,
  )


def cytomat_rack_9mm_51(name: str):
  return _cytomat_rack(name=name, site_height=9, num_sites=51, model="cytomat_rack_9mm_51")


def cytomat_rack_10mm_47(name: str):
  return _cytomat_rack(name=name, site_height=10, num_sites=47, model="cytomat_rack_10mm_47")


def cytomat_rack_17mm_28(name: str):
  return _cytomat_rack(name=name, site_height=17, num_sites=28, model="cytomat_rack_17mm_28")


def cytomat_rack_18mm_26(name: str):
  return _cytomat_rack(name=name, site_height=18, num_sites=26, model="cytomat_rack_18mm_26")


def cytomat_rack_23mm_21(name: str):
  return _cytomat_rack(name=name, site_height=23, num_sites=21, model="cytomat_rack_23mm_21")


def cytomat_rack_26mm_18(name: str):
  return _cytomat_rack(name=name, site_height=26, num_sites=18, model="cytomat_rack_26mm_18")


def cytomat_rack_28mm_17(name: str):
  return _cytomat_rack(name=name, site_height=28, num_sites=17, model="cytomat_rack_28mm_17")


def cytomat_rack_29mm_16(name: str):
  return _cytomat_rack(name=name, site_height=29, num_sites=16, model="cytomat_rack_29mm_16")


def cytomat_rack_33mm_15(name: str):
  return _cytomat_rack(name=name, site_height=33, num_sites=15, model="cytomat_rack_33mm_15")


def cytomat_rack_35mm_14(name: str):
  return _cytomat_rack(name=name, site_height=35, num_sites=14, model="cytomat_rack_35mm_14")


def cytomat_rack_38mm_13(name: str):
  return _cytomat_rack(name=name, site_height=38, num_sites=13, model="cytomat_rack_38mm_13")


def cytomat_rack_43mm_11(name: str):
  return _cytomat_rack(name=name, site_height=43, num_sites=11, model="cytomat_rack_43mm_11")


def cytomat_rack_45p5mm_11(name: str):
  return _cytomat_rack(name=name, site_height=45.5, num_sites=11, model="cytomat_rack_45.5mm_11")


def cytomat_rack_50mm_10(name: str):
  return _cytomat_rack(name=name, site_height=50, num_sites=10, model="cytomat_rack_50mm_10")


def cytomat_rack_57mm_9(name: str):
  return _cytomat_rack(name=name, site_height=57, num_sites=9, model="cytomat_rack_57mm_9")


def cytomat_rack_60mm_8(name: str):
  return _cytomat_rack(name=name, site_height=60, num_sites=8, model="cytomat_rack_60mm_8")


def cytomat_rack_69mm_7(name: str):
  return _cytomat_rack(name=name, site_height=69, num_sites=7, model="cytomat_rack_69mm_7")


def cytomat_rack_95mm_5(name: str):
  return _cytomat_rack(name=name, site_height=95, num_sites=5, model="cytomat_rack_95mm_5")
