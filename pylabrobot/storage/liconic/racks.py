from pylabrobot.resources import Coordinate
from pylabrobot.resources.carrier import PlateCarrier, PlateHolder
from typing import Optional

def _liconic_rack(name: str,
                  pitch: int,
                  site_height: int,
                  num_sites: int,
                  model: str,
                  total_height: Optional[int] = 505, # 645 and 1210 for STX 500 and STX1000 only
                  bicarousel: Optional[bool] = False # for STX500 and STX1000 only
                  ):
  start = 17.2 # rough height of first plate position
  pitch=pitch,
  bicarousel = bicarousel,
  return PlateCarrier(
    name=name,
    size_x=109, # based off cytomat rack dimensions roughly the same
    size_y=142,
    size_z= total_height,
    sites={
      i: PlateHolder(
        size_x=85.48,
        size_y=127.27,
        # estimates
        size_z=max(site_height, total_height - site_height) if i == num_sites - 1 else site_height,
        name=f"{name} - {i + 1}",
        pedestal_size_z=0,
      ).at(
        Coordinate(
          x=11.76, #estimate
          y=0,
          z=start + site_height * i,
        )
      )
      for i in range(num_sites)
    },
    model=model,
  )

def liconic_rack_5mm_42(name: str):
  return _liconic_rack(name=name, pitch=11, site_height=5, num_sites=42, model="liconic_rack_5mm_42")

def liconic_rack_5mm_55(name: str):
  return _liconic_rack(name=name, pitch=11, site_height=5, num_sites=55, model="liconic_rack_5mm_55", total_height=645, bicarousel=True)

def liconic_rack_5mm_111(name: str):
  return _liconic_rack(name=name, pitch=11, site_height=5, num_sites=111, model="liconic_rack_5mm_111", total_height=1210, bicarousel=True)

def liconic_rack_11mm_28(name: str):
  return _liconic_rack(name=name, pitch=17, site_height=11, num_sites=28, model="liconic_rack_5mm_28")

def liconic_rack_11mm_37(name: str):
  return _liconic_rack(name=name, pitch=17, site_height=11, num_sites=37, model="liconic_rack_5mm_37", total_height=645, bicarousel=True)

def liconic_rack_11mm_72(name: str):
  return _liconic_rack(name=name, pitch=17, site_height=11, num_sites=72, model="liconic_rack_5mm_72", total_height=1210, bicarousel=True)

def liconic_rack_12mm_27(name: str):
  return _liconic_rack(name=name, pitch=18, site_height=12, num_sites=27, model="liconic_rack_5mm_27")

def liconic_rack_12mm_35(name: str):
  return _liconic_rack(name=name, pitch=18, site_height=12, num_sites=35, model="liconic_rack_5mm_35", total_height=645, bicarousel=True)

def liconic_rack_12mm_68(name: str):
  return _liconic_rack(name=name, pitch=18, site_height=12, num_sites=68, model="liconic_rack_5mm_68", total_height=1210, bicarousel=True)

def liconic_rack_17mm_22(name: str):
  return _liconic_rack(name=name, pitch=23, site_height=17, num_sites=22, model="liconic_rack_5mm_22")

def liconic_rack_17mm_28(name: str):
  return _liconic_rack(name=name, pitch=23, site_height=17, num_sites=28, model="liconic_rack_5mm_28", total_height=645, bicarousel=True)

def liconic_rack_17mm_53(name: str):
  return _liconic_rack(name=name, pitch=23, site_height=17, num_sites=53, model="liconic_rack_5mm_53", total_height=1210, bicarousel=True)

def liconic_rack_22mm_17(name: str):
  return _liconic_rack(name=name, pitch=28, site_height=22, num_sites=17, model="liconic_rack_22mm_17")

def liconic_rack_22mm_23(name: str):
  return _liconic_rack(name=name, pitch=28, site_height=22, num_sites=23, model="liconic_rack_22mm_23", total_height=645, bicarousel=True)

def liconic_rack_22mm_43(name: str):
  return _liconic_rack(name=name, pitch=28, site_height=22, num_sites=43, model="liconic_rack_22mm_43", total_height=1210, bicarousel=True)

