/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

use crate::bridge::View;
use gtk::glib;
use gtk::prelude::*;
use gtk::subclass::prelude::*;
use std::cell::{Cell, RefCell};
use std::rc::Rc;

glib::wrapper! {
    pub struct WebView(ObjectSubclass<imp::WebView>)
        @extends gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl WebView {
    pub fn new() -> Self {
        glib::Object::new()
    }

    pub fn attach_view(&self, view: Rc<View>) {
        self.imp().view.replace(Some(view));
    }

    pub fn detach_view(&self) {
        self.imp().view.replace(None);
    }
}

impl Default for WebView {
    fn default() -> Self {
        Self::new()
    }
}

mod imp {
    use super::*;

    pub struct WebView {
        pub view: RefCell<Option<Rc<View>>>,
        pub allocated_size: Cell<(i32, i32)>,
    }

    impl Default for WebView {
        fn default() -> Self {
            Self {
                view: RefCell::new(None),
                allocated_size: Cell::new((0, 0)),
            }
        }
    }

    #[glib::object_subclass]
    impl ObjectSubclass for WebView {
        const NAME: &'static str = "LadybirdRsWebView";
        type Type = super::WebView;
        type ParentType = gtk::Widget;

        fn class_init(klass: &mut Self::Class) {
            klass.set_css_name("ladybird-web-view");
        }
    }

    impl ObjectImpl for WebView {
        fn constructed(&self) {
            self.parent_constructed();

            let obj = self.obj();
            obj.set_focusable(true);
            obj.set_hexpand(true);
            obj.set_vexpand(true);
        }
    }

    impl WidgetImpl for WebView {
        fn measure(&self, orientation: gtk::Orientation, _for_size: i32) -> (i32, i32, i32, i32) {
            let natural = match orientation {
                gtk::Orientation::Horizontal => 800,
                gtk::Orientation::Vertical => 600,
                _ => 600,
            };

            (100, natural, -1, -1)
        }

        fn size_allocate(&self, width: i32, height: i32, _baseline: i32) {
            self.allocated_size.set((width, height));

            if let Some(view) = self.view.borrow().as_ref() {
                view.set_device_pixel_ratio(self.obj().scale_factor() as f64);
                view.set_viewport_size(width, height);
            }
        }

        fn snapshot(&self, snapshot: &gtk::Snapshot) {
            let (width, height) = self.allocated_size.get();
            if width == 0 || height == 0 {
                return;
            }

            if let Some(view) = self.view.borrow().as_ref() {
                view.paint(snapshot, width, height);
            }
        }
    }
}
