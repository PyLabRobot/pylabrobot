# Heater Shakers

Heater-shakers are a hybrid of {class}`~pylabrobot.temperature_controllers.temperature_controller.TemperatureController` and {class}`~pylabrobot.shakers.shaker.Shaker`. They are used to control the temperature of a sample while shaking it.

PyLabRobot supports the following heater shakers:

- Inheco ThermoShake RM (tested)
- Inheco ThermoShake (should have the same API as RM)
- Inheco ThermoShake AC (should have the same API as RM)
- Hamilton Heater Shaker (tested)
- QInstruments BioShake (3000 elmm, 5000 elm, and D30-T elm tested)

```{toctree}
:maxdepth: 1

Inheco ThermoShake <inheco>
Hamilton Heater Shaker <hamilton>
QInstruments BioShake <qinstruments>
```
