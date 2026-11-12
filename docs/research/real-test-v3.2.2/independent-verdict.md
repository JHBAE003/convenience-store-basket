# Independent bounded research verification

- date:2026-11-12
- reviewer_scope: read-only review of root catalogue selection/generator/SQLite/CU-Emart facts and independently executed route fixtures. Reviewer authored GS/Seven collection earlier; this verdict does not self-certify those collection claims.
- verdict: partially-confirmed
- rationale: canonical traceability, count/provenance separation and total-route behavior are confirmed within the checks below. The full frozen M4 application/browser verification was not yet available to this reviewer, and current external source revalidation was limited. No actionable implementation defect was found in reviewed scope.

## Frozen plan coverage

- H1/M1: retained research files contain1,104 raw observations, including official CU520/Emart216/Seven306 and explicit third-party GS25 observations62. Enough exact collected names exist to select300 without constructing new flavour/volume combinations. Official source provenance and retained observation fields were inspected; this is not an independent fresh re-download of every original response.
- M2: all300 selected names normalize to their attached collected names;345 attached facts match a raw brand/name/promotion/reference-price/source-URL tuple. Every selected volume string is present in the source name or remains `규격 미제공`; manufacturer remains `미확인`. No expanded size/flavour Cartesian products were generated.
- M2: SQLite counts24stores/300products/1,200brand links/7,200offers;6stores perbrand and300offers perstore. Integrity check is `ok`; foreign-key check empty. No nonempty barcode/GTIN was introduced.
- M2: bundle names remain distinct identities and keep source pack counts: `풀무원)샘물펫500ml*20입(QR)`=20, `동원)샘물펫2L*6입(QR)`=6, `CJ)햇반200g*3입`=3. Unspecified packs are0/unknown rather than guessed1. One TEST quantity for such a catalogue identity means one listed bundle, not one bottle.
- M2:343 observed-brand links are labelled `공개 목록 관측·점포 판매는 가정`;857 unobserved-brand links are labelled `브랜드 판매 미확인·TEST 가정`. All7,200offers have synthetic=1, stock basis `가상 재고` and a TEST price basis. Observed source listing/reference price never establishes actual branch stock/price/availability.
- M2: all7,200offer source start/end/update fields remainNULL because source facts do not provide them. Source fact JSON retains unknown member/card/cross-gift conditions and GS price-validity/page-month fields separately. Generator's November dates and conditions_verified=1 are explicitly TEST simulation assumptions, not verified source facts; metadata/provider observations/UI reference panel state that boundary. Unknown real conditions cannot be claimed confirmed.
- H2/M3: `route_for` sums directed origin→store→store legs, enumerates visit permutations, and includes/reoptimizes the final return leg when selected. `rank` keeps price-first and distance-first modes separate; balance score includes total route distance. Engine and map modules have identical SHA-256 to v3.2.1.
- M3: independently ran only `test_route_total.py` under the existing loopback-allowing socket guard:6passed in0.346s, external connection attempts0. Tests cover nearer radial/opposite direction but longer total tour, price vs shortest/balance outcomes, complete-tour upper bound, directed three-store return-order changes, actual stored OSM minimum among all6three-store permutations, and sum of rendered map legs.
- M4: full allowed suite and browser QA remain the root agent's responsibility; no application execution, LIVE transitions, mocked LIVE provider tests, or LIVE inventory/price requests were performed by this reviewer.

## Fresh source spot check and limits

- Opened official CU public event page: https://cu.bgfretail.com/event/plus.do?category=event&depth2=1&sf=N . It exposes1+1/2+1 filters and warns that some products may be unavailable; product cards are populated separately, so the page text alone did not revalidate all catalogue rows.
- Attempted exact recorded Emart public URL https://emart24.co.kr/goods/event?page=1&search=%EC%83%98%EB%AC%BC&category_seq=&align= . The web tool could not access it. This does not disprove the retained successfulHTTP observations and was not treated as a successful current revalidation. No additional requests or bypass were attempted.
- Source response hashes identify what the collector observed but do not independently prove every raw upstream response. Retained exact fact fields were checked against selected records, not represented as complete external re-verification.
- Local OSM shortest paths validate the file/model math, not latest construction, physical shop entrances, traffic lights, measured walking time or present-day passage access. Dashed source-to-road connections remain estimated and are included in total distance.
- Scope excludes complete UI/accessibility/deployment verification; no whole-task completion claim follows from this independent bounded review alone.

## Artifact fingerprints at review

- `data/real_catalogue.json`: `31b8a255e7df1693a70c6154151dc89c9f78e27c046bd2503952893f3c0b7724`
- `data/test_dataset.sqlite3`: `e5f02ec11a1bc66fd8a1cfd97ace85c063cd0cc3753500061c9ba9b5a8d87400`
- `scripts/build_real_test_dataset.py`: `9eecd007622f9f6637113df3673fc290edb58b4d4d3016bdbf1130f531a1839f`
- `tests/test_route_total.py`: `5b804a1647038d930247f54dd2b62b7fdddf51098bb0bd7ac755e7e4dd9d14c7`

