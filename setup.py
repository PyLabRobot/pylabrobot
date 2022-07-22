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

extras_testing = [
    'pytest',
    'pytest-timeout',
    'requests'
] + extras_simulation

extras_all =  extras_docs + extras_simulation + extras_testing

setup(
    name='pyhamilton',
    version='1.235',
    packages=find_packages(exclude="tools"),
    license='MIT',
    description='Python for Hamilton liquid handling robots',
    long_description=long_description,
    long_description_content_type='text/markdown',
    install_requires=['pyusb', 'websockets'],
    package_data={'pyhamilton': ['star-oem/*', 'star-oem/VENUS_Method/*']},
    url='https://github.com/dgretton/pyhamilton.git',
    author='Dana Gretton',
    author_email='dgretton@mit.edu',
    extras_require={
        'testing': extras_testing,
        'docs': extras_docs,
        'simulation': extras_simulation,
        'all': extras_all
    }
)
