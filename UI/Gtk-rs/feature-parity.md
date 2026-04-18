# GTK-rs Feature Parity Checklist

This checklist is for bringing the Rust GTK frontend to functional parity with
the C++ GTK frontend after the unsafe/FFI boundary cleanup is in better shape.

The goal is not to write C++ in Rust. The goal is to keep the port reviewable
against the C++ frontend while using gtk-rs-native APIs where they are the
natural equivalent.

## Porting Principle

Keep the same conceptual object boundaries:

- `Application.cpp/.h` -> `src/app.rs`
- `BrowserWindow.cpp/.h` -> `src/window.rs`
- `Tab.cpp/.h` -> `src/tab.rs`
- `Widgets/LadybirdLocationEntry.*` -> `src/location_entry.rs`
- `Menu.cpp/.h` -> `src/menus.rs`
- `Dialogs.cpp/.h` -> `src/dialogs.rs`
- `WebContentView` interaction -> `src/bridge.rs` plus `Bridge.cpp`

Keep method names and responsibilities close to C++ where practical.

Use gtk-rs-native APIs inside those methods:

- `glib::Object` subclassing
- `CompositeTemplate`
- `connect_*` signal helpers
- `gio::SimpleAction` / action entries
- `gtk::ShortcutController`
- `glib::WeakRef`
- RAII cleanup patterns

Avoid broad abstractions until parity is reached. FFI may differ structurally,
but user-visible behavior should map back to the C++ implementation.

## Global Review Rules

- For every C++ method, identify the Rust counterpart or add a `TODO(parity)` in
  the matching Rust module.
- Prefer small helper methods that correspond to visible C++ blocks.
- Avoid moving behavior across object boundaries just because Rust makes it
  convenient.
- Keep raw FFI structs and raw pointers out of UI modules except at the narrow
  callback boundary.
- Safe Rust APIs must not accept raw pointers.
- If behavior cannot be implemented yet because bridge support is missing, add a
  focused bridge TODO and keep the call site in the matching Rust method.

## Application Parity

Compare `../Gtk/Application.cpp` with `src/app.rs`.

- Match application ID and `GApplication` behavior intentionally.
- Decide whether Rust should use `G_APPLICATION_HANDLES_OPEN` parity instead of
  `NON_UNIQUE`.
- Implement or explicitly defer single-instance behavior:
  - remote instance forwarding
  - `open` signal
  - `activate` signal
  - active window reuse
- Track active window like C++:
  - update active window on `notify::is-active`
  - remove windows on destroy
  - quit event loop when last window closes
- Implement `on_open(urls)` behavior:
  - open URLs in active window if present
  - first opened URL activates, later URLs open in background
  - create a new window if no active window exists
- Implement `on_activate()` parity:
  - present active window
  - create new window when none exists
- Match `active_tab()` and `active_web_view()` behavior through bridge callbacks
  where needed.
- Implement `open_blank_new_tab(activate_tab)` equivalent if WebView application
  code needs it.
- Implement download save path parity:
  - parent to active window
  - default Downloads folder
  - initial filename
  - nested event loop or async bridge-compatible result
- Implement download confirmation toast parity.
- Implement error dialog parity.
- Implement clipboard parity:
  - `clipboard_text`
  - `clipboard_entries`
  - `insert_clipboard_entry`
  - at least `text/plain`
- Implement devtools broadcast:
  - `on_devtools_enabled`
  - `on_devtools_disabled`
  - update all windows, not just one banner
- Leave bookmarks functions as explicit stubs if still no-op in C++.

## BrowserWindow Parity

Compare `../Gtk/BrowserWindow.cpp` with `src/window.rs`.

- Keep `BrowserWindow::new` flow aligned:
  - setup UI
  - setup keyboard shortcuts
  - create initial tabs
- Split Rust `setup_ui` into reviewable helpers matching C++ blocks:
  - `connect_find_entry_signals`
  - `register_actions`
  - `connect_tab_view_signals`
  - `setup_location_entry`
  - `setup_developer_tools_menu`
  - `connect_fullscreen_state`
  - `install_action_accelerators`
  - `initialize_devtools_banner`
