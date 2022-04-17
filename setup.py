from setuptools import setup, find_packages

setup(
    name='pyhamilton',
    version='1.235',
    packages=find_packages(exclude=['tests*', 'examples*']),
    license='MIT',
    description='Python for Hamilton liquid handling robots',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    install_requires=['requests', 'pythonnet', 'pywin32', 'pyserial'],
    package_data={'pyhamilton': ['star-oem/*', 'star-oem/VENUS_Method/*']},
    url='https://github.com/dgretton/pyhamilton.git',
    author='Dana Gretton',
    author_email='dgretton@mit.edu'
)
