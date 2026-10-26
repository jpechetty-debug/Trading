from tests.test_live_safety import make_stream_scanner


def test_on_candle_close_forwards_full_result_dict(monkeypatch):
    scanner, expected, candle, db = make_stream_scanner(monkeypatch)
    scanner.on_candle_close('A.NS', candle)
    scanner.exec_model.generate_orders.assert_called_once()
    passed = scanner.exec_model.generate_orders.call_args.args[0]
    assert passed == expected
    assert passed['kill_score'] == 8.5
    assert passed['support'] == 2400.0
    assert passed['resistance'] == 2600.0
    assert 'reasons' in passed
    db.log_trade.assert_called_once()
