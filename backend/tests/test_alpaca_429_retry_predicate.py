from strategies.utils import _is_http_429_exception


class _ExcWithStatus(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"status={status_code}")
        self.status_code = status_code


def test_is_http_429_exception_true_on_status_code_attr():
    assert _is_http_429_exception(_ExcWithStatus(429)) is True


def test_is_http_429_exception_true_on_alpaca_apierror_message_json():
    exc = Exception('{"message": "too many requests."}')
    assert _is_http_429_exception(exc) is True

