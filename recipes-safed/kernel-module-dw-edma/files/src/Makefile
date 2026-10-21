# SPDX-License-Identifier: GPL-2.0-or-later
#
# Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
#
# Author: AMD

ifeq ($(LINUX_SRC),)
  LINUX_SRC := /lib/modules/$(shell uname -r)/build
endif

CURRENT_DIR := $(dir $(abspath $(firstword $(MAKEFILE_LIST))))

all:
	make -C $(LINUX_SRC) M=$(CURRENT_DIR) modules

clean:
	make -C $(LINUX_SRC) M=$(CURRENT_DIR) clean
