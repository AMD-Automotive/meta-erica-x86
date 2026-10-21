// SPDX-License-Identifier: MIT
// Copyright © Advanced Micro Devices, Inc., or its affiliates. All rights reserved.
// Contributors: AK
// Maintainer: AK


#include <memory>

#include <QApplication>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QQuickWindow>
#include <QScreen>
#include <QTimer>

#include "Dashboard.hpp"

#include <rave_mq.h>

class automq {
public:
	automq() {
		_mq = rave_mq_open();
		if (!_mq)
			throw std::system_error(errno, std::system_category(),
						"failed to open rave mq");
	}
	automq(const automq&) = delete;
	automq &operator = (const automq&) = delete;
	automq(automq&&) = default;
	~automq() {
		rave_mq_close(_mq);
	}
	int send(const struct rave_mq_msg *msg) {
		return rave_mq_send(_mq, msg);
	}
	int recv(struct rave_mq_msg *msg) {
		return rave_mq_recv(_mq, msg);
	}
private:
	rave_mq *_mq;
};

int main(int argc, char ** argv)
{
	QApplication qapp(argc, argv);

	const auto rootQmlUrl = [] () -> QUrl {
#ifdef DASHBOARD_SHARE_DIR
		return QUrl(DASHBOARD_SHARE_DIR "/").resolved(QUrl("qml/"));
#else
		return QUrl("qrc:///dashboard/qml/");
#endif
	}();

	try {
		const auto screens = QGuiApplication::screens();
		for (QScreen * const screen : screens) {
			qDebug() << "QScreen: name:" << screen->name();
		}

		QQmlEngine engine;
		QObject::connect(&engine, &QQmlEngine::quit, &qapp, [&qapp] {
			qDebug() << "QML signalled quit()";
			qapp.quit();
		});

		QQuickWindow::setDefaultAlphaBuffer(false);

		const Dashboard::Args args{
			.engine = engine,
			.rootQmlUrl = rootQmlUrl,
		};

		auto dashboard = Dashboard::create(args);

		/*
		 * Periodically re-assert START as the application's liveness
		 * manifest. The safe-display controller only recovers to the
		 * remote (x86) pipeline after an explicit START followed by a
		 * few seconds of stable stream. A single START sent at startup
		 * races with compositor/GPU warm-up: early frame jitter trips
		 * the controller's stability window and, with no further START,
		 * it stays latched on the backup pipeline forever. Re-sending
		 * START at a steady cadence lets a later, already-stable stream
		 * satisfy the recovery condition, while preserving the safety
		 * semantics (a hung app stops sending START and cannot reclaim
		 * the display).
		 */
		QTimer startTimer;
		try {
			auto mq = std::make_shared<automq>();
			auto sendStart = [mq] {
				rave_mq_msg startMsg = {
					.type = RAVE_MQ_MSG_START,
				};
				if (mq->send(&startMsg) < 0)
					qWarning() << "Failed to send start message";
			};

			QObject::connect(&startTimer, &QTimer::timeout,
					 &qapp, sendStart);
			startTimer.start(1000);

			/* Assert liveness immediately, then keep re-asserting. */
			sendStart();
		} catch (const std::system_error &e) {
			qWarning() << "Failed to open rave mq: " << e.what();
		}

		return qapp.exec();
	} catch (...) {
		qWarning() << "Runtime error";
		return 1;
	}
}
