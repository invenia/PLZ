"""
Build a package
"""
import json
import subprocess
from pathlib import Path
from shutil import copy2, rmtree
from typing import Optional, Sequence, Set, Union
from zipfile import ZipFile

PYTHON_VERSION = "3.6"


__all__ = ["build_package"]


def build_package(
    build: Path,
    *files: Path,
    requirements: Optional[Union[Path, Sequence[Path]]] = None,
    version: str = PYTHON_VERSION,
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
        version (str): The python version to use.
        force (bool): Build the package even if a pre-built version
            already exists.
    """
    zipfile = build / "package.zip"

    if requirements is None:
        requirements = ()
    elif isinstance(requirements, Path):
        requirements = (requirements,)

    build_info = build / "build-info.json"

    try:
        info = {
            "files": {str(file): int(file.stat().st_mtime) for file in files},
            "requirements": {
                str(file): int(file.stat().st_mtime) for file in requirements
            },
            "version": version,
        }

        if build_info.exists():
            with build_info.open("rb") as stream:
                old_info = json.load(stream)
        else:
            old_info = {}

        if force or info != old_info:
            python = f"python{version}"
            package = build / "package"

            build.mkdir(parents=True, exist_ok=True)
            with build_info.open("w") as stream:
                json.dump(info, stream)

            if package.exists():
                rmtree(package)

            package.mkdir()

            for path in files:
                destination = package / path.name

                if path.is_dir():
                    subprocess.run(("cp", "-Rn", f"{path}", str(destination)))
                else:
                    copy2(str(path), str(destination))

            if requirements:
                env = build / "package-env"

                subprocess.run(("virtualenv", "--clear", f"--python={python}", env))

                # Debian-based distros put packages in dist-packages,
                # on linux there may be a separate lib64 directory.
                package_directories = {
                    env / lib / python / packages
                    for lib in ("lib", "lib64")
                    for packages in ("site-packages", "dist-packages")
                }

                # Apparently packages can also go here on our CI system
                package_directories = package_directories.union(
                    {env / lib / python for lib in ("lib", "lib64")}
                )

                existing: Set[Path] = set()

                # package directories will have some additional files in
                # them (e.g, pip) that don't need to be copied into our
                # package.
                for directory in package_directories:
                    if directory.exists():
                        existing = existing.union(directory.iterdir())

                if requirements:
                    pip = env / "bin" / "pip"
                    subprocess.run((pip, "install", "-r", *map(str, requirements)))

                for directory in package_directories:
                    if directory.exists():
                        for path in directory.iterdir():
                            # Skip things we know we don't need to add.
                            # These are separate because black and
                            # flake8 can't agree on where an or should
                            # go.
                            if path in package_directories or path in existing:
                                continue
                            if path.name == "__pycache__":
                                continue
                            if path.suffix == ".dist-info":
                                continue

                            destination = package / path.name

                            if not destination.exists():
                                if path.is_dir():
                                    subprocess.run(
                                        ("cp", "-Rn", f"{path}", str(destination))
                                    )
                                else:
                                    copy2(str(path), str(destination))

            with ZipFile(zipfile, "w") as z:
                directories = [package]

                while directories:
                    directory = directories.pop()

                    for path in directory.iterdir():
                        if path.is_dir():
                            if path.name != "__pycache__":
                                directories.append(path)
                        else:
                            z.write(path, path.relative_to(package))
    except BaseException:
        # If _anything_ goes wrong, we need to rebuild, so kill
        # build_info.
        if build_info.exists():
            build_info.unlink()

        raise

    return zipfile
