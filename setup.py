from setuptools import setup, find_packages

long_description = open('README.md', encoding='utf-8').read()

extras_docs = [
    'sphinx_book_theme',
    'myst_nb',
    'sphinx_copybutton',
]

extras_simulation = [
    'websockets'
]

extras_venus = [
    'pyhamilton'
]

extras_testing = [
    'pytest',
    'pytest-timeout',
    'requests'
] + extras_simulation

extras_all =  extras_docs + extras_simulation + extras_testing + extras_venus

setup(
    name='PyLabRobot',
    version='0.1',
    packages=find_packages(exclude="tools"),
    description='A robot agnostic platform for liquid handling',
    long_description=long_description,
    long_description_content_type='text/markdown',
    install_requires=['pyusb', 'websockets'],
    url='https://github.com/pylabrobot/pylabrobot.git',
    extras_require={
        'testing': extras_testing,
        'docs': extras_docs,
        'simulation': extras_simulation,
        'all': extras_all
    }
)
