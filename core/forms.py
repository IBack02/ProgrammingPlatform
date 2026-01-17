from django import forms
from django.core.exceptions import ValidationError
from .models import Student


class StudentAdminForm(forms.ModelForm):
    pin = forms.CharField(
        required=False,
        max_length=6,
        min_length=6,
        help_text="Введите 6 цифр. При сохранении будет захэшировано."
    )

    class Meta:
        model = Student
        fields = ["full_name", "class_group", "is_active", "pin"]

    def clean_pin(self):
        pin = (self.cleaned_data.get("pin") or "").strip()
        if pin == "":
            return ""
        if not pin.isdigit() or len(pin) != 6:
            raise ValidationError("PIN должен быть ровно из 6 цифр.")
        return pin

    def save(self, commit=True):
        obj: Student = super().save(commit=False)
        pin = self.cleaned_data.get("pin") or ""
        if pin:
            obj.set_pin(pin)
        if commit:
            obj.save()
        return obj
