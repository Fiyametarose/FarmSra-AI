from django.urls import path
from . import views

urlpatterns = [
    # Farmer consent endpoints
    path('cases/<uuid:case_id>/request-review/', views.request_review, name='request-review'),
    path('cases/<uuid:case_id>/revoke-review/', views.revoke_review, name='revoke-review'),

    # Expert dashboard and workflows
    path('expert/', views.dashboard, name='expert-dashboard'),
    path('expert/assigned/', views.assigned_cases, name='expert-assigned-cases'),
    path('expert/referrals/<uuid:referral_id>/', views.case_detail, name='expert-case-detail'),
    path('expert/referrals/<uuid:referral_id>/review/', views.submit_review, name='expert-submit-review'),
    path('expert/referrals/<uuid:referral_id>/image/', views.case_image, name='expert-case-image'),
    path('expert/history/', views.history, name='expert-history'),
    path('expert/settings/', views.settings_account, name='expert-settings'),
]
