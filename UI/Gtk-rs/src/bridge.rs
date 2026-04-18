/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

use adw::prelude::*;
use std::ffi::{c_char, c_void};
use std::ptr::NonNull;

#[repr(C)]
pub struct LadybirdView {
    _private: [u8; 0],
}

#[repr(C)]
pub struct LadybirdContextMenuItem {
    pub id: i32,
    pub is_separator: bool,
    pub is_section_start: bool,
    pub is_section_end: bool,
    pub label: *const c_char,
    pub label_len: usize,
    pub enabled: bool,
    pub checkable: bool,
    pub checked: bool,
}

#[repr(C)]
pub struct LadybirdSelectItem {
    pub is_separator: bool,
    pub is_group_start: bool,
    pub is_group_end: bool,
    pub id: u32,
    pub selected: bool,
    pub disabled: bool,
    pub label: *const c_char,
    pub label_len: usize,
}

const LADYBIRD_FILE_FILTER_EXTENSION: i32 = 0;
const LADYBIRD_FILE_FILTER_MIME_TYPE: i32 = 1;
const LADYBIRD_FILE_FILTER_AUDIO: i32 = 2;
const LADYBIRD_FILE_FILTER_IMAGE: i32 = 3;
const LADYBIRD_FILE_FILTER_VIDEO: i32 = 4;

#[repr(C)]
pub struct LadybirdFileFilter {
    pub type_: i32,
    pub value: *const c_char,
    pub value_len: usize,
}

#[repr(C)]
#[derive(Default)]
pub struct LadybirdViewCallbacks {
    pub on_ready_to_paint: Option<unsafe extern "C" fn(usize)>,
    pub on_title_changed: Option<unsafe extern "C" fn(usize, *const c_char, usize)>,
    pub on_url_changed: Option<unsafe extern "C" fn(usize, *const c_char, usize)>,
    pub on_load_start: Option<unsafe extern "C" fn(usize)>,
    pub on_load_finish: Option<unsafe extern "C" fn(usize)>,
    pub on_favicon_change: Option<unsafe extern "C" fn(usize, *const u8, i32, i32, i32)>,
    pub on_cursor_change: Option<unsafe extern "C" fn(usize, *const c_char, usize)>,
    pub on_link_hover: Option<unsafe extern "C" fn(usize, *const c_char, usize)>,
    pub on_link_unhover: Option<unsafe extern "C" fn(usize)>,
    pub on_enter_tooltip_area: Option<unsafe extern "C" fn(usize, *const c_char, usize)>,
    pub on_leave_tooltip_area: Option<unsafe extern "C" fn(usize)>,
    pub on_request_alert: Option<unsafe extern "C" fn(usize, *const c_char, usize)>,
    pub on_request_confirm: Option<unsafe extern "C" fn(usize, *const c_char, usize)>,
    pub on_request_prompt:
        Option<unsafe extern "C" fn(usize, *const c_char, usize, *const c_char, usize)>,
    pub on_request_color_picker: Option<unsafe extern "C" fn(usize, u32)>,
    pub on_new_web_view:
        Option<unsafe extern "C" fn(usize, bool, i64, *mut c_char, usize)>,
    pub on_activate_tab: Option<unsafe extern "C" fn(usize)>,
    pub on_close: Option<unsafe extern "C" fn(usize)>,
    pub on_zoom_level_changed: Option<unsafe extern "C" fn(usize)>,
    pub on_find_in_page: Option<unsafe extern "C" fn(usize, u32, bool, u32)>,
    pub on_audio_play_state_changed: Option<unsafe extern "C" fn(usize, bool)>,
    pub on_fullscreen_window: Option<unsafe extern "C" fn(usize)>,
    pub on_exit_fullscreen_window: Option<unsafe extern "C" fn(usize)>,
    pub on_maximize_window: Option<unsafe extern "C" fn(usize)>,
    pub on_minimize_window: Option<unsafe extern "C" fn(usize)>,
    pub on_restore_window: Option<unsafe extern "C" fn(usize)>,
    pub on_resize_window: Option<unsafe extern "C" fn(usize, i32, i32)>,
    pub on_context_menu: Option<
        unsafe extern "C" fn(usize, f64, f64, *const LadybirdContextMenuItem, usize),
    >,
    pub on_show_select_dropdown: Option<
        unsafe extern "C" fn(usize, f64, f64, i32, *const LadybirdSelectItem, usize),
    >,
    pub on_request_file_picker:
        Option<unsafe extern "C" fn(usize, bool, *const LadybirdFileFilter, usize)>,
}

