/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/Atomic.h>
#include <LibCore/Export.h>

namespace Core {

class ProfiledThread;
class StackSampler;

// One SamplingHandle per (ProfiledThread, StackSampler) pair for the
// duration of a session. The platform sampler reads the handle via each
// target thread's ThreadProfilerState::active_sampling_handle with
// acquire ordering, then dispatches capture_sample() through it without
// any further lookups — that's what keeps the signal handler
// allocation- and lock-free.
//
// Install (session start):
//   1. allocate ProfiledThread + SamplingHandle per target thread
//   2. store the handle into each thread's active_sampling_handle with
//      release ordering
//   3. install the SIGUSR2 handler (Linux) or start the thread-suspend
//      loop (macOS/Windows)
//   4. start the timer thread
//
// Teardown reverses. See plan section "Signal-safe sampling: permanent
// per-thread state" for the full ordering.
struct SamplingHandle {
    StackSampler* sampler { nullptr };
    ProfiledThread* profiled { nullptr };
    Atomic<bool> in_handler { false };
};

}
