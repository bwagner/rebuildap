#!/usr/bin/env python

import subprocess
import textwrap
from pathlib import Path


def run(cmd):
    print(f"$ {cmd}")
    subprocess.run(cmd, shell=True, check=True)


def main():
    project = Path.cwd().name
    dist_dir = Path("dist")

    print("👉 Checking version")
    print("   Hopefully, you've set the correct version.")
    print("   If not, use:  hatch version micro | minor | major")
    print("   See: https://hatch.pypa.io/latest/version/")

    print("\n🔨 Building package (wheel + sdist)...")
    run("uv build")

    wheels = list(dist_dir.glob("*.whl"))
    if not wheels:
        print("❌ No wheel file found in dist/")
        return
    wheel = wheels[0]

    print(f"\n📦 Found wheel: {wheel.name}")

    print(f"🚫 Uninstalling previous {project} tool (if installed)...")
    run(f"uv tool uninstall {project}")

    print("🔧 Installing new tool from wheel...")
    run(f"uv tool install {wheel}")

    print("🧹 Cleaning up .tar.gz (source distributions)...")
    for tarball in dist_dir.glob("*.tar.gz"):
        tarball.unlink()
        print(f"  Removed: {tarball.name}")

    print(
        textwrap.dedent(
            f"""
            ✅ Done.

            You can now run the tool from anywhere:
              {project} [...]

            If you want to test in a clean environment:

              uv venv .venv
              source .venv/bin/activate
              {project} [...]

            If you're happy with the result, you can upload:
              python -m twine upload dist/*

            """
        )
    )


if __name__ == "__main__":
    main()
