/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

use crate::bridge::{tab_callbacks, ContextMenuEntry, FileFilter, KeyEventType, LadybirdViewCallbacks, MouseEventType, SelectEntry, View};
use crate::dialogs::DialogRequest;
use crate::web_view::WebView;
use adw::prelude::*;
use adw::TabPage;
use gtk::glib;
use gtk::glib::translate::IntoGlib;
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;

thread_local! {
    static NEXT_ID: Cell<usize> = const { Cell::new(1) };
    static STATES: RefCell<HashMap<usize, Rc<TabState>>> = RefCell::new(HashMap::new());
}

// RAII guard: removes state from the registry on drop unless defused.
struct StateGuard(usize);

impl StateGuard {
    fn arm(id: usize, state: Rc<TabState>) -> Self {
        STATES.with_borrow_mut(|m| m.insert(id, state));
        Self(id)
    }

    fn defuse(self) {
        std::mem::forget(self);
    }
}

impl Drop for StateGuard {
    fn drop(&mut self) {
        STATES.with_borrow_mut(|m| m.remove(&self.0));
    }
}

pub struct TabState {
    pub title: RefCell<String>,
    pub url: RefCell<String>,
    pub loading: RefCell<bool>,
    pub tab_page: RefCell<Option<TabPage>>,
    pub widget: RefCell<Option<WebView>>,
    pub window: RefCell<Option<glib::WeakRef<crate::window::BrowserWindow>>>,
    pub view: RefCell<Option<std::rc::Weak<View>>>,
}

impl TabState {
    fn new(initial_url: &str) -> Self {
        Self {
            title: RefCell::new("New Tab".into()),
            url: RefCell::new(initial_url.into()),
            loading: RefCell::new(false),
            tab_page: RefCell::new(None),
            widget: RefCell::new(None),
            window: RefCell::new(None),
            view: RefCell::new(None),
        }
    }
}

pub struct Tab {
    id: usize,
    view: Rc<View>,
    state: Rc<TabState>,
    widget: WebView,
}

impl Tab {
    pub fn new(initial_url: &str) -> Option<Self> {
        let id = NEXT_ID.with(|c| { let id = c.get(); c.set(id + 1); id });
        let state = Rc::new(TabState::new(initial_url));
        let guard = StateGuard::arm(id, state.clone());

        let callbacks = build_callbacks();
        let view = Rc::new(View::create(id, &callbacks)?); // guard drops and removes state on None
        guard.defuse();

        let widget = WebView::new();
        widget.attach_view(view.clone());
        *state.widget.borrow_mut() = Some(widget.clone());
        *state.view.borrow_mut() = Some(Rc::downgrade(&view));
        install_input_controllers(&widget, &view);
        install_visibility_handlers(&widget, &view);

        view.load(initial_url);

        Some(Self { id, view, state, widget })
    }

    pub fn widget(&self) -> &gtk::Widget {
        self.widget.upcast_ref()
    }

    pub fn state(&self) -> &Rc<TabState> {
        &self.state
    }

    pub fn view(&self) -> Rc<View> {
        self.view.clone()
    }

    pub fn set_tab_page(&self, page: &TabPage) {
        *self.state.tab_page.borrow_mut() = Some(page.clone());
        page.set_title(&self.state.title.borrow());
        page.set_loading(*self.state.loading.borrow());
    }

    pub fn set_window(&self, window: glib::WeakRef<crate::window::BrowserWindow>) {
        *self.state.window.borrow_mut() = Some(window);
    }

    pub fn detach_state(&self) {
        self.widget.detach_view();
        *self.state.widget.borrow_mut() = None;
    }
}

impl Drop for Tab {
    fn drop(&mut self) {
        self.detach_state();
        STATES.with_borrow_mut(|m| m.remove(&self.id));
    }
}

// ---- Input controllers -------------------------------------------------------

fn install_visibility_handlers(widget: &WebView, view: &Rc<View>) {
    {
        let view = Rc::downgrade(view);
        widget.connect_map(move |widget| {
            let Some(view) = view.upgrade() else { return };
            view.set_system_visibility(true);
            view.set_device_pixel_ratio(f64::from(widget.scale_factor()));
            let width = widget.width();
            let height = widget.height();
            if width > 0 && height > 0 {
                view.set_viewport_size(width, height);
            }
            widget.queue_draw();
        });
    }
    {
        let view = Rc::downgrade(view);
        widget.connect_unmap(move |_| {
            if let Some(view) = view.upgrade() {
                view.set_system_visibility(false);
            }
        });
    }
}

