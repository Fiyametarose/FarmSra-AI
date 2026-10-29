import io
import shutil
import tempfile
from PIL import Image

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from users.models import User
from diagnoses.models import DiagnosisCase
from expert_reviews.models import ExpertReferral, ExpertReview


def make_test_image():
    image = Image.new("RGB", (30, 30), color="green")
    buf = io.BytesIO()
    image.save(buf, format="JPEG")
    return SimpleUploadedFile("specimen.jpg", buf.getvalue(), content_type="image/jpeg")


class InterfaceQualityTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_root = tempfile.mkdtemp()
        cls.settings_override = override_settings(MEDIA_ROOT=cls.media_root)
        cls.settings_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.settings_override.disable()
        shutil.rmtree(cls.media_root)

    def setUp(self):
        self.client = Client()
        self.farmer = User.objects.create_user(
            email="farmer_test@farmsra.test",
            password="testpassword",
            role=User.Role.FARMER,
            town="Nyeri",
            country="KE",
        )
        self.other_farmer = User.objects.create_user(
            email="other_farmer@farmsra.test",
            password="testpassword",
            role=User.Role.FARMER,
        )
        self.expert = User.objects.create_user(
            email="expert_test@farmsra.test",
            password="testpassword",
            role=User.Role.EXPERT,
        )

    def test_agricultural_experts_page_with_active_and_revoked_referrals(self):
        """
        Verify agricultural experts page provides active_referrals,
        correctly renders ref.diagnosis attributes, safe links,
        and excludes revoked or other farmers' referrals.
        """
        case1 = DiagnosisCase.objects.create(
            farmer=self.farmer,
            crop=DiagnosisCase.Crop.TOMATO,
            image=make_test_image(),
            status=DiagnosisCase.Status.COMPLETED,
            predicted_class="Tomato_Late_blight",
            model_confidence=0.94,
        )
        case2 = DiagnosisCase.objects.create(
            farmer=self.farmer,
            crop=DiagnosisCase.Crop.POTATO,
            image=make_test_image(),
            status=DiagnosisCase.Status.COMPLETED,
            predicted_class="Potato___Early_blight",
            model_confidence=0.88,
        )
        case_other = DiagnosisCase.objects.create(
            farmer=self.other_farmer,
            crop=DiagnosisCase.Crop.MAIZE,
            image=make_test_image(),
            status=DiagnosisCase.Status.COMPLETED,
            predicted_class="Corn_(maize)___Common_rust",
            model_confidence=0.91,
        )

        active_ref = ExpertReferral.objects.create(
            diagnosis=case1,
            state=ExpertReferral.State.REQUESTED,
        )
        revoked_ref = ExpertReferral.objects.create(
            diagnosis=case2,
            state=ExpertReferral.State.REVOKED,
        )
        other_ref = ExpertReferral.objects.create(
            diagnosis=case_other,
            state=ExpertReferral.State.REQUESTED,
        )

        self.client.force_login(self.farmer)
        response = self.client.get(reverse("agricultural-experts"))
        self.assertEqual(response.status_code, 200)

        # Context active_referrals must contain only farmer's active referral
        referrals_in_context = list(response.context["active_referrals"])
        self.assertIn(active_ref, referrals_in_context)
        self.assertNotIn(revoked_ref, referrals_in_context)
        self.assertNotIn(other_ref, referrals_in_context)

        # Template rendering checks
        self.assertContains(response, case1.reference)
        self.assertContains(response, case1.display_prediction)
        self.assertContains(response, reverse("case-detail", args=[case1.id]))
        self.assertNotContains(response, case2.reference)
        self.assertNotContains(response, case_other.reference)

    def test_dashboard_statistics_counts_all_diagnoses(self):
        """
        Dashboard Total Diagnoses stat must count all farmer's diagnoses,
        not just the three recent cases.
        """
        for i in range(5):
            DiagnosisCase.objects.create(
                farmer=self.farmer,
                crop=DiagnosisCase.Crop.TOMATO,
                image=make_test_image(),
                status=DiagnosisCase.Status.COMPLETED,
                predicted_class="Tomato_healthy",
                model_confidence=0.95,
            )

        self.client.force_login(self.farmer)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.context["total_diagnoses_count"], 5)
        self.assertEqual(len(response.context["recent_cases"]), 3)
        self.assertContains(response, "Total Diagnoses")
        # Template displays total_diagnoses_count (5)
        self.assertContains(response, ">5</h3>")

    def test_diagnosis_confidence_template_references_and_none_handling(self):
        """
        Verify case_detail and history render model_confidence and confidence_percent,
        and handle cases where model_confidence is None without template errors.
        """
        case_with_conf = DiagnosisCase.objects.create(
            farmer=self.farmer,
            crop=DiagnosisCase.Crop.TOMATO,
            image=make_test_image(),
            status=DiagnosisCase.Status.COMPLETED,
            predicted_class="Tomato_Late_blight",
            model_confidence=0.925,
        )
        case_no_conf = DiagnosisCase.objects.create(
            farmer=self.farmer,
            crop=DiagnosisCase.Crop.POTATO,
            image=make_test_image(),
            status=DiagnosisCase.Status.PENDING,
            model_confidence=None,
        )

        self.client.force_login(self.farmer)

        # Case detail with confidence
        res_detail = self.client.get(reverse("case-detail", args=[case_with_conf.id]))
        self.assertEqual(res_detail.status_code, 200)
        self.assertContains(res_detail, "92.5%")
        self.assertNotContains(res_detail, "None%")

        # Case detail with no confidence
        res_no_conf = self.client.get(reverse("case-detail", args=[case_no_conf.id]))
        self.assertEqual(res_no_conf.status_code, 200)
        self.assertNotContains(res_no_conf, "None%")

        # History list
        res_history = self.client.get(reverse("history"))
        self.assertEqual(res_history.status_code, 200)
        self.assertContains(res_history, "92.5%")

    def test_notification_settings_informational_page(self):
        """
        Notification settings page must be a truthful informational page
        linking to the real inbox, without non-persisting checkboxes or fake alerts.
        """
        self.client.force_login(self.farmer)
        response = self.client.get(reverse("settings-notifications"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Notification Delivery")
        self.assertContains(response, reverse("notifications:inbox"))
        self.assertNotContains(response, 'name="notify_weather"')
        self.assertNotContains(response, 'name="notify_reviews"')
        self.assertNotContains(response, "Save Notification Settings")

    def test_preferences_page_metric_units_and_language_selector(self):
        """
        Preferences page must retain working Django language selector,
        remove nonfunctional Imperial selector, and state metric unit standard.
        """
        self.client.force_login(self.farmer)
        response = self.client.get(reverse("settings-preferences"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("set_language"))
        self.assertContains(response, "Metric")
        self.assertNotContains(response, "Imperial (°F")

    def test_expert_dashboard_truthful_metrics(self):
        """
        Expert dashboard metrics must include total_assigned_count, pending_count,
        completed_count and not compute substring-based symptom priority.
        """
        case = DiagnosisCase.objects.create(
            farmer=self.farmer,
            crop=DiagnosisCase.Crop.TOMATO,
            image=make_test_image(),
            status=DiagnosisCase.Status.COMPLETED,
            predicted_class="Tomato_Late_blight",
            model_confidence=0.91,
            symptoms="spots spreading fast",
        )
        ref = ExpertReferral.objects.create(
            diagnosis=case,
            assigned_expert=self.expert,
            state=ExpertReferral.State.ASSIGNED,
        )

        self.client.force_login(self.expert)
        response = self.client.get(reverse("expert-dashboard"))
        self.assertEqual(response.status_code, 200)

        self.assertIn("total_assigned_count", response.context)
        self.assertIn("pending_count", response.context)
        self.assertIn("completed_count", response.context)
        self.assertNotIn("priority_count", response.context)
        self.assertEqual(response.context["total_assigned_count"], 1)
        self.assertEqual(response.context["pending_count"], 1)
        self.assertEqual(response.context["completed_count"], 0)
        self.assertContains(response, "Total Assigned")

    def test_ui_claims_accuracy(self):
        """
        Unsupported claims are replaced with accurate descriptions and the consent
        disclosure identifies every piece of case data shared with the assigned expert.
        """
        self.client.force_login(self.farmer)

        # Upload screen claims
        upload_resp = self.client.get(reverse("case-upload", args=["tomato"]))
        self.assertEqual(upload_resp.status_code, 200)
        self.assertContains(upload_resp, "Local server inference")
        self.assertContains(upload_resp, "Protected private storage")
        self.assertNotContains(upload_resp, "Edge AI classification")
        self.assertNotContains(upload_resp, "Encrypted storage")

        # Case detail consent claims
        case = DiagnosisCase.objects.create(
            farmer=self.farmer,
            crop=DiagnosisCase.Crop.TOMATO,
            image=make_test_image(),
            status=DiagnosisCase.Status.COMPLETED,
            predicted_class="Tomato_Late_blight",
            model_confidence=0.88,
            location="Nyeri North Plot",
        )
        case_resp = self.client.get(reverse("case-detail", args=[case.id]))
        self.assertEqual(case_resp.status_code, 200)
        self.assertContains(case_resp, "assigned agricultural expert")
        self.assertContains(case_resp, "case location")
        self.assertContains(case_resp, "prediction, confidence, and symptoms")
        self.assertNotContains(case_resp, "verified agricultural extension officer")
        self.assertNotContains(case_resp, "certified agricultural extension officer")
        self.assertNotContains(case_resp, "certified agricultural expert")

    def test_navigation_is_accessible_and_role_specific(self):
        self.client.force_login(self.farmer)
        farmer_response = self.client.get(reverse("dashboard"))
        self.assertContains(farmer_response, 'id="sidebarToggle"')
        self.assertContains(farmer_response, 'aria-controls="appSidebar"')
        self.assertContains(farmer_response, 'aria-expanded="false"')
        self.assertContains(farmer_response, reverse("guidance-index"))
        self.assertNotContains(farmer_response, reverse("expert-assigned-cases"))
        self.assertNotContains(farmer_response, "floating-chat-bubble")

        self.client.force_login(self.expert)
        expert_response = self.client.get(reverse("expert-dashboard"))
        self.assertContains(expert_response, reverse("expert-assigned-cases"))
        self.assertNotContains(expert_response, reverse("guidance-index"))
        self.assertNotContains(expert_response, "floating-chat-bubble")

    def test_registration_step_copy_translations_and_attributes(self):
        """
        Registration template must render data-step1 and data-step2 attributes on #step-copy,
        and dynamically localize them when requested in Swahili and Amharic.
        """
        # English
        resp_en = self.client.get(reverse("register"))
        self.assertEqual(resp_en.status_code, 200)
        self.assertContains(resp_en, 'data-step1="Step 1 of 2 · Add your personal details to continue."')
        self.assertContains(resp_en, 'data-step2="Step 2 of 2 · Personal details complete. Add your farm location."')
        self.assertContains(resp_en, "Create Farmer Account")

        # Swahili
        resp_sw = self.client.get(reverse("register"), HTTP_ACCEPT_LANGUAGE="sw")
        self.assertEqual(resp_sw.status_code, 200)
        self.assertContains(resp_sw, "Fungua Akaunti ya Mkulima")
        self.assertContains(resp_sw, "Hatua ya 1 ya 2")
        self.assertContains(resp_sw, "Hatua 2 ya 2")

        # Amharic
        resp_am = self.client.get(reverse("register"), HTTP_ACCEPT_LANGUAGE="am")
        self.assertEqual(resp_am.status_code, 200)
        self.assertContains(resp_am, "የገበሬ መለያ ፍጠር")
        self.assertContains(resp_am, "ደረጃ 1 ከ 2")
        self.assertContains(resp_am, "ደረጃ 2 ከ 2")

    def test_important_screens_render_swahili_and_amharic(self):
        """
        Verify that important screens render meaningful translated text in both Swahili and Amharic:
        farmer dashboard, diagnosis results, guidance, expert workflow, notification inbox,
        settings, and weather.
        """
        case = DiagnosisCase.objects.create(
            farmer=self.farmer,
            crop=DiagnosisCase.Crop.TOMATO,
            image=make_test_image(),
            status=DiagnosisCase.Status.COMPLETED,
            predicted_class="Tomato_Late_blight",
            model_confidence=0.88,
            location="Nyeri Farm",
        )
        ref = ExpertReferral.objects.create(
            diagnosis=case,
            assigned_expert=self.expert,
            state=ExpertReferral.State.ASSIGNED,
        )

        # 1. Farmer screens in Swahili
        self.client.force_login(self.farmer)

        # Farmer Dashboard
        resp = self.client.get(reverse("dashboard"), HTTP_ACCEPT_LANGUAGE="sw")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Dashibodi")
        self.assertContains(resp, "Chunguza Mmea")

        # Diagnosis Results / Case Detail
        resp = self.client.get(reverse("case-detail", args=[case.id]), HTTP_ACCEPT_LANGUAGE="sw")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sampuli ya Jani Iliyochambuliwa")
        self.assertContains(resp, "Tathmini ya Awali ya AI")

        # Guidance Detail
        resp = self.client.get(reverse("case-guidance", args=[case.id]), HTTP_ACCEPT_LANGUAGE="sw")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Mwongozo wa Kina wa Kilimo")

        # Notifications Inbox
        resp = self.client.get(reverse("notifications:inbox"), HTTP_ACCEPT_LANGUAGE="sw")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Arifa")

        # Settings Preferences
        resp = self.client.get(reverse("settings-preferences"), HTTP_ACCEPT_LANGUAGE="sw")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Mipangilio")
        self.assertContains(resp, "Lugha ya Mfumo")

        # Weather Widget / Search
        resp = self.client.get(reverse("weather:widget") + "?search=", HTTP_ACCEPT_LANGUAGE="sw")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Tafadhali ingiza jina la jiji au mji")
        self.assertContains(resp, "Tafuta")

        # 2. Farmer screens in Amharic
        # Farmer Dashboard
        resp = self.client.get(reverse("dashboard"), HTTP_ACCEPT_LANGUAGE="am")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ዳሽቦርድ")
        self.assertContains(resp, "ሰብል መመርመር")

        # Diagnosis Results / Case Detail
        resp = self.client.get(reverse("case-detail", args=[case.id]), HTTP_ACCEPT_LANGUAGE="am")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "የተመረመረ የቅጠል ናሙና")
        self.assertContains(resp, "ጊዜያዊ የ-AI ግምገማ")

        # Guidance Detail
        resp = self.client.get(reverse("case-guidance", args=[case.id]), HTTP_ACCEPT_LANGUAGE="am")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ዝርዝር የግብርና መመሪያ")

        # Notifications Inbox
        resp = self.client.get(reverse("notifications:inbox"), HTTP_ACCEPT_LANGUAGE="am")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ማሳወቂያዎች")

        # Settings Preferences
        resp = self.client.get(reverse("settings-preferences"), HTTP_ACCEPT_LANGUAGE="am")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ቅንብሮች")
        self.assertContains(resp, "የበይነገጽ ቋንቋ")

        # Weather Widget / Search
        resp = self.client.get(reverse("weather:widget") + "?search=", HTTP_ACCEPT_LANGUAGE="am")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "የአየር ሁኔታን ለመመልከት እባክዎ")
        self.assertContains(resp, "ፈልግ")

        # 3. Expert screens in Swahili and Amharic
        self.client.force_login(self.expert)

        # Expert Assigned Cases
        resp_sw = self.client.get(reverse("expert-assigned-cases"), HTTP_ACCEPT_LANGUAGE="sw")
        self.assertEqual(resp_sw.status_code, 200)
        self.assertContains(resp_sw, "Kesi Zilizopangwa")

        resp_am = self.client.get(reverse("expert-assigned-cases"), HTTP_ACCEPT_LANGUAGE="am")
        self.assertEqual(resp_am.status_code, 200)
        self.assertContains(resp_am, "የተመደቡ ጉዳዮች")

        # Expert Case Review Detail
        resp_sw = self.client.get(reverse("expert-case-detail", args=[ref.id]), HTTP_ACCEPT_LANGUAGE="sw")
        self.assertEqual(resp_sw.status_code, 200)
        self.assertContains(resp_sw, "Tathmini ya Kitaalamu")

        resp_am = self.client.get(reverse("expert-case-detail", args=[ref.id]), HTTP_ACCEPT_LANGUAGE="am")
        self.assertEqual(resp_am.status_code, 200)
        self.assertContains(resp_am, "ሙያዊ ግምገማ")

    def test_translation_catalogs_cover_all_template_strings(self):
        """
        Verify that all literal template translation strings in HTML templates
        exist with non-empty translations in both Swahili and Amharic PO catalogs.
        """
        import os
        import re
        import polib
        from django.conf import settings

        template_dirs = [
            settings.BASE_DIR / "templates",
            settings.BASE_DIR / "users/templates",
            settings.BASE_DIR / "diagnoses/templates",
            settings.BASE_DIR / "expert_reviews/templates",
            settings.BASE_DIR / "notifications/templates",
            settings.BASE_DIR / "weather/templates",
            settings.BASE_DIR / "crops/templates",
        ]

        pattern_trans = re.compile(r'{%\s*trans\s+(?:"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\')\s*%}')
        pattern_blocktrans = re.compile(r'{%\s*blocktrans\s*(?:trimmed)?\s*%}(.*?){%\s*endblocktrans\s*%}', re.DOTALL)

        found_keys = set()
        for root_dir in template_dirs:
            for dirpath, _, filenames in os.walk(root_dir):
                for f in filenames:
                    if f.endswith(".html"):
                        with open(os.path.join(dirpath, f), "r", encoding="utf-8") as fp:
                            content = fp.read()
                            for m in pattern_trans.finditer(content):
                                key = m.group(1) if m.group(1) is not None else m.group(2)
                                key = key.replace('\\"', '"').replace("\\'", "'")
                                found_keys.add(key)
                            for m in pattern_blocktrans.finditer(content):
                                text = m.group(1).strip()
                                normalized = re.sub(r'\s+', ' ', text)
                                normalized = normalized.replace('%', '%%')
                                normalized = re.sub(
                                    r'{{\s*([\w.]+)\s*}}',
                                    r'%(\1)s',
                                    normalized,
                                )
                                found_keys.add(normalized)

        po_sw = polib.pofile(str(settings.BASE_DIR / "locale/sw/LC_MESSAGES/django.po"))
        po_am = polib.pofile(str(settings.BASE_DIR / "locale/am/LC_MESSAGES/django.po"))

        missing_sw = []
        missing_am = []
        for k in found_keys:
            e_sw = po_sw.find(k)
            if not e_sw or not e_sw.msgstr:
                missing_sw.append(k)
            e_am = po_am.find(k)
            if not e_am or not e_am.msgstr:
                missing_am.append(k)

        self.assertEqual(missing_sw, [], f"Missing Swahili translations for: {missing_sw}")
        self.assertEqual(missing_am, [], f"Missing Amharic translations for: {missing_am}")
