# v3.0.0 요구사항 추적

| 요구 | 구현/검증 |
|---|---|
| A 5모드·동률·예산 | engine.rank/solve, UI sidebar, oracle 36개×5모드 |
| B 수량 분할·1/2/3곳·같은 브랜드 | allocations+mask DP, 분할/3곳최저/동일브랜드 검사 |
| B 불가능·부족수량·부분 구매 | Result.reasons/shortages/partial, budget-only 부분 예시 검사 |
| C 반경/전체경로·왕복·순열 | route_for, 제한/왕복 검사, 로컬SVG 지도 |
| D 일반/1+1/2+1·고정기준일 | 기존 결제식 재사용, 경계/매장분리/재고·만료 검사 |
| E 대표추천·비교·매장목록·지도 | streamlit_app.py, AppTest 및 브라우저 |
| E 중복계획 합침·수치 비교 | signature 그룹, comparison_text |
| F 8시연·근거 | scenarios.py, README 예상값, 8시연 AppTest |
| TEST 외부요청 없음 | 원격사진/지도/주소API 제거, socket 차단 검사 |
| LIVE 보존·원본불변 | legacy_v211 및 source-manifest, 기존 전체 검사 |
| 규격·미확인 구분 | product_id+spec, None/0/누락 검사 |
| 정확 최적화 대조 | 별도 cross-product oracle. 핵심 전체 구매안만 정확 최적화 주장 |
| 지원범위·근사 고지 | 엔진6종×12개·12점포·3방문, 부분 예산 예시는 탐욕법 표시 |
| 문서·실행파일 | README, 배치2종, algorithm/verification 문서 |

대체품 자동 적용·새 LIVE 연동·외부 배포·GitHub 업로드는 범위에 포함하지 않는다. 기존 대체품 기능은 보존 경로에 남아 있다. 일부 예산 구매 예시는 최적해 보장 대상이 아니며 정상 전체 추천과 분리한다.
