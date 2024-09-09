/*
 * Copyright (c) 2022, Andreas Kling <kling@serenityos.org>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <LibJS/Heap/Heap.h>
#include <LibJS/Runtime/Error.h>
#include <LibWeb/DOM/XPathResult.h>

namespace Web::DOM {

JS_DEFINE_ALLOCATOR(XPathResult);

JS::NonnullGCPtr<XPathResult> XPathResult::create(JS::Realm& realm)
{
    return realm.heap().allocate<XPathResult>(realm, realm);
}

XPathResult::XPathResult(JS::Realm& realm)
    : JS::Object(ConstructWithPrototypeTag::Tag, realm.intrinsics().object_prototype())
{
}

void XPathResult::visit_edges(Cell::Visitor& visitor)
{
    Base::visit_edges(visitor);
}

}
