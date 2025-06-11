# Protocol Library
A basis and quickstart applications library for the PyLabRobot platform!


## File Structure

### General
    protocol_library/
    ├── protocol_family/
    │  ├── protocol_type/
    │  │  ├── generic_examples/
    │  │  │  ├── example_1/
    │  │  │  │  ├── README.md
    │  │  │  │  ├── protocol_example.py
    │  │  │  │  └── requirements.txt
    │  │  │  ├── example_2/
    │  │  │  │  ├── README.md
    │  │  │  │  ├── protocol_example.py
    │  │  │  │  └── requirements.txt
    │  │  │  └── ...
    │  │  └── plug_n_play/
    │  │     ├── example_1/
    │  │     │  ├── README.md
    │  │     │  ├── protocol_example.py
    │  │     │  └── requirements.txt
    │  │     └── ...
    │  └── protocol_type_2/ ...
    └── protocol_family_2/ ...

### Example
    protocol_library/
    ├── bacterial_culture/
    │  ├── turbidostat/
    │  │  ├── generic_turbidostat/
    │  │  │  ├── turbidostat_with_pumps/
    │  │  │  │  ├── README.md
    │  │  │  │  ├── turbidostat.ipynb
    │  │  │  │  └── requirements.txt
    │  │  │  ├── turbidostat_no_pumps/
    │  │  │  │  ├── README.md
    │  │  │  │  ├── turbidostat.ipynb
    │  │  │  │  └── requirements.txt
    │  │  │  └── ...
    │  │  └── turbidostat_opentrons/
    │  │     ├── README.md
    │  │     ├── protocol_example.py
    │  │     └── requirements.txt
    │  │     └── ...
    │  └── protocol_type_2/ ...
    └── protocol_family_2/ ...



Other things to think about with the file structure:
- .gitignore for each
- ideas to include in each README.md?
  - troubleshooting guidelines
  - explaination of proceedure
  - how to cite
  - requirements of system
  - how to configure for systems 
    - ex. for systems with and without pumping troughs



