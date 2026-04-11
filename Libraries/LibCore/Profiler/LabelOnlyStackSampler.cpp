/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Profiler/Label.h>
#include <LibCore/Profiler/LabelOnlyStackSampler.h>
#include <LibCore/Profiler/ProfiledThread.h>

namespace Core {

void LabelOnlyStackSampler::capture_sample(ProfiledThread& output, Optional<u32>)
{
    // Step 5 placeholder — the real raw-sample buffer and processing
    // path lands in step 6 when the platform sampler is generalized.
    // For now we just read the current thread's label stack; the size
    // observation is enough to keep the fixed stack's acquire-ordered
    // size load from being optimized away in debug builds.
    (void)output;
    if (auto* state = t_profiler_state)
        (void)state->profiling_stack.size();
}

}
