// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


import QtQuick 2.0

Rectangle {
	id: root

	property var app

	color: '#ff000000'

	Image {
		anchors.fill: parent
		source: app.backgroundImageUrl
		fillMode: Image.PreserveAspectFit
		smooth: true
		visible: source !== ''
	}

	QtObject {
		id: p
		readonly property real centerX: app.size.x * 0.5
		readonly property real centerY: app.size.y * 0.5
		readonly property real leftMeterOffsetX: app.sx(app.leftMeterCenter.x - centerX)
		readonly property real leftMeterOffsetY: app.sy(app.leftMeterCenter.y - centerY)
		readonly property real rightMeterOffsetX: app.sx(app.rightMeterCenter.x - centerX)
		readonly property real rightMeterOffsetY: app.sy(app.rightMeterCenter.y - centerY)
	}

	Meter {
		anchors.centerIn: parent
		anchors.horizontalCenterOffset: p.rightMeterOffsetX
		anchors.verticalCenterOffset: p.rightMeterOffsetY
		maxNumber: 8
		numberCount: 5
		showValue: false
		showCenterSymbol: true
		centerSymbolColor: '#d2d7de'
		activeColorStart: '#0f7f2f'
		activeColorEnd: '#39d97a'
		app: root.app
		period: 7
	}

	Meter {
		anchors.centerIn: parent
		anchors.horizontalCenterOffset: p.leftMeterOffsetX
		anchors.verticalCenterOffset: p.leftMeterOffsetY
		maxNumber: 100
		numberCount: 7
		activeColorStart: '#1a4fd0'
		activeColorEnd: '#57a8ff'
		app: root.app
	}
}
