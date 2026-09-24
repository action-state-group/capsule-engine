# SPDX-License-Identifier: Apache-2.0
"""[fix-emit-wheel-packaging-schemas]: prove the schemas actually installed
into a wheel, not just an editable checkout.

The bug this guards: ``capsule_engine.packs.contract_validate`` and
``capsule_engine.report.result`` both load a vendored JSON Schema file off
disk via ``Path(__file__).resolve().parents[N]``. Every other test in this
repo runs against the editable install (``pip install -e .``, same as CI's
``install`` step), where the repo checkout is right there on disk regardless
of what ``[tool.setuptools.package-data]`` declares -- so a schema left out
of package-data, or a path computed as if it still lived at the project
root, passes every editable-install test and still 404s in production. Real
build, real non-editable install, real subprocess with no repo checkout on
``sys.path`` -- the only thing that actually exercises what ships.
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def _build_wheel(dest: Path) -> Path:
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(dest)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = sorted(dest.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one built wheel, found {wheels}"
    return wheels[0]


def test_built_wheel_contains_the_vendored_schemas(tmp_path):
    """Package-data half of the bug: the files must be IN the wheel at all."""
    wheel = _build_wheel(tmp_path / "dist")
    names = zipfile.ZipFile(wheel).namelist()
    assert "capsule_engine/schemas/evidence-contract-v0.json" in names
    assert "capsule_engine/schemas/vendor/evidence-result-v0.json" in names
    assert "capsule_engine/schemas/vendor/epistemic-types.json" in names


def test_installed_wheel_loads_schemas_with_no_repo_checkout_on_sys_path(tmp_path):
    """Path-resolution half of the bug: even with the files present, code that
    assumes a project-root ``schemas/`` dir (``parents[2]`` from a module two
    levels inside the package) breaks once the package is unpacked into an
    arbitrary install target instead of living inside the source checkout.
    ``--no-deps --target`` installs ONLY this package's own files -- no
    repo directory, no sibling project files, nothing but what the wheel
    declared -- so a FileNotFoundError here is exactly what production sees."""
    wheel = _build_wheel(tmp_path / "dist")
    install_dir = tmp_path / "install"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(install_dir), str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )

    script = (
        "import capsule_engine.packs.contract_validate as cv\n"
        "import capsule_engine.report.result as r\n"
        "contract_schema = cv.load_schema()\n"
        "result_schema = r.load_schema()\n"
        "assert contract_schema['title'] == 'Evidence Contract v0', contract_schema.get('title')\n"
        "assert result_schema['title'] == 'Evidence Result v0', result_schema.get('title')\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,  # no repo checkout in the cwd or on sys.path
        env={"PYTHONPATH": str(install_dir)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert proc.stdout.strip() == "OK"