fn install_input_controllers(widget: &WebView, view: &Rc<View>) {
    let focus = gtk::EventControllerFocus::new();
    {
        let view = Rc::downgrade(view);
        focus.connect_enter(move |_| {
            if let Some(view) = view.upgrade() {
                view.set_has_focus(true);
            }
        });
    }
    {
        let view = Rc::downgrade(view);
        focus.connect_leave(move |_| {
            if let Some(view) = view.upgrade() {
                view.set_has_focus(false);
            }
        });
    }
    widget.add_controller(focus);

    let keys = gtk::EventControllerKey::new();
    {
        let view = Rc::downgrade(view);
        keys.connect_key_pressed(move |_, keyval, _, state| {
            if let Some(view) = view.upgrade() {
                view.key_event(KeyEventType::Down, keyval.into_glib(), state.into_glib());
                glib::Propagation::Stop
            } else {
                glib::Propagation::Proceed
            }
        });
    }
    {
        let view = Rc::downgrade(view);
        keys.connect_key_released(move |_, keyval, _, state| {
            if let Some(view) = view.upgrade() {
                view.key_event(KeyEventType::Up, keyval.into_glib(), state.into_glib());
            }
        });
    }
    widget.add_controller(keys);

    let click = gtk::GestureClick::new();
    click.set_button(0);
    {
        let view = Rc::downgrade(view);
        let widget_weak = widget.downgrade();
        click.connect_pressed(move |gesture, click_count, x, y| {
            if let Some(w) = widget_weak.upgrade() {
                w.grab_focus();
            }
            if let Some(view) = view.upgrade() {
                view.mouse_event(
                    MouseEventType::Down,
                    x,
                    y,
                    gesture.current_button(),
                    gesture.current_event_state().into_glib(),
                    click_count,
                );
            }
        });
    }
    {
        let view = Rc::downgrade(view);
        click.connect_released(move |gesture, click_count, x, y| {
            if let Some(view) = view.upgrade() {
                view.mouse_event(
                    MouseEventType::Up,
                    x,
                    y,
                    gesture.current_button(),
                    gesture.current_event_state().into_glib(),
                    click_count,
                );
            }
        });
    }
    widget.add_controller(click);

    let motion = gtk::EventControllerMotion::new();
    {
        let view = Rc::downgrade(view);
        motion.connect_motion(move |controller, x, y| {
            if let Some(view) = view.upgrade() {
                view.mouse_event(
                    MouseEventType::Move,
                    x,
                    y,
                    0,
                    controller.current_event_state().into_glib(),
                    0,
                );
            }
        });
    }
    {
        let view = Rc::downgrade(view);
        motion.connect_leave(move |_| {
            if let Some(view) = view.upgrade() {
                view.mouse_event(MouseEventType::Leave, 0.0, 0.0, 0, 0, 0);
            }
        });
    }
    widget.add_controller(motion);

    let scroll = gtk::EventControllerScroll::new(gtk::EventControllerScrollFlags::BOTH_AXES);
    {
        let view = Rc::downgrade(view);
        scroll.connect_scroll(move |controller, dx, dy| {
            let Some(view) = view.upgrade() else {
                return glib::Propagation::Proceed;
            };
            let state = controller.current_event_state().into_glib();
            if controller
                .current_event_state()
                .contains(gdk::ModifierType::CONTROL_MASK)
            {
                if dy < 0.0 {
                    view.zoom_in();
                } else if dy > 0.0 {
                    view.zoom_out();
                }
            } else {
                view.scroll_event(dx, dy, state, controller.unit().into_glib());
            }
            glib::Propagation::Stop
        });
    }
    widget.add_controller(scroll);
}

// ---- Callback vtable ---------------------------------------------------------

