/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/ByteString.h>
#include <AK/String.h>
#include <AK/StringView.h>
#include <AK/Utf16String.h>
#include <LibCore/Event.h>
#include <LibCore/EventLoop.h>
#include <LibCore/EventLoopImplementation.h>
#include <LibCore/EventReceiver.h>
#include <LibCore/Notifier.h>
#include <LibCore/Resource.h>
#include <LibCore/System.h>
#include <LibCore/ThreadEventQueue.h>
#include <LibGfx/Bitmap.h>
#include <LibGfx/Palette.h>
#include <LibGfx/SharedImageBuffer.h>
#include <LibGfx/SystemTheme.h>
#include <LibMain/Main.h>
#include <LibThreading/Mutex.h>
#include <LibURL/Parser.h>
#include <LibURL/URL.h>
#include <LibWeb/Page/InputEvent.h>
#include <LibWebView/Application.h>
#include <LibWebView/Menu.h>
#include <LibWebView/ViewImplementation.h>
#include <LibWebView/WebContentClient.h>
#include <UI/Gtk-rs/Bridge.h>
#include <UI/Gtk/Events.h>

#include <AK/LexicalPath.h>
#include <LibWeb/HTML/FileFilter.h>
#include <LibWeb/HTML/SelectItem.h>
#include <LibWeb/HTML/SelectedFile.h>
#include <LibWebView/Menu.h>
#include <string.h>

#include <adwaita.h>
#include <gdk/gdk.h>
#include <glib-unix.h>
#include <glib.h>
#include <gtk/gtk.h>

namespace LadybirdRs {

class GlibEventLoopImpl final : public Core::EventLoopImplementation {
public:
    static NonnullOwnPtr<GlibEventLoopImpl> create() { return adopt_own(*new GlibEventLoopImpl); }

    GlibEventLoopImpl()
        : m_loop(g_main_loop_new(nullptr, FALSE))
    {
    }

    ~GlibEventLoopImpl() override
    {
        g_clear_pointer(&m_loop, g_main_loop_unref);
    }

    int exec() override
    {
        g_main_loop_run(m_loop);
        return m_exit_code;
    }

    size_t pump(PumpMode mode) override
    {
        auto result = Core::ThreadEventQueue::current().process();
        auto may_block = mode == PumpMode::WaitForEvents ? TRUE : FALSE;
        g_main_context_iteration(g_main_loop_get_context(m_loop), may_block);
        result += Core::ThreadEventQueue::current().process();
        return result;
    }

    void quit(int code) override
    {
        m_exit_code = code;
        m_exit_requested = true;
        if (m_loop && g_main_loop_is_running(m_loop))
            g_main_loop_quit(m_loop);
    }

    void wake() override
    {
        g_main_context_wakeup(g_main_loop_get_context(m_loop));
    }

    bool was_exit_requested() const override
    {
        return m_exit_requested;
    }

private:
    GMainLoop* m_loop { nullptr };
    bool m_exit_requested { false };
    int m_exit_code { 0 };
};

class GlibEventLoopManager final : public Core::EventLoopManager {
public:
    NonnullOwnPtr<Core::EventLoopImplementation> make_implementation() override
    {
        return GlibEventLoopImpl::create();
    }

    intptr_t register_timer(Core::EventReceiver& object, int milliseconds, bool should_reload) override
    {
        struct TimerData {
            WeakPtr<Core::EventReceiver> weak_object;
            bool should_reload;
        };

        auto* data = new TimerData { object.make_weak_ptr(), should_reload };
        return g_timeout_add_full(
            G_PRIORITY_DEFAULT,
            milliseconds,
            [](gpointer user_data) -> gboolean {
                auto* data = static_cast<TimerData*>(user_data);
                auto object = data->weak_object.strong_ref();
                if (!object)
                    return G_SOURCE_REMOVE;
                Core::TimerEvent event;
                object->dispatch_event(event);
                return data->should_reload ? G_SOURCE_CONTINUE : G_SOURCE_REMOVE;
            },
            data,
            [](gpointer user_data) {
                delete static_cast<TimerData*>(user_data);
            });
    }

    void unregister_timer(intptr_t timer_id) override
    {
        g_source_remove(static_cast<guint>(timer_id));
    }

    void register_notifier(Core::Notifier& notifier) override
    {
        GIOCondition condition {};
        switch (notifier.type()) {
        case Core::Notifier::Type::Read:
            condition = G_IO_IN;
            break;
        case Core::Notifier::Type::Write:
            condition = G_IO_OUT;
            break;
        default:
            VERIFY_NOT_REACHED();
        }

        auto* weak_notifier = new WeakPtr<Core::EventReceiver>(notifier.make_weak_ptr());
        auto source_id = g_unix_fd_add_full(
            G_PRIORITY_DEFAULT,
            notifier.fd(),
            condition,
            [](gint, GIOCondition, gpointer user_data) -> gboolean {
                auto notifier = static_cast<WeakPtr<Core::EventReceiver>*>(user_data)->strong_ref();
                if (!notifier)
                    return G_SOURCE_REMOVE;
                Core::NotifierActivationEvent event;
                notifier->dispatch_event(event);
                return G_SOURCE_CONTINUE;
            },
            weak_notifier,
            [](gpointer user_data) {
                delete static_cast<WeakPtr<Core::EventReceiver>*>(user_data);
            });

        Threading::MutexLocker locker(m_notifiers_mutex);
        m_notifiers.set(&notifier, source_id);
    }

    void unregister_notifier(Core::Notifier& notifier) override
    {
        Threading::MutexLocker locker(m_notifiers_mutex);
        auto it = m_notifiers.find(&notifier);
        if (it == m_notifiers.end())
            return;
        g_source_remove(it->value);
        m_notifiers.remove(it);
    }

