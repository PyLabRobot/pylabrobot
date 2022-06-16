from setuptools import setup

long_description = open('README.md', encoding='utf-8').read()

setup(
    name='pyhamilton',
    version='1.235',
    packages=['pyhamilton'],
    license='MIT',
    description='Python for Hamilton liquid handling robots',
    long_description=long_description,
    long_description_content_type='text/markdown',
    install_requires=[],
    package_data={'pyhamilton': ['star-oem/*', 'star-oem/VENUS_Method/*']},
    url='https://github.com/dgretton/pyhamilton.git',
    author='Dana Gretton',
    author_email='dgretton@mit.edu',
    extras_require={
        'testing': [
            'pytest'
        ]
    }
)
