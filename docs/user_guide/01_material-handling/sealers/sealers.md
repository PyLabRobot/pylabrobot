# Sealers

In automated wet lab workflows, **microplate sealers** are essential for preserving sample integrity.
They prevent **evaporation**, **cross-contamination**, and **spillage**, especially during heating, shaking, storage, or robotic transport.

PyLabRobot supports integration with various sealer machines, allowing you to programmatically seal plates as part of your automation workflows.

---

## Types of Sealers

There are two primary categories of sealers commonly used in automated labs:

### Thermal Sealers

These use heat and pressure to bond a sealing film (typically foil or heat-reactive plastic) to the top of the microplate.

- **Examples**: Azenta a4S, Bio-Rad PX1, Agilent PlateLoc (thermal mode)
- **Best for**: Long-term storage, PCR/qPCR workflows, high-integrity applications
- **Pros**:
  - Very strong seal (potentially, depends on chosen parameters)
  - Compatible with a wide range of films
- **Cons**:
  - Slower sealing time (typically 5–10 seconds per plate)
  - Requires warm-up time
  - May need precise plate/film alignment
  - When peeled, thermal seals remove (at least some) well material.
  Thermal seals might therefore only be usable until too much well material has been ripped off.

### Adhesive (Pressure) Sealers

These apply pre-cut adhesive seals to the plate using downward mechanical pressure.
They do **not** use heat, making them faster and simpler for certain workflows.

- **Examples**: Agilent PlateLoc (adhesive mode), Thermo ALPS5000 (in adhesive mode)
- **Best for**: Medium-throughput workflows, frequent access, short-term incubation
- **Pros**:
  - Faster (as low as 1–2 seconds per plate)
  - No warm-up period
  - Compatible with repeelable seals
- **Cons**:
  - Weaker seal compared to thermal
  - Not suitable for long-term storage or high-temperature protocols

---


```{toctree}
:maxdepth: 1
:hidden:

Azenta a4S <a4s>
```
