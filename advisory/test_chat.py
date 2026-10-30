import json
import os
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests
from django.conf import settings
from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from advisory import chat
from advisory.library import filter_documents, load_documents
from advisory.models import AIChatMessage, AIChatSession
from diagnoses.models import DiagnosisCase
from expert_reviews.models import ExpertReferral
from users.models import User
from weather.models import WeatherContext

EVIDENCE_TEXT = (
    "Early blight is caused by Alternaria solani. Remove infected lower leaves and rotate crops. "
    "Avoid overhead watering to keep foliage dry and improve airflow through proper spacing."
)
EVIDENCE = [{
    "chunk_id": "tomato_early_blight_abc_0",
    "source_id": "tomato_early_blight",
    "title": "Early Blight",
    "publisher": "Test Extension",
    "url": "https://extension.example.org/early-blight",
    "date": "2021",
    "section": "",
    "text": EVIDENCE_TEXT,
}]
ANSWER_EN = (
    "Early blight is a possible explanation for this case. Removing infected lower leaves, "
    "rotating crops and avoiding overhead watering can reduce spread."
)
ANSWER_SW = "Ukungu wa mapema unaweza kuwa sababu. Ondoa majani ya chini yaliyoambukizwa na epuka umwagiliaji wa juu."
ANSWER_AM = "ቀደምት ቅጠል በሽታ ሊሆን ይችላል። የተጎዱትን የታችኛ ቅጠሎች ያስወግዱ።"


def gemini_payload(answer=ANSWER_EN, answer_en=ANSWER_EN, citations=None, insufficient=False):
    return json.dumps({
        "answer": answer,
        "answer_en": answer_en,
        "insufficient_evidence": insufficient,
        "citations": EVIDENCE_IDS if citations is None else citations,
    })


EVIDENCE_IDS = [item["chunk_id"] for item in EVIDENCE]


class FakeGemini:
    """Stands in for requests.post against the Gemini REST API."""

    def __init__(self, payloads=None, error=None, finish="STOP", rewrite_error=None,
                 rewrite_text="early blight management tomato", statuses=None):
        self.payloads = list(payloads or [])
        self.error = error
        self.finish = finish
        self.rewrite_error = rewrite_error
        self.rewrite_text = rewrite_text
        self.statuses = list(statuses or [])
        self.prompts = []
        self.calls = []
        self.rewrites = 0

    @staticmethod
    def response(text, finish="STOP", status=200):
        reply = MagicMock(status_code=status)
        reply.json.return_value = {
            "candidates": [{"finishReason": finish, "content": {"parts": [{"text": text}]}}]
        }
        return reply

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        prompt = kwargs["json"]["contents"][0]["parts"][0]["text"]
        if self.statuses:
            status = self.statuses.pop(0)
            if status != 200:
                return self.response("", status=status)
        if "Rewrite the farmer's question" in prompt:
            self.rewrites += 1
            if self.rewrite_error:
                raise self.rewrite_error
            return self.response(self.rewrite_text)
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        text = self.payloads.pop(0) if self.payloads else gemini_payload()
        return self.response(text, self.finish)


class ChatTestCase(TestCase):
    def setUp(self):
        self.farmer = User.objects.create_user(email="f1@example.com", password="pwd", role="farmer")
        self.other = User.objects.create_user(email="f2@example.com", password="pwd", role="farmer")
        self.expert = User.objects.create_user(email="ex@example.com", password="pwd", role="expert")
        self.case = self.make_case(self.farmer, "Tomato_Early_blight", 0.88)
        self.url = reverse("case-chat", args=[self.case.id])
        self.client.force_login(self.farmer)
        chat._thinking_choice = 0

    def make_case(self, farmer, predicted, confidence, crop="tomato"):
        return DiagnosisCase.objects.create(
            farmer=farmer, crop=crop, status="completed", predicted_class=predicted,
            model_confidence=confidence, symptoms="Brown rings on lower leaves",
            completed_at=timezone.now(),
        )

    def ask(self, question, token=None, client=None):
        return (client or self.client).post(
            self.url, {"question": question, "token": token or uuid.uuid4()}
        )

    def run_chat(self, fake, questions, evidence=EVIDENCE):
        responses = []
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test"}), \
             patch("advisory.chat.requests.post", side_effect=fake.post), \
             patch("advisory.chat.retrieve_evidence", return_value=evidence) as retrieve:
            for question in questions:
                responses.append(self.ask(question))
        self.retrieve = retrieve
        return responses


