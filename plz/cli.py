"""
Command-line interface for PLZ.
"""
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Optional, Sequence

from plz.build import build_package


def parse_args(args: Optional[Sequence[str]] = None):
    """
    Parse command line arguments for PLZ

    Args:
        args (Optional[Sequence[str]]): The command-line args.
    """
    parser = ArgumentParser(description="plz - Package a python script for AWS Lambda.")
    parser.add_argument(
        "-r",
        "--requirements",
        type=Path,
        action="append",
        default=[],
        help=(
            "Path to a requirements file for the package. "
            "Can be supplied multiple times."
        ),
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
        "--zipped-prefix",
        default=None,
        type=Path,
        help="path to prepend to all files in the package when zipping",
    )
    parser.add_argument(
        "--force", action="store_true", help="Build even if a matching package exists."
    )

    logger_group_parent = parser.add_argument_group(
        title="logging arguments",
        description="Control what log level the log outputs (default: logger.ERROR)",
    )
    logger_group = logger_group_parent.add_mutually_exclusive_group()

    logger_group.add_argument(
        "-d",
        "--debug",
        dest="log_level",
        action="store_const",
        const=logging.DEBUG,
        default=logging.ERROR,
        help="Set log level to DEBUG",
    )
    logger_group.add_argument(
        "-v",
        "--verbose",
        dest="log_level",
        action="store_const",
        const=logging.INFO,
        default=logging.ERROR,
        help="Log at info level",
    )

    return parser.parse_args(args)


def main(args: Optional[Sequence[str]] = None):
    """
    Run PLZ from the command line.

    Args:
        args (Optional[Sequence[str]]): The command-line args.
    """
    args = parse_args(args=args)
    logging.getLogger().setLevel(args.log_level)

    package = build_package(
        args.build, *args.files, requirements=args.requirements, force=args.force
    )

    print(package)


if __name__ == "__main__":
    main()
