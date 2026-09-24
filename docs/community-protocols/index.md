# Community Protocols

Open-source protocols built by the PyLabRobot community. These are maintained by their respective authors and shared here for the benefit of the community.

If you'd like to add your protocol to this page, please open a pull request or post on the [forum](https://discuss.pylabrobot.org/).

## NGS Library Prep

**Author:** [Pioneer Research Labs](https://www.pioneer-labs.org) (Emeryville, CA)

**Repository:** [Pioneer-Research-Labs/ngs_library_prep](https://github.com/Pioneer-Research-Labs/ngs_library_prep)

**Hardware:** Hamilton STARlet

Automated Illumina NGS (Next-Generation Sequencing) library preparation. The full workflow includes three steps:

1. **Sample consolidation** -- consolidating samples across 96-well plates
2. **Adapter PCR** -- running adapter PCR with the same primers across all samples
3. **Index PCR** -- running index PCR with different primers per sample

Each step can be run independently. Supports simulation mode for development without hardware.