#[repr(i32)]
#[derive(Clone, Copy)]
pub enum MouseEventType { Down = 0, Up = 1, Move = 2, Leave = 3 }

#[repr(i32)]
#[derive(Clone, Copy)]
pub enum KeyEventType { Down = 0, Up = 1 }

unsafe extern "C" {
    fn ladybird_app_init(argc: i32, argv: *mut *mut c_char) -> i32;
    fn ladybird_app_shutdown();
    fn ladybird_app_initial_url_count() -> usize;
    fn ladybird_app_initial_url_at(index: usize, out_ptr: *mut *const c_char, out_len: *mut usize);
    fn ladybird_app_is_headless() -> bool;
    fn ladybird_app_quit(exit_code: i32);
    fn ladybird_app_set_toast_overlay(overlay: *mut c_void);
    fn ladybird_app_devtools_enabled() -> bool;
    fn ladybird_app_devtools_port() -> u16;
    fn ladybird_app_toggle_devtools();
    fn ladybird_app_activate_about();
    fn ladybird_app_activate_settings();

    fn ladybird_view_create(rust_ctx: usize, callbacks: *const LadybirdViewCallbacks) -> *mut LadybirdView;
    fn ladybird_view_destroy(view: *mut LadybirdView);
    fn ladybird_view_load(view: *mut LadybirdView, url: *const c_char, len: usize);
    fn ladybird_view_set_viewport_size(view: *mut LadybirdView, width: i32, height: i32);
    fn ladybird_view_set_device_pixel_ratio(view: *mut LadybirdView, device_pixel_ratio: f64);
    fn ladybird_view_set_system_visibility(view: *mut LadybirdView, visible: bool);
    fn ladybird_view_set_has_focus(view: *mut LadybirdView, has_focus: bool);
    fn ladybird_view_mouse_event(
        view: *mut LadybirdView,
        ty: i32,
        x: f64,
        y: f64,
        button: u32,
        state: u32,
        click_count: i32,
    );
    fn ladybird_view_scroll_event(view: *mut LadybirdView, dx: f64, dy: f64, state: u32, unit: i32);
    fn ladybird_view_key_event(view: *mut LadybirdView, ty: i32, keyval: u32, state: u32);
    fn ladybird_view_go_back(view: *mut LadybirdView);
    fn ladybird_view_go_forward(view: *mut LadybirdView);
    fn ladybird_view_reload(view: *mut LadybirdView);
    fn ladybird_view_zoom_in(view: *mut LadybirdView);
    fn ladybird_view_zoom_out(view: *mut LadybirdView);
    fn ladybird_view_reset_zoom(view: *mut LadybirdView);
    fn ladybird_view_zoom_level(view: *const LadybirdView) -> f64;
    fn ladybird_view_paint(view: *mut LadybirdView, snapshot: *mut c_void, width: i32, height: i32);
    fn ladybird_view_handle(view: *const LadybirdView, buf: *mut c_char, buf_len: usize);
    fn ladybird_view_alert_closed(view: *mut LadybirdView);
    fn ladybird_view_confirm_closed(view: *mut LadybirdView, accepted: bool);
    fn ladybird_view_prompt_closed(
        view: *mut LadybirdView,
        response: *const c_char,
        len: usize,
        cancelled: bool,
    );
    fn ladybird_view_color_picker_update(view: *mut LadybirdView, rgba: u32, released: bool);
    fn ladybird_view_find_in_page(
        view: *mut LadybirdView,
        query: *const c_char,
        len: usize,
        case_sensitive: bool,
    );
    fn ladybird_view_find_in_page_next_match(view: *mut LadybirdView);
    fn ladybird_view_find_in_page_previous_match(view: *mut LadybirdView);
    fn ladybird_view_context_menu_action(view: *mut LadybirdView, id: i32);
    fn ladybird_view_select_dropdown_closed(view: *mut LadybirdView, has_id: bool, id: u32);
    fn ladybird_view_file_picker_closed(
        view: *mut LadybirdView,
        paths: *const *const c_char,
        count: usize,
    );
}

