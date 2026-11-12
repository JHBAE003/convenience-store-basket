"""현재 버전의 공개 상품 기반 TEST 자료 재빌드 진입점. 실행 중 생성/통신 없음."""
from pathlib import Path
import argparse
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.build_real_test_dataset import build

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(build(args.output))
