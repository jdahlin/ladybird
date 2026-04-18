/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

//! BrowserWindow: a `CompositeTemplate` subclass of `AdwApplicationWindow`
//! that consumes `browser-window.ui`.

use crate::bridge;
use crate::dialogs::DialogRequest;
use crate::tab::{Tab, TabState};
use adw::prelude::*;
use adw::subclass::prelude::*;
use gtk::gio;
use gtk::glib;
use std::cell::RefCell;
use std::rc::Rc;

glib::wrapper! {
    pub struct BrowserWindow(ObjectSubclass<imp::BrowserWindow>)
        @extends gtk::Widget, gtk::Window, gtk::ApplicationWindow, adw::ApplicationWindow,
        @implements gio::ActionGroup, gio::ActionMap, gtk::Root, gtk::Native;
}

impl BrowserWindow {
    pub fn new(app: &adw::Application, initial_urls: &[String]) -> Self {
        let window: Self = glib::Object::builder().property("application", app).build();
        window.install_actions();
        window.install_shortcuts();
        window.wire_signals();
        window.setup_devtools_banner();

        bridge::app_set_toast_overlay(&window.imp().toast_overlay);

        if initial_urls.is_empty() {
            window.open_tab("about:blank", true);
        } else {
            for (i, url) in initial_urls.iter().enumerate() {
                window.open_tab(url, i == 0);
            }
        }
        window
    }

    pub fn open_urls(&self, urls: &[String]) {
        for (i, url) in urls.iter().enumerate() {
            self.open_tab(url, i == 0);
        }
    }

    pub fn notify_tab_url_changed(&self, state: &TabState, url: &str) {
        let is_current = self
            .current_tab_state()
            .as_ref()
            .map(|rc| Rc::as_ptr(rc))
            == Some(state as *const _);
        if is_current {
            self.update_location_entry(url);
        }
    }

    pub fn close_tab_page(&self, page: &adw::TabPage) {
        self.imp().tab_view.close_page(page);
    }

    fn finish_close_tab_page(&self, page: &adw::TabPage) {
        self.imp().tab_view.close_page_finish(page, true);
        self.imp()
            .tabs
            .borrow_mut()
            .retain(|t| t.state().tab_page.borrow().as_ref() != Some(page));
        if self.imp().tabs.borrow().is_empty() {
            self.close();
        }
    }

    pub fn show_dialog_request(&self, state: &TabState, request: DialogRequest) {
        let found = {
            let tabs = self.imp().tabs.borrow();
            tabs.iter()
                .find(|t| Rc::as_ptr(t.state()) == state as *const _)
                .map(|t| (t.widget().clone(), t.view()))
        };
        let Some((widget, view)) = found else { return };
        crate::dialogs::spawn_dialog(&widget, view, request);
    }

    pub fn update_zoom_label(&self) {
        let Some(page) = self.imp().tab_view.selected_page() else { return };
        let zoom = {
            let tabs = self.imp().tabs.borrow();
            tabs.iter()
                .find(|t| t.state().tab_page.borrow().as_ref() == Some(&page))
                .map(|t| t.view().zoom_level())
        };
        if let Some(level) = zoom {
            self.imp()
                .zoom_label
                .set_label(&format!("{}%", (level * 100.0).round() as u32));
            // zoom-reset is enabled only when not at 100 %.
            if let Some(action) = self.lookup_action("zoom-reset") {
                action
                    .downcast::<gio::SimpleAction>()
                    .ok()
                    .inspect(|a| a.set_enabled((level - 1.0).abs() > f64::EPSILON));
            }
        }
    }

    pub fn update_find_result(&self, current: u32, total: Option<u32>) {
        let text = match total {
            Some(t) => format!("{} of {} matches", current + 1, t),
            None => "No matches".to_string(),
        };
        self.imp().find_result_label.set_label(&text);
    }

    pub fn new_popup_tab(&self, activate: bool) -> String {
        // TODO(parity): pass parent client + page_index for child-tab creation
        // (requires bridge support for Tab::new_child).
        let Some(tab) = Tab::new("about:blank") else { return String::new() };
        let handle = tab.view().handle();
        let page = self.imp().tab_view.append(tab.widget());
        page.set_title("New Tab");
        tab.set_tab_page(&page);
        tab.set_window(self.downgrade());
        if activate {
            self.imp().tab_view.set_selected_page(&page);
        }
        self.imp().tabs.borrow_mut().push(tab);
        handle
    }

