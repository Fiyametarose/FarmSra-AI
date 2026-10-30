from django.urls import path

from advisory.views import guidance_library

from . import views


urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("diagnose/", views.select_crop, name="select-crop"),
    path("diagnose/<str:crop>/", views.upload, name="case-upload"),
    path("cases/<uuid:case_id>/analyzing/", views.analyzing, name="case-analyzing"),
    path("cases/<uuid:case_id>/process/", views.process_case, name="case-process"),
    path("cases/<uuid:case_id>/recover/", views.recover_case, name="case-recover"),
    path("cases/<uuid:case_id>/guidance/", views.case_guidance, name="case-guidance"),
    path("guidance/", guidance_library, name="guidance-index"),
    path("guidance-hub/", guidance_library, name="guidance"),
    path("experts/", views.agricultural_experts, name="agricultural-experts"),
    path("cases/<uuid:case_id>/", views.case_detail, name="case-detail"),
    path("cases/<uuid:case_id>/image/", views.case_image, name="case-image"),
    path("history/", views.history, name="history"),
]
