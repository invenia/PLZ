"""
Build a package
"""
import json
import logging
from pathlib import Path
from shutil import copy2, copytree, rmtree
from typing import Optional, Sequence, Union
from zipfile import ZipFile

import docker  # type: ignore
import yaml

from .plzdocker import (
    build_docker_image,
    pip_install,
    run_docker_cmd,
    start_docker_container,
    stop_docker_container,
    yum_install,
)


__all__ = ["build_package"]


def build_package(
    build: Path,
    *files: Path,
    requirements: Optional[Union[Sequence[Path], Path]] = None,
    yum_requirements: Optional[Union[Sequence[Path], Path]] = None,
    python_version: str = "3.7",
    zipped_prefix: Optional[Path] = None,
    force: bool = False,
) -> Path:
    """
    Build a python package.

    Args:
        build (:obj:`pathlib.Path`): The directory to build the package in.
        *files (:obj:`pathlib.Path`): Any number of files to include. Directories will
            be copied as subdirectories.
        requirements (:obj:`Optional[Union[Sequence[pathlib.Path], pathlib.Path]]`):
            If given, a path to or a sequence of paths to requirements files to be
            installed.
        yum_requirements (:obj:`Optional[Union[Sequence[pathlib.Path], pathlib.Path]]`):
            If given, a path to or a sequence of paths to yum requirements yaml files to
            be installed
        python_version (:obj:`str`): The version of Python to build for
            (<major>.<minor>), default: "3.7"
        zipped_prefix (:obj:`Optional[pathlib.Path`]): If given, a path to prepend to
            all files in the package when zipping
        force (:obj:`bool`): Build the package even if a pre-built version already
            exists.

    Raises:
        :obj:`BaseException`: If anything goes wrong
    """
    zipfile = build / "package.zip"
    build_info = build / "build-info.json"
    package = build / "package"

    # Make sure requirements and yum_requirements are lists
    if requirements is None:
        requirements = []
    elif isinstance(requirements, Path):
        requirements = [requirements]

    if yum_requirements is None:
        yum_requirements = []
    elif isinstance(yum_requirements, Path):
        yum_requirements = [yum_requirements]

    try:
        info = {
            "files": {str(f): _get_last_modified_timestamp(f) for f in files},
            "requirements": {
                str(f): _get_last_modified_timestamp(f) for f in requirements
            },
            "yum_requirements": {
                str(f): _get_last_modified_timestamp(f) for f in yum_requirements
            },
            "zipped_prefix": str(zipped_prefix),
        }

        if build_info.exists():
            with build_info.open("rb") as stream:
                old_info = json.load(stream)
        else:
            old_info = {}

        # Just return the zipfile path, if there is no new work to do on it
        if not force and info == old_info:
            return zipfile

        # Copy the included files into the package dir
        copy_included_files(build, build_info, info, package, *files)

        # Process requirements files if they exist
        if requirements or yum_requirements:
            env = build / "package-env"
            process_requirements(
                requirements, yum_requirements, package, env, python_version
            )

        # Zip it all up
        zip_package(zipfile, package, zipped_prefix=zipped_prefix)
    except BaseException:
        # If _anything_ goes wrong, we need to rebuild, so kill
        # build_info.
        logging.error("Build Package Failed. See Traceback for Error")
        if build_info.exists():
            build_info.unlink()
        raise

    return zipfile


def _get_last_modified_timestamp(path: Path):
    """
    Return the last modified time of the file or directory pointed to by path

    Args:
        path (:obj:`pathlib.Path`): The file/directory to get the last modified time
            from.
    """
    last_modified = int(path.stat().st_mtime)

    if path.is_dir():
        directory_last_modified = max(
            (_get_last_modified_timestamp(p) for p in path.iterdir()), default=None
        )

        if directory_last_modified is not None:
            last_modified = max(last_modified, directory_last_modified)

    return last_modified


