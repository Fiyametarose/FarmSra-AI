from django.urls import path
from . import views

urlpatterns = [
    path('case/<uuid:case_id>/advisory/', views.generate_case_advisory, name='generate-advisory'),
    path('actions/<uuid:action_id>/', views.update_action, name='update-action'),
    path('case/<uuid:case_id>/advisory/status/', views.advisory_status, name='advisory-status'),
    path('cases/<uuid:case_id>/chat/', views.case_chat, name='case-chat'),
    path('chat/state/', views.global_chat_state, name='global-chat-state'),
    path('chat/send/', views.global_chat_send, name='global-chat-send'),
]