class IsolationAndAccessTests(ChatTestCase):
    def test_other_farmer_cannot_open_or_post_to_case_chat(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.ask("hello").status_code, 404)
        self.assertFalse(AIChatSession.objects.exists())

    def test_chats_are_separate_per_farmer(self):
        other_case = self.make_case(self.other, "Tomato_Early_blight", 0.9)
        fake = FakeGemini()
        self.run_chat(fake, ["How do I manage this?"])
        self.client.force_login(self.other)
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test"}), \
             patch("advisory.chat.requests.post", side_effect=fake.post), \
             patch("advisory.chat.retrieve_evidence", return_value=EVIDENCE):
            self.client.post(
                reverse("case-chat", args=[other_case.id]),
                {"question": "My own question", "token": uuid.uuid4()},
            )
        page = self.client.get(reverse("case-chat", args=[other_case.id]))
        self.assertContains(page, "My own question")
        self.assertNotContains(page, "How do I manage this?")
        self.assertEqual(AIChatSession.objects.count(), 2)

    def test_expert_and_anonymous_are_blocked(self):
        self.client.force_login(self.expert)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_post_requires_csrf_token(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.farmer)
        response = strict.post(self.url, {"question": "hi", "token": uuid.uuid4()})
        self.assertEqual(response.status_code, 403)

    def test_incomplete_case_redirects_to_case(self):
        pending = DiagnosisCase.objects.create(farmer=self.farmer, crop="tomato")
        response = self.client.get(reverse("case-chat", args=[pending.id]))
        self.assertRedirects(response, reverse("case-detail", args=[pending.id]))

    def test_question_length_is_bounded(self):
        response = self.ask("x" * (chat.MAX_QUESTION_CHARS + 1))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AIChatMessage.objects.exists())


class ConversationTests(ChatTestCase):
    def test_three_message_conversation_persists_and_reopens(self):
        fake = FakeGemini()
        self.run_chat(fake, ["First question?", "Second question?", "Third question?"])
        session = AIChatSession.objects.get(case=self.case)
        self.assertEqual(session.farmer, self.farmer)
        self.assertEqual(session.messages.count(), 6)
        self.assertEqual(
            list(session.messages.values_list("role", flat=True)),
            ["farmer", "assistant"] * 3,
        )
        third_prompt = fake.prompts[2]
        self.assertIn("First question?", third_prompt)
        self.assertIn("Second question?", third_prompt)
        self.assertIn(ANSWER_EN, third_prompt)
        page = self.client.get(self.url)
        for text in ("First question?", "Second question?", "Third question?", "Early Blight"):
            self.assertContains(page, text)
        self.assertContains(page, "https://extension.example.org/early-blight")
        self.assertContains(page, "not a confirmed diagnosis")
        self.assertContains(page, "88.0%")

    def test_history_in_prompt_is_bounded(self):
        fake = FakeGemini()
        self.run_chat(fake, [f"Question number {i}?" for i in range(6)])
        last_prompt = fake.prompts[-1]
        self.assertNotIn("Question number 0?", last_prompt)
        self.assertIn("Question number 4?", last_prompt)

    def test_duplicate_submission_token_is_ignored(self):
        fake = FakeGemini()
        token = uuid.uuid4()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test"}), \
             patch("advisory.chat.requests.post", side_effect=fake.post), \
             patch("advisory.chat.retrieve_evidence", return_value=EVIDENCE):
            self.ask("Same question", token=token)
            response = self.ask("Same question", token=token)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AIChatMessage.objects.count(), 2)

    def test_question_limit_per_case(self):
        fake = FakeGemini()
        with patch.object(chat, "MAX_QUESTIONS_PER_CASE", 1):
            self.run_chat(fake, ["One", "Two"])
        self.assertEqual(AIChatMessage.objects.filter(role="farmer").count(), 1)

    def test_content_is_escaped(self):
        fake = FakeGemini()
        self.run_chat(fake, ["<script>alert(1)</script>"])
        page = self.client.get(self.url)
        self.assertNotContains(page, "<script>alert(1)</script>")
        self.assertContains(page, "&lt;script&gt;alert(1)&lt;/script&gt;")


