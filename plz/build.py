"""
Build a package
"""
import json
import logging
import subprocess
from pathlib import Path
from shutil import copy2, rmtree
from typing import Optional, Sequence, Union
from zipfile import ZipFile

import docker

from .docker import (
    build_docker_image,
    pip_install,
    start_docker_container,
    stop_docker_container,
)

__all__ = ["build_package"]


def build_package(
    build: Path,
    *files: Path,
    requirements: Optional[Union[Path, Sequence[Path]]] = None,
    zipped_prefix: Optional[Path] = None,
    force: bool = False,
) -> Path:
    """
    Build a python package.

    Args:
        build (Path): The directory to build the package in.
        *files (Path): Any number of files to include. Directories will
            be copied as subdirectories.
        requirements (Optional[Union[Path, Sequence[Path]]]): If given,
            a path to or a sequence of paths to requirements files to be
            installed.
        zipped_prefix (Optional[Path]): If given, a path to prepend to
            all files in the package when zipping
        force (bool): Build the package even if a pre-built version
            already exists.
    """
    zipfile = build / "package.zip"
    build_info = build / "build-info.json"
    package = build / "package"

    try:
        info = {
            "files": {str(file): int(file.stat().st_mtime) for file in files},
            "requirements": {
                str(file): int(file.stat().st_mtime) for file in requirements
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
        if requirements:
            process_requirements(requirements, build, package)

        # Zip it all up
        zip_package(zipfile, package, zipped_prefix=zipped_prefix)
    except BaseException:
        # If _anything_ goes wrong, we need to rebuild, so kill
        # build_info.
        if build_info.exists():
            build_info.unlink()

        raise

    return zipfile


def copy_included_files(
    build_path: Path,
    build_info: Path,
    info: dict,
    package_path: Path,
    *files: Path,
):
    build_path.mkdir(parents=True, exist_ok=True)
    with build_info.open("w") as stream:
        json.dump(info, stream)

    if package_path.exists():
        rmtree(package_path)

    package_path.mkdir()

    for path in files:
        destination = package_path / path.name

        if path.is_dir():
            subprocess.run(("cp", "-Rn", f"{path}", str(destination)))
        else:
            copy2(str(path), str(destination))


def process_requirements(
    requirements: Union[Path, Sequence[Path]],
    build_path: Path,
    package_path: Path,
):
    client = docker.APIClient()
    env = build_path / "package-env"
    container = "plz-container"
    build_docker_image(client, env)
    start_docker_container(client, container, env)

    # pip install all requirements files
    for r_file in requirements:
        with r_file.open() as f:
            for line in f.readlines():
                dep = line.strip()
                pip_install(client, container, dep)

    stop_docker_container(client, container)

    # Remove all unnecessary __pycache__ dirs
    for cache_dir in env.glob("**/__pycache__"):
        rmtree(cache_dir)

    # Remove all unnecessary *.dist-info dirs
    for dist_dir in env.glob("**/*.dist-info"):
        rmtree(dist_dir)

    # Copy the python libs to the package
    for path in env.iterdir():
        destination = package_path / path.name
        if not destination.exists():
            logging.debug(f"Copying {destination}")
            if path.is_dir():
                subprocess.run(("cp", "-Rn", f"{path}", str(destination)))
            else:
                copy2(str(path), str(destination))


def zip_package(
    zipfile_path: Path,
    package_path: Path,
    zipped_prefix: Optional[Path] = None
):
    with ZipFile(zipfile_path, "w") as z:
        directories = [package_path]

        while directories:
            directory = directories.pop()

            for path in directory.iterdir():
                if path.is_dir():
                    if path.name != "__pycache__":
                        directories.append(path)
                else:
                    destination = path.relative_to(package_path)
                    if zipped_prefix:
                        destination = zipped_prefix / destination
                    logging.debug(f"Archiving {path} to {destination}")
                    z.write(path, destination)
