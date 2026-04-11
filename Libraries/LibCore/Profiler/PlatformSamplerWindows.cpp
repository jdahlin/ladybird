/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibCore/Profiler/PlatformSampler.h>

namespace Core {

// TODO: Windows needs SuspendThread / GetThreadContext / ResumeThread driven
// by a timer thread, mirroring the macOS path. Until then timed sampling is
// unsupported on Windows; the bytecode interpreter's safe-point path still
// works via request_sample_for_test in the sampler itself.
bool PlatformSampler::start(Target) { return false; }
void PlatformSampler::stop() { }
bool PlatformSampler::is_active() { return false; }

}