pub struct View(NonNull<LadybirdView>);

impl View {
    pub fn create(id: usize, callbacks: &LadybirdViewCallbacks) -> Option<Self> {
        let raw = unsafe { ladybird_view_create(id, callbacks) };
        NonNull::new(raw).map(Self)
    }

    pub fn load(&self, url: &str) {
        unsafe { ladybird_view_load(self.0.as_ptr(), url.as_ptr().cast(), url.len()) }
    }

    pub fn set_viewport_size(&self, width: i32, height: i32) {
        unsafe { ladybird_view_set_viewport_size(self.0.as_ptr(), width, height) }
    }

    pub fn set_device_pixel_ratio(&self, device_pixel_ratio: f64) {
        unsafe { ladybird_view_set_device_pixel_ratio(self.0.as_ptr(), device_pixel_ratio) }
    }

    pub fn set_system_visibility(&self, visible: bool) {
        unsafe { ladybird_view_set_system_visibility(self.0.as_ptr(), visible) }
    }

    pub fn set_has_focus(&self, has_focus: bool) {
        unsafe { ladybird_view_set_has_focus(self.0.as_ptr(), has_focus) }
    }

    pub fn mouse_event(&self, ty: MouseEventType, x: f64, y: f64, button: u32, state: u32, click_count: i32) {
        unsafe { ladybird_view_mouse_event(self.0.as_ptr(), ty as i32, x, y, button, state, click_count) }
    }

    pub fn scroll_event(&self, dx: f64, dy: f64, state: u32, unit: i32) {
        unsafe { ladybird_view_scroll_event(self.0.as_ptr(), dx, dy, state, unit) }
    }

    pub fn key_event(&self, ty: KeyEventType, keyval: u32, state: u32) {
        unsafe { ladybird_view_key_event(self.0.as_ptr(), ty as i32, keyval, state) }
    }

    pub fn go_back(&self) {
        unsafe { ladybird_view_go_back(self.0.as_ptr()) }
    }

    pub fn go_forward(&self) {
        unsafe { ladybird_view_go_forward(self.0.as_ptr()) }
    }

    pub fn reload(&self) {
        unsafe { ladybird_view_reload(self.0.as_ptr()) }
    }

    pub fn zoom_in(&self) {
        unsafe { ladybird_view_zoom_in(self.0.as_ptr()) }
    }

    pub fn zoom_out(&self) {
        unsafe { ladybird_view_zoom_out(self.0.as_ptr()) }
    }

    pub fn reset_zoom(&self) {
        unsafe { ladybird_view_reset_zoom(self.0.as_ptr()) }
    }

    pub fn zoom_level(&self) -> f64 {
        unsafe { ladybird_view_zoom_level(self.0.as_ptr()) }
    }

    pub fn paint(&self, snapshot: &gtk::Snapshot, width: i32, height: i32) {
        use gtk::glib::translate::ToGlibPtr;
        let raw: *mut gtk::ffi::GtkSnapshot = snapshot.to_glib_none().0;
        unsafe { ladybird_view_paint(self.0.as_ptr(), raw as *mut c_void, width, height) }
    }

    pub fn handle(&self) -> String {
        let mut buf = [0u8; 256];
        unsafe { ladybird_view_handle(self.0.as_ptr(), buf.as_mut_ptr().cast(), buf.len()) };
        let end = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
        String::from_utf8_lossy(&buf[..end]).into_owned()
    }

    pub fn alert_closed(&self) {
        unsafe { ladybird_view_alert_closed(self.0.as_ptr()) }
    }

    pub fn confirm_closed(&self, accepted: bool) {
        unsafe { ladybird_view_confirm_closed(self.0.as_ptr(), accepted) }
    }

    pub fn prompt_closed(&self, response: Option<&str>) {
        match response {
            Some(s) => unsafe {
                ladybird_view_prompt_closed(self.0.as_ptr(), s.as_ptr().cast(), s.len(), false)
            },
            None => unsafe { ladybird_view_prompt_closed(self.0.as_ptr(), std::ptr::null(), 0, true) },
        }
    }

    pub fn color_picker_update(&self, rgba: u32, released: bool) {
        unsafe { ladybird_view_color_picker_update(self.0.as_ptr(), rgba, released) }
    }