## Final M4 addendum — appended after completion evidence

- reviewed_at: 2026-11-12T12:54:39+09:00
- latest_bounded_verdict: partially-confirmed
- earlier review and fingerprints above are preserved as historical observations. This addendum supersedes the earlier M4-pending statement and refreshes fingerprints after the final pack/parser/UI fixes; no prior evidence has been rewritten.

### M4 closure and final local state

- Read final `v3.2.2-tests.json` and matching final log:99checks passed,0failures/errors/skips,36.755seconds, external socket attempts0. The selected modules exclude LIVE/transitions/mocked LIVE providers/deployment. This final complete run was executed by the root agent, not repeated by this reviewer; root retained the first failed attempt and the fix explanation.
- Read final `v3.2.2-data.json` and independently reopened the current SQLite read-only:24/300/1200/7200 plus345source facts; integrityok, foreign-keyerrorsnone, allGTINempty, sourceperiod/updateunknowns preserved. Rechecked all345attachedfacts against1,104retained raw observations after the last rebuild.
- The explicit `*10` bundle detection now distinguishes a10pack from unknown/single packaging. Current selected examples: [["정식품)아몬드호두병190ml*10", 10]]. No source name or new size combination was invented by that parser correction.
- Inspected final new UI tests: recommendation-mode change synchronizes the selected purchase plan; cumulative last-leg distance matches displayed total; source-reference details start collapsed. Corrected XML comparison checks decodedtext while retaining safe escaped markup (for names such as P&G).
- Independently viewed the saved1440px balance/cheapest screenshots and390px mobile screenshot. Balance displays4,600KRW/1store/82m and explicitly says200KRWextra,562mlesswalking,2fewerstops. Cheapest displays4,400KRW/3stores/644m; visible leg values82/152/410m and cumulative82/234/644m agree arithmetically. The mobile image shows compact cart/compare actions and readable selected-plan/price/distance/visits without visible horizontal clipping.
- M4 is closed against these executed test/log/screenshot artifacts; no LIVE application execution or further web requests occurred during this addendum. This is evidence review rather than an independent live browser session.200%zoom, screenreader, multiplebrowser and allUIstates remain excluded exactly as the root report states.

### Remaining reason for bounded partial confirmation

The implementation/count/traceability/local-route and evidencedM4 claims are confirmed. The overall research verdict remains partially-confirmed solely because this reviewer did not freshly re-download every upstream source record, the earlier exact Emart web spot-check was inaccessible, and GS25 official catalogue retrieval was not completed. Those external limits are disclosed and do not establish a local defect. No additional actionable defect was found in this final bounded pass. Actual branch stock, price, assortment, entrance access and real-world walking time remain unverified and must not be promoted from TEST assumptions.

### Final artifact fingerprints

- `data/real_catalogue.json`: `b91f939ed08db2eb381462afff25f34824e7a764680d637b7e5e0bf6b9bf1af0`
- `data/test_dataset.sqlite3`: `7caf98bd99f863b7233ea6fedf76abd979663451cba08b1f0a9c2e6b65377686`
- `scripts/build_real_test_dataset.py`: `7c475747c0f4798dca57e74826fe5ce245cb09cd66f153d125ad4a4e8421e6b5`
- `streamlit_app.py`: `3b505f51d2935c5cee6a2d59e2e9391a69922dbf841ed77499a24c12d5cba06c`
- `tests/test_route_total.py`: `5b804a1647038d930247f54dd2b62b7fdddf51098bb0bd7ac755e7e4dd9d14c7`
- `tests/test_v322_ui.py`: `6607d99462f9705505485409aaf0a0f01dc897646c7d5a59c7e63859a4d61d7c`
- `docs/verification/v3.2.2-tests.json`: `4cc2a37823bfc17756a9a22746b18ec9416fea6ac967d0c008a5e93199b5a27f`
- `docs/verification/v3.2.2-data.json`: `adaa148fc1cb5ce66a6c94e3d3e554d3c5d32845cb636d38c1eb7bf5fb116243`
- `docs/verification/v3.2.2.md`: `133ab645dbc19b2c40f225a1d2aa51c9aac7ab91b7eab3fdfd62eebe51a8d610`
- `docs/screenshots/v3.2.2-balanced-route.jpg`: `7fc7c9f38d0cc8bfaefcb2bd77f947293ae8e59eb55f856eb12ec488484b0bc4`
- `docs/screenshots/v3.2.2-cheapest-route.jpg`: `9d2108fef7d26c0b44f8da2becd63267a58b7c2bdd9f7e3cafe105f9a61023fd`
- `docs/screenshots/v3.2.2-mobile.jpg`: `884f5e4a79c017f8aec6ff7092461a508454fdc799e8e8d88b842fc028763a6d`
