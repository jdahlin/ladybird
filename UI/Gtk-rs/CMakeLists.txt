find_package(PkgConfig REQUIRED)
pkg_check_modules(GTK4 REQUIRED IMPORTED_TARGET gtk4)
pkg_check_modules(LIBADWAITA REQUIRED IMPORTED_TARGET libadwaita-1>=1.4)

find_program(GLIB_COMPILE_RESOURCES NAMES glib-compile-resources REQUIRED)

set(GTK_UI_RESOURCES
    "${CMAKE_CURRENT_SOURCE_DIR}/Resources/browser-window.ui"
    "${CMAKE_CURRENT_SOURCE_DIR}/Resources/list-popover.ui"
    "${CMAKE_CURRENT_SOURCE_DIR}/Resources/location-entry.ui"
)

set(GTK_GRESOURCE_XML "${CMAKE_CURRENT_SOURCE_DIR}/Resources/resources.gresource.xml")
set(GTK_GRESOURCE_SOURCE "${CMAKE_CURRENT_BINARY_DIR}/gtk-ui-resources.cpp")

add_custom_command(
    OUTPUT "${GTK_GRESOURCE_SOURCE}"
    DEPENDS "${GTK_GRESOURCE_XML}" ${GTK_UI_RESOURCES}
    COMMAND "${GLIB_COMPILE_RESOURCES}"
        "${GTK_GRESOURCE_XML}"
        --sourcedir "${CMAKE_CURRENT_SOURCE_DIR}/Resources"
        --target "${GTK_GRESOURCE_SOURCE}"
        --generate-source
    VERBATIM
)
add_custom_target(gtk_rs_ui_resources DEPENDS "${GTK_GRESOURCE_SOURCE}")

# C++ bridge compiled as a static library so cargo can link it into the Rust binary.
add_library(ladybird_gtk_bridge STATIC
    Bridge.cpp
    ../Gtk/Events.cpp
    "${GTK_GRESOURCE_SOURCE}"
)
add_dependencies(ladybird_gtk_bridge gtk_rs_ui_resources)
target_link_libraries(ladybird_gtk_bridge PRIVATE PkgConfig::GTK4 PkgConfig::LIBADWAITA)

import_rust_binary(
    MANIFEST_PATH Cargo.toml
    BIN_NAME ladybird
    FFI_HEADER LadybirdGtkFFI.h
    BRIDGE_LIB ladybird_gtk_bridge
)

create_ladybird_bundle(ladybird)
