"""Create Kody's virtual environment using only the bundled wheels."""

from __future__ import annotations

import platform
import struct
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
WHEELS = ROOT / "wheels"
REQUIREMENTS = ROOT / "requirements.txt"
REQUIRED_PYTHON = (3, 15)


def fail(message: str) -> int:
    print(f"Setup stopped: {message}", file=sys.stderr)
    return 1


def main() -> int:
    if sys.version_info[:2] != REQUIRED_PYTHON:
        command = "py -3.15 init_environment.py" if sys.platform == "win32" else "python3.15 init_environment.py"
        return fail(
            f"Python 3.15 is required; this interpreter is {platform.python_version()}. "
            f"Install 64-bit Python 3.15, then run: {command}"
        )

    if struct.calcsize("P") * 8 != 64:
        return fail("64-bit Python 3.15 is required. Install it and rerun this script.")

    machine = platform.machine().lower()
    if sys.platform == "win32" and machine in ("amd64", "x86_64"):
        wheel_tag = "win_amd64"
        venv_python = VENV / "Scripts" / "python.exe"
    elif sys.platform == "linux" and machine in ("x86_64", "amd64"):
        wheel_tag = "manylinux"
        venv_python = VENV / "bin" / "python"
    else:
        return fail(f"No bundled wheels support {sys.platform} on {machine}.")

    if not REQUIREMENTS.is_file() or not WHEELS.is_dir():
        return fail("requirements.txt or the wheels directory is missing from the repository root.")
    if not any(wheel_tag in path.name for path in WHEELS.glob("*.whl")):
        return fail(f"The wheels directory has no {wheel_tag} wheels for this platform.")

    if VENV.exists() and not venv_python.is_file():
        return fail(f"{VENV} exists but has no {venv_python.name}; remove it and rerun setup.")

    if venv_python.is_file():
        version = subprocess.run(
            [str(venv_python), "-c", "import sys; print(*sys.version_info[:2])"],
            capture_output=True,
            text=True,
        )
        if version.returncode != 0 or version.stdout.strip() != "3 15":
            return fail(f"{VENV} uses a different Python version; remove it and rerun setup.")
    else:
        print(f"Creating {VENV} ...", flush=True)
        try:
            subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        except subprocess.CalledProcessError:
            return fail("Could not create the virtual environment. Check that Python's venv module is installed.")

    print("Installing dependencies from wheels (offline) ...", flush=True)
    try:
        subprocess.run(
            [
                str(venv_python), "-m", "pip", "install", "--no-index",
                "--find-links", str(WHEELS), "--requirement", str(REQUIREMENTS),
            ],
            check=True,
            cwd=ROOT,
        )
        subprocess.run([str(venv_python), "-m", "pip", "check"], check=True, cwd=ROOT)
    except subprocess.CalledProcessError:
        return fail("Dependency installation failed. Check that wheels contains compatible Python 3.15 wheels for this platform.")

    print(f"Environment ready. Start Kody with: {venv_python} {ROOT / 'main.py'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
