/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

//! Async dialog requests from the view (alert/confirm/prompt/color). Each
//! request ends with a reply back to the `View`.

use crate::bridge::{FileFilter, View};
use adw::prelude::*;
use adw::{AlertDialog, ResponseAppearance};
use gtk::gio;
use gtk::glib;
use std::rc::Rc;

#[derive(Debug, Clone)]
pub enum DialogRequest {
    Alert { message: String },
    Confirm { message: String },
    Prompt { message: String, default: String },
    Color { initial: u32 },
}

pub fn spawn_dialog(
    parent: &impl IsA<gtk::Widget>,
    view: Rc<View>,
    request: DialogRequest,
) {
    let parent = parent.clone().upcast::<gtk::Widget>();
    let ctx = glib::MainContext::default();
    ctx.spawn_local(async move {
        match request {
            DialogRequest::Alert { message } => {
                run_alert(&parent, &message).await;
                view.alert_closed();
            }
            DialogRequest::Confirm { message } => {
                let accepted = run_confirm(&parent, &message).await;
                view.confirm_closed(accepted);
            }
            DialogRequest::Prompt { message, default } => {
                let reply = run_prompt(&parent, &message, &default).await;
                view.prompt_closed(reply.as_deref());
            }
            DialogRequest::Color { initial } => {
                let picked = run_color_picker(&parent, initial).await;
                if let Some(rgba) = picked {
                    view.color_picker_update(rgba, true);
                } else {
                    view.color_picker_update(initial, true);
                }
            }
        }
    });
}

async fn run_alert(parent: &gtk::Widget, message: &str) {
    let dialog = AlertDialog::new(Some("Alert"), Some(message));
    dialog.add_response("ok", "OK");
    dialog.set_default_response(Some("ok"));
    dialog.set_close_response("ok");
    dialog.choose_future(parent).await;
}

async fn run_confirm(parent: &gtk::Widget, message: &str) -> bool {
    let dialog = AlertDialog::new(Some("Confirm"), Some(message));
    dialog.add_response("cancel", "Cancel");
    dialog.add_response("ok", "OK");
    dialog.set_response_appearance("ok", ResponseAppearance::Suggested);
    dialog.set_default_response(Some("ok"));
    dialog.set_close_response("cancel");
    dialog.choose_future(parent).await == "ok"
}

async fn run_prompt(parent: &gtk::Widget, message: &str, default: &str) -> Option<String> {
    let dialog = AlertDialog::new(Some("Prompt"), Some(message));
    let entry = gtk::Entry::new();
    entry.set_text(default);
    entry.set_margin_top(12);
    entry.set_margin_bottom(12);
    entry.set_margin_start(12);
    entry.set_margin_end(12);
    dialog.set_extra_child(Some(&entry));
    dialog.add_response("cancel", "Cancel");
    dialog.add_response("ok", "OK");
    dialog.set_response_appearance("ok", ResponseAppearance::Suggested);
    dialog.set_default_response(Some("ok"));
    dialog.set_close_response("cancel");

    let response = dialog.choose_future(parent).await;
    if response == "ok" {
        Some(entry.text().to_string())
    } else {
        None
    }
}

pub fn spawn_file_picker(
    parent: &impl IsA<gtk::Widget>,
    view: Rc<View>,
    allow_multiple: bool,
    filters: Vec<FileFilter>,
) {
    let parent = parent.clone().upcast::<gtk::Widget>();
    let ctx = glib::MainContext::default();
    ctx.spawn_local(async move {
        let paths = run_file_picker(&parent, allow_multiple, &filters).await;
        view.file_picker_closed(&paths);
    });
}

async fn run_file_picker(
    parent: &gtk::Widget,
    allow_multiple: bool,
    filters: &[FileFilter],
) -> Vec<std::path::PathBuf> {
    let dialog = gtk::FileDialog::new();
    dialog.set_title("Open File");

    if !filters.is_empty() {
        let filter_list = gio::ListStore::new::<gtk::FileFilter>();
        let combined = gtk::FileFilter::new();
        combined.set_name(Some("Accepted Files"));
        for f in filters {
            match f {
                FileFilter::Extension(ext) => combined.add_suffix(ext),
                FileFilter::MimeType(mime) => combined.add_mime_type(mime),
                FileFilter::Audio => combined.add_mime_type("audio/*"),
                FileFilter::Image => combined.add_mime_type("image/*"),
                FileFilter::Video => combined.add_mime_type("video/*"),
            }
        }
        filter_list.append(&combined);
        let all = gtk::FileFilter::new();
        all.set_name(Some("All Files"));
        all.add_pattern("*");
        filter_list.append(&all);
        dialog.set_filters(Some(&filter_list));
    }

    let window = parent.root().and_downcast::<gtk::Window>();

    if allow_multiple {
        match dialog.open_multiple_future(window.as_ref()).await {
            Ok(model) => (0..model.n_items())
                .filter_map(|i| model.item(i)?.downcast::<gio::File>().ok()?.path())
                .collect(),
            Err(_) => Vec::new(),
        }
    } else {
        match dialog.open_future(window.as_ref()).await {
            Ok(file) => file.path().map(|p| vec![p]).unwrap_or_default(),
            Err(_) => Vec::new(),
        }
    }
}

async fn run_color_picker(parent: &gtk::Widget, initial: u32) -> Option<u32> {
    let dialog = gtk::ColorDialog::new();
    dialog.set_with_alpha(true);
    let r = ((initial >> 24) & 0xff) as f32 / 255.0;
    let g = ((initial >> 16) & 0xff) as f32 / 255.0;
    let b = ((initial >> 8) & 0xff) as f32 / 255.0;
    let a = (initial & 0xff) as f32 / 255.0;
    let initial_rgba = gdk::RGBA::new(r, g, b, a);

    let window = parent.root().and_downcast::<gtk::Window>();
    let rgba = dialog
        .choose_rgba_future(window.as_ref(), Some(&initial_rgba))
        .await
        .ok()?;
    let r = (rgba.red() * 255.0) as u32 & 0xff;
    let g = (rgba.green() * 255.0) as u32 & 0xff;
    let b = (rgba.blue() * 255.0) as u32 & 0xff;
    let a = (rgba.alpha() * 255.0) as u32 & 0xff;
    Some((r << 24) | (g << 16) | (b << 8) | a)
}

