/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <AK/Optional.h>
#include <AK/Types.h>
#include <LibCore/Export.h>

namespace Core {

class ProfiledThread;

// StackSampler — abstract interface for capturing one sample for a single
// thread. Concrete impls:
//   - JSStackSampler (currently JS::Profiler): walks the JS execution
//     context stack plus the calling thread's FixedProfilingStack.
//   - LabelOnlyStackSampler: walks only the FixedProfilingStack, used
//     for worker and service threads that have no JS runtime.
//
// capture_sample() MUST be async-signal-safe on the Linux signal path:
// no allocation, no locking, no non-reentrant pthread calls. Reads from
// pre-allocated per-thread storage and writes into a pre-allocated raw
// buffer on the ProfiledThread.
class CORE_API StackSampler {
public:
    virtual ~StackSampler() = default;

    // leaf_program_counter: register-derived bytecode PC for the topmost
    // frame (macOS Mach / Windows SuspendThread path). Absent on the
    // Linux signal path where the interpreter's ctx->program_counter
    // serves the same purpose.
    virtual void capture_sample(ProfiledThread& output, Optional<u32> leaf_program_counter) = 0;

    // True when the sampler relies on the bytecode interpreter polling a
    // "sample pending" flag at safe points instead of reading the PC
    // from registers/ucontext. The current LibJS timed-sampling path on
    // Linux reads from ucontext, so this is false there.
    virtual bool needs_bytecode_safe_points() const { return false; }

    // True when the platform sampler should pass a register-derived leaf
    // PC into capture_sample(). macOS and Windows use this; Linux with a
    // register-readable ucontext does too.
    virtual bool wants_register_pc() const { return false; }
};

}
