"""Reading an instrument's Biomek files into its declared configuration
(`DeviceConfiguration.from_biomek`): Biomek's instrument file (`instrument_file.py`, over the
variant-tree reader `variant_tree.py`) and the Master Controller's instrument record
(`master_controller.py`), put together and checked against each other by `importer.py`. Read-only
on every Biomek file, and standard library only."""
