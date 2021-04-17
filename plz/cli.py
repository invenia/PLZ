"""
Command-line interface for PLZ.
"""
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Optional, Sequence

from plz.build import build_package
from plz.plzdocker import DEFAULT_PYTHON


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
        "-s",
        "--system-requirements",
        type=Path,
        default=None,
        help="Path to a system requirements yaml file for the package. ",
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
        "-p",
        "--python-version",
        default=DEFAULT_PYTHON,
        help="Version of Python to build with/for (<major>.<minor>)",
    )
    parser.add_argument(
        "--zipped-prefix",
        default=None,
        type=Path,
        help="path to prepend to all files in the package when zipping",
    )
    parser.add_argument(
        "--reinstall-python",
        action="store_true",
        help="Force python requirements to reinstall",
    )
    parser.add_argument(
        "--reinstall-system",
        action="store_true",
        help="Force system requirements to reinstall",
    )
    parser.add_argument(
        "--rebuild-image",
        action="store_true",
        help="Rebuild the base docker image",
    )
    parser.add_argument(
        "--rezip",
        action="store_true",
        help="Rebuild the actual zip file even if no files have changed",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Force all stages to be recreated "
            "(same as --reinstall-python --reinstall-system --rebuild-image --rezip)"
        ),
    )
    parser.add_argument(
        "--no-secrets",
        action="store_true",
        help="Don't bind .ssh keys or .pypi directory to docker ccontainer",
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
    parsed_args = parse_args(args=args)
    logging.getLogger().setLevel(parsed_args.log_level)

    package = build_package(
        parsed_args.build,
        *parsed_args.files,
        requirements=parsed_args.requirements,
        system_requirements=parsed_args.system_requirements,
        python_version=parsed_args.python_version,
        reinstall_python=parsed_args.reinstall_python,
        reinstall_system=parsed_args.reinstall_system,
        rebuild_image=parsed_args.rebuild_image,
        rezip=parsed_args.rezip,
        force=parsed_args.force,
        no_secrets=parsed_args.no_secrets,
    )

    print(package)


if __name__ == "__main__":
    main()
