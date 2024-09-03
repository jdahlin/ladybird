/*
 * Copyright (c) 2024, Johan Dahlin <jdahlin@gmail.com>
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

#include <AK/String.h>
#include <LibJS/Forward.h>
#include <LibJS/Runtime/Object.h>
#include <LibWeb/DOM/Node.h>
#include <LibWeb/Forward.h>

#pragma once

namespace Web::DOM {

class XPathResult final : public JS::Object {
    JS_OBJECT(XPathResult, JS::Object);
    JS_DECLARE_ALLOCATOR(XPathResult);
public:
    virtual ~XPathResult() override = default;

    [[nodiscard]] static JS::NonnullGCPtr<XPathResult> create(JS::Realm&, String const& expression);
    XPathResult(JS::Realm&);

    unsigned short result_type() const { return m_result_type; }
    double number_value() const { return m_number_value; }
    String string_value() const { return m_string_value; }
    bool boolean_value() const { return m_boolean_value; }
    Node* single_node_value() const { return m_single_node_value; }
    bool invalid_iterator_state() const { return m_invalid_iterator_state; }
    unsigned long snapshot_length() const { return m_snapshot_length; }
    Node* iterate_next() { return nullptr; }
    Node* snapshot_item([[maybe_unused]] unsigned long index) { return nullptr; }

private:
    unsigned short m_result_type { 0 };
    double m_number_value { 0 };
    String m_string_value;
    bool m_boolean_value { false };
    Node* m_single_node_value { nullptr };
    bool m_invalid_iterator_state { false };
    unsigned long m_snapshot_length { 0 };

    virtual void visit_edges(Cell::Visitor&) override;

};

}