    pub fn find_in_page(&self, query: &str, case_sensitive: bool) {
        unsafe {
            ladybird_view_find_in_page(self.0.as_ptr(), query.as_ptr().cast(), query.len(), case_sensitive)
        }
    }

    pub fn find_in_page_next_match(&self) {
        unsafe { ladybird_view_find_in_page_next_match(self.0.as_ptr()) }
    }

    pub fn find_in_page_previous_match(&self) {
        unsafe { ladybird_view_find_in_page_previous_match(self.0.as_ptr()) }
    }

    pub fn context_menu_action(&self, id: i32) {
        unsafe { ladybird_view_context_menu_action(self.0.as_ptr(), id) }
    }

    pub fn select_dropdown_closed(&self, id: Option<u32>) {
        match id {
            Some(i) => unsafe { ladybird_view_select_dropdown_closed(self.0.as_ptr(), true, i) },
            None => unsafe { ladybird_view_select_dropdown_closed(self.0.as_ptr(), false, 0) },
        }
    }

    pub fn file_picker_closed(&self, paths: &[std::path::PathBuf]) {
        let c_strings: Vec<std::ffi::CString> = paths
            .iter()
            .filter_map(|p| p.to_str().and_then(|s| std::ffi::CString::new(s).ok()))
            .collect();
        let c_ptrs: Vec<*const c_char> = c_strings.iter().map(|s| s.as_ptr()).collect();
        unsafe {
            ladybird_view_file_picker_closed(self.0.as_ptr(), c_ptrs.as_ptr(), c_ptrs.len())
        }
    }
}

impl Drop for View {
    fn drop(&mut self) {
        unsafe { ladybird_view_destroy(self.0.as_ptr()) }
    }
}

/// Decode a C string (ptr + len) from the C++ side into an owned String.
/// # Safety
/// `ptr` must be valid for `len` bytes for the duration of this call.
pub unsafe fn cstr_to_string(ptr: *const c_char, len: usize) -> String {
    if ptr.is_null() || len == 0 {
        return String::new();
    }
    let bytes = unsafe { std::slice::from_raw_parts(ptr.cast::<u8>(), len) };
    std::str::from_utf8(bytes).unwrap_or("").to_owned()
}

/// Borrow a C array as a Rust slice. Private — only used inside bridge.rs.
/// # Safety
/// `ptr` must be valid for `count` elements for the duration of the returned slice.
unsafe fn ffi_slice<'a, T>(ptr: *const T, count: usize) -> &'a [T] {
    if ptr.is_null() || count == 0{
        return &[];
    }
    unsafe { std::slice::from_raw_parts(ptr, count) }
}

/// Write `value` into a C-provided output buffer, always null-terminating.
/// # Safety
/// `out` must be a valid writable buffer of at least `capacity` bytes.
pub unsafe fn write_c_buffer(out: *mut c_char, capacity: usize, value: &str) {
    if capacity == 0 {
        return;
    }
    let bytes = value.as_bytes();
    let n = bytes.len().min(capacity - 1);
    unsafe {
        std::ptr::copy_nonoverlapping(bytes.as_ptr(), out.cast::<u8>(), n);
        *out.add(n) = 0;
    }
}

// ---- Owned domain types decoded from C callback data -------------------------

pub enum ContextMenuEntry {
    Separator,
    SectionStart { label: String },
    SectionEnd,
    Item { id: i32, label: String, enabled: bool, checkable: bool, checked: bool },
}

impl ContextMenuEntry {
    unsafe fn from_ffi(raw: &LadybirdContextMenuItem) -> Self {
        if raw.is_separator { return Self::Separator; }
        if raw.is_section_end { return Self::SectionEnd; }
        let label = unsafe { cstr_to_string(raw.label, raw.label_len) };
        if raw.is_section_start { return Self::SectionStart { label }; }
        Self::Item { id: raw.id, label, enabled: raw.enabled, checkable: raw.checkable, checked: raw.checked }
    }
}

pub enum SelectEntry {
    Separator,
    GroupStart { label: String },
    GroupEnd,
    Item { id: u32, label: String, selected: bool, disabled: bool },
}

