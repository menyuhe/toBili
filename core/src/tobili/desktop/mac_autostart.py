"""Current-user launch agent. A normal Quit must not restart the application."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path


def location(name):
    label = "cn.4wdo." + name.lower().replace(" ", "-") + ".desktop"
    return label, Path.home() / "Library/LaunchAgents" / (label + ".plist")


def enabled(name):
    return location(name)[1].is_file()


def configure(on, name, exe=None):
    label, path = location(name)
    domain = f"gui/{os.getuid()}"
    if not on:
        path.unlink(missing_ok=True)
        subprocess.run(["/bin/launchctl", "disable", domain + "/" + label], capture_output=True)
        return
    args = [str(exe or sys.executable)]
    if exe is None and not getattr(sys, "frozen", False):
        args += ["-m", "tobili.desktop"]
    args += ["--tray"]
    value = dict(
        Label=label,
        ProgramArguments=args,
        RunAtLoad=True,
        KeepAlive={"SuccessfulExit": False},
        ThrottleInterval=20,
        ProcessType="Interactive",
        WorkingDirectory=str(Path.home()),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        plistlib.dump(value, stream)
    temporary.chmod(0o600)
    temporary.replace(path)
    subprocess.run(["/bin/launchctl", "enable", domain + "/" + label], capture_output=True)
    # RunAtLoad takes effect at the next login. Do not start a second tray while configuring.
