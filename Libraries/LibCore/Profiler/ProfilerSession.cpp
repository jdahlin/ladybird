/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Profiler/ProfilerSession.h>

namespace Core {

ProfilerSession* g_profiler_session { nullptr };

ProfilerSession::ProfilerSession()
{
    VERIFY(g_profiler_session == nullptr);
    g_profiler_session = this;
    // Keep g_marker_collector working for the MARKER_* macros — it aliases
    // the session's marker collector for the session's lifetime. Will go
    // away when the macros stop reading it directly.
    g_marker_collector = &m_markers;
}

ProfilerSession::~ProfilerSession()
{
    g_marker_collector = nullptr;
    g_profiler_session = nullptr;
}

}