impl SelectEntry {
    unsafe fn from_ffi(raw: &LadybirdSelectItem) -> Self {
        if raw.is_separator { return Self::Separator; }
        if raw.is_group_end { return Self::GroupEnd; }
        let label = unsafe { cstr_to_string(raw.label, raw.label_len) };
        if raw.is_group_start { return Self::GroupStart { label }; }
        Self::Item { id: raw.id, label, selected: raw.selected, disabled: raw.disabled }
    }
}

pub enum FileFilter {
    Extension(String),
    MimeType(String),
    Audio,
    Image,
    Video,
}

impl FileFilter {
    unsafe fn from_ffi(raw: &LadybirdFileFilter) -> Option<Self> {
        Some(match raw.type_ {
            LADYBIRD_FILE_FILTER_EXTENSION => Self::Extension(unsafe { cstr_to_string(raw.value, raw.value_len) }),
            LADYBIRD_FILE_FILTER_MIME_TYPE => Self::MimeType(unsafe { cstr_to_string(raw.value, raw.value_len) }),
            LADYBIRD_FILE_FILTER_AUDIO => Self::Audio,
            LADYBIRD_FILE_FILTER_IMAGE => Self::Image,
            LADYBIRD_FILE_FILTER_VIDEO => Self::Video,
            _ => return None,
        })
    }
}

/// Validate and copy a raw favicon pixel buffer from a C callback.
/// Returns `(bytes, stride_usize)` or `None` if the pointer is null or dimensions overflow.
/// # Safety
/// `data` must be valid for `stride * height` bytes if non-null.
pub unsafe fn favicon_bytes_from_raw(
    data: *const u8,
    height: i32,
    stride: i32,
) -> Option<(Vec<u8>, usize)> {
    if data.is_null() { return None; }
    let h = usize::try_from(height).ok()?;
    let s = usize::try_from(stride).ok()?;
    let byte_len = s.checked_mul(h)?;
    Some((unsafe { std::slice::from_raw_parts(data, byte_len) }.to_vec(), s))
}

/// Decode context menu items from a C callback. All pointer validity is the caller's responsibility.
/// # Safety
/// `ptr` must point to `count` valid `LadybirdContextMenuItem` elements, and all embedded
/// string pointers within those elements must be valid for the duration of this call.
pub unsafe fn context_menu_entries_from_raw(ptr: *const LadybirdContextMenuItem, count: usize) -> Vec<ContextMenuEntry> {
    unsafe { ffi_slice(ptr, count) }.iter().map(|raw| unsafe { ContextMenuEntry::from_ffi(raw) }).collect()
}

/// Decode select dropdown items from a C callback.
/// # Safety
/// Same contract as `context_menu_entries_from_raw`.
pub unsafe fn select_entries_from_raw(ptr: *const LadybirdSelectItem, count: usize) -> Vec<SelectEntry> {
    unsafe { ffi_slice(ptr, count) }.iter().map(|raw| unsafe { SelectEntry::from_ffi(raw) }).collect()
}

/// Decode file filter items from a C callback.
/// # Safety
/// Same contract as `context_menu_entries_from_raw`.
pub unsafe fn file_filters_from_raw(ptr: *const LadybirdFileFilter, count: usize) -> Vec<FileFilter> {
    unsafe { ffi_slice(ptr, count) }.iter().filter_map(|raw| unsafe { FileFilter::from_ffi(raw) }).collect()
}

// ---- App-level safe wrappers -------------------------------------------------

pub fn app_init(args: &[*mut c_char]) -> i32 {
    unsafe { ladybird_app_init(args.len() as i32, args.as_ptr() as *mut _) }
}

pub fn app_shutdown() { unsafe { ladybird_app_shutdown() } }
pub fn app_is_headless() -> bool { unsafe { ladybird_app_is_headless() } }
pub fn app_quit(exit_code: i32) { unsafe { ladybird_app_quit(exit_code) } }
pub fn app_devtools_enabled() -> bool { unsafe { ladybird_app_devtools_enabled() } }
pub fn app_devtools_port() -> u16 { unsafe { ladybird_app_devtools_port() } }
pub fn app_toggle_devtools() { unsafe { ladybird_app_toggle_devtools() } }
pub fn app_activate_about() { unsafe { ladybird_app_activate_about() } }
pub fn app_activate_settings() { unsafe { ladybird_app_activate_settings() } }

