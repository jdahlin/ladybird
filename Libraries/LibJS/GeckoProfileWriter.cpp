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
#include <LibCore/MarkerCollector.h>
#include <LibCore/Profiler/CounterRegistry.h>
#include <LibCore/Profiler/ThreadRegistry.h>
#include <LibCore/System.h>
#include <LibJS/GeckoProfileWriter.h>
#include <LibJS/Profiler.h>

using Core::MarkerCategory;
using Core::MarkerCollector;
using Core::MarkerPhase;
using Core::MarkerSchema;

namespace JS {

// Helper: build a { schema: { col0: 0, col1: 1, ... }, data: [...], length: N } table
// from column names and a pre-built data array. The `length` field is required for
// profiler.firefox.com's processed format conversion.
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

// Category — colors stack frames in the flame chart.
// https://github.com/firefox-devtools/profiler/blob/8114501/src/types/profile.ts#L435-L448
// Required: name, color (one of "grey","yellow","lightblue",...), subcategories (["Other"] at index 0).
// Constraint: at least one category with color:"grey" must exist.
//
// Indices used elsewhere:
//   0 = Other (grey, default)     — frameTable[category], markers[category]
//   1 = Idle (transparent)
//   2 = Layout (purple)
//   3 = JavaScript (yellow)
//   4 = GC / CC (orange)
//   5 = Network (lightblue)
//   6 = Graphics (green)
//   7 = DOM (blue)
static JsonArray build_categories()
{
    struct CategoryDef {
        StringView name;
        StringView color;
    };
    // The order MUST match the MarkerCategory enum so that
    // index lookups in the gecko profile align.
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

static constexpr auto CATEGORY_JAVASCRIPT = MarkerCategory::JavaScript;
static constexpr auto CATEGORY_NETWORK = MarkerCategory::Network;

// GeckoProfileFullMeta — top-level profile metadata.
// https://github.com/firefox-devtools/profiler/blob/8114501/src/types/gecko-profile.ts#L449-L523
// Required: version, startTime, shutdownTime, interval, stackwalk, debug,
//           gcpoison, asyncstack, processType, categories, markerSchema.
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

static JsonArray build_marker_schemas(MarkerCollector const* collector)
{
    JsonArray schemas;
    if (!collector)
        return schemas;

    for (auto const& schema : collector->schemas()) {
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

static JsonObject build_meta(Profiler const& profiler, MarkerCollector const* collector)
{
    // interval: ms between samples. Use 1ms nominal for safe-point-only profilers
    // (interval_us == 0) since the spec expects a positive number.
    double interval_ms = profiler.interval_us() > 0
        ? static_cast<double>(profiler.interval_us()) / 1000.0
        : 1.0;

    JsonObject meta;
    // Must be 34
    meta.set("version"sv, 34);
    // ms since Unix epoch; all sample/marker timestamps are relative to this
    meta.set("startTime"sv, static_cast<double>(profiler.start_time_epoch_ms()));
    auto stop_ms = profiler.stop_time_epoch_ms();
    meta.set("shutdownTime"sv, stop_ms > 0 ? JsonValue(static_cast<double>(stop_ms)) : JsonValue {});
    // Sampling interval in ms
    meta.set("interval"sv, interval_ms);
    // 0 = no native stack walking
    meta.set("stackwalk"sv, 0);
    // 0 = optimized build
    meta.set("debug"sv, 0);
    // 0 = GC poisoning disabled
    meta.set("gcpoison"sv, 0);
    // 0 = async stacks disabled
    meta.set("asyncstack"sv, 0);
    // 0 = default/parent process
    meta.set("processType"sv, 0);
    meta.set("categories"sv, build_categories());
    meta.set("markerSchema"sv, build_marker_schemas(collector));
    // profiler.firefox.com displays the title as "{product} - {oscpu}", so set
    // product to just "Ladybird" and let oscpu carry the OS name.
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
    meta.set("product"sv, "Ladybird"sv);
    meta.set("oscpu"sv, os_name);
    (void)collector;
    if (!Core::profiler_process_name().is_empty())
        meta.set("processName"sv, Core::profiler_process_name());

    // profilingStartTime/EndTime are RELATIVE to startTime (i.e. ms since profile began).
    // profiler.firefox.com uses these for the visible timeline range. Without them the
    // UI may not display the timeline correctly.
    meta.set("profilingStartTime"sv, 0.0);
    auto duration_ms = stop_ms > 0
        ? static_cast<double>(stop_ms - profiler.start_time_epoch_ms())
        : profiler.elapsed_ms_since_start();
    meta.set("profilingEndTime"sv, duration_ms);

    // Hides "Look up on Searchfox" menu entry
    meta.set("sourceCodeIsNotOnSearchfox"sv, true);
    return meta;
}

// GeckoSamples — profiling samples in schema/data encoding.
// https://github.com/firefox-devtools/profiler/blob/8114501/src/types/gecko-profile.ts#L116-L157
// Tuple: [stack, time, responsiveness]
//   stack:          index into thread's GeckoStackTable, null = idle
//   time:           ms relative to meta.startTime
//   responsiveness: event delay metric (0 if not measured)
static JsonObject build_samples(Profiler const& profiler)
{
    JsonArray data;
    for (auto const& sample : profiler.samples()) {
        JsonArray row;
        row.must_append(sample.stack_index); // stack
        row.must_append(sample.time_ms);     // time
        row.must_append(0);                  // responsiveness
        data.must_append(move(row));
    }
    return schema_data_table({ "stack"sv, "time"sv, "responsiveness"sv }, move(data));
}

// GeckoStackTable — tree of call stack nodes in schema/data encoding.
// https://github.com/firefox-devtools/profiler/blob/8114501/src/types/gecko-profile.ts#L261-L267
// Tuple: [prefix, frame]
//   prefix: index of parent stack node, null for root
//   frame:  index into thread's GeckoFrameTable
// Note: column order is [prefix, frame] (opposite of processed format).
static JsonObject build_stack_table(Profiler const& profiler)
{
    JsonArray data;
    for (auto const& stack : profiler.stack_table()) {
        JsonArray row;
        row.must_append(stack.prefix.has_value() ? JsonValue(stack.prefix.value()) : JsonValue {}); // prefix
        row.must_append(stack.frame_index);                                                         // frame
        data.must_append(move(row));
    }
    return schema_data_table({ "prefix"sv, "frame"sv }, move(data));
}

// GeckoFrameTable — frame data in schema/data encoding.
// https://github.com/firefox-devtools/profiler/blob/8114501/src/types/gecko-profile.ts#L193-L245
// Tuple: [location, relevantForJS, innerWindowID, implementation, line, column, category, subcategory]
//   location:      index into stringTable (function name or "0xHEX" address)
//   relevantForJS: true for frames shown in JS-only view
//   innerWindowID: null for non-JS frames
//   implementation: null for native frames; stringTable index for JIT tier
//   line/column:   source location (null if unknown)
//   category:      index into meta.categories (null = inherit from parent)
//   subcategory:   index into category's subcategories (0 = "Other")
static JsonObject build_frame_table(Profiler const& profiler)
{
    JsonArray data;
    for (auto const& frame : profiler.frame_table()) {
        JsonArray row;
        row.must_append(frame.string_index); // location
        row.must_append(true);               // relevantForJS
        row.must_append(JsonValue {});       // innerWindowID
        row.must_append(JsonValue {});       // implementation
        row.must_append(frame.line);         // line
        row.must_append(frame.column);       // column
        // Use the frame's stored category (JavaScript for real JS frames,
        // Style/Layout/Paint/etc for synthetic marker scope frames).
        row.must_append(static_cast<int>(frame.category)); // category
        row.must_append(0);                                // subcategory
        data.must_append(move(row));
    }
    return schema_data_table(
        { "location"sv, "relevantForJS"sv, "innerWindowID"sv, "implementation"sv,
            "line"sv, "column"sv, "category"sv, "subcategory"sv },
        move(data));
}

// All markers go to whatever real thread they were emitted from. The main thread
// gets a rich call tree because MARKER_SCOPE pushes synthetic frames onto the
// JS profiler's sampled stack — no need for fake routing.
static u64 virtual_tid_for_marker(Core::Marker const& m, u64 main_tid)
{
    if (m.tid != 0)
        return m.tid;
    return main_tid;
}

// GeckoMarkers — marker events in schema/data encoding.
// https://github.com/firefox-devtools/profiler/blob/8114501/src/types/gecko-profile.ts#L44-L65
// Tuple: [name, startTime, endTime, phase, category, data]
//   name:      index into thread's stringTable
//   startTime: ms relative to meta.startTime (non-null for phase 0,1,2)
//   endTime:   ms relative to meta.startTime (non-null for phase 1,3; null for instant)
//   phase:     0=Instant, 1=Interval, 2=IntervalStart, 3=IntervalEnd
//   category:  index into meta.categories
//   data:      payload object with "type" field matching a markerSchema name
//
// Network markers use paired IntervalStart/IntervalEnd by "data.id":
//   STATUS_START (phase 2): marks channel creation
//   STATUS_STOP  (phase 3): carries timing breakdown (DNS, TCP, TLS, etc.)
// Builds the markers table for one thread.
// thread_tid: only markers with marker.tid == thread_tid are emitted.
// include_network: if true (main thread), include the Profiler's network markers
//                  (those are not tid-tagged — they all belong to the JS main thread).
static JsonObject build_markers(Profiler const& profiler, MarkerCollector const* collector,
    u64 thread_tid, bool include_network, JsonArray& string_table)
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

    // Network markers are only emitted into the main thread (they aren't tid-tagged).
    if (include_network)
        for (auto const& nm : profiler.network_markers()) {
            auto name_index = intern(MUST(String::formatted("Load {}: {}", nm.id, nm.url)));

            // Network marker payload — type:"Network" is recognized by profiler.firefox.com
            // without needing a markerSchema entry (hardcoded in marker-data.ts).
            JsonObject payload;
            payload.set("type"sv, "Network"sv);
            // startTime/endTime: channel creation and request completion times
            payload.set("startTime"sv, nm.start_time_ms);
            payload.set("endTime"sv, nm.end_time_ms);
            // id: pairs STATUS_START and STATUS_STOP markers together
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

                // Timing breakdown (ms relative to meta.startTime)
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
            row.must_append(name_index); // name
            if (nm.is_stop) {
                row.must_append(nm.start_time_ms); // startTime
                row.must_append(nm.end_time_ms);   // endTime
                row.must_append(3);                // phase: IntervalEnd
            } else {
                row.must_append(nm.start_time_ms); // startTime
                row.must_append(JsonValue {});     // endTime: null
                row.must_append(2);                // phase: IntervalStart
            }
            row.must_append(to_underlying(CATEGORY_NETWORK)); // category
            row.must_append(move(payload));                   // data
            data.must_append(move(row));
        }

    // Generic markers from the MarkerCollector
    if (collector) {
        auto profiler_start_monotonic = MonotonicTime::now() - AK::Duration::from_milliseconds(static_cast<i64>(profiler.elapsed_ms_since_start()));

        auto to_relative_ms = [&](MonotonicTime t) -> double {
            auto elapsed = t - profiler_start_monotonic;
            return static_cast<double>(max(elapsed.to_microseconds(), static_cast<i64>(0))) / 1000.0;
        };

        // Re-route markers to virtual threads based on their category.
        // Markers stay on their real thread if they came from a worker; otherwise
        // they're routed by category (Paint/Graphics → Renderer, Style → Style).
        auto main_tid = profiler.os_tid();
        for (auto const& m : collector->markers()) {
            auto virtual_tid = virtual_tid_for_marker(m, main_tid);
            if (virtual_tid != thread_tid)
                continue;
            // Marker name is either a StringView (literal) or a String — both intern as String.
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
            // Captured JS call stack (visible in the marker tooltip in profiler.firefox.com).
            if (!m.stack.is_empty()) {
                StringBuilder sb;
                for (auto const& frame : m.stack) {
                    if (!sb.is_empty())
                        sb.append('\n');
                    sb.append(frame.location);
                }
                payload.set("stackTrace"sv, MUST(sb.to_string()));
            }

            JsonArray row;
            row.must_append(name_index);
            bool has_start = m.phase != MarkerPhase::IntervalEnd;
            bool has_end = m.phase == MarkerPhase::Interval || m.phase == MarkerPhase::IntervalEnd;
            row.must_append(has_start ? JsonValue(to_relative_ms(m.start)) : JsonValue {});
            row.must_append(has_end ? JsonValue(to_relative_ms(m.end)) : JsonValue {});
            row.must_append(static_cast<int>(m.phase));
            row.must_append(to_underlying(m.category));
            row.must_append(move(payload));
            data.must_append(move(row));
        }
    }

    return schema_data_table(
        { "name"sv, "startTime"sv, "endTime"sv, "phase"sv, "category"sv, "data"sv },
        move(data));
}

// GeckoThread — per-thread data with its own tables and string table.
// https://github.com/firefox-devtools/profiler/blob/8114501/src/types/gecko-profile.ts#L275-L308
// Required: name, processType, registerTime, unregisterTime, tid, pid,
//           samples, stackTable, frameTable, stringTable, markers.
static JsonObject build_thread(Profiler const& profiler, MarkerCollector const* collector,
    u64 tid, StringView thread_name, bool is_main_thread, bool include_network = false)
{
    // String table is per-thread; built first, then extended by markers.
    JsonArray string_table;
    if (is_main_thread) {
        for (auto const& str : profiler.string_table())
            string_table.must_append(str);
    }

    auto markers = build_markers(profiler, collector, tid, include_network, string_table);

    JsonObject thread;
    // profiler.firefox.com treats "GeckoMain" as a special JS-capable main
    // thread. We store the real thread name ("Main") internally and translate
    // on export so the gecko format sees what it expects.
    StringView exported_name = thread_name == "Main"sv ? "GeckoMain"sv : thread_name;
    thread.set("name"sv, exported_name);
    // processType: "default" (parent), "tab" (content renderer), "plugin", etc.
    auto process_type = !Core::profiler_process_type().is_empty()
        ? Core::profiler_process_type().bytes_as_string_view()
        : "default"sv;
    thread.set("processType"sv, process_type);
    // processName groups threads visually under one process row.
    if (!Core::profiler_process_name().is_empty())
        thread.set("processName"sv, Core::profiler_process_name());
    thread.set("tid"sv, tid);
    thread.set("pid"sv, Core::System::getpid());
    // registerTime: ms relative to meta.startTime when this thread was created.
    // Use 0 for the main thread; other threads can use their first marker time.
    thread.set("registerTime"sv, 0.0);
    thread.set("unregisterTime"sv, JsonValue {});
    thread.set("markers"sv, move(markers));
    if (is_main_thread) {
        thread.set("samples"sv, build_samples(profiler));
        thread.set("frameTable"sv, build_frame_table(profiler));
        thread.set("stackTable"sv, build_stack_table(profiler));
    } else {
        // Non-main threads have no JS samples — emit empty tables in the correct schema.
        thread.set("samples"sv, schema_data_table({ "stack"sv, "time"sv, "responsiveness"sv }, JsonArray {}));
        thread.set("frameTable"sv, schema_data_table({ "location"sv, "relevantForJS"sv, "innerWindowID"sv, "implementation"sv, "line"sv, "column"sv, "category"sv, "subcategory"sv }, JsonArray {}));
        thread.set("stackTable"sv, schema_data_table({ "prefix"sv, "frame"sv }, JsonArray {}));
    }
    thread.set("stringTable"sv, move(string_table));
    return thread;
}

// Builds all thread entries: the main JS thread (with samples) plus any
// additional threads that registered or recorded markers.
static JsonArray build_threads(Profiler const& profiler, MarkerCollector const* collector)
{
    JsonArray threads;

    auto main_tid = profiler.os_tid();
    auto const& registry = Core::profiler_threads();
    // Use the registered name, falling back to "Main" (gets translated to
    // "GeckoMain" inside build_thread).
    StringView main_name = "Main"sv;
    if (auto it = registry.find(main_tid); it != registry.end())
        main_name = it->value.name.bytes_as_string_view();
    // Network markers don't go on the main thread anymore — they're owned by the
    // synthetic RequestServer thread emitted below. Pass include_network=false here.
    threads.must_append(build_thread(profiler, collector, main_tid, main_name,
        /* is_main_thread = */ true, /* include_network = */ false));

    if (!collector)
        return threads;

    // Discover all distinct tids from the collector's markers + registered threads.
    HashTable<u64> seen;
    seen.set(main_tid);

    auto emit_thread_for_tid = [&](u64 tid) {
        if (seen.contains(tid))
            return;
        seen.set(tid);
        StringView name;
        String fallback_name;
        if (auto it = registry.find(tid); it != registry.end()) {
            name = it->value.name.bytes_as_string_view();
        } else {
            fallback_name = MUST(String::formatted("Thread {}", tid));
            name = fallback_name.bytes_as_string_view();
        }
        threads.must_append(build_thread(profiler, collector, tid, name, /* is_main_thread = */ false));
    };

    for (auto const& [tid, _] : registry)
        emit_thread_for_tid(tid);
    for (auto const& m : collector->markers())
        emit_thread_for_tid(m.tid);

    return threads;
}

// GeckoSourceTable — JS source file references in schema/data encoding.
// https://github.com/firefox-devtools/profiler/blob/8114501/src/types/gecko-profile.ts#L589-L606
// For profiles without JS sources, use an empty table with proper schema.
static JsonObject build_source_table()
{
    return schema_data_table(
        { "id"sv, "filename"sv, "startLine"sv, "startColumn"sv, "sourceMapURL"sv },
        JsonArray {});
}

// GeckoCounter — periodic samples of a quantity (memory, CPU, etc.)
// https://github.com/firefox-devtools/profiler/blob/8114501/src/types/gecko-profile.ts#L348-L392
static JsonArray build_counters(Profiler const& profiler, MarkerCollector const* collector)
{
    JsonArray counters;
    (void)collector;

    auto profiler_start_monotonic = MonotonicTime::now() - AK::Duration::from_milliseconds(static_cast<i64>(profiler.elapsed_ms_since_start()));
    auto to_relative_ms = [&](MonotonicTime t) -> double {
        auto elapsed = t - profiler_start_monotonic;
        return static_cast<double>(max(elapsed.to_microseconds(), static_cast<i64>(0))) / 1000.0;
    };

    for (auto const& [_, series] : Core::profiler_counters()) {
        JsonObject counter;
        counter.set("name"sv, series.name);
        counter.set("category"sv, series.category);
        counter.set("description"sv, series.description);
        counter.set("pid"sv, Core::System::getpid());
        counter.set("mainThreadIndex"sv, 0);

        // Counter samples — modern flat format (post-version-48 in profiler.firefox.com).
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

// GeckoProfile (top level) — the root JSON object.
// https://github.com/firefox-devtools/profiler/blob/8114501/src/types/gecko-profile.ts#L608-L628
// Required: meta, libs, threads, processes, pausedRanges, sources.
String write_gecko_profile(Profiler const& profiler, MarkerCollector const* collector)
{
    JsonObject root;
    root.set("meta"sv, build_meta(profiler, collector));
    // Empty: no native symbolication info
    root.set("libs"sv, JsonArray {});
    // Empty source table (proper {schema,data} format required by profiler.firefox.com)
    root.set("sources"sv, build_source_table());
    root.set("threads"sv, build_threads(profiler, collector));
    root.set("counters"sv, build_counters(profiler, collector));
    // No child processes
    root.set("processes"sv, JsonArray {});
    // No paused ranges
    root.set("pausedRanges"sv, JsonArray {});
    return root.serialized();
}

}
