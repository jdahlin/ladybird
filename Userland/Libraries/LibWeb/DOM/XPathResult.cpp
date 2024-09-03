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

JS::NonnullGCPtr<XPathResult> XPathResult::create(JS::Realm& realm, String const& expression)
{
    return realm.heap().allocate<XPathResult>(realm, realm, expression);
}

XPathResult::XPathResult(JS::Realm& realm, String const& expression)
    : JS::Object(ConstructWithPrototypeTag::Tag, realm.intrinsics().object_prototype())
    , m_expression(expression)
{
}

void XPathResult::visit_edges(Cell::Visitor& visitor)
{
    Base::visit_edges(visitor);
}

}
