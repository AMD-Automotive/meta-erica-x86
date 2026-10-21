# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Check registry + threaded runner.

The :class:`CheckRunner` executes the configured pipeline on a background
``QThread`` and emits Qt signals so the UI can update live without blocking.
Critical failures short-circuit the remaining pipeline.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from .checks.base import Check, CheckResult, Status
from .checks.drivers import DriverInstallCheck
from .checks.kmod import KmodCheck
from .checks.pcie import PcieCheck
from .checks.serial_versal import VersalSerialCheck
#: Maps config keys to check classes. Extend this to add new check types.
REGISTRY: dict[str, type[Check]] = {
    DriverInstallCheck.key: DriverInstallCheck,
    PcieCheck.key: PcieCheck,
    VersalSerialCheck.key: VersalSerialCheck,
    KmodCheck.key: KmodCheck,
}


def build_pipeline(config: dict[str, Any]) -> list[Check]:
    """Instantiate the ordered, enabled checks named in ``config['pipeline']``."""
    checks: list[Check] = []
    checks_cfg = config.get("checks", {})
    for key in config.get("pipeline", []):
        cls = REGISTRY.get(key)
        if cls is None:
            continue
        check = cls(checks_cfg.get(key, {}))
        if check.enabled:
            checks.append(check)
    return checks


class CheckRunner(QThread):
    """Runs a list of checks on a worker thread.

    Implemented as a :class:`QThread` subclass: :meth:`run` executes on the
    worker thread and emits signals, which Qt delivers to GUI slots via queued
    connections. Critical failures short-circuit the remaining pipeline.

    Signals:
        check_started(str): emitted with the check title before it runs.
        check_finished(object): emitted with the :class:`CheckResult`.
        finished_all(bool, list): emitted once with overall success + results.
    """

    check_started = Signal(str)
    check_finished = Signal(object)
    finished_all = Signal(bool, list)

    def __init__(self, checks: list[Check], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._checks = checks

    def stop(self) -> None:
        self.quit()
        self.wait()

    def run(self) -> None:
        results: list[CheckResult] = []
        overall_ok = True
        for check in self._checks:
            self.check_started.emit(check.title)
            try:
                result = check.run()
            except Exception as exc:  # noqa: BLE001 — a check must never crash the app
                result = CheckResult(
                    check.title, Status.FAIL, f"Unexpected error: {exc}",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            results.append(result)
            self.check_finished.emit(result)

            if result.status is Status.FAIL and check.critical:
                overall_ok = False
                break

        self.finished_all.emit(overall_ok, results)
