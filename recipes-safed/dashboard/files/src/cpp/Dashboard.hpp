// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


#pragma once

#include <functional>
#include <memory>

#include <QObject>
#include <QUrl>




class App;
class QQmlEngine;




class Dashboard : public QObject {
public:
	struct Args {
		QQmlEngine & engine;
		QUrl rootQmlUrl;
	};

	static std::unique_ptr<Dashboard> create(const Args & args);

	virtual ~Dashboard() noexcept {}
};