    void did_post_event() override
    {
        if (m_idle_pending)
            return;

        m_idle_pending = true;
        g_idle_add_once(
            [](gpointer user_data) {
                auto& self = *static_cast<GlibEventLoopManager*>(user_data);
                self.m_idle_pending = false;
                Core::ThreadEventQueue::current().process();
            },
            this);
    }

    int register_signal(int, Function<void(int)>) override { return 0; }
    void unregister_signal(int) override { }

private:
    HashMap<Core::Notifier*, guint> m_notifiers;
    Threading::Mutex m_notifiers_mutex;
    bool m_idle_pending { false };
};

class RsApplication final : public WebView::Application {
    WEB_VIEW_APPLICATION(RsApplication)

public:
    ~RsApplication() override = default;

    void set_toast_overlay(GtkWidget* widget) { m_toast_overlay = widget; }

private:
    RsApplication() = default;

    NonnullOwnPtr<Core::EventLoop> create_platform_event_loop() override
    {
        if (!browser_options().headless_mode.has_value())
            Core::EventLoopManager::install(*new GlibEventLoopManager);
        return WebView::Application::create_platform_event_loop();
    }

    Optional<WebView::ViewImplementation&> active_web_view() const override { return {}; }
    Optional<WebView::ViewImplementation&> open_blank_new_tab(Web::HTML::ActivateTab) const override { return {}; }

    Optional<ByteString> ask_user_for_download_path(StringView file) const override
    {
        if (!m_toast_overlay)
            return {};

        GObjectPtr dialog { gtk_file_dialog_new() };
        gtk_file_dialog_set_title(GTK_FILE_DIALOG(dialog.ptr()), "Save As");

        auto const* downloads_dir = g_get_user_special_dir(G_USER_DIRECTORY_DOWNLOAD);
        if (downloads_dir) {
            GObjectPtr initial_folder { g_file_new_for_path(downloads_dir) };
            gtk_file_dialog_set_initial_folder(GTK_FILE_DIALOG(dialog.ptr()), G_FILE(initial_folder.ptr()));
        }
        gtk_file_dialog_set_initial_name(GTK_FILE_DIALOG(dialog.ptr()), ByteString(file).characters());

        Optional<ByteString> result;
        Core::EventLoop nested_loop;

        struct SaveState {
            Optional<ByteString>* result;
            Core::EventLoop* loop;
        };
        auto* state = new SaveState { &result, &nested_loop };

        auto* parent_window = gtk_widget_get_ancestor(m_toast_overlay, GTK_TYPE_WINDOW);
        gtk_file_dialog_save(GTK_FILE_DIALOG(dialog.ptr()), GTK_WINDOW(parent_window), nullptr, +[](GObject* source, GAsyncResult* async_result, gpointer user_data) {
                auto* s = static_cast<SaveState*>(user_data);
                GError* error = nullptr;
                GObjectPtr file { gtk_file_dialog_save_finish(GTK_FILE_DIALOG(source), async_result, &error) };
                if (file.ptr()) {
                    g_autofree char* path = g_file_get_path(G_FILE(file.ptr()));
                    if (path)
                        *s->result = ByteString(path);
                }
                if (error)
                    g_error_free(error);
                s->loop->quit(0);
                delete s; }, state);

        nested_loop.exec();
        return result;
    }

    void display_download_confirmation_dialog(StringView download_name, LexicalPath const& path) const override
    {
        if (!m_toast_overlay)
            return;
        auto message = ByteString::formatted("{} saved to {}", download_name, path.dirname());
        auto* toast = adw_toast_new(message.characters());
        adw_toast_set_timeout(toast, 5);
        adw_toast_overlay_add_toast(ADW_TOAST_OVERLAY(m_toast_overlay), toast);
    }

    void display_error_dialog(StringView) const override { }
    Utf16String clipboard_text() const override { return {}; }
    Vector<Web::Clipboard::SystemClipboardRepresentation> clipboard_entries() const override { return {}; }
    void insert_clipboard_entry(Web::Clipboard::SystemClipboardRepresentation) override { }
    bool should_capture_web_content_output() const override { return false; }
    void rebuild_bookmarks_menu() const override { }
    void update_bookmarks_bar_display(bool) const override { }
    void on_devtools_enabled() const override { }
    void on_devtools_disabled() const override { }

    GtkWidget* m_toast_overlay { nullptr };
};

static void serialize_menu(
    WebView::Menu& menu,
    Vector<LadybirdContextMenuItem>& out,
    Vector<WeakPtr<WebView::Action>>& actions)
{
    for (auto& item : menu.items()) {
        item.visit(
            [&](NonnullRefPtr<WebView::Action> const& action) {
                int id = static_cast<int>(actions.size());
                actions.append(action->make_weak_ptr());
                auto text = action->text();
                out.append(LadybirdContextMenuItem {
                    .id = id,
                    .is_separator = false,
                    .is_section_start = false,
                    .is_section_end = false,
                    .label = text.characters_without_null_termination(),
                    .label_len = text.length(),
                    .enabled = action->enabled() && action->visible(),
                    .checkable = action->is_checkable(),
                    .checked = action->checked(),
                });
            },
            [&](NonnullRefPtr<WebView::Menu> const& submenu) {
                auto title = submenu->title();
                out.append(LadybirdContextMenuItem {
                    .id = -1,
                    .is_section_start = true,
                    .label = title.characters_without_null_termination(),
                    .label_len = title.length(),
                });
                serialize_menu(*submenu, out, actions);
                out.append(LadybirdContextMenuItem { .id = -1, .is_section_end = true });
            },
            [&](WebView::Separator const&) {
                out.append(LadybirdContextMenuItem { .id = -1, .is_separator = true });
            });
    }
}

class RsView final : public WebView::ViewImplementation {
public:
    RsView(uintptr_t rust_context, LadybirdViewCallbacks const& callbacks)
        : m_rust_context(rust_context)
        , m_callbacks(callbacks)
    {
        wire_callbacks();
        initialize_client(CreateNewClient::Yes);
    }

