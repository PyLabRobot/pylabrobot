# Byonoy

```{toctree}
:maxdepth: 1

absorbance_96/hello-world
luminescence_96/hello-world
luminescence_96/led_bar
```

## Absorbance 96 models

| Model | PLR resource | Factory function |
|---|---|---|
| A96A full setup | `ByonoyAbsorbance96` + illumination unit | `byonoy_a96a` |
| Detection unit only | `ByonoyAbsorbance96` | `byonoy_a96a_detection_unit` |
| Illumination unit | `Resource` | `byonoy_a96a_illumination_unit` |
| Parking base (no backend) | `ByonoyAbsorbanceBaseUnit` | `byonoy_a96a_parking_unit` |
| SBS adapter | `ResourceHolder` | `byonoy_sbs_adapter` |

## Luminescence 96 models

| Model | PLR resource | Factory function |
|---|---|---|
| L96 full setup | `ByonoyLuminescenceBaseUnit` + `ByonoyLuminescence96` | `byonoy_l96` |
| L96A full setup (automate) | `ByonoyLuminescenceBaseUnit` + `ByonoyLuminescence96` | `byonoy_l96a` |
| L96 reader unit only | `ByonoyLuminescence96` | `byonoy_l96_reader_unit` |
| L96A reader unit only | `ByonoyLuminescence96` | `byonoy_l96a_reader_unit` |
| L96 base unit only | `ByonoyLuminescenceBaseUnit` | `byonoy_l96_base_unit` |
| L96A base unit only | `ByonoyLuminescenceBaseUnit` | `byonoy_l96a_base_unit` |
