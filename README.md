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

Python Lambda Zipper provides a single function, `build_package`:

```python
from plz import build_package

package = build_package(build_directory, any, files, folders, requirements=requirements_file)
```

Python Lambda Zipper also provides a command line interface:

```sh
>plz --requirements requirements.txt file1 file2 folder
<PATH TO ZIP FILE>
```
