# SPDX-License-Identifier: MIT
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# Contributors: BW
# Maintainer: BW
# Build the camera_dmabuf zero-copy display helper. Uses only kernel uAPI
# headers (no libdrm), matching the driver's ship-source-and-build model.

CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra
LDFLAGS ?=
LDLIBS  += -lm

all: camera_dmabuf

camera_dmabuf: camera_dmabuf.c
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $< $(LDLIBS)

clean:
	rm -f camera_dmabuf

.PHONY: all clean
