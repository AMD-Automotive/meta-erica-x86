# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: KC
# Maintainer: KC

GRUB_BUILDIN:append = "  multiboot multiboot2 font gfxterm gfxmenu png"
FILESEXTRAPATHS:prepend := "${THISDIR}/${PN}:"

# Fetch Liberation Sans TTFs directly (${datadir}/fonts is in SYSROOT_DIRS_IGNORE
# so liberation-fonts-native can't populate into our sysroot)
LIBERATION_FONTS_VER = "2.1.5"
SRC_URI:append = " \
    https://github.com/liberationfonts/liberation-fonts/files/7261482/liberation-fonts-ttf-${LIBERATION_FONTS_VER}.tar.gz;name=liberation-fonts \
    file://grub-weston-basic.cfg \
    file://splash.png \
    file://theme/theme.txt \
    file://theme/background.png \
    file://theme/select_c.png \
    file://theme/select_w.png \
    file://theme/select_e.png \
"
SRC_URI[liberation-fonts.sha256sum] = "7191c669bf38899f73a2094ed00f7b800553364f90e2637010a69c0e268f25d0"

do_deploy:append() {
    install -d ${DEPLOYDIR}/EFI/BOOT/x86_64-efi
    for modfile in ${D}${libdir}/grub/x86_64-efi/*.mod; do
        install -m 0644 $modfile ${DEPLOYDIR}/EFI/BOOT/x86_64-efi/
    done
    install -m 0644 ${S}/../grub-weston-basic.cfg ${DEPLOYDIR}
    install -m 0644 ${S}/../splash.png ${DEPLOYDIR}

    # Install theme directory
    install -d ${DEPLOYDIR}/theme
    install -m 0644 ${S}/../theme/theme.txt ${DEPLOYDIR}/theme/
    install -m 0644 ${S}/../theme/background.png ${DEPLOYDIR}/theme/
    install -m 0644 ${S}/../theme/select_c.png ${DEPLOYDIR}/theme/
    install -m 0644 ${S}/../theme/select_w.png ${DEPLOYDIR}/theme/
    install -m 0644 ${S}/../theme/select_e.png ${DEPLOYDIR}/theme/

    # Generate GRUB fonts from Liberation Sans TTFs fetched via SRC_URI
    FONT_DIR="${WORKDIR}/liberation-fonts-ttf-${LIBERATION_FONTS_VER}"
    FONT_TTF="$FONT_DIR/LiberationSans-Regular.ttf"
    FONT_BOLD_TTF="$FONT_DIR/LiberationSans-Bold.ttf"
    if [ -f "$FONT_TTF" ] && [ -f "$FONT_BOLD_TTF" ]; then
        grub-mkfont -s 12 -o ${DEPLOYDIR}/theme/DejaVuSans12.pf2 "$FONT_TTF"
        grub-mkfont -s 14 -o ${DEPLOYDIR}/theme/DejaVuSans14.pf2 "$FONT_TTF"
        grub-mkfont -s 16 -o ${DEPLOYDIR}/theme/DejaVuSans16.pf2 "$FONT_TTF"
        grub-mkfont -s 18 -o ${DEPLOYDIR}/theme/DejaVuSans18.pf2 "$FONT_TTF"
        grub-mkfont -s 18 -o ${DEPLOYDIR}/theme/DejaVuSansBold18.pf2 "$FONT_BOLD_TTF"
        grub-mkfont -s 20 -o ${DEPLOYDIR}/theme/DejaVuSansBold20.pf2 "$FONT_BOLD_TTF"
        grub-mkfont -s 16 -o ${DEPLOYDIR}/unicode.pf2 "$FONT_TTF"
    else
        bbfatal "Cannot find Liberation Sans TTF fonts in ${FONT_DIR}"
    fi
}
