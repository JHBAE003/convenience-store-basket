"""Block all Python socket connections during TEST engine and UI tests."""
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
attempts=[]
original_connect=socket.socket.connect
original_connect_ex=socket.socket.connect_ex
def guard(original):
    def checked(sock,address):
        # Windows asyncio builds its internal socketpair through loopback TCP.
        if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1'):
            return original(sock,address)
        attempts.append(True)
        raise AssertionError('TEST 실행에서 외부 네트워크 연결 시도')
    return checked

if __name__=='__main__':
    with patch.object(socket.socket,'connect',new=guard(original_connect)),patch.object(socket.socket,'connect_ex',new=guard(original_connect_ex)):
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(ROOT/'tests')))
    print('TEST 외부 네트워크 연결 시도:',len(attempts),'(Windows 내부 loopback 허용)')
    sys.exit(0 if result.wasSuccessful() and not attempts else 1)
