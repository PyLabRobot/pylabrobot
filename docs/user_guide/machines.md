# Supported Machines

```{raw} html
<style>
tr > td:first-child { width: 15%; }
tr > td:nth-child(2) { width: 15%; }
tr > td:nth-child(3) { width: 20%; }
tr > td:nth-child(4) { width: 15%; }
tr > td:nth-child(5) { width: 15%; }
.badge {
  border-radius: 8px;
  padding: 2px 8px;
  display: inline-block;
  font-size: 90%;
  margin-right: 4px;
  color: black;
}
.badge-liquid { background: #d0eaff; }
.badge-transfer { background: #f0d0ff; }
.badge-centrifuge { background: #e6e6fa; }
.badge-heating { background: #ffd6d6; }
.badge-cooling { background: #d6f1ff; }
.badge-shaking { background: #ddffdd; }
.badge-thermo { background: #fff4cc; }
.badge-sealing { background: #ffe0b3; }
.badge-weight { background: #f0f0f0; }
.badge-reading { background: #d6eaff; }
.badge-cyto { background: #f5ccff; }
.badge-air { background: #e6f7ff; }
.badge-tilt { background: #e0f7da; }
.badge-storage { background: #b2e0e0; }
.badge-microscopy { background: #e0cfff; }
</style>
```

## Liquid Handling

### Liquid Handling Workstations

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Hamilton | STAR(let) | <span class="badge badge-transfer">arm</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/00_liquid-handling/hamilton-star/_hamilton-star.html) / [OEM](https://www.hamiltoncompany.com/microlab-star) |
| Hamilton | Vantage | <span class="badge badge-transfer">arm</span> | Mostly | [PLR](https://docs.pylabrobot.org/user_guide/00_liquid-handling/hamilton-vantage/_hamilton-vantage.html) / [OEM](https://www.hamiltoncompany.com/microlab-vantage) |
| Hamilton | Prep | <span class="badge badge-transfer">arm</span> | WIP | PLR / [OEM](https://www.hamiltoncompany.com/microlab-prep) |
| Tecan | EVO |  | Basic | [PLR](https://docs.pylabrobot.org/user_guide/00_liquid-handling/tecan-evo/_tecan-evo.html) / [OEM](https://lifesciences.tecan.com/freedom-evo-platform) |
| Opentrons | OT-2 |  | Mostly | [PLR](https://docs.pylabrobot.org/user_guide/00_liquid-handling/opentrons-ot2/_opentrons-ot2.html) / [OEM](https://opentrons.com/products/ot-2-robot) |

### Pumps

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Cole Parmer | Masterflex L/S 07522-20 07522-30 07551-20 07551-30 07575-30 07575-40 | Full | [PLR](https://docs.pylabrobot.org/user_guide/00_liquid-handling/pumps/cole-parmer-masterflex.html) / [OEM](https://www.masterflex.nl/assets/uploads/2017/09/07551-xx.pdf) |
| Agrowtek | Pump Array | | Full | PLR / OEM |

---

## Material Handling

### Arms

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Brooks Automation | PreciseFlex 400 | WIP | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/arms/c_scara/precise-flex-pf400/_precise-flex-pf400.html) / [OEM](https://www.brooks.com/laboratory-automation/collaborative-robots/preciseflex-400/) |

### Centrifuges

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Agilent | VSpin | Mostly | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/centrifuge/agilent_vspin.html) / [OEM](https://www.agilent.com/en/product/automated-liquid-handling/automated-microplate-management/microplate-centrifuge) |
| Agilent | VSpin Access2 Loader | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/centrifuge/agilent_vspin.html#loader) / [OEM](https://www.agilent.com/en/product/automated-liquid-handling/automated-microplate-management/microplate-centrifuge) |

### Fans

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Hamilton | HEPA Fan | <span class="badge badge-air">air filtration</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/fans/fans.html) |

### Heater Shakers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Inheco | Thermoshake RM | <span class="badge badge-heating">heating</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/heating_shaking/inheco.html) / [OEM](https://www.inheco.com/thermoshake-classic.html) |
| Inheco | Thermoshake | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">Active cooling</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/heating_shaking/inheco.html) / [OEM](https://www.inheco.com/thermoshake.html) |
| Inheco | Thermoshake AC | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">Active cooling</span> | Mostly | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/heating_shaking/inheco.html) / [OEM](https://www.inheco.com/thermoshake-ac.html) |
| Opentrons | Thermoshake | <span class="badge badge-heating">heating</span> | Full | [OEM](https://opentrons.com/products/heater-shaker-module) |
| Hamilton | Heater Shaker | <span class="badge badge-heating">heating</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/heating_shaking/hamilton.html) / [OEM](https://www.hamiltoncompany.com/temperature-control/hamilton-heater-shaker) |

### Incubators

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Thermo Fisher Scientific | Cytomat C6000 | <span class="badge badge-heating">heating</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/incubators/cytomat.html) / OEM |
| Thermo Fisher Scientific | Cytomat C6002 | <span class="badge badge-heating">heating</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/incubators/cytomat.html) / OEM |
| Thermo Fisher Scientific | Cytomat C2C_50 | <span class="badge badge-heating">heating</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/incubators/cytomat.html) / OEM |
| Thermo Fisher Scientific | Cytomat C2C_425 | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">cooling</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/incubators/cytomat.html) / OEM |
| Thermo Fisher Scientific | Cytomat C2C_450_SHAKE | <span class="badge badge-heating">heating</span><span class="badge badge-shaking">shaking</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/incubators/cytomat.html) / OEM |
| Thermo Fisher Scientific | Cytomat 5C | <span class="badge badge-heating">heating</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/incubators/cytomat.html) / OEM |
| Thermo/Liconic | Heraeus Cytomat | <span class="badge badge-heating">heating</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/incubators/cytomat.html) / OEM |
| Inheco | Incubator Shaker MTP & DWP | <span class="badge badge-heating">heating</span><span class="badge badge-shaking">shaking</span> | WIP | PLR / OEM |

