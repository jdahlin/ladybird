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

#if defined(AK_OS_LINUX)
#    include <sys/syscall.h>
#    include <unistd.h>
#elif defined(AK_OS_MACOS)
#    include <pthread.h>
#endif

namespace Core {

struct ThreadInfo {
    String name;
    MonotonicTime register_time;
};

// Kernel-level thread id used for /proc/self/task/<tid>/* lookups and
// shown in profiler.firefox.com. NOT the pthread handle returned by
// pthread_self() — that's a userspace opaque pointer, not a TID, and
// /proc paths require the real kernel TID.
ALWAYS_INLINE u64 profiler_current_tid()
{
#if defined(AK_OS_LINUX)
    return static_cast<u64>(::syscall(SYS_gettid));
#elif defined(AK_OS_MACOS)
    uint64_t tid = 0;
    pthread_threadid_np(nullptr, &tid);
    return tid;
#else
    return reinterpret_cast<uintptr_t>(pthread_self());
#endif
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
