from services.agent import _strategy_code_present


def test_strategy_code_present():
    assert _strategy_code_present(None) is False
    assert _strategy_code_present("") is False
    assert _strategy_code_present("   \n\t  ") is False
    assert _strategy_code_present("print('hi')\n") is True

