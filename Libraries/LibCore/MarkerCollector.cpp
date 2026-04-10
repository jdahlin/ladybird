/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/StringBuilder.h>
#include <LibCore/MarkerCollector.h>
#include <LibCore/Profiler/Label.h>
#include <LibCore/Profiler/ThreadRegistry.h>

namespace Core {

MarkerCollector* g_marker_collector { nullptr };

MarkerCollector::MarkerCollector()
{
    m_markers.ensure_capacity(4096);
    m_schemas.ensure_capacity(8);

    register_schema({ "Text"sv,
        { { "name"sv, "Details"sv, MarkerSchema::Field::Format::String, true } },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable },
        {},
        "{marker.data.name}"sv,
        "{marker.name}"sv });

    register_schema({ "DOMEvent"sv,
        {
            { "eventType"sv, "Event Type"sv, MarkerSchema::Field::Format::String, true },
            { "target"sv, "Target"sv, MarkerSchema::Field::Format::String, true },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable,
            MarkerSchema::Location::TimelineOverview },
        "{marker.data.eventType} \u2014 DOMEvent"sv,
        "{marker.data.eventType} \u2014 {marker.data.target}"sv,
        "{marker.data.eventType}"sv });

    register_schema({ "CSSAnimation"sv,
        {
            { "animationName"sv, "Animation Name"sv, MarkerSchema::Field::Format::String, true },
            { "eventType"sv, "Event Type"sv, MarkerSchema::Field::Format::String },
            { "elapsedTime"sv, "Elapsed Time"sv, MarkerSchema::Field::Format::Milliseconds },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable },
        {},
        "{marker.data.eventType} \u2014 {marker.data.animationName}"sv,
        "{marker.data.animationName}"sv });

    register_schema({ "CSSTransition"sv,
        {
            { "propertyName"sv, "Property"sv, MarkerSchema::Field::Format::String, true },
            { "eventType"sv, "Event Type"sv, MarkerSchema::Field::Format::String },
            { "elapsedTime"sv, "Elapsed Time"sv, MarkerSchema::Field::Format::Milliseconds },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable },
        {},
        "{marker.data.eventType} \u2014 {marker.data.propertyName}"sv,
        "{marker.data.propertyName}"sv });

    // performance.mark() and performance.measure() — produced by the User Timing API.
    register_schema({ "UserTiming"sv,
        {
            { "name"sv, "Name"sv, MarkerSchema::Field::Format::String, true },
            { "entryType"sv, "Entry Type"sv, MarkerSchema::Field::Format::String },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable,
            MarkerSchema::Location::TimelineOverview },
        "UserTiming \u2014 {marker.data.name}"sv,
        "{marker.data.entryType}: {marker.data.name}"sv,
        "{marker.data.name}"sv });

    // console.time() / console.timeEnd() — paired with the same label.
    register_schema({ "ConsoleTime"sv,
        {
            { "name"sv, "Label"sv, MarkerSchema::Field::Format::String, true },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable },
        "console.time \u2014 {marker.data.name}"sv,
        "{marker.data.name}"sv,
        "{marker.data.name}"sv });

    // Style invalidation — colored purple in the Layout category track.
    // Include TimelineOverview so it shows up on the global timeline track.
    register_schema({ "Style"sv,
        {
            { "elements"sv, "Elements"sv, MarkerSchema::Field::Format::Integer, false },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable,
            MarkerSchema::Location::TimelineOverview },
        "Recalculate Style"sv, "Recalculate Style"sv, "Style"sv });

    // Layout pass with reason.
    register_schema({ "Layout"sv,
        {
            { "reason"sv, "Reason"sv, MarkerSchema::Field::Format::String, true },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable,
            MarkerSchema::Location::TimelineOverview },
        "Layout: {marker.data.reason}"sv, "{marker.data.reason}"sv, "Layout"sv });

