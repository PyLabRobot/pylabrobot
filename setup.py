from setuptools import setup

long_description = open('README.md', encoding='utf-8').read()

extras_testing = [
    'pytest',
]

extras_docs = [
    'sphinx_book_theme',
    'myst_nb',
    'sphinx_copybutton',
    'commonmark',
]

extras_all = extras_testing + extras_docs

setup(
    name='pyhamilton',
    version='1.235',
    packages=['pyhamilton'],
    license='MIT',
    description='Python for Hamilton liquid handling robots',
    long_description=long_description,
    long_description_content_type='text/markdown',
    install_requires=['pyusb'],
    package_data={'pyhamilton': ['star-oem/*', 'star-oem/VENUS_Method/*']},
    url='https://github.com/dgretton/pyhamilton.git',
    author='Dana Gretton',
    author_email='dgretton@mit.edu',
    extras_require={
        'testing': extras_testing,
        'docs': extras_docs,
        'all': extras_all
    }
)