def liconic_rack_23mm_17(name: str):
  return _liconic_rack(name=name, pitch=29, site_height=23, num_sites=17, model="liconic_rack_23mm_17")

def liconic_rack_23mm_22(name: str):
  return _liconic_rack(name=name, pitch=29, site_height=23, num_sites=22, model="liconic_rack_23mm_22", total_height=645, bicarousel=True)

def liconic_rack_23mm_42(name: str):
  return _liconic_rack(name=name, pitch=29, site_height=23, num_sites=42, model="liconic_rack_23mm_42", total_height=1210, bicarousel=True)

def liconic_rack_24mm_17(name: str):
  return _liconic_rack(name=name, pitch=30, site_height=24, num_sites=17, model="liconic_rack_24mm_17")

def liconic_rack_24mm_21(name: str):
  return _liconic_rack(name=name, pitch=30, site_height=24, num_sites=21, model="liconic_rack_24mm_21", total_height=645, bicarousel=True)

def liconic_rack_24mm_41(name: str):
  return _liconic_rack(name=name, pitch=30, site_height=24, num_sites=41, model="liconic_rack_24mm_41", total_height=1210, bicarousel=True)

def liconic_rack_27mm_15(name: str):
  return _liconic_rack(name=name, pitch=33, site_height=27, num_sites=15, model="liconic_rack_27mm_15")

def liconic_rack_27mm_19(name: str):
  return _liconic_rack(name=name, pitch=33, site_height=27, num_sites=19, model="liconic_rack_27mm_19", total_height=645, bicarousel=True)

def liconic_rack_27mm_37(name: str):
  return _liconic_rack(name=name, pitch=33, site_height=27, num_sites=37, model="liconic_rack_27mm_37", total_height=1210, bicarousel=True)

def liconic_rack_44mm_10(name: str):
  return _liconic_rack(name=name, pitch=50, site_height=44, num_sites=10, model="liconic_rack_44mm_10")

def liconic_rack_44mm_13(name: str):
  return _liconic_rack(name=name, pitch=50, site_height=44, num_sites=13, model="liconic_rack_44mm_13", total_height=645, bicarousel=True)

def liconic_rack_44mm_25(name: str):
  return _liconic_rack(name=name, pitch=50, site_height=44, num_sites=25, model="liconic_rack_44mm_25", total_height=1210, bicarousel=True)

def liconic_rack_53mm_8(name: str):
  return _liconic_rack(name=name, pitch=59, site_height=53, num_sites=8, model="liconic_rack_53mm_8")

def liconic_rack_53mm_10(name: str):
  return _liconic_rack(name=name, pitch=59, site_height=53, num_sites=10, model="liconic_rack_53mm_10", total_height=645, bicarousel=True)

def liconic_rack_53mm_21(name: str):
  return _liconic_rack(name=name, pitch=59, site_height=53, num_sites=21, model="liconic_rack_53mm_21", total_height=1210, bicarousel=True)

def liconic_rack_66mm_7(name: str):
  return _liconic_rack(name=name, pitch=72, site_height=66, num_sites=7, model="liconic_rack_66mm_7")

def liconic_rack_66mm_8(name: str):
  return _liconic_rack(name=name, pitch=72, site_height=66, num_sites=8, model="liconic_rack_66mm_8", total_height=645, bicarousel=True)

def liconic_rack_66mm_17(name: str):
  return _liconic_rack(name=name, pitch=72, site_height=66, num_sites=17, model="liconic_rack_66mm_17", total_height=1210, bicarousel=True)

def liconic_rack_104mm_4(name: str):
  return _liconic_rack(name=name, pitch=110, site_height=104, num_sites=4, model="liconic_rack_104mm_4")

def liconic_rack_104mm_5(name: str):
  return _liconic_rack(name=name, pitch=110, site_height=104, num_sites=5, model="liconic_rack_104mm_5", total_height=645, bicarousel=True)

def liconic_rack_104mm_11(name: str):
  return _liconic_rack(name=name, pitch=110, site_height=104, num_sites=11, model="liconic_rack_104mm_11", total_height=1210, bicarousel=True)