pub fn app_set_toast_overlay(overlay: &adw::ToastOverlay) {
    use gtk::glib::translate::ToGlibPtr;
    let ptr: *mut gtk::ffi::GtkWidget = overlay.upcast_ref::<gtk::Widget>().to_glib_none().0;
    unsafe { ladybird_app_set_toast_overlay(ptr as *mut c_void) }
}

pub fn initial_urls() -> Vec<String> {
    let count = unsafe { ladybird_app_initial_url_count() };
    let mut urls = Vec::with_capacity(count);
    for index in 0..count {
        let mut ptr = std::ptr::null();
        let mut len = 0;
        unsafe { ladybird_app_initial_url_at(index, &mut ptr, &mut len) };
        urls.push(unsafe { cstr_to_string(ptr, len) });
    }
    urls
}

// ---- Panic containment for C→Rust callbacks ----------------------------------

pub fn ffi_callback_ret<T, F: FnOnce() -> T>(f: F) -> T {
    match std::panic::catch_unwind(std::panic::AssertUnwindSafe(f)) {
        Ok(v) => v,
        Err(_) => std::process::abort(),
    }
}

pub fn ffi_callback<F: FnOnce()>(f: F) {
    ffi_callback_ret(f);
}

// ---- Tab callback trampolines: decode raw ABI, enter panic guard, dispatch ---

pub mod tab_callbacks {
    use std::ffi::c_char;
    use super::{
        LadybirdContextMenuItem, LadybirdFileFilter, LadybirdSelectItem,
        context_menu_entries_from_raw, cstr_to_string, favicon_bytes_from_raw,
        ffi_callback, ffi_callback_ret, file_filters_from_raw, select_entries_from_raw,
        write_c_buffer,
    };

    pub unsafe extern "C" fn on_ready_to_paint(id: usize) {
        ffi_callback(|| crate::tab::handle_ready_to_paint(id));
    }

    pub unsafe extern "C" fn on_title_changed(id: usize, ptr: *const c_char, len: usize) {
        let title = unsafe { cstr_to_string(ptr, len) };
        ffi_callback(|| crate::tab::handle_title_changed(id, title));
    }

    pub unsafe extern "C" fn on_url_changed(id: usize, ptr: *const c_char, len: usize) {
        let url = unsafe { cstr_to_string(ptr, len) };
        ffi_callback(|| crate::tab::handle_url_changed(id, url));
    }

    pub unsafe extern "C" fn on_load_start(id: usize) {
        ffi_callback(|| crate::tab::handle_load_start(id));
    }

    pub unsafe extern "C" fn on_load_finish(id: usize) {
        ffi_callback(|| crate::tab::handle_load_finish(id));
    }

    pub unsafe extern "C" fn on_favicon_change(
        id: usize,
        data: *const u8,
        width: i32,
        height: i32,
        stride: i32,
    ) {
        let Some((bytes, stride)) = (unsafe { favicon_bytes_from_raw(data, height, stride) }) else { return };
        ffi_callback(|| crate::tab::handle_favicon_change(id, bytes, width, height, stride));
    }

    pub unsafe extern "C" fn on_cursor_change(id: usize, ptr: *const c_char, len: usize) {
        let name = unsafe { cstr_to_string(ptr, len) };
        ffi_callback(|| crate::tab::handle_cursor_change(id, name));
    }

    pub unsafe extern "C" fn on_link_hover(id: usize, ptr: *const c_char, len: usize) {
        let url = unsafe { cstr_to_string(ptr, len) };
        ffi_callback(|| crate::tab::handle_link_hover(id, url));
    }

    pub unsafe extern "C" fn on_link_unhover(id: usize) {
        ffi_callback(|| crate::tab::handle_link_unhover(id));
    }

    pub unsafe extern "C" fn on_enter_tooltip_area(id: usize, ptr: *const c_char, len: usize) {
        let text = unsafe { cstr_to_string(ptr, len) };
        ffi_callback(|| crate::tab::handle_enter_tooltip_area(id, text));
    }

    pub unsafe extern "C" fn on_leave_tooltip_area(id: usize) {
        ffi_callback(|| crate::tab::handle_leave_tooltip_area(id));
    }

    pub unsafe extern "C" fn on_request_alert(id: usize, ptr: *const c_char, len: usize) {
        let message = unsafe { cstr_to_string(ptr, len) };
        ffi_callback(|| crate::tab::handle_request_alert(id, message));
    }

