// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


#pragma once

#include "Dashboard.hpp"

#include <memory>
#include <vector>

#include <QObject>
#include <QQmlComponent>

#include "ui_TestDashboard.hpp"

#include "App.hpp"




class TestDashboard : public Dashboard {
	Q_OBJECT

public:
	TestDashboard(const Args & args);
	~TestDashboard() override;

public slots:
	void onDesync();
	void onSync();
	void onRadeonPopup();
	void onVersalPopup();
	void onRadeonAnimate();
	void onVersalAnimate();

protected:
	bool eventFilter(QObject * o, QEvent * e) override;

private:
	static QStringList winList() noexcept {
		return QStringList() << "radeon" << "versal";
	}

	App & radeon() noexcept { return *apps_.at(0); }
	App & versal() noexcept { return *apps_.at(1); }

	std::unique_ptr<QWidget> widget_;
	std::unique_ptr<Ui::DashboardTest> ui_;

	std::unique_ptr<QQmlComponent> component_;
	std::vector<std::unique_ptr<App>> apps_;
};
