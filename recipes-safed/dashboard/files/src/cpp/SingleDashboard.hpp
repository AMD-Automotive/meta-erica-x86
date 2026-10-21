// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


#pragma once

#include <memory>

#include <QQmlComponent>
#include <QSocketNotifier>

#include "App.hpp"
#include "Dashboard.hpp"




class SingleDashboard : public Dashboard {
	Q_OBJECT

public:
	SingleDashboard(const Args & args);

private:
	std::unique_ptr<QQmlComponent> component_;
	std::unique_ptr<App> app_;
	std::unique_ptr<QSocketNotifier> mqSocket_;
};