    // Display list build / paint pass.
    register_schema({ "Paint"sv,
        {},
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable,
            MarkerSchema::Location::TimelineOverview },
        "Paint"sv, "Paint"sv, "Paint"sv });

    // GC: full vs partial collection.
    register_schema({ "GC"sv,
        {
            { "kind"sv, "Kind"sv, MarkerSchema::Field::Format::String, true },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable },
        "GC: {marker.data.kind}"sv, "{marker.data.kind}"sv, "GC"sv });

    // Parse / Compile script or module.
    register_schema({ "ParseScript"sv,
        {
            { "kind"sv, "Kind"sv, MarkerSchema::Field::Format::String, true },
            { "size"sv, "Size (code units)"sv, MarkerSchema::Field::Format::Integer, false },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable,
            MarkerSchema::Location::TimelineOverview },
        "Parse {marker.data.kind}"sv, "Parse {marker.data.kind} ({marker.data.size})"sv,
        "Parse {marker.data.kind}"sv });

    register_schema({ "CompileScript"sv,
        {
            { "kind"sv, "Kind"sv, MarkerSchema::Field::Format::String, true },
            { "size"sv, "Size (code units)"sv, MarkerSchema::Field::Format::Integer, false },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable,
            MarkerSchema::Location::TimelineOverview },
        "Compile {marker.data.kind}"sv, "Compile {marker.data.kind} ({marker.data.size})"sv,
        "Compile {marker.data.kind}"sv });

    register_schema({ "EvaluateScript"sv,
        {
            { "url"sv, "URL"sv, MarkerSchema::Field::Format::Url, true },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable,
            MarkerSchema::Location::TimelineOverview },
        "Evaluate script"sv, "Evaluate {marker.data.url}"sv, "Evaluate"sv });

    // CSS parsing — separate schema so it shows on the global timeline.
    register_schema({ "ParseCSS"sv,
        {
            { "url"sv, "URL"sv, MarkerSchema::Field::Format::Url, true },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable,
            MarkerSchema::Location::TimelineOverview },
        "Parse CSS: {marker.data.url}"sv, "{marker.data.url}"sv, "CSS"sv });

    // IPC message handling.
    register_schema({ "IPCHandle"sv,
        {
            { "name"sv, "Message"sv, MarkerSchema::Field::Format::String, true },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable },
        "IPC: {marker.data.name}"sv, "{marker.data.name}"sv, "{marker.data.name}"sv });

    // Microtask checkpoint — only emitted for non-trivial queues.
    register_schema({ "MicrotaskCheckpoint"sv,
        {
            { "count"sv, "Microtask count"sv, MarkerSchema::Field::Format::Integer, false },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable },
        "Microtask checkpoint ({marker.data.count})"sv,
        "{marker.data.count} microtasks"sv,
        "Microtasks"sv });

    // EventLoop task processing — name comes from task source.
    register_schema({ "Task"sv,
        {
            { "source"sv, "Task source"sv, MarkerSchema::Field::Format::String, true },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable },
        "Task: {marker.data.source}"sv, "{marker.data.source}"sv, "{marker.data.source}"sv });

    // Image decode — async, captures URL and byte size.
    register_schema({ "ImageDecode"sv,
        {
            { "url"sv, "URL"sv, MarkerSchema::Field::Format::Url, true },
            { "bytes"sv, "Bytes"sv, MarkerSchema::Field::Format::Bytes, false },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable },
        "Image decode"sv, "{marker.data.url}"sv, "Image decode"sv });

    // Media demux/decode (audio + video).
    register_schema({ "MediaDecode"sv,
        {
            { "kind"sv, "Stream"sv, MarkerSchema::Field::Format::String, true },
            { "bytes"sv, "Bytes"sv, MarkerSchema::Field::Format::Bytes, false },
            { "pts_us"sv, "Presentation Time"sv, MarkerSchema::Field::Format::Integer, false },
        },
        { MarkerSchema::Location::MarkerChart, MarkerSchema::Location::MarkerTable },
        "{marker.name}: {marker.data.kind}"sv,
        "{marker.data.kind} pts={marker.data.pts_us}us"sv,
        "{marker.data.kind}"sv });
}

static StringView phase_name(MarkerPhase phase)
{
    switch (phase) {
    case MarkerPhase::Instant:
        return "Instant"sv;
    case MarkerPhase::Interval:
        return "Interval"sv;
    case MarkerPhase::IntervalStart:
        return "IntervalStart"sv;
    case MarkerPhase::IntervalEnd:
        return "IntervalEnd"sv;
    }
    VERIFY_NOT_REACHED();
}

static StringView marker_string_view(MarkerString const& s)
{
    return s.visit(
        [](StringView sv) { return sv; },
        [](String const& str) { return str.bytes_as_string_view(); });
}

void MarkerCollector::add_marker(Marker marker)
{
    if (marker.tid == 0)
        marker.tid = marker_current_tid();

    // Capture JS call stack if a profiler is attached. The capture function
    // self-checks that it's running on the JS main thread; from other threads
    // it leaves marker.stack empty.
    if (m_stack_capture)
        m_stack_capture(marker.stack);

    if (m_debug) {
        StringBuilder sb;
        sb.append("MARKER: "sv);
        sb.append(phase_name(marker.phase));
        sb.append(' ');
        sb.append(marker.type);
        sb.append(" \""sv);
        sb.append(marker_string_view(marker.name));
        sb.append('"');
        for (auto const& field : marker.fields) {
            sb.append(' ');
            sb.append(field.key);
            sb.append('=');
            field.value.visit(
                [&](StringView sv) { sb.append(sv); },
                [&](String const& s) { sb.append(s); },
                [&](double d) { sb.appendff("{}", d); },
                [&](i64 i) { sb.appendff("{}", i); },
                [&](bool b) { sb.append(b ? "true"sv : "false"sv); });
        }
        dbgln("{}", sb.string_view());
    }
    m_markers.append(move(marker));
}

