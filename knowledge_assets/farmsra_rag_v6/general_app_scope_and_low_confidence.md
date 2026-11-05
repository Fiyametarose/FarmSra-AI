---
title: App scope, confidence and uncertain results
crop: all
condition: general
language: en
country: general
publisher: FarmSra AI — original evidence-guided synthesis
source_type: source-grounded original educational summary
url: https://extension.colostate.edu/resource/diagnosing-plant-disorders/
references_json: '[{"id": "SRC1", "publisher": "Colorado State University Extension", "title": "Diagnosing
  Plant Disorders", "url": "https://extension.colostate.edu/resource/diagnosing-plant-disorders/", "source_language":
  "en", "scope": "general context / safety"}, {"id": "SRC2", "publisher": "NC State Extension", "title": "Diagnostics",
  "url": "https://content.ces.ncsu.edu/extension-gardener-handbook/7-diagnostics", "source_language": "en",
  "scope": "general context / safety"}]'
evidence_map_json: '{"what": "FarmSra AI internal model/class coverage, NOT asserted by external source", "signs":
  "Published diagnostic limitations from source; model-label semantics from FarmSra app design", "check": "SRC1",
  "action": "SRC1", "manage": "SRC1; additional general IPM context when available", "treatment": "SRC1 for
  condition; local label and registration must be checked separately", "weather": "SRC1 when stated; otherwise
  no diagnosis from weather alone", "monitor": "Published crop source for signs/progression; FarmSra project
  policy for farmer-initiated expert consent"}'
licence: FarmSra original synthesis, links only; external sources not reproduced
source_language: en
editorial_review: English core facts and references reviewed 2026-11-04
translation_review: not applicable
agronomist_signoff: false
approved_for_rag: true
safe_for_unreviewed_treatment_decisions: false
last_reviewed: '2026-11-04'
content_version: '6.0'
rag_approval_scope: educational_prototype_only
production_approval: false
production_review_requirement: independent agronomist and qualified language review pending
activation_basis: owner-requested staging use; editorial source-relevance checks, not independent professional
  certification
review_method: human-authored editorial comparison to retrieved authoritative web sources; no field or laboratory
  verification
topic_id: general_app_scope_and_low_confidence
topic_type: general
review_status: english_web_source_checked
intended_display: general_guidance_library_and_chatbot
provenance_note: System behavior and farmer-consent rules originate from FarmSra specifications; agricultural
  citations do not substantiate app-specific feature claims.
---

# App scope, confidence and uncertain results

## What this is [what]
FarmSra AI compares a leaf image with the 17 classes represented by its three existing tomato, potato and maize models. A model output is a preliminary classification, not confirmation of a plant disease.

## Signs to check [signs]
A healthy-class result means the model did not identify one of its modeled conditions in that image. It does not rule out unseen pests, nutrient problems, root diseases, or an early-stage infection.

## Conditions that may look similar [check]
Check crop identity, photo sharpness, symptoms on other leaves and the pattern across plants. Image-confidence percentage is not the percentage of crop damage, disease severity or probability of treatment success.

## What to do now [action]
If the image is unclear or the model is uncertain, take a new photo of both leaf surfaces and the whole plant. Record when symptoms began; do not select a pesticide on the basis of one prediction.

## Management and prevention [manage]
Show condition-specific guidance only when the result, evidence and case context are appropriate. Healthy, uncertain, and unmodeled results require a cautionary response instead of a fabricated treatment.

## Treatment considerations [treatment]
Do not prescribe a pesticide or claim that a crop is disease-free solely from an AI image result.

## Weather and spread [weather]
Weather adds context when its location and observation time are known. It cannot establish a diagnosis alone.

## Monitoring and expert review [monitor]
If symptoms worsen, affect many plants or remain unexplained, the farmer may choose an expert and consent to a case review. Consultation is never automatic.

## Sources and evidence [references]
- [SRC1] Colorado State University Extension: Diagnosing Plant Disorders — https://extension.colostate.edu/resource/diagnosing-plant-disorders/ (source language: English)
- [SRC2] NC State Extension: Diagnostics — https://content.ces.ncsu.edu/extension-gardener-handbook/7-diagnostics (source language: English)

Source-grounded educational synthesis; not a confirmed diagnosis or local pesticide prescription.
