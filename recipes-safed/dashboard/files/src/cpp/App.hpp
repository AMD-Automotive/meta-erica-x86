// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


#pragma once

#include <memory>
#include <optional>

#include <QMetaMethod>
#include <QMetaProperty>
#include <QObject>




class QQmlComponent;




class App : public QObject {
	Q_OBJECT

public:
	struct Args {
		std::optional<QString> screen;
		std::optional<bool> fullscreen;
	};

	App(QQmlComponent & component, const Args & args);

	void sync(qreal delay);
	void popup(bool on);
	void animate(bool on);
	void fullscreen(bool on);

private:
	std::unique_ptr<QObject> object_;
	QMetaMethod syncMethod_;
	QMetaProperty popupProperty_;
	QMetaProperty animateProperty_;
	QMetaProperty fullscreenProperty_;
};
