# GS25 / Seven product research evidence log
Frozen methods: parent research-plan.md M1; no LIVE/provider execution.
## Earlier bounded probes
- Seven official page HTTP200; visible13 productcards, public JavaScript explicitly posts listMoreAjax.asp. Page CSS cache-buster receipt is not source data update time.
- GS25 old official event/mobile URLs redirect to https://www.gsretail.com/brand/gs25, which returns JavaScript-only shell; no official product list verified.
- 편의점행사.com public POST form works and exposes20 Product ItemList JSON-LD records per query.
- Public infinite-scroll ajax.item_list.php attempt returned403; stopped that route, no auth/bypass attempted.
- Secondary site dateModified/og:updated_time indicate page update, not per-product or exact promotion validity. priceValidUntil retained as price validity only.
- E001: official Seven tab1/page1,HTTP200,rawcards26,new13,sha256=d6a26f12b443a749367197f8d0c9d856097073ee6c96f78b6ff46b049cb3792f.
- E002: official Seven tab1/page2,HTTP200,rawcards40,new20,sha256=5f79d495c7a38617823669af6770d9333f4de60c8249d8b03e36d070355f9c7e.
- E003: official Seven tab1/page3,HTTP200,rawcards40,new20,sha256=77da27aaa11bce1dff4507a7193a137caf1ac22a82e87e3034a38a210ea21e57.
- E004: official Seven tab1/page4,HTTP200,rawcards40,new20,sha256=9ade690ea21d529eb9e3c4b501745b246a7ac99c455f5ddb7bef5b67f0c9886c.
- E005: official Seven tab1/page5,HTTP200,rawcards40,new20,sha256=12c55c2ecb76133aaeed46b4c05396ec59d494a2aff411a0a4e6b84b29d0201b.
- E006: official Seven tab1/page6,HTTP200,rawcards40,new20,sha256=03b20c30fad5f86b841c99a4d785c6129a01414d1a3e223b4ede549bb593b3ad.
- E007: official Seven tab1/page7,HTTP200,rawcards40,new20,sha256=bd2359e935b25b5b078045fe2e644ed183311b595bf6011a72df2b4d7b20c83a.
- E008: official Seven tab1/page8,HTTP200,rawcards40,new20,sha256=4ca98f310531b52668403da0d1eb77ba9ebb19331f9a23a32405fe794f9288c8.
- E009: official Seven tab2/page1,HTTP200,rawcards26,new13,sha256=946557d3614d459d0dbc5c22eec58e2a9e618313e6123e5044411326e5384413.
- E010: official Seven tab2/page2,HTTP200,rawcards40,new20,sha256=ac69ab9123a84f26cdbc952a6ce847b6ba9299a8c66c51235e8aa8ee47510a3f.
- E011: official Seven tab2/page3,HTTP200,rawcards40,new20,sha256=04495a1d9cc5c112b5ea9093726b50d9643be92cdc0e448336260217186129e1.
- E012: official Seven tab2/page4,HTTP200,rawcards40,new20,sha256=1a54d7e13aa627147c16a475ac07db1016d2f30e26bd569eb268218ccc466a3d.
- E013: official Seven tab2/page5,HTTP200,rawcards40,new20,sha256=df05c598fb362943754ebab41d25c2161aa60fcf5383322d5fe19b7ce383c33a.
- E014: official Seven tab2/page6,HTTP200,rawcards40,new20,sha256=e417515f49bb195edff8a6ad74f7bf49064d9cef13c33e7a7a1eb94efc8d3d2e.
- E015: official Seven tab2/page7,HTTP200,rawcards40,new20,sha256=b42993b09dcdf1ee1528b9dc3162d50cf6199b54d5fec0745a0688cd92a17111.
- E016: official Seven tab2/page8,HTTP200,rawcards40,new20,sha256=7d83e177937536168de0f43209c2920cf0f479d8ae48dd06625aa1f1b686bf0e.
- E017: third-party GS25 public search '우유',HTTP200,new7,sha256=dce00a2bb29a7d3ddc5d61726b88989a6eb214603e06793a72fb458ee59eef97.
- E018: third-party GS25 public search '커피',HTTP200,new5,sha256=c1a399530eff17e3b80be46fd4a9327c54957969ecd80b80e10dbbc4b0ae334e.
- E019: third-party GS25 public search '라면',HTTP200,new3,sha256=4cd54a6f1a43f83f26512d9a1cc5a09c4e43db08c929e0bc3816413c404fda85.
- E020: third-party GS25 public search '콜라',HTTP200,new19,sha256=33b583de0ea9104f1ec0fcb7d2be0f1809f1be324eee4c6a1357efaefdce21cd.
- E021: third-party GS25 public search '사이다',HTTP200,new13,sha256=9c7f2a3eea9287e5d88a389c2d31b2deddf8ee4e61a8d8e16f6e507324c3e35e.
- E022: third-party GS25 public search '초콜릿',HTTP200,new13,sha256=250495fb28be2b64c5dfe2707a431b0004935328bccb7313b678a8945003cbbc.
- E023: third-party GS25 public search '과자',HTTP200,new0,sha256=8b5b21f919709f494880596aa7f660698a77aa0668043f119eb65d3dc100e52f.
- E024: third-party GS25 public search '생수',HTTP200,new0,sha256=0987cd0efb27c8214ddc7be2d1aceee9e4552f24fbe8ae6a3b3ee80839f10940.
- E025: third-party GS25 public search '아이스크림',HTTP200,new0,sha256=9d4758589c9434b51cea1808e41b6189f4dad68c31e1374c922b6cb5e48a9979.
- E026: third-party GS25 public search '요구르트',HTTP200,new2,sha256=171ebc91a06a47d563f548f95a1b65d7282ca1a965169c4867bcd244b74b718c.
## Findings and limits
- Exact observed records: {'GS25': 62, '세븐일레븐': 306}.
- Official Seven names/prices/promotion labels verified through public HTML; exact promotion start/end/source update not supplied.
- GS25 records are explicit third-party observations. The JSON-LD source claims November2026 and price validity2026-11-30; those fields are not represented as exact confirmed promotion dates.
- All stock and per-branch availability fields intentionally absent. Explicit GTIN absent; no code extracted from image filenames.
- No application or LIVE providers executed, no authentication or private endpoints used.
- Request bounds: Seven19 counted including priorweb/GET/probe plus16 collection calls; GS25<=20 including public official/fallback/probes and10 searches.

## Local artifact validation
- JSON readback and schema invariants checked:368 unique brand/name/promotion rows; all reference prices positive; all evidence IDs resolve; no manufacturer/GTIN guesses; no exact start/end/source timestamp invented.
- GS25 goal150 was not reached within normal public query response subsets;62 observed GS25 rows retained. No synthetic rows added to research facts.
