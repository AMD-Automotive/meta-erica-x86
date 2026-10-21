# SPDX-License-Identifier: MIT
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# Contributors: AK
# Maintainer: AK

add_library(rave-mq STATIC rave_mq.cpp)

target_include_directories(rave-mq PUBLIC
	${CMAKE_CURRENT_SOURCE_DIR}
)

add_executable(rave-mq-client rave-mq-client.cpp)
target_link_libraries(rave-mq-client PRIVATE rave-mq)
