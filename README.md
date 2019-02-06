# Python Lambda Zipper

[![Docs Stable](https://img.shields.io/badge/docs-stable-blue.svg)](https://invenia.pages.invenia.ca/plz/docs/)
[![Build Status](https://gitlab.invenia.ca/invenia/plz/badges/master/build.svg)](https://gitlab.invenia.ca/invenia/plz/commits/master)
[![Coverage Status](https://gitlab.invenia.ca/invenia/plz/badges/master/coverage.svg)](https://invenia.pages.invenia.ca/plz/coverage/)
[![Python Version](https://img.shields.io/badge/python-3.6-blue.svg)](https://www.python.org/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)

Zip up a python script (and its dependencies) so that it can be deployed to an AWS Lambda.

## Installation

Python Lambda Zipper requires Python 3.6 or higher.

```python
python setup.py install
```

## Basic Usage

Python Lambda Zipper provides a single function, `build_package` which takes  a build directory to set up the package in and any number of files or folders as arguments and returns the path to the zipped package:


```python
from pathlib import Path
from zipfile import ZipFile

from plz import build_package


build_directory = Path("build")

# the files to include:
script = Path("lambda/index.py")
utilities = Path("utilities/")

print(list(utilities.iterdir()))
# [Path("utilities/a.py"), Path("utilities/b.py"), ... Path("utilities/z.py")]

package = build_package(build_directory, script, utilities)

print(package)  # Path("build/package.zip")
for file in ZipFile(package, "r").namelist():
    print(file)
# index.py
# utilities/a.py
# utilities/b.py
# ...
# utilities/z.py
```

You can also supply one or more requirements files for installing needed python packages:

```python
requirements = Path("requirements.txt")

print(requirements.read_text())  # pg8000

package = build_package(build_directory, script, utilities, requirements=requirements)

print(package)  # Path("./build/package.zip")
for file in ZipFile(package, "r").namelist():
    print(file)
# index.py
# pg8000/__init__.py
# pg8000/_version.py
# pg8000/core.py
# pg8000/pg_scram.py
# utilities/lorem.py
# ...
# utilities/ipsum.py
```

Python Lambda Zipper also provides a command line interface:

```sh
>plz --requirements requirements.txt lambda/index.py utilities
build/package.zip
>ls build/package
index.py
pg8000/__init__.py
pg8000/_version.py
pg8000/core.py
pg8000/pg_scram.py
utilities/lorem.py
...
utilities/ipsum.py
```

## Testing

In order to run tests, you will need to install both `tox`, and `tox-venv`:

```sh
pip install tox tox-venv
```

`tox-venv` is needed to get around an issue where virtual environments created using `venv.create` will install packages to the incorrect directory when run from within a virtual environment created by `virtualenv` ([issue](https://bugs.python.org/issue30811)).
