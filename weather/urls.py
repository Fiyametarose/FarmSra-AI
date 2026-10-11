from django.urls import path
from . import views

app_name = 'weather'

urlpatterns = [
    path('widget/', views.weather_widget, name='widget'),
]
