/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Profiler/Label.h>

namespace Core {

thread_local ThreadProfilerState* t_profiler_state = nullptr;

// One storage slot per thread. ensure_profiler_state() publishes its address
// to t_profiler_state on first touch. Keeping the storage separate from the
// published pointer lets the signal handler distinguish "thread never touched
// the profiler" (pointer null) from "thread has profiler state".
static thread_local ThreadProfilerState s_thread_profiler_state;

ThreadProfilerState& ensure_profiler_state()
{
    if (t_profiler_state == nullptr)
        t_profiler_state = &s_thread_profiler_state;
    return *t_profiler_state;
}

}
