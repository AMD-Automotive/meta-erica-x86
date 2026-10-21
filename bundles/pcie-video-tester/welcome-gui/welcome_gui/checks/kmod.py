# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Kernel-module presence check (by name, without loading).

Confirms that the expected kernel module(s) are *available* — i.e. the built
``.ko`` is installed in the module tree — by searching
``/lib/modules/$(uname -r)`` for a file named ``<module>.ko`` (any compression
suffix). It deliberately does NOT load the module and does NOT require it to be
loaded: the mdb5_v4l2 driver must only be loaded on demand (by the e2e
orchestrator, after the endpoint proxy has stamped its mailbox), never at boot.

A module that happens to be loaded already (``/sys/module/<name>``) also counts
as present. The expected module names come from ``config/welcome.yaml``.
"""

from __future__ import annotations

import os
import platform

from .base import Check, CheckResult

_REMEDIATION = (
    "Expected kernel module(s) were not found in the module tree. Re-run the "
    "driver build/install step (delete the drivers marker or set force: true), "
    "or reboot into Buddy and re-install this application."
)


def _module_present(name: str) -> bool:
    """True if ``name``'s ``.ko`` exists on disk, or it is already loaded.

    Looks the module up *by name* in the running kernel's module tree. This
    reads directory entries only — it never inserts the module.
    """
    norm = name.replace("-", "_")
    # Already loaded counts as present (e.g. the e2e harness loaded it).
    if os.path.isdir(f"/sys/module/{norm}"):
        return True
    base = f"/lib/modules/{platform.release()}"
    # Module filenames use the same name with an optional compression suffix,
    # e.g. mdb5_v4l2.ko / mdb5_v4l2.ko.zst. Match either underscore/hyphen form.
    wanted = {f"{norm}.ko", f"{name}.ko"}
    for _root, _dirs, files in os.walk(base):
        for fname in files:
            stem = fname.split(".ko", 1)[0] + ".ko" if ".ko" in fname else fname
            if stem in wanted:
                return True
    return False


class KmodCheck(Check):
    key = "kmod"
    title = "Kernel modules"
    critical = True

    def run(self) -> CheckResult:
        expected = [str(m) for m in self.config.get("modules", [])]
        if not expected:
            return self.skip("No kernel modules configured")

        missing = [m for m in expected if not _module_present(m)]
        if not missing:
            return self.ok("Available: " + ", ".join(expected))
        return self.fail(
            "Missing module(s): " + ", ".join(missing),
            detail="Built .ko was not found in the kernel module tree.",
            remediation=self.config.get("remediation", _REMEDIATION),
        )

