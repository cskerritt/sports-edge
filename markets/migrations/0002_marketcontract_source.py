from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("markets", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="marketcontract",
            name="source",
            field=models.CharField(
                choices=[("COINBASE", "Coinbase"), ("KALSHI", "Kalshi")],
                default="COINBASE",
                max_length=20,
            ),
        ),
    ]
