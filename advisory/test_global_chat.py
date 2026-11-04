import uuid
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from advisory.chat import validate_answer
from advisory import chat
from advisory.models import AIChatMessage, AIChatSession
from diagnoses.models import DiagnosisCase


class GlobalChatTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.farmer = users.objects.create_user(
            "farmer-chat@example.com", "password", role="farmer"
        )
        self.other = users.objects.create_user(
            "other-chat@example.com", "password", role="farmer"
        )
        self.expert = users.objects.create_user(
            "expert-chat@example.com", "password", role="expert"
        )
        self.admin = users.objects.create_superuser(
            "admin-chat@example.com", "password"
        )
        self.case = DiagnosisCase.objects.create(
            farmer=self.farmer, crop="tomato", status="completed",
            predicted_class="Tomato_Early_blight", model_confidence=.9,
            symptoms="Dark leaf spots",
        )
        self.other_case = DiagnosisCase.objects.create(
            farmer=self.other, crop="maize", status="completed",
            predicted_class="Corn_Common_rust", model_confidence=.9,
        )

    def test_launcher_is_farmer_only(self):
        self.client.force_login(self.farmer)
        self.assertContains(self.client.get(reverse("dashboard")), "floatingChatLauncher")
        self.client.force_login(self.expert)
        self.assertNotContains(self.client.get(reverse("expert-dashboard")), "floatingChatLauncher")
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("global-chat-state")).status_code, 403)

    @patch("advisory.views.chat.reply_to_question")
    def test_general_conversation_persists_without_case(self, reply):
        reply.return_value = ("answer", "Inspect leaves regularly.", [])
        self.client.force_login(self.farmer)
        token = uuid.uuid4()
        response = self.client.post(reverse("global-chat-send"), {
            "question": "How do I monitor tomato plants?", "crop": "tomato", "token": token,
        })
        self.assertEqual(response.status_code, 200)
        session = AIChatSession.objects.get(farmer=self.farmer, case=None)
        self.assertEqual(session.messages.count(), 2)
        self.assertIsNone(session.case_id)
        state = self.client.get(reverse("global-chat-state")).json()
        self.assertEqual(state["general_session"], str(session.id))
        self.assertEqual(len(next(item for item in state["sessions"] if item["id"] == str(session.id))["messages"]), 2)

    @patch("advisory.views.chat.reply_to_question")
    def test_case_selection_uses_owned_case_context(self, reply):
        reply.return_value = ("answer", "Case-specific answer.", [])
        self.client.force_login(self.farmer)
        response = self.client.post(reverse("global-chat-send"), {
            "question": "What should I inspect?", "case": self.case.id, "token": uuid.uuid4(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(reply.call_args.args[1], self.case)
        denied = self.client.post(reverse("global-chat-send"), {
            "question": "Show the other case", "case": self.other_case.id, "token": uuid.uuid4(),
        })
        self.assertEqual(denied.status_code, 404)

    @patch("advisory.views.chat.reply_to_question")
    def test_duplicate_token_does_not_duplicate_messages(self, reply):
        reply.return_value = ("answer", "Answer.", [])
        self.client.force_login(self.farmer)
        token = uuid.uuid4()
        data = {"question": "Tomato prevention?", "crop": "tomato", "token": token}
        self.assertEqual(self.client.post(reverse("global-chat-send"), data).status_code, 200)
        duplicate = self.client.post(reverse("global-chat-send"), data)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertEqual(AIChatMessage.objects.count(), 2)
        reply.assert_called_once()

    @patch("advisory.views.chat.reply_to_question")
    def test_new_answers_follow_selected_language(self, reply):
        reply.return_value = ("answer", "Localized answer.", [])
        self.client.force_login(self.farmer)
        for language in ("en", "sw", "am"):
            self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = language
            response = self.client.post(reverse("global-chat-send"), {
                "question": "Crop question", "crop": "potato", "token": uuid.uuid4(),
            })
            self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(AIChatMessage.objects.filter(role="assistant").values_list("language", flat=True)),
            ["en", "sw", "am"],
        )

    def test_unknown_citation_is_rejected(self):
        evidence = [{"chunk_id": "approved-1", "text": "Rotate crops to reduce disease pressure."}]
        data = {
            "answer": "Rotate crops to reduce disease pressure.",
            "answer_en": "Rotate crops to reduce disease pressure.",
            "citations": ["invented"], "insufficient_evidence": False,
        }
        self.assertIsNone(validate_answer(data, evidence, {}, "en"))

    @patch("advisory.chat.generate_chat_answer")
    @patch("advisory.chat.retrieve_evidence")
    def test_general_reply_retrieves_crop_evidence_without_condition(self, retrieve, generate):
        retrieve.return_value = [{
            "chunk_id": "approved-1", "source_id": "source-1", "title": "Guide",
            "publisher": "Extension", "date": "", "url": "https://example.test/guide",
            "text": "Rotate crops to reduce disease pressure.",
        }]
        generate.return_value = {
            "answer": "Rotate crops to reduce disease pressure.",
            "answer_en": "Rotate crops to reduce disease pressure.",
            "insufficient_evidence": False, "citations": ["approved-1"],
        }
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
            kind, content, sources = chat.reply_to_question(
                self.farmer, None, "How can I prevent disease?", "en", [], crop="maize"
            )
        self.assertEqual(kind, "answer")
        self.assertEqual(sources[0]["chunk_id"], "approved-1")
        self.assertEqual(retrieve.call_args.kwargs["crop"], "maize")
        self.assertIsNone(retrieve.call_args.kwargs["condition"])

    @patch("advisory.views.chat.reply_to_question")
    def test_existing_case_chat_still_uses_same_session(self, reply):
        reply.return_value = ("answer", "Existing case reply.", [])
        self.client.force_login(self.farmer)
        response = self.client.post(reverse("case-chat", args=[self.case.id]), {
            "question": "Follow-up question", "token": uuid.uuid4(),
        })
        self.assertRedirects(response, reverse("case-chat", args=[self.case.id]))
        self.assertEqual(AIChatSession.objects.get(case=self.case).messages.count(), 2)
