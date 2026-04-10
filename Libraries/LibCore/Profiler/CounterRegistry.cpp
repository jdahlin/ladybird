/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Profiler/CounterRegistry.h>

namespace Core {

static HashMap<String, CounterSeries> s_counters;

void profiler_add_counter_sample(StringView name, StringView category, StringView description, i64 count, i64 number)
{
    auto name_string = MUST(String::from_utf8(name));
    if (auto it = s_counters.find(name_string); it != s_counters.end()) {
        it->value.samples.append({ MonotonicTime::now(), count, number });
        return;
    }
    CounterSeries series;
    series.name = name_string;
    series.category = MUST(String::from_utf8(category));
    series.description = MUST(String::from_utf8(description));
    series.samples.append({ MonotonicTime::now(), count, number });
    s_counters.set(move(name_string), move(series));
}

HashMap<String, CounterSeries> const& profiler_counters()
{
    return s_counters;
}

void profiler_reset_counter_registry()
{
    s_counters.clear();
}

}
