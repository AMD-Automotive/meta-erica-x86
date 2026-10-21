// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


import QtQuick 2.0

QtObject {
	id: root
	readonly property int cycleInterval: 60000
	readonly property point size: Qt.point(1920, 1080)

	property string shareDir: '../'
	property int instance: 0
	property bool fullscreen: true
	property bool popup: false
	property bool animate: true

	property string title: 'Dashboard'
	property string renderedByText: 'GPU'
	property string screen: ''
	property string popupImageUrl
	property string backgroundImageUrl: ''
	property point leftMeterCenter: Qt.point(360, 540)
	property point rightMeterCenter: Qt.point(1560, 540)

	property real cycleDelay: 0.0
	property real cycleAnimationTime
	property real cycleTime: (cycleAnimationTime + cycleDelay) % 1.0

	property point scale: Qt.point(
		window.width / size.x,
		window.height / size.y
	)

	function sx(v) {
		return v * scale.x;
	}

	function sy(v) {
		return v * scale.y;
	}

	function sync(delay) {
		console.log('App: sync: delay:', delay);
		root.cycleDelay = delay;
		if (root.animate) {
			root.cycleAnimation.restart();
		}
	}

	function rnd(i) {
		return (i * 194.3826982367) % 1.0;
	}

	function radianFromDegree(d) {
		return d * Math.PI / 180.0;
	}

	function radianToDegree(d) {
		return d * 180.0 / Math.PI;
	}

	readonly property string rotationMatrix: '
		mat2 rotationMatrix(float angle) {
			float s = sin(angle);
			float c = cos(angle);
			return mat2(c, s, -s, c);
		}
	'

	property var cycleAnimation: NumberAnimation {
		from: 0.0
		to: 1.0
		target: root
		property: 'cycleAnimationTime'
		duration: root.cycleInterval
		running: root.animate
		loops: Animation.Infinite
	}

	property var window: Win {
		app: root
		title: root.title
	}

	Component.onCompleted: {
		console.log('App.onCompleted:', title);
		console.log('App.onCompleted: shareDir:', root.shareDir, 'instance:', root.instance,
				'screens:', Qt.application.screens, 'screen count:', Qt.application.screens.length);
		for (var i = 0; i < Qt.application.screens.length; i++) {
			var s = Qt.application.screens[i];
			console.log('    screen:', s, s.name);
		}
	}
}
