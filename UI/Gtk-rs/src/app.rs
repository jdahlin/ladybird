/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

use crate::bridge;
use crate::window::BrowserWindow;
use adw::prelude::*;
use gtk::gio;
use gtk::gio::ApplicationFlags;
use gtk::glib;
use std::cell::RefCell;
use std::ffi::{CString, c_char};

thread_local! {
    static WINDOWS: RefCell<Vec<BrowserWindow>> = const { RefCell::new(Vec::new()) };
}

pub fn run() -> i32 {
    use std::os::unix::ffi::OsStrExt;
    let args_os: Vec<_> = std::env::args_os().collect();
    let cstrings: Vec<_> = args_os
        .iter()
        .map(|a| CString::new(a.as_bytes()).unwrap_or_default())
        .collect();
    let ptrs: Vec<*mut c_char> = cstrings.iter().map(|s| s.as_ptr() as *mut _).collect();
    run_impl(&ptrs)
}

fn run_impl(args: &[*mut c_char]) -> i32 {
    if bridge::app_init(args) != 0 {
        return 1;
    }

    if bridge::app_is_headless() {
        bridge::app_shutdown();
        return 0;
    }

    adw::init().expect("adw::init");

    let app = adw::Application::builder()
        .application_id("org.ladybird.Ladybird.GtkRs")
        .flags(ApplicationFlags::NON_UNIQUE)
        .build();

    install_application_actions(&app);
    app.connect_activate(on_activate);

    let empty: [&str; 0] = [];
    let exit = app.run_with_args(&empty).value();

    WINDOWS.with_borrow_mut(Vec::clear);
    bridge::app_shutdown();

    exit
}

fn on_activate(app: &adw::Application) {
    let already_open = WINDOWS.with_borrow(|windows| !windows.is_empty());
    if already_open {
        WINDOWS.with_borrow(|windows| windows[0].present());
        return;
    }

    let urls = bridge::initial_urls();
    let window = BrowserWindow::new(app, &urls);

    {
        let app = app.clone();
        window.connect_close_request(move |_| {
            request_quit(&app);
            glib::Propagation::Stop
        });
    }

    window.present();
    WINDOWS.with_borrow_mut(|windows| windows.push(window));
}

fn install_application_actions(app: &adw::Application) {
    let quit = gio::SimpleAction::new("quit", None);
    {
        let app = app.clone();
        quit.connect_activate(move |_, _| {
            request_quit(&app);
        });
    }
    app.add_action(&quit);
    app.set_accels_for_action("app.quit", &["<Control>q"]);
}

fn request_quit(app: &adw::Application) {
    let app = app.clone();
    glib::idle_add_local_once(move || {
        quit_application(&app);
    });
}

fn quit_application(app: &adw::Application) {
    WINDOWS.with_borrow_mut(Vec::clear);
    bridge::app_quit(0);
    app.quit();
}
