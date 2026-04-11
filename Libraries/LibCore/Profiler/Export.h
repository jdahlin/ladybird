/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/String.h>
#include <LibCore/Export.h>

namespace Core {

class ProfilerSession;

// Serialize a ProfilerSession to a Gecko Profile Format v34 JSON string.
// The session is the single source of truth: process metadata, profiled
// threads (with their intern tables), markers, counters, and network
// markers are all reached through it.
CORE_API String write_gecko_profile(ProfilerSession const&);

}
