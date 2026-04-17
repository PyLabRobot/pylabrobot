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
.badge-absorbance { background: #ffe6cc; }
.badge-fluorescence { background: #e0ffcc; }
.badge-luminescence { background: #e6e6ff; }
.badge-time-resolved-fluo { background: #ddffdd; }
.badge-fluo-polarization { background: #ddf2ffff; }
</style>
```


## Liquid Handling

### Liquid Handling Workstations

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Hamilton | STAR(let) | <span class="badge badge-transfer">arm</span> | Full | [PLR](00_liquid-handling/hamilton-star/_hamilton-star.rst) / [OEM](https://www.hamiltoncompany.com/microlab-star) |
| Hamilton | Vantage | <span class="badge badge-transfer">arm</span> | Mostly | [PLR](00_liquid-handling/hamilton-vantage/_hamilton-vantage.rst) / [OEM](https://www.hamiltoncompany.com/microlab-vantage) |
| Hamilton | Prep | | WIP | [OEM](https://www.hamiltoncompany.com/microlab-prep) |
| Hamilton | Nimbus | <span class="badge badge-transfer">arm</span> | Mostly | [OEM](https://www.hamiltoncompany.com/microlab-nimbus) |
| Tecan | EVO | <span class="badge badge-transfer">arm</span> | Basic | [PLR](00_liquid-handling/tecan-evo/_tecan-evo.rst) / [OEM](https://lifesciences.tecan.com/freedom-evo-platform) |
| Opentrons | OT-2 |  | Mostly | [PLR](00_liquid-handling/opentrons/ot2/hello-world.ipynb) / [OEM](https://opentrons.com/products/ot-2-robot) |

### Pumps

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Cole Parmer | Masterflex L/S 07522-20 07522-30 07551-20 07551-30 07575-30 07575-40 | Full | [PLR](00_liquid-handling/pumps/cole-parmer-masterflex.md) / [OEM](https://www.masterflex.nl/assets/uploads/2017/09/07551-xx.pdf) |
| Agrowtek | Pump Array | Full | [OEM](https://www.agrowtek.com/index.php/products/dosing_systems/dosing-pumps/agrowdose-adi-digital-persitaltic-dosing-pumps-detail) |

### Plate Washers

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Agilent (BioTek) | EL406 | Mostly | [PLR](00_liquid-handling/plate-washing/biotek-el406.ipynb) / [OEM](https://www.agilent.com/en/product/microplate-instrumentation/microplate-washers-dispensers/biotek-el406-washer-dispenser-795212) |
| Agilent (BioTek) | EL405 | Mostly | [OEM](https://www.agilent.com/en/product/microplate-instrumentation/microplate-washers-dispensers/biotek-elx405-select-deep-well-microplate-washer-1623186) |

### Bulk Dispensers

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Thermo Fisher Scientific | Multidrop Combi | Mostly (v1b1) | [OEM](https://www.thermofisher.com/order/catalog/product/5840300) |
| Formulatrix | Mantis | WIP | [OEM](https://formulatrix.com/liquid-handling-systems/mantis-liquid-handler/) |

---

## Material Handling

### Arms

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Brooks Automation | PreciseFlex PF400 |  Full | [PLR](01_material-handling/arms/c_scara/precise-flex-pf400/hello-world.ipynb) / [OEM](https://www.brooks.com/laboratory-automation/collaborative-robots/preciseflex-400/) |
| Brooks Automation | PreciseFlex PF3400 |  Full | [PLR](01_material-handling/arms/c_scara/precise-flex-pf400/hello-world.ipynb) / [OEM](https://www.brooks.com/laboratory-automation/collaborative-robots/preciseflex-400/) |
| PAA | KX2 | Mostly | |
| UFactory | xArm 6 | Basics (v1b1) | [OEM](https://www.ufactory.cc/xarm-collaborative-robot/) |

### Centrifuges

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Agilent | VSpin | Mostly | [PLR](01_material-handling/centrifuge/agilent_vspin.ipynb) / [OEM](https://www.agilent.com/en/product/automated-liquid-handling/automated-microplate-management/microplate-centrifuge) |
| Agilent | VSpin Access2 Loader | Full | [PLR](01_material-handling/centrifuge/agilent_vspin.ipynb#loader) / [OEM](https://www.agilent.com/en/product/automated-liquid-handling/automated-microplate-management/microplate-centrifuge) |

### Fans

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Hamilton | HEPA Fan | <span class="badge badge-air">air filtration</span> | Full | [PLR](01_material-handling/fans/fans.md) |

### Heater Shakers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Inheco | Thermoshake RM | <span class="badge badge-heating">heating</span> | Full | [PLR](01_material-handling/heating_shaking/inheco.ipynb) / [OEM](https://www.inheco.com/thermoshake-classic.html) |
| Inheco | Thermoshake | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">active cooling</span> | Full | [PLR](01_material-handling/heating_shaking/inheco.ipynb) / [OEM](https://www.inheco.com/thermoshake.html) |
| Inheco | Thermoshake AC | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">active cooling</span> | Full | [PLR](01_material-handling/heating_shaking/inheco.ipynb) / [OEM](https://www.inheco.com/thermoshake-ac.html) |
| Opentrons | Thermoshake | <span class="badge badge-heating">heating</span> | Full | [OEM](https://opentrons.com/products/heater-shaker-module) |
| Hamilton | Heater Shaker | <span class="badge badge-heating">heating</span> | Full | [PLR](01_material-handling/heating_shaking/hamilton.ipynb) / [OEM](https://www.hamiltoncompany.com/temperature-control/hamilton-heater-shaker) |
| QInstruments | BioShake | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">active cooling</span> | Full | [PLR](01_material-handling/heating_shaking/qinstruments.ipynb) / [OEM](https://www.qinstruments.com/automation/) |

### Storage

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Thermo Fisher Scientific | Cytomat 6000 | <span class="badge badge-heating">heating</span> | Full | [PLR](01_material-handling/storage/cytomat.ipynb) / [OEM](https://assets.thermofisher.com/TFS-Assets/CMD/brochures/br-90468-cytomat-2-c-lin-br90468-en.pdf) |
| Thermo Fisher Scientific | Cytomat 6002 | <span class="badge badge-heating">heating</span> | Full | [PLR](01_material-handling/storage/cytomat.ipynb) / [OEM](https://www.thermofisher.com/order/catalog/product/50075279) |
| Thermo Fisher Scientific | Cytomat 2 C_50 | <span class="badge badge-heating">heating</span> | Full | [PLR](01_material-handling/storage/cytomat.ipynb) / OEM? |
| Thermo Fisher Scientific | Cytomat 2 C425 | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">active cooling</span> | Full | [PLR](01_material-handling/storage/cytomat.ipynb) / [OEM](https://www.thermofisher.com/order/catalog/product/51033032) |
| Thermo Fisher Scientific | Cytomat 2 C450_SHAKE | <span class="badge badge-heating">heating</span><span class="badge badge-shaking">shaking</span> | Full | [PLR](01_material-handling/storage/cytomat.ipynb) / [OEM](https://www.thermofisher.com/order/catalog/product/51033035) |
| Thermo Fisher Scientific | Cytomat 5C | <span class="badge badge-heating">heating</span> | Full | [PLR](01_material-handling/storage/cytomat.ipynb) / [OEM](https://www.thermofisher.com/order/catalog/product/51031526) |
| Thermo/Liconic | Heraeus Cytomat | <span class="badge badge-heating">heating</span> | Full | [PLR](01_material-handling/storage/cytomat.ipynb) / OEM? |
| Inheco | Incubator Shaker (MTP/DWP) | <span class="badge badge-heating">heating</span><span class="badge badge-shaking">shaking</span> | Mostly | [OEM](https://www.inheco.com/incubator-shaker.html) |
| Inheco | SCILA | <span class="badge badge-heating">heating</span><span class="badge badge-shaking">shaking</span> | Mostly | [OEM](https://www.inheco.com/scila.html) |
| Liconic | STX series | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">active cooling</span> | Mostly | [PLR](01_material-handling/storage/liconic.ipynb) / [OEM](https://www.liconic.com/stx.html) |

### Peelers

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Azenta Life Sciences | XPeel | Full | [OEM](https://www.azenta.com/products/automated-plate-seal-remover-formerly-xpeel) |

### Sealers

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Azenta Life Sciences | a4S | Full | [PLR](01_material-handling/sealers/a4s.ipynb) / [OEM](https://www.azenta.com/products/automated-roll-heat-sealer-formerly-a4s) |

### Thermocyclers

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Opentrons | Thermocycler | Full | [OEM](https://opentrons.com/products/thermocycler-module-1) |
| Thermo Fisher Scientific | ATC | Full | [OEM](https://www.thermofisher.com/us/en/home/life-science/pcr/thermal-cyclers-realtime-instruments/thermal-cyclers/automated-thermal-cycler-atc.html) |
| Thermo Fisher Scientific | ProFlex | Full | [OEM](https://www.thermofisher.com/us/en/home/life-science/pcr/thermal-cyclers-realtime-instruments/thermal-cyclers/proflex-pcr-system.html) |
| Inheco | ODTC | Mostly | [OEM](https://www.inheco.com/odtc.html) |

### Temperature Controllers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| Opentrons | Temperature Module | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">active cooling</span> | Mostly | [PLR](01_material-handling/temperature-controllers/ot-temperature-controller.ipynb) / [OEM](https://opentrons.com/products/temperature-module-gen2) |
| Inheco | CPAC | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">active cooling</span> | Full | [OEM](https://www.inheco.com/cpac.html) |
| Hamilton | Heater/Cooler | <span class="badge badge-heating">heating</span><span class="badge badge-cooling">active cooling</span> | Full (v1b1) | [OEM](https://www.hamiltoncompany.com/temperature-control/heater-cooler) |

### Tilting

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Hamilton | Tilt Module | Full | [PLR](01_material-handling/tilting.md) / [OEM](https://www.hamiltoncompany.com/other-robotics/188061) |

---

## Analytical Machines

### Plate Readers

| Manufacturer | Machine | Features | PLR-Support | Links |
|--------------|---------|----------|-------------|--------|
| BMG Labtech | CLARIOstar (Plus) | <span class="badge badge-absorbance">absorbance</span><span class="badge badge-fluorescence">fluorescence</span><span class="badge badge-luminescence">luminescence</span> | Full | [PLR](02_analytical/plate-reading/bmg-clariostar.ipynb) / [OEM](https://www.bmglabtech.com/en/clariostar-plus/) |
| Agilent (BioTek) | Cytation 1 | <span class="badge badge-absorbance">absorbance</span><span class="badge badge-fluorescence">fluorescence</span><span class="badge badge-luminescence">luminescence</span><span class="badge badge-microscopy">microscopy</span> | Full | [PLR](02_analytical/plate-reading/cytation.ipynb) / [OEM](https://www.agilent.com/en/product/cell-analysis/cell-imaging-microscopy/cell-imaging-multimode-readers/biotek-cytation-1-cell-imaging-multimode-reader-1623200) |
| Agilent (BioTek) | Cytation 5 | <span class="badge badge-absorbance">absorbance</span><span class="badge badge-fluorescence">fluorescence</span><span class="badge badge-luminescence">luminescence</span><span class="badge badge-microscopy">microscopy</span> | Full | [PLR](02_analytical/plate-reading/cytation.ipynb) / [OEM](https://www.agilent.com/en/product/cell-analysis/cell-imaging-microscopy/cell-imaging-multimode-readers/biotek-cytation-5-cell-imaging-multimode-reader-1623202) |
| Agilent (BioTek) | Synergy H1 | <span class="badge badge-absorbance">absorbance</span><span class="badge badge-fluorescence">fluorescence</span><span class="badge badge-luminescence">luminescence</span> | Full | [PLR](02_analytical/plate-reading/synergyh1.ipynb) / [OEM](https://www.agilent.com/en/product/microplate-instrumentation/microplate-readers/multimode-microplate-readers/biotek-synergy-h1-multimode-reader-1623193) |
| Agilent (BioTek) | Synergy HT | <span class="badge badge-absorbance">absorbance</span><span class="badge badge-fluorescence">fluorescence</span><span class="badge badge-luminescence">luminescence</span> | Full | [OEM](https://www.agilent.com/en/product/microplate-instrumentation/microplate-readers/multimode-microplate-readers/biotek-synergy-ht-multi-mode-reader-1623194) |
| Byonoy | Absorbance 96 Automate | <span class="badge badge-absorbance">absorbance</span> | Full | [PLR](02_analytical/plate-reading/byonoy/absorbance.ipynb) / [OEM](https://byonoy.com/absorbance-96-automate/) |
| Byonoy | Luminescence 96 | <span class="badge badge-luminescence">luminescence</span> | Full | [PLR](02_analytical/plate-reading/byonoy/luminescence.ipynb) / [OEM](https://byonoy.com/luminescence-96/) |
| Byonoy | Luminescence 96 Automate | <span class="badge badge-luminescence">luminescence</span> | Full | [PLR](02_analytical/plate-reading/byonoy/luminescence.ipynb) / [OEM](https://byonoy.com/luminescence-96-automate/) |
| Molecular Devices | SpectraMax M5e | <span class="badge badge-absorbance">absorbance</span><span class="badge badge-fluorescence">fluorescence</span> <span class="badge badge-time-resolved-fluo">time-resolved fluorescence</span><span class="badge badge-fluo-polarization">fluorescence polarization</span> | Full | [OEM](https://www.moleculardevices.com/products/microplate-readers/multi-mode-readers/spectramax-m-series-readers) |
| Molecular Devices | SpectraMax 384plus | <span class="badge badge-absorbance">absorbance</span> | Full | [OEM](https://www.moleculardevices.com/products/microplate-readers/absorbance-readers/spectramax-abs-plate-readers) |
| Molecular Devices | ImageXpress Pico | <span class="badge badge-microscopy">microscopy</span> | Basics | [PLR](02_analytical/plate-reading/pico.ipynb) / [OEM](https://www.moleculardevices.com/products/cellular-imaging-systems/high-content-imaging/imagexpress-pico) |
| Molecular Devices | ImageXpress Micro | <span class="badge badge-microscopy">microscopy</span> | WIP | [OEM](https://www.moleculardevices.com/products/cellular-imaging-systems/high-content-imaging/imagexpress-micro-4) |
| Molecular Devices | ImageXpress Nano | <span class="badge badge-microscopy">microscopy</span> | WIP | [OEM](https://www.moleculardevices.com/products/cellular-imaging-systems/high-content-imaging/imagexpress-nano) |
| Tecan | Infinite 200 PRO | <span class="badge badge-absorbance">absorbance</span><span class="badge badge-fluorescence">fluorescence</span><span class="badge badge-luminescence">luminescence</span> | Mostly | [PLR](02_analytical/plate-reading/tecan-infinite.ipynb) / [OEM](https://lifesciences.tecan.com/infinite-200-pro) |
| Tecan | Spark 20M | <span class="badge badge-absorbance">absorbance</span><span class="badge badge-fluorescence">fluorescence</span> | Mostly | [PLR](02_analytical/plate-reading/tecan-spark.ipynb) / [OEM](https://lifesciences.tecan.com/spark-multimode-microplate-reader) |


### Flow Cytometers

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Beckman Coulter | CytoFLEX S | WIP | [OEM](https://www.beckman.com/flow-cytometry/research-flow-cytometers/cytoflex-s) |

### qPCR Machines

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Thermo Fisher Scientific | QuantStudio 5 | WIP | [OEM](https://www.thermofisher.com/order/catalog/product/A34322) |

### Scales

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Mettler Toledo | WXS205SDU | Full | [PLR](02_analytical/scales/mettler-toledo-WXS205SDU.ipynb) / [OEM](https://www.mt.com/us/en/home/products/Industrial_Weighing_Solutions/high-precision-weigh-sensors/weigh-module-wxs205sdu-15-11121008.html) |

### Barcode Scanners

| Manufacturer | Machine | PLR-Support | Links |
|--------------|---------|-------------|--------|
| Keyence | Barcode Scanner | Full | [OEM](https://www.keyence.com/products/barcode/) |

---

## Understanding the Tables

Classifying lab automation equipment can be challenging.
There are many reasons for this, including (but not limited to):
- many machines have overlapping capabilities (the TFS Cytomat 2 C470 can be a fridge, heated chamber, oven, smart storage/plate hotel and shaker all in one machine!),
- different user groups require varying levels of software integration but tend to only refer to the capabilities they use in that moment (e.g. what is a shaker to one group might be a heater to another),
- balancing naming/classification based on user intuition/historic legacy and first principles, (e.g. a thermocycler is just a speedy heater/cooler but scientists are used to the term thermocycler)
- there is no widely accepted naming standard.

PyLabRobot does not solve these human classification issues.
But to provide *some* structure PyLabRobot classifies machines into three purposefully broad categories:

- **Liquid handling**: Machines directly manipulating liquids.
- **Material handling**: Machines handling materials other than liquids.
- **Analytical**: Machines primarily responsible for performing measurements.

**Table Columns Explained:**

- **Features**: Core capabilities provided by the machine.
- **PLR-Support**: Indicates PyLabRobot integration status:
  - **WIP**: Work in progress.
  - **Basics**: Core functionalities available.
  Code integrated into `pylabrobot:main`. Documentation pages in `docs.pylabrobot.org`.
  - **Mostly**: Most capabilities available, but some known commands still missing.
  - **Full**: Comprehensive capabilities (≥90%) fully supported, extensive documentation available.

Note: PyLabRobot aims to provide access to all hardware-firmware capabilities available on integrated equipment, even beyond OEM software.
This allows users to *choose* the machine functionalities they require.
