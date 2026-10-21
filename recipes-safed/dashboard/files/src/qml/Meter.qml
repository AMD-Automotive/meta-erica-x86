// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


import QtQuick 2.0

Item {
	id: root

	property var app

	property real circleSize: app.sx(160)
	property real circleWidth: app.sx(30)

	property int minNumber: 0
	property int maxNumber: 10
	property int numberCount: 11
	property int numberTicks: 4

	property int minAngle: -120
	property int maxAngle: 60

	property int period: 4
	property real alt: 0.1
	property bool showValue: true
	property bool showCenterSymbol: false

	property real dotRadius: app.sx(120)
	property real dotWidth: app.sx(4)
	property color activeColorStart: '#e64d00'
	property color activeColorEnd: '#ffbf1a'
	property color accentArcColor: '#b0b0b0'
	property real accentArcWidth: app.sx(2)
	property real accentArcGap: app.sx(5)
	property real accentArcOpacity: 0.28
	property real valueUnderlineWidth: app.sx(180)
	property string topLabelText: 'POWER'
	property real topLabelRadius: root.circleSize + app.sx(0)
	property real topLabelArcSpanDeg: 38
	property color centerSymbolColor: '#d2d7de'
	property real centerSymbolSize: root.dotRadius * 1.1
	property real centerSymbolStroke: app.sx(16)
	property real centerSymbolOffsetY: -app.sy(20)
	property real centerSymbolRotationDeg: -30
	property string centerSymbolLabelText: 'REGEN'

	QtObject {
		id: p

		readonly property real minAngle: root.app.radianFromDegree(root.minAngle)
		readonly property real maxAngle: root.app.radianFromDegree(root.maxAngle)
		readonly property real angleRange: maxAngle - minAngle

		function posToAngle(pos) {
			return minAngle + angleRange * pos;
		}

		property real time: (app.cycleTime * root.period) % 1.0
		property real modTime: (app.cycleTime * root.period * 3.0) % 1.0

		property real value: {
			var v = 0.1 + 0.3 * (1.0 + Math.sin(time * Math.PI * 2.0));
			v += root.alt * (1.0 + Math.sin(modTime * Math.PI * 2.0)) * Math.sin(time * 10 * Math.PI * 2.0)
			return Math.min(1.0, Math.max(0.0, v));
		}
	}

	ShaderEffect {
		id: activeArc
		anchors.centerIn: parent
		z: -1
		width: 1
		height: 1

		mesh: Qt.size(200, 1)

		property color accentArcColor: root.accentArcColor
		property real accentArcOpacity: root.accentArcOpacity

		vertexShader: '
			attribute highp vec4 qt_Vertex;
			attribute highp vec2 qt_MultiTexCoord0;

			uniform highp mat4 qt_Matrix;

			varying highp vec2 qt_TexCoord0;

			const highp float minAngle = ' + p.minAngle.toFixed(1) + ';
			const highp float maxAngle = ' + p.maxAngle.toFixed(1) + ';
			const highp float size = ' + (root.circleSize + root.circleWidth * 0.5 + root.accentArcGap).toFixed(1) + ';
			const highp float width = ' + root.accentArcWidth.toFixed(1) + ';

			void main() {
				qt_TexCoord0 = qt_MultiTexCoord0;

				const float minRadius = size - width * 0.5;
				const float maxRadius = size + width * 0.5;

				float rot = mix(minAngle, maxAngle, qt_MultiTexCoord0.x);
				float dist = mix(minRadius, maxRadius, qt_MultiTexCoord0.y);

				vec2 pos = vec2(sin(rot), -cos(rot));

				gl_Position = qt_Matrix * vec4(pos * dist, 0.0, 1.0);
			}
		'

		fragmentShader: '
			#ifdef GL_ES
			precision mediump float;
			precision mediump int;
			#endif

			varying highp vec2 qt_TexCoord0;

			uniform lowp vec4 accentArcColor;
			uniform highp float accentArcOpacity;

			void main() {
				gl_FragColor = vec4(accentArcColor.rgb, accentArcOpacity);
			}
		'
	}

	Canvas {
		anchors.centerIn: parent
		anchors.verticalCenterOffset: root.centerSymbolOffsetY
		width: root.centerSymbolSize
		height: root.centerSymbolSize
		visible: root.showCenterSymbol
		rotation: root.centerSymbolRotationDeg
		transformOrigin: Item.Center

		onPaint: {
			var ctx = getContext('2d');
			ctx.clearRect(0, 0, width, height);

			var cx = width * 0.5;
			var cy = height * 0.5;
			var lw = Math.max(1.0, root.centerSymbolStroke);
			var radius = Math.max(2.0, width * 0.5 - lw * 0.65);
			var horizontalRightX = cx + radius * 0.78;
			var arcTopRightAngle = 1.92 * Math.PI;
			var arcTopRightX = cx + Math.cos(arcTopRightAngle) * radius;
			var arcTopRightY = cy + Math.sin(arcTopRightAngle) * radius;

			ctx.strokeStyle = root.centerSymbolColor;
			ctx.lineWidth = lw;
			ctx.lineCap = 'round';
			ctx.lineJoin = 'round';

			ctx.beginPath();
			ctx.arc(cx, cy, radius, 0.28 * Math.PI, 1.92 * Math.PI, false);
			ctx.stroke();

			ctx.beginPath();
			ctx.moveTo(cx - radius * 0.58, cy);
			ctx.lineTo(horizontalRightX, cy);
			ctx.stroke();

			ctx.beginPath();
			ctx.moveTo(horizontalRightX, cy);
			ctx.lineTo(arcTopRightX, arcTopRightY);
			ctx.stroke();
		}

		onWidthChanged: requestPaint()
		onHeightChanged: requestPaint()
	}

	Text {
		anchors.top: parent.verticalCenter
		anchors.topMargin: root.centerSymbolOffsetY + root.centerSymbolSize * 0.8
		anchors.horizontalCenter: parent.horizontalCenter
		visible: root.showCenterSymbol
		text: root.centerSymbolLabelText
		color: 'white'
		font.bold: true
		font.pointSize: root.dotRadius * 0.2
	}

	ShaderEffect {
		anchors.centerIn: parent
		width: 1
		height: 1

		mesh: Qt.size(200, 1)

		property real time: p.value
		property color activeColorStart: root.activeColorStart
		property color activeColorEnd: root.activeColorEnd

		vertexShader: '
			attribute highp vec4 qt_Vertex;
			attribute highp vec2 qt_MultiTexCoord0;

			uniform highp mat4 qt_Matrix;

			varying highp vec2 qt_TexCoord0;

			const highp float minAngle = ' + p.minAngle.toFixed(1) + ';
			const highp float maxAngle = ' + p.maxAngle.toFixed(1) + ';
			const highp float size = ' + root.circleSize.toFixed(1) + ';
			const highp float width = ' + root.circleWidth.toFixed(1) + ';

			void main() {
				qt_TexCoord0 = qt_MultiTexCoord0;

				const float minRadius = size - width * 0.5;
				const float maxRadius = size + width * 0.5;

				float rot = mix(minAngle, maxAngle, qt_MultiTexCoord0.x);
				float dist = mix(minRadius, maxRadius, qt_MultiTexCoord0.y);

				vec2 pos = vec2(sin(rot), -cos(rot));

				gl_Position = qt_Matrix * vec4(pos * dist, 0.0, 1.0);
			}
		'

		fragmentShader: '
			#ifdef GL_ES
			precision mediump float;
			precision mediump int;
			#endif

			varying highp vec2 qt_TexCoord0;

			uniform highp float time;
			uniform lowp vec4 activeColorStart;
			uniform lowp vec4 activeColorEnd;

			void main() {
				vec3 c;
				float a = 1.0;
				if (qt_TexCoord0.x < time) {
					float t = abs(mod(qt_TexCoord0.x * 160.0, 2.0) - 1.0);
					vec3 grad = mix(activeColorStart.rgb, activeColorEnd.rgb, qt_TexCoord0.x);
					c = grad * mix(0.85, 1.0, t);
				} else {
					c = vec3(0.0);
					a = 0.0;
				}
				gl_FragColor = vec4(c, a);
			}
		'
	}

	ShaderEffect {
		anchors.centerIn: parent
		width: 1
		height: 1

		mesh: Qt.size(200, 1)

		property color accentArcColor: root.accentArcColor
		property real accentArcOpacity: root.accentArcOpacity

		vertexShader: '
			attribute highp vec4 qt_Vertex;
			attribute highp vec2 qt_MultiTexCoord0;

			uniform highp mat4 qt_Matrix;

			varying highp vec2 qt_TexCoord0;

			const highp float minAngle = ' + p.minAngle.toFixed(1) + ';
			const highp float maxAngle = ' + p.maxAngle.toFixed(1) + ';
			const highp float size = ' + (root.circleSize - root.circleWidth * 0.5 - root.accentArcGap).toFixed(1) + ';
			const highp float width = ' + root.accentArcWidth.toFixed(1) + ';

			void main() {
				qt_TexCoord0 = qt_MultiTexCoord0;

				const float minRadius = size - width * 0.5;
				const float maxRadius = size + width * 0.5;

				float rot = mix(minAngle, maxAngle, qt_MultiTexCoord0.x);
				float dist = mix(minRadius, maxRadius, qt_MultiTexCoord0.y);

				vec2 pos = vec2(sin(rot), -cos(rot));

				gl_Position = qt_Matrix * vec4(pos * dist, 0.0, 1.0);
			}
		'

		fragmentShader: '
			#ifdef GL_ES
			precision mediump float;
			precision mediump int;
			#endif

			varying highp vec2 qt_TexCoord0;

			uniform lowp vec4 accentArcColor;
			uniform highp float accentArcOpacity;

			void main() {
				gl_FragColor = vec4(accentArcColor.rgb, accentArcOpacity);
			}
		'
	}

	Item {
		id: powerOverlayLayer
		anchors.centerIn: parent
		width: root.circleSize * 4
		height: root.circleSize * 4
		z: 1000
		visible: root.showCenterSymbol
		layer.enabled: true

		Repeater {
			id: powerOverlayRepeater
			model: root.topLabelText.length

			delegate: Text {
				readonly property real angleDeg: (powerOverlayRepeater.count > 1)
					? (-root.topLabelArcSpanDeg * 0.5 + index * root.topLabelArcSpanDeg / (powerOverlayRepeater.count - 1))
					: 0
				readonly property real angleRad: angleDeg * Math.PI / 180.0

				text: root.topLabelText.charAt(index)
				color: 'white'
				font.bold: true
				font.pointSize: root.dotRadius * 0.2

				x: powerOverlayLayer.width * 0.5 + Math.sin(angleRad) * root.topLabelRadius - width * 0.5
				y: powerOverlayLayer.height * 0.5 - Math.cos(angleRad) * root.topLabelRadius - height * 0.5
				rotation: angleDeg
				transformOrigin: Item.Center
			}
		}
	}

	Text {
		id: valueText
		anchors.centerIn: parent
		anchors.verticalCenterOffset: -root.dotRadius * 0.3
		visible: root.showValue
		color: 'white'
		text: (root.minNumber + p.value * (root.maxNumber - root.minNumber)).toFixed()
		font.bold: true
		font.pointSize: root.dotRadius * 0.8
	}

	Rectangle {
		anchors.top: valueText.bottom
		anchors.topMargin: app.sy(-16)
		anchors.horizontalCenter: valueText.horizontalCenter
		visible: root.showValue
		width: root.valueUnderlineWidth
		height: app.sy(2)
		radius: height * 0.5
		color: 'white'
		opacity: 0.65
	}

	Text {
		id: readyText
		anchors.top: valueText.bottom
		anchors.topMargin: app.sy(-8)
		anchors.horizontalCenter: valueText.horizontalCenter
		visible: root.showValue
		text: 'READY'
		color: '#39d97a'
		font.bold: true
		font.pointSize: root.dotRadius * 0.2
	}

	Text {
		anchors.top: readyText.bottom
		anchors.topMargin: app.sy(-2)
		anchors.horizontalCenter: readyText.horizontalCenter
		visible: root.showValue
		text: 'AUTOHOLD'
		color: 'white'
		font.bold: true
		font.pointSize: root.dotRadius * 0.2
	}

}