    ~RsView() override
    {
        if (m_cached_texture)
            g_object_unref(m_cached_texture);
    }

    Web::DevicePixelSize viewport_size() const override
    {
        return { m_viewport_size.width(), m_viewport_size.height() };
    }

    Gfx::IntPoint to_content_position(Gfx::IntPoint widget_position) const override
    {
        return widget_position;
    }

    Gfx::IntPoint to_widget_position(Gfx::IntPoint content_position) const override
    {
        return content_position;
    }

    void set_viewport_size(int width, int height)
    {
        m_viewport_size = {
            static_cast<int>(width * m_device_pixel_ratio),
            static_cast<int>(height * m_device_pixel_ratio)
        };
        handle_resize();
    }

    void set_device_pixel_ratio(double device_pixel_ratio)
    {
        m_device_pixel_ratio = device_pixel_ratio;
    }

    void set_system_visibility(bool visible)
    {
        set_system_visibility_state(visible ? Web::HTML::VisibilityState::Visible : Web::HTML::VisibilityState::Hidden);
    }

    void set_has_focus(bool has_focus)
    {
        client().async_set_has_focus(page_id(), has_focus);
    }

    void load_url(char const* url, size_t len)
    {
        auto parsed_url = URL::Parser::basic_parse(StringView { url, len });
        if (parsed_url.has_value())
            load(parsed_url.release_value());
    }

    void dispatch_mouse_event(int type, double x, double y, unsigned button, GdkModifierType state, int click_count)
    {
        Web::MouseEvent::Type mouse_type {};
        switch (type) {
        case LADYBIRD_MOUSE_DOWN:
            mouse_type = Web::MouseEvent::Type::MouseDown;
            break;
        case LADYBIRD_MOUSE_UP:
            mouse_type = Web::MouseEvent::Type::MouseUp;
            break;
        case LADYBIRD_MOUSE_MOVE:
            mouse_type = Web::MouseEvent::Type::MouseMove;
            break;
        case LADYBIRD_MOUSE_LEAVE:
            mouse_type = Web::MouseEvent::Type::MouseLeave;
            break;
        default:
            return;
        }

        auto device_pixel_ratio = m_device_pixel_ratio;
        auto position = Web::DevicePixelPoint {
            static_cast<int>(x * device_pixel_ratio),
            static_cast<int>(y * device_pixel_ratio)
        };
        auto web_button = Ladybird::gdk_button_to_web(button);
        auto modifiers = Ladybird::gdk_modifier_to_web(state);

        Web::UIEvents::MouseButton buttons;
        switch (mouse_type) {
        case Web::MouseEvent::Type::MouseDown:
            buttons = web_button;
            break;
        case Web::MouseEvent::Type::MouseMove:
            buttons = Ladybird::gdk_buttons_to_web(state);
            break;
        default:
            buttons = Web::UIEvents::MouseButton::None;
            break;
        }

        Web::MouseEvent event {
            .type = mouse_type,
            .position = position,
            .screen_position = position,
            .button = web_button,
            .buttons = buttons,
            .modifiers = modifiers,
            .wheel_delta_x = 0,
            .wheel_delta_y = 0,
            .click_count = click_count,
            .browser_data = {},
        };
        enqueue_input_event(move(event));
    }

    void dispatch_scroll_event(double dx, double dy, GdkModifierType state, int unit)
    {
        if (state & GDK_CONTROL_MASK) {
            if (dy < 0)
                zoom_in();
            else if (dy > 0)
                zoom_out();
            return;
        }

        auto device_pixel_ratio = m_device_pixel_ratio;

        int wheel_delta_x = 0;
        int wheel_delta_y = 0;

        if (unit == GDK_SCROLL_UNIT_SURFACE) {
            wheel_delta_x = static_cast<int>(dx * device_pixel_ratio);
            wheel_delta_y = static_cast<int>(dy * device_pixel_ratio);
        } else {
            static constexpr double scroll_lines = 3.0;
            static constexpr double scroll_step_size = 24.0;
            wheel_delta_x = static_cast<int>(dx * scroll_lines * scroll_step_size * device_pixel_ratio);
            wheel_delta_y = static_cast<int>(dy * scroll_lines * scroll_step_size * device_pixel_ratio);
        }

        Web::MouseEvent event {
            .type = Web::MouseEvent::Type::MouseWheel,
            .position = {},
            .screen_position = {},
            .button = Web::UIEvents::MouseButton::None,
            .buttons = Web::UIEvents::MouseButton::None,
            .modifiers = Ladybird::gdk_modifier_to_web(state),
            .wheel_delta_x = wheel_delta_x,
            .wheel_delta_y = wheel_delta_y,
            .click_count = 0,
            .browser_data = {},
        };
        enqueue_input_event(move(event));
    }

    void dispatch_key_event(int type, unsigned keyval, GdkModifierType state)
    {
        Web::KeyEvent::Type key_type;
        switch (type) {
        case LADYBIRD_KEY_DOWN:
            key_type = Web::KeyEvent::Type::KeyDown;
            break;
        case LADYBIRD_KEY_UP:
            key_type = Web::KeyEvent::Type::KeyUp;
            break;
        default:
            return;
        }

        Web::KeyEvent event {
            .type = key_type,
            .key = Ladybird::gdk_keyval_to_web(keyval),
            .modifiers = Ladybird::gdk_modifier_to_web(state),
            .code_point = gdk_keyval_to_unicode(keyval),
            .repeat = false,
            .browser_data = {},
        };
        enqueue_input_event(move(event));
    }

