/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/HashMap.h>
#include <AK/Optional.h>
#include <AK/String.h>
#include <AK/Time.h>
#include <AK/Vector.h>
#include <LibCore/Export.h>

namespace Core {

// Processed frame entry emitted to the gecko profile. One per unique
// (function-name, source-position, category) triple seen in a sample.
struct ProfiledFrame {
    u32 string_index;
    u32 line;
    u32 column;
    u8 category;
};

// Processed stack-trace node — a linked-list-through-index chain of
// frames that match a gecko `stackTable` row.
struct ProfiledStack {
    u32 frame_index;
    Optional<u32> prefix;
};

// One sample = one (time, stack) pair. Index into ProfiledThread::samples
// corresponds to a row in the gecko `samples` table.
struct ProfiledSample {
    double time_ms;
    u32 stack_index;
};

// Per-session per-thread sampled output state. Lives inside
// ProfilerSession; consumed by the gecko exporter at session stop.
//
// In step 4 the sampler (JS::Profiler) writes here directly through the
// public tables. In step 5 the actual interning logic will move from the
// sampler into ProfiledThread once there are multiple sampler impls.
class CORE_API ProfiledThread {
public:
    explicit ProfiledThread(String name);

    String const& name() const { return m_name; }
    MonotonicTime register_time() const { return m_register_time; }

    // Raw tables. Public on purpose — the sampler populates them
    // in-place while the intern_* helpers stay next to the walking code.
    Vector<String> string_table;
    HashMap<String, u32> string_map;
    Vector<ProfiledFrame> frame_table;
    HashMap<u32, u32> frame_map;
    Vector<ProfiledStack> stack_table;
    HashMap<u64, u32> stack_map;
    Vector<ProfiledSample> samples;

    void reserve_capacity();
    void clear();
    u32 intern_string(String const&);
    u32 intern_frame(String const& location, u32 line, u32 column, u8 category);

private:
    String m_name;
    MonotonicTime m_register_time { MonotonicTime::now() };
};

}
