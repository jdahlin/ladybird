/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/Atomic.h>
#include <AK/HashMap.h>
#include <AK/OwnPtr.h>
#include <AK/String.h>
#include <AK/Time.h>
#include <AK/Utf16FlyString.h>
#include <AK/Vector.h>
#include <LibCore/MarkerCollector.h>
#include <LibCore/Profiler/ProfiledThread.h>
#include <LibJS/Export.h>
#include <LibJS/Forward.h>
#include <LibThreading/Thread.h>
#include <pthread.h>
#include <signal.h>

namespace JS {

class JS_API Profiler {
public:
    explicit Profiler(VM&, int interval_us = 1000);
    ~Profiler();

    // Output destination for sampled frames/stacks. Must be set before
    // start(). Usually points at a ProfiledThread owned by the
    // ProfilerSession; if unset, the profiler keeps an owned fallback so
    // tests and ad-hoc embeddings don't have to plumb a session.
    void set_profiled_thread(Core::ProfiledThread& thread)
    {
        m_profiled_thread = &thread;
        m_fallback_profiled_thread = nullptr;
    }
    Core::ProfiledThread* profiled_thread() { return m_profiled_thread; }
    Core::ProfiledThread const* profiled_thread() const { return m_profiled_thread; }

    void start();
    void stop();

    // Captures a pending sample at the next bytecode dispatch boundary. Linux uses
    // this for signal-driven safe-point sampling; tests also use it explicitly.
    void sample_if_needed();
    void request_sample_for_test();
    bool supports_timed_sampling() const;
    bool needs_bytecode_safe_points() const;

    // Forward to the output ProfiledThread when one is set, so callers
    // (gecko exporter, tests) keep reading the sampled state through the
    // profiler during the step 4 transition.
    Vector<String> const& string_table() const { return m_profiled_thread->string_table; }
    Vector<Core::ProfiledFrame> const& frame_table() const { return m_profiled_thread->frame_table; }
    Vector<Core::ProfiledStack> const& stack_table() const { return m_profiled_thread->stack_table; }
    Vector<Core::ProfiledSample> const& samples() const { return m_profiled_thread->samples; }

    struct NetworkTimings {
        double domain_lookup_start_ms { 0 };
        double domain_lookup_end_ms { 0 };
        double connect_start_ms { 0 };
        double tcp_connect_end_ms { 0 };
        double secure_connection_start_ms { 0 };
        double connect_end_ms { 0 };
        double request_start_ms { 0 };
        double response_start_ms { 0 };
        double response_end_ms { 0 };
    };

    // Network markers: two per request (STATUS_START + STATUS_STOP) linked by id.
    // Raw data is stored and serialized only during gecko profile export.
    struct NetworkMarker {
        u64 id;
        String url;
        String method;
        double start_time_ms;
        double end_time_ms;
        bool is_stop { false };
        u32 status_code { 0 };
        String content_type;
        i64 body_size { 0 };
        NetworkTimings timings;
    };
    Vector<NetworkMarker> const& network_markers() const { return m_network_markers; }

    void add_network_request_start(u64 id, String url, String method, double start_time_ms);
    void add_network_request_stop(u64 id, String url, String method, double start_time_ms, double end_time_ms,
        u32 status_code, String content_type, i64 body_size, NetworkTimings const&);

    double elapsed_ms_since_start() const;
    int interval_us() const { return m_interval_us; }
    i64 start_time_epoch_ms() const;
    i64 stop_time_epoch_ms() const;
    u64 os_tid() const;

    // Walk the JS execution context stack and produce a list of frame names.
    // Used by MarkerCollector to attach a "cause" stack to each marker.
    // Cheap if the stack is small; allocates per call.
    void capture_marker_stack(Vector<Core::MarkerStackFrame, 8>& out);

private:
    static constexpr u32 MAX_STACK_DEPTH = 64;
    static constexpr u32 MAX_RAW_SAMPLES = 16384;

