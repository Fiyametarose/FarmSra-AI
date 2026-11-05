# FarmSra AI: English source-checked multilingual RAG v6

## Classroom use and quality status

This 23-topic / 69-Markdown-file package was rewritten from the v5 classroom prototype set and checked against online disease-specific extension publications and FAO guidance on 4 November 2026. English is the editorial source of truth. Swahili and Amharic are project-draft localizations and are not independently native-speaker certified; do not infer that they have identical nuance in every paragraph.

`approved_for_rag: true` means **educational prototype testing** only. `production_approval: false`, `agronomist_signoff: false`, and translation review status are explicitly retained. These flags have no effect without application support for them.

## Contents

* 17 model-class topics: 10 tomato, 3 potato, 4 maize (including the three healthy labels and the tomato two-spotted-spider-mite out-of-scope label)
* Six common guidance topics: scope and uncertainty, leaf photographs, IPM, pesticide safety, weather, expert support
* Three languages per topic: English (`.md`), Swahili (`_sw.md`), Amharic (`_am.md`)
* `ENGLISH_SOURCE_AND_CLAIM_LEDGER.csv`: section-level evidence review and URLs
* `MODEL_CLASS_AND_UI_COVERAGE.csv`: exact model labels and file coverage
* `FARMER_DISPLAY_CONTENT_BY_MODEL_CLASS.json`: structured eight-section farmer results for all model classes in all languages
* `QA_CHECKS.csv`: automatic audit findings

## Recommended farmer-facing diagnosis-result layout

1. Crop and preliminary model classification + actual confidence score (never imply % severity)
2. Clear uncertainty flag, healthy/pest/out-of-scope handling
3. *What this is*, *Signs to check*, and *Conditions that may look similar*
4. *What to do now* (concrete first steps)
5. *Management and prevention*; *Treatment considerations* (with product-label warning)
6. Case-local *Weather and spread* as actual contextual data; timestamp observed vs forecast
7. *Monitoring and expert review*: farmer selects the expert and actively consents
8. *Sources and evidence*: links and source language; show both original source title and translated explanatory text

## Implementation notes

- The current repository's ingest_knowledge command does not yet support all of: general documents (`crop: all`), exact three-language filtering, structured `references_json`, condition-specific evidence checks, multi-source citations, approval scope and metadata. Update it; do not assume an added metadata field is enforced automatically.
- Preserve exact model class names in `condition`; do not normalize underscores or rename labels.
- Retrieve a matching disease document plus applicable general IPM/safety/scope documents. Healthy and spider-mite classes require special response logic.
- Pass preferred language through retrieval, LLM prompt and UI; cite English originals when translations are derived from them.
- Enforce authorization by farmer and by case before allowing chatbot access to images/history/weather.
- Automated advisory generation is allowed; **never automatically send an expert referral**.
- Use case location and real date-stamped weather when available, never create thresholds from general guides.
- For chemical controls, never invent registered products, doses, spray intervals, mixing or label directions.
- Keep a record of document versions, active publication states and reference provenance for repeatable academic demonstrations.

## Limits of verification

No claim of real-farm diagnostic accuracy can be inferred from web checks. No native speaker reviewed the translations. An English page may contain location-specific context that has **deliberately not been carried** into our general farmer-facing text. Citation-to-claim association is recorded by section; independent line-by-line agronomist certification is not represented.