    void navigate_back() { traverse_the_history_by_delta(-1); }
    void navigate_forward() { traverse_the_history_by_delta(1); }

    void alert_closed() { ViewImplementation::alert_closed(); }
    void confirm_closed(bool accepted) { ViewImplementation::confirm_closed(accepted); }
    void prompt_closed(char const* response, size_t len, bool cancelled)
    {
        if (cancelled)
            ViewImplementation::prompt_closed({});
        else
            ViewImplementation::prompt_closed(MUST(String::from_utf8(StringView { response, len })));
    }
    void color_picker_update(uint32_t rgba, bool released)
    {
        auto state = released ? Web::HTML::ColorPickerUpdateState::Closed : Web::HTML::ColorPickerUpdateState::Update;
        Color color(
            static_cast<u8>(rgba >> 24),
            static_cast<u8>(rgba >> 16),
            static_cast<u8>(rgba >> 8),
            static_cast<u8>(rgba));
        ViewImplementation::color_picker_update(color, state);
    }

    void find_in_page(char const* query, size_t len, bool case_sensitive)
    {
        ViewImplementation::find_in_page(
            MUST(String::from_utf8(StringView { query, len })),
            case_sensitive ? CaseSensitivity::CaseSensitive : CaseSensitivity::CaseInsensitive);
    }
    void find_in_page_next_match() { ViewImplementation::find_in_page_next_match(); }
    void find_in_page_previous_match() { ViewImplementation::find_in_page_previous_match(); }

    void activate_context_menu_action(int id)
    {
        if (id < 0 || static_cast<size_t>(id) >= m_context_actions.size())
            return;
        if (auto action = m_context_actions[id].strong_ref())
            action->activate();
    }