class LanguageTests(ChatTestCase):
    def select_language(self, code):
        response = self.client.post(reverse("set_language"), {"language": code, "next": "/"})
        self.assertEqual(response.status_code, 302)

    def test_language_switch_changes_next_reply_and_keeps_history(self):
        fake = FakeGemini([
            gemini_payload(),
            gemini_payload(answer=ANSWER_SW),
            gemini_payload(answer=ANSWER_AM),
        ])
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test"}), \
             patch("advisory.chat.requests.post", side_effect=fake.post), \
             patch("advisory.chat.retrieve_evidence", return_value=EVIDENCE):
            self.select_language("en")
            self.ask("Is this serious?")
            self.select_language("sw")
            self.ask("Nifanye nini sasa?")
            self.select_language("am")
            self.ask("ምን ማድረግ አለብኝ?")
        messages = list(AIChatMessage.objects.filter(role="assistant"))
        self.assertEqual([m.language for m in messages], ["en", "sw", "am"])
        self.assertEqual([m.content for m in messages], [ANSWER_EN, ANSWER_SW, ANSWER_AM])
        self.assertIn("Swahili", fake.prompts[1])
        self.assertIn("Amharic", fake.prompts[2])
        self.assertIn("Is this serious?", fake.prompts[2])
        self.assertTrue(AIChatMessage.objects.filter(content="ምን ማድረግ አለብኝ?").exists())
        self.assertEqual(AIChatMessage.objects.count(), 6)
        self.farmer.refresh_from_db()
        self.assertEqual(self.farmer.preferred_language, "am")
        self.assertEqual(fake.rewrites, 2)

    def test_selector_persists_preference_and_login_restores_it(self):
        self.assertEqual(self.farmer.preferred_language, "en")
        self.select_language("sw")
        self.farmer.refresh_from_db()
        self.assertEqual(self.farmer.preferred_language, "sw")
        self.client.logout()
        fresh = Client()
        response = fresh.post(reverse("login"), {"email": "f1@example.com", "password": "pwd"})
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "sw")

    def test_preferences_page_saves_language(self):
        self.client.post(reverse("settings-preferences"), {"language": "am"})
        self.farmer.refresh_from_db()
        self.assertEqual(self.farmer.preferred_language, "am")

    def test_unsupported_language_is_not_saved(self):
        self.client.post(reverse("set_language"), {"language": "fr", "next": "/"})
        self.farmer.refresh_from_db()
        self.assertEqual(self.farmer.preferred_language, "en")

    def test_registration_stores_active_language(self):
        response = Client().post(reverse("register"), {
            "full_name": "New Farmer", "email": "new@example.com",
            "password1": "S3cure-pass-word!", "password2": "S3cure-pass-word!",
            "country": "KE", "region": "Nakuru", "subcounty": "Njoro", "ward": "Mau", "town": "Njoro",
            "terms_accepted": "on",
        }, HTTP_ACCEPT_LANGUAGE="sw")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(email="new@example.com").preferred_language, "sw")


