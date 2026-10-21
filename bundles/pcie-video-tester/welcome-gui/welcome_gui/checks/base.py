# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Base types for the modular check framework.

A *check* is a small, self-contained, reusable unit of verification. Each check
reports a :class:`CheckResult`. Checks are deliberately decoupled from the UI and
from each other so the same framework can back other example apps.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(Enum):
    """Outcome of a check."""

    OK = "ok"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class CheckResult:
    """The result of running a single check.

    Attributes:
        name: Human-readable check name (shown in the UI).
        status: OK / FAIL / SKIP.
        summary: One-line outcome (e.g. "Found 10ee:9038 at 0000:01:00.0").
        detail: Optional multi-line diagnostic text.
        remediation: Optional guidance shown to the user on failure.
        info: Ordered key/value pairs to surface on the welcome screen
            (e.g. {"OSPI Configuration": "RAVE2 ECP - AMD Versal Gen 2"}).
    """

    name: str
    status: Status
    summary: str
    detail: str = ""
    remediation: str = ""
    info: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is Status.OK


class Check(abc.ABC):
    """Abstract base for all checks.

    Subclasses set :attr:`key`/:attr:`title` and implement :meth:`run`. A check
    is configured from the matching block in ``welcome.yaml`` (passed as a dict).
    """

    #: Stable identifier matching the config block key.
    key: str = "check"
    #: Human-readable title for the UI.
    title: str = "Check"
    #: If True, a FAIL aborts the remaining pipeline and routes to the error UI.
    critical: bool = True

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = config or {}

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    # Convenience builders so subclasses stay terse. ----------------------
    def ok(self, summary: str, detail: str = "",
           info: dict[str, str] | None = None) -> CheckResult:
        return CheckResult(self.title, Status.OK, summary, detail, info=info or {})

    def fail(self, summary: str, detail: str = "", remediation: str = "",
             info: dict[str, str] | None = None) -> CheckResult:
        return CheckResult(self.title, Status.FAIL, summary, detail, remediation,
                           info=info or {})

    def skip(self, summary: str, detail: str = "") -> CheckResult:
        return CheckResult(self.title, Status.SKIP, summary, detail)

    @abc.abstractmethod
    def run(self) -> CheckResult:
        """Execute the check. Must not raise — convert errors to a FAIL result."""
        raise NotImplementedError
