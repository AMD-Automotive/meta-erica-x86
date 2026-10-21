#!/bin/bash
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# SPDX-License-Identifier: MIT
# Contributors: BW
# Maintainer: BW

# Loads mdb5_v4l2 kernel module and dependencies
set -e

# --- Unload existing module ---
if lsmod | grep -q "^mdb5_v4l2"; then
    echo "Removing existing mdb5_v4l2..."
    sudo rmmod mdb5_v4l2
    sleep 1
fi

# --- Load dependencies ---
echo "Loading V4L2/vb2 dependencies..."
sudo modprobe videodev
sudo modprobe videobuf2-v4l2
sudo modprobe videobuf2-vmalloc
sudo modprobe videobuf2-dma-contig
sudo modprobe videobuf2-dma-sg
sudo modprobe dw-edma

# --- Load mdb5_v4l2 ---
# Pass skip_ep_wait=1 if BRAM is undersized (remove once new bitstream is loaded)
SKIP_EP_WAIT="${1:-0}"
echo "Loading mdb5_v4l2.ko (skip_ep_wait=${SKIP_EP_WAIT})..."
sudo modprobe mdb5_v4l2 skip_ep_wait="$SKIP_EP_WAIT"

echo ""
echo "--- dmesg ---"
dmesg | grep mdb5 | tail -20

echo ""
echo "--- Video devices ---"
ls -la /dev/video* 2>/dev/null || echo "No video devices found"

