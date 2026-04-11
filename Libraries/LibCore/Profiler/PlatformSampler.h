/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <LibCore/Export.h>
#include <LibCore/Profiler/SamplingHandle.h>
#include <pthread.h>

namespace Core {

// Process-wide platform sampler driver. Owns the timer thread and the
// platform-specific delivery mechanism:
//   - Linux: SIGUSR2 + pthread_kill; handler reads t_profiler_state
//   - macOS: thread_suspend / thread_get_state / thread_resume
//   - Windows: SuspendThread / GetThreadContext / ResumeThread
//
// Step 6a: single-target. One call to start() binds one SamplingHandle
// to one target thread and drives sampling until stop(). A follow-up
// generalizes this to iterate a list of SamplingHandles and signal
// every registered profiled thread on each tick.
class CORE_API PlatformSampler {
public:
    struct Target {
        SamplingHandle* handle;
        pthread_t thread;
        int interval_us;
    };

    // Publish the handle into the target thread's ThreadProfilerState,
    // install the platform-specific delivery mechanism, and start the
    // timer thread. Caller must have already called
    // ensure_profiler_state() on the target thread so t_profiler_state
    // is non-null.
    static bool start(Target);

    // Stop the timer thread, drain in-flight signals (Linux), restore
    // the previous signal handler, and clear active_sampling_handle on
    // the target thread.
    static void stop();

    static bool is_active();
};

}
