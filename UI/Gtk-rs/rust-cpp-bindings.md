# Rust/C++ Binding Notes

This note is aimed at contributors who already know GObject-introspection,
manually written bindings, or dynamic-language binding layers, and need the
specific Rust/C++ mental model for this GTK-rs frontend.

The short version: GObject/Python bindings are usually runtime/object-model
bindings. C++/Rust integration is mostly ABI, ownership, lifetime, and unsafe
boundary engineering.

## Rust Does Not Bind C++ ABI Directly

Rust has stable interop with C ABI, not C++ ABI.

For C++ integration, common options are:

- a manually written `extern "C"` C shim
- the `cxx` crate, which generates a constrained Rust/C++ bridge
- `bindgen` over C-compatible headers
- C ABI plus opaque handles

This frontend currently uses a manual C shim:

```cpp
extern "C" LadybirdView* ladybird_view_create(...);
```

Rust sees that as:

```rust
unsafe extern "C" {
    fn ladybird_view_create(...) -> *mut LadybirdView;
}
```

The real public contract is therefore not the C++ class. It is the C ABI we
design.

## ABI Layout Matters

With GObject introspection, metadata carries a lot of information: object type,
transfer ownership, nullable, element type, signals, properties.

With Rust FFI, none of that exists unless we encode it.

Be explicit about:

- `#[repr(C)]` on Rust structs passed over FFI
- matching field order and field types
- `bool` size and representation across C++/Rust
- `size_t` vs `usize`
- `int` vs `i32`
- pointer mutability
- nullable pointers
- borrowed vs owned pointers
- pointer validity duration
- who frees memory
- thread affinity

If C++ says "pointer valid for this callback only", Rust should usually copy the
data immediately into an owned Rust type.

## What `unsafe` Means

In Rust, `unsafe` does not mean "this code is bad". It means "the compiler
cannot check the contract here".

For example:

```rust
std::slice::from_raw_parts(ptr, len)
```

asserts all of the following:

- `ptr` is non-null if `len > 0`
- memory is valid for `len` elements
- memory is properly aligned
- memory is not mutated incompatibly while borrowed
- the returned lifetime does not outlive the source allocation

The useful pattern is:

```rust
unsafe extern "C" fn callback(ptr: *const RawItem, len: usize) {
    let owned = unsafe { decode_items(ptr, len) };
    safe_handler(owned);
}
```

The unsafe part should be tiny and should convert into safe Rust quickly.

## Ownership Must Be Designed

Python bindings often lean on reference counting, GObject transfer annotations,
or wrapper ownership conventions. Rust needs a precise ownership model.

For opaque C++ objects, an owned wrapper usually looks like:

```rust
pub struct View(NonNull<LadybirdView>);

impl Drop for View {
    fn drop(&mut self) {
        unsafe { ladybird_view_destroy(self.0.as_ptr()) }
    }
}
```

That means Rust owns the C++ object and destroys it.

Borrowed objects need a different model. Do not use the same wrapper for owned
and borrowed pointers unless the lifetime contract is explicit.

Useful distinction:

```rust
struct OwnedView(NonNull<RawView>);
struct BorrowedView<'a>(NonNull<RawView>, PhantomData<&'a RawView>);
```

We may not need both immediately, but the mental model matters.

## Lifetimes Do Not Cross FFI Automatically

Rust lifetimes are compile-time only. C++ does not know them.

This is a reasonable safe helper because it copies:

```rust
fn cstr_to_string(ptr: *const c_char, len: usize) -> String
```

This is dangerous as a safe public API:

```rust
fn ffi_slice<'a, T>(ptr: *const T, len: usize) -> &'a [T]
```

A safe caller can invent any lifetime. Such a function should be `unsafe`,
private, or should immediately copy into an owned `Vec<T>`.

Rule of thumb: safe FFI-facing helpers should return owned data, not borrowed
data, unless the borrow is tied to an actual Rust owner.

## Panics Must Not Cross C/C++

Rust panics must not unwind through C/C++ FFI unless using a carefully designed
unwind ABI. For this frontend, callbacks should not unwind into C++.

Callbacks from C++ into Rust should either never panic by construction or catch
panic and abort/log.

Pattern:

```rust
unsafe extern "C" fn callback(...) {
    if std::panic::catch_unwind(|| {
        safe_callback_body(...);
    }).is_err() {
        std::process::abort();
    }
}
```

This differs from Python exceptions, where a binding layer usually has an
exception propagation convention.

## Threading And Main Context