class ContextTests(ChatTestCase):
    def test_prompt_contains_case_and_dated_weather_context(self):
        self.case.completed_at = timezone.now() - timedelta(days=2)
        self.case.save(update_fields=["completed_at"])
        observed = timezone.now()
        WeatherContext.objects.create(
            diagnosis=self.case, location_name="Plot", location_source="explicit",
            latitude=1, longitude=2, provider="Open-Meteo", observed_at=observed,
            temperature=21, humidity=83, precipitation=2, wind_speed=4, condition="Rain",
        )
        self.make_case(self.farmer, "Tomato_Late_blight", 0.9)
        self.make_case(self.other, "Tomato_Other_farmer_only", 0.9)
        fake = FakeGemini()
        self.run_chat(fake, ["Did the rain matter?"])
        prompt = fake.prompts[0]
        self.assertIn("Tomato_Early_blight", prompt)
        self.assertIn("Brown rings on lower leaves", prompt)
        self.assertIn("0.88", prompt)
        self.assertIn(observed.isoformat(), prompt)
        self.assertIn('"timing": "later_observation"', prompt)
        self.assertIn('"is_historical_context": false', prompt)
        self.assertIn("Tomato_Late_blight", prompt)
        self.assertNotIn("Tomato_Other_farmer_only", prompt)
        kwargs = self.retrieve.call_args.kwargs
        self.assertEqual(kwargs["crop"], "tomato")
        self.assertEqual(kwargs["condition"], "Tomato_Early_blight")
        self.assertEqual(kwargs["query_texts"][0], "Did the rain matter?")
        self.assertEqual(fake.rewrites, 0)


class GroundingTests(ChatTestCase):
    def last_reply(self):
        return AIChatMessage.objects.filter(role="assistant").latest("id")

    def test_valid_answer_saves_only_cited_valid_sources(self):
        evidence = EVIDENCE + [{
            **EVIDENCE[0], "chunk_id": "other_1", "source_id": "other",
            "title": "Uncited", "url": "javascript:alert(1)",
        }]
        self.run_chat(FakeGemini(), ["How to manage?"], evidence=evidence)
        reply = self.last_reply()
        self.assertEqual(reply.kind, "answer")
        self.assertEqual([s["chunk_id"] for s in reply.sources], EVIDENCE_IDS)
        self.assertEqual(reply.sources[0]["url"], "https://extension.example.org/early-blight")

    def test_unsafe_urls_are_dropped_from_sources(self):
        evidence = [{**EVIDENCE[0], "url": "javascript:alert(1)"}]
        self.run_chat(FakeGemini(), ["How to manage?"], evidence=evidence)
        self.assertEqual(self.last_reply().sources[0]["url"], "")

    def test_unknown_citation_falls_back_to_source_excerpts(self):
        self.run_chat(FakeGemini([gemini_payload(citations=["invented-id"])]), ["Q?"])
        reply = self.last_reply()
        self.assertEqual(reply.kind, "excerpts")
        self.assertIn("Remove infected lower leaves and rotate crops.", reply.content)
        self.assertNotIn(ANSWER_EN, reply.content)
        self.assertEqual(reply.sources[0]["chunk_id"], EVIDENCE_IDS[0])

    def test_missing_citations_are_rejected(self):
        self.run_chat(FakeGemini([gemini_payload(citations=[])]), ["Q?"])
        self.assertEqual(self.last_reply().kind, "excerpts")

    def test_invented_dose_or_schedule_is_rejected(self):
        unsafe = [
            "Early blight is a possible explanation. Mix 5 ml per litre of water for control.",
            "Early blight is a possible explanation. Remove infected lower leaves every 7 days.",
            "Early blight is a possible explanation. Apply a fungicide on the lower leaves.",
        ]
        for text in unsafe:
            AIChatMessage.objects.all().delete()
            self.run_chat(FakeGemini([gemini_payload(answer=text, answer_en=text)]), ["Q?"])
            self.assertEqual(self.last_reply().kind, "excerpts", text)

    def test_localized_dose_is_rejected_even_when_english_text_is_clean(self):
        self.client.post(reverse("set_language"), {"language": "sw", "next": "/"})
        text = "Changanya gramu 5 kwa lita ya maji. Ondoa majani ya chini yaliyoambukizwa."
        self.run_chat(FakeGemini([gemini_payload(answer=text)]), ["Q?"])
        self.assertEqual(self.last_reply().kind, "excerpts")

    def test_unsupported_claims_are_rejected(self):
        text = (
            "Tomato plants infected by this organism produce dangerous mycotoxins contaminating "
            "neighbouring greenhouse vegetables permanently across seasons."
        )
        self.run_chat(FakeGemini([gemini_payload(answer=text, answer_en=text)]), ["Q?"])
        self.assertEqual(self.last_reply().kind, "excerpts")

    def test_overconfident_diagnosis_is_rejected(self):
        text = "This is definitely early blight. Remove infected lower leaves."
        self.run_chat(FakeGemini([gemini_payload(answer=text, answer_en=text)]), ["Q?"])
        self.assertEqual(self.last_reply().kind, "excerpts")

    def test_insufficient_evidence_returns_notice_without_sources(self):
        self.run_chat(FakeGemini([gemini_payload(insufficient=True, answer="Made up claim")]), ["Weather tomorrow?"])
        reply = self.last_reply()
        self.assertEqual(reply.kind, "notice")
        self.assertEqual(reply.sources, [])
        self.assertNotIn("Made up claim", reply.content)

    def test_prompt_contains_safety_rules(self):
        fake = FakeGemini()
        self.run_chat(fake, ["Q?"])
        self.assertIn("Never give a product, dose, mixing rate, spray schedule", fake.prompts[0])
        self.assertIn("untrusted data", fake.prompts[0])


