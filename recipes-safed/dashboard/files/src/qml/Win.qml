// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


import QtQuick 2.0
import QtQuick.Window 2.3

Window {
	id: root

	property var app

	// Bind screen at construction time. Setting Window.screen later
	// (e.g. from Component.onCompleted) is a no-op on the Wayland QPA
	// because the wl_surface is already associated with an output by
	// the time the assignment happens.
	screen: {
		if (!root.app || !root.app.screen)
			return null;
		for (var i = 0; i < Qt.application.screens.length; i++) {
			var s = Qt.application.screens[i];
			if (s.name === root.app.screen)
				return s;
		}
		return null;
	}

	x: 20 + app.instance * 450
	y: 20
	width: 1920
	height: 1080

	Dashboard {
		app: root.app
		width: root.width
		height: root.height
	}

	Item {
		anchors.fill: parent
		visible: false

		Text {
			id: title
			anchors.horizontalCenter: parent.horizontalCenter
			anchors.bottom: parent.bottom
			anchors.bottomMargin: app.sx(160)
			text: root.app.title
			color: 'white'
			font.pointSize: app.sx(20)
			z: 2
		}

		Rectangle {
			z: 1
			anchors.fill: title
			anchors.margins: -radius
			color: '#80ffffff'
			radius: app.sx(15)
		}
	}

	Popup {
		app: root.app
		anchors.centerIn: parent
		source: app.popupImageUrl
		visible: app.popup
	}

	Component.onCompleted: {
		console.log('Win.onCompleted: Qt.application.version:', Qt.application.version);
		console.log('Win.onCompleted: root.app.fullscreen:', root.app.fullscreen);
		console.log('Win.onCompleted, current screen:', screen ? screen.name : '(null)',
			    'requested:', root.app.screen);

		if (root.app.screen && (!screen || screen.name !== root.app.screen))
			console.error('Win.onCompleted: Requested screen not found or not bound:', root.app.screen);

		if (root.app.fullscreen) {
			root.width = Screen.width;
			root.height = Screen.height;
			root.visibility = Window.FullScreen;
		} else {
			root.width = 400;
			root.height = 225;
			root.visibility = Window.Windowed;
		}
	}
}
