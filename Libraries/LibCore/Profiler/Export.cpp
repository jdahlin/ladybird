/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

// Gecko Profile Format v34 writer.
// Reference: gecko-profile-format-reference.md (generated from profiler source)
// Source of truth: https://github.com/firefox-devtools/profiler/blob/8114501/src/types/gecko-profile.ts

#include <AK/HashTable.h>
#include <AK/JsonArray.h>
#include <AK/JsonObject.h>
#include <AK/StringBuilder.h>
#include <AK/Time.h>
#include <LibCore/MarkerCollector.h>
#include <LibCore/Profiler/CounterRegistry.h>
#include <LibCore/Profiler/Export.h>
#include <LibCore/Profiler/NetworkMarkerStore.h>
#include <LibCore/Profiler/ProfiledThread.h>
#include <LibCore/Profiler/ProfilerSession.h>
#include <LibCore/Profiler/ThreadRegistry.h>
#include <LibCore/System.h>
#include <unistd.h>

namespace Core {

static JsonObject schema_data_table(std::initializer_list<StringView> columns, JsonArray data)
{
    JsonObject schema;
    int i = 0;
    for (auto col : columns)
        schema.set(col, i++);
    JsonObject table;
    table.set("schema"sv, move(schema));
    auto length = data.size();
    table.set("data"sv, move(data));
    table.set("length"sv, length);
    return table;
}

// Ordered list of marker categories, matching MarkerCategory enum values.
// Index used for frame.category and marker.category in the gecko profile.
static JsonArray build_categories()
{
    struct CategoryDef {
        StringView name;
        StringView color;
    };
    auto def_for = [](MarkerCategory cat) -> CategoryDef {
        switch (cat) {
        case MarkerCategory::Other:
            return { "Other"sv, "grey"sv };
        case MarkerCategory::Idle:
            return { "Idle"sv, "transparent"sv };
        case MarkerCategory::Layout:
            return { "Layout"sv, "purple"sv };
        case MarkerCategory::JavaScript:
            return { "JavaScript"sv, "yellow"sv };
        case MarkerCategory::GC:
            return { "GC / CC"sv, "orange"sv };
        case MarkerCategory::Network:
            return { "Network"sv, "lightblue"sv };
        case MarkerCategory::Graphics:
            return { "Graphics"sv, "green"sv };
        case MarkerCategory::DOM:
            return { "DOM"sv, "blue"sv };
        case MarkerCategory::IPC:
            return { "IPC"sv, "lightgreen"sv };
        case MarkerCategory::Media:
            return { "Media"sv, "orange"sv };
        case MarkerCategory::Timer:
            return { "Timer"sv, "grey"sv };
        case MarkerCategory::Profiler:
            return { "Profiler"sv, "lightred"sv };
        case MarkerCategory::Accessibility:
            return { "Accessibility"sv, "brown"sv };
        case MarkerCategory::Style:
            return { "Style"sv, "purple"sv };
        case MarkerCategory::Paint:
            return { "Paint"sv, "green"sv };
        case MarkerCategory::Parser:
            return { "Parser"sv, "yellow"sv };
        }
        VERIFY_NOT_REACHED();
    };

    constexpr MarkerCategory all_categories[] = {
        MarkerCategory::Other,
        MarkerCategory::Idle,
        MarkerCategory::Layout,
        MarkerCategory::JavaScript,
        MarkerCategory::GC,
        MarkerCategory::Network,
        MarkerCategory::Graphics,
        MarkerCategory::DOM,
        MarkerCategory::IPC,
        MarkerCategory::Media,
        MarkerCategory::Timer,
        MarkerCategory::Profiler,
        MarkerCategory::Accessibility,
        MarkerCategory::Style,
        MarkerCategory::Paint,
        MarkerCategory::Parser,
    };

    JsonArray categories;
    for (auto cat : all_categories) {
        auto def = def_for(cat);
        JsonObject obj;
        obj.set("name"sv, def.name);
        obj.set("color"sv, def.color);
        JsonArray sub;
        sub.must_append("Other"sv);
        obj.set("subcategories"sv, move(sub));
        categories.must_append(move(obj));
    }
    return categories;
}

static constexpr auto CATEGORY_NETWORK = MarkerCategory::Network;

static StringView format_to_string(MarkerSchema::Field::Format format)
{
    switch (format) {
    case MarkerSchema::Field::Format::String:
        return "string"sv;
    case MarkerSchema::Field::Format::Url:
        return "url"sv;
    case MarkerSchema::Field::Format::Duration:
        return "duration"sv;
    case MarkerSchema::Field::Format::Time:
        return "time"sv;
    case MarkerSchema::Field::Format::Milliseconds:
        return "milliseconds"sv;
    case MarkerSchema::Field::Format::Integer:
        return "integer"sv;
    case MarkerSchema::Field::Format::Decimal:
        return "decimal"sv;
    case MarkerSchema::Field::Format::Bytes:
        return "bytes"sv;
    }
    VERIFY_NOT_REACHED();
}

static JsonArray build_marker_schemas(MarkerCollector const& collector)
{
    JsonArray schemas;
    for (auto const& schema : collector.schemas()) {
        JsonObject s;
        s.set("name"sv, schema.name);

        JsonArray display;
        for (auto loc : schema.locations) {
            switch (loc) {
            case MarkerSchema::Location::MarkerChart:
                display.must_append("marker-chart"sv);
                break;
            case MarkerSchema::Location::MarkerTable:
                display.must_append("marker-table"sv);
                break;
            case MarkerSchema::Location::TimelineOverview:
                display.must_append("timeline-overview"sv);
                break;
            }
        }
        s.set("display"sv, move(display));

        JsonArray data_fields;
        for (auto const& field : schema.fields) {
            JsonObject f;
            f.set("key"sv, field.key);
            f.set("label"sv, field.label);
            f.set("format"sv, format_to_string(field.format));
            if (field.searchable)
                f.set("searchable"sv, true);
            data_fields.must_append(move(f));
        }
        s.set("data"sv, move(data_fields));

        if (!schema.tooltip_label.is_empty())
            s.set("tooltipLabel"sv, schema.tooltip_label);
        if (!schema.table_label.is_empty())
            s.set("tableLabel"sv, schema.table_label);
        if (!schema.chart_label.is_empty())
            s.set("chartLabel"sv, schema.chart_label);

        schemas.must_append(move(s));
    }
    return schemas;
}

static JsonObject build_meta(ProfilerSession const& session)
{
    double interval_ms = session.interval_us() > 0
        ? static_cast<double>(session.interval_us()) / 1000.0
        : 1.0;
    auto start_epoch_ms = session.start_epoch_ms();
    auto stop_epoch_ms = session.stop_epoch_ms();

    JsonObject meta;
    meta.set("version"sv, 34);
    meta.set("startTime"sv, static_cast<double>(start_epoch_ms));
    meta.set("shutdownTime"sv, stop_epoch_ms > 0 ? JsonValue(static_cast<double>(stop_epoch_ms)) : JsonValue {});
    meta.set("interval"sv, interval_ms);
    meta.set("stackwalk"sv, 0);
    meta.set("debug"sv, 0);
    meta.set("gcpoison"sv, 0);
    meta.set("asyncstack"sv, 0);
    meta.set("processType"sv, 0);
    meta.set("categories"sv, build_categories());
    meta.set("markerSchema"sv, build_marker_schemas(session.markers()));

    StringView os_name =
#if defined(AK_OS_LINUX)
        "Linux"sv;
#elif defined(AK_OS_MACOS)
        "macOS"sv;
#elif defined(AK_OS_WINDOWS)
        "Windows"sv;
#else
        "Unknown"sv;
#endif

    StringView platform =
#if defined(AK_OS_LINUX)
        "X11"sv;
#elif defined(AK_OS_MACOS)
        "Macintosh"sv;
#elif defined(AK_OS_WINDOWS)
        "Windows"sv;
#else
        "Unknown"sv;
#endif

    StringView toolkit =
#if defined(AK_OS_LINUX)
        "gtk"sv;
#elif defined(AK_OS_MACOS)
        "cocoa"sv;
#elif defined(AK_OS_WINDOWS)
        "windows"sv;
#else
        "unknown"sv;
#endif

    meta.set("product"sv, "Ladybird"sv);
    meta.set("oscpu"sv, os_name);
    meta.set("platform"sv, platform);
    meta.set("toolkit"sv, toolkit);
    meta.set("abi"sv, "x86_64-gcc3"sv);
    meta.set("misc"sv, "rv:1.0"sv);
    meta.set("symbolicated"sv, true);
    meta.set("appBuildID"sv, "Ladybird"sv);
    meta.set("updateChannel"sv, "default"sv);
    meta.set("sourceURL"sv, ""sv);

    // profiler.firefox.com shows these in the "System" panel.
    auto logical_cpus = sysconf(_SC_NPROCESSORS_ONLN);
    if (logical_cpus > 0)
        meta.set("logicalCPUs"sv, static_cast<double>(logical_cpus));
    auto physical_cpus = sysconf(_SC_NPROCESSORS_CONF);
    if (physical_cpus > 0)
        meta.set("physicalCPUs"sv, static_cast<double>(physical_cpus));

    // profiler.firefox.com reads sampleUnits to label the time and
    // event-delay columns in the sample table.
    JsonObject sample_units;
    sample_units.set("time"sv, "ms"sv);
    sample_units.set("eventDelay"sv, "ms"sv);
    sample_units.set("threadCPUDelta"sv, "ns"sv);
    meta.set("sampleUnits"sv, move(sample_units));

    if (!session.process_name().is_empty())
        meta.set("processName"sv, session.process_name());

    meta.set("profilingStartTime"sv, 0.0);
    auto now = MonotonicTime::now();
    auto elapsed = now - session.start_time();
    double fallback_elapsed_ms = static_cast<double>(max(elapsed.to_microseconds(), static_cast<i64>(0))) / 1000.0;
    auto duration_ms = stop_epoch_ms > 0
        ? static_cast<double>(stop_epoch_ms - start_epoch_ms)
        : fallback_elapsed_ms;
    meta.set("profilingEndTime"sv, duration_ms);

    meta.set("sourceCodeIsNotOnSearchfox"sv, true);
    return meta;
}

static JsonObject build_samples(ProfiledThread const& thread)
{
    JsonArray data;
    for (auto const& sample : thread.samples) {
        JsonArray row;
        row.must_append(sample.stack_index);
        row.must_append(sample.time_ms);
        row.must_append(0);
        data.must_append(move(row));
    }
    return schema_data_table({ "stack"sv, "time"sv, "responsiveness"sv }, move(data));
}

static JsonObject build_stack_table(ProfiledThread const& thread)
{
    JsonArray data;
    for (auto const& stack : thread.stack_table) {
        JsonArray row;
        row.must_append(stack.prefix.has_value() ? JsonValue(stack.prefix.value()) : JsonValue {});
        row.must_append(stack.frame_index);
        data.must_append(move(row));
    }
    return schema_data_table({ "prefix"sv, "frame"sv }, move(data));
}

static JsonObject build_frame_table(ProfiledThread const& thread)
{
    JsonArray data;
    for (auto const& frame : thread.frame_table) {
        JsonArray row;
        row.must_append(frame.string_index);
        row.must_append(true);
        row.must_append(JsonValue {});
        row.must_append(JsonValue {});
        row.must_append(frame.line);
        row.must_append(frame.column);
        row.must_append(static_cast<int>(frame.category));
        row.must_append(0);
        data.must_append(move(row));
    }
    return schema_data_table(
        { "location"sv, "relevantForJS"sv, "innerWindowID"sv, "implementation"sv,
            "line"sv, "column"sv, "category"sv, "subcategory"sv },
        move(data));
}

// stack_target: when non-null, marker stacks are interned into this thread's
// frame_table/stack_table and a `cause: { stack: <index> }` field is added to
// the marker payload (matching Firefox's CauseBacktrace shape).
static JsonObject build_markers(ProfilerSession const& session, u64 thread_tid, bool include_network, JsonArray& string_table, ProfiledThread* stack_target)
{
    HashMap<String, i64> string_map;
    auto intern = [&](String const& str) -> i64 {
        if (auto it = string_map.find(str); it != string_map.end())
            return it->value;
        auto index = static_cast<i64>(string_table.size());
        string_table.must_append(str);
        string_map.set(str, index);
        return index;
    };

    JsonArray data;

    if (include_network) {
        for (auto const& nm : session.network_markers().markers()) {
            auto name_index = intern(MUST(String::formatted("Load {}: {}", nm.id, nm.url)));

            JsonObject payload;
            payload.set("type"sv, "Network"sv);
            payload.set("startTime"sv, nm.start_time_ms);
            payload.set("endTime"sv, nm.end_time_ms);
            payload.set("id"sv, static_cast<double>(nm.id));
            payload.set("URI"sv, nm.url);
            payload.set("requestMethod"sv, nm.method);

            if (nm.is_stop) {
                payload.set("status"sv, "STATUS_STOP"sv);
                if (nm.status_code > 0)
                    payload.set("responseStatus"sv, nm.status_code);
                payload.set("contentType"sv, !nm.content_type.is_empty() ? JsonValue(nm.content_type) : JsonValue {});
                if (nm.body_size > 0)
                    payload.set("count"sv, nm.body_size);

                auto const& t = nm.timings;
                if (t.domain_lookup_start_ms > 0)
                    payload.set("domainLookupStart"sv, t.domain_lookup_start_ms);
                if (t.domain_lookup_end_ms > 0)
                    payload.set("domainLookupEnd"sv, t.domain_lookup_end_ms);
                if (t.connect_start_ms > 0)
                    payload.set("connectStart"sv, t.connect_start_ms);
                if (t.tcp_connect_end_ms > 0)
                    payload.set("tcpConnectEnd"sv, t.tcp_connect_end_ms);
                if (t.secure_connection_start_ms > 0)
                    payload.set("secureConnectionStart"sv, t.secure_connection_start_ms);
                if (t.connect_end_ms > 0)
                    payload.set("connectEnd"sv, t.connect_end_ms);
                if (t.request_start_ms > 0)
                    payload.set("requestStart"sv, t.request_start_ms);
                if (t.response_start_ms > 0)
                    payload.set("responseStart"sv, t.response_start_ms);
                if (t.response_end_ms > 0)
                    payload.set("responseEnd"sv, t.response_end_ms);
            } else {
                payload.set("status"sv, "STATUS_START"sv);
                payload.set("contentType"sv, JsonValue {});
            }

            JsonArray row;
            row.must_append(name_index);
            if (nm.is_stop) {
                row.must_append(nm.start_time_ms);
                row.must_append(nm.end_time_ms);
                row.must_append(3);
            } else {
                row.must_append(nm.start_time_ms);
                row.must_append(JsonValue {});
                row.must_append(2);
            }
            row.must_append(to_underlying(CATEGORY_NETWORK));
            row.must_append(move(payload));
            data.must_append(move(row));
        }
    }

    // Convert MonotonicTime to ms-since-session-start.
    auto const session_start = session.start_time();
    auto to_relative_ms = [&](MonotonicTime t) -> double {
        auto delta = t - session_start;
        return static_cast<double>(max(delta.to_microseconds(), static_cast<i64>(0))) / 1000.0;
    };

    for (auto const& m : session.markers().markers()) {
        auto virtual_tid = m.tid == 0 ? thread_tid : m.tid;
        if (virtual_tid != thread_tid)
            continue;
        auto name_string = m.name.visit(
            [](StringView sv) { return MUST(String::from_utf8(sv)); },
            [](String const& s) { return s; });
        auto name_index = intern(name_string);

        JsonObject payload;
        payload.set("type"sv, m.type);
        for (auto const& field : m.fields) {
            field.value.visit(
                [&](StringView sv) { payload.set(field.key, sv); },
                [&](String const& s) { payload.set(field.key, s); },
                [&](double d) { payload.set(field.key, d); },
                [&](i64 i) { payload.set(field.key, static_cast<double>(i)); },
                [&](bool b) { payload.set(field.key, b); });
        }
        bool const has_start = m.phase != MarkerPhase::IntervalEnd;
        bool const has_end = m.phase == MarkerPhase::Interval || m.phase == MarkerPhase::IntervalEnd;

        if (!m.stack.is_empty() && stack_target) {
            Vector<ProfiledThread::MarkerStackFrameInput> frames;
            frames.ensure_capacity(m.stack.size());
            for (auto const& frame : m.stack)
                frames.append({ frame.location, frame.line, frame.column });
            if (auto stack_index = stack_target->intern_marker_stack(frames); stack_index.has_value()) {
                JsonObject cause;
                cause.set("tid"sv, thread_tid);
                cause.set("time"sv, has_start ? to_relative_ms(m.start) : 0.0);
                cause.set("stack"sv, *stack_index);
                payload.set("cause"sv, move(cause));
            }
        }

        JsonArray row;
        row.must_append(name_index);
        row.must_append(has_start ? JsonValue(to_relative_ms(m.start)) : JsonValue {});
        row.must_append(has_end ? JsonValue(to_relative_ms(m.end)) : JsonValue {});
        row.must_append(static_cast<int>(m.phase));
        row.must_append(to_underlying(m.category));
        row.must_append(move(payload));
        data.must_append(move(row));
    }

    return schema_data_table(
        { "name"sv, "startTime"sv, "endTime"sv, "phase"sv, "category"sv, "data"sv },
        move(data));
}

static void populate_common_thread_fields(ProfilerSession const& session, JsonObject& thread, StringView name, u64 tid, bool is_main_thread)
{
    // profiler.firefox.com treats "GeckoMain" as the special JS-capable
    // main thread. Ladybird registers its JS thread as "Main" internally
    // and we translate here so gecko sees what it expects.
    StringView exported_name = name == "Main"sv ? "GeckoMain"sv : name;
    thread.set("name"sv, exported_name);
    thread.set("isMainThread"sv, is_main_thread);
    auto process_type = !session.process_type().is_empty()
        ? session.process_type().bytes_as_string_view()
        : "default"sv;
    thread.set("processType"sv, process_type);
    if (!session.process_name().is_empty())
        thread.set("processName"sv, session.process_name());
    thread.set("tid"sv, tid);
    thread.set("pid"sv, Core::System::getpid());
    thread.set("registerTime"sv, 0.0);
    thread.set("unregisterTime"sv, JsonValue {});
    // These three are required by profiler.firefox.com's thread-shape
    // validator even when they're empty.
    thread.set("processStartupTime"sv, 0.0);
    thread.set("processShutdownTime"sv, JsonValue {});
    thread.set("pausedRanges"sv, JsonArray {});
    thread.set("usedInnerWindowIDs"sv, JsonArray {});
}

// Build one gecko thread entry from a ProfiledThread.
static JsonObject build_thread_from_profiled(ProfilerSession const& session, ProfiledThread& profiled, u64 tid, bool is_main_thread, bool include_network)
{
    // Pre-intern marker stacks into this thread's frame/stack tables BEFORE
    // we snapshot string_table — intern_marker_stack appends to all three
    // tables, so we need them to settle first.
    for (auto const& m : session.markers().markers()) {
        auto effective_tid = m.tid == 0 ? tid : m.tid;
        if (effective_tid != tid)
            continue;
        if (m.stack.is_empty())
            continue;
        Vector<ProfiledThread::MarkerStackFrameInput> frames;
        frames.ensure_capacity(m.stack.size());
        for (auto const& frame : m.stack)
            frames.append({ frame.location, frame.line, frame.column });
        (void)profiled.intern_marker_stack(frames);
    }

    JsonArray string_table;
    for (auto const& str : profiled.string_table)
        string_table.must_append(str);

    auto markers = build_markers(session, tid, include_network, string_table, &profiled);

    JsonObject thread;
    populate_common_thread_fields(session, thread, profiled.name().bytes_as_string_view(), tid, is_main_thread);
    thread.set("markers"sv, move(markers));
    thread.set("samples"sv, build_samples(profiled));
    thread.set("frameTable"sv, build_frame_table(profiled));
    thread.set("stackTable"sv, build_stack_table(profiled));
    thread.set("stringTable"sv, move(string_table));
    return thread;
}

// Build a gecko thread entry for a registered thread that has no
// profiled samples — it gets empty sample/frame/stack tables but still
// carries any markers that were emitted from it.
static JsonObject build_thread_markers_only(ProfilerSession const& session, u64 tid, StringView name)
{
    JsonArray string_table;
    auto markers = build_markers(session, tid, /* include_network = */ false, string_table, /* stack_target = */ nullptr);

    JsonObject thread;
    populate_common_thread_fields(session, thread, name, tid, /* is_main_thread = */ false);
    thread.set("markers"sv, move(markers));
    thread.set("samples"sv, schema_data_table({ "stack"sv, "time"sv, "responsiveness"sv }, JsonArray {}));
    thread.set("frameTable"sv, schema_data_table({ "location"sv, "relevantForJS"sv, "innerWindowID"sv, "implementation"sv, "line"sv, "column"sv, "category"sv, "subcategory"sv }, JsonArray {}));
    thread.set("stackTable"sv, schema_data_table({ "prefix"sv, "frame"sv }, JsonArray {}));
    thread.set("stringTable"sv, move(string_table));
    return thread;
}

static JsonArray build_threads(ProfilerSession const& session)
{
    JsonArray threads;

    // Each profiled thread maps to a gecko thread with samples. The
    // registered tid (from the module-global thread registry) is found
    // by matching the ProfiledThread's name — ThreadPool workers and
    // service-process mains get the correct OS tid.
    auto const& registry = profiler_threads();
    auto lookup_tid_for_name = [&](StringView name) -> u64 {
        for (auto const& [tid, info] : registry) {
            if (info.name == name)
                return tid;
        }
        return 0;
    };

    HashTable<u64> emitted_tids;

    bool first = true;
    for (auto const& profiled : session.profiled_threads()) {
        auto tid = lookup_tid_for_name(profiled->name());
        if (tid == 0)
            continue;
        emitted_tids.set(tid);
        // ProfiledThread is mutated (intern_marker_stack appends to its
        // intern tables) — the session is being consumed at stop time, so
        // const-stripping the dereference is safe.
        threads.must_append(build_thread_from_profiled(session, const_cast<ProfiledThread&>(*profiled), tid, /* is_main_thread = */ first, /* include_network = */ first));
        first = false;
    }

    // Registered-but-not-sampled threads (typically ThreadPool workers
    // once label-only sampling lands) plus any marker-only threads.
    for (auto const& [tid, info] : registry) {
        if (emitted_tids.contains(tid))
            continue;
        emitted_tids.set(tid);
        threads.must_append(build_thread_markers_only(session, tid, info.name.bytes_as_string_view()));
    }

    // Any remaining unknown tids that appear only in the marker stream.
    for (auto const& m : session.markers().markers()) {
        if (m.tid == 0 || emitted_tids.contains(m.tid))
            continue;
        emitted_tids.set(m.tid);
        auto fallback_name = MUST(String::formatted("Thread {}", m.tid));
        threads.must_append(build_thread_markers_only(session, m.tid, fallback_name.bytes_as_string_view()));
    }

    return threads;
}

static JsonObject build_source_table()
{
    return schema_data_table(
        { "id"sv, "filename"sv, "startLine"sv, "startColumn"sv, "sourceMapURL"sv },
        JsonArray {});
}

static JsonArray build_counters(ProfilerSession const& session)
{
    JsonArray counters;

    auto const session_start = session.start_time();
    auto to_relative_ms = [&](MonotonicTime t) -> double {
        auto delta = t - session_start;
        return static_cast<double>(max(delta.to_microseconds(), static_cast<i64>(0))) / 1000.0;
    };

    for (auto const& [_, series] : profiler_counters()) {
        JsonObject counter;
        counter.set("name"sv, series.name);
        counter.set("category"sv, series.category);
        counter.set("description"sv, series.description);
        counter.set("pid"sv, Core::System::getpid());
        counter.set("mainThreadIndex"sv, 0);

        JsonArray rows;
        for (auto const& s : series.samples) {
            JsonArray row;
            row.must_append(to_relative_ms(s.time));
            row.must_append(s.number);
            row.must_append(s.count);
            rows.must_append(move(row));
        }
        counter.set("samples"sv, schema_data_table({ "time"sv, "number"sv, "count"sv }, move(rows)));

        counters.must_append(move(counter));
    }
    return counters;
}

String write_gecko_profile(ProfilerSession const& session)
{
    JsonObject root;
    root.set("meta"sv, build_meta(session));
    root.set("libs"sv, JsonArray {});
    root.set("pages"sv, JsonArray {});
    root.set("sources"sv, build_source_table());
    root.set("threads"sv, build_threads(session));
    root.set("counters"sv, build_counters(session));
    root.set("processes"sv, JsonArray {});
    root.set("pausedRanges"sv, JsonArray {});
    return root.serialized();
}

}
