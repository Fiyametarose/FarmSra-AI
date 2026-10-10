from django.urls import path
from . import views

urlpatterns = [
    path("", views.crop_list, name="crop-list"),
    path("add/", views.crop_add, name="crop-add"),
    path("<uuid:crop_id>/", views.crop_detail, name="crop-detail"),
    path("<uuid:crop_id>/edit/", views.crop_edit, name="crop-edit"),
]
