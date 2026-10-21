# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

"""Versal control helpers (FTDI POR_B reset + PCIe rescan).

Vendored from the rave-menu app so the PCIe check can recover the Versal when it
is not present on the bus.
"""
