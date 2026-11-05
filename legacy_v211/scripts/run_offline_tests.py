"""전체 자동 검사는 외부 HTTP를 차단한다. 실제 점검은 별도 기록한다."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import requests
from urllib3.connectionpool import HTTPConnectionPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
blocked_requests = []


def no_external_http(*args, **kwargs):
    blocked_requests.append(True)
    raise AssertionError('자동 검사에서는 외부 HTTP 요청을 허용하지 않습니다. 모의 응답을 지정하세요.')


if __name__ == '__main__':
    with patch.object(requests.sessions.Session, 'request', side_effect=no_external_http), \
         patch.object(HTTPConnectionPool, 'urlopen', side_effect=no_external_http):
        suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(f'모의 응답이 없는 외부 HTTP 시도: {len(blocked_requests)}회 (실제 연결은 차단됨)')
    sys.exit(0 if result.wasSuccessful() and not blocked_requests else 1)
