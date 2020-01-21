# Python Lambda Zipper

[![Docs Stable](https://img.shields.io/badge/docs-stable-blue.svg)](https://infrastructure.pages.invenia.ca/plz/docs/)
[![Build Status](https://gitlab.invenia.ca/infrastructure/plz/badges/master/build.svg)](https://gitlab.invenia.ca/infrastructure/plz/commits/master)
[![Coverage Status](https://gitlab.invenia.ca/infrastructure/plz/badges/master/coverage.svg)](https://infrastructure.pages.invenia.ca/plz/coverage/)
[![Python Version](https://img.shields.io/badge/python-3.6%20%7C%203.7-blue.svg)](https://www.python.org/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)

Zip up a python script (and its dependencies) so that it can be deployed to an AWS Lambda.

## Installation

Python Lambda Zipper requires Python 3.6 or higher, and [docker](https://gitlab.invenia.ca/invenia/wiki/blob/master/setup/docker.md).

Use `pip3` to install plz directly from gitlab:

```sh
# Make sure your installation of pip3 is up to date
$ pip3 install --upgrade pip
# Install plz
$ pip3 install git+ssh://git@gitlab.invenia.ca/infrastructure/plz
```

## Upgrading

Since we installed it through `pip3` with the gitlab url, we can just upgrade it with pip3:

```sh
pip3 install --upgrade plz
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
>plz --requirements requirements.txt --python-version 3.8 lambda/index.py utilities
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

In order to run tests, you will need to install `tox`:

```sh
pip install tox
```
