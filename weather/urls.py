from django.urls import path
from . import views

app_name = 'weather'

urlpatterns = [
    path('widget/', views.weather_widget, name='widget'),
    path('cases/<uuid:case_id>/capture/', views.capture_case_weather, name='capture-case-weather'),
]
