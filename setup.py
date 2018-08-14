from setuptools import setup, find_packages

setup(
    name='pyhamilton',
    version='1.0',
    packages=find_packages(exclude=['tests*', 'examples*']),
    license='MIT',
    description='Python for Hamilton liquid handling robots',
    long_description=open('README.md').read(),
    install_requires=['requests', 'pythonnet', 'pywin32', 'pyserial'],
    url='https://github.com/dgretton/pyhamilton.git',
    author='Dana Gretton',
    author_email='dgretton@mit.edu'
)