def copy_included_files(
    build_path: Path, build_info: Path, info: dict, package_path: Path, *files: Path
):
    """
    Copy the files and dirs pointed to by `*files` into the build_path

    Args:
        build_path (:obj:`pathlib.Path`): The main build directory
        build_info (:obj:`pathlib.Path`): The path to the build-info.json file
        info (:obj:`dict`): The build-info
        package_path (:obj:`pathlib.Path`): The path to the resulting package directory
        *files (:obj:`pathlib.Path`): The files/dirs to copy
    """
    # Make sure the main build directory is created
    build_path.mkdir(parents=True, exist_ok=True)
    with build_info.open("w") as stream:
        json.dump(info, stream)

    # Delete the package path if it exists, and recreate it
    if package_path.exists():
        rmtree(package_path)
    package_path.mkdir()

    # For each file or dir, copy it into the package_path.
    for path in files:
        destination = package_path / path.name
        if path.is_dir():
            copytree(path, destination)
        else:
            copy2(path, destination)


def process_requirements(
    requirements: Sequence[Path],
    yum_requirements: Sequence[Path],
    package_path: Path,
    env: Path,
    python_version: str = "3.7",
):
    """
    Using pip, install python requirements into a docker instance

    Args:
        requirements (:obj:`Sequence[pathlib.Path]`): Paths to requirements files to
            install
        yum_requirements (:obj:`Sequence[pathlib.Path]`): Paths to yum requirements yaml
            files to install
        package_path (:obj:`pathlib.Path`): Path to resulting package dir
        env (:obj:`pathlib.Path`): Path to docker environment to install packages to
        python_version (:obj:`str`): The version of Python to build for
            (<major>.<minor>), default: "3.7"

    Raises:
        FileNotFoundError: If no files are copied after pip installation
    """
    client = docker.APIClient()
    container = "plz-container"
    build_docker_image(client, python_version)
    start_docker_container(client, container, env)

    try:
        # pip install all requirements files
        for r_file in requirements:
            with r_file.open() as f:
                for line in f.readlines():
                    dep = line.strip()
                    pip_install(client, container, dep)

        # Install epel from amazon-linux-extras if there are yum_requirements and the
        # Lambda runtime for the specified python version is "amazonlinux:2".
        #
        # As of python 3.8, the AWS Lambda runtime has switched to "amazonlinux:2"
        # see https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtimes.html
        # Note: The "amazonlinux" runtime used for older python versions does not
        # contain amazon-linux-extras
        version_tuple = tuple(map(int, python_version.split(".", 1)))
        if yum_requirements and version_tuple >= (3, 8):
            run_docker_cmd(
                client,
                container,
                # Note: If we don't set the python version to python2 then the epel
                # install will fail
                ["env", "PYTHON=python2", "amazon-linux-extras", "install", "epel"],
            )

        # yum install all yum_requirements
        for y_file in yum_requirements:
            with y_file.open() as f:
                data = yaml.safe_load(f)
                for key in data:
                    paths = list(map(Path, data[key]))
                    yum_install(client, container, key, paths)

    finally:
        stop_docker_container(client, container)

    # Copy the libs to the package
    has_copied = False
    for path in env.iterdir():
        destination = package_path / path.name
        if not destination.exists():
            logging.debug(f"Copying {destination}")
            has_copied = True
            if path.is_dir():
                copytree(path, destination)
            else:
                copy2(path, destination)

    # If we haven't copied any files, raise an error because if we've installed
    # something from a requirements.txt file, then we should have copied some files over
    if not has_copied:
        logging.error("Error: No files were copied after requirements processing.")
        raise FileNotFoundError()


def zip_package(
    zipfile_path: Path, package_path: Path, zipped_prefix: Optional[Path] = None
):
    """
    Zip up files/dirs and python packages

    Args:
        zipfile_path (:obj:`pathlib.Path`): The path to the zipfile you want to make
        package_path (:obj:`pathlib.Path`): The path to the package to be zipped
        zipped_prefix (:obj:`Optional[pathlib.Path]`): A path to prefix non dirs in the
            resulting zip file
    """
    # Delete unnecessary dirs
    for cache_dir in package_path.glob("**/__pycache__"):
        rmtree(cache_dir)

    for dist_dir in package_path.glob("**/*.dist-info"):
        rmtree(dist_dir)

    with ZipFile(zipfile_path, "w") as z:
        directories = [package_path]

        while directories:
            directory = directories.pop()

            for path in directory.iterdir():
                if path.is_dir():
                    directories.append(path)
                else:
                    destination = path.relative_to(package_path)
                    if zipped_prefix:
                        destination = zipped_prefix / destination
                    logging.debug(f"Archiving {path} to {destination}")
                    z.write(path, destination)
