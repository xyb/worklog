"""TOON encoder conformance tests — pinned against the official spec v3.3
Appendix A examples (github.com/toon-format/spec) plus wl-specific shapes.

TOON = Token-Oriented Object Notation: a compact, LLM-friendly JSON alternative.
The encoder is hand-rolled (zero deps, principle G3); these tests are the contract.
"""
import pytest

from worklog.toon import encode


class TestObjects:
    def test_flat_object(self):
        assert encode({"id": 123, "name": "Ada", "active": True}) == \
            "id: 123\nname: Ada\nactive: true"

    def test_nested_object(self):
        assert encode({"user": {"id": 123, "name": "Ada"}}) == \
            "user:\n  id: 123\n  name: Ada"

    def test_empty_object_is_empty_document(self):
        assert encode({}) == ""

    def test_empty_nested_object_is_bare_key(self):
        assert encode({"type": {}, "id": 1}) == "type:\nid: 1"

    def test_deep_nesting(self):
        v = {"root": {"level1": {"level2": {"level3": {
            "items": [{"id": 1, "val": "a"}, {"id": 2, "val": "b"}]}}}}}
        assert encode(v) == (
            "root:\n  level1:\n    level2:\n      level3:\n"
            "        items[2]{id,val}:\n          1,a\n          2,b"
        )


class TestPrimitiveArrays:
    def test_inline(self):
        assert encode({"tags": ["admin", "ops", "dev"]}) == "tags[3]: admin,ops,dev"

    def test_empty_array_field(self):
        assert encode({"tags": []}) == "tags: []"

    def test_numbers_in_array(self):
        assert encode({"scores": [95, 87, 92]}) == "scores[3]: 95,87,92"


class TestTabular:
    def test_uniform_primitive_objects(self):
        v = {"items": [{"sku": "A1", "qty": 2, "price": 9.99},
                       {"sku": "B2", "qty": 1, "price": 14.5}]}
        assert encode(v) == \
            "items[2]{sku,qty,price}:\n  A1,2,9.99\n  B2,1,14.5"

    def test_row_cell_with_comma_is_quoted(self):
        v = {"rows": [{"id": 1, "note": "a,b"}]}
        assert encode(v) == 'rows[1]{id,note}:\n  1,"a,b"'

    def test_row_cell_with_colon_is_quoted(self):
        v = {"links": [{"id": 1, "url": "http://a:b"}]}
        assert encode(v) == 'links[1]{id,url}:\n  1,"http://a:b"'


class TestMixedAndListItems:
    def test_mixed_array(self):
        assert encode({"items": [1, {"a": 1}, "text"]}) == \
            "items[3]:\n  - 1\n  - a: 1\n  - text"

    def test_non_uniform_objects_expanded(self):
        # differing key sets → not tabular → expanded list, first field on hyphen line
        v = {"items": [{"id": 1, "name": "First"},
                       {"id": 2, "name": "Second", "extra": True}]}
        assert encode(v) == (
            "items[2]:\n  - id: 1\n    name: First\n"
            "  - id: 2\n    name: Second\n    extra: true"
        )

    def test_list_item_object_with_nested_array(self):
        # a tree-shaped payload: object list items carrying their own child array
        v = {"nodes": [
            {"id": 1, "children": [{"id": 2, "title": "x"}]},
            {"id": 3, "children": []},
        ]}
        # key sets differ only in value shape; children is a list → not primitive → expanded
        assert encode(v) == (
            "nodes[2]:\n"
            "  - id: 1\n    children[1]{id,title}:\n      2,x\n"
            "  - id: 3\n    children: []"
        )


