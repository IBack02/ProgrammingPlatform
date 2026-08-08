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
# core/forms.py
from django import forms
from .models import Teacher

class TeacherAdminForm(forms.ModelForm):
    password = forms.CharField(
        min_length=6,
        max_length=15,
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Set or reset a teacher password (6-15 printable ASCII characters).",
    )

    class Meta:
        model = Teacher
        fields = ("full_name", "is_active", "password")

    def clean_password(self):
        password = self.cleaned_data.get("password") or ""
        if not password:
            return ""
        if not all(33 <= ord(char) <= 126 for char in password):
            raise ValidationError("Password must contain printable ASCII characters without spaces.")
        return password

    def save(self, commit=True):
        obj: Teacher = super().save(commit=False)
        password = self.cleaned_data.get("password") or ""
        if password:
            obj.set_password(password)
        if commit:
            obj.save()
        return obj