from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_lessonhighlight_revealed_blocks_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="theorymaterialblock",
            name="heading_level",
            field=models.CharField(
                blank=True,
                choices=[("h1", "H1"), ("h2", "H2"), ("h3", "H3")],
                default="",
                max_length=8,
            ),
        ),
    ]
