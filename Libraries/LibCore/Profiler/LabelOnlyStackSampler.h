/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#pragma once

#include <LibCore/Export.h>
#include <LibCore/Profiler/StackSampler.h>

namespace Core {

// Minimum-viable stack sampler for threads with no JS runtime. It only
// walks the calling thread's FixedProfilingStack — PROFILER_LABEL frames
// show up in the call tree, C++ call chain does not (native unwinding is
// out of scope).
//
// Used for ThreadPool workers, RequestServer/ImageDecoder/Browser/
// WebWorker main threads, and every other registered non-JS thread.
//
// In step 5 the sampler exists and compiles; the per-thread raw buffer
// and platform-sampler wiring that make it actually record samples land
// in step 6.
class CORE_API LabelOnlyStackSampler : public StackSampler {
public:
    virtual ~LabelOnlyStackSampler() override = default;

    virtual void capture_sample(ProfiledThread& output, Optional<u32> leaf_program_counter) override;
};

}
