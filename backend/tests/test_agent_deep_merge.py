from services.agent import _deep_merge_json_values


def test_deep_merge_json_values():
    assert _deep_merge_json_values({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
    assert _deep_merge_json_values({"a": 1}, {"a": 2}) == {"a": 2}
    assert _deep_merge_json_values({"x": {"p": 1}}, {"x": {"q": 2}}) == {"x": {"p": 1, "q": 2}}
    assert _deep_merge_json_values({"x": {"p": 1}}, {"x": {"p": 3}}) == {"x": {"p": 3}}
    assert _deep_merge_json_values([1, 2, 3], [10]) == [10, 2, 3]
    assert _deep_merge_json_values([1], [10, 20]) == [10, 20]
    assert _deep_merge_json_values([{"a": 1}], [{"b": 2}]) == [{"a": 1, "b": 2}]
    assert _deep_merge_json_values(1, 2) == 2
    assert _deep_merge_json_values({"a": 1}, [1]) == [1]
    assert _deep_merge_json_values([1], {"a": 1}) == {"a": 1}
