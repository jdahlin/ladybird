/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/Time.h>
#include <LibCore/Profiler/Label.h>
#include <LibCore/Profiler/PlatformSampler.h>
#include <LibJS/Bytecode/Executable.h>
#include <LibJS/JSStackSampler.h>
#include <LibJS/Runtime/VM.h>
#include <LibJS/SourceRange.h>

namespace JS {

static i64 current_epoch_ms()
{
    return UnixDateTime::now().milliseconds_since_epoch();
}

JSStackSampler::JSStackSampler(VM& vm, int interval_us)
    : m_vm(vm)
    , m_interval_us(interval_us)
    , m_fallback_profiled_thread(make<Core::ProfiledThread>("Main"_string))
{
    // Safe default so reads through the profiler don't require start() to
    // have run first (tests that stop() without start()). A real session
    // overrides this via set_profiled_thread().
    m_profiled_thread = m_fallback_profiled_thread.ptr();
}

JSStackSampler::~JSStackSampler()
{
    stop();
}

void JSStackSampler::sample_if_needed()
{
    if (!m_sample_pending.exchange(false, AK::MemoryOrder::memory_order_relaxed))
        return;
    do_capture_sample({});
}

void JSStackSampler::request_sample_for_test()
{
    m_sample_pending.store(true, AK::MemoryOrder::memory_order_relaxed);
}

void JSStackSampler::reset_state_for_start()
{
    VERIFY(m_profiled_thread);
    m_start_time = MonotonicTime::now();
    m_start_epoch_ms = current_epoch_ms();
    m_stop_epoch_ms = 0;
    m_js_thread = pthread_self();
    m_raw_sample_count.store(0, AK::MemoryOrder::memory_order_relaxed);
    m_sample_pending.store(false, AK::MemoryOrder::memory_order_relaxed);
    m_raw_samples = new RawSample[MAX_RAW_SAMPLES];
    m_profiled_thread->clear();
}

void JSStackSampler::reserve_output_tables()
{
    VERIFY(m_profiled_thread);
    m_profiled_thread->reserve_capacity();
}

void JSStackSampler::allocate_raw_samples()
{
    delete[] m_raw_samples;
    m_raw_samples = new RawSample[MAX_RAW_SAMPLES];
}

void JSStackSampler::process_and_free_raw_samples()
{
    if (!m_raw_samples)
        return;

    process_raw_samples();
    delete[] m_raw_samples;
    m_raw_samples = nullptr;
}

double JSStackSampler::elapsed_ms_since_start() const
{
    auto elapsed = MonotonicTime::now() - m_start_time;
    return static_cast<double>(max(elapsed.to_microseconds(), static_cast<i64>(0))) / 1000.0;
}

void JSStackSampler::allocate_sample_buffer()
{
    reset_state_for_start();
    allocate_raw_samples();
    reserve_output_tables();
}

void JSStackSampler::collect_and_free_samples()
{
    m_stop_epoch_ms = current_epoch_ms();
    process_and_free_raw_samples();
}

// Platform-neutral start/stop — the timed sampling driver lives in
// Core::PlatformSampler; this just allocates the buffer, publishes the
// SamplingHandle, and asks the driver to start.
void JSStackSampler::start()
{
    allocate_sample_buffer();

    // Make sure the calling thread has profiler state so the unified
    // profiling stack is reachable. Required for both timed sampling
    // (signal handler reads it) AND safe-point sampling (capture_frames
    // walks state->profiling_stack), so do it before the timed-sampling
    // early return below.
    Core::ensure_profiler_state();

    if (m_interval_us <= 0)
        return;

    m_sampling_handle.sampler = this;
    m_sampling_handle.profiled = m_profiled_thread;
    m_sampling_handle.in_handler.store(false, AK::MemoryOrder::memory_order_relaxed);

    (void)Core::PlatformSampler::start({ &m_sampling_handle, m_js_thread, m_interval_us });
}

void JSStackSampler::stop()
{
    Core::PlatformSampler::stop();
    collect_and_free_samples();
}

bool JSStackSampler::supports_timed_sampling() const { return m_interval_us > 0; }

// Timed sampling reads the PC from the signal handler's ucontext on
// Linux and from thread_get_state on macOS, so safe-point polling is
// only needed on platforms where timed sampling is unavailable (today
// that's Windows and the "interval <= 0" test-only path).
bool JSStackSampler::needs_bytecode_safe_points() const { return m_interval_us <= 0; }

void JSStackSampler::capture_frames(RawSample& tick, Optional<u32> leaf_program_counter)
{
    // Walk the unified profiling stack: every label push (PROFILER_LABEL,
    // MARKER_SCOPE) AND every JS function entry (push_inline_frame /
    // vm.push_execution_context) lives on the same FixedProfilingStack, so
    // their relative ordering is preserved. The leaf is the most recently
    // pushed frame, the root is the first one.
    //
    // Reading the fixed stack from a signal handler is safe: the owning
    // thread is the only writer, we acquire-load the size, and frames
    // below that size are guaranteed to have been fully written before the
    // size store was released.
    u32 frame_count = 0;
    auto* state = Core::t_profiler_state;
    if (!state) {
        tick.frame_count = 0;
        return;
    }

    auto const& stack = state->profiling_stack;
    auto const stack_size = stack.size();

    for (u32 i = 0; i < stack_size && frame_count < MAX_STACK_DEPTH; ++i) {
        // Highest index = most recently pushed = innermost frame.
        auto const& src = stack.at(stack_size - 1 - i);
        auto& dst = tick.frames[frame_count++];

        if (src.js_context != nullptr) {
            // JS frame. js_context points at a live ExecutionContext on
            // the interpreter stack — read its executable through the
            // pointer so we see the latest value (the script context is
            // pushed *before* run_executable assigns context.executable).
            auto const* ec = static_cast<ExecutionContext const*>(src.js_context);
            auto const* executable = ec->executable.ptr();
            if (!executable) {
                // Context is on the interpreter stack but its executable
                // hasn't been bound yet — skip this frame in the sample.
                --frame_count;
                continue;
            }

            // Reject obviously bad pointers (GC cells are 8-byte aligned).
            if (bit_cast<uintptr_t>(executable) & 0x7) {
                --frame_count;
                continue;
            }

            // Topmost frame: prefer the register-derived leaf PC when the
            // platform sampler supplied one (macOS Mach / Linux ucontext),
            // since exec_ctx->program_counter may lag the ASM interpreter
            // by a few instructions.
            u32 program_counter;
            if (frame_count == 1 && leaf_program_counter.has_value())
                program_counter = *leaf_program_counter;
            else if (src.pc_ptr != nullptr)
                program_counter = *src.pc_ptr;
            else
                program_counter = 0;

            dst.executable = bit_cast<FlatPtr>(executable);
            dst.program_counter = program_counter;
            dst.name = executable->name;
            dst.marker_name_ptr = nullptr;
            dst.marker_name_len = 0;
        } else {
            // Label frame.
            dst.executable = 0;
            dst.program_counter = static_cast<u32>(to_underlying(src.category));
            dst.name = Utf16FlyString {};
            dst.marker_name_ptr = src.name.characters_without_null_termination();
            dst.marker_name_len = static_cast<u32>(src.name.length());
        }
    }

    tick.frame_count = frame_count;
}

void JSStackSampler::do_capture_sample(Optional<u32> leaf_program_counter)
{
    auto index = m_raw_sample_count.load(AK::MemoryOrder::memory_order_relaxed);
    if (index >= MAX_RAW_SAMPLES || !m_raw_samples)
        return;

    auto& tick = m_raw_samples[index];
    tick.time_ms = elapsed_ms_since_start();
    capture_frames(tick, leaf_program_counter);
    m_raw_sample_count.store(index + 1, AK::MemoryOrder::memory_order_relaxed);
}

void JSStackSampler::capture_sample(Core::ProfiledThread& output, Optional<u32> leaf_program_counter)
{
    // The output is driven by set_profiled_thread() today — step 6 wires
    // it through the SamplingHandle so the platform sampler picks the
    // target directly. Until then we just verify they agree.
    VERIFY(&output == m_profiled_thread);
    do_capture_sample(leaf_program_counter);
}

void JSStackSampler::process_raw_samples()
{
    VERIFY(m_profiled_thread);
    auto count = min(m_raw_sample_count.load(AK::MemoryOrder::memory_order_relaxed), MAX_RAW_SAMPLES);
    m_profiled_thread->samples.ensure_capacity(count);

    for (u32 i = 0; i < count; ++i) {
        auto const& tick = m_raw_samples[i];
        if (tick.frame_count > MAX_STACK_DEPTH)
            continue;
        // Empty-stack samples are kept as IDLE markers (no stack index).
        // Dropping them would make the activity timeline read 100% busy
        // even when the thread was waiting in epoll_wait between events.
        if (tick.frame_count == 0) {
            m_profiled_thread->samples.append({ tick.time_ms, {} });
            continue;
        }
        m_profiled_thread->samples.append({ tick.time_ms, intern_stack_trace(tick) });
    }
}

// Capture a stack snapshot suitable for the gecko marker `cause` field by
// walking the unified profiling stack. Includes both JS frames and label
// frames in the order they were pushed, so a marker fired while no JS is
// running still gets a meaningful cause from the active label scopes.
//
// Safe to call from any thread that has its own ThreadProfilerState — the
// fixed profiling stack is per-thread and only the owning thread writes to
// it. Allocates per call (we materialize String location names) so it must
// only be called outside the signal-safe path.
void JSStackSampler::capture_marker_stack(Vector<Core::MarkerStackFrame, 8>& out)
{
    out.clear_with_capacity();

    auto* state = Core::t_profiler_state;
    if (!state)
        return;

    auto const& stack = state->profiling_stack;
    auto const stack_size = stack.size();

    for (u32 i = 0; i < stack_size && out.size() < MAX_STACK_DEPTH; ++i) {
        // Highest index = most recently pushed = innermost (leaf) frame.
        auto const& src = stack.at(stack_size - 1 - i);

        String location;
        u32 line = 0;
        u32 column = 0;

        if (src.js_context != nullptr) {
            // JS frame — read the executable through the live context.
            auto const* ec = static_cast<ExecutionContext const*>(src.js_context);
            auto const* executable = ec->executable.ptr();
            if (!executable)
                continue;
            if (bit_cast<uintptr_t>(executable) & 0x7)
                continue;

            String filename;
            u32 program_counter = src.pc_ptr ? *src.pc_ptr : 0;
            if (program_counter < executable->bytecode.size()) {
                auto unrealized = executable->source_range_at(program_counter);
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

            location = filename.is_empty()
                ? function_name
                : MUST(String::formatted("{} ({}:{}:{})", function_name, filename, line, column));
        } else {
            // Label frame.
            location = MUST(String::from_utf8(src.name));
        }

        out.append({ move(location), line, column });
    }
}

u32 JSStackSampler::intern_stack_trace(RawSample const& tick)
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

        auto frame_index = m_profiled_thread->intern_frame(location, line, column, category);
        u64 key = (static_cast<u64>(frame_index) << 32) | prefix.value_or(UINT32_MAX);

        if (auto it = m_profiled_thread->stack_map.find(key); it != m_profiled_thread->stack_map.end()) {
            prefix = it->value;
        } else {
            u32 stack_index = m_profiled_thread->stack_table.size();
            m_profiled_thread->stack_table.append({ frame_index, prefix });
            m_profiled_thread->stack_map.set(key, stack_index);
            prefix = stack_index;
        }
    }

    return prefix.value_or(0);
}

}
