/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Profiler/CounterRegistry.h>
#include <LibCore/Profiler/ProfilerSession.h>

namespace Core {

// Counters are session-scoped — they only make sense while profiling is
// active. All reads and writes route through g_profiler_session.
static HashMap<String, CounterSeries> const s_empty_counters;

void profiler_add_counter_sample(StringView name, StringView category, StringView description, i64 count, i64 number)
{
    auto* session = g_profiler_session;
    if (!session)
        return;
    auto& storage = session->counter_storage();
    auto name_string = MUST(String::from_utf8(name));
    if (auto it = storage.find(name_string); it != storage.end()) {
        it->value.samples.append({ MonotonicTime::now(), count, number });
        return;
    }
    CounterSeries series;
    series.name = name_string;
    series.category = MUST(String::from_utf8(category));
    series.description = MUST(String::from_utf8(description));
    series.samples.append({ MonotonicTime::now(), count, number });
    storage.set(move(name_string), move(series));
}

HashMap<String, CounterSeries> const& profiler_counters()
{
    if (auto* session = g_profiler_session)
        return session->counter_storage();
    return s_empty_counters;
}

void profiler_reset_counter_registry()
{
    if (auto* session = g_profiler_session)
        session->counter_storage().clear();
}

}
