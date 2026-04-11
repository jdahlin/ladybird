/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Profiler/ProfiledThread.h>

namespace Core {

ProfiledThread::ProfiledThread(String name)
    : m_name(move(name))
{
    reserve_capacity();
}

void ProfiledThread::reserve_capacity()
{
    string_table.ensure_capacity(256);
    frame_table.ensure_capacity(256);
    stack_table.ensure_capacity(1024);
}

void ProfiledThread::clear()
{
    string_table.clear();
    string_map.clear();
    frame_table.clear();
    frame_map.clear();
    stack_table.clear();
    stack_map.clear();
    samples.clear();
}

u32 ProfiledThread::intern_string(String const& str)
{
    if (auto it = string_map.find(str); it != string_map.end())
        return it->value;
    u32 index = string_table.size();
    string_table.append(str);
    string_map.set(str, index);
    return index;
}

u32 ProfiledThread::intern_frame(String const& location, u32 line, u32 column, u8 category)
{
    // location uniquely encodes (function, file, line, col), so string index
    // is a safe key for frame deduplication.
    auto string_index = intern_string(location);
    if (auto it = frame_map.find(string_index); it != frame_map.end())
        return it->value;
    u32 index = frame_table.size();
    frame_table.append({ string_index, line, column, category });
    frame_map.set(string_index, index);
    return index;
}

Optional<u32> ProfiledThread::intern_marker_stack(Vector<MarkerStackFrameInput> const& frames_innermost_first)
{
    if (frames_innermost_first.is_empty())
        return {};

    // The gecko stack table is a linked list from leaf to root via prefix
    // indices. Walk frames root-first (so the prefix of each link is the
    // already-interned ancestor), matching JSStackSampler::intern_stack_trace.
    Optional<u32> prefix;
    auto category = static_cast<u8>(to_underlying(MarkerCategory::JavaScript));
    for (ssize_t i = static_cast<ssize_t>(frames_innermost_first.size()) - 1; i >= 0; --i) {
        auto const& f = frames_innermost_first[i];
        auto frame_index = intern_frame(f.location, f.line, f.column, category);
        u64 key = (static_cast<u64>(frame_index) << 32) | prefix.value_or(UINT32_MAX);
        if (auto it = stack_map.find(key); it != stack_map.end()) {
            prefix = it->value;
            continue;
        }
        u32 stack_index = stack_table.size();
        stack_table.append({ frame_index, prefix });
        stack_map.set(key, stack_index);
        prefix = stack_index;
    }
    return prefix;
}

}