    fn setup_devtools_banner(&self) {
        if bridge::app_devtools_enabled() {
            let port = bridge::app_devtools_port();
            self.imp()
                .devtools_banner
                .set_title(&format!("DevTools is enabled on port {port}"));
            self.imp().devtools_banner.set_button_label(Some("Stop"));
            self.imp().devtools_banner.set_revealed(true);
        }
        self.imp()
            .devtools_banner
            .connect_button_clicked(move |banner| {
                bridge::app_toggle_devtools();
                banner.set_revealed(false);
            });
    }

    fn open_tab(&self, url: &str, activate: bool) {
        let Some(tab) = Tab::new(url) else { return };
        let page = self.imp().tab_view.append(tab.widget());
        page.set_title("New Tab");
        tab.set_tab_page(&page);
        tab.set_window(self.downgrade());
        if activate {
            self.imp().tab_view.set_selected_page(&page);
            if is_internal_url(url) {
                self.imp().location_entry.set_text("");
                self.imp().location_entry.grab_focus();
            } else {
                self.imp().location_entry.set_text(url);
                tab.widget().grab_focus();
            }
        }
        self.imp().tabs.borrow_mut().push(tab);
    }

    pub fn current_tab_state(&self) -> Option<Rc<TabState>> {
        let page = self.imp().tab_view.selected_page()?;
        self.imp()
            .tabs
            .borrow()
            .iter()
            .find(|t| t.state().tab_page.borrow().as_ref() == Some(&page))
            .map(|t| t.state().clone())
    }

    fn with_current_view<F: FnOnce(&crate::bridge::View)>(&self, f: F) {
        let Some(page) = self.imp().tab_view.selected_page() else { return };
        let tabs = self.imp().tabs.borrow();
        if let Some(tab) = tabs
            .iter()
            .find(|t| t.state().tab_page.borrow().as_ref() == Some(&page))
        {
            f(&tab.view());
        }
    }

    fn focus_current_web_view(&self) {
        let Some(page) = self.imp().tab_view.selected_page() else { return };
        let tabs = self.imp().tabs.borrow();
        if let Some(tab) = tabs
            .iter()
            .find(|t| t.state().tab_page.borrow().as_ref() == Some(&page))
        {
            tab.widget().grab_focus();
        }
    }

    fn navigate_current(&self, input: &str) {
        let url = normalize_url(input);
        if let Some(state) = self.current_tab_state() {
            *state.url.borrow_mut() = url.clone();
            if let Some(page) = state.tab_page.borrow().as_ref() {
                page.set_title(&url);
            }
        }
        self.with_current_view(|v| v.load(&url));
    }

    fn update_location_entry(&self, url: &str) {
        if is_internal_url(url) {
            self.imp().location_entry.set_text("");
        } else {
            self.imp().location_entry.set_text(url);
        }
    }

    fn wire_signals(&self) {
        use glib::clone;

        self.imp().location_entry.connect_activate(clone!(
            #[weak(rename_to = this)] self,
            move |entry| {
                let text = entry.text();
                this.navigate_current(&text);
            }
        ));

        self.imp().tab_view.connect_selected_page_notify(clone!(
            #[weak(rename_to = this)] self,
            move |_| {
                if let Some(state) = this.current_tab_state() {
                    this.update_location_entry(&state.url.borrow().clone());
                }
                this.update_zoom_label();
            }
        ));

        self.imp().tab_view.connect_close_page(clone!(
            #[weak(rename_to = this)] self,
            #[upgrade_or] glib::Propagation::Proceed,
            move |_, page| {
                this.finish_close_tab_page(page);
                glib::Propagation::Stop
            }
        ));

        self.imp().find_entry.connect_search_changed(clone!(
            #[weak(rename_to = this)] self,
            move |entry| {
                let query = entry.text();
                this.with_current_view(|v| v.find_in_page(&query, false));
            }
        ));

        self.imp().find_entry.connect_activate(clone!(
            #[weak(rename_to = this)] self,
            move |_| this.with_current_view(|v| v.find_in_page_next_match())
        ));

        self.imp().find_entry.connect_next_match(clone!(
            #[weak(rename_to = this)] self,
            move |_| this.with_current_view(|v| v.find_in_page_next_match())
        ));

        self.imp().find_entry.connect_previous_match(clone!(
            #[weak(rename_to = this)] self,
            move |_| this.with_current_view(|v| v.find_in_page_previous_match())
        ));

        self.imp().find_entry.connect_stop_search(clone!(
            #[weak(rename_to = this)] self,
            move |_| {
                this.imp().find_bar_revealer.set_reveal_child(false);
                this.imp().find_result_label.set_label("");
                this.focus_current_web_view();
            }
        ));

        // Hide title buttons and show restore button in fullscreen.
        self.connect_fullscreened_notify(|window| {
            let fullscreen = window.is_fullscreen();
            window
                .imp()
                .header_bar
                .set_show_start_title_buttons(!fullscreen);
            window
                .imp()
                .header_bar
                .set_show_end_title_buttons(!fullscreen);
            window.imp().restore_button.set_visible(fullscreen);
        });
    }

