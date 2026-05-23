from api.routes import _apply_generated_strategy_name
from db.models import Strategy


def test_apply_generated_strategy_name():
    manual = Strategy(
        thread_id="11111111-1111-1111-1111-111111111111",
        strategy_name="Custom title",
        strategy_name_source="manual",
    )
    _apply_generated_strategy_name(manual, "Agent title")
    assert manual.strategy_name == "Custom title"
    assert manual.strategy_name_source == "manual"

    generated = Strategy(
        thread_id="22222222-2222-2222-2222-222222222222",
        strategy_name="Old title",
        strategy_name_source="generated",
    )
    _apply_generated_strategy_name(generated, "  New title  ")
    assert generated.strategy_name == "New title"
    assert generated.strategy_name_source == "generated"
