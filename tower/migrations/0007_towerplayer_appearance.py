from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tower", "0006_publish_initial_character_sets")]
    operations = [migrations.AddField(model_name="towerplayer", name="appearance",
                                     field=models.JSONField(blank=True, default=dict))]