fn build_callbacks() -> LadybirdViewCallbacks {
    LadybirdViewCallbacks {
        on_ready_to_paint: Some(tab_callbacks::on_ready_to_paint),
        on_title_changed: Some(tab_callbacks::on_title_changed),
        on_url_changed: Some(tab_callbacks::on_url_changed),
        on_load_start: Some(tab_callbacks::on_load_start),
        on_load_finish: Some(tab_callbacks::on_load_finish),
        on_favicon_change: Some(tab_callbacks::on_favicon_change),
        on_cursor_change: Some(tab_callbacks::on_cursor_change),
        on_link_hover: Some(tab_callbacks::on_link_hover),
        on_link_unhover: Some(tab_callbacks::on_link_unhover),
        on_enter_tooltip_area: Some(tab_callbacks::on_enter_tooltip_area),
        on_leave_tooltip_area: Some(tab_callbacks::on_leave_tooltip_area),
        on_request_alert: Some(tab_callbacks::on_request_alert),
        on_request_confirm: Some(tab_callbacks::on_request_confirm),
        on_request_prompt: Some(tab_callbacks::on_request_prompt),
        on_request_color_picker: Some(tab_callbacks::on_request_color_picker),
        on_new_web_view: Some(tab_callbacks::on_new_web_view),
        on_activate_tab: Some(tab_callbacks::on_activate_tab),
        on_close: Some(tab_callbacks::on_close),
        on_zoom_level_changed: Some(tab_callbacks::on_zoom_level_changed),
        on_find_in_page: Some(tab_callbacks::on_find_in_page),
        on_audio_play_state_changed: Some(tab_callbacks::on_audio_play_state_changed),
        on_fullscreen_window: Some(tab_callbacks::on_fullscreen_window),
        on_exit_fullscreen_window: Some(tab_callbacks::on_exit_fullscreen_window),
        on_maximize_window: Some(tab_callbacks::on_maximize_window),
        on_minimize_window: Some(tab_callbacks::on_minimize_window),
        on_restore_window: Some(tab_callbacks::on_restore_window),
        on_resize_window: Some(tab_callbacks::on_resize_window),
        on_context_menu: Some(tab_callbacks::on_context_menu),
        on_show_select_dropdown: Some(tab_callbacks::on_show_select_dropdown),
        on_request_file_picker: Some(tab_callbacks::on_request_file_picker),
    }
}

// ---- Helpers -----------------------------------------------------------------

fn with_state<F: FnOnce(&TabState)>(id: usize, f: F) {
    STATES.with_borrow(|m| {
        if let Some(state) = m.get(&id) {
            f(state);
        }
    });
}

fn with_window<F>(state: &TabState, f: F)
where
    F: FnOnce(&crate::window::BrowserWindow),
{
    if let Some(window) = state.window.borrow().as_ref().and_then(|w| w.upgrade()) {
        f(&window);
    }
}

fn state_widget_and_view(state: &TabState) -> Option<(gtk::Widget, Rc<View>)> {
    let widget = state.widget.borrow().as_ref()?.clone().upcast::<gtk::Widget>();
    let view = state.view.borrow().as_ref()?.upgrade()?;
    Some((widget, view))
}

// ---- Safe callback handlers --------------------------------------------------

pub(crate) fn handle_ready_to_paint(id: usize) {
    with_state(id, |state| {
        if let Some(widget) = state.widget.borrow().as_ref() {
            widget.queue_draw();
        }
    });
}

pub(crate) fn handle_title_changed(id: usize, title: String) {
    with_state(id, |state| {
        *state.title.borrow_mut() = title.clone();
        if let Some(page) = state.tab_page.borrow().as_ref() {
            page.set_title(&title);
        }
    });
}

pub(crate) fn handle_url_changed(id: usize, url: String) {
    with_state(id, |state| {
        *state.url.borrow_mut() = url.clone();
        with_window(state, |w| w.notify_tab_url_changed(state, &url));
    });
}

pub(crate) fn handle_load_start(id: usize) {
    with_state(id, |state| {
        *state.loading.borrow_mut() = true;
        if let Some(page) = state.tab_page.borrow().as_ref() {
            page.set_loading(true);
        }
    });
}

pub(crate) fn handle_load_finish(id: usize) {
    with_state(id, |state| {
        *state.loading.borrow_mut() = false;
        if let Some(page) = state.tab_page.borrow().as_ref() {
            page.set_loading(false);
        }
    });
}

pub(crate) fn handle_favicon_change(id: usize, bytes: Vec<u8>, width: i32, height: i32, stride: usize) {
    with_state(id, |state| {
        let Some(page) = state.tab_page.borrow().as_ref().cloned() else { return };
        let bytes = glib::Bytes::from(&bytes);
        let texture = gdk::MemoryTexture::new(
            width, height,
            gdk::MemoryFormat::B8g8r8a8Premultiplied,
            &bytes,
            stride,
        );
        page.set_icon(Some(&texture));
    });
}

pub(crate) fn handle_cursor_change(id: usize, name: String) {
    with_state(id, |state| {
        let Some(widget) = state.widget.borrow().as_ref().cloned() else { return };
        let cursor = gdk::Cursor::from_name(&name, None);
        widget.set_cursor(cursor.as_ref());
    });
}

pub(crate) fn handle_link_hover(id: usize, url: String) {
    with_state(id, |state| {
        if let Some(widget) = state.widget.borrow().as_ref() {
            widget.set_tooltip_text(Some(&url));
        }
    });
}

pub(crate) fn handle_link_unhover(id: usize) {
    with_state(id, |state| {
        if let Some(widget) = state.widget.borrow().as_ref() {
            widget.set_tooltip_text(None);
        }
    });
}

pub(crate) fn handle_enter_tooltip_area(id: usize, text: String) {
    with_state(id, |state| {
        if let Some(widget) = state.widget.borrow().as_ref() {
            widget.set_tooltip_text(Some(&text));
        }
    });
}

