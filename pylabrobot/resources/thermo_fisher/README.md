# Resource definitions: Thermo Fisher Scientific Inc.

Company page: [Thermo Fisher Scientific Inc. Wikipedia](https://en.wikipedia.org/wiki/Thermo_Fisher_Scientific)

> Thermo Fisher Scientific Inc. is an American supplier of analytical instruments, life sciences solutions, specialty diagnostics, laboratory, pharmaceutical and biotechnology services. Based in Waltham, Massachusetts, Thermo Fisher was formed through the **merger of Thermo Electron and Fisher Scientific in 2006**. Thermo Fisher Scientific has acquired other reagent, consumable, instrumentation, and service providers, including Life Technologies Corporation (2013), Alfa Aesar (2015), Affymetrix (2016), FEI Company (2016), BD Advanced Bioprocessing (2018),and PPD (2021).

> As of 2023, the company had a market capitalization of $202 billion. It ranked 97th on the Fortune 500 list based on its 2022 annual revenue of US$44.92 billion.

A basic structure of the companiy, [its brands](https://www.thermofisher.com/uk/en/home/brands.html) and product lines looks like this:

```rust
Thermo Fisher Scientific Inc. (TFS, aka "Thermo")
├── Applied Biosystems (AB; brand)
│   └── MicroAmp
│      └── EnduraPlate
├── Fisher Scientific (FS; brand)
├── Invitrogen (INV; brand)
├── Ion Torrent (IT; brand)
├── Gibco (GIB; brand)
├── Thermo Scientific (TS; brand)
│   ├── Nalgene
│   ├── Nunc
│   └── Pierce
├── Unity Lab Services (brand, services)
├── Patheon (brand, services)
└── PPD (brand, services)
```

## Plates

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'Thermo_TS_96_wellplate_1200ul_Rb'<br>Part no.: AB-1127 or 10243223<br>[manufacturer website](https://www.fishersci.co.uk/shop/products/product/10243223) <br><br>- Material: Polypropylene (AB-1068, polystyrene) <br> | <img src="imgs/Thermo_TS_96_wellplate_1200ul_Rb.webp" alt="Thermo_TS_96_wellplate_1200ul_Rb" style="width:250px;"/> | `Thermo_TS_96_wellplate_1200ul_Rb` |
| 'Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate'<br>Part no.: 4483354 (TFS) or 15273005 (FS) (= with barcode)<br>Part no.: 16698853 (FS) (= **without** barcode)<br>[manufacturer website](https://www.thermofisher.com/order/catalog/product/4483354) <br><br>- Material: Polycarbonate, Polypropylene<br>- plate_type: semi-skirted<br>- product line: "MicroAmp"<br>- (sub)product line: "EnduraPlate" | <img src="imgs/Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate.png" alt="Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate" style="width:250px;"/> | `Thermo_AB_96_wellplate_300ul_Vb_EnduraPlate` |

## Troughs

| Description               | Image              | PLR definition |
|--------------------|--------------------|--------------------|
| 'ThermoFisherMatrixTrough8094'<br>Part no.: 8094<br>[manufacturer website](https://www.thermofisher.com/order/catalog/product/8094) | <img src="imgs/ThermoFisherMatrixTrough8094.jpg.avif" alt="ThermoFisherMatrixTrough8094.jpg.avif" width="250"/> | `ThermoFisherMatrixTrough8094` |
