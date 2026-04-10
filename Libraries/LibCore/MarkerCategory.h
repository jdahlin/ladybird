/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/Types.h>

namespace Core {

// Strongly-typed category for both markers and labels. Adding a value here
// will break exhaustive switches in GeckoProfileWriter's build_categories()
// and category_to_underlying(), forcing the update to ripple out.
enum class MarkerCategory : u8 {
    Other = 0,
    Idle = 1,
    Layout = 2,
    JavaScript = 3,
    GC = 4,
    Network = 5,
    Graphics = 6,
    DOM = 7,
    IPC = 8,
    Media = 9,
    Timer = 10,
    Profiler = 11,
    Accessibility = 12,
    Style = 13,
    Paint = 14,
    Parser = 15,
};

}
