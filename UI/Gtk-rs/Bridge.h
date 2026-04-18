/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct LadybirdView LadybirdView;

/* A single item in a context menu.  id == -1 for structural items. */
typedef struct {
    int id;
    bool is_separator;
    bool is_section_start;
    bool is_section_end;
    char const* label;
    size_t label_len;
    bool enabled;
    bool checkable;
    bool checked;
} LadybirdContextMenuItem;

/* A single item in a <select> dropdown. */
typedef struct {
    bool is_separator;
    bool is_group_start;
    bool is_group_end;
    uint32_t id;
    bool selected;
    bool disabled;
    char const* label;
    size_t label_len;
} LadybirdSelectItem;

#define LADYBIRD_FILE_FILTER_EXTENSION 0
#define LADYBIRD_FILE_FILTER_MIME_TYPE 1
#define LADYBIRD_FILE_FILTER_AUDIO 2
#define LADYBIRD_FILE_FILTER_IMAGE 3
#define LADYBIRD_FILE_FILTER_VIDEO 4

typedef struct {
    int type;          /* LADYBIRD_FILE_FILTER_* */
    char const* value; /* null for type-based filters */
    size_t value_len;
} LadybirdFileFilter;

typedef struct {
    void (*on_ready_to_paint)(uintptr_t ctx);
    void (*on_title_changed)(uintptr_t ctx, char const* title, size_t len);
    void (*on_url_changed)(uintptr_t ctx, char const* url, size_t len);
    void (*on_load_start)(uintptr_t ctx);
    void (*on_load_finish)(uintptr_t ctx);
    void (*on_favicon_change)(uintptr_t ctx, uint8_t const* data, int width, int height, int stride);
    void (*on_cursor_change)(uintptr_t ctx, char const* css_name, size_t len);
    void (*on_link_hover)(uintptr_t ctx, char const* url, size_t len);
    void (*on_link_unhover)(uintptr_t ctx);
    void (*on_enter_tooltip_area)(uintptr_t ctx, char const* text, size_t len);
    void (*on_leave_tooltip_area)(uintptr_t ctx);
    void (*on_request_alert)(uintptr_t ctx, char const* message, size_t len);
    void (*on_request_confirm)(uintptr_t ctx, char const* message, size_t len);
    void (*on_request_prompt)(uintptr_t ctx, char const* message, size_t msg_len, char const* default_value, size_t def_len);
    void (*on_request_color_picker)(uintptr_t ctx, uint32_t color);
    /* on_new_web_view: write the new view's handle UUID into handle_out.
       page_index < 0 means "create independent view", >= 0 means "child page". */
    void (*on_new_web_view)(uintptr_t ctx, bool activate, int64_t page_index, char* handle_out, size_t handle_capacity);
    void (*on_activate_tab)(uintptr_t ctx);
    void (*on_close)(uintptr_t ctx);
    void (*on_zoom_level_changed)(uintptr_t ctx);
    void (*on_find_in_page)(uintptr_t ctx, uint32_t current, bool has_total, uint32_t total);
    void (*on_audio_play_state_changed)(uintptr_t ctx, bool playing);
    void (*on_fullscreen_window)(uintptr_t ctx);
    void (*on_exit_fullscreen_window)(uintptr_t ctx);
    void (*on_maximize_window)(uintptr_t ctx);
    void (*on_minimize_window)(uintptr_t ctx);
    void (*on_restore_window)(uintptr_t ctx);
    void (*on_resize_window)(uintptr_t ctx, int width, int height);
    void (*on_context_menu)(uintptr_t ctx, double x, double y,
        LadybirdContextMenuItem const* items, size_t count);
    void (*on_show_select_dropdown)(uintptr_t ctx, double x, double y, int min_width,
        LadybirdSelectItem const* items, size_t count);
    void (*on_request_file_picker)(uintptr_t ctx, bool allow_multiple,
        LadybirdFileFilter const* filters, size_t filter_count);
} LadybirdViewCallbacks;

enum LadybirdMouseType {
    LADYBIRD_MOUSE_DOWN = 0,
    LADYBIRD_MOUSE_UP,
    LADYBIRD_MOUSE_MOVE,
    LADYBIRD_MOUSE_LEAVE,
};

enum LadybirdKeyType {
    LADYBIRD_KEY_DOWN = 0,
    LADYBIRD_KEY_UP,
};

int ladybird_app_init(int argc, char** argv);
void ladybird_app_shutdown(void);
void ladybird_app_quit(int exit_code);

size_t ladybird_app_initial_url_count(void);
void ladybird_app_initial_url_at(size_t index, char const** out_ptr, size_t* out_len);
bool ladybird_app_is_headless(void);

void ladybird_app_set_toast_overlay(void* overlay_ptr);
bool ladybird_app_devtools_enabled(void);
uint16_t ladybird_app_devtools_port(void);
void ladybird_app_toggle_devtools(void);
void ladybird_app_activate_about(void);
void ladybird_app_activate_settings(void);

LadybirdView* ladybird_view_create(uintptr_t rust_ctx, LadybirdViewCallbacks const* callbacks);
void ladybird_view_destroy(LadybirdView* view);

void ladybird_view_load(LadybirdView* view, char const* url, size_t len);
void ladybird_view_set_viewport_size(LadybirdView* view, int width, int height);
void ladybird_view_set_device_pixel_ratio(LadybirdView* view, double device_pixel_ratio);
void ladybird_view_set_system_visibility(LadybirdView* view, bool visible);
void ladybird_view_set_has_focus(LadybirdView* view, bool has_focus);
void ladybird_view_mouse_event(LadybirdView* view, int type, double x, double y, unsigned button, unsigned state, int click_count);
void ladybird_view_scroll_event(LadybirdView* view, double dx, double dy, unsigned state, int unit);
void ladybird_view_key_event(LadybirdView* view, int type, unsigned keyval, unsigned state);
void ladybird_view_go_back(LadybirdView* view);
void ladybird_view_go_forward(LadybirdView* view);
void ladybird_view_reload(LadybirdView* view);
void ladybird_view_zoom_in(LadybirdView* view);
void ladybird_view_zoom_out(LadybirdView* view);
void ladybird_view_reset_zoom(LadybirdView* view);
double ladybird_view_zoom_level(LadybirdView const* view);
void ladybird_view_paint(LadybirdView* view, void* snapshot, int width, int height);
void ladybird_view_handle(LadybirdView const* view, char* buf, size_t buf_len);

void ladybird_view_alert_closed(LadybirdView* view);
void ladybird_view_confirm_closed(LadybirdView* view, bool accepted);
void ladybird_view_prompt_closed(LadybirdView* view, char const* response, size_t len, bool cancelled);
void ladybird_view_color_picker_update(LadybirdView* view, uint32_t rgba, bool released);

void ladybird_view_find_in_page(LadybirdView* view, char const* query, size_t len, bool case_sensitive);
void ladybird_view_find_in_page_next_match(LadybirdView* view);
void ladybird_view_find_in_page_previous_match(LadybirdView* view);

void ladybird_view_context_menu_action(LadybirdView* view, int id);
void ladybird_view_select_dropdown_closed(LadybirdView* view, bool has_id, uint32_t id);
void ladybird_view_file_picker_closed(LadybirdView* view, char const* const* paths, size_t count);

#ifdef __cplusplus
}
#endif
