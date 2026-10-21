# SPDX-License-Identifier: GPL-2.0-only
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# Contributors: BW, KC
# Maintainer: BW
# Makefile for MDB5 V4L2 video bridge host driver
#
# Build:   make
# Install: make install
# Clean:   make clean

obj-m := mdb5_v4l2.o
mdb5_v4l2-objs := mdb5_pci.o mdb5_channel.o mdb5_dma.o

KDIR ?= /lib/modules/$(shell uname -r)/build

.PHONY: all clean install

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean

install:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules_install
	depmod -a
