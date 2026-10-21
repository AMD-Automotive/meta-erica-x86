# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""One-shot kernel-module build + install.

The driver sources are shipped with the app under ``drivers/`` (see
``drivers/README.md``). On first run this check builds and installs them via the
project's ``make`` / ``make install`` flow, then drops a marker file so it never
runs the build again. Subsequent runs short-circuit to OK.

This is modelled as a "check" so it slots into the same ordered pipeline as the
verification checks and reports progress/errors through the same UI, but its
real job is a side effect (build + install).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import Check, CheckResult

_REMEDIATION = (
    "The kernel module could not be built/installed. Check that kernel headers "
    "for the running kernel and build-essential are present on the target."
)


class DriverInstallCheck(Check):
    key = "drivers"
    title = "Driver build/install"
    critical = True

    def run(self) -> CheckResult:
        source_dir = Path(self.config.get("source_dir", "drivers")).expanduser()
        marker = Path(self.config.get("marker", "/var/lib/welcome-gui/drivers-installed"))
        build_targets = self.config.get("build_targets", ["all"]) or []
        install_cmd = self.config.get("install_command", "make install")
        force = bool(self.config.get("force", False))

        if marker.exists() and not force:
            return self.ok(f"Already installed (marker {marker})")

        if not source_dir.is_dir():
            return self.skip(f"No driver sources at {source_dir}")
        # A bare README-only drop counts as "nothing to build yet".
        sources = [p for p in source_dir.iterdir() if p.suffix in {".c", ".h"} or p.name == "Makefile"]
        if not any(p.name == "Makefile" for p in sources):
            return self.skip(f"No Makefile in {source_dir} — nothing to build yet")

        log: list[str] = []
        build_dir: Path | None = None
        try:
            # Build in a private, writable scratch copy of the sources. The GUI
            # runs as an unprivileged user, but kbuild writes its .o/.mod/.ko
            # artifacts into the build directory — and the deployed source_dir
            # (e.g. /opt/welcome-gui/drivers) is root-owned, so an in-place build
            # fails with "Permission denied". Copying to a temp dir keeps the
            # build writable and leaves the read-only app payload untouched.
            build_dir = Path(tempfile.mkdtemp(prefix="mdb5-build-"))
            src_copy = build_dir / "src"
            shutil.copytree(source_dir, src_copy)
            for target in build_targets:
                self._run(["make", str(target)], src_copy, log)
            # install_command may be multi-word (e.g. "sudo make install").
            self._run(install_cmd.split(), src_copy, log)
            # Refresh module dependencies so the module resolves by name.
            self._run(["depmod", "-a"], src_copy, log, check=False)
            # NOTE: the module is intentionally NOT loaded here. The mdb5_v4l2
            # driver latches its channel layout from the endpoint BRAM mailbox at
            # probe time and never re-syncs, so it must only be loaded on demand
            # (by the e2e orchestrator, after ep_proxy has stamped the mailbox),
            # never auto-loaded. Boot auto-load is suppressed by the
            # blacklist drop-in installed with the OS image; the kmod check below
            # verifies the built module by name without loading it.
        except subprocess.CalledProcessError as exc:
            return self.fail(
                f"Build/install failed (exit {exc.returncode})",
                detail="\n".join(log),
                remediation=self.config.get("remediation", _REMEDIATION),
            )
        finally:
            if build_dir is not None:
                shutil.rmtree(build_dir, ignore_errors=True)

        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("installed\n", encoding="ascii")
        return self.ok("Built and installed driver(s)", detail="\n".join(log[-20:]))

    @staticmethod
    def _run(cmd: list[str], cwd: Path, log: list[str], check: bool = True) -> None:
        log.append(f"$ {' '.join(cmd)}  (cwd={cwd})")
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            env={**os.environ, "LC_ALL": "C"},
        )
        if proc.stdout:
            log.append(proc.stdout.rstrip())
        if proc.stderr:
            log.append(proc.stderr.rstrip())
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)
