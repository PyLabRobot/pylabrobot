from setuptools import setup

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
    version='1.46',
    packages=find_packages(exclude=['tests*', 'examples*']),
    license='MIT',
    description='Python for Hamilton liquid handling robots',
    long_description='Forthcoming due to markdown incompatibility',
    install_requires=['requests', 'pythonnet', 'pywin32', 'pyserial'],
    package_data={'pyhamilton': ['star-oem/*', 'star-oem/VENUS_Method/*', 'bin/*','library/*']},
    url='https://github.com/dgretton/pyhamilton.git',
    author='Dana Gretton',
    author_email='dgretton@mit.edu',
    entry_points={
        'console_scripts': [
            'pyhamilton-quickstart = pyhamilton.cmd.quickstart:main',
            'pyhamilton-config = pyhamilton.__init__:autoconfig'
        ],
    },
    extras_require={
        'testing': extras_testing,
        'docs': extras_docs,
        'simulation': extras_simulation,
        'all': extras_all
    }
)