    void paint(GtkSnapshot* snapshot, int width, int height)
    {
        if (width == 0 || height == 0)
            return;

        Gfx::Bitmap const* bitmap = nullptr;
        Gfx::IntSize bitmap_size;

        if (m_client_state.has_usable_bitmap) {
            VERIFY(m_client_state.front_bitmap.shared_image_buffer);
            bitmap = m_client_state.front_bitmap.shared_image_buffer->bitmap().ptr();
            bitmap_size = m_client_state.front_bitmap.last_painted_size.to_type<int>();
        } else if (m_backup_shared_image_buffer) {
            bitmap = m_backup_shared_image_buffer->bitmap().ptr();
            bitmap_size = m_backup_bitmap_size.to_type<int>();
        }

        auto append_background = [&] {
            auto is_dark = adw_style_manager_get_dark(adw_style_manager_get_default());
            GdkRGBA background = is_dark ? GdkRGBA { 0.14, 0.14, 0.14, 1.0 } : GdkRGBA { 1.0, 1.0, 1.0, 1.0 };
            graphene_rect_t full_rect = GRAPHENE_RECT_INIT(0, 0, static_cast<float>(width), static_cast<float>(height));
            gtk_snapshot_append_color(snapshot, &background, &full_rect);
        };

        if (!bitmap) {
            append_background();
            return;
        }

        auto painted_width = bitmap_size.width();
        auto painted_height = bitmap_size.height();
        if (painted_width == 0 || painted_height == 0) {
            painted_width = bitmap->width();
            painted_height = bitmap->height();
        }

        if (bitmap != m_cached_bitmap || bitmap_size != m_cached_painted_size || !m_cached_texture) {
            auto* bytes = g_bytes_new_static(bitmap->scanline_u8(0), bitmap->pitch() * painted_height);
            auto* builder = gdk_memory_texture_builder_new();
            gdk_memory_texture_builder_set_bytes(builder, bytes);
            gdk_memory_texture_builder_set_stride(builder, bitmap->pitch());
            gdk_memory_texture_builder_set_width(builder, painted_width);
            gdk_memory_texture_builder_set_height(builder, painted_height);
            gdk_memory_texture_builder_set_format(builder, GDK_MEMORY_B8G8R8A8_PREMULTIPLIED);

            if (m_cached_texture) {
                gdk_memory_texture_builder_set_update_texture(builder, m_cached_texture);
                cairo_rectangle_int_t full_rect = { 0, 0, painted_width, painted_height };
                auto* update_region = cairo_region_create_rectangle(&full_rect);
                gdk_memory_texture_builder_set_update_region(builder, update_region);
                cairo_region_destroy(update_region);
            }

            auto* texture = gdk_memory_texture_builder_build(builder);
            g_object_unref(builder);
            g_bytes_unref(bytes);

            if (m_cached_texture)
                g_object_unref(m_cached_texture);
            m_cached_texture = texture;

            m_cached_bitmap = bitmap;
            m_cached_painted_size = bitmap_size;
        }

        auto draw_width = static_cast<float>(painted_width / m_device_pixel_ratio);
        auto draw_height = static_cast<float>(painted_height / m_device_pixel_ratio);

        graphene_rect_t texture_rect = GRAPHENE_RECT_INIT(0, 0, draw_width, draw_height);
        gtk_snapshot_append_texture(snapshot, m_cached_texture, &texture_rect);

        auto is_dark = adw_style_manager_get_dark(adw_style_manager_get_default());
        GdkRGBA background = is_dark ? GdkRGBA { 0.14, 0.14, 0.14, 1.0 } : GdkRGBA { 1.0, 1.0, 1.0, 1.0 };

        if (draw_width < width) {
            graphene_rect_t right_rect = GRAPHENE_RECT_INIT(draw_width, 0, static_cast<float>(width) - draw_width, static_cast<float>(height));
            gtk_snapshot_append_color(snapshot, &background, &right_rect);
        }

        if (draw_height < height) {
            graphene_rect_t bottom_rect = GRAPHENE_RECT_INIT(0, draw_height, static_cast<float>(width), static_cast<float>(height) - draw_height);
            gtk_snapshot_append_color(snapshot, &background, &bottom_rect);
        }
    }

private:
    void wire_callbacks()
    {
        on_ready_to_paint = [this]() {
            if (m_callbacks.on_ready_to_paint)
                m_callbacks.on_ready_to_paint(m_rust_context);
        };
        on_title_change = [this](Utf16String const& title) {
            if (!m_callbacks.on_title_changed)
                return;
            auto utf8 = title.to_utf8();
            auto bytes = utf8.bytes();
            m_callbacks.on_title_changed(m_rust_context, reinterpret_cast<char const*>(bytes.data()), bytes.size());
        };
        on_url_change = [this](URL::URL const& url) {
            if (!m_callbacks.on_url_changed)
                return;
            auto s = url.serialize();
            auto bytes = s.bytes();
            m_callbacks.on_url_changed(m_rust_context, reinterpret_cast<char const*>(bytes.data()), bytes.size());
        };
        on_load_start = [this](URL::URL const&, bool) {
            if (m_callbacks.on_load_start)
                m_callbacks.on_load_start(m_rust_context);
        };
        on_load_finish = [this](URL::URL const&) {
            if (m_callbacks.on_load_finish)
                m_callbacks.on_load_finish(m_rust_context);
        };
        on_favicon_change = [this](Gfx::Bitmap const& bitmap) {
            if (!m_callbacks.on_favicon_change)
                return;
            m_callbacks.on_favicon_change(m_rust_context, bitmap.scanline_u8(0), bitmap.width(), bitmap.height(), bitmap.pitch());
        };
        on_cursor_change = [this](Gfx::Cursor const& cursor) {
            if (!m_callbacks.on_cursor_change)
                return;
            auto name = cursor.visit(
                [](Gfx::StandardCursor sc) { return Ladybird::standard_cursor_to_css_name(sc); },
                [](Gfx::ImageCursor const&) { return "default"sv; });
            m_callbacks.on_cursor_change(m_rust_context, name.characters_without_null_termination(), name.length());
        };
        on_link_hover = [this](URL::URL const& url) {
            if (!m_callbacks.on_link_hover)
                return;
            auto s = url.serialize();
            auto bytes = s.bytes();
            m_callbacks.on_link_hover(m_rust_context, reinterpret_cast<char const*>(bytes.data()), bytes.size());
        };
        on_link_unhover = [this]() {
            if (m_callbacks.on_link_unhover)
                m_callbacks.on_link_unhover(m_rust_context);
        };
        on_enter_tooltip_area = [this](ByteString const& text) {
            if (m_callbacks.on_enter_tooltip_area)
                m_callbacks.on_enter_tooltip_area(m_rust_context, text.characters(), text.length());
        };
        on_leave_tooltip_area = [this]() {
            if (m_callbacks.on_leave_tooltip_area)
                m_callbacks.on_leave_tooltip_area(m_rust_context);
        };
        on_request_alert = [this](String const& message) {
            if (!m_callbacks.on_request_alert)
                return;
            auto bytes = message.bytes();
            m_callbacks.on_request_alert(m_rust_context, reinterpret_cast<char const*>(bytes.data()), bytes.size());
        };
        on_request_confirm = [this](String const& message) {
            if (!m_callbacks.on_request_confirm)
                return;
            auto bytes = message.bytes();
            m_callbacks.on_request_confirm(m_rust_context, reinterpret_cast<char const*>(bytes.data()), bytes.size());
        };
        on_request_prompt = [this](String const& message, String const& default_value) {
            if (!m_callbacks.on_request_prompt)
                return;
            auto mb = message.bytes(), db = default_value.bytes();
            m_callbacks.on_request_prompt(m_rust_context,
                reinterpret_cast<char const*>(mb.data()), mb.size(),
                reinterpret_cast<char const*>(db.data()), db.size());
        };
        on_request_color_picker = [this](Color color) {
            if (!m_callbacks.on_request_color_picker)
                return;
            uint32_t rgba = (static_cast<uint32_t>(color.red()) << 24)
                | (static_cast<uint32_t>(color.green()) << 16)
                | (static_cast<uint32_t>(color.blue()) << 8)
                | static_cast<uint32_t>(color.alpha());
            m_callbacks.on_request_color_picker(m_rust_context, rgba);
        };
        on_new_web_view = [this](Web::HTML::ActivateTab activate, Web::HTML::WebViewHints, Optional<u64> page_index) -> String {
            if (!m_callbacks.on_new_web_view)
                return {};
            char buf[256] = {};
            int64_t pidx = page_index.has_value() ? static_cast<int64_t>(page_index.value()) : -1;
            m_callbacks.on_new_web_view(m_rust_context, activate == Web::HTML::ActivateTab::Yes, pidx, buf, sizeof(buf) - 1);
            return MUST(String::from_utf8(StringView { buf, strlen(buf) }));
        };
        on_activate_tab = [this]() {
            if (m_callbacks.on_activate_tab)
                m_callbacks.on_activate_tab(m_rust_context);
        };
        on_close = [this]() {
            if (m_callbacks.on_close)
                m_callbacks.on_close(m_rust_context);
        };
        on_zoom_level_changed = [this]() {
            if (m_callbacks.on_zoom_level_changed)
                m_callbacks.on_zoom_level_changed(m_rust_context);
        };
        on_find_in_page = [this](size_t current, Optional<size_t> const& total) {
            if (!m_callbacks.on_find_in_page)
                return;
            m_callbacks.on_find_in_page(m_rust_context,
                static_cast<uint32_t>(current),
                total.has_value(),
                static_cast<uint32_t>(total.value_or(0)));
        };
        on_audio_play_state_changed = [this](Web::HTML::AudioPlayState state) {
            if (m_callbacks.on_audio_play_state_changed)
                m_callbacks.on_audio_play_state_changed(m_rust_context, state == Web::HTML::AudioPlayState::Playing);
        };
        on_fullscreen_window = [this]() {
            if (m_callbacks.on_fullscreen_window)
                m_callbacks.on_fullscreen_window(m_rust_context);
        };
        on_exit_fullscreen_window = [this]() {
            if (m_callbacks.on_exit_fullscreen_window)
                m_callbacks.on_exit_fullscreen_window(m_rust_context);
        };
        on_maximize_window = [this]() {
            if (m_callbacks.on_maximize_window)
                m_callbacks.on_maximize_window(m_rust_context);
        };
        on_minimize_window = [this]() {
            if (m_callbacks.on_minimize_window)
                m_callbacks.on_minimize_window(m_rust_context);
        };
        on_restore_window = [this]() {
            if (m_callbacks.on_restore_window)
                m_callbacks.on_restore_window(m_rust_context);
        };
        on_resize_window = [this](Gfx::IntSize size) {
            if (m_callbacks.on_resize_window)
                m_callbacks.on_resize_window(m_rust_context, size.width(), size.height());
        };
        on_finish_handling_key_event = [this](Web::KeyEvent const& event) {
            finish_handling_key_event(event);
        };

        auto wire_context_menu = [this](WebView::Menu& menu) {
            menu.on_activation = [this, &menu](Gfx::IntPoint position) {
                if (!m_callbacks.on_context_menu)
                    return;
                m_context_actions.clear_with_capacity();
                Vector<LadybirdContextMenuItem> items;
                serialize_menu(menu, items, m_context_actions);
                m_callbacks.on_context_menu(
                    m_rust_context,
                    position.x() / m_device_pixel_ratio,
                    position.y() / m_device_pixel_ratio,
                    items.data(), items.size());
            };
        };
        wire_context_menu(page_context_menu());
        wire_context_menu(link_context_menu());
        wire_context_menu(image_context_menu());

        on_request_select_dropdown = [this](Gfx::IntPoint pos, i32 min_width, Vector<Web::HTML::SelectItem> items) {
            if (!m_callbacks.on_show_select_dropdown)
                return;
            Vector<LadybirdSelectItem> out;
            for (auto const& item : items) {
                item.visit(
                    [&](Web::HTML::SelectItemOption const& opt) {
                        auto bytes = opt.label.bytes();
                        out.append(LadybirdSelectItem {
                            .is_separator = false, .is_group_start = false, .is_group_end = false, .id = opt.id, .selected = opt.selected, .disabled = opt.disabled, .label = reinterpret_cast<char const*>(bytes.data()), .label_len = bytes.size() });
                    },
                    [&](Web::HTML::SelectItemOptionGroup const& group) {
                        auto gl = group.label.bytes();
                        out.append(LadybirdSelectItem { .is_group_start = true,
                            .label = reinterpret_cast<char const*>(gl.data()),
                            .label_len = gl.size() });
                        for (auto const& opt : group.items) {
                            auto ol = opt.label.bytes();
                            out.append(LadybirdSelectItem {
                                .id = opt.id, .selected = opt.selected, .disabled = opt.disabled, .label = reinterpret_cast<char const*>(ol.data()), .label_len = ol.size() });
                        }
                        out.append(LadybirdSelectItem { .is_group_end = true });
                    },
                    [&](Web::HTML::SelectItemSeparator const&) {
                        out.append(LadybirdSelectItem { .is_separator = true });
                    });
            }
            m_callbacks.on_show_select_dropdown(m_rust_context,
                pos.x() / m_device_pixel_ratio, pos.y() / m_device_pixel_ratio,
                min_width, out.data(), out.size());
        };

        on_request_file_picker = [this](Web::HTML::FileFilter const& filter, Web::HTML::AllowMultipleFiles allow_multiple) {
            if (!m_callbacks.on_request_file_picker)
                return;
            Vector<LadybirdFileFilter> out;
            for (auto const& f : filter.filters) {
                f.visit(
                    [&](Web::HTML::FileFilter::Extension const& ext) {
                        auto bytes = ext.value.bytes();
                        out.append({ LADYBIRD_FILE_FILTER_EXTENSION,
                            reinterpret_cast<char const*>(bytes.data()), bytes.size() });
                    },
                    [&](Web::HTML::FileFilter::MimeType const& mime) {
                        auto bytes = mime.value.bytes();
                        out.append({ LADYBIRD_FILE_FILTER_MIME_TYPE,
                            reinterpret_cast<char const*>(bytes.data()), bytes.size() });
                    },
                    [&](Web::HTML::FileFilter::FileType const& ft) {
                        int type = ft == Web::HTML::FileFilter::FileType::Audio ? LADYBIRD_FILE_FILTER_AUDIO
                            : ft == Web::HTML::FileFilter::FileType::Image      ? LADYBIRD_FILE_FILTER_IMAGE
                                                                                : LADYBIRD_FILE_FILTER_VIDEO;
                        out.append({ type, nullptr, 0 });
                    });
            }
            m_callbacks.on_request_file_picker(m_rust_context,
                allow_multiple == Web::HTML::AllowMultipleFiles::Yes,
                out.data(), out.size());
        };
    }

