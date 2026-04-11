/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Profiler/NetworkMarkerStore.h>

namespace Core {

void NetworkMarkerStore::add_request_start(u64 id, String url, String method, double start_time_ms)
{
    m_markers.append({
        .id = id,
        .url = move(url),
        .method = move(method),
        .start_time_ms = start_time_ms,
        .end_time_ms = start_time_ms,
        .is_stop = false,
        .status_code = 0,
        .content_type = {},
        .body_size = 0,
        .timings = {},
    });
}

void NetworkMarkerStore::add_request_stop(u64 id, String url, String method, double start_time_ms, double end_time_ms,
    u32 status_code, String content_type, i64 body_size, NetworkTimings const& timings)
{
    m_markers.append({
        .id = id,
        .url = move(url),
        .method = move(method),
        .start_time_ms = start_time_ms,
        .end_time_ms = end_time_ms,
        .is_stop = true,
        .status_code = status_code,
        .content_type = move(content_type),
        .body_size = body_size,
        .timings = timings,
    });
}

}