- Replace plain `GtkEntry` location entry with custom `LocationEntry`.
- Match initial tab behavior:
  - if no URLs, open configured new tab page, not hardcoded `about:blank`, unless
    bridge support is missing
  - if URLs are provided, first tab is active and the rest open in background
- Implement `create_new_tab(activate_tab)` equivalent.
- Implement `create_new_tab(url, activate_tab)` equivalent.
- Implement `create_child_tab(activate_tab, parent, page_index)` equivalent.
- Fix popup/new web view handling to respect `page_index`.
- Match tab page title initialization: `"New Tab"` before title callback.
- Match internal URL handling:
  - `about:blank`, `about:newtab`, and empty scheme should clear location entry
  - new internal tab should focus and select the location entry
- Implement `current_tab()` helper, not just `current_tab_state()`.
- Implement `view()` helper returning the current `View`.
- Match close behavior:
  - close current tab
  - finish close request
  - remove tab
  - close window if last tab
- Match selected tab behavior:
  - update location entry
  - bind navigation actions
  - update zoom label
- Implement navigation action binding:
  - back/forward actions disabled initially
  - bind to current view enabled state
  - update when selected tab changes
- Match `update_navigation_buttons` if bridge exposes back/forward state
  directly.
- Match `update_location_entry`:
  - empty/internal -> plain empty text and no icon
  - normal URL -> `LocationEntry::set_url`
- Match `show_find_bar`:
  - reveal bar
  - focus find entry
- Match `hide_find_bar`:
  - hide bar
  - return focus to current web view
- Match find result text:
  - C++ uses `"{} of {} matches"` with `current + 1`
  - no total means `"No matches"`
- Match `update_zoom_label`:
  - integer percent
  - enable `zoom-reset` only when zoom is not `1.0`
- Match fullscreen state UI:
  - hide header bar title buttons in fullscreen
  - show restore button in fullscreen
  - hide restore button otherwise
- Match devtools banner wording and behavior.
- Ensure `Preferences`, `About`, and `Reload` use WebView application actions
  through bridge when possible, not ad hoc behavior.

## Actions And Shortcuts

Compare `BrowserWindow::register_actions`, `setup_keyboard_shortcuts`, and
`Menu.cpp`.

- Use gtk-rs-native `gio::SimpleAction` or action entries, but match action
  names.
- Required window actions:
  - `new-tab`
  - `new-window`
  - `close-tab`
  - `focus-location`
  - `go-back`
  - `go-forward`
  - `reload`
  - `zoom-in`
  - `zoom-out`
  - `zoom-reset`
  - `find`
  - `find-close`
  - `find-next`
  - `find-previous`
  - `quit`
  - `fullscreen`
  - `preferences`
  - `about`
- Initial enabled state must match C++:
  - `go-back`: disabled
  - `go-forward`: disabled
  - `zoom-reset`: disabled at 100%
- Required accelerators:
  - `<Ctrl>t` new tab
  - `<Ctrl>w` close tab
  - `<Ctrl>l` focus location
  - `<Ctrl>f` find
  - `Escape` find close
  - `<Alt>Left` back
  - `<Alt>Right` forward
  - `<Ctrl>equal`, `<Ctrl>plus` zoom in
  - `<Ctrl>minus` zoom out
  - `<Ctrl>0` zoom reset
  - `F11` fullscreen
  - `<Ctrl>q` quit
  - `<Ctrl>n` new window
  - reload: `<Ctrl>r`, `F5`
  - devtools: `<Ctrl><Shift>i`, `<Ctrl><Shift>c`, `F12`
- Ensure accelerators are installed at application/window level so focused web
  content does not swallow them incorrectly.
- Add inspect/debug menu accelerators when those menus are bridged.

## LocationEntry Parity

Add `src/location_entry.rs`, corresponding to
`Widgets/LadybirdLocationEntry.*`.

- Implement as gtk-rs custom widget/subclass wrapping `gtk::Entry`.
- Public API should mirror C++:
  - `new`
  - `set_url`
  - `set_text`
  - `set_security_icon`
  - `focus_and_select_all`
  - `connect_navigate` or equivalent callback setter
- Replace `GtkEntry` template child with custom `LocationEntry`, or set it as
  the header title widget in setup.
