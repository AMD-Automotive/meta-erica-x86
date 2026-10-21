// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


#include "App.hpp"

#include <QDebug>
#include <QQmlComponent>




App::App(QQmlComponent & component, const Args & args)
{
	const auto props = [&args] () -> QVariantMap {
		QVariantMap map;
#ifdef DASHBOARD_SHARE_DIR
		map["shareDir"] = "file://" DASHBOARD_SHARE_DIR "/";
#endif
		if (args.screen.has_value()) {
			map["screen"] = args.screen.value();
		}
		if (args.fullscreen.has_value()) {
			map["fullscreen"] = args.fullscreen.value();
		}
		return map;
	}();

	object_.reset(component.createWithInitialProperties(props));
	if (!object_) {
		qWarning() << "QML App component create error";
		throw "QML App component create error";
	}

	const QMetaObject & metaObject = *object_->metaObject();

	const auto getMethod = [&metaObject] (const char * name) -> QMetaMethod {
		const int index = metaObject.indexOfMethod(name);
		if (index == -1) {
			qWarning() << "Cannot find method:" << name;
			throw "Cannot find method";
		}
		return metaObject.method(index);
	};

	const auto getProperty = [&metaObject] (const char * name) -> QMetaProperty {
		const int index = metaObject.indexOfProperty(name);
		if (index == -1) {
			qWarning() << "Cannot find property:" << name;
			throw "Cannot find property";
		}
		return metaObject.property(index);
	};

	syncMethod_ = getMethod("sync(QVariant)");
	popupProperty_ = getProperty("popup");
	animateProperty_ = getProperty("animate");
	fullscreenProperty_ = getProperty("fullscreen");
}


void App::sync(const qreal delay)
{
	QVariant ret;
	syncMethod_.invoke(object_.get(), Qt::DirectConnection,
			Q_RETURN_ARG(QVariant, ret), Q_ARG(QVariant, delay));
}


void App::popup(const bool on)
{
	popupProperty_.write(object_.get(), on);
}


void App::animate(const bool on)
{
	animateProperty_.write(object_.get(), on);
}


void App::fullscreen(const bool on)
{
	fullscreenProperty_.write(object_.get(), on);
}
