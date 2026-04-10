/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/NonnullRefPtr.h>
#include <AK/String.h>
#include <LibDevTools/Actor.h>
#include <LibDevTools/Forward.h>

namespace DevTools {

class DEVTOOLS_API PerfActor final : public Actor {
public:
    static constexpr auto base_name = "perf"sv;

    static NonnullRefPtr<PerfActor> create(DevToolsServer&, String name);
    virtual ~PerfActor() override;

private:
    PerfActor(DevToolsServer&, String name);

    virtual void handle_message(Message const&) override;

    bool m_is_active { false };
    String m_captured_profile;
};

}
