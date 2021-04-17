# Python Lambda Zipper

[![Docs Stable](https://img.shields.io/badge/docs-stable-blue.svg)](https://invenia.pages.invenia.ca/plz/docs/)
[![Build Status](https://gitlab.invenia.ca/invenia/plz/badges/master/pipeline.svg)](https://gitlab.invenia.ca/invenia/plz/commits/master)
[![Coverage Status](https://gitlab.invenia.ca/invenia/plz/badges/master/coverage.svg)](https://invenia.pages.invenia.ca/plz/coverage/)
[![Python Version](https://img.shields.io/badge/python-3.6%20%7C%203.7-blue.svg)](https://www.python.org/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)

Zip up a python script (and its dependencies) so that it can be deployed to an AWS Lambda.

## Installation

Python Lambda Zipper requires Python 3.6 or higher, and [docker](https://gitlab.invenia.ca/invenia/wiki/blob/master/dev/docker.md).

Use `pip3` to install plz directly from gitlab:

```sh
# Make sure your installation of pip3 is up to date
$ pip3 install --upgrade pip
# Install plz
$ pip3 install git+ssh://git@gitlab.invenia.ca/invenia/plz
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

You can also supply one or more system requirements files for installing needed system packages:

The yaml format for the yum requirements files is as follows:
```yaml
version: 0.1.0
packages:
  <packagename>:
    - <location of a file to copy>
  <packagename2>:
    - <location of a file to copy>
    - <location of another file to copy>
filetypes:  # optional
  - <file extension to copy over>
  - <another file extension>
extra:  # optional
  - <a package that you didn't install but want in the bundle>
ignore:  # optional
  - <a package you installed but don't want in the bundle>
```

For example:
```yaml
version: 0.1.0
packages:
  libpng:
    - /usr/lib64/libpng15.so.15
  eccodes:
    - /usr/lib64/libeccodes.so.0.1
  eccodes-data:
    - /usr/share/eccodes/definitions
```

If `filetypes` is provided (or no system files are needed in the actual bundle), `packages` can be provided as a list instead of a dictionary:

```yaml
version: 0.1.0
filetypes:
  - .so
packages:
  - libpng
  - eccodes
```

If a package is only needed for installation, you can add it to a list of packages to skip when copying files:

```yaml
version: 0.1.0
filetypes:
  - .so
packages:
  - libpng
  - eccodes
skip:
  - libpng
```

```python
system_requirements = Path("system_requirements.yaml")

print(system_requirements.read_text())
# version: 0.1.0
# packages:
#   libpng:
#     - /usr/lib64/libpng15.so.15

package = build_package(build_directory, script, utilities, system_requirements=system_requirements)

print(package)  # Path("./build/package.zip")
for file in ZipFile(package, "r").namelist():
  print(file)
# index.py
# libpng15.so.15
# utilities/lorem.py
...
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
