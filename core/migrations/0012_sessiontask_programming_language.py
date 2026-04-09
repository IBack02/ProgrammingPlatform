from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_theoryquizmodule_theoryquizquestion_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessiontask",
            name="programming_language",
            field=models.CharField(
                choices=[("python", "Python"), ("cpp", "C++")],
                default="python",
                max_length=16,
            ),
        ),
    ]