GTK/GObject has main-thread assumptions, and Rust makes thread safety visible
through `Send` and `Sync`.

Types like `Rc`, `RefCell`, GTK widgets, and many gtk-rs objects are not freely
sendable across threads. That is useful: Rust often stops accidental
cross-thread use.

For FFI callbacks, document and guarantee:

- are callbacks called only on the GTK main thread?
- can C++ call during destruction?
- can callbacks be reentrant?
- can callbacks happen after Rust unregisters state?
- can a callback synchronously destroy the view?

These questions matter for `Rc<RefCell<_>>`, tab/window state, and registry
cleanup.

## Callback Lifetime Ownership

In Python/GObject, callback lifetime often follows signal connection IDs or
closure references.

For C++/Rust, define it explicitly.

Common pattern:

- Rust creates state
- Rust passes an ID or context pointer to C++
- C++ stores callbacks
- C++ calls back later with that ID/pointer
- Rust looks up state

The key invariant is: C++ must not call after Rust state is removed.

Better patterns include:

- RAII registration guards
- explicit destroy/unregister ordering
- weak handles that safely no-op after destruction
- C++ `RsView` destructor clearing callbacks before anything can fire

## Strings Are Usually Pointer Plus Length

Rust `&str` is UTF-8 bytes plus length. It is not null-terminated.

C++ `StringView` is also pointer plus length, which is a good match.

Prefer ABI shape:

```c
char const* ptr, size_t len
```

over null-terminated strings where possible.

Use `CString` only when C++ expects null-terminated strings. Watch for embedded
NULs.

For outbound Rust -> C++ calls, passing `s.as_ptr(), s.len()` is fine if C++
copies or consumes the data during the call and does not store the pointer.

## Error Handling Needs An ABI Convention

Rust `Result<T, E>` cannot cross C ABI directly.

Pick explicit conventions:

- return nullable pointer for creation failure
- return `bool` plus out-param
- return integer status code
- return owned error string through explicit free function
- panic/abort only for impossible invariants

For UI bridge code, nullable pointer or boolean is often enough, but document the
contract.

## Generated Bindings Are Not Automatically Safe

`bindgen` gives raw Rust declarations. It does not make them safe.

`cbindgen` gives C/C++ headers from Rust types. It does not prove the API is
sound.

Still build a safe wrapper layer:

```rust
mod raw {
    unsafe extern "C" {
        // raw declarations
    }
}

pub struct View {
    // owned handle
}

impl View {
    pub fn load(&self, url: &str) {
        // safe wrapper around raw FFI
    }
}
```

This is analogous to writing a Python extension wrapper, except Rust draws a
hard line between raw and safe APIs.

## Learn `cxx` Even If We Do Not Adopt It

For C++/Rust integration, the `cxx` crate is worth knowing as a design reference.

It provides:

- generated bridge code
- safer shared types for strings/vectors/unique pointers
- less hand-written ABI layout
- constraints that avoid many bad C++/Rust interop patterns

It is opinionated and may not fit Ladybird's build or style right now, but it is
useful for understanding what a safer C++/Rust bridge can look like.

## GObject Knowledge Still Transfers

GObject-introspection and manual binding experience helps with:

- naming stable APIs
- transfer ownership thinking
- nullable annotations
- boxed vs object types
- signal/callback lifetime
- main loop integration
- widget subclassing
- action/menu modeling

The difference is that Rust wants these annotations encoded in types, not only
in documentation.

Examples:

- nullable -> `Option<T>`
- transfer full -> owned type with `Drop`
- transfer none -> borrowed reference with lifetime
- callback may outlive object -> weak reference or registration token
- main-thread-only -> non-`Send` state, GTK object ownership

## What To Learn First For This Codebase

1. Rust FFI basics: `extern "C"`, `#[repr(C)]`, opaque types, `NonNull`, `Drop`.
2. Unsafe API design: how to turn unsafe raw calls into safe wrappers.
3. Lifetimes and ownership around borrowed vs owned FFI data.
4. Panic/unwind rules across FFI.
5. gtk-rs subclassing and `CompositeTemplate`.
6. `gio::Action`, `glib::WeakRef`, `Rc<RefCell<T>>`, and main-context async.
7. Optional: `cxx` crate as a safer C++ bridge model.

## Practical Mantra

C++ owns behavior. Rust owns safety boundaries.

For the parity phase, keep the C++ object and method shape. At every FFI edge,
force data into Rust-owned, typed, safe structures as early as possible.
