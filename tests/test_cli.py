import logging
from pathlib import Path


def test_parse_args():
    from plz.cli import parse_args

    args = parse_args(
        ["image", "-r", "requirements.txt", "-p", "3.8", "test1.py", "testpath"]
    )
    assert args.build == Path("./build")
    assert args.files == [Path("test1.py"), Path("testpath")]
    assert not args.rebuild
    assert args.requirements == [Path("requirements.txt")]
    assert args.python_version == "3.8"
    assert args.log_level == logging.ERROR


def test_main(tmpdir):
    from plz.cli import main

    build_path = Path(tmpdir / "build")
    build_path.mkdir()

    file_path = Path(tmpdir / "test.py")
    with file_path.open("w") as f:
        f.write("#test")

    main(["zip", "--build", str(build_path), "--", str(file_path)])

    assert (build_path / "package.zip").exists()
