"""LIVE 공급자나 LIVE 전환 검사를 실행하지 않는 TEST 전용 검사."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'scripts'))
from run_tests import socket,guard,original_connect,original_connect_ex,attempts

MODULES=('test_engine','test_dataset','test_ui','test_product_art','test_walk_map','test_v311_ui','test_search_ui','test_design_ui')

if __name__=='__main__':
    suite=unittest.TestSuite(unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=name+'.py') for name in MODULES)
    with patch.object(socket.socket,'connect',new=guard(original_connect)),patch.object(socket.socket,'connect_ex',new=guard(original_connect_ex)):
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    print('TEST 외부 네트워크 연결 시도:',len(attempts),'· LIVE 검사 제외')
    sys.exit(0 if result.wasSuccessful() and not attempts else 1)
