"""
Command-line interface for PLZ.
"""
import re
from argparse import ArgumentParser
from pathlib import Path
from typing import Optional, Sequence

from plz.build import PYTHON_VERSION, build_package


def python_version(version: str) -> str:
    """
    Check a python version
    """
    if not re.match(r"^[2,3,4]\.\d+$", version):
        raise ValueError(version)

    return version


def main(input_args: Optional[Sequence[str]] = None):
    """
    Run PLZ from the command line.

    Args:
        input_args (Optional[Sequence[str]]): The command-line args.
    """
    parser = ArgumentParser(description="Package a python script for AWS Lambda.")
    parser.add_argument(
        "--requirements",
        "-r",
        type=Path,
        action="append",
        help=(
            "Path to a requirements file for the package. "
            "Can be supplied multiple times."
        ),
    )
    parser.add_argument(
        "--version",
        type=python_version,
        default=PYTHON_VERSION,
        help="The version of python the lambda runs on (default 3.6).",
    )
    parser.add_argument(
        "--build",
        type=Path,
        default=Path("./build"),
        help="Where to put the build directory for the package.",
    )
    parser.add_argument(
        "files",
        type=Path,
        nargs="*",
        help="Any number of files or directories to include in the package.",
    )
    parser.add_argument(
        "--force", action="store_true", help="Build even if a matching package exists."
    )

    args = parser.parse_args(input_args)

    package = build_package(
        args.build,
        *args.files,
        requirements=args.requirements,
        version=args.version,
        force=args.force,
    )

    print(package)


if __name__ == "__main__":
    main()
