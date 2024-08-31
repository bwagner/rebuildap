#!/usr/bin/env python

import runpy
import shutil
import textwrap
from pathlib import Path


def delete_dir(path: Path):
    if not path:
        print(f"Error: '{path}' invalid.")
        return False
    if not path.is_dir():
        print(f"Error: '{path}' not a dir.")
        return False
    stat = path.stat()
    if not stat:
        print(f"Error: can't read '{path}'.")
        return False
    # Try to remove the tree; if it fails, throw an error using try...except.
    try:
        shutil.rmtree(path)
        return True
    except OSError as e:
        print(f"Error: {e.filename} - {e.strerror}.")

    return False


def setup_dist_dir(dist_dir):
    path = Path(dist_dir)
    if not delete_dir(path):
        print(f"deleting '{path}' failed, exiting.")
        return
    Path.mkdir(path)


def build():
    runpy.run_module(mod_name="build", run_name="__main__", alter_sys=True)


def main():
    """
    Run this script at the top level of the project.
    It makes sure the "dist" directory is empty.
    It builds the package in the "dist" directory.
    It instructs how to proceed.
    """
    dist_dir = "dist"
    print(f"setting up {dist_dir} directory")
    setup_dist_dir(dist_dir)
    package = Path.cwd().name
    print(
        "Hopefully, you set the version number. If you didn't, call hatch version micro/minor/major"
    )
    print("see https://hatch.pypa.io/latest/version/")
    print(f"building {package}")
    build()
    dist = Path(dist_dir)
    wheel = str(next(dist.glob("*.whl")))
    print(
        textwrap.dedent(
            f"""
            now test by (creating) activating a virtual env
            pip uninstall -y {package}
            pip install -q {wheel}
            test the package {package} interactively
            pip uninstall -y {package}
            deactivate
            if you're happy:
            python -m twine upload {dist_dir}/*
            """
        )
    )


if __name__ == "__main__":
    main()
