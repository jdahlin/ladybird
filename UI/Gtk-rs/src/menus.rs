/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

use crate::bridge::{ContextMenuEntry, SelectEntry, View};
use gtk::gio;
use gtk::prelude::*;
use std::cell::Cell;
use std::rc::Rc;

pub fn show_context_menu(
    widget: &gtk::Widget,
    view: Rc<View>,
    x: f64,
    y: f64,
    items: &[ContextMenuEntry],
) {
    let group = gio::SimpleActionGroup::new();
    let menu = gio::Menu::new();
    let mut section = gio::Menu::new();
    let mut section_label: Option<String> = None;
    let mut section_count = 0usize;

    let flush_section = |menu: &gio::Menu, section: &mut gio::Menu, label: &mut Option<String>, count: &mut usize| {
        if *count > 0 {
            menu.append_section(label.as_deref(), section);
            *section = gio::Menu::new();
            *label = None;
            *count = 0;
        }
    };

    for item in items {
        match item {
            ContextMenuEntry::Separator => {
                flush_section(&menu, &mut section, &mut section_label, &mut section_count);
            }
            ContextMenuEntry::SectionStart { label } => {
                flush_section(&menu, &mut section, &mut section_label, &mut section_count);
                section_label = Some(label.clone());
            }
            ContextMenuEntry::SectionEnd => {
                flush_section(&menu, &mut section, &mut section_label, &mut section_count);
            }
            ContextMenuEntry::Item { id, label, enabled, .. } => {
                let name = format!("item{id}");
                let action = gio::SimpleAction::new(&name, None);
                action.set_enabled(*enabled);
                let id = *id;
                let view_weak = Rc::downgrade(&view);
                action.connect_activate(move |_, _| {
                    if let Some(v) = view_weak.upgrade() {
                        v.context_menu_action(id);
                    }
                });
                group.add_action(&action);
                section.append(Some(label), Some(&format!("ctx.{name}")));
                section_count += 1;
            }
        }
    }

    if section_count > 0 {
        menu.append_section(section_label.as_deref(), &section);
    }

    widget.insert_action_group("ctx", Some(&group));

    let popover = gtk::PopoverMenu::from_model(Some(&menu));
    popover.set_parent(widget);
    popover.set_has_arrow(false);
    let rect = gdk::Rectangle::new(x as i32, y as i32, 1, 1);
    popover.set_pointing_to(Some(&rect));

    let widget_weak = widget.downgrade();
    popover.connect_closed(move |_| {
        if let Some(w) = widget_weak.upgrade() {
            w.insert_action_group("ctx", None::<&gio::ActionGroup>);
        }
    });

    popover.popup();
}

pub fn show_select_dropdown(
    widget: &gtk::Widget,
    view: Rc<View>,
    x: f64,
    y: f64,
    min_width: i32,
    items: &[SelectEntry],
) {
    let list = gtk::ListBox::new();
    list.set_selection_mode(gtk::SelectionMode::None);
    list.set_activate_on_single_click(true);

    let mut row_ids: Vec<Option<u32>> = Vec::new();

    for item in items {
        match item {
            SelectEntry::Separator => {
                let row = gtk::ListBoxRow::new();
                row.set_selectable(false);
                row.set_activatable(false);
                row.set_child(Some(&gtk::Separator::new(gtk::Orientation::Horizontal)));
                list.append(&row);
                row_ids.push(None);
            }
            SelectEntry::GroupStart { label } => {
                let lbl = gtk::Label::new(Some(label));
                lbl.set_halign(gtk::Align::Start);
                lbl.add_css_class("caption-heading");
                let row = gtk::ListBoxRow::new();
                row.set_selectable(false);
                row.set_activatable(false);
                row.set_child(Some(&lbl));
                list.append(&row);
                row_ids.push(None);
            }
            SelectEntry::GroupEnd => {}
            SelectEntry::Item { id, label, selected, disabled } => {
                let lbl = gtk::Label::new(Some(label));
                lbl.set_halign(gtk::Align::Start);
                let row = gtk::ListBoxRow::new();
                if *selected { row.add_css_class("selected"); }
                row.set_sensitive(!disabled);
                row.set_activatable(!disabled);
                row.set_child(Some(&lbl));
                list.append(&row);
                row_ids.push(Some(*id));
            }
        }
    }

    let scrolled = gtk::ScrolledWindow::new();
    scrolled.set_child(Some(&list));
    scrolled.set_policy(gtk::PolicyType::Never, gtk::PolicyType::Automatic);
    scrolled.set_max_content_height(300);
    scrolled.set_propagate_natural_height(true);
    if min_width > 0 {
        scrolled.set_min_content_width(min_width);
    }

    let popover = gtk::Popover::new();
    popover.set_child(Some(&scrolled));
    popover.set_parent(widget);
    popover.set_has_arrow(false);
    let rect = gdk::Rectangle::new(x as i32, y as i32, 1, 1);
    popover.set_pointing_to(Some(&rect));

    let responded = Rc::new(Cell::new(false));
    let row_ids = Rc::new(row_ids);

    {
        let responded = responded.clone();
        let row_ids = row_ids.clone();
        let view_weak = Rc::downgrade(&view);
        let popover_weak = popover.downgrade();
        list.connect_row_activated(move |_, row| {
            let idx = row.index() as usize;
            let id = row_ids.get(idx).copied().flatten();
            if let Some(v) = view_weak.upgrade() {
                responded.set(true);
                v.select_dropdown_closed(id);
            }
            if let Some(p) = popover_weak.upgrade() {
                p.popdown();
            }
        });
    }

    {
        let view_weak = Rc::downgrade(&view);
        popover.connect_closed(move |_| {
            if !responded.get() {
                if let Some(v) = view_weak.upgrade() {
                    v.select_dropdown_closed(None);
                }
            }
        });
    }

    popover.popup();
}
