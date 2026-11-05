# 편의점 장바구니 버전 목록

2026-11-05 기준 보관 목록입니다. 기존 브랜치와 Streamlit 연결 설정은 변경하지 않았습니다.

| 버전 | 내용 | GitHub |
|---|---|---|
| v1 | 상품 사진 검색·장바구니·점포 비교 초기 버전 | [기존 v1-stable](https://github.com/JHBAE003/convenience-store-basket/tree/v1-stable) |
| v2 | 예산·복수 점포 비교·행사 수량 확장 | [기존 v2](https://github.com/JHBAE003/convenience-store-basket/tree/v2) |
| v2.1 | 수집 경로·캐시·상품 식별 개선 | [기존 v2.1](https://github.com/JHBAE003/convenience-store-basket/tree/v2.1) |
| v2.1.1 | 보안 보완 버전 | [기존 v2.1.1](https://github.com/JHBAE003/convenience-store-basket/tree/v2.1.1) |
| v3.0.0 | 수량 배분·최대 3곳 방문·5가지 추천 기준과 8개 고정 시연 | [v3.0.0](https://github.com/JHBAE003/convenience-store-basket/tree/v3.0.0) |
| v3.1.0 | 같은 화면에서 TEST/LIVE 선택, SQLite TEST 자료와 24개 점포 직접 추천 | [v3.1.0](https://github.com/JHBAE003/convenience-store-basket/tree/v3.1.0) |

v2.2.0은 수집 개선 계획서 단계이며 실행 가능한 별도 구현 버전으로 표시하지 않습니다.

## 새 보관 버전 사용

각 브랜치의 README와 실행 파일을 사용합니다. v3.0.0은 `v3.0.0 실행.bat`, v3.1.0은 `v3.1.0 실행.bat`입니다.
브랜치 전체를 내려받아야 `legacy_v211/`의 공통 기능과 이미지·자료가 포함됩니다.
v3.1.0의 `data/test_dataset.sqlite3`도 저장소에 함께 보관했습니다.

점포명·위치는 공개자료 기반이며 가상 상품·가격·행사·재고는 실제 매장 자료가 아닙니다.
기존 검증 기록을 함께 보관했으며 이번 업로드 과정에서 LIVE 가격·재고 검증이나 배포는 실행하지 않았습니다.
