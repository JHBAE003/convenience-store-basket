"""v3.2.1 TEST/UI·순수 자료 검사만 실행. LIVE 검사와 외부 연결 제외."""
import json
from pathlib import Path
import sys
from time import perf_counter
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'scripts'))
from run_test_mode import MODULES
from run_tests import socket,guard,original_connect,original_connect_ex,attempts

if __name__=='__main__':
    names=MODULES+('test_public_records','test_v321_map_cache','test_v321_ui')
    suite=unittest.TestSuite(unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=name+'.py') for name in names)
    start=perf_counter()
    with patch.object(socket.socket,'connect',new=guard(original_connect)),patch.object(socket.socket,'connect_ex',new=guard(original_connect_ex)):
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'version':'3.2.1','modules':names,'tests_run':result.testsRun,'failures':len(result.failures),
            'errors':len(result.errors),'skipped':len(result.skipped),'elapsed_seconds':round(perf_counter()-start,3),
            'external_socket_attempts':len(attempts),'passed':result.wasSuccessful() and not attempts,
            'excluded':['LIVE 조회','LIVE 전환','모의 LIVE 공급자','배포 검증']}
    target=ROOT/'docs/verification/v3.2.1-tests.json';target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False));sys.exit(0 if report['passed'] else 1)
