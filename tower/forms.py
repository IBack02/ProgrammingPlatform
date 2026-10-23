from django import forms
from django.conf import settings
from django.core.validators import MaxValueValidator

from .models import RewardPolicy, WalletEntry


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


class RewardPolicyForm(forms.ModelForm):
    class Meta:
        model = RewardPolicy
        fields = ("race_first", "race_second", "race_third", "task_solved", "page_opened")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.min_value = 0
            field.validators.append(MaxValueValidator(settings.TOWER_MANUAL_AWARD_LIMIT))
            field.widget.attrs.update(min=0, max=settings.TOWER_MANUAL_AWARD_LIMIT, step=1)

    def clean(self):
        values = super().clean()
        if all(key in values for key in ("race_first", "race_second", "race_third")):
            if not values["race_first"] >= values["race_second"] >= values["race_third"]:
                self.add_error("race_second", "1 ≥ 2 ≥ 3")
        return values


class BatchAwardForm(AwardForm):
    student_id = None
    student_ids = forms.TypedMultipleChoiceField(coerce=int, choices=())

    def __init__(self, *args, students, **kwargs):
        kwargs.setdefault("auto_id", "batch_%s")
        super().__init__(*args, **kwargs)
        self.fields["student_ids"].choices = [(student.pk, student.full_name) for student in students]
