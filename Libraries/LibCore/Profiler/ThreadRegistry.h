/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/HashMap.h>
#include <AK/Platform.h>
#include <AK/String.h>
#include <AK/StringView.h>
#include <AK/Time.h>
#include <LibCore/Export.h>
#include <pthread.h>

namespace Core {

struct ThreadInfo {
    String name;
    MonotonicTime register_time;
};

ALWAYS_INLINE u64 profiler_current_tid()
{
    return reinterpret_cast<uintptr_t>(pthread_self());
}

// Register the calling thread under the given human-readable name. Cheap
// no-op if profiling is not active.
CORE_API void profiler_thread_register(StringView name);
CORE_API void profiler_thread_unregister();

// Lower-level: register an arbitrary (tid, name) pair.
CORE_API void profiler_thread_register(u64 tid, String name);
CORE_API void profiler_thread_unregister(u64 tid);

CORE_API HashMap<u64, ThreadInfo> const& profiler_threads();

CORE_API void profiler_set_process_name(String);
CORE_API void profiler_set_process_type(String);
CORE_API String const& profiler_process_name();
CORE_API String const& profiler_process_type();

// Clear the registry and process metadata. Called at session start/stop
// while the shim still owns the state; becomes a no-op once ProfilerSession
// owns these registries in step 3.
CORE_API void profiler_reset_thread_registry();

}

#define PROFILER_THREAD_REGISTER(NAME) ::Core::profiler_thread_register(NAME)
