from django.urls import path
from . import views

urlpatterns = [
    path('case/<uuid:case_id>/advisory/', views.generate_case_advisory, name='generate-advisory'),
]
