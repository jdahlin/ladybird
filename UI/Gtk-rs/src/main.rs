/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#[cfg(any(target_os = "windows", target_os = "macos"))]
compile_error!("The GTK frontend is only supported on Linux");

fn main() {
    std::process::exit(ladybird_gtk::app::run());
}
