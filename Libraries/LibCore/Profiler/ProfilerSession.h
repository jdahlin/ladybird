/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/HashMap.h>
#include <AK/String.h>
#include <LibCore/Export.h>
#include <LibCore/MarkerCollector.h>
#include <LibCore/Profiler/CounterRegistry.h>

namespace Core {

// ProfilerSession — one per process, owned by whoever started profiling
// (PageClient in WebContent, the js CLI in standalone, etc). Lives for
// the duration of a single profile; torn down at stop time.
//
// In step 3 the session owns the MarkerCollector, process metadata, and
// the per-session counter store. Later steps migrate the thread registry,
// platform sampler, network marker store, and profiled threads into the
// session as well. The current surface area matches what step 2 moved out
// of MarkerCollector; new members are added incrementally.
class CORE_API ProfilerSession {
public:
    ProfilerSession();
    ~ProfilerSession();

    MarkerCollector& markers() { return m_markers; }
    MarkerCollector const& markers() const { return m_markers; }

    void set_process_name(String name) { m_process_name = move(name); }
    void set_process_type(String type) { m_process_type = move(type); }
    String const& process_name() const { return m_process_name; }
    String const& process_type() const { return m_process_type; }

    HashMap<String, CounterSeries>& counter_storage() { return m_counters; }
    HashMap<String, CounterSeries> const& counter_storage() const { return m_counters; }

private:
    MarkerCollector m_markers;
    String m_process_name;
    String m_process_type;
    HashMap<String, CounterSeries> m_counters;
};

// Global pointer — null when no session is active. Set in the
// ProfilerSession constructor, cleared in the destructor. Single writer
// (the owning thread) per session lifetime.
extern CORE_API ProfilerSession* g_profiler_session;

}
