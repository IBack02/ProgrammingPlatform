from django import forms
from django.conf import settings
from django.core.validators import MaxValueValidator

from .models import WalletEntry


class AwardForm(forms.Form):
    class_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    student_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    request_id = forms.UUIDField(widget=forms.HiddenInput)
    amount = forms.IntegerField(min_value=1, widget=forms.NumberInput(attrs={"min": 1, "step": 1, "class": "award-amount"}))
    reason = forms.CharField(max_length=240, widget=forms.TextInput(attrs={"class": "award-reason", "required": True}))

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("auto_id", False)  # Each student has a separate form on the same page.
        super().__init__(*args, **kwargs)
        self.fields["amount"].max_value = settings.TOWER_MANUAL_AWARD_LIMIT
        self.fields["amount"].validators.append(MaxValueValidator(settings.TOWER_MANUAL_AWARD_LIMIT))
        self.fields["amount"].widget.attrs["max"] = settings.TOWER_MANUAL_AWARD_LIMIT


class AdjustmentForm(forms.Form):
    request_id = forms.UUIDField(widget=forms.HiddenInput)
    currency = forms.ChoiceField(choices=WalletEntry.Currency.choices)
    delta = forms.IntegerField()
    reason = forms.CharField(max_length=240, widget=forms.Textarea(attrs={"rows": 3, "cols": 60}))

    def clean_delta(self):
        delta = self.cleaned_data["delta"]
        if delta == 0 or abs(delta) > settings.TOWER_ADMIN_ADJUSTMENT_LIMIT:
            raise forms.ValidationError("Укажите ненулевую целую сумму в установленном лимите.")
        return delta
