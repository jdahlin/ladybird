/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/Time.h>
#include <LibJS/Bytecode/Executable.h>
#include <LibJS/Profiler.h>
#include <LibJS/Runtime/VM.h>
#include <LibJS/SourceRange.h>

namespace JS {

static i64 current_epoch_ms()
{
    return UnixDateTime::now().milliseconds_since_epoch();
}

Profiler::Profiler(VM& vm, int interval_us)
    : m_vm(vm)
    , m_interval_us(interval_us)
{
}

Profiler::~Profiler()
{
    stop();
}

void Profiler::sample_if_needed()
{
    if (!m_sample_pending.exchange(false, AK::MemoryOrder::memory_order_relaxed))
        return;
    capture_sample({});
}

void Profiler::request_sample_for_test()
{
    m_sample_pending.store(true, AK::MemoryOrder::memory_order_relaxed);
}

void Profiler::reset_state_for_start()
{
    m_start_time = MonotonicTime::now();
    m_start_epoch_ms = current_epoch_ms();
    m_stop_epoch_ms = 0;
    m_js_thread = pthread_self();
    m_raw_sample_count.store(0, AK::MemoryOrder::memory_order_relaxed);
    m_sample_pending.store(false, AK::MemoryOrder::memory_order_relaxed);
    m_raw_samples = new RawSample[MAX_RAW_SAMPLES];
    m_string_table.clear();
    m_string_map.clear();
    m_frame_table.clear();
    m_frame_map.clear();
    m_stack_table.clear();
    m_stack_map.clear();
    m_samples.clear();
}

void Profiler::reserve_output_tables()
{
    m_string_table.ensure_capacity(256);
    m_frame_table.ensure_capacity(256);
    m_stack_table.ensure_capacity(1024);
}

void Profiler::allocate_raw_samples()
{
    delete[] m_raw_samples;
    m_raw_samples = new RawSample[MAX_RAW_SAMPLES];
}

void Profiler::process_and_free_raw_samples()
{
    if (!m_raw_samples)
        return;

    process_raw_samples();
    delete[] m_raw_samples;
    m_raw_samples = nullptr;
}

void Profiler::stop_timer_thread()
{
    m_timer_running.store(false, AK::MemoryOrder::memory_order_relaxed);
    if (!m_timer_thread)
        return;

    (void)m_timer_thread->join();
    m_timer_thread = nullptr;
}

double Profiler::elapsed_ms_since_start() const
{
    auto elapsed = MonotonicTime::now() - m_start_time;
    return static_cast<double>(max(elapsed.to_microseconds(), static_cast<i64>(0))) / 1000.0;
}

void Profiler::allocate_sample_buffer()
{
    reset_state_for_start();
    allocate_raw_samples();
    reserve_output_tables();
}

void Profiler::collect_and_free_samples()
{
    m_stop_epoch_ms = current_epoch_ms();
    stop_timer_thread();
    process_and_free_raw_samples();
}

void Profiler::capture_frames(RawSample& tick, Optional<u32> leaf_program_counter)
{
    // Frames are stored leaf-first: frames[0] is the innermost call.
    // intern_stack_trace iterates frame_count-1 down to 0, so the LAST stored
    // frame is processed FIRST (it becomes the outermost / root).
    //
    // To put marker scope frames at the BOTTOM (outermost) of the call tree,
    // we store them AFTER the JS frames so they end up at high indices and
    // get processed first by intern_stack_trace.
    auto const& ec_stack = m_vm.execution_context_stack();
    u32 frame_count = 0;

    // Snapshot the stack size up front. The vector may grow under us if a signal
    // arrives mid-push, but we only iterate up to the size we observed.
    auto const stack_size = ec_stack.size();

    for (ssize_t i = static_cast<ssize_t>(stack_size) - 1; i >= 0 && frame_count < MAX_STACK_DEPTH; --i) {
        auto* ctx = ec_stack[i];
        if (!ctx)
            continue;

        // Defensively snapshot the executable pointer once.  push_inline_frame may
        // have set this last (race window between push_back and field assignment),
        // and we'd rather skip a frame than crash dereferencing garbage.
        auto* executable = ctx->executable.ptr();
        if (!executable)
            continue;

        // Sanity check the executable pointer alignment — GC cells are at least
        // 8-byte aligned. A garbage pointer is unlikely to be aligned.
        if (bit_cast<uintptr_t>(executable) & 0x7)
            continue;

        // Use the register-derived offset only for the topmost frame (macOS Mach path).
        // On the Linux safe-point path leaf_program_counter is absent and ctx->program_counter
        // is used for all frames. For inner frames on macOS (frame_count > 0),
        // ctx->program_counter may be stale when the ASM interpreter is running.
        auto program_counter = (frame_count == 0 && leaf_program_counter.has_value())
            ? *leaf_program_counter
            : ctx->program_counter;

        auto& frame = tick.frames[frame_count++];
        frame.executable = bit_cast<FlatPtr>(executable);
        frame.program_counter = program_counter;
        frame.name = executable->name;
        frame.marker_name_ptr = nullptr;
        frame.marker_name_len = 0;
    }

    // Append marker scope frames AFTER JS frames so they end up at high indices
    // and become the OUTERMOST callers (bottom of the call tree). They survive
    // even when JS isn't running — critical because much of the work we want to
    // attribute (Layout, Style, Paint) happens in C++ with no JS frames active.
    //
    // Reading t_marker_scope_stack from a signal handler is safe because:
    //   - It's thread-local and we're on the JS thread
    //   - The vector grows linearly via push_back/take_last (no shuffling)
    //   - Walking up to current size is consistent with what was pushed before
    //     the signal arrived
    //
    // Iterate the scope stack in reverse so the OUTERMOST scope (the one pushed
    // first, at index 0) ends up at the highest frame index (processed first by
    // intern_stack_trace, becoming the root of the call tree).
    auto const& scope_stack = Core::t_marker_scope_stack;
    auto const scope_count = scope_stack.size();
    for (ssize_t i = 0; i < static_cast<ssize_t>(scope_count) && frame_count < MAX_STACK_DEPTH; ++i) {
        // Store in reverse: scope_stack[0] (outermost) goes last, becomes high frame index.
        auto const& scope = scope_stack[scope_count - 1 - i];
        auto& frame = tick.frames[frame_count++];
        frame.executable = 0; // sentinel: synthetic marker scope frame
        // Encode marker category in the low byte of program_counter so the
        // interner can pick up the category for the synthetic frame.
        frame.program_counter = static_cast<u32>(to_underlying(scope.category));
        // Store the raw string pointer + length. The marker name MUST be a
        // string literal so this pointer outlives the scope. We cannot allocate
        // here (signal handler) — the FlyString conversion happens later in
        // process_raw_samples().
        frame.name = Utf16FlyString {};
        frame.marker_name_ptr = scope.name.characters_without_null_termination();
        frame.marker_name_len = static_cast<u32>(scope.name.length());
    }

    tick.frame_count = frame_count;
}

void Profiler::capture_sample(Optional<u32> leaf_program_counter)
{
    auto index = m_raw_sample_count.load(AK::MemoryOrder::memory_order_relaxed);
    if (index >= MAX_RAW_SAMPLES || !m_raw_samples)
        return;

    auto& tick = m_raw_samples[index];
    tick.time_ms = elapsed_ms_since_start();
    capture_frames(tick, leaf_program_counter);
    m_raw_sample_count.store(index + 1, AK::MemoryOrder::memory_order_relaxed);
}

void Profiler::process_raw_samples()
{
    auto count = min(m_raw_sample_count.load(AK::MemoryOrder::memory_order_relaxed), MAX_RAW_SAMPLES);
    m_samples.ensure_capacity(count);

    for (u32 i = 0; i < count; ++i) {
        auto const& tick = m_raw_samples[i];
        if (tick.frame_count == 0 || tick.frame_count > MAX_STACK_DEPTH)
            continue;
        m_samples.append({ tick.time_ms, intern_stack_trace(tick) });
    }
}

void Profiler::add_network_request_start(u64 id, String url, String method, double start_time_ms)
{
    m_network_markers.append({
        .id = id,
        .url = move(url),
        .method = move(method),
        .start_time_ms = start_time_ms,
        .end_time_ms = start_time_ms,
        .is_stop = false,
        .status_code = 0,
        .content_type = {},
        .body_size = 0,
        .timings = {},
    });
}

void Profiler::add_network_request_stop(u64 id, String url, String method, double start_time_ms, double end_time_ms,
    u32 status_code, String content_type, i64 body_size, NetworkTimings const& timings)
{
    m_network_markers.append({
        .id = id,
        .url = move(url),
        .method = move(method),
        .start_time_ms = start_time_ms,
        .end_time_ms = end_time_ms,
        .is_stop = true,
        .status_code = status_code,
        .content_type = move(content_type),
        .body_size = body_size,
        .timings = timings,
    });
}

// Capture a JS call stack snapshot suitable for attaching to a marker.
// Walks the execution context stack from leaf to root.
//
// CRITICAL: Only safe to call from the JS main thread. The execution context
// stack is owned by that thread and not synchronized. Markers emitted from
// other threads (e.g. media decoder workers) must NOT call this — we'd race
// against the main thread modifying the stack.
void Profiler::capture_marker_stack(Vector<Core::MarkerStackFrame, 8>& out)
{
    out.clear_with_capacity();

    // Skip if we're not on the JS main thread.
    if (!pthread_equal(pthread_self(), m_js_thread))
        return;

    auto const& ec_stack = m_vm.execution_context_stack();
    auto const stack_size = ec_stack.size();

    for (ssize_t i = static_cast<ssize_t>(stack_size) - 1; i >= 0 && out.size() < MAX_STACK_DEPTH; --i) {
        auto* ctx = ec_stack[i];
        if (!ctx)
            continue;
        auto const* executable = ctx->executable.ptr();
        if (!executable)
            continue;

        u32 line = 0;
        u32 column = 0;
        String filename;
        if (ctx->program_counter < executable->bytecode.size()) {
            auto unrealized = executable->source_range_at(ctx->program_counter);
            if (unrealized.source_code) {
                auto range = unrealized.realize();
                line = range.start.line;
                column = range.start.column;
                filename = MUST(String::from_byte_string(range.filename()));
            }
        }

        String function_name = executable->name.is_empty()
            ? "(anonymous)"_string
            : MUST(String::formatted("{}", executable->name));

        String location = filename.is_empty()
            ? function_name
            : MUST(String::formatted("{} ({}:{}:{})", function_name, filename, line, column));

        out.append({ move(location), line, column });
    }
}

u32 Profiler::intern_stack_trace(RawSample const& tick)
{
    Optional<u32> prefix;

    for (ssize_t i = static_cast<ssize_t>(tick.frame_count) - 1; i >= 0; --i) {
        auto const& frame = tick.frames[i];

        String location;
        u32 line = 0;
        u32 column = 0;
        u8 category = static_cast<u8>(to_underlying(Core::MarkerCategory::JavaScript));

        if (frame.executable == 0) {
            // Synthetic marker scope frame.
            // The category is encoded in the low byte of program_counter.
            // marker_name_ptr/len point at a string literal that's still alive.
            if (frame.marker_name_ptr && frame.marker_name_len > 0) {
                location = MUST(String::from_utf8(StringView { frame.marker_name_ptr, frame.marker_name_len }));
            } else {
                location = "(marker)"_string;
            }
            category = static_cast<u8>(frame.program_counter & 0xff);
        } else {
            // Regular JS frame.
            auto const* executable = bit_cast<Bytecode::Executable const*>(frame.executable);

            String filename;
            if (executable && frame.program_counter < executable->bytecode.size()) {
                auto unrealized = executable->source_range_at(frame.program_counter);
                if (unrealized.source_code) {
                    auto range = unrealized.realize();
                    line = range.start.line;
                    column = range.start.column;
                    filename = MUST(String::from_byte_string(range.filename()));
                }
            }

            String function_name = frame.name.is_empty()
                ? "(anonymous)"_string
                : MUST(String::formatted("{}", frame.name));

            location = filename.is_empty()
                ? function_name
                : MUST(String::formatted("{} ({}:{}:{})", function_name, filename, line, column));
            // JS frames stay JavaScript category.
        }

        auto frame_index = intern_frame(location, line, column, category);
        u64 key = (static_cast<u64>(frame_index) << 32) | prefix.value_or(UINT32_MAX);

        if (auto it = m_stack_map.find(key); it != m_stack_map.end()) {
            prefix = it->value;
        } else {
            u32 stack_index = m_stack_table.size();
            m_stack_table.append({ frame_index, prefix });
            m_stack_map.set(key, stack_index);
            prefix = stack_index;
        }
    }

    return prefix.value_or(0);
}

u32 Profiler::intern_string(String const& str)
{
    if (auto it = m_string_map.find(str); it != m_string_map.end())
        return it->value;
    u32 index = m_string_table.size();
    m_string_table.append(str);
    m_string_map.set(str, index);
    return index;
}

u32 Profiler::intern_frame(String const& location, u32 line, u32 column, u8 category)
{
    // The location string encodes function name + source position, so string_index
    // is a unique key for each distinct (function, file, line, col) combination.
    auto string_index = intern_string(location);
    if (auto it = m_frame_map.find(string_index); it != m_frame_map.end())
        return it->value;
    u32 index = m_frame_table.size();
    m_frame_table.append({ string_index, line, column, category });
    m_frame_map.set(string_index, index);
    return index;
}

}
