// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


#include "SingleDashboard.hpp"

#include <QCoreApplication>




SingleDashboard::SingleDashboard(const Args & args)
{
	struct Opts {
		QString name = "versal";
		QString screen;
	} opts = [] () {
		Opts opts;
		const QStringList arguments = QCoreApplication::arguments();
		for (int i = 1; i < arguments.count(); ++i) {
			const QString & opt = arguments.at(i);
			if (opt == "--name") {
				i++;
				opts.name = arguments.at(i);
			} else if (opt == "--screen") {
				i++;
				opts.screen = arguments.at(i);
			}
		}
		return opts;
	}();

	const auto qmlUrl = args.rootQmlUrl.resolved(QUrl(opts.name + ".qml"));
	qDebug() << "QML url:" << qmlUrl << "screen:" << opts.screen;

	component_.reset(new QQmlComponent(&args.engine));
	component_->loadUrl(qmlUrl);
	if (!component_->isReady()) {
		qWarning() << "QML create error:" << component_->errorString();
		throw "QML create error";
	}

	app_.reset(new App(*component_, App::Args{.screen = opts.screen}));

	const int mqFileDescriptor = -1; //
	mqSocket_.reset(new QSocketNotifier(mqFileDescriptor, QSocketNotifier::Read));
	connect(mqSocket_.get(), &QSocketNotifier::activated, this, [this] {
		// read mq with rave_mq_recv() here
	});
}


std::unique_ptr<Dashboard> Dashboard::create(const Args & args)
{
	return std::unique_ptr<Dashboard>(new SingleDashboard(args));
}