class ConservativeCaseTests(ChatTestCase):
    def assert_notice_without_ai(self, case):
        self.url = reverse("case-chat", args=[case.id])
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test"}), \
             patch("advisory.chat.requests.post") as post, \
             patch("advisory.chat.retrieve_evidence") as retrieve:
            response = self.ask("What should I do?")
        self.assertEqual(response.status_code, 302)
        post.assert_not_called()
        retrieve.assert_not_called()
        reply = AIChatMessage.objects.filter(session__case=case, role="assistant").get()
        self.assertEqual(reply.kind, "notice")
        self.assertEqual(reply.sources, [])
        return reply

    def test_healthy_case(self):
        reply = self.assert_notice_without_ai(self.make_case(self.farmer, "Tomato_healthy", 0.97))
        self.assertIn("not a guarantee", reply.content)

    def test_uncertain_case(self):
        reply = self.assert_notice_without_ai(self.make_case(self.farmer, "Tomato_Late_blight", 0.4))
        self.assertIn("uncertain", reply.content)

    def test_spider_mite_case(self):
        case = self.make_case(self.farmer, "Tomato_Spider_mites_Two_spotted_spider_mite", 0.95)
        reply = self.assert_notice_without_ai(case)
        self.assertIn("outside the disease scope", reply.content)

    def test_notice_is_translated(self):
        self.client.post(reverse("set_language"), {"language": "sw", "next": "/"})
        reply = self.assert_notice_without_ai(self.make_case(self.farmer, "Tomato_healthy", 0.97))
        self.assertIn("mmea wenye afya", reply.content)


