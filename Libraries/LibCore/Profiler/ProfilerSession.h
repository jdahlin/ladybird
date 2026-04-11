/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/HashMap.h>
#include <AK/OwnPtr.h>
#include <AK/String.h>
#include <AK/Time.h>
#include <AK/Vector.h>
#include <LibCore/Export.h>
#include <LibCore/MarkerCollector.h>
#include <LibCore/Profiler/CounterRegistry.h>
#include <LibCore/Profiler/NetworkMarkerStore.h>
#include <LibCore/Profiler/ProfiledThread.h>

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

    // Profiled threads — one per actively sampled thread. Today only the
    // JS main thread gets an entry; step 5 adds label-only samplers and
    // profiled threads for ThreadPool workers, process mains, etc.
    ProfiledThread& create_profiled_thread(String name);
    Vector<OwnPtr<ProfiledThread>>& profiled_threads() { return m_profiled_threads; }
    Vector<OwnPtr<ProfiledThread>> const& profiled_threads() const { return m_profiled_threads; }

    // Session timing metadata. Owners (PageClient, js CLI) stamp these
    // at start/stop time so the gecko exporter can compute sample and
    // marker times relative to the session start.
    void set_timing(MonotonicTime start, i64 start_epoch_ms, int interval_us)
    {
        m_start_time = start;
        m_start_epoch_ms = start_epoch_ms;
        m_interval_us = interval_us;
    }
    void set_stop_epoch_ms(i64 ms) { m_stop_epoch_ms = ms; }
    MonotonicTime start_time() const { return m_start_time; }
    i64 start_epoch_ms() const { return m_start_epoch_ms; }
    i64 stop_epoch_ms() const { return m_stop_epoch_ms; }
    int interval_us() const { return m_interval_us; }

    NetworkMarkerStore& network_markers() { return m_network_markers; }
    NetworkMarkerStore const& network_markers() const { return m_network_markers; }

private:
    MarkerCollector m_markers;
    String m_process_name;
    String m_process_type;
    HashMap<String, CounterSeries> m_counters;
    Vector<OwnPtr<ProfiledThread>> m_profiled_threads;
    NetworkMarkerStore m_network_markers;

    MonotonicTime m_start_time { MonotonicTime::now() };
    i64 m_start_epoch_ms { 0 };
    i64 m_stop_epoch_ms { 0 };
    int m_interval_us { 0 };
};

// Global pointer — null when no session is active. Set in the
// ProfilerSession constructor, cleared in the destructor. Single writer
// (the owning thread) per session lifetime.
extern CORE_API ProfilerSession* g_profiler_session;

}