class TestQuoting:
    def test_empty_string(self):
        assert encode({"name": ""}) == 'name: ""'

    def test_numeric_like_string_quoted(self):
        assert encode({"version": "123"}) == 'version: "123"'

    def test_bool_like_string_quoted(self):
        assert encode({"enabled": "true"}) == 'enabled: "true"'

    def test_null_like_string_quoted(self):
        assert encode({"x": "null"}) == 'x: "null"'

    def test_leading_dash_quoted(self):
        assert encode({"x": "-hi"}) == 'x: "-hi"'
        assert encode({"x": "-"}) == 'x: "-"'

    def test_internal_space_not_quoted(self):
        assert encode({"title": "Blue Lake"}) == "title: Blue Lake"

    def test_leading_space_quoted(self):
        assert encode({"x": " hi"}) == 'x: " hi"'

    def test_backslash_and_quote_escaped(self):
        assert encode({"x": 'a"b\\c'}) == 'x: "a\\"b\\\\c"'

    def test_newline_escaped(self):
        assert encode({"x": "a\nb"}) == 'x: "a\\nb"'

    def test_unicode_and_emoji_unquoted(self):
        assert encode({"msg": "Hello 世界 👋"}) == "msg: Hello 世界 👋"

    def test_quoted_key_when_not_identifier(self):
        assert encode({"my-key": 1}) == '"my-key": 1'


class TestNumbers:
    def test_float_trailing_zeros_trimmed(self):
        assert encode({"x": 1.5000}) == "x: 1.5"

    def test_integral_float_is_integer(self):
        assert encode({"x": 1.0}) == "x: 1"

    def test_negative_zero_normalized(self):
        assert encode({"x": -0.0}) == "x: 0"

    def test_score_decimal(self):
        assert encode({"x": 0.517}) == "x: 0.517"

    def test_null_value(self):
        assert encode({"x": None}) == "x: null"

    def test_bool_values(self):
        assert encode({"a": True, "b": False}) == "a: true\nb: false"


class TestEdgeCases:
    def test_nan_and_inf_become_null(self):
        assert encode({"a": float("nan"), "b": float("inf"), "c": float("-inf")}) == \
            "a: null\nb: null\nc: null"

    def test_carriage_return_and_tab_escaped(self):
        assert encode({"x": "a\rb"}) == 'x: "a\\rb"'
        assert encode({"x": "a\tb"}) == 'x: "a\\tb"'

    def test_control_char_unicode_escaped(self):
        assert encode({"x": "a\x01b"}) == 'x: "a\\u0001b"'

    def test_empty_object_as_list_item_is_bare_hyphen(self):
        assert encode({"items": [{"a": 1}, {}]}) == "items[2]:\n  - a: 1\n  -"

    def test_array_of_arrays_expanded(self):
        # spec §9.2: arrays of primitive arrays → "- [M]: …" list items
        assert encode({"pairs": [[1, 2], [3, 4]]}) == \
            "pairs[2]:\n  - [2]: 1,2\n  - [2]: 3,4"


class TestRootForms:
    def test_root_tabular_array_omits_key(self):
        v = [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bob"}]
        assert encode(v) == "[2]{id,name}:\n  1,Ada\n  2,Bob"

    def test_root_primitive_array(self):
        assert encode([1, 2, 3]) == "[3]: 1,2,3"

    def test_empty_root_array(self):
        assert encode([]) == "[]"

    def test_root_primitive(self):
        assert encode(42) == "42"
        assert encode("hello") == "hello"
        assert encode(True) == "true"

    def test_no_trailing_newline(self):
        assert not encode({"a": 1}).endswith("\n")


class TestDataclassNormalization:
    def test_dataclass_encoded_as_object(self):
        from dataclasses import dataclass
        @dataclass
        class P:
            id: int
            name: str
        assert encode(P(1, "Ada")) == "id: 1\nname: Ada"

    def test_list_of_dataclasses_tabular(self):
        from dataclasses import dataclass
        @dataclass
        class Row:
            id: int
            v: str
        assert encode([Row(1, "a"), Row(2, "b")]) == "[2]{id,v}:\n  1,a\n  2,b"
