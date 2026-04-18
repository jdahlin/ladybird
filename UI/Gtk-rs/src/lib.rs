/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#![cfg(not(any(target_os = "windows", target_os = "macos")))]
#![allow(clippy::too_many_arguments)]

#[path = "../../../Libraries/RustAllocator.rs"]
mod rust_allocator;

pub mod app;
mod bridge;
mod dialogs;
mod menus;
mod tab;
mod web_view;
mod window;
