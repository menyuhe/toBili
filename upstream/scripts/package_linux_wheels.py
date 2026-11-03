"""Check target markers and package downloaded official Linux CPython 3.10 wheels."""
from email.parser import Parser
from pathlib import Path
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
import zipfile

root = Path(__file__).resolve().parents[2]
wheels = sorted((root / "test-runtime/tobili-linux-wheels").glob("*.whl"))
available = {canonicalize_name(parse_wheel_filename(p.name)[0]): parse_wheel_filename(p.name)[1] for p in wheels}
env = {**default_environment(), "python_version": "3.10", "python_full_version": "3.10.12",
       "implementation_version": "3.10.12", "sys_platform": "linux", "os_name": "posix",
       "platform_system": "Linux", "platform_machine": "x86_64"}
extras = {"uvicorn": ["standard"], "psycopg": ["binary"]}
missing = []
for wheel in wheels:
    package = canonicalize_name(parse_wheel_filename(wheel.name)[0])
    with zipfile.ZipFile(wheel) as archive:
        metadata = Parser().parsestr(archive.read(next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))).decode())
    for value in metadata.get_all("Requires-Dist", []):
        requirement = Requirement(value)
        if requirement.marker and not any(requirement.marker.evaluate({**env, "extra": extra}) for extra in ["", *extras.get(package, [])]):
            continue
        key = canonicalize_name(requirement.name)
        if key not in available or available[key] not in requirement.specifier:
            missing.append(f"{package}: {value}")
if missing:
    raise SystemExit("Missing target dependencies: " + str(missing))
output = root / "dist/ToBili-Linux-Wheels.zip"
with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
    for wheel in wheels:
        archive.write(wheel, "wheels/" + wheel.name)
print(f"Validated {len(wheels)} Linux wheels; archive: {output}")
