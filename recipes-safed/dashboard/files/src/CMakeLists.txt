# SPDX-License-Identifier: MIT
# Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
# Contributors: AK
# Maintainer: AK


cmake_minimum_required(VERSION 3.20)

project(Dashboard)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)

set(Dashboard_Debug NO CACHE BOOL "")
set(Dashboard_Single YES CACHE BOOL "")

find_package(Qt5 REQUIRED COMPONENTS Core Quick Widgets)
find_package(Qt5Core REQUIRED)
find_package(Qt5Quick REQUIRED)
find_package(Qt5Widgets REQUIRED)

# rave-mq is provided as an installed static library + header via its own
# recipe (DEPENDS = "rave-mq"), so resolve it from the sysroot rather than
# building it as an in-tree CMake target.
find_library(RAVE_MQ_LIBRARY NAMES rave-mq REQUIRED)

if (NOT Dashboard_Debug)
	set(qrc_cpp_files "dashboard.qrc")
else()
	set(qrc_cpp_files)
endif()

if (Dashboard_Single)
	set(ui_cpp_files)
	set(dashboard_hpp_files cpp/SingleDashboard.hpp)
	set(dashboard_cpp_files cpp/SingleDashboard.cpp)
else()
	set(ui_test_file "${CMAKE_CURRENT_SOURCE_DIR}/ui/TestDashboard.ui")
	set(ui_test_cpp_file "${CMAKE_CURRENT_BINARY_DIR}/ui_TestDashboard.hpp")

	add_custom_command(
		OUTPUT ${ui_test_cpp_file}
		COMMAND ${Qt5Widgets_UIC_EXECUTABLE}
			-o "${ui_test_cpp_file}"
			"${ui_test_file}"
		DEPENDS
			"${ui_test_file}"
		VERBATIM
	)

	set(ui_cpp_files "${ui_test_cpp_file}")
	set(dashboard_hpp_files cpp/TestDashboard.hpp)
	set(dashboard_cpp_files cpp/TestDashboard.cpp)
endif()

add_executable(dashboard
	cpp/main.cpp
	cpp/App.cpp
	cpp/App.hpp
	${dashboard_hpp_files}
	${qrc_cpp_files}
	${ui_cpp_files}
	${dashboard_cpp_files}
)

if (Dashboard_Debug)
	target_compile_definitions(dashboard PRIVATE
		DASHBOARD_SHARE_DIR="${CMAKE_CURRENT_SOURCE_DIR}"
	)
endif()

if (Dashboard_Single)
else()
	target_include_directories(dashboard PRIVATE
		"${CMAKE_CURRENT_BINARY_DIR}"
	)
endif()

if (TARGET Qt5::Quick AND TARGET Qt5::Widgets)
	target_link_libraries(dashboard PRIVATE
		Qt5::Quick
		Qt5::Widgets
		${RAVE_MQ_LIBRARY}
	)
else()
	target_link_libraries(dashboard PRIVATE
		${Qt5Quick_LIBRARIES}
		${Qt5Widgets_LIBRARIES}
		${RAVE_MQ_LIBRARY}
	)
	target_include_directories(dashboard PRIVATE
		${Qt5Quick_INCLUDE_DIRS}
		${Qt5Widgets_INCLUDE_DIRS}
	)
	target_compile_definitions(dashboard PRIVATE
		${Qt5Quick_DEFINITIONS}
		${Qt5Widgets_DEFINITIONS}
	)
endif()
