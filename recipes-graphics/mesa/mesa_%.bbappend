# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

# Ensure gallium-llvm is enabled (this triggers GALLIUMDRIVERS_LLVM which includes radeonsi)
PACKAGECONFIG:append = " gallium-llvm r600 gallium gbm egl gles"
PACKAGECONFIG:append:class-target = " dri3"

# Enable VA-API backend so radeonsi exposes hardware video decode via VCN
PACKAGECONFIG:append:class-target = " va"

# Clear DRI drivers
DRIDRIVERS = ""