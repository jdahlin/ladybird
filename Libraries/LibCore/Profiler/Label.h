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

// One entry on the FixedProfilingStack. The name is a raw StringView because
// PROFILER_LABEL must be callable from contexts that cannot allocate (signal
// handlers observe the stack from the sampler side). Callers MUST pass a
// string literal or other memory that outlives the scope.
struct ProfilingStackFrame {
    StringView name;
    MarkerCategory category;
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
        m_frames[current] = { name, category };
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
