// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


#include "TestDashboard.hpp"

#include <QCoreApplication>
#include <QDebug>




TestDashboard::TestDashboard(const Args & args)
{
	widget_.reset(new QWidget);
	ui_.reset(new Ui::DashboardTest);
	ui_->setupUi(widget_.get());

	widget_->installEventFilter(this);

	connect(ui_->desyncButton, &QPushButton::clicked, this, &TestDashboard::onDesync);
	connect(ui_->syncButton, &QPushButton::clicked, this, &TestDashboard::onSync);
	connect(ui_->radeonPopupCheckBox, &QPushButton::clicked, this, &TestDashboard::onRadeonPopup);
	connect(ui_->versalPopupCheckBox, &QPushButton::clicked, this, &TestDashboard::onVersalPopup);
	connect(ui_->radeonAnimateCheckBox, &QPushButton::clicked, this, &TestDashboard::onRadeonAnimate);
	connect(ui_->versalAnimateCheckBox, &QPushButton::clicked, this, &TestDashboard::onVersalAnimate);

	component_.reset(new QQmlComponent(&args.engine));

	const QStringList names = winList();
	for (int instance = 0; instance < names.count(); ++instance) {
		const QString & name = names.at(instance);
		const QUrl qmlUrl = args.rootQmlUrl.resolved(QUrl(name + ".qml"));

		component_->loadUrl(qmlUrl);
		if (!component_->isReady()) {
			qWarning() << "QML create error:" << component_->errorString();
			throw "QML create error";
		}

		apps_.push_back(std::unique_ptr<App>(new App(*component_, {
			.fullscreen = false,
		})));
	}

	onRadeonPopup();
	onVersalPopup();
	onRadeonAnimate();
	onVersalAnimate();

	widget_->show();
}


TestDashboard::~TestDashboard()
{
}


bool TestDashboard::eventFilter(QObject * const o, QEvent * const e)
{
	if (o == widget_.get() && e->type() == QEvent::Close) {
		qDebug() << "Closed";
		QCoreApplication::instance()->quit();
		return true;
	}

	return Dashboard::eventFilter(o, e);
}


void TestDashboard::onDesync()
{
	qDebug() << "desync";

	int index = 0;
	for (auto & app : apps_) {
		app->sync(0.5f * index);
		index++;
	}
}


void TestDashboard::onSync()
{
	qDebug() << "sync";

	for (auto & app : apps_) {
		app->sync(0);
	}
}


void TestDashboard::onRadeonPopup()
{
	radeon().popup(ui_->radeonPopupCheckBox->isChecked());
}


void TestDashboard::onVersalPopup()
{
	versal().popup(ui_->versalPopupCheckBox->isChecked());
}


void TestDashboard::onRadeonAnimate()
{
	radeon().animate(ui_->radeonAnimateCheckBox->isChecked());
}


void TestDashboard::onVersalAnimate()
{
	versal().animate(ui_->versalAnimateCheckBox->isChecked());
}


std::unique_ptr<Dashboard> Dashboard::create(const Args & args)
{
	return std::unique_ptr<Dashboard>(new TestDashboard(args));
}
