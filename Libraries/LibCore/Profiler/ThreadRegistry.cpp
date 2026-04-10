/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Profiler/ThreadRegistry.h>

namespace Core {

// Global registry state. In step 3 this moves into ProfilerSession as
// owned members; the free functions here are the temporary shim layer
// that lets call sites migrate before the session type exists.
static HashMap<u64, ThreadInfo> s_threads;
static String s_process_name;
static String s_process_type;

void profiler_thread_register(u64 tid, String name)
{
    s_threads.set(tid, ThreadInfo { move(name), MonotonicTime::now() });
}

void profiler_thread_unregister(u64 tid)
{
    s_threads.remove(tid);
}

void profiler_thread_register(StringView name)
{
    profiler_thread_register(profiler_current_tid(), MUST(String::from_utf8(name)));
}

void profiler_thread_unregister()
{
    profiler_thread_unregister(profiler_current_tid());
}

HashMap<u64, ThreadInfo> const& profiler_threads()
{
    return s_threads;
}

void profiler_set_process_name(String name) { s_process_name = move(name); }
void profiler_set_process_type(String type) { s_process_type = move(type); }
String const& profiler_process_name() { return s_process_name; }
String const& profiler_process_type() { return s_process_type; }

void profiler_reset_thread_registry()
{
    s_threads.clear();
    s_process_name = {};
    s_process_type = {};
}

}