void MarkerCollector::register_schema(MarkerSchema schema)
{
    m_schemas.append(move(schema));
}

void MarkerCollector::clear()
{
    m_markers.clear_with_capacity();
}

// Implementation entry points — called only from macros that have already
// verified g_marker_collector is non-null.

void marker_do_add_instant(MarkerString name, StringView type, MarkerCategory category,
    Vector<MarkerField, 4> fields)
{
    auto* c = g_marker_collector;
    if (!c)
        return;
    auto now = MonotonicTime::now();
    c->add_marker({ move(name), type, now, now, MarkerPhase::Instant, category, 0, move(fields), {} });
}

void marker_do_add_interval(MarkerString name, StringView type, MarkerCategory category,
    MonotonicTime start, Vector<MarkerField, 4> fields)
{
    auto* c = g_marker_collector;
    if (!c)
        return;
    c->add_marker({ move(name), type, start, MonotonicTime::now(), MarkerPhase::Interval, category, 0, move(fields), {} });
}

void marker_do_add_interval_explicit(MarkerString name, StringView type, MarkerCategory category,
    MonotonicTime start, MonotonicTime end, Vector<MarkerField, 4> fields)
{
    auto* c = g_marker_collector;
    if (!c)
        return;
    c->add_marker({ move(name), type, start, end, MarkerPhase::Interval, category, 0, move(fields), {} });
}

void marker_do_add_interval_start(MarkerString name, StringView type, MarkerCategory category,
    Vector<MarkerField, 4> fields)
{
    auto* c = g_marker_collector;
    if (!c)
        return;
    auto now = MonotonicTime::now();
    c->add_marker({ move(name), type, now, now, MarkerPhase::IntervalStart, category, 0, move(fields), {} });
}

void marker_do_add_interval_end(MarkerString name, StringView type, MarkerCategory category,
    Vector<MarkerField, 4> fields)
{
    auto* c = g_marker_collector;
    if (!c)
        return;
    auto now = MonotonicTime::now();
    c->add_marker({ move(name), type, now, now, MarkerPhase::IntervalEnd, category, 0, move(fields), {} });
}

void marker_do_add_text(MarkerString name, MarkerCategory category, MarkerString text)
{
    marker_do_add_instant(move(name), "Text"sv, category, { { "name"sv, move(text) } });
}

// MarkerScope: combined label + interval marker. Pushes a frame onto the
// calling thread's FixedProfilingStack for the lifetime of the scope, and
// emits an interval marker on destruction. Equivalent to pairing
// PROFILER_LABEL with MARKER_INTERVAL in one RAII helper.
MarkerScope::MarkerScope(StringView name, StringView schema_type, MarkerCategory category)
    : m_name(name)
    , m_schema_type(schema_type)
    , m_category(category)
    , m_start(MonotonicTime::now())
    , m_active(true)
{
    ensure_profiler_state().profiling_stack.push(name, category);
}

MarkerScope::MarkerScope(StringView name, StringView schema_type, MarkerCategory category, Vector<MarkerField, 4> fields)
    : m_name(name)
    , m_schema_type(schema_type)
    , m_category(category)
    , m_start(MonotonicTime::now())
    , m_fields(move(fields))
    , m_active(true)
{
    ensure_profiler_state().profiling_stack.push(name, category);
}

MarkerScope::~MarkerScope()
{
    if (!m_active)
        return;
    if (auto* state = t_profiler_state)
        state->profiling_stack.pop();
    if (g_marker_collector) [[unlikely]]
        marker_do_add_interval(m_name, m_schema_type, m_category, m_start, move(m_fields));
}

}

// C ABI for FFI consumers (Rust, etc.).
// Safe to call from any thread before/after profiling — null collector is a no-op.
extern "C" {

void ladybird_marker_thread_register(char const* name, size_t name_length)
{
    ::Core::profiler_thread_register(StringView { name, name_length });
}

void ladybird_marker_thread_unregister()
{
    ::Core::profiler_thread_unregister();
}

bool ladybird_marker_collector_is_active()
{
    return ::Core::g_marker_collector != nullptr;
}
}