    fn install_actions(&self) {
        use gio::SimpleAction;
        let add = |win: &BrowserWindow, name: &str, enabled: bool, f: Box<dyn Fn(&BrowserWindow)>| {
            let action = SimpleAction::new(name, None);
            action.set_enabled(enabled);
            let this = win.clone();
            action.connect_activate(move |_, _| f(&this));
            win.add_action(&action);
        };

        add(self, "go-back", false, Box::new(|w| w.with_current_view(|v| v.go_back())));
        add(self, "go-forward", false, Box::new(|w| w.with_current_view(|v| v.go_forward())));
        add(self, "reload", true, Box::new(|w| w.with_current_view(|v| v.reload())));
        add(self, "new-tab", true, Box::new(|w| w.open_tab("about:blank", true)));
        add(self, "new-window", true, Box::new(|w| {
            if let Some(app) = w.application().and_then(|a| a.downcast::<adw::Application>().ok()) {
                BrowserWindow::new(&app, &[]).present();
            }
        }));
        add(self, "close-tab", true, Box::new(|w| {
            if let Some(page) = w.imp().tab_view.selected_page() {
                w.close_tab_page(&page);
            }
        }));
        add(self, "zoom-in", true, Box::new(|w| w.with_current_view(|v| v.zoom_in())));
        add(self, "zoom-out", true, Box::new(|w| w.with_current_view(|v| v.zoom_out())));
        add(self, "zoom-reset", false, Box::new(|w| {
            w.with_current_view(|v| v.reset_zoom());
            w.update_zoom_label();
        }));
        add(self, "find", true, Box::new(|w| {
            w.imp().find_bar_revealer.set_reveal_child(true);
            w.imp().find_entry.grab_focus();
        }));
        add(self, "find-close", true, Box::new(|w| {
            w.imp().find_bar_revealer.set_reveal_child(false);
            w.imp().find_result_label.set_label("");
            w.focus_current_web_view();
        }));
        add(self, "find-next", true, Box::new(|w| w.with_current_view(|v| v.find_in_page_next_match())));
        add(self, "find-previous", true, Box::new(|w| w.with_current_view(|v| v.find_in_page_previous_match())));
        add(self, "fullscreen", true, Box::new(|w| {
            if w.is_fullscreen() { w.unfullscreen(); } else { w.fullscreen(); }
        }));
        add(self, "focus-location", true, Box::new(|w| {
            w.imp().location_entry.grab_focus_without_selecting();
            w.imp().location_entry.select_region(0, -1);
        }));
        add(self, "devtools", true, Box::new(|w| {
            bridge::app_toggle_devtools();
            let enabled = bridge::app_devtools_enabled();
            w.imp().devtools_banner.set_revealed(enabled);
            if enabled {
                let port = bridge::app_devtools_port();
                w.imp()
                    .devtools_banner
                    .set_title(&format!("DevTools is enabled on port {port}"));
                w.imp().devtools_banner.set_button_label(Some("Stop"));
            }
        }));
        add(self, "preferences", true, Box::new(|_| bridge::app_activate_settings()));
        add(self, "about", true, Box::new(|_| bridge::app_activate_about()));
        add(self, "quit", true, Box::new(|w| {
            bridge::app_quit(0);
            if let Some(app) = w.application() {
                app.quit();
            }
        }));
    }

