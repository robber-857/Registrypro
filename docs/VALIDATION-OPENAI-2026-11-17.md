# OpenAI screenshot validation — 2026-11-17

- Real official Responses API call succeeded with `gpt-5.4-mini`. User-supplied Young Accountants screenshot yielded two Cancelled rows, with unavailable ABN/ACN/date empty.
- Installed extension completed the same 20-business list, forced refresh, 5-second interval, no company-review tasks. Run `a715d9a4c8d13c5d5e225d87`, 20/20, cache 0, wall time 726.2 seconds including CAPTCHA waiting.
- 46 screenshot observations were saved by OpenAI. Some capture the preceding page during navigation; those are excluded from query-specific cancellation observations. No OpenAI call error appeared in the completed per-task metrics.
- Automated confirmed renewal dates: 11/20 (55%), unchanged from the preceding 11/20 baseline (533.8 seconds). OCR did not improve this run's date hit rate, and the wall time increased; CAPTCHA waiting makes a direct speed comparison inappropriate.
- Dental clinic remains an unconfirmed ADS DENTAL CLINIC candidate. It is not counted in confirmed renewals.
- Mikazuki Strathfield: screenshot contains exact original name MIKAZUKI STRATHFIELD, Cancelled. The automated scorer used shortened query `mikazuki` and missed it; evidence was reviewed separately and cancellation recorded. Raw automated result remains in final_run.json. Added original-name-alias scoring test; this last fix has not undergone another full live 20-business run.
- Young Accountants: a real Google image CAPTCHA paused the queue. After user completion it resumed automatically, then ASIC technical difficulty exhausted two recovery attempts. It is a retryable service failure, not no-results or a live cancellation success.
- EL TAURUS: returned summary EL TAURUS / Cancelled with historical 2002 date, rejected because summary name does not match assigned name. No historical date entered confirmed sheet. Fallback returned at approximately 142 seconds.
- Other unresolved rows: Ho Jiak (explicit no-results), Subway, Kim Sun Young, Strathfield Plaza and Whim (no accepted matching current record). Observed cancelled rows are retained separately from confirmed target ownership.
- Local artifacts: output/v24_openai_trial/ASIC_20家续期结果.xlsx and .csv; raw final_run.json, final_rows.json, ocr_observations.json, review.json. XLSX has 11 confirmed rows plus 9 cancellation/candidate/unresolved rows. It is not a desktop Excel acceptance test.
- Code changes: bind screenshot grant to reused active ASIC tab; show OCR errors and emit OCR metrics; original full-name alias participates in result scoring. Existing automatic verification-clear recovery was observed live.
- Validation: 103 Python tests and 28 JavaScript tests passed. API key and pairing token are excluded from published files.
