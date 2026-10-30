from django.urls import path
from . import views

urlpatterns = [
    path('case/<uuid:case_id>/advisory/', views.generate_case_advisory, name='generate-advisory'),
    path('cases/<uuid:case_id>/chat/', views.case_chat, name='case-chat'),
]
