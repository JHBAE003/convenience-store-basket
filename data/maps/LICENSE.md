# 지도 자료의 출처와 이용 조건

`seoul_walk.osm.json.gz`는 © OpenStreetMap contributors의 공개 자료에서 서울대입구역 주변 도로·보행로와 그 좌표만 추출한 데이터베이스입니다. **Open Database License(ODbL) 1.0**으로 제공합니다. 압축을 해제한 JSON이 수정 가능한 데이터 원본입니다. 상품·가격·재고 SQLite 자료와 별도로 보관합니다.

- [저작권·출처 표시](https://www.openstreetmap.org/copyright)
- [ODbL 1.0 전문](https://opendatacommons.org/licenses/odbl/1-0/)
- [OSMF 출처 표시 지침](https://osmfoundation.org/wiki/Attribution_Guidelines)

2026-11-05에 공식 OSM API의 공개 읽기 요청 4개로 지역을 나눠 내려받았습니다. `metadata.json`에 요청 범위·다운로드 시각·해시를 보관했습니다. 원본 전체 자료의 갱신 시각은 응답에 없어 `null`입니다. 개별 객체의 시각을 전체 갱신 시각으로 바꾸지 않았습니다.

가공: 건물·상점 등의 객체를 제외하고 `highway` 길과 참조 좌표를 모아 중복을 제거했습니다. 앱은 원본 태그를 보존한 상태에서 보행 가능 길을 필터링합니다. 경로 SVG와 GPX에도 출처와 이용 조건을 표시합니다.

OSM 표준 래스터 타일을 일괄 내려받거나 저장하지 않았습니다. 앱은 포함된 벡터 자료로 지도를 직접 그립니다. [타일 정책](https://operations.osmfoundation.org/policies/tiles/)과 데이터의 ODbL 조건을 구분합니다.