    pub unsafe extern "C" fn on_request_confirm(id: usize, ptr: *const c_char, len: usize) {
        let message = unsafe { cstr_to_string(ptr, len) };
        ffi_callback(|| crate::tab::handle_request_confirm(id, message));
    }

    pub unsafe extern "C" fn on_request_prompt(
        id: usize,
        msg_ptr: *const c_char,
        msg_len: usize,
        def_ptr: *const c_char,
        def_len: usize,
    ) {
        let message = unsafe { cstr_to_string(msg_ptr, msg_len) };
        let default = unsafe { cstr_to_string(def_ptr, def_len) };
        ffi_callback(|| crate::tab::handle_request_prompt(id, message, default));
    }

    pub unsafe extern "C" fn on_request_color_picker(id: usize, color: u32) {
        ffi_callback(|| crate::tab::handle_request_color_picker(id, color));
    }

    pub unsafe extern "C" fn on_new_web_view(
        id: usize,
        activate: bool,
        _page_index: i64,
        handle_out: *mut c_char,
        capacity: usize,
    ) {
        let handle = ffi_callback_ret(|| crate::tab::handle_new_web_view(id, activate));
        unsafe { write_c_buffer(handle_out, capacity, &handle) };
    }

    pub unsafe extern "C" fn on_activate_tab(id: usize) {
        ffi_callback(|| crate::tab::handle_activate_tab(id));
    }

    pub unsafe extern "C" fn on_close(id: usize) {
        ffi_callback(|| crate::tab::handle_close(id));
    }

    pub unsafe extern "C" fn on_zoom_level_changed(id: usize) {
        ffi_callback(|| crate::tab::handle_zoom_level_changed(id));
    }

    pub unsafe extern "C" fn on_find_in_page(id: usize, current: u32, has_total: bool, total: u32) {
        ffi_callback(|| crate::tab::handle_find_in_page(id, current, has_total.then_some(total)));
    }

    pub unsafe extern "C" fn on_audio_play_state_changed(id: usize, playing: bool) {
        ffi_callback(|| crate::tab::handle_audio_play_state_changed(id, playing));
    }

    pub unsafe extern "C" fn on_fullscreen_window(id: usize) {
        ffi_callback(|| crate::tab::handle_fullscreen_window(id));
    }

    pub unsafe extern "C" fn on_exit_fullscreen_window(id: usize) {
        ffi_callback(|| crate::tab::handle_exit_fullscreen_window(id));
    }

    pub unsafe extern "C" fn on_maximize_window(id: usize) {
        ffi_callback(|| crate::tab::handle_maximize_window(id));
    }

    pub unsafe extern "C" fn on_minimize_window(id: usize) {
        ffi_callback(|| crate::tab::handle_minimize_window(id));
    }

    pub unsafe extern "C" fn on_restore_window(id: usize) {
        ffi_callback(|| crate::tab::handle_restore_window(id));
    }

    pub unsafe extern "C" fn on_resize_window(id: usize, width: i32, height: i32) {
        ffi_callback(|| crate::tab::handle_resize_window(id, width, height));
    }

    pub unsafe extern "C" fn on_context_menu(
        id: usize,
        x: f64,
        y: f64,
        ptr: *const LadybirdContextMenuItem,
        count: usize,
    ) {
        let items = unsafe { context_menu_entries_from_raw(ptr, count) };
        ffi_callback(|| crate::tab::handle_context_menu(id, x, y, items));
    }

    pub unsafe extern "C" fn on_show_select_dropdown(
        id: usize,
        x: f64,
        y: f64,
        min_width: i32,
        ptr: *const LadybirdSelectItem,
        count: usize,
    ) {
        let items = unsafe { select_entries_from_raw(ptr, count) };
        ffi_callback(|| crate::tab::handle_show_select_dropdown(id, x, y, min_width, items));
    }

    pub unsafe extern "C" fn on_request_file_picker(
        id: usize,
        allow_multiple: bool,
        ptr: *const LadybirdFileFilter,
        count: usize,
    ) {
        let filters = unsafe { file_filters_from_raw(ptr, count) };
        ffi_callback(|| crate::tab::handle_request_file_picker(id, allow_multiple, filters));
    }
}
