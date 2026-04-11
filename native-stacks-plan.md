# Plan: Native stacks in the Ladybird profiler

## Context

profiler.firefox.com profiles from Firefox interleave native C++ frames
(`__GI___lll_lock_wait`, `js::RunScript`, `XPCWrappedJS method call`, etc.)
with the JS call tree, which makes "where did the time go below the JS leaf"
immediately answerable. Our profiles only have JS frames + label scopes:
once a sample lands inside C++ work that no `PROFILER_LABEL` covers, the
trail goes cold at the deepest JS frame.

A real Firefox profile (`~/Downloads/Firefox 2026-04-11 15.43 profile.json.gz`)
shows two pieces of machinery we don't have yet:

1. A populated top-level `libs[]` array, one entry per loaded shared
   object, with `breakpadId` (GNU build ID), `path`, `debugName`,
   `arch`, and `codeId`. profiler.firefox.com uses this to upload
   addresses to a symbol server and resolve them back to function names
   on demand.
2. A `shared.nativeSymbols` table plus `address` and `nativeSymbol`
   columns on `shared.frameTable`, so a sample frame can carry both a
   raw library-relative address AND a (deferred-resolved) symbol name.

The user wants to "submit the shared libraries so we can get native
stacks better". This plan covers (a) emitting `libs[]` so profiles can
be symbolicated remotely, and (b) actually capturing native PCs at
sample time so the call tree extends below JS into C++.

## Strategy

Three phases, each independently committable. Phase 1 alone unblocks
remote symbolication of any future profile. Phases 2–3 add the actual
native frame capture and benefit fully from Phase 1's library data.

### Phase 1 — Library enumeration + `libs[]` emission (no unwinding)

Build a per-session library registry from `dl_iterate_phdr`, parse each
loaded SO's `.note.gnu.build-id`, and emit a populated gecko `libs[]`
on export. No sampling-path changes — this stands alone.

**New file**: `Libraries/LibCore/Profiler/LibraryRegistry.{h,cpp}`

```cpp
namespace Core {

struct LoadedLibrary {
    String name;          // basename, e.g. "liblagom-js.so.0"
    String path;          // full path
    FlatPtr base_address; // first PT_LOAD vaddr in process address space
    FlatPtr end_address;  // base + max(p_vaddr + p_memsz)
    String breakpad_id;   // uppercase hex(build_id) + "0"
    String code_id;       // lowercase hex(build_id)
    StringView arch;      // "x86_64" / "aarch64"
};

class CORE_API LibraryRegistry {
public:
    // Walks dl_iterate_phdr once and snapshots every loaded library.
    // Cheap to call again — does NOT re-walk on each sample.
    void capture_now();
    Vector<LoadedLibrary> const& libraries() const { return m_libraries; }

    // Address → library lookup. Returns nullopt for addresses outside
    // any loaded SO. Used by Export.cpp to map raw native PCs to
    // (lib_index, relative_offset) pairs.
    Optional<size_t> find_library_for_address(FlatPtr address) const;

private:
    Vector<LoadedLibrary> m_libraries; // sorted by base_address
};

}
```

**Implementation notes**:

- `dl_iterate_phdr` is **not** signal-safe per glibc; we MUST only call it
  from `PageClient::start_profiling` / `js.cpp` profile setup (normal
  thread context, no signal mask). The signal handler reads the cached
  vector by index only.
- Build-ID extraction walks the process-image program headers we already
  receive in the callback. For each `PT_NOTE` entry, walk the in-memory
  notes (`Elf64_Nhdr`) until we find one with `n_type == NT_GNU_BUILD_ID`
  and `n_namesz == 4` and name `"GNU\0"`. Hex-format the descriptor.
  This needs no dlopen and no extra deps — `<elf.h>` only.
- `breakpadId` is uppercase hex of the first 16 bytes of the build ID,
  followed by a single `'0'` character. `codeId` is the full lowercase
  hex.

**`ProfilerSession` change**: own a `LibraryRegistry`, populate it once
in the session ctor (or first sample). Add a `libraries()` accessor.

**`Export.cpp` change**: replace `JsonArray {}` at line 648 with
`build_libs(session)` that walks the registry and emits one JsonObject
per library with the gecko-format fields:
`{ start, end, offset, arch, name, path, debugName, debugPath, breakpadId, codeId }`.

**Verification**: profile a real page, upload the result to
profiler.firefox.com, click on any address-bearing frame and confirm
the symbol-server request fires (it'll fail unless the server has
build IDs for our libs, but the request shape is what matters here).

### Phase 2 — Capture native PCs in the signal handler

Add a frame-pointer-walk path to the Linux + macOS samplers and a
`-fno-omit-frame-pointer` build flag so the walk actually works.