    void initialize_client(CreateNewClient create_new_client) override
    {
        ViewImplementation::initialize_client(create_new_client);
        update_palette();
        update_screen_rects();
    }

    void update_zoom() override
    {
        ViewImplementation::update_zoom();
        if (m_callbacks.on_zoom_level_changed)
            m_callbacks.on_zoom_level_changed(m_rust_context);
    }

    void finish_handling_key_event(Web::KeyEvent const& event)
    {
        if (event.type != Web::KeyEvent::Type::KeyDown)
            return;
        if (!(event.modifiers & Web::UIEvents::KeyModifier::Mod_Ctrl))
            return;

        auto& app = WebView::Application::the();
        switch (event.key) {
        case Web::UIEvents::Key_C:
            app.copy_selection_action().activate();
            break;
        case Web::UIEvents::Key_V:
            app.paste_action().activate();
            break;
        case Web::UIEvents::Key_A:
            app.select_all_action().activate();
            break;
        default:
            break;
        }
    }

    void update_palette()
    {
        auto is_dark = adw_style_manager_get_dark(adw_style_manager_get_default());
        auto theme_file = is_dark ? "Dark"sv : "Default"sv;
        auto theme_ini = Core::Resource::load_from_uri(MUST(String::formatted("resource://themes/{}.ini", theme_file)));
        if (theme_ini.is_error())
            return;

        auto theme_or_error = Gfx::load_system_theme(theme_ini.value()->filesystem_path().to_byte_string());
        if (theme_or_error.is_error())
            return;

        set_preferred_color_scheme(is_dark ? Web::CSS::PreferredColorScheme::Dark : Web::CSS::PreferredColorScheme::Light);
        client().async_update_system_theme(page_id(), theme_or_error.release_value());
    }

