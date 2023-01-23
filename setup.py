from setuptools import setup, find_packages

from pylabrobot.__version__ import __version__

with open('README.md', 'r', encoding='utf-8') as f:
    long_description = f.read()

extras_docs = [
    'sphinx_book_theme',
    'myst_nb',
    'sphinx_copybutton',
]

extras_fw = [
    'pyusb'
]

extras_http = [
    'requests',
    'types-requests'
]

extras_plate_reading = [
    'pylibftdi',
]

extras_websockets = [
    'websockets'
]

extras_simulation = extras_websockets

extras_venus = [
    'pyhamilton'
]

extras_opentrons = [
    'opentrons-http-api-client',
    'opentrons-shared-data'
]

extras_server = [
    'flask[async]',
]

extras_testing = [
    'pytest',
    'pytest-timeout',
    'pylint',
    'mypy',
    'responses'
] + extras_simulation + extras_opentrons + extras_server + extras_http + extras_plate_reading

extras_dev = extras_docs + extras_simulation + extras_http + extras_websockets + extras_testing + \
              extras_server + extras_fw + extras_opentrons + extras_plate_reading

extras_all = extras_docs + extras_simulation + extras_http + extras_websockets + extras_testing + \
              extras_venus + extras_server + extras_fw + extras_opentrons + extras_plate_reading

setup(
    name='PyLabRobot',
    version=__version__,
    packages=find_packages(exclude="tools"),
    description='A hardware agnostic platform for liquid handling',
    long_description=long_description,
    long_description_content_type='text/markdown',
    install_requires=["typing_extensions"],
    url='https://github.com/pylabrobot/pylabrobot.git',
    package_data={'pylabrobot': ['liquid_handling/backends/simulation/simulator/*']},
    extras_require={
        'testing': extras_testing,
        'docs': extras_docs,
        'fw': extras_fw,
        'simulation': extras_simulation,
        'http': extras_http,
        'plate_reading': extras_plate_reading,
        'websockets': extras_websockets,
        'venus': extras_venus,
        'opentrons': extras_opentrons,
        'server': extras_server,
        'dev': extras_dev,
        'all': extras_all,
    },
    entry_points={
        'console_scripts': [
            'lh-server=pylabrobot.server.liquid_handling_server:main',
        ],
    }
)
