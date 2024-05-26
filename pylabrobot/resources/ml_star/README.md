
## Resource defintions: "ML_STAR"

Company history: [Hamilton Robotics history](https://www.hamiltoncompany.com/history)

> Hamilton Robotics provides automated liquid handling workstations for the scientific community.  Our portfolio includes three liquid handling platforms, small devices, consumables, and OEM solutions.

---

### Currently defined tip carriers:

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'TIP_CAR_480_A00'<br>Part no.: 182085<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/182085) <br>Carrier for 5x 96 tip (10μl, 50μl, 300μl, 1000μl) racks or 5x 24 tip (5ml) racks (6T) | <img src="ims/TIP_CAR_480_A00_182085.jpg" alt="TIP_CAR_480_A00" width="250"/> | `TIP_CAR_480_A00` |

---

### Currently defined plate carriers:

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'PLT_CAR_L5AC_A00'<br>Part no.: 182090<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/182090) <br>Carrier for 5x 96 Deep Well Plates or for 5x 384 tip racks (e.g.384HEAD_384TIPS_50μl) (6T) | <img src="ims/PLT_CAR_L5AC_A00_182090.jpg" alt="PLT_CAR_L5AC_A00" width="250"/> | `PLT_CAR_L5AC_A00` |


---

### Currently defined MFX carriers:

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'MFX_CAR_L5_base'<br>Part no.: 188039<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/188039) <br>Labware carrier base for up to 5 Multiflex Modules | <img src="ims/MFX_CAR_L5_base_188039.jpg" alt="MFX_CAR_L5_base" width="250"/> | `MFX_CAR_L5_base` |



#### Currently defined MFX modules:

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'MFX_TIP_module'<br>Part no.: 188160 or 188040<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/188040) <br>Module to position a high-, standard-, low volume or 5ml tip rack (but not a 384 tip rack) | <img src="ims/MFX_TIP_module_188040.jpg" alt="MFX_TIP_module" width="250"/> | `MFX_TIP_module` |
| 'MFX_DWP_rackbased_module'<br>Part no.: 188229?<br>[manufacturer website](https://www.hamiltoncompany.com/automated-liquid-handling/other-robotics/188229) (<-non-functional link?) <br>MFX DWP module rack-based | <img src="ims/MFX_DWP_RB_module_188229_.jpg" alt="MFX_DWP_rackbased_module" width="250"/> | `MFX_DWP_rackbased_module` |

---
---
### Hamilton carrier naming guide

| Type of carrier | Construction | Orientation | Number of.. | Labware info | Revision |
| --------------- | ------------ | ----------- | ----------- | ------------ | -------- |
| **PLT**         | Plate carrier|              |             |              |          |
|                 | **CAR**      | Standard Carrier | | **L** Landscape | Plate positions: | **3** | **AC** Deepwell plate (Archive) | **A00, A01** |
|                 | **APE** Application engineering | | **P** Portrait | | **4** | **MD** Medium Density (96/384) | **A00, A01** |
|                 | **DAT** Deck adaptor template | | | | **5** | **HD** High Density (1536 well) | **A00, A01** |
| **SMP**         | Sample carrier | | | | |
|                 | **CAR** Standard | | | Tubes: | **12** | **15x75** Tube size | **A00, A01** |
|                 | | | | | **16** | | **A00, A01** |
|                 | | | | | **24** | | **A00, A01** |
|                 | | | | | **32** | | **A00, A01** |
| **TIP**         | Tip carrier | | | | |
|                 | **CAR** Standard Carrier | | **L** Landscape | 1000ul Channel: | **288** | **LT** Low volume 10ul | **A00, A01** |
|                 | | | **P** Portrait | | **384** | **50ul** 50ul Tip | **A00, A01** |
|                 | | | | | **480** | **ST** Standard vol. 300ul | **A00, A01** |
|                 | | | | | **5mlTips:** | **72** | **HT** High volume 1000ul Filter | **A00, A01** |
|                 | | | | | **96** | **5mlT** 5ml Tip Size | **A00, A01** |
|                 | | | | | **120** | | **A00, A01** |
|                 | | | | | **384 Head:** | **1920** | | **A00, A01** |
|                 | | | | | **BC** = Barcoded Tip rack | | **A00, A01** |
|                 | | | | | **NTR** = Nestable Tip Rack | | **A00, A01** |
| **RGT**         | Reagent | | | | |
|                 | **CAR** Standard | | | Reagent troughs: | **3, 4, 5** | **R** Reagent | **A00, A01** |
| **CTR**         | Control carrier | | | | **C** Controls | **A00, A01** |
| **VER**         | Verification | | | | | **A00, A01** |

