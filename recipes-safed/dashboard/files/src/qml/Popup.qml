// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


import QtQuick 2.0

Item {
	id: root

	property var app
	property string source

	width: app.sx(780)
	height: app.sy(464)

	property Image image: Image {
		anchors.centerIn: parent
		source: app.shareDir + 'images/popup/' + root.source
	}

	ShaderEffect {
		anchors.fill: parent
		property var source: root.image
	}

	Rectangle {
		anchors.fill: parent
		color: 'transparent'
		border.color: 'white'
		border.width: radius
		radius: app.sx(4)
	}
}