**Build flag**: `Meta/CMake/compile_options.cmake` — promote the
existing `add_cxx_compile_options(-fno-omit-frame-pointer)` (currently
fuzzer-only at line 285) so it always applies. Cost is ~3–5% on x86_64,
acceptable for a debugging-targeted browser. Document in the commit.

**`PlatformSamplerLinux.cpp`** — extend `read_program_counter_from_ucontext`
to also read RIP / PC and frame pointer from the same `ucontext_t`:

```cpp
struct CapturedRegisters {
    Optional<u32> bytecode_pc;  // existing — r13 / x21-x26
    FlatPtr machine_pc;          // new — REG_RIP / regs[32]
    FlatPtr frame_pointer;       // new — REG_RBP / regs[29]
    FlatPtr stack_pointer;       // new — REG_RSP / regs[31]
};
```

Add a free function `walk_frame_pointers(machine_pc, frame_pointer, stack_pointer, FlatPtr* out, size_t out_capacity)` in
`Libraries/LibCore/Profiler/NativeUnwinder.{h,cpp}` (new). It walks
RBP-style stack frames, validating each link against the supplied
stack pointer (must be above current SP, must be aligned, must produce
a return address that's also above SP). Returns the number of PCs
written. Pure code, signal-safe.

**`RawSample` extension** in `Libraries/LibJS/JSStackSampler.h` — add
a fixed-size native PC array next to the existing
`UnprocessedFrame frames[MAX_STACK_DEPTH]`:

```cpp
struct RawSample {
    double time_ms;
    u32 frame_count;                       // existing JS+label frames
    UnprocessedFrame frames[MAX_STACK_DEPTH];
    u32 native_frame_count;                // new
    FlatPtr native_pcs[MAX_NATIVE_DEPTH];  // new (32 PCs is plenty)
};
```

**`JSStackSampler::do_capture_sample`** — accept the new
`CapturedRegisters` argument from the platform sampler, pass it through
to `capture_frames`, which calls `walk_frame_pointers` and stores the
PCs into `tick.native_pcs[]`.

**macOS path** — `PlatformSamplerMacOS.cpp` reads RIP/PC + RBP/x29 from
`x86_thread_state64_t::__rbp` / `arm_thread_state64_t::__fp` and
constructs the same `CapturedRegisters` struct.

**Windows path** — `PlatformSamplerWindows.cpp` already a stub; can
land empty bodies that always return zero native frames; not blocking.

### Phase 3 — Emit native frames in the gecko frameTable

Translate raw native PCs into `(library_index, relative_offset)` pairs
at sample-processing time, store them in a per-thread native symbol
table, and emit `nativeSymbol` + `address` columns on the gecko
frameTable.

**`Core::ProfiledThread`** — add a per-thread native symbol table
mirroring the gecko shape:

```cpp
struct NativeSymbol {
    u32 lib_index;
    FlatPtr relative_address;
    u32 name_string_index; // 0 if unresolved
    Optional<u64> function_size;
};
Vector<NativeSymbol> native_symbol_table;
HashMap<FlatPtr, u32> native_symbol_map; // raw_pc -> index
u32 intern_native_symbol(FlatPtr raw_pc, LibraryRegistry const&);
```

**`JSStackSampler::process_raw_samples`** — for each raw native PC,
call `intern_native_symbol`. If the address falls inside a library:
emit a frame with `executable=NATIVE_SENTINEL`, store the
`native_symbol_table` index. If `dladdr` resolves a name (called once
per unique address at process-time, NOT in the signal handler), store
the demangled name; otherwise leave the name empty so
profiler.firefox.com requests it from the symbol server using the
already-emitted `breakpadId`.

**Frame ordering** — native PCs are walked from the leaf C++ frame
upward. They should appear UNDER the deepest JS frame in the call
tree (i.e., higher indices in the existing root-first storage). Walk
order: existing JS+label frames first (innermost-first), then native
PCs (innermost-first). In `intern_stack_trace` they get processed
high-to-low, so native frames become outer/deeper than JS, which is
correct: C++ called the bytecode interpreter which called the JS
function — the C++ frames are the *outer* call stack.

**`Export.cpp`** — extend `build_frame_table` to add the `address` and
`nativeSymbol` columns; emit `shared.nativeSymbols` (or per-thread if
we don't have a shared table yet — the `shared` block is added by
profiler.firefox.com's converter on import, not by us). Native frames
get `category` = `Other` (grey) until we add a Native category.

## Critical files

| File | Phase | What |
|---|---|---|
| `Libraries/LibCore/Profiler/LibraryRegistry.{h,cpp}` (new) | 1 | dl_iterate_phdr + ELF note parsing |
| `Libraries/LibCore/Profiler/ProfilerSession.{h,cpp}` | 1 | own a LibraryRegistry, populate at session start |
| `Libraries/LibCore/Profiler/Export.cpp` (line 648) | 1 | populate `libs[]` |
| `Libraries/LibCore/CMakeLists.txt` | 1 | add LibraryRegistry.cpp |
| `Meta/CMake/compile_options.cmake` (line 284–286) | 2 | hoist `-fno-omit-frame-pointer` out of the fuzzer-only block |
| `Libraries/LibCore/Profiler/PlatformSamplerLinux.cpp` | 2 | read RIP+RBP from ucontext, build CapturedRegisters |
| `Libraries/LibCore/Profiler/PlatformSamplerMacOS.cpp` | 2 | same via thread_get_state |
| `Libraries/LibCore/Profiler/NativeUnwinder.{h,cpp}` (new) | 2 | signal-safe FP walker |
| `Libraries/LibJS/JSStackSampler.h` | 2 | extend RawSample with native_pcs[] |
| `Libraries/LibJS/JSStackSampler.cpp` | 2,3 | thread CapturedRegisters into capture_frames; intern native PCs in process_raw_samples |
| `Libraries/LibCore/Profiler/ProfiledThread.{h,cpp}` | 3 | native_symbol_table + intern_native_symbol |
| `Libraries/LibCore/Profiler/Export.cpp` | 3 | nativeSymbol/address columns; nativeSymbols emit |

## Reuse

- **Signal handler dispatch**: `platform_sampler_signal_handler` in
  `PlatformSamplerLinux.cpp` already has the `t_profiler_state` →
  `active_sampling_handle` → `StackSampler::capture_sample` chain. Add
  the new register reads next to the existing
  `read_program_counter_from_ucontext` and pass alongside the existing
  bytecode PC.
- **Per-thread cached state**: `Core::ThreadProfilerState` and the
  `t_profiler_state` TLS pointer already exist (`Libraries/LibCore/Profiler/Label.h`).
  The native unwinder doesn't need new TLS — it reads the registers from
  ucontext and walks them in-place.
- **Pre-allocated raw buffer**: The existing `RawSample` array is
  pre-allocated at session start and indexed by an atomic counter — same
  pattern works for the native PC array, just bigger.
- **Process-time post-processing**: `JSStackSampler::process_raw_samples`
  already walks raw samples after the session stops and interns them
  into the ProfiledThread's tables. Symbol resolution via `dladdr` slots
  in here cleanly.
- **Build ID encoding**: standard Linux ELF, no third-party deps.
  `<elf.h>` is already pulled in transitively by libc headers; `Elf64_Nhdr`
  / `NT_GNU_BUILD_ID` / `PT_NOTE` are part of the standard glibc headers.

## Out of scope (deliberately)

- DWARF unwinding via libunwind / `_Unwind_Backtrace`. Not async-signal-safe.
  If frame-pointer walks miss frames in third-party libraries (e.g. Skia,
  Vulkan) compiled without `-fno-omit-frame-pointer`, we accept the
  shorter stack and stop the walk where the FP chain breaks.
- Symbol resolution in the signal handler. `dladdr` is not signal-safe.
  Resolution happens at session-stop time in `process_raw_samples`.
- Inline frame expansion (`inlineDepth` column). Firefox emits this from
  DWARF inline tables — out of scope.
- Cross-process symbolication. Each process emits its own `libs[]`; the
  cross-process merger from the existing rearch plan handles aggregation.
- Windows native unwinding. Stub remains.

## Verification

End-to-end check:

1. `./Meta/ladybird.py build ladybird` — should still build with the
   new flag.
2. `bin/test-js-profiler` — all 9 tests must still pass (no behavior
   change in the JS-only path).
3. `bin/Ladybird --headless=profile --profile-output=/tmp/native.json --profile-duration=15000 https://www.youtube.com`
4. `node ~/profiler/dist/gecko-to-processed-cli.js /tmp/native.json /tmp/native.processed.json`
5. Inspection script:
   ```python
   p = json.load(open('/tmp/native.processed.json'))
   assert len(p['libs']) > 0, "Phase 1 broken"
   assert any(lib['name'] == 'liblagom-js.so.0' for lib in p['libs'])
   for lib in p['libs']:
       assert len(lib['breakpadId']) == 33  # 32 hex + '0'
       assert lib['breakpadId'] == lib['breakpadId'].upper()
   # Phase 2/3:
   ft = p['shared']['frameTable']
   assert 'address' in ft and 'nativeSymbol' in ft
   native_count = sum(1 for a in ft['address'] if a is not None and a >= 0)
   assert native_count > 0, "no native frames captured"
   # Find a sample whose chain has both JS and native frames
   ```
6. Drag `/tmp/native.json` into profiler.firefox.com and confirm:
   - The "System" panel shows our libraries
   - At least one stack in the call tree contains a native frame below
     a JS leaf
   - Clicking a native frame triggers a symbolication request
     (visible in the network tab)

Phase 1 is independently shippable: if `libs[]` is populated and the
shape matches Firefox's, profiler.firefox.com will accept it without
any of phase 2/3 in place.
