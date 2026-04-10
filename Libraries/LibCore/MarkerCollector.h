/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/Function.h>
#include <AK/HashMap.h>
#include <AK/Optional.h>
#include <AK/Platform.h>
#include <AK/String.h>
#include <AK/StringView.h>
#include <AK/Time.h>
#include <AK/Variant.h>
#include <AK/Vector.h>
#include <LibCore/Export.h>
#include <LibCore/MarkerCategory.h>
#include <pthread.h>

namespace Core {

enum class MarkerPhase : u8 {
    Instant = 0,
    Interval = 1,
    IntervalStart = 2,
    IntervalEnd = 3,
};

// Either a static StringView (literal — no allocation) or a heap-owned String.
// Most marker names are literals; dynamic names from event types use String.
using MarkerString = Variant<StringView, String>;

// Field payload value. StringView for literals, String for dynamic strings.
using MarkerFieldValue = Variant<StringView, String, double, i64, bool>;

struct MarkerField {
    StringView key;
    MarkerFieldValue value;
};

// A single stack frame captured at marker emit time. The Profiler fills these
// when its stack_capture callback is invoked. Storing as a flat name string
// avoids cross-library type dependencies (LibCore can't see LibJS::Bytecode).
struct MarkerStackFrame {
    String location; // e.g., "myFunction (foo.js:12:3)" or "(anonymous)"
    u32 line { 0 };
    u32 column { 0 };
};

// Marker stores absolute MonotonicTime — converted to profile-relative ms at export.
struct Marker {
    MarkerString name;
    StringView type;
    MonotonicTime start;
    MonotonicTime end;
    MarkerPhase phase;
    MarkerCategory category;
    u64 tid { 0 }; // OS thread id (pthread_self cast). 0 = unknown / main.
    Vector<MarkerField, 4> fields;
    // Captured JS call stack at emit time. Empty if no profiler is attached or
    // if the marker did not request stack capture. Innermost frame at index 0.
    Vector<MarkerStackFrame, 8> stack;
};

struct MarkerSchema {
    struct Field {
        StringView key;
        StringView label;
        enum class Format { String,
            Url,
            Duration,
            Time,
            Milliseconds,
            Integer,
            Decimal,
            Bytes };
        Format format;
        bool searchable { false };
    };
    enum class Location { MarkerChart,
        MarkerTable,
        TimelineOverview };

    StringView name;
    Vector<Field> fields;
    Vector<Location> locations;
    StringView tooltip_label {};
    StringView table_label {};
    StringView chart_label {};
};

struct ThreadInfo {
    String name;
    MonotonicTime register_time;
};

// Counter sample — a single (time, value) point.
// Used for memory/CPU/etc. graphs at the top of the timeline.
struct CounterSample {
    MonotonicTime time;
    i64 count;  // total at this point in time (or delta — depends on counter)
    i64 number; // number of operations since previous sample (e.g., allocations)
};

struct CounterSeries {
    String name;     // "malloc", "processCPU", "threadCPU.<name>", etc.
    String category; // "Memory", "CPU"
    String description;
    Vector<CounterSample> samples;
};

class CORE_API MarkerCollector {
public:
    MarkerCollector();

    // Set by the JS Profiler at start time. When non-null, add_marker invokes
    // this to capture a snapshot of the current JS call stack and stores it on
    // the marker. The capture function should be cheap (no allocation in hot
    // paths if possible) and must be safe to call from any thread.
    using StackCaptureFn = Function<void(Vector<MarkerStackFrame, 8>&)>;
    void set_stack_capture(StackCaptureFn capture) { m_stack_capture = move(capture); }

    void add_marker(Marker);
    void register_schema(MarkerSchema);

    // Process metadata (set once at construction by the owner)
    void set_process_name(String name) { m_process_name = move(name); }
    void set_process_type(String type) { m_process_type = move(type); }
    String const& process_name() const { return m_process_name; }
    String const& process_type() const { return m_process_type; }

    // Thread registration. Multiple threads may add markers; each should
    // register itself once at startup so its name appears in the export.
    void register_thread(u64 tid, String name);
    void unregister_thread(u64 tid);
    HashMap<u64, ThreadInfo> const& threads() const { return m_threads; }

    // Counter sampling. Each named series (e.g., "malloc", "processCPU") is a
    // graph row at the top of the profile timeline.
    void add_counter_sample(StringView name, StringView category, StringView description, i64 count, i64 number = 0);
    HashMap<String, CounterSeries> const& counters() const { return m_counters; }

