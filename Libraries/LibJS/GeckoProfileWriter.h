/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/String.h>
#include <LibJS/Export.h>
#include <LibJS/Forward.h>

namespace Core {

class MarkerCollector;

}

namespace JS {

class Profiler;

JS_API String write_gecko_profile(Profiler const&, Core::MarkerCollector const* = nullptr);

}
