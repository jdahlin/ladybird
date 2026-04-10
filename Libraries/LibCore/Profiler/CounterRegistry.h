/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/HashMap.h>
#include <AK/String.h>
#include <AK/StringView.h>
#include <AK/Time.h>
#include <AK/Vector.h>
#include <LibCore/Export.h>

namespace Core {

struct CounterSample {
    MonotonicTime time;
    i64 count;
    i64 number;
};

struct CounterSeries {
    String name;
    String category;
    String description;
    Vector<CounterSample> samples;
};

CORE_API void profiler_add_counter_sample(StringView name, StringView category, StringView description, i64 count, i64 number = 0);
CORE_API HashMap<String, CounterSeries> const& profiler_counters();
CORE_API void profiler_reset_counter_registry();

}