pub(crate) fn handle_leave_tooltip_area(id: usize) {
    with_state(id, |state| {
        if let Some(widget) = state.widget.borrow().as_ref() {
            widget.set_tooltip_text(None);
        }
    });
}

pub(crate) fn handle_request_alert(id: usize, message: String) {
    with_state(id, |state| {
        with_window(state, |w| {
            w.show_dialog_request(state, DialogRequest::Alert { message: message.clone() });
        });
    });
}

pub(crate) fn handle_request_confirm(id: usize, message: String) {
    with_state(id, |state| {
        with_window(state, |w| {
            w.show_dialog_request(state, DialogRequest::Confirm { message: message.clone() });
        });
    });
}

pub(crate) fn handle_request_prompt(id: usize, message: String, default: String) {
    with_state(id, |state| {
        with_window(state, |w| {
            w.show_dialog_request(
                state,
                DialogRequest::Prompt { message: message.clone(), default: default.clone() },
            );
        });
    });
}

pub(crate) fn handle_request_color_picker(id: usize, color: u32) {
    with_state(id, |state| {
        with_window(state, |w| {
            w.show_dialog_request(state, DialogRequest::Color { initial: color });
        });
    });
}

pub(crate) fn handle_new_web_view(id: usize, activate: bool) -> String {
    let mut result = String::new();
    with_state(id, |state| {
        with_window(state, |w| result = w.new_popup_tab(activate));
    });
    result
}

pub(crate) fn handle_activate_tab(id: usize) {
    with_state(id, |state| {
        if let Some(widget) = state.widget.borrow().as_ref() {
            widget.grab_focus();
        }
    });
}

pub(crate) fn handle_close(id: usize) {
    with_state(id, |state| {
        with_window(state, |w| {
            if let Some(page) = state.tab_page.borrow().as_ref() {
                w.close_tab_page(page);
            }
        });
    });
}

pub(crate) fn handle_zoom_level_changed(id: usize) {
    with_state(id, |state| with_window(state, |w| w.update_zoom_label()));
}

pub(crate) fn handle_find_in_page(id: usize, current: u32, total: Option<u32>) {
    with_state(id, |state| {
        with_window(state, |w| w.update_find_result(current, total));
    });
}

pub(crate) fn handle_audio_play_state_changed(id: usize, playing: bool) {
    with_state(id, |state| {
        let Some(page) = state.tab_page.borrow().as_ref().cloned() else { return };
        let icon = if playing {
            Some(gtk::gio::ThemedIcon::new("audio-volume-high-symbolic"))
        } else {
            None
        };
        page.set_indicator_icon(icon.as_ref().map(|i| i.upcast_ref::<gtk::gio::Icon>()));
    });
}

pub(crate) fn handle_fullscreen_window(id: usize) {
    with_state(id, |state| with_window(state, |w| w.fullscreen()));
}

pub(crate) fn handle_exit_fullscreen_window(id: usize) {
    with_state(id, |state| with_window(state, |w| w.unfullscreen()));
}

pub(crate) fn handle_maximize_window(id: usize) {
    with_state(id, |state| with_window(state, |w| w.maximize()));
}

pub(crate) fn handle_minimize_window(id: usize) {
    with_state(id, |state| with_window(state, |w| w.minimize()));
}

pub(crate) fn handle_restore_window(id: usize) {
    with_state(id, |state| {
        with_window(state, |w| {
            w.unmaximize();
            w.unfullscreen();
        });
    });
}

pub(crate) fn handle_resize_window(id: usize, width: i32, height: i32) {
    with_state(id, |state| {
        with_window(state, |w| w.set_default_size(width, height));
    });
}

pub(crate) fn handle_context_menu(id: usize, x: f64, y: f64, items: Vec<ContextMenuEntry>) {
    with_state(id, |state| {
        let Some((widget, view)) = state_widget_and_view(state) else { return };
        crate::menus::show_context_menu(&widget, view, x, y, &items);
    });
}

pub(crate) fn handle_show_select_dropdown(id: usize, x: f64, y: f64, min_width: i32, items: Vec<SelectEntry>) {
    with_state(id, |state| {
        let Some((widget, view)) = state_widget_and_view(state) else { return };
        crate::menus::show_select_dropdown(&widget, view, x, y, min_width, &items);
    });
}

pub(crate) fn handle_request_file_picker(id: usize, allow_multiple: bool, filters: Vec<FileFilter>) {
    with_state(id, |state| {
        let Some((widget, view)) = state_widget_and_view(state) else { return };
        crate::dialogs::spawn_file_picker(&widget, view, allow_multiple, filters);
    });
}

