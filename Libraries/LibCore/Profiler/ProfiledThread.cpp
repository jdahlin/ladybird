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

}
