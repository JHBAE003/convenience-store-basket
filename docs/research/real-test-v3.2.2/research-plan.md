# Realistic TEST dataset research — frozen plan
Date:2026-11-12
Question: Which real product identities and observed promotions can populate a fixed-size offline TEST dataset, and does total directed walking distance affect store recommendations?
H1: Public official promotion/catalogue pages provide enough real product name/spec records for300 products. Falsifier: fewer than300 verifiable distinct records after bounded sources.
H2: Existing route engine uses cumulative origin-to-store-to-store distance. Falsifier: opposite-direction case chooses a lower straight-radius but longer actual tour for distance mode.
M1 confirmatory: inspect four chain official pages and bounded public requests (up to20 perchain), retain sources/receipt dates, parse actual fields. Secondary public sources only with explicit attribution. Stop at>=300 distinct known product/spec records and representative promotion evidence per availablechain or concrete retrieval limits.
M2 confirmatory: offline dataset count/provenance/date/price/stock/variant checks, no invented source fields. Keep synthetic branch availability/price/stock labelled.
M3 confirmatory: inspect actual local OSM source and compare one/two/three-stop route orders and opposite-direction detour fixtures; no LIVE execution.
M4 confirmatory: safe TEST/pure suite and browser TEST QA.
Evidence log: append findings/deadends/deviations in research-log.md and source JSON records; do not revise frozen plan.
