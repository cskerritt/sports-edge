"""Change EloRating.game from OneToOneField to ForeignKey.

Each game produces two Elo records (one per team), so OneToOne is incorrect.
Also adds unique_together on (team, game) for idempotent bulk_create.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0001_initial"),
        ("sports", "0001_initial"),
    ]

    operations = [
        # 1. Drop the old unique constraint (OneToOneField auto-creates one)
        migrations.AlterField(
            model_name="elorating",
            name="game",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="elo_snapshots",
                to="sports.game",
            ),
        ),
        # 2. Add unique_together on (team, game)
        migrations.AlterUniqueTogether(
            name="elorating",
            unique_together={("team", "game")},
        ),
    ]