    Vector<Marker> const& markers() const { return m_markers; }
    Vector<MarkerSchema> const& schemas() const { return m_schemas; }

    void set_debug(bool debug) { m_debug = debug; }
    bool debug() const { return m_debug; }

    void clear();

private:
    Vector<Marker> m_markers;
    Vector<MarkerSchema> m_schemas;
    HashMap<u64, ThreadInfo> m_threads;
    HashMap<String, CounterSeries> m_counters;
    StackCaptureFn m_stack_capture;
    String m_process_name;
    String m_process_type;
    bool m_debug { false };
};

// Global pointer — null when no collector is active. Set once at start, cleared at stop.
// Single-threaded write per thread; the collector itself takes a lock for cross-thread adds.
extern CORE_API MarkerCollector* g_marker_collector;

// Cheap thread id read (TLS on Linux/macOS).
ALWAYS_INLINE u64 marker_current_tid() { return reinterpret_cast<uintptr_t>(pthread_self()); }

ALWAYS_INLINE MonotonicTime marker_now() { return MonotonicTime::now(); }

// ── Implementation entry points ──
// These are called by the macros below — never call directly. They assume the
// global collector is non-null (the macro guards that). MarkerString accepts
// either StringView (literals, no allocation) or String (dynamic names).
CORE_API void marker_do_add_instant(MarkerString name, StringView type, MarkerCategory category,
    Vector<MarkerField, 4> fields);
CORE_API void marker_do_add_interval(MarkerString name, StringView type, MarkerCategory category,
    MonotonicTime start, Vector<MarkerField, 4> fields);
CORE_API void marker_do_add_interval_explicit(MarkerString name, StringView type, MarkerCategory category,
    MonotonicTime start, MonotonicTime end, Vector<MarkerField, 4> fields);
CORE_API void marker_do_add_interval_start(MarkerString name, StringView type, MarkerCategory category,
    Vector<MarkerField, 4> fields);
CORE_API void marker_do_add_interval_end(MarkerString name, StringView type, MarkerCategory category,
    Vector<MarkerField, 4> fields);
CORE_API void marker_do_add_text(MarkerString name, MarkerCategory category, MarkerString text);

// Register the calling thread under a human-readable name. Cheap no-op if no
// collector is active. Safe to call multiple times (re-registers the name).
CORE_API void marker_thread_register(StringView name);
CORE_API void marker_thread_unregister();

// RAII marker scope. Pushes a frame onto the thread-local marker scope stack on
// construction; pops it and emits an interval marker on destruction. Safe to use
// even when no profiler is attached — push/pop is a cheap thread-local vector op.
//
// Critical: this is what makes layout/style/paint TIME visible in the call tree.
// During the scope, every JS sample taken by the Profiler will have this marker
// name as a frame at the bottom of the captured stack. profiler.firefox.com's
// call tree view then shows "Layout 30% > recompute > ...".
class CORE_API MarkerScope {
public:
    MarkerScope(StringView name, StringView schema_type, MarkerCategory category);
    MarkerScope(StringView name, StringView schema_type, MarkerCategory category, Vector<MarkerField, 4> fields);
    ~MarkerScope();

    MarkerScope(MarkerScope const&) = delete;
    MarkerScope& operator=(MarkerScope const&) = delete;
    MarkerScope(MarkerScope&&) = delete;
    MarkerScope& operator=(MarkerScope&&) = delete;

private:
    StringView m_name;
    StringView m_schema_type;
    MarkerCategory m_category;
    MonotonicTime m_start;
    Vector<MarkerField, 4> m_fields;
    bool m_active { false };
};

}

// C ABI for FFI consumers (Rust, etc.). Declared at namespace scope so the
// linker exports them. Safe to call from any thread; null collector is no-op.
extern "C" {
CORE_API void ladybird_marker_thread_register(char const* name, size_t name_length);
CORE_API void ladybird_marker_thread_unregister();
CORE_API bool ladybird_marker_collector_is_active();
}

// ── Public macro API ──
// These gate ALL argument evaluation behind a single null-pointer check, so
// when no profiler is active there is truly zero work — no String allocations,
// no clock_gettime calls, nothing. Macros use the (...) form so callers can
// pass brace-enclosed initializer lists for the fields parameter.

// Capture a start time only when profiling. When disabled the Optional is empty
// (just sets a tag byte — no clock_gettime, no allocation). MARKER_INTERVAL
// dereferences VAR but only after re-checking the collector, so the value is
// only read when it was actually populated.
#define MARKER_START_TIME(VAR)                                                        \
    ::AK::Optional<::AK::MonotonicTime> VAR = (::Core::g_marker_collector != nullptr) \
        ? ::AK::Optional<::AK::MonotonicTime> { ::Core::marker_now() }                \
        : ::AK::Optional<::AK::MonotonicTime> { }

