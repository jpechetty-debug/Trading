import io

import pytest

from src.logger import get_logger


@pytest.mark.parametrize('encoding', ['cp1252', 'utf-8'])
def test_console_logging_supports_currency_and_emoji(monkeypatch, encoding):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding=encoding, errors='strict')
    monkeypatch.setattr('sys.stdout', stream)
    logger = get_logger(f'trading.test.console.{encoding}')
    try:
        logger.info('Equity: ₹%.2f', 100_000)
        stream.flush()
        rendered = buffer.getvalue().decode(encoding)
        assert 'Equity:' in rendered
        assert '100000.00' in rendered
        if encoding == 'utf-8':
            assert '📋' in rendered
            assert '₹' in rendered
        else:
            assert r'\u20b9' in rendered
    finally:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
