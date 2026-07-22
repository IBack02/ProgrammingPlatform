# Generated manually for persistent, multi-worker request throttling.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_exam_examattempt_examclass_exam_allowed_classes_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="SecurityThrottle",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("key_hash", models.CharField(max_length=64, unique=True)),
                ("scope", models.CharField(db_index=True, max_length=48)),
                ("hits", models.PositiveIntegerField(default=0)),
                ("window_started_at", models.DateTimeField()),
                ("blocked_until", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddIndex(
            model_name="securitythrottle",
            index=models.Index(
                fields=["scope", "updated_at"],
                name="security_scope_updated_idx",
            ),
        ),
    ]