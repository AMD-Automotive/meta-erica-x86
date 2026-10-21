# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

# Enable grub-mkfont in grub-native so grub-efi can generate .pf2 fonts
DEPENDS:append:class-native = " freetype-native"
EXTRA_OECONF:append:class-native = " --enable-grub-mkfont"
