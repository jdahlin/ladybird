/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/String.h>
#include <AK/Vector.h>
#include <LibCore/Export.h>

namespace Core {

struct NetworkTimings {
    double domain_lookup_start_ms { 0 };
    double domain_lookup_end_ms { 0 };
    double connect_start_ms { 0 };
    double tcp_connect_end_ms { 0 };
    double secure_connection_start_ms { 0 };
    double connect_end_ms { 0 };
    double request_start_ms { 0 };
    double response_start_ms { 0 };
    double response_end_ms { 0 };
};

// Two entries per request — STATUS_START and STATUS_STOP linked by id.
// Serialized as paired IntervalStart/IntervalEnd markers in the gecko
// profile. The gecko exporter is the only reader.
struct NetworkMarker {
    u64 id;
    String url;
    String method;
    double start_time_ms;
    double end_time_ms;
    bool is_stop { false };
    u32 status_code { 0 };
    String content_type;
    i64 body_size { 0 };
    NetworkTimings timings;
};

class CORE_API NetworkMarkerStore {
public:
    void add_request_start(u64 id, String url, String method, double start_time_ms);
    void add_request_stop(u64 id, String url, String method, double start_time_ms, double end_time_ms,
        u32 status_code, String content_type, i64 body_size, NetworkTimings const&);

    Vector<NetworkMarker> const& markers() const { return m_markers; }

private:
    Vector<NetworkMarker> m_markers;
};

}
