# Python Lambda Zipper

[![Docs Stable](https://img.shields.io/badge/docs-stable-blue.svg)](https://invenia.pages.invenia.ca/plz/docs/)
[![Build Status](https://gitlab.invenia.ca/invenia/plz/badges/master/pipeline.svg)](https://gitlab.invenia.ca/invenia/plz/commits/master)
[![Coverage Status](https://gitlab.invenia.ca/invenia/plz/badges/master/coverage.svg)](https://invenia.pages.invenia.ca/plz/coverage/)
[![Python Version](https://img.shields.io/badge/python-3.6%20%7C%203.7-blue.svg)](https://www.python.org/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)

Bundle up a python script (and its dependencies) so that it can be deployed to an AWS Lambda.

## Prerequisites

Docker needs to be installed and running during operation.

## Installation

Python Lambda Zipper requires Python 3.8 or higher, and [docker](https://gitlab.invenia.ca/invenia/wiki/blob/master/dev/docker.md).

As long as you have access to our [private PyPI](https://gitlab.invenia.ca/invenia/wiki/-/blob/master/python/gitlab-pypi.md) set up, you can install plz using `pip`:

```sh

$ pip install plz
```

## Basic Usage

Python Lambda Zipper provides functions for producing zip files and Docker
images that can be used as the code bundles for AWS Lambda Functions.

These functions (`build_image` and `build_zip`) work largely the same, and are designed to be quick to switch between:

```python
from pathlib import Path

from plz import build_image, build_zip

BUILD = Path.cwd("build")

image = build_image(BUILD, Path("lambdas/handler"), requirements=Path("requirements.txt"))

zip_file = build_zip(BUILD, Path("lambdas/handler"), requirements=Path("requirements.txt"))
```

Both functions take the same basic arguments:
* `directory`: The directory to store any build artifacts produced by the process
* `*files`: Any number of files to include in your bundle.
  Directories will be copied over recursively, they will not be flattened.
* `requirements`: Either a requirements file or a list of requirements files
  * plz doesn't support references to other requirements files as these files will not necessarily have the same name on the docker image the bundle is built in and only explicitly passed requirements files are copied over.
* `constraints`: Either a constraints file or a list of constraints files to
  use when installing python packages
* `pip_args`: Any number of arguments to be passed through to `pip` when installing packages.
  * These arguments will be passed for all requirements.
    If you only want a flag to a specific package, you can specify those flags within the requirements file
* `system_packages`: A list of packages to install into the image the bundle is being built in.
  * These packages will not be copied out to the zip.
    If you need a system dependency in your lambda, you should use an image
* `repository`: What to call the docker image used to build the bundle
* `tag`: What tag to use for the image used to build the bundle
* `python_version`: What version of python to build for (default 3.9)
* `rebuild`: Rebuild the docker image from scratch.
  * If `False`, plz will only rebuild the layers that need to be updated.
* `freeze`: Whether to create a pip freeze file.
  * If `True`, the file will be saved to `frozen-requirements.txt` in the build directory.
    You can pass a `Path` if you'd like the file to be saved there instead.


### Zip Files

The `build_zip` function will return the path to the created zip file.
By default, the zip file will be named `package.zip` and will be saved to the build directory.

Here's a sample CloudFormation template for a zip file with a `handler` function in `index.py` that has been uploaded to the S3 bucket `codebucket` with the key `example-lambda.zip`:

```yaml
Resources:
  YourLambdaFunction:
    Type: AWS::Lambda::Function
    Properties:
      Description: An example lambda function
      Code:
        S3Bucket: codebucket
        S3Key: example-lambda.zip
      Runtime: python3.9
      Handler: index.handler
```

You can have `build_zip` upload your code for you using the following arguments:
* `bucket`: The S3 bucket to upload code to.
* `key`: The S3 key to use
  * If not supplied, the name of the zip file will be used
* `session`: A boto session to use to upload the file to S3
  * If not supplied, the default session will be used
* `unique_key`: Add a hash of the zip to the S3 key so that the name reflects the contents
  * You should always do this. CloudFormation will not update a Lambda function if an S3 object has been updated, it will only updated the function if the Lambda changes which S3 object the function is using
  * plz will not clean up old bundles for you

If `bucket` is supplied, the zip file will be updated and `build_zip` will return the S3 key instead of a path to the local zip file.

Docker is only used if you supply dependencies and will only be updated if the dependencies change, not if Lambda files change.


### Images

The `build_image` function will return the name of the created Docker image.
The image will be built in three layers:
* The base image with any required system dependencies installed
* A middle image with all python dependencies installed
* The final image with all supplied files copied into the working directory

If a layer doesn't require additional changes, it will be skipped.
Rerunning `build_image` will only result in the layers that require changing being rebuilt (i.e., updating your Lambda files won't require you to reinstall python or system layers).

Here's a sample CloudFormation template for an image with a `handler` function in `index.py` that has been uploaded to the ECR repository `example-lambda` in the account `12345678` in the `us-east-1` region with the tag `3.9`:

```yaml
Resources:
  YourLambdaFunction:
    Type: AWS::Lambda::Function
    Properties:
      Description: An example lambda function
      Code:
        ImageUri: 12345678.dkr.ecr.us-east-1.amazonaws.com/example-lambda:3.9"
      Runtime: python3.9
      Handler: index.handler
```

You can have `build_image` upload your code for you using the following arguments:
* `remote_repository`: The name of the repository to be used
* `session`: A boto session to use to upload the image
  * If not supplied, the default session will be used
  * The account and region will automatically be pulled from this session.
  * If a session is provided, it must have a profile listed in your `~/.aws/config` file
    * This is because uploading the image is done using `docker tag` and that command shells out to the command line.

The tag used will match the tag used for the local image.
If `remote_repository` is supplied the image will be uploaded and the function will return the remote path to image.

## Testing

In order to run tests, you will need to install `tox`:

```sh
pip install tox
```

You can then run tests by running `tox`:

```sh
tox
```