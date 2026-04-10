/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Profiler/ProfilerSession.h>
#include <LibCore/Profiler/ThreadRegistry.h>

namespace Core {

// Thread registry is process-lifetime: workers started before a profile
// session exists (e.g. ThreadPool at process init) still need to register
// so their names appear when a later profile is taken. That's why this
// state is NOT owned by ProfilerSession.
static HashMap<u64, ThreadInfo> s_threads;

// Fallback storage used before any ProfilerSession is created. Once a
// session exists, process metadata is stored on the session.
static String s_fallback_process_name;
static String s_fallback_process_type;

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

void profiler_set_process_name(String name)
{
    if (auto* session = g_profiler_session)
        session->set_process_name(move(name));
    else
        s_fallback_process_name = move(name);
}

void profiler_set_process_type(String type)
{
    if (auto* session = g_profiler_session)
        session->set_process_type(move(type));
    else
        s_fallback_process_type = move(type);
}

String const& profiler_process_name()
{
    if (auto* session = g_profiler_session)
        return session->process_name();
    return s_fallback_process_name;
}

String const& profiler_process_type()
{
    if (auto* session = g_profiler_session)
        return session->process_type();
    return s_fallback_process_type;
}

void profiler_reset_thread_registry()
{
    s_threads.clear();
    if (auto* session = g_profiler_session) {
        session->set_process_name({});
        session->set_process_type({});
    }
    s_fallback_process_name = {};
    s_fallback_process_type = {};
}

}