#define MARKER_INSTANT(NAME, TYPE, CATEGORY, ...)                             \
    do {                                                                      \
        if (::Core::g_marker_collector) [[unlikely]]                          \
            ::Core::marker_do_add_instant(NAME, TYPE, CATEGORY, __VA_ARGS__); \
    } while (0)

// Note: also checks (START).has_value() because profiling may have started AFTER
// MARKER_START_TIME was captured. If so, we silently skip the marker rather than crash.
#define MARKER_INTERVAL(NAME, TYPE, CATEGORY, START, ...)                                \
    do {                                                                                 \
        if (::Core::g_marker_collector && (START).has_value()) [[unlikely]]              \
            ::Core::marker_do_add_interval(NAME, TYPE, CATEGORY, *(START), __VA_ARGS__); \
    } while (0)

#define MARKER_INTERVAL_EXPLICIT(NAME, TYPE, CATEGORY, START, END, ...)                             \
    do {                                                                                            \
        if (::Core::g_marker_collector) [[unlikely]]                                                \
            ::Core::marker_do_add_interval_explicit(NAME, TYPE, CATEGORY, START, END, __VA_ARGS__); \
    } while (0)

#define MARKER_INTERVAL_START(NAME, TYPE, CATEGORY, ...)                             \
    do {                                                                             \
        if (::Core::g_marker_collector) [[unlikely]]                                 \
            ::Core::marker_do_add_interval_start(NAME, TYPE, CATEGORY, __VA_ARGS__); \
    } while (0)

#define MARKER_INTERVAL_END(NAME, TYPE, CATEGORY, ...)                             \
    do {                                                                           \
        if (::Core::g_marker_collector) [[unlikely]]                               \
            ::Core::marker_do_add_interval_end(NAME, TYPE, CATEGORY, __VA_ARGS__); \
    } while (0)

#define MARKER_TEXT(NAME, CATEGORY, TEXT)                     \
    do {                                                      \
        if (::Core::g_marker_collector) [[unlikely]]          \
            ::Core::marker_do_add_text(NAME, CATEGORY, TEXT); \
    } while (0)

#define MARKER_THREAD_REGISTER(NAME)                 \
    do {                                             \
        if (::Core::g_marker_collector) [[unlikely]] \
            ::Core::marker_thread_register(NAME);    \
    } while (0)

// MARKER_SCOPE — RAII wrapper that pushes a frame onto the marker scope stack
// for the lifetime of the enclosing scope. The Profiler picks it up at sample
// time so the call tree shows time spent in this scope.
//
// Use this for wrap-a-block work like update_layout, update_style, GC, paint, etc.
// Use MARKER_INSTANT for point-in-time events.
#define MARKER_SCOPE_IMPL2(NAME, TYPE, CATEGORY, COUNTER) \
    ::Core::MarkerScope _marker_scope_##COUNTER { NAME, TYPE, CATEGORY }
#define MARKER_SCOPE_IMPL1(NAME, TYPE, CATEGORY, COUNTER) MARKER_SCOPE_IMPL2(NAME, TYPE, CATEGORY, COUNTER)
#define MARKER_SCOPE(NAME, TYPE, CATEGORY) MARKER_SCOPE_IMPL1(NAME, TYPE, CATEGORY, __COUNTER__)

// MARKER_SCOPE_FIELDS — same as MARKER_SCOPE but attaches a payload of fields to
// the interval marker emitted on scope exit. The fields are constructed at
// scope-entry; for callers that allocate strings in field values, gate the
// surrounding code with `if (Core::g_marker_collector)` to keep the noop path
// truly free.
#define MARKER_SCOPE_FIELDS_IMPL2(NAME, TYPE, CATEGORY, COUNTER, ...) \
    ::Core::MarkerScope _marker_scope_##COUNTER { NAME, TYPE, CATEGORY, __VA_ARGS__ }
#define MARKER_SCOPE_FIELDS_IMPL1(NAME, TYPE, CATEGORY, COUNTER, ...) MARKER_SCOPE_FIELDS_IMPL2(NAME, TYPE, CATEGORY, COUNTER, __VA_ARGS__)
#define MARKER_SCOPE_FIELDS(NAME, TYPE, CATEGORY, ...) MARKER_SCOPE_FIELDS_IMPL1(NAME, TYPE, CATEGORY, __COUNTER__, __VA_ARGS__)