    fn install_shortcuts(&self) {
        let sc = gtk::ShortcutController::new();
        let bind = |sc: &gtk::ShortcutController, accel: &str, action: &str| {
            if let Some(trigger) = gtk::ShortcutTrigger::parse_string(accel) {
                let action = gtk::NamedAction::new(action);
                sc.add_shortcut(gtk::Shortcut::new(Some(trigger), Some(action)));
            }
        };
        bind(&sc, "<Control>t", "win.new-tab");
        bind(&sc, "<Control>w", "win.close-tab");
        bind(&sc, "<Control>r", "win.reload");
        bind(&sc, "F5", "win.reload");
        bind(&sc, "<Alt>Left", "win.go-back");
        bind(&sc, "<Alt>Right", "win.go-forward");
        bind(&sc, "<Control>l", "win.focus-location");
        bind(&sc, "<Control>f", "win.find");
        bind(&sc, "Escape", "win.find-close");
        bind(&sc, "<Control>plus", "win.zoom-in");
        bind(&sc, "<Control>equal", "win.zoom-in");
        bind(&sc, "<Control>minus", "win.zoom-out");
        bind(&sc, "<Control>0", "win.zoom-reset");
        bind(&sc, "F11", "win.fullscreen");
        bind(&sc, "<Control>n", "win.new-window");
        bind(&sc, "<Control><Shift>i", "win.devtools");
        bind(&sc, "<Control><Shift>c", "win.devtools");
        bind(&sc, "F12", "win.devtools");
        self.add_controller(sc);
    }
}

fn is_internal_url(url: &str) -> bool {
    url.is_empty() || url == "about:blank" || url == "about:newtab"
}

/// Crude URL normaliser — TODO(parity): replace with bridge `sanitize_url`.
fn normalize_url(input: &str) -> String {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return "about:blank".into();
    }
    if trimmed.contains("://") {
        return trimmed.to_string();
    }
    if trimmed.contains('.') && !trimmed.contains(' ') {
        return format!("https://{trimmed}");
    }
    format!("https://duckduckgo.com/?q={}", urlencode(trimmed))
}

fn urlencode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char);
            }
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

mod imp {
    use super::*;
    use gtk::CompositeTemplate;

    #[derive(Default, CompositeTemplate)]
    #[template(resource = "/org/ladybird/Ladybird/gtk/browser-window.ui")]
    pub struct BrowserWindow {
        #[template_child]
        pub toolbar_view: TemplateChild<adw::ToolbarView>,
        #[template_child]
        pub header_bar: TemplateChild<adw::HeaderBar>,
        #[template_child]
        pub location_entry: TemplateChild<gtk::Entry>,
        #[template_child]
        pub tab_view: TemplateChild<adw::TabView>,
        #[template_child]
        pub tab_bar: TemplateChild<adw::TabBar>,
        #[template_child]
        pub toast_overlay: TemplateChild<adw::ToastOverlay>,
        #[template_child]
        pub find_bar_revealer: TemplateChild<gtk::Revealer>,
        #[template_child]
        pub find_entry: TemplateChild<gtk::SearchEntry>,
        #[template_child]
        pub find_result_label: TemplateChild<gtk::Label>,
        #[template_child]
        pub zoom_label: TemplateChild<gtk::Label>,
        #[template_child]
        pub devtools_banner: TemplateChild<adw::Banner>,
        #[template_child]
        pub restore_button: TemplateChild<gtk::Button>,

        pub tabs: RefCell<Vec<Tab>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for BrowserWindow {
        const NAME: &'static str = "LadybirdBrowserWindow";
        type Type = super::BrowserWindow;
        type ParentType = adw::ApplicationWindow;

        fn class_init(klass: &mut Self::Class) {
            klass.bind_template();
        }

        fn instance_init(obj: &glib::subclass::InitializingObject<Self>) {
            obj.init_template();
        }
    }

    impl ObjectImpl for BrowserWindow {}
    impl WidgetImpl for BrowserWindow {}
    impl WindowImpl for BrowserWindow {}
    impl ApplicationWindowImpl for BrowserWindow {}
    impl AdwApplicationWindowImpl for BrowserWindow {}
}
