"""Frozen initial catalogue; future content changes must not rewrite this migration."""
from django.db import migrations


INITIAL_HEROES = [{'slug': 'nis-coder',
  'name': 'Арман',
  'rarity': 'common',
  'roles': {'ru': 'Программист', 'kk': 'Бағдарламашы', 'en': 'Programmer'},
  'portrait': 'chars/nis-coder.png',
  'required_fragments': 10,
  'shard_cost': 2},
 {'slug': 'nis-mathematician',
  'name': 'Аружан',
  'rarity': 'common',
  'roles': {'ru': 'Математик', 'kk': 'Математик', 'en': 'Mathematician'},
  'portrait': 'chars/nis-mathematician.png',
  'required_fragments': 10,
  'shard_cost': 2},
 {'slug': 'nis-chemist',
  'name': 'Ильяс',
  'rarity': 'common',
  'roles': {'ru': 'Химик', 'kk': 'Химик', 'en': 'Chemist'},
  'portrait': 'chars/nis-chemist.png',
  'required_fragments': 10,
  'shard_cost': 2},
 {'slug': 'nis-roboticist',
  'name': 'Аяулым',
  'rarity': 'rare',
  'roles': {'ru': 'Робототехник', 'kk': 'Робототехник', 'en': 'Roboticist'},
  'portrait': 'chars/nis-roboticist.png',
  'required_fragments': 10,
  'shard_cost': 4},
 {'slug': 'nis-artist',
  'name': 'Тимур',
  'rarity': 'rare',
  'roles': {'ru': 'Художник', 'kk': 'Суретші', 'en': 'Artist'},
  'portrait': 'chars/nis-artist.png',
  'required_fragments': 10,
  'shard_cost': 4},
 {'slug': 'nis-biologist',
  'name': 'Сара',
  'rarity': 'rare',
  'roles': {'ru': 'Биолог', 'kk': 'Биолог', 'en': 'Biologist'},
  'portrait': 'chars/nis-biologist.png',
  'required_fragments': 10,
  'shard_cost': 4},
 {'slug': 'nis-physicist',
  'name': 'Руслан',
  'rarity': 'epic',
  'roles': {'ru': 'Физик', 'kk': 'Физик', 'en': 'Physicist'},
  'portrait': 'chars/nis-physicist.png',
  'required_fragments': 10,
  'shard_cost': 8},
 {'slug': 'nis-debater',
  'name': 'Алия',
  'rarity': 'epic',
  'roles': {'ru': 'Дебатёр', 'kk': 'Пікірсайысшы', 'en': 'Debater'},
  'portrait': 'chars/nis-debater.png',
  'required_fragments': 10,
  'shard_cost': 8},
 {'slug': 'nis-astronomer',
  'name': 'Мади',
  'rarity': 'epic',
  'roles': {'ru': 'Астроном', 'kk': 'Астроном', 'en': 'Astronomer'},
  'portrait': 'chars/nis-astronomer.png',
  'required_fragments': 10,
  'shard_cost': 8},
 {'slug': 'nis-musician',
  'name': 'Дана',
  'rarity': 'legendary',
  'roles': {'ru': 'Музыкант', 'kk': 'Музыкант', 'en': 'Musician'},
  'portrait': 'chars/nis-musician.png',
  'required_fragments': 10,
  'shard_cost': 16},
 {'slug': 'nis-chessplayer',
  'name': 'Аскар',
  'rarity': 'legendary',
  'roles': {'ru': 'Шахматист', 'kk': 'Шахматшы', 'en': 'Chess player'},
  'portrait': 'chars/nis-chessplayer.png',
  'required_fragments': 10,
  'shard_cost': 16},
 {'slug': 'nis-inventor',
  'name': 'Томирис',
  'rarity': 'legendary',
  'roles': {'ru': 'Изобретатель', 'kk': 'Өнертапқыш', 'en': 'Inventor'},
  'portrait': 'chars/nis-inventor.png',
  'required_fragments': 10,
  'shard_cost': 16}]

INITIAL_PACK = {'price': 50,
 'slots': 3,
 'fragments_per_slot': 2,
 'weights': {'common': 70, 'rare': 22, 'epic': 7, 'legendary': 1},
 'pity_after': 4,
 'overflow_rate': 1,
 'heroes': {'common': ['nis-coder', 'nis-mathematician', 'nis-chemist'],
            'rare': ['nis-roboticist', 'nis-artist', 'nis-biologist'],
            'epic': ['nis-physicist', 'nis-debater', 'nis-astronomer'],
            'legendary': ['nis-musician', 'nis-chessplayer', 'nis-inventor']}}

INITIAL_COSMETICS = [{'slug': 'cap',
  'slot': 'head',
  'price': 30,
  'labels': {'ru': 'Кепка', 'kk': 'Кепка', 'en': 'Cap'}},
 {'slug': 'crown',
  'slot': 'head',
  'price': 80,
  'labels': {'ru': 'Корона', 'kk': 'Тәж', 'en': 'Crown'}},
 {'slug': 'round-glasses',
  'slot': 'glasses',
  'price': 40,
  'labels': {'ru': 'Круглые очки', 'kk': 'Дөңгелек көзілдірік', 'en': 'Round glasses'}},
 {'slug': 'sunglasses',
  'slot': 'glasses',
  'price': 60,
  'labels': {'ru': 'Тёмные очки', 'kk': 'Қара көзілдірік', 'en': 'Sunglasses'}},
 {'slug': 'mustache',
  'slot': 'mustache',
  'price': 35,
  'labels': {'ru': 'Усы', 'kk': 'Мұрт', 'en': 'Mustache'}},
 {'slug': 'parchment',
  'slot': 'theme',
  'price': 100,
  'labels': {'ru': 'Пергамент', 'kk': 'Пергамент', 'en': 'Parchment'}},
 {'slug': 'forest',
  'slot': 'theme',
  'price': 100,
  'labels': {'ru': 'Лесной журнал', 'kk': 'Орман журналы', 'en': 'Forest journal'}}]


def seed_initial_catalog(apps, schema_editor):
    alias = schema_editor.connection.alias
    Hero = apps.get_model("tower", "HeroDefinition")
    Version = apps.get_model("tower", "PackVersion")
    Offer = apps.get_model("tower", "PackOffer")
    Cosmetic = apps.get_model("tower", "CosmeticDefinition")
    for definition in INITIAL_HEROES:
        defaults = {key: value for key, value in definition.items() if key != "slug"}
        Hero.objects.using(alias).get_or_create(slug=definition["slug"], defaults=defaults)
    version, _ = Version.objects.using(alias).get_or_create(
        slug="school-v1", defaults={"config": INITIAL_PACK},
    )
    if version.config != INITIAL_PACK:
        raise RuntimeError("The existing school-v1 pack differs from its published initial configuration.")
    # Preserve existing offer versions and deliberate catalogue shutdowns.
    Offer.objects.using(alias).get_or_create(slug="school", defaults={"version_id": version.pk})
    for definition in INITIAL_COSMETICS:
        defaults = {key: value for key, value in definition.items() if key != "slug"}
        Cosmetic.objects.using(alias).get_or_create(slug=definition["slug"], defaults=defaults)


class Migration(migrations.Migration):
    dependencies = [("tower", "0003_cosmeticdefinition_packversion_and_more")]
    operations = [migrations.RunPython(seed_initial_catalog, migrations.RunPython.noop)]
