from setuptools import find_packages, setup

from plz.version import __version__

TEST_DEPS = ["coverage", "pytest", "pytest-cov"]
DOCS_DEPS = [
    "sphinx",
    "sphinx-rtd-theme",
    "sphinx-autoapi",
    "recommonmark",
    "sphinxcontrib-runcmd",
]
CHECK_DEPS = ["isort", "flake8", "flake8-quotes", "pep8-naming", "black", "mypy"]
REQUIREMENTS = ["virtualenv"]

EXTRAS = {
    "test": TEST_DEPS,
    "docs": DOCS_DEPS,
    "check": CHECK_DEPS,
    "dev": TEST_DEPS + CHECK_DEPS + DOCS_DEPS,
}

setup(
    name="plz",
    version=__version__,
    description="Python Lambda Zipper",
    author="bcj",
    url="https://gitlab.invenia.ca/invenia/plz",
    packages=find_packages(exclude=["tests"]),
    install_requires=REQUIREMENTS,
    test_require=TEST_DEPS,
    extras_require=EXTRAS,
    entry_points={"console_scripts": ["plz = plz.cli:main"]},
)
