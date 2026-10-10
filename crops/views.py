from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from users.models import User
from .models import Crop
from .forms import CropForm

def farmer_required(view_func):
    @login_required
    def wrapped(request, *args, **kwargs):
        if request.user.role != User.Role.FARMER:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapped

def owned_crop(request, crop_id):
    return get_object_or_404(Crop, id=crop_id, farmer=request.user)

@farmer_required
def crop_list(request):
    crops = request.user.registered_crops.all()
    return render(request, "crops/crop_list.html", {"crops": crops})

@farmer_required
def crop_add(request):
    form = CropForm(request.POST or None, initial={"location": request.user.farm_location})
    if request.method == "POST" and form.is_valid():
        crop = form.save(commit=False)
        crop.farmer = request.user
        crop.save()
        return redirect("crop-detail", crop_id=crop.id)
    return render(request, "crops/crop_form.html", {"form": form, "is_edit": False})

@farmer_required
def crop_detail(request, crop_id):
    crop = owned_crop(request, crop_id)
    diagnoses = crop.diagnoses.filter(farmer=request.user)
    return render(request, "crops/crop_detail.html", {"crop": crop, "diagnoses": diagnoses})

@farmer_required
def crop_edit(request, crop_id):
    crop = owned_crop(request, crop_id)
    form = CropForm(request.POST or None, instance=crop)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("crop-detail", crop_id=crop.id)
    return render(request, "crops/crop_form.html", {"form": form, "is_edit": True, "crop": crop})
