from django import forms
from .models import Crop

class CropForm(forms.ModelForm):
    class Meta:
        model = Crop
        fields = ("name", "species", "planting_date", "location", "notes", "status")
        widgets = {
            "planting_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_species(self):
        species = self.cleaned_data.get("species")
        if self.instance and self.instance.pk:
            if species != self.instance.species and self.instance.diagnoses.exists():
                raise forms.ValidationError("Cannot change the species of a crop that has associated diagnosis cases.")
        return species

    def clean_planting_date(self):
        planting_date = self.cleaned_data.get("planting_date")
        if planting_date:
            from datetime import date
            if planting_date > date.today():
                raise forms.ValidationError("Planting date cannot be in the future.")
        return planting_date
