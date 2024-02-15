# Parsing Hamilton VENUS Resources

PyLabRobot allows you to easily import resources from the VENUS labware library.

There are two ways to do this:

1. Creating a Python resource definition file
2. Importing a resource directly into Python

(creating-a-python-resource-definition-file)=

## Creating a Python resource definition file

To create a Python resource definition file, you will need to use the `make_ham_resources.py` script. This script will generate a Python resource definition file for you.

```bash
# from the root of the repository
python tools/make_resources/make_ham_resources.py -o <output_file> --filepath /path/to/file.rck
```

Where `<output_file>` is the name of the file you want to create `path/to/file.rck` is the path to the `.rck` or `.tml` file you want to parse.

The `-o` flag is optional. If you do not provide an output file, the script will print the resource definition to the console.

You can also parse an entire directory of `.rck` or `.tml` files by providing the `--base-dir` flag.

```bash
# from the root of the repository
python tools/make_resources/make_ham_resources.py -o <output_file> --base-dir /path/to/directory
```

The Hamilton labware library is usually located at `C:\Program Files (x86)\HAMILTON\LabWare` on the Windows computer where VENUS is installed.

## Importing a resource directly into Python

You can also import a resource directly into Python using the `hamilton_parse` module.

```python
from pylabrobot.resources.hamilton_parse import create_plate
create_plate('/path/to/file.rck', name='my_plate')
```

## Contributing resources

Please contribute resources to PLR if you have any that are not already in the library! (This makes for a great first contribution to the project!)

To contribute resources, create resource definitions as described [above](#creating-a-python-resource-definition-file) and add it to the `pylabrobot/resources` module. Then create a pull request. You can find a guide on creating pull requests {doc}`here </how-to-open-source>`.
