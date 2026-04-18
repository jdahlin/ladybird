/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

use std::env;
use std::error::Error;
use std::path::PathBuf;

fn main() -> Result<(), Box<dyn Error>> {
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR")?);
    let out_dir = PathBuf::from(env::var("OUT_DIR")?);

    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=cbindgen.toml");
    println!("cargo:rerun-if-env-changed=FFI_OUTPUT_DIR");
    println!("cargo:rerun-if-env-changed=LADYBIRD_BRIDGE_LIB");
    println!("cargo:rerun-if-env-changed=LADYBIRD_LINK_LIBS_FILE");
    println!("cargo:rerun-if-changed=src");

    link_cpp_bridge();

    let ffi_out_dir = env::var("FFI_OUTPUT_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| out_dir.clone());

    cbindgen::generate(manifest_dir).map_or_else(
        |error| match error {
            cbindgen::Error::ParseSyntaxError { .. } => {}
            e => panic!("{e:?}"),
        },
        |bindings| {
            let header_path = out_dir.join("LadybirdGtkFFI.h");
            bindings.write_to_file(&header_path);

            if ffi_out_dir != out_dir {
                bindings.write_to_file(ffi_out_dir.join("LadybirdGtkFFI.h"));
            }
        },
    );

    Ok(())
}

fn link_cpp_bridge() {
    let bridge_lib = env::var("LADYBIRD_BRIDGE_LIB").unwrap_or_default();
    if bridge_lib.is_empty() {
        return;
    }

    let bridge_path = PathBuf::from(&bridge_lib);
    if let Some(dir) = bridge_path.parent() {
        println!("cargo:rustc-link-search=native={}", dir.display());
    }
    println!("cargo:rustc-link-lib=static=ladybird_gtk_bridge");

    // Ladybird C++ libs listed in LADYBIRD_LINK_LIBS_FILE (one absolute path per line).
    if let Ok(libs_file) = env::var("LADYBIRD_LINK_LIBS_FILE") {
        if let Ok(content) = std::fs::read_to_string(&libs_file) {
            let mut dirs_emitted = std::collections::HashSet::new();
            for lib_path in content.lines().map(str::trim).filter(|l| !l.is_empty()) {
                let path = PathBuf::from(lib_path);
                if let Some(dir) = path.parent() {
                    if dirs_emitted.insert(dir.to_owned()) {
                        println!("cargo:rustc-link-search=native={}", dir.display());
                    }
                }
                if let Some(name) = path.file_stem().and_then(|s| s.to_str()) {
                    println!("cargo:rustc-link-lib=static={}", name.trim_start_matches("lib"));
                }
            }
        }
    }

    // C++ runtime and system libs needed by the bridge and Ladybird C++ libs.
    println!("cargo:rustc-link-lib=stdc++");
    println!("cargo:rustc-link-lib=ssl");
    println!("cargo:rustc-link-lib=crypto");

    // On Linux/BSD, allow duplicate symbols that arise when static libs bundle overlapping code.
    if env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("linux")
        || env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("freebsd")
        || env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("openbsd")
    {
        println!("cargo:rustc-link-arg=-Wl,--allow-multiple-definition");
    }
}
