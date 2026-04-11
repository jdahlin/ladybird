/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/Atomic.h>
#include <AK/Platform.h>
#include <AK/StringView.h>
#include <AK/Types.h>
#include <LibCore/Export.h>
#include <LibCore/MarkerCategory.h>
#include <LibCore/Profiler/SamplingHandle.h>

namespace Core {

// One entry on the FixedProfilingStack. Two flavors share the same slot:
//
//   - Label frames carry a literal name + category. PROFILER_LABEL and
//     MARKER_SCOPE push these. `js_context` is null.
//
//   - JS frames carry an opaque pointer to a JS::ExecutionContext (which
//     LibCore intentionally doesn't know about) plus a pointer to the live
//     ExecutionContext::program_counter. Pushed and popped by the bytecode
//     interpreter at every JS function entry/exit so the unified stack
//     interleaves correctly with label scopes — that's what makes JS↔C++
//     alternation render right in the call tree. The sampler reads the
//     executable from the live ExecutionContext at sample time, which
//     handles the case where the context is pushed before its executable
//     field is assigned.
//
// All fields must be readable from a signal handler: no allocation, no
// refcount bumps. The ExecutionContext lives as long as the profiling
// stack frame because both are managed by the JS interpreter — push and
// pop are structurally symmetric.
struct ProfilingStackFrame {
    StringView name;
    MarkerCategory category;
    void const* js_context { nullptr };
    u32 const* pc_ptr { nullptr };
};

// Signal-safe fixed-capacity stack of active profiler labels.
//
// The owning thread is the only writer. Signal handlers and cross-thread
// samplers are the only readers. The stack pointer is published with
// release/acquire ordering so a reader that observes `size == N` is
// guaranteed to see frames[0..N) as fully written by the writer before N
// was published.
class CORE_API FixedProfilingStack {
public:
    static constexpr u32 CAPACITY = 64;

    ALWAYS_INLINE void push(StringView name, MarkerCategory category)
    {
        auto current = m_size.load(AK::MemoryOrder::memory_order_relaxed);
        if (current >= CAPACITY) {
            ++m_overflow_depth;
            return;
        }
        m_frames[current] = { name, category, nullptr, nullptr };
        m_size.store(current + 1, AK::MemoryOrder::memory_order_release);
    }

    // JS frame push: stores an opaque ExecutionContext pointer and a
    // pointer to the live ExecutionContext::program_counter. The
    // interpreter writes the PC field on every dispatch, so the sampler
    // reads the current value by dereferencing pc_ptr.
    ALWAYS_INLINE void push_js(void const* js_context, u32 const* pc_ptr)
    {
        auto current = m_size.load(AK::MemoryOrder::memory_order_relaxed);
        if (current >= CAPACITY) {
            ++m_overflow_depth;
            return;
        }
        m_frames[current] = { {}, MarkerCategory::JavaScript, js_context, pc_ptr };
        m_size.store(current + 1, AK::MemoryOrder::memory_order_release);
    }

    ALWAYS_INLINE void pop()
    {
        if (m_overflow_depth > 0) {
            --m_overflow_depth;
            return;
        }
        auto current = m_size.load(AK::MemoryOrder::memory_order_relaxed);
        if (current == 0)
            return;
        m_size.store(current - 1, AK::MemoryOrder::memory_order_release);
    }

    ALWAYS_INLINE u32 size() const
    {
        return m_size.load(AK::MemoryOrder::memory_order_acquire);
    }

    ALWAYS_INLINE ProfilingStackFrame const& at(u32 index) const
    {
        return m_frames[index];
    }

private:
    Atomic<u32> m_size { 0 };
    // Counts pushes that didn't fit so the matching pops don't underflow the
    // visible stack. Written only from the owning thread, so no atomic needed.
    u32 m_overflow_depth { 0 };
    ProfilingStackFrame m_frames[CAPACITY];
};

// Permanent per-thread profiler state. Exists independently of any active
// session. Created lazily on first profiler op from the owning thread.
struct ThreadProfilerState {
    FixedProfilingStack profiling_stack;
    // Set by ProfilerSession::start() with release ordering before the
    // timer thread can deliver a signal; cleared on session stop after
    // signals are drained. The platform sampler's signal handler reads
    // this with acquire ordering and dispatches through it — no further
    // lookups, no locks, no allocation.
    Atomic<SamplingHandle*> active_sampling_handle { nullptr };
};

// Thread-local pointer to the owning thread's profiler state. Null until
// ensure_profiler_state() has run on this thread. The pointer is read by
// the signal handler to decide whether this thread is registered.
extern CORE_API thread_local ThreadProfilerState* t_profiler_state;

// Publish (and return) the calling thread's profiler state, creating it
// on first access. Not signal-safe — call from normal code only.
CORE_API ThreadProfilerState& ensure_profiler_state();

// RAII label scope. Pushes a frame onto the calling thread's profiling
// stack at construction, pops it at destruction. Does NOT emit a marker on
// the marker chart. The frame is read by the sampler and becomes a
// pseudo-frame in the call tree.
class CORE_API ProfilerLabel {
public:
    ALWAYS_INLINE ProfilerLabel(StringView name, MarkerCategory category)
    {
        ensure_profiler_state().profiling_stack.push(name, category);
    }

    ALWAYS_INLINE ~ProfilerLabel()
    {
        if (auto* state = t_profiler_state)
            state->profiling_stack.pop();
    }

    ProfilerLabel(ProfilerLabel const&) = delete;
    ProfilerLabel& operator=(ProfilerLabel const&) = delete;
    ProfilerLabel(ProfilerLabel&&) = delete;
    ProfilerLabel& operator=(ProfilerLabel&&) = delete;
};

}

// PROFILER_LABEL — RAII pseudo-frame for the sampled call tree.
//
// Pushes a frame onto the calling thread's FixedProfilingStack for the
// lifetime of the enclosing scope. Unlike MARKER_SCOPE, does NOT emit an
// interval marker on the marker chart — this is pure call-tree
// attribution.
//
// NAME must be a string literal (or memory that outlives the scope).
#define PROFILER_LABEL_IMPL2(NAME, CATEGORY, COUNTER) \
    ::Core::ProfilerLabel _profiler_label_##COUNTER { NAME, CATEGORY }
#define PROFILER_LABEL_IMPL1(NAME, CATEGORY, COUNTER) PROFILER_LABEL_IMPL2(NAME, CATEGORY, COUNTER)
#define PROFILER_LABEL(NAME, CATEGORY) PROFILER_LABEL_IMPL1(NAME, CATEGORY, __COUNTER__)
