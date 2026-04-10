/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/JsonArray.h>
#include <AK/JsonObject.h>
#include <AK/JsonParser.h>
#include <LibDevTools/Actors/PerfActor.h>
#include <LibDevTools/Actors/TabActor.h>
#include <LibDevTools/DevToolsDelegate.h>
#include <LibDevTools/DevToolsServer.h>

namespace DevTools {

NonnullRefPtr<PerfActor> PerfActor::create(DevToolsServer& devtools, String name)
{
    return adopt_ref(*new PerfActor(devtools, move(name)));
}

PerfActor::PerfActor(DevToolsServer& devtools, String name)
    : Actor(devtools, move(name))
{
}

PerfActor::~PerfActor() = default;

void PerfActor::handle_message(Message const& message)
{
    JsonObject response;

    // Firefox spec: response: { value: RetVal("boolean") }
    // Firefox sends interval in milliseconds (e.g. 1 = 1ms), our Profiler takes microseconds.
    if (message.type == "startProfiler"sv) {
        double interval_ms = 1.0;
        if (auto interval = message.data.get_double_with_precision_loss("interval"sv); interval.has_value())
            interval_ms = *interval;
        u32 interval_us = max(static_cast<u32>(interval_ms * 1000), static_cast<u32>(100));

        auto tabs = devtools().delegate().tab_list();
        for (auto const& tab : tabs)
            devtools().delegate().start_profiling(tab, interval_us);

        m_is_active = true;
        response.set("value"sv, true);
        send_response(message, move(response));

        // Emit "profiler-started" event
        JsonObject event;
        event.set("type"sv, "profiler-started"sv);
        event.set("entries"sv, 1000000);
        event.set("interval"sv, static_cast<double>(interval_us) / 1000.0);
        event.set("features"sv, 0);
        event.set("duration"sv, 0);
        event.set("activeTabID"sv, 0);
        send_message(move(event));
        return;
    }

    // Firefox spec: response: {}
    if (message.type == "stopProfilerAndDiscardProfile"sv) {
        for (auto const& tab : devtools().delegate().tab_list()) {
            devtools().delegate().stop_profiling(tab, [](auto) { });
        }

        m_is_active = false;
        send_response(message, move(response));

        JsonObject event;
        event.set("type"sv, "profiler-stopped"sv);
        send_message(move(event));
        return;
    }

    // Firefox spec: response: { value: RetVal("number") }
    // Two-step flow: capture stores the profile, then bulk request retrieves it.
    if (message.type == "startCaptureAndStopProfiler"sv) {
        auto tabs = devtools().delegate().tab_list();
        if (tabs.is_empty()) {
            m_is_active = false;
            response.set("value"sv, 0);
            send_response(message, move(response));
            return;
        }

        m_is_active = false;
        auto first_tab = tabs[0];

        devtools().delegate().stop_profiling(first_tab,
            async_handler<PerfActor>(message, [](auto& self, auto profile_json, auto& resp) {
                self.m_captured_profile = move(profile_json);
                resp.set("value"sv, 1);
            }));

        for (size_t i = 1; i < tabs.size(); ++i)
            devtools().delegate().stop_profiling(tabs[i], [](auto) { });

        return;
    }

    // Firefox ESR <=140: getProfileAndStopProfiler returns the profile as
    // inline JSON.  The PerfFront then extracts sharedLibraries from the
    // profile object.  Spec: response: RetVal("nullable:json").
    if (message.type == "getProfileAndStopProfiler"sv) {
        auto tabs = devtools().delegate().tab_list();
        if (tabs.is_empty()) {
            m_is_active = false;
            send_response(message, move(response));
            return;
        }

        m_is_active = false;
        auto first_tab = tabs[0];

        devtools().delegate().stop_profiling(first_tab,
            async_handler<PerfActor>(message, [](auto&, auto profile_json, auto& resp) {
                // RetVal("nullable:json") means the profile IS the response,
                // not nested under a key.  We merge the parsed profile object
                // into the response so all its keys (meta, libs, threads, etc.)
                // become top-level response fields.
                auto parsed = JsonParser::parse(profile_json);
                if (!parsed.is_error() && parsed.value().is_object()) {
                    parsed.value().as_object().for_each_member([&](auto const& key, auto const& value) {
                        resp.set(key, value);
                    });
                }
            }));

        for (size_t i = 1; i < tabs.size(); ++i)
            devtools().delegate().stop_profiling(tabs[i], [](auto) { });

        return;
    }

    // Firefox spec: response: BULK_RESPONSE
    if (message.type == "getPreviouslyCapturedProfileDataBulk"sv) {
        if (m_captured_profile.is_empty()) {
            JsonObject error_response;
            error_response.set("error"sv, "noProfile"sv);
            error_response.set("message"sv, "No captured profile data available"sv);
            send_response(message, move(error_response));
            return;
        }

        send_bulk_data(message, "getPreviouslyCapturedProfileDataBulk"sv, m_captured_profile.bytes());
        m_captured_profile = {};
        return;
    }

    // Firefox spec: response: { value: RetVal("nullable:json") }
    if (message.type == "getPreviouslyRetrievedAdditionalInformation"sv) {
        JsonObject info;
        info.set("sharedLibraries"sv, JsonArray {});
        response.set("value"sv, move(info));
        send_response(message, move(response));
        return;
    }

    // Firefox spec: response: { value: RetVal("boolean") }
    if (message.type == "isActive"sv) {
        response.set("value"sv, m_is_active);
        send_response(message, move(response));
        return;
    }

    // Firefox spec: response: { value: RetVal("boolean") }
    if (message.type == "isSupportedPlatform"sv) {
        response.set("value"sv, true);
        send_response(message, move(response));
        return;
    }

    // Firefox spec: response: { value: RetVal("array:string") }
    if (message.type == "getSupportedFeatures"sv) {
        JsonArray features;
        features.must_append("js"sv);
        response.set("value"sv, move(features));
        send_response(message, move(response));
        return;
    }

    send_unrecognized_packet_type_error(message);
}

}
