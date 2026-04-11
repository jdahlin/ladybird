/*
 * Copyright (c) 2026, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/Atomic.h>
#include <LibCore/Profiler/Label.h>
#include <LibCore/Profiler/PlatformSampler.h>
#include <LibCore/Profiler/ProfiledThread.h>
#include <LibCore/Profiler/StackSampler.h>
#include <mach/mach.h>
#include <pthread.h>
#include <unistd.h>

// ASM interpreter register conventions (callee-saved, survive C++ calls):
//   aarch64: x26 = bytecode base (pb), x21 = pb+pc  =>  pc = x21 - x26
//   x86_64:  r13 = pc
static Optional<u32> read_program_counter_from_suspended_thread(mach_port_t mach_thread)
{
#if defined(__aarch64__)
    arm_thread_state64_t state {};
    mach_msg_type_number_t count = ARM_THREAD_STATE64_COUNT;
    bool ok = thread_get_state(mach_thread, ARM_THREAD_STATE64, reinterpret_cast<thread_state_t>(&state), &count) == KERN_SUCCESS;
    return ok ? Optional<u32>(static_cast<u32>(state.__x[21] - state.__x[26])) : Optional<u32> {};
#elif defined(__x86_64__)
    x86_thread_state64_t state {};
    mach_msg_type_number_t count = x86_THREAD_STATE64_COUNT;
    bool ok = thread_get_state(mach_thread, x86_THREAD_STATE64, reinterpret_cast<thread_state_t>(&state), &count) == KERN_SUCCESS;
    return ok ? Optional<u32>(static_cast<u32>(state.__r13)) : Optional<u32> {};
#else
    return {};
#endif
}

namespace Core {

static SamplingHandle* s_handle { nullptr };
static pthread_t s_target_thread {};
static mach_port_t s_mach_thread {};
static int s_interval_us { 0 };
static Atomic<bool> s_timer_running { false };
static bool s_active { false };
static pthread_t s_timer_thread {};
static bool s_timer_thread_joinable { false };

static void* timer_thread_main(void*)
{
    pthread_setname_np("Profiler Timer");
    while (s_timer_running.load(AK::MemoryOrder::memory_order_relaxed)) {
        usleep(s_interval_us);
        if (thread_suspend(s_mach_thread) != KERN_SUCCESS)
            continue;
        if (s_handle) {
            auto pc = read_program_counter_from_suspended_thread(s_mach_thread);
            if (pc.has_value())
                s_handle->sampler->capture_sample(*s_handle->profiled, pc);
        }
        thread_resume(s_mach_thread);
    }
    return nullptr;
}

bool PlatformSampler::start(Target target)
{
    if (s_active || target.interval_us <= 0)
        return false;
    s_active = true;
    s_handle = target.handle;
    s_target_thread = target.thread;
    s_interval_us = target.interval_us;
    s_mach_thread = pthread_mach_thread_np(s_target_thread);

    VERIFY(t_profiler_state);
    t_profiler_state->active_sampling_handle.store(target.handle, AK::MemoryOrder::memory_order_release);

    s_timer_running.store(true, AK::MemoryOrder::memory_order_relaxed);
    if (pthread_create(&s_timer_thread, nullptr, timer_thread_main, nullptr) != 0) {
        s_active = false;
        return false;
    }
    s_timer_thread_joinable = true;
    return true;
}

void PlatformSampler::stop()
{
    if (!s_active)
        return;

    s_timer_running.store(false, AK::MemoryOrder::memory_order_relaxed);
    if (s_timer_thread_joinable) {
        pthread_join(s_timer_thread, nullptr);
        s_timer_thread_joinable = false;
    }

    if (t_profiler_state)
        t_profiler_state->active_sampling_handle.store(nullptr, AK::MemoryOrder::memory_order_release);

    s_handle = nullptr;
    s_active = false;
}

bool PlatformSampler::is_active()
{
    return s_active;
}

}