### Peelers

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Azenta Life Sciences | XPeel | WIP | PLR / OEM |

### Sealers

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Azenta Life Sciences | a4S Sealer | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/sealers/a4s.html) / [OEM](https://www.azenta.com/products/automated-roll-heat-sealer-formerly-a4s) |

### Thermocyclers

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Opentrons | Thermocycler | Full | PLR / [OEM](https://opentrons.com/products/thermocycler-module-1) |
| Thermo Fisher Scientific | ATC | WIP | PLR / [OEM](https://www.thermofisher.com/us/en/home/life-science/pcr/thermal-cyclers-realtime-instruments/thermal-cyclers/automated-thermal-cycler-atc.html) |
| Thermo Fisher Scientific | Proflex | WIP | PLR / [OEM](https://www.thermofisher.com/us/en/home/life-science/pcr/thermal-cyclers-realtime-instruments/thermal-cyclers/proflex-pcr-system.html) |
| Inheco | ODTC | WIP | PLR / [OEM](https://www.inheco.com/odtc.html) |

### Temperature Controllers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Opentrons | Temperature Module | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">cooling</span> | Mostly | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/temperature.html) / [OEM](https://opentrons.com/products/temperature-module-gen2) |
| Inheco | CPAC | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">active cooling</span> | WIP | PLR / [OEM](https://www.inheco.com/cpac.html) |

### Tilting

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Hamilton | Tilt Module | Full | [PLR](https://docs.pylabrobot.org/user_guide/01_material-handling/tilting.html) / [OEM](https://www.hamiltoncompany.com/other-robotics/188061) |

---

## Analytical Machines

### Plate Readers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| BMG Labtech | CLARIOstar | <span class="badge">Absorbance</span><span class="badge">Fluorescence</span><span class="badge">Luminescence</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/02_analytical/plate-reading/bmg-clariostar.html) / [OEM](https://www.bmglabtech.com/en/clariostar-plus/) |
| Agilent (BioTek) | Cytation 1 | <span class="badge">Absorbance</span><span class="badge">Fluorescence</span><span class="badge">Luminescence</span><span class="badge badge-microscopy">microscopy</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/02_analytical/plate-reading/cytation5.html) / [OEM](https://www.agilent.com/en/product/cell-analysis/cell-imaging-microscopy/cell-imaging-multimode-readers/biotek-cytation-1-cell-imaging-multimode-reader-1623200) |
| Agilent (BioTek) | Cytation 5 | <span class="badge">Absorbance</span><span class="badge">Fluorescence</span><span class="badge">Luminescence</span><span class="badge badge-microscopy">microscopy</span> | Full | [PLR](https://docs.pylabrobot.org/user_guide/02_analytical/plate-reading/cytation5.html) / [OEM](https://www.agilent.com/en/product/cell-analysis/cell-imaging-microscopy/cell-imaging-multimode-readers/biotek-cytation-5-cell-imaging-multimode-reader-1623202) |
| Byonoy | Absorbance 96 Automate | <span class="badge">Absorbance</span> | WIP | PLR / [OEM](https://byonoy.com/absorbance-96-automate/) |
| Byonoy | Luminescence 96 Automate | <span class="badge">Luminescence</span> | WIP | PLR / [OEM](https://byonoy.com/luminescence-96-automate/) |

### Flow Cytometers

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Beckman Coulter | CytoFLEX S | WIP | PLR / [OEM](https://www.beckman.com/flow-cytometry/research-flow-cytometers/cytoflex-s) |

### qPCR Machines

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Thermo Fisher Scientific | QuantStudio 5 | WIP | PLR / [OEM](https://www.thermofisher.com/order/catalog/product/A34322) |

### Scales

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Mettler Toledo | WXS205SDU | Full | [PLR](https://docs.pylabrobot.org/user_guide/02_analytical/scales.html#mettler-toledo-wxs205sdu) / [OEM](https://www.mt.com/us/en/home/products/Industrial_Weighing_Solutions/high-precision-weigh-sensors/weigh-module-wxs205sdu-15-11121008.html) |

---

## Understanding the Tables

Classifying lab automation equipment can be challenging, as many machines have overlapping capabilities and different user groups require varying levels of software integration.  
PyLabRobot aims to provide access to all hardware-firmware capabilities available on integrated equipment, even beyond OEM software. This allows users to selectively utilize the functionalities they require.

The machines are organized into three PyLabRobot categories:

- **Liquid handling**: Machines directly manipulating liquids.
- **Material handling**: Machines handling materials other than liquids.
- **Analytical**: Machines primarily responsible for performing measurements.

**Table Columns Explained:**

- **Features**: Core capabilities provided by the machine.
- **PLR-Support**: Indicates PyLabRobot integration status:
  - **WIP**: Work in progress.
  - **Basics**: Core functionalities available.
  Available on `pylabrobot:main` and has documentation pages.
  - **Mostly**: Most capabilities available but incomplete.
  - **Full**: Comprehensive capabilities (≥90%) fully supported.