class FailureTests(ChatTestCase):
    def test_chroma_unavailable_shows_controlled_error_and_saves_nothing(self):
        fake = FakeGemini()
        responses = self.run_chat(fake, ["Q?"], evidence=None)
        self.assertEqual(responses[0].status_code, 200)
        self.assertContains(responses[0], "temporarily unavailable")
        self.assertContains(responses[0], "Q?")
        self.assertFalse(AIChatMessage.objects.exists())
        self.assertEqual(fake.prompts, [])

    def test_no_sources_found_is_a_controlled_error(self):
        responses = self.run_chat(FakeGemini(), ["Q?"], evidence=[])
        self.assertContains(responses[0], "No verified sources")
        self.assertFalse(AIChatMessage.objects.exists())

    def test_gemini_error_falls_back_to_source_excerpts(self):
        for error in (requests.ConnectionError("down"), requests.Timeout("slow")):
            AIChatMessage.objects.all().delete()
            fake = FakeGemini(error=error)
            self.run_chat(fake, ["Q?"])
            reply = AIChatMessage.objects.get(role="assistant")
            self.assertEqual(reply.kind, "excerpts")
            self.assertIn("Remove infected lower leaves and rotate crops.", reply.content)
            self.assertEqual(len(fake.calls), 1)

    def test_http_error_falls_back_without_retrying(self):
        fake = FakeGemini(statuses=[503])
        self.run_chat(fake, ["Q?"])
        self.assertEqual(AIChatMessage.objects.get(role="assistant").kind, "excerpts")
        self.assertEqual(len(fake.calls), 1)

    def test_missing_gemini_key_falls_back_to_source_excerpts(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("advisory.chat.retrieve_evidence", return_value=EVIDENCE):
            self.ask("Q?")
        reply = AIChatMessage.objects.get(role="assistant")
        self.assertEqual(reply.kind, "excerpts")
        self.assertEqual(reply.sources[0]["title"], "Early Blight")

    def test_malformed_gemini_json_falls_back(self):
        self.run_chat(FakeGemini(["not json"]), ["Q?"])
        self.assertEqual(AIChatMessage.objects.get(role="assistant").kind, "excerpts")

    def test_chroma_query_failure_is_controlled(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("advisory.services.get_chroma_collection") as collection:
            collection.return_value.query.side_effect = RuntimeError("down")
            response = self.ask("Q?")
        self.assertContains(response, "temporarily unavailable")
        self.assertFalse(AIChatMessage.objects.exists())


class NoAutomaticReferralTests(ChatTestCase):
    def test_chat_never_creates_expert_referral_and_links_to_review(self):
        self.run_chat(FakeGemini(), ["Should an expert look at this?"])
        self.make_case(self.farmer, "Tomato_Late_blight", 0.4)
        self.assertEqual(ExpertReferral.objects.count(), 0)
        page = self.client.get(self.url)
        self.assertContains(page, reverse("case-detail", args=[self.case.id]) + "#expert-review")
        self.assertContains(page, "Nothing from this chat is sent to an expert")
        detail = self.client.get(reverse("case-detail", args=[self.case.id]))
        self.assertContains(detail, self.url)


class GuidanceLibraryTests(ChatTestCase):
    def test_all_thirteen_documents_load_with_valid_links(self):
        documents = load_documents()
        self.assertEqual(len(documents), 13)
        for document in documents:
            self.assertTrue(document["url"].startswith("https://"))
            self.assertTrue(document["publisher"])

    def test_page_lists_all_documents_without_a_case(self):
        response = self.client.get(reverse("guidance-index"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["documents"]), 13)
        self.assertContains(response, "not a guarantee")

    def test_crop_condition_and_search_filters(self):
        documents = load_documents()
        self.assertEqual({d["crop"] for d in filter_documents(documents, crop="potato")}, {"potato"})
        self.assertEqual(len(filter_documents(documents, crop="potato")), 2)
        self.assertEqual(len(filter_documents(documents, crop="maize")), 3)
        self.assertEqual(len(filter_documents(documents, crop="tomato")), 8)
        one = filter_documents(documents, condition="Tomato_Late_blight")
        self.assertEqual([d["source_id"] for d in one], ["tomato_late_blight"])
        found = filter_documents(documents, query="gray leaf spot")
        self.assertIn("maize_gray_leaf_spot", [d["source_id"] for d in found])
        self.assertEqual(filter_documents(documents, crop="maize", query="late blight"), [])

    def test_view_applies_filters_and_ignores_invalid_values(self):
        url = reverse("guidance-index")
        response = self.client.get(url, {"crop": "maize"})
        self.assertEqual({d["crop"] for d in response.context["documents"]}, {"maize"})
        response = self.client.get(url, {"crop": "potato", "q": "late blight"})
        self.assertEqual([d["source_id"] for d in response.context["documents"]], ["potato_late_blight"])
        response = self.client.get(url, {"crop": "wheat", "condition": "Nope"})
        self.assertEqual(len(response.context["documents"]), 13)
        response = self.client.get(url, {"q": "zzzz-no-match"})
        self.assertContains(response, "No guidance documents match")

    def test_library_requires_farmer(self):
        self.client.force_login(self.expert)
        self.assertEqual(self.client.get(reverse("guidance-index")).status_code, 403)


class GeminiTransportTests(ChatTestCase):
    def last_reply(self):
        return AIChatMessage.objects.filter(role="assistant").latest("id")

    def test_truncated_or_malformed_json_is_never_accepted(self):
        cases = [
            ('{"answer": "Early bl', "MAX_TOKENS"),
            ('{"answer": "Early bl', "STOP"),
            (gemini_payload(), "MAX_TOKENS"),
            ("[]", "STOP"),
        ]
        for payload, finish in cases:
            AIChatMessage.objects.all().delete()
            fake = FakeGemini([payload], finish=finish)
            self.run_chat(fake, ["How do I manage this?"])
            self.assertEqual(self.last_reply().kind, "excerpts", (payload, finish))
            self.assertNotIn("Early bl", self.last_reply().content.replace("Early blight is caused", ""))

    def test_english_question_makes_a_single_structured_low_thinking_request(self):
        fake = FakeGemini()
        with patch.dict(os.environ, {"GEMINI_ADVISORY_MODEL": "gemini-3.8-flash"}):
            self.run_chat(fake, ["How do I manage this?"])
        self.assertEqual(self.last_reply().kind, "answer")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.rewrites, 0)
        call = fake.calls[0]
        config = call["json"]["generationConfig"]
        self.assertEqual(config["responseMimeType"], "application/json")
        self.assertEqual(config["responseSchema"], chat.ANSWER_SCHEMA)
        self.assertEqual(config["maxOutputTokens"], chat.ANSWER_MAX_TOKENS)
        self.assertEqual(config["thinkingConfig"], {"thinkingLevel": "low"})
        self.assertEqual(call["timeout"], chat.ANSWER_TIMEOUT)
        self.assertIn("gemini-3.8-flash:generateContent", call["url"])
        self.assertEqual(call["headers"], {"x-goog-api-key": "test"})
        self.assertNotIn("test", call["url"].replace("gemini-3.8-flash", "").replace("generateContent", ""))

    def test_unsupported_thinking_setting_degrades_once_and_is_remembered(self):
        fake = FakeGemini(statuses=[400, 200])
        self.run_chat(fake, ["First?", "Second?"])
        self.assertEqual(AIChatMessage.objects.filter(kind="answer").count(), 2)
        configs = [call["json"]["generationConfig"].get("thinkingConfig") for call in fake.calls]
        self.assertEqual(
            configs, [{"thinkingLevel": "low"}, {"thinkingBudget": 0}, {"thinkingBudget": 0}]
        )

    def test_non_english_question_is_rewritten_once_with_plain_text(self):
        for question in ("Nifanye nini sasa?", "ምን ማድረግ አለብኝ?"):
            AIChatMessage.objects.all().delete()
            fake = FakeGemini(rewrite_text='"early blight management tomato"\nignored line')
            self.run_chat(fake, [question])
            self.assertEqual(fake.rewrites, 1)
            self.assertEqual(len(fake.calls), 2)
            rewrite_config = fake.calls[0]["json"]["generationConfig"]
            self.assertNotIn("responseSchema", rewrite_config)
            self.assertEqual(fake.calls[0]["timeout"], chat.REWRITE_TIMEOUT)
            self.assertEqual(
                self.retrieve.call_args.kwargs["query_texts"][0], "early blight management tomato"
            )
            self.assertEqual(self.last_reply().kind, "answer")

    def test_rewrite_failure_still_answers_with_a_safe_search_query(self):
        for error in (requests.Timeout("slow"), requests.ConnectionError("down")):
            for question, expected in (("Nifanye nini sasa?", "Nifanye nini sasa?"), ("ምን ማድረግ አለብኝ?", "")):
                AIChatMessage.objects.all().delete()
                fake = FakeGemini(rewrite_error=error)
                self.run_chat(fake, [question])
                self.assertEqual(self.retrieve.call_args.kwargs["query_texts"][0], expected)
                self.assertEqual(self.last_reply().kind, "answer")
                self.assertEqual(self.last_reply().content, ANSWER_EN)

    def test_looks_english(self):
        self.assertTrue(chat.looks_english("How do I stop late blight spreading?"))
        self.assertTrue(chat.looks_english("tomato leaf spots"))
        self.assertFalse(chat.looks_english("Nifanye nini kuzuia ugonjwa huu?"))
        self.assertFalse(chat.looks_english("ምን ማድረግ አለብኝ?"))

    def test_database_lock_is_not_held_during_external_calls(self):
        baseline = len(connection.savepoint_ids)
        seen = []

        def reply(*args, **kwargs):
            seen.append(len(connection.savepoint_ids))
            return "notice", "ok", []

        with patch("advisory.chat.reply_to_question", side_effect=reply):
            self.ask("Q?")
        self.assertEqual(seen, [baseline])
        self.assertEqual(AIChatMessage.objects.count(), 2)

    def test_question_is_released_when_generation_raises(self):
        with patch("advisory.chat.reply_to_question", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                self.ask("Q?")
        self.assertFalse(AIChatMessage.objects.exists())

    def test_fallback_is_short_deduplicated_and_free_of_markdown(self):
        evidence = [
            {**EVIDENCE[0], "chunk_id": "a_0", "text": (
                "# Early Blight in Tomato Caused by Alternaria solani. It affects older leaves first. "
                "## Symptoms Dark concentric rings appear on older leaves. Leaves may turn yellow and drop."
            )},
            {**EVIDENCE[0], "chunk_id": "a_1", "text": (
                "Dark concentric rings appear on older leaves. Leaves may turn yellow and drop. "
                "## Management Remove infected leaves. Rotate crops every season and avoid overhead"
            )},
        ]
        self.run_chat(FakeGemini(error=requests.Timeout("slow")), ["Q?"], evidence=evidence)
        reply = self.last_reply()
        self.assertEqual(reply.kind, "excerpts")
        self.assertNotIn("#", reply.content)
        self.assertEqual(reply.content.count("Dark concentric rings"), 1)
        self.assertNotIn("overhead", reply.content)
        self.assertLessEqual(reply.content.count("•"), chat.FALLBACK_SENTENCES)
        self.assertEqual(len(reply.sources), 1)
        self.assertLess(len(reply.content), 700)

    def test_fallback_prefers_sentences_that_match_the_question(self):
        evidence = [{**EVIDENCE[0], "text": (
            "It affects older leaves first. Wind spreads spores between fields. "
            "Dark rings appear on leaves. Remove infected lower leaves and rotate crops."
        )}]
        content, _sources = chat.excerpt_fallback(evidence, "en", "rotate crops")
        self.assertIn("Remove infected lower leaves and rotate crops.", content)