    void update_screen_rects()
    {
        auto* display = gdk_display_get_default();
        if (!display)
            return;

        auto* monitors = gdk_display_get_monitors(display);
        auto monitor_count = g_list_model_get_n_items(monitors);

        Vector<Web::DevicePixelRect> screen_rects;
        screen_rects.ensure_capacity(monitor_count);

        for (guint i = 0; i < monitor_count; ++i) {
            GdkMonitor* monitor = GDK_MONITOR(g_list_model_get_item(monitors, i));
            GdkRectangle geometry;
            gdk_monitor_get_geometry(monitor, &geometry);
            auto scale = gdk_monitor_get_scale_factor(monitor);

            screen_rects.append(Web::DevicePixelRect {
                geometry.x * scale,
                geometry.y * scale,
                geometry.width * scale,
                geometry.height * scale });

            g_object_unref(monitor);
        }

        if (screen_rects.is_empty())
            screen_rects.append(Web::DevicePixelRect { 0, 0, 1920, 1080 });

        client().async_update_screen_rects(page_id(), move(screen_rects), 0);
    }

    uintptr_t m_rust_context { 0 };
    LadybirdViewCallbacks m_callbacks {};
    Gfx::IntSize m_viewport_size;
    Vector<WeakPtr<WebView::Action>> m_context_actions;
    GdkTexture* m_cached_texture { nullptr };
    Gfx::Bitmap const* m_cached_bitmap { nullptr };
    Gfx::IntSize m_cached_painted_size;
};

static OwnPtr<RsApplication> s_application;
static thread_local ByteString s_url_scratch;
static GtkWidget* s_toast_overlay = nullptr;

}

using LadybirdRs::RsApplication;
using LadybirdRs::RsView;

