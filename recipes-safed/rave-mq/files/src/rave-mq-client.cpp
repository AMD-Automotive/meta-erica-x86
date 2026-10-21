// SPDX-License-Identifier: MIT
// Contributors: AK
// Maintainer: AK
/*
 * RAVE2 Message Queue Client
 *
 * Copyright (C) 2026, Advanced Micro Devices, Inc.
 */

#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>
#include <system_error>

#include "rave_mq.h"

class automq {
public:
	automq() {
		_mq = rave_mq_open();
		if (!_mq)
			throw std::system_error(errno, std::system_category(),
						"failed to open /dev/rave-mq");
	}
	automq(const automq&) = delete;
	automq &operator = (const automq&) = delete;
	automq(automq&&) = delete;
	~automq() {
		rave_mq_close(_mq);
	}

	rave_mq *get() const {
		return _mq;
	}

private:
	rave_mq *_mq;
};

int reader() {
	automq mq;
	while(true) {
		rave_mq_msg msg;
		auto ret = rave_mq_recv(mq.get(), &msg);
		if (ret < 0)
			throw std::system_error(-ret,
						std::system_category(),
						"read failed");
		std::cout << std::hex << msg.payload << std::dec << std::endl;
	}

	return 0;
}

int writer(std::string cmd, const char *payload) {
	automq mq;
	rave_mq_msg msg;
	msg.payload = std::atoll(payload);

	if (cmd == "start")
		msg.type = RAVE_MQ_MSG_START;
	else if (cmd == "stop")
		msg.type = RAVE_MQ_MSG_STOP;
	else if (cmd == "pause")
		msg.type = RAVE_MQ_MSG_PAUSE;
	else if (cmd == "flip")
		msg.type = RAVE_MQ_MSG_PAGE_FLIP;
	else if (cmd == "vblank")
		msg.type = RAVE_MQ_MSG_VBLANK;
	else if (cmd == "keep")
		msg.type = RAVE_MQ_MSG_KEEP_ALIVE;
	else {
		std::cerr << "unsupported command '" << cmd << "'" << std::endl;
		return -1;
	}

	auto ret = rave_mq_send(mq.get(), &msg);
	if (ret < 0) {
		std::cerr << "failed to send message: " << ret << std::endl;
		return -1;
	}

	return 0;
}

int main(int argc, char *argv[]) {
	if (argc < 3)
		return reader();

	return writer(argv[1], argv[2]);
}