- Match placeholder behavior:
  - if search engine exists: `"Search with {engine} or enter URL"`
  - otherwise: `"Enter URL or search..."`
- Match navigation sanitization:
  - use WebView `sanitize_url` via bridge, not Rust's crude normalizer
  - search engine behavior should match C++
- Match security icon:
  - `https` -> `channel-secure-symbolic`, tooltip `"Secure connection"`
  - `http` and other external schemes -> `channel-insecure-symbolic`, tooltip
    `"Insecure connection"`
  - `file`, `resource`, `about`, `data`, or no scheme -> no icon
- Match display attributes when unfocused:
  - dim full URL
  - highlight effective TLD+1
  - medium weight on effective TLD+1
  - use `WebView::break_url_into_parts` via bridge, or add TODO if bridge is
    missing
- Focus behavior:
  - focused entry clears display attributes
  - leaving focus restores display attributes
  - `focus_and_select_all` selects all text
- Autocomplete behavior:
  - keep user text
  - query autocomplete on user edits only
  - suppress callbacks when programmatically changing text
  - show popover only while focused and suggestions exist
  - row activation navigates selected suggestion
  - Down/Up cycles selected suggestion, including `-1` original-text state
  - Escape hides completions and restores user text
- Popover behavior:
  - load/use `location-entry.ui`
  - width follows entry width
  - rows ellipsize
  - cleanup/unparent on finalize/drop

## Tab Parity

Compare `../Gtk/Tab.cpp` with `src/tab.rs`.

- Keep a `setup_callbacks`-style method or equivalent grouping.
- Title callback:
  - update tab page title only
  - ensure title fallback remains `"New Tab"`
- URL callback:
  - only update window location entry for current tab
  - internal URLs clear location entry
  - normal URLs call `BrowserWindow::update_location_entry`
- Load callbacks:
  - update tab page loading state
- Cursor callback:
  - map image cursor to default
  - set cursor on web view widget
- Tooltip/link hover callbacks:
  - set tooltip text on web view widget
  - clear on unhover/leave
- New web view callback:
  - if `page_index >= 0`, create child tab using parent client/page index
  - otherwise create normal new tab
  - return new view handle
- Activate tab:
  - focus web view widget
- Close:
  - close this tab through `BrowserWindow::close_tab`
- Zoom changed:
  - update zoom label
- Dialog callbacks:
  - alert
  - confirm
  - prompt
  - color picker
  - file picker
- Select dropdown:
  - match layout and behavior from C++
  - selected rows show checkmark icon with spacer for unselected rows
  - option group indentation
  - disabled rows insensitive
  - close without selection sends `None`
- Find callback:
  - update find result with same formatting as C++
- Window callbacks:
  - fullscreen
  - exit fullscreen
  - restore
  - maximize
  - minimize
  - resize
  - reposition intentionally unsupported with comment
- Favicon callback:
  - set tab page icon
  - ensure bytes lifetime is correct
- Audio play state:
  - show `audio-volume-high-symbolic` while playing
  - clear icon otherwise
- Context menus:
  - include page, link, image, and media context menus
  - Rust bridge currently wires page/link/image only: add media
- Keep WebDriver prompt TODO if still missing upstream support.

## Input Event Parity

Compare `../Gtk/Events.cpp`, `Bridge.cpp`, and Rust input handling.

- Mouse button mapping:
  - primary/middle/secondary/backward/forward
  - default behavior should match C++
- Mouse buttons state mapping:
  - include backward/forward if available
- Modifier mapping:
  - Shift
  - Ctrl
  - Alt
  - Super
- Key mapping:
  - Rust currently sends raw GDK keyval to bridge and C++ maps there; verify
    parity with `Events.cpp`
  - include code point behavior
  - repeat behavior if supported later
- Scroll behavior:
  - match C++ scaling:
    - surface unit uses raw delta times DPR
    - non-surface uses `3 lines * 24px * DPR`
  - Ctrl+scroll zoom handling should happen exactly once, not both Rust and C++
- Mouse wheel position:
  - C++ bridge currently sends empty wheel position; verify this matches old
    frontend behavior or fix both.
- Keyboard post-processing:
  - Ctrl+C / Ctrl+V / Ctrl+A should activate app copy/paste/select-all actions
    as C++ does in `finish_handling_key_event`.