    // capture_sample() runs either in a signal handler (Linux) or while the JS thread
    // is suspended via Mach (macOS).  In both cases the GC cannot run, so GC-managed
    // objects on the execution-context stack are alive — but only for the duration of
    // capture_sample() itself.  Once the JS thread resumes the GC is free to collect them,
    // so by the time process_raw_samples() runs they may be gone.
    //
    // Consequences for UnprocessedFrame:
    //  - No GC::Ptr: raw GC pointers are only safe to read during capture_sample().
    //  - No heap allocation: malloc may deadlock if the suspended thread was inside it.
    //  - The frame name is copied from Bytecode::Executable::name while the executable is live.
    struct UnprocessedFrame {
        FlatPtr executable;  // Bytecode::Executable const* — GC cell, valid only during capture_sample()
                             // SENTINEL: 0 means this is a synthetic marker scope frame, not a JS frame.
        u32 program_counter; // offset into executable->bytecode (not a machine PC)
                             // For synthetic marker frames: low byte = MarkerCategory enum value.
        Utf16FlyString name; // For JS frames: function name. For marker frames: empty.
        // For synthetic marker frames only: raw pointer to the marker name string
        // (always a static string literal, so the pointer is stable). Read at
        // process_raw_samples() time, never inside the signal handler.
        char const* marker_name_ptr { nullptr };
        u32 marker_name_len { 0 };
    };
    struct RawSample {
        double time_ms;
        u32 frame_count;
        UnprocessedFrame frames[MAX_STACK_DEPTH];
    };

    static void signal_handler(int, siginfo_t*, void*);

    // Async-signal-safe: called from a POSIX signal handler (Linux) or while the JS
    // thread is Mach-suspended (macOS).  Must not allocate or call non-reentrant
    // functions — see UnprocessedFrame comment above.
    // leaf_program_counter: register-derived PC for the topmost frame (macOS path);
    // absent on the Linux safe-point path, where ctx->program_counter is used instead.
    void capture_sample(Optional<u32> leaf_program_counter);
    void reset_state_for_start();
    void reserve_output_tables();
    void allocate_raw_samples();
    void process_and_free_raw_samples();
    void capture_frames(RawSample&, Optional<u32> leaf_program_counter);
    void stop_timer_thread();
    void allocate_sample_buffer();
    void collect_and_free_samples();

    void process_raw_samples();
    u32 intern_stack_trace(RawSample const&);

    VM& m_vm;
    int m_interval_us;
    Core::ProfiledThread* m_profiled_thread { nullptr };
    // Owned fallback when no session-owned ProfiledThread has been supplied
    // (used by the test harness and any ad-hoc embedding). Goes away once
    // all callers plumb ProfilerSession through.
    OwnPtr<Core::ProfiledThread> m_fallback_profiled_thread;
    ::MonotonicTime m_start_time { ::MonotonicTime::now() };
    i64 m_start_epoch_ms { 0 };
    i64 m_stop_epoch_ms { 0 };
    Atomic<bool> m_timer_running { false };
    RefPtr<Threading::Thread> m_timer_thread;
    pthread_t m_js_thread {};

    RawSample* m_raw_samples { nullptr };
    Atomic<u32> m_raw_sample_count { 0 };

    // Set either by the Linux signal handler or by tests requesting a sample,
    // then consumed by sample_if_needed() at the next safe bytecode boundary.
    Atomic<bool> m_sample_pending { false };
    struct sigaction m_old_sigaction {};
    bool m_platform_sampling_active { false };

    Vector<NetworkMarker> m_network_markers;
};

inline i64 Profiler::start_time_epoch_ms() const
{
    return m_start_epoch_ms;
}

inline i64 Profiler::stop_time_epoch_ms() const
{
    return m_stop_epoch_ms;
}

inline u64 Profiler::os_tid() const
{
    return static_cast<u64>(reinterpret_cast<uintptr_t>(m_js_thread));
}

}