extern "C" {

int ladybird_app_init(int argc, char** argv)
{
    AK::set_rich_debug_enabled(true);

    static Vector<StringView> s_argument_strings;
    s_argument_strings.clear_with_capacity();
    s_argument_strings.ensure_capacity(argc);
    for (int i = 0; i < argc; ++i)
        s_argument_strings.append(StringView { argv[i], strlen(argv[i]) });

    Main::Arguments arguments {
        .argc = argc,
        .argv = argv,
        .strings = s_argument_strings.span(),
    };

    auto application = RsApplication::create(arguments);
    if (application.is_error())
        return 1;

    LadybirdRs::s_application = application.release_value();
    return 0;
}

void ladybird_app_shutdown(void)
{
    LadybirdRs::s_application = nullptr;
}

void ladybird_app_quit(int exit_code)
{
    Core::EventLoop::current().quit(exit_code);
}

size_t ladybird_app_initial_url_count(void)
{
    return WebView::Application::browser_options().urls.size();
}

void ladybird_app_initial_url_at(size_t index, char const** out_ptr, size_t* out_len)
{
    auto const& urls = WebView::Application::browser_options().urls;
    if (index >= urls.size()) {
        *out_ptr = nullptr;
        *out_len = 0;
        return;
    }

    LadybirdRs::s_url_scratch = urls[index].serialize().to_byte_string();
    *out_ptr = LadybirdRs::s_url_scratch.characters();
    *out_len = LadybirdRs::s_url_scratch.length();
}

bool ladybird_app_is_headless(void)
{
    return WebView::Application::browser_options().headless_mode.has_value();
}

LadybirdView* ladybird_view_create(uintptr_t rust_context, LadybirdViewCallbacks const* callbacks)
{
    return reinterpret_cast<LadybirdView*>(new RsView(rust_context, *callbacks));
}

void ladybird_view_destroy(LadybirdView* view)
{
    delete reinterpret_cast<RsView*>(view);
}

void ladybird_view_load(LadybirdView* view, char const* url, size_t len)
{
    reinterpret_cast<RsView*>(view)->load_url(url, len);
}

void ladybird_view_set_viewport_size(LadybirdView* view, int width, int height)
{
    reinterpret_cast<RsView*>(view)->set_viewport_size(width, height);
}

void ladybird_view_set_device_pixel_ratio(LadybirdView* view, double device_pixel_ratio)
{
    reinterpret_cast<RsView*>(view)->set_device_pixel_ratio(device_pixel_ratio);
}

void ladybird_view_set_system_visibility(LadybirdView* view, bool visible)
{
    reinterpret_cast<RsView*>(view)->set_system_visibility(visible);
}

void ladybird_view_set_has_focus(LadybirdView* view, bool has_focus)
{
    reinterpret_cast<RsView*>(view)->set_has_focus(has_focus);
}

void ladybird_view_mouse_event(LadybirdView* view, int type, double x, double y, unsigned button, unsigned state, int click_count)
{
    reinterpret_cast<RsView*>(view)->dispatch_mouse_event(type, x, y, button, static_cast<GdkModifierType>(state), click_count);
}

void ladybird_view_scroll_event(LadybirdView* view, double dx, double dy, unsigned state, int unit)
{
    reinterpret_cast<RsView*>(view)->dispatch_scroll_event(dx, dy, static_cast<GdkModifierType>(state), unit);
}

void ladybird_view_key_event(LadybirdView* view, int type, unsigned keyval, unsigned state)
{
    reinterpret_cast<RsView*>(view)->dispatch_key_event(type, keyval, static_cast<GdkModifierType>(state));
}

void ladybird_view_go_back(LadybirdView* view)
{
    reinterpret_cast<RsView*>(view)->navigate_back();
}

void ladybird_view_go_forward(LadybirdView* view)
{
    reinterpret_cast<RsView*>(view)->navigate_forward();
}

void ladybird_view_reload(LadybirdView* view)
{
    reinterpret_cast<RsView*>(view)->reload();
}

void ladybird_view_zoom_in(LadybirdView* view)
{
    reinterpret_cast<RsView*>(view)->zoom_in();
}

void ladybird_view_zoom_out(LadybirdView* view)
{
    reinterpret_cast<RsView*>(view)->zoom_out();
}

void ladybird_view_reset_zoom(LadybirdView* view)
{
    reinterpret_cast<RsView*>(view)->reset_zoom();
}

double ladybird_view_zoom_level(LadybirdView const* view)
{
    return reinterpret_cast<RsView const*>(view)->zoom_level();
}

void ladybird_view_alert_closed(LadybirdView* view)
{
    reinterpret_cast<RsView*>(view)->alert_closed();
}

void ladybird_view_confirm_closed(LadybirdView* view, bool accepted)
{
    reinterpret_cast<RsView*>(view)->confirm_closed(accepted);
}

void ladybird_view_prompt_closed(LadybirdView* view, char const* response, size_t len, bool cancelled)
{
    reinterpret_cast<RsView*>(view)->prompt_closed(response, len, cancelled);
}

void ladybird_view_color_picker_update(LadybirdView* view, uint32_t rgba, bool released)
{
    reinterpret_cast<RsView*>(view)->color_picker_update(rgba, released);
}

void ladybird_view_find_in_page(LadybirdView* view, char const* query, size_t len, bool case_sensitive)
{
    reinterpret_cast<RsView*>(view)->find_in_page(query, len, case_sensitive);
}

void ladybird_view_find_in_page_next_match(LadybirdView* view)
{
    reinterpret_cast<RsView*>(view)->find_in_page_next_match();
}

void ladybird_view_find_in_page_previous_match(LadybirdView* view)
{
    reinterpret_cast<RsView*>(view)->find_in_page_previous_match();
}

void ladybird_view_paint(LadybirdView* view, void* snapshot, int width, int height)
{
    reinterpret_cast<RsView*>(view)->paint(static_cast<GtkSnapshot*>(snapshot), width, height);
}

void ladybird_view_handle(LadybirdView const* view, char* buf, size_t buf_len)
{
    if (!buf || buf_len == 0)
        return;
    auto const& handle = reinterpret_cast<RsView const*>(view)->handle();
    auto bytes = handle.bytes();
    size_t copy_len = min(bytes.size(), buf_len - 1);
    memcpy(buf, bytes.data(), copy_len);
    buf[copy_len] = '\0';
}

void ladybird_view_context_menu_action(LadybirdView* view, int id)
{
    reinterpret_cast<RsView*>(view)->activate_context_menu_action(id);
}

void ladybird_view_select_dropdown_closed(LadybirdView* view, bool has_id, uint32_t id)
{
    auto* rs_view = reinterpret_cast<RsView*>(view);
    if (has_id)
        rs_view->select_dropdown_closed(id);
    else
        rs_view->select_dropdown_closed({});
}

void ladybird_view_file_picker_closed(LadybirdView* view, char const* const* paths, size_t count)
{
    Vector<Web::HTML::SelectedFile> files;
    for (size_t i = 0; i < count; ++i) {
        auto result = Web::HTML::SelectedFile::from_file_path(ByteString(paths[i]));
        if (!result.is_error())
            files.append(result.release_value());
    }
    reinterpret_cast<RsView*>(view)->file_picker_closed(move(files));
}

void ladybird_app_set_toast_overlay(void* overlay_ptr)
{
    LadybirdRs::s_toast_overlay = static_cast<GtkWidget*>(overlay_ptr);
    if (LadybirdRs::s_application)
        LadybirdRs::s_application->set_toast_overlay(LadybirdRs::s_toast_overlay);
}

bool ladybird_app_devtools_enabled(void)
{
    return WebView::Application::browser_options().devtools_port.has_value();
}

uint16_t ladybird_app_devtools_port(void)
{
    return WebView::Application::browser_options().devtools_port.value_or(0);
}

void ladybird_app_toggle_devtools(void)
{
    if (LadybirdRs::s_application)
        (void)LadybirdRs::s_application->toggle_devtools_enabled();
}

void ladybird_app_activate_about(void)
{
    if (LadybirdRs::s_application)
        LadybirdRs::s_application->open_about_page_action().activate();
}

void ladybird_app_activate_settings(void)
{
    if (LadybirdRs::s_application)
        LadybirdRs::s_application->open_settings_page_action().activate();
}
}