## Menu Parity

Compare `../Gtk/Menu.cpp` with `src/menus.rs`.

- Preserve module boundary: menu code stays in `menus.rs`.
- Context menus should support:
  - visible action filtering
  - enabled state
  - checkable state
  - checked state
  - submenus
  - separators/sections
  - icons
  - accelerators
- Rust currently serializes submenus into section markers; assess if this loses
  nested menu behavior. If yes, extend FFI model to represent submenus.
- Add media context menu bridge.
- Match action activation:
  - checkable actions toggle checked state before activation
  - disabled actions cannot activate
- Match native icon mapping from `initialize_native_control`.
- Match accelerator display for menu items.
- Developer tools menu:
  - inspect menu
  - debug menu
  - inserted after/near New Window like C++
- Action observers:
  - enabled state changes
  - checked state changes
  - engaged state where relevant, such as bookmark icons

## Dialogs Parity

Compare `../Gtk/Dialogs.cpp` with `src/dialogs.rs`.

- Alert:
  - title/response labels match C++
- Confirm:
  - default/close responses match C++
- Prompt:
  - entry initialized with default
  - OK returns entry text
  - Cancel closes with no response
- Color picker:
  - RGBA packing/unpacking matches C++
  - alpha enabled
  - cancellation behavior matches C++
- File picker:
  - accepted filters map exactly
  - multiple selection behavior
  - cancellation sends empty selection
- Error dialog:
  - currently app-level in C++; add Rust equivalent if missing
- Download save dialog:
  - app-level, not page file picker
  - default Downloads folder
  - initial filename

## Bridge/FFI Parity

Keep this as the only intentionally non-1:1 layer.

- Add bridge methods only where a matching C++ behavior needs WebView internals.
- Likely bridge additions:
  - settings new tab URL
  - settings search engine placeholder info
  - `sanitize_url`
  - `break_url_into_parts`
  - autocomplete query support
  - navigation back/forward enabled state or action observer callbacks
  - inspect/debug menu serialization
  - action icon/accelerator metadata or action ID exposure
  - media context menu serialization
  - child tab creation using parent client/page index
  - active web view / open blank tab hooks for application callbacks
  - clipboard operations if retained in C++
- Raw FFI callbacks should only decode data and call safe Rust methods.
- Keep FFI raw structs private or in `bridge::raw`.
- Public Rust-facing types should be owned and safe.

## Resource/UI Files

- Keep `browser-window.ui` close to the C++ resource layout.
- If Rust uses a custom `LocationEntry` template child, make the resource diff
  intentional and minimal.
- Reuse `location-entry.ui` for autocomplete popover.
- Reuse `list-popover.ui` for select dropdown if practical.
- Ensure hamburger popover custom slots still work:
  - zoom controls
  - tools controls
- Confirm missing icons are not resource-path or action-model problems.

## Known Current Delta Hotspots

Use these as first tickets after unsafe cleanup lands.

- Plain `GtkEntry` should become `LocationEntry`.
- Rust URL normalization should be replaced by WebView sanitization/search engine
  behavior.
- Internal URL display is wrong/incomplete.
- Back/forward actions do not track enabled state.
- Zoom reset action does not track enabled state.
- Find result text differs.
- Find close does not return focus to web view.
- Popup child-tab `page_index` is ignored.
- Media context menu is missing.
- Context menu icons/accelerators/checkable state are incomplete.
- Developer tools inspect/debug menus are missing.
- App single-instance/open handling differs.
- Clipboard implementation is missing.
- Error dialog implementation is missing.
- Active window/active web view plumbing is incomplete.
- Location entry security icons/domain highlighting/autocomplete are missing.

## Definition Of Done For Each Ported Area

- The Rust method has a clear C++ counterpart.
- Behavior differences are either eliminated or documented as `TODO(parity): ...`.
- gtk-rs APIs are used naturally, but object boundaries remain C++-reviewable.
- No raw FFI pointer type leaks into normal UI code.
- Manual smoke testing covers the behavior using `../Gtk/test.html` where
  applicable.
- If visual behavior changed, include a short note describing C++ behavior and
  Rust behavior.
