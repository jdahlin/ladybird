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
#include <pthread.h>
#include <signal.h>
#include <ucontext.h>
#include <unistd.h>

// ASM interpreter register conventions (callee-saved, survive C++ calls):
//   x86_64:  r13 = bytecode PC
//   aarch64: x21 = pb+pc, x26 = pb  =>  pc = x21 - x26
static Optional<u32> read_program_counter_from_ucontext(ucontext_t const* uc)
{
#if defined(__x86_64__)
    auto r13 = static_cast<u64>(uc->uc_mcontext.gregs[REG_R13]);
    return static_cast<u32>(r13);
#elif defined(__aarch64__)
    auto x21 = static_cast<u64>(uc->uc_mcontext.regs[21]);
    auto x26 = static_cast<u64>(uc->uc_mcontext.regs[26]);
    return static_cast<u32>(x21 - x26);
#else
    (void)uc;
    return {};
#endif
}

namespace Core {

// Single-target driver state — replaced by a session-owned list of
// SamplingHandles when step 6b lands.
static pthread_t s_target_thread {};
static int s_interval_us { 0 };
static struct sigaction s_old_sigaction {};
static Atomic<bool> s_timer_running { false };
static bool s_active { false };
static pthread_t s_timer_thread {};
static bool s_timer_thread_joinable { false };

static void* timer_thread_main(void*)
{
    pthread_setname_np(pthread_self(), "Profiler Timer");
    while (s_timer_running.load(AK::MemoryOrder::memory_order_relaxed)) {
        usleep(s_interval_us);
        pthread_kill(s_target_thread, SIGUSR2);
    }
    return nullptr;
}

// Three-step signal handler: t_profiler_state → active_sampling_handle
// → StackSampler::capture_sample(). No HashMap lookups, no locks, no
// allocation — everything was resolved at session start.
static void platform_sampler_signal_handler(int, siginfo_t*, void* ucontext)
{
    auto* state = t_profiler_state;
    if (!state)
        return;
    auto* handle = state->active_sampling_handle.load(AK::MemoryOrder::memory_order_acquire);
    if (!handle)
        return;
    if (handle->in_handler.exchange(true, AK::MemoryOrder::memory_order_acquire))
        return; // re-entry; drop this sample
    auto pc = read_program_counter_from_ucontext(static_cast<ucontext_t const*>(ucontext));
    handle->sampler->capture_sample(*handle->profiled, pc);
    handle->in_handler.store(false, AK::MemoryOrder::memory_order_release);
}

bool PlatformSampler::start(Target target)
{
    if (s_active || target.interval_us <= 0)
        return false;
    s_active = true;
    s_target_thread = target.thread;
    s_interval_us = target.interval_us;

    // Publish the handle into the calling thread's profiler state with
    // release ordering so the signal handler's acquire load sees a fully
    // initialized SamplingHandle.
    VERIFY(t_profiler_state);
    t_profiler_state->active_sampling_handle.store(target.handle, AK::MemoryOrder::memory_order_release);

    struct sigaction sa = {};
    sa.sa_sigaction = platform_sampler_signal_handler;
    sa.sa_flags = SA_SIGINFO | SA_RESTART;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGUSR2, &sa, &s_old_sigaction);

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

    // Block SIGUSR2, drain any signal queued just before the timer thread
    // exited, then restore the previous handler. Without the drain a
    // pending SIGUSR2 delivered after restoration would use the old
    // handler — potentially SIG_DFL (fatal).
    sigset_t only_usr2;
    sigset_t old_mask;
    sigemptyset(&only_usr2);
    sigaddset(&only_usr2, SIGUSR2);
    pthread_sigmask(SIG_BLOCK, &only_usr2, &old_mask);
    struct timespec zero = { 0, 0 };
    sigtimedwait(&only_usr2, nullptr, &zero);
    sigaction(SIGUSR2, &s_old_sigaction, nullptr);
    pthread_sigmask(SIG_SETMASK, &old_mask, nullptr);

    // Clear the target thread's active handle. This is a normal atomic
    // write to shared per-thread state — the target thread's TLS slot
    // itself is not modified.
    if (t_profiler_state)
        t_profiler_state->active_sampling_handle.store(nullptr, AK::MemoryOrder::memory_order_release);

    s_active = false;
}

bool PlatformSampler::is_active()
{
    return s_active;
}

}
