from django.db import migrations, models
import django.db.models.deletion


def assign_legacy_owner_and_author(apps, schema_editor):
    Teacher = apps.get_model("core", "Teacher")
    ClassGroup = apps.get_model("core", "ClassGroup")
    Session = apps.get_model("core", "Session")

    owner, _ = Teacher.objects.get_or_create(
        full_name="OnAibek",
        defaults={
            "pin_hash": "!",
            "is_active": True,
        },
    )

    ClassGroup.objects.filter(owner__isnull=True).update(owner=owner)
    Session.objects.filter(author__isnull=True).update(author=owner)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_rename_core_quiz_attempt_sm_idx_core_studen_student_f73b48_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="classgroup",
            name="owner",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="owned_classes", to="core.teacher"),
        ),
        migrations.AddField(
            model_name="session",
            name="author",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="owned_sessions", to="core.teacher"),
        ),
        migrations.AddField(
            model_name="session",
            name="is_shared_template",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="session",
            name="source_session",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="cloned_sessions", to="core.session"),
        ),
        migrations.AddField(
            model_name="sessiontask",
            name="hint1_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="sessiontask",
            name="hint1_unlock_attempts",
            field=models.PositiveIntegerField(default=2),
        ),
        migrations.AddField(
            model_name="sessiontask",
            name="hint2_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="sessiontask",
            name="hint2_unlock_attempts",
            field=models.PositiveIntegerField(default=3),
        ),
        migrations.AddField(
            model_name="sessiontask",
            name="hint3_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="sessiontask",
            name="hint3_unlock_attempts",
            field=models.PositiveIntegerField(default=3),
        ),
        migrations.AddField(
            model_name="sessiontask",
            name="hints_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(assign_legacy_owner_and_author, migrations.RunPython.noop),
    ]
