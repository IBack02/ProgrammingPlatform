"""Frozen pilot publication and once-only restoration, independent of live manifests."""
from django.db import migrations


INITIAL_SETS = [{'slug': 'nis-coder-v1',
  'hero_id': 'nis-coder',
  'revision': 1,
  'config': {'hero_id': 'nis-coder',
             'revision': 1,
             'status': 'published',
             'components': {'head': {'id': 'nis-coder-head',
                                     'slot': 'head',
                                     'labels': {'ru': 'Шапка · Арман',
                                                'kk': 'Бас киім · Арман',
                                                'en': 'Hat · Арман'},
                                     'asset': {'path': 'sets/nis-coder/v1/head.png',
                                               'width': 1254,
                                               'height': 1254},
                                     'attachment': {'anchor': 'head',
                                                    'pivot': {'x': 0.5, 'y': 0.707},
                                                    'width_ratio': 1.26,
                                                    'rotation': 0.0,
                                                    'layer': 'front'},
                                     'behavior_profile': None,
                                     'animation': 'none',
                                     'placeholder': False},
                            'prop': {'id': 'nis-coder-prop',
                                     'slot': 'prop',
                                     'labels': {'ru': 'Игровая приставка · Арман',
                                                'kk': 'Ойын құрылғысы · Арман',
                                                'en': 'Game console · Арман'},
                                     'asset': {'path': 'sets/nis-coder/v1/prop.png',
                                               'width': 1254,
                                               'height': 1254},
                                     'attachment': {'anchor': 'hand',
                                                    'pivot': {'x': 0.5, 'y': 0.5},
                                                    'width_ratio': 0.84,
                                                    'rotation': 0.0,
                                                    'layer': 'front'},
                                     'behavior_profile': None,
                                     'animation': 'none',
                                     'placeholder': False},
                            'background': {'id': 'nis-coder-background',
                                           'slot': 'background',
                                           'labels': {'ru': 'Фон · Арман',
                                                      'kk': 'Фон · Арман',
                                                      'en': 'Background · Арман'},
                                           'asset': {'path': 'sets/nis-coder/v1/background.png',
                                                     'width': 1672,
                                                     'height': 941},
                                           'attachment': None,
                                           'behavior_profile': None,
                                           'animation': 'none',
                                           'placeholder': False},
                            'ui_loader': {'id': 'nis-coder-ui-loader',
                                          'slot': 'ui_loader',
                                          'labels': {'ru': 'Загрузчик · Арман',
                                                     'kk': 'Жүктеу белгісі · Арман',
                                                     'en': 'Loading indicator · Арман'},
                                          'asset': {'path': 'sets/nis-coder/v1/ui-loader.svg',
                                                    'width': 128,
                                                    'height': 128},
                                          'attachment': None,
                                          'behavior_profile': None,
                                          'animation': 'rotate',
                                          'placeholder': False},
                            'ui_decoration': {'id': 'nis-coder-ui-decoration',
                                              'slot': 'ui_decoration',
                                              'labels': {'ru': 'Декор интерфейса · Арман',
                                                         'kk': 'Интерфейс безендіруі · Арман',
                                                         'en': 'Interface decoration · Арман'},
                                              'asset': {'path': 'sets/nis-coder/v1/ui-decoration.svg',
                                                        'width': 256,
                                                        'height': 128},
                                              'attachment': None,
                                              'behavior_profile': None,
                                              'animation': 'none',
                                              'placeholder': False},
                            'companion': {'id': 'nis-coder-companion',
                                          'slot': 'companion',
                                          'labels': {'ru': 'Помощник · Арман',
                                                     'kk': 'Көмекші · Арман',
                                                     'en': 'Companion · Арман'},
                                          'asset': {'path': 'sets/nis-coder/v1/companion-placeholder.svg',
                                                    'width': 512,
                                                    'height': 512},
                                          'attachment': None,
                                          'behavior_profile': 'nis-coder',
                                          'animation': 'none',
                                          'placeholder': True}}},
  'behavior': {'hero_id': 'nis-coder',
               'revision': 1,
               'temperament': 'Арман — спокойный практичный напарник, любит превращать большую задачу в один '
                              'понятный следующий шаг.',
               'tone': 'Дружелюбный, прямой, без наставнического нажима. Короткие ясные фразы, не больше '
                       'одной технической метафоры.',
               'vocabulary': ['проверим', 'следующий шаг', 'разобраться', 'проверка', 'отладка'],
               'humor': 'Изредка мягко шутит про потерянную скобку; никогда не шутит над учеником или его '
                        'ошибкой.',
               'support_style': 'Называет конкретное действие и предлагает начать с небольшого шага; не '
                                'сообщает готовый алгоритм решения.',
               'examples': {'ru': ['Задача про помидоры осталась без решения. Давай вернёмся к ней и '
                                   'проверим, что уже получилось.',
                                   'Учитель отметил границы цикла. Стоит ещё раз посмотреть, какие значения '
                                   'попадают в повторение.',
                                   'В последнем уроке был полезный пример с условием. Он помогает понять, '
                                   'почему программа выбирает разные действия.'],
                            'kk': ['Қызанақ туралы есеп әлі шешілмеген. Оған оралып, қай жерге дейін '
                                   'келгеніңді қарайық.',
                                   'Мұғалім цикл шектеріне назар аударған. Қай мәндер қайталанатынын қайта '
                                   'қарап шық.',
                                   'Соңғы сабақта шартқа қатысты пайдалы мысал болды. Ол бағдарламаның '
                                   'әртүрлі әрекетті қалай таңдайтынын түсіндіреді.'],
                            'en': ["The tomato task is still unfinished. Let's return to it and check what "
                                   'you already have.',
                                   'Your teacher pointed out the loop boundaries. Take another look at which '
                                   'values the repetition includes.',
                                   'The last lesson had a useful example about a condition. It shows how a '
                                   'program chooses between actions.']}},
  'asset_manifest': [{'path': 'sets/nis-coder/v1/head.png',
                      'bytes': 1327077,
                      'sha256': '6491739c098b3f699fefc490d7d671f4651cd3d7d7b5afb7ec4a7edd9197059a',
                      'content_type': 'image/png',
                      'slot': 'head',
                      'placeholder': False},
                     {'path': 'sets/nis-coder/v1/prop.png',
                      'bytes': 1097184,
                      'sha256': '4632835535364355004ba0bee33950a1ac0076a640744a2cdc59b33c6fcf2d82',
                      'content_type': 'image/png',
                      'slot': 'prop',
                      'placeholder': False},
                     {'path': 'sets/nis-coder/v1/background.png',
                      'bytes': 1642880,
                      'sha256': 'a89778e2b0f67a2ad4350c8d2390478302e4f90fd70c0b0b8ad46e9c5b94041e',
                      'content_type': 'image/png',
                      'slot': 'background',
                      'placeholder': False},
                     {'path': 'sets/nis-coder/v1/ui-loader.svg',
                      'bytes': 570,
                      'sha256': '08047d955f0c081ee17f16fe64e0500a5707e1f77e43cdc168109b829c2d8274',
                      'content_type': 'image/svg+xml',
                      'slot': 'ui_loader',
                      'placeholder': False},
                     {'path': 'sets/nis-coder/v1/ui-decoration.svg',
                      'bytes': 680,
                      'sha256': 'e4a5630d78e249621c53f30a3235f44475738af77ef91d27f2ac8af10ee58b79',
                      'content_type': 'image/svg+xml',
                      'slot': 'ui_decoration',
                      'placeholder': False},
                     {'path': 'sets/nis-coder/v1/companion-placeholder.svg',
                      'bytes': 520,
                      'sha256': 'a28133b25dae605c5eed4db26163b7ae10f94f62a840b822e235ef1a7a731da7',
                      'content_type': 'image/svg+xml',
                      'slot': 'companion',
                      'placeholder': True}]},
 {'slug': 'nis-artist-v1',
  'hero_id': 'nis-artist',
  'revision': 1,
  'config': {'hero_id': 'nis-artist',
             'revision': 1,
             'status': 'published',
             'components': {'head': {'id': 'nis-artist-head',
                                     'slot': 'head',
                                     'labels': {'ru': 'Шапка · Тимур',
                                                'kk': 'Бас киім · Тимур',
                                                'en': 'Hat · Тимур'},
                                     'asset': {'path': 'sets/nis-artist/v1/head.png',
                                               'width': 1254,
                                               'height': 1254},
                                     'attachment': {'anchor': 'head',
                                                    'pivot': {'x': 0.5, 'y': 0.719},
                                                    'width_ratio': 1.3,
                                                    'rotation': 0.0,
                                                    'layer': 'front'},
                                     'behavior_profile': None,
                                     'animation': 'none',
                                     'placeholder': False},
                            'prop': {'id': 'nis-artist-prop',
                                     'slot': 'prop',
                                     'labels': {'ru': 'Палитра · Тимур',
                                                'kk': 'Бояу тақтасы · Тимур',
                                                'en': 'Palette · Тимур'},
                                     'asset': {'path': 'sets/nis-artist/v1/prop.png',
                                               'width': 1254,
                                               'height': 1254},
                                     'attachment': {'anchor': 'hand',
                                                    'pivot': {'x': 0.5, 'y': 0.5},
                                                    'width_ratio': 0.84,
                                                    'rotation': 0.0,
                                                    'layer': 'front'},
                                     'behavior_profile': None,
                                     'animation': 'none',
                                     'placeholder': False},
                            'background': {'id': 'nis-artist-background',
                                           'slot': 'background',
                                           'labels': {'ru': 'Фон · Тимур',
                                                      'kk': 'Фон · Тимур',
                                                      'en': 'Background · Тимур'},
                                           'asset': {'path': 'sets/nis-artist/v1/background.png',
                                                     'width': 1672,
                                                     'height': 941},
                                           'attachment': None,
                                           'behavior_profile': None,
                                           'animation': 'none',
                                           'placeholder': False},
                            'ui_loader': {'id': 'nis-artist-ui-loader',
                                          'slot': 'ui_loader',
                                          'labels': {'ru': 'Загрузчик · Тимур',
                                                     'kk': 'Жүктеу белгісі · Тимур',
                                                     'en': 'Loading indicator · Тимур'},
                                          'asset': {'path': 'sets/nis-artist/v1/ui-loader.svg',
                                                    'width': 128,
                                                    'height': 128},
                                          'attachment': None,
                                          'behavior_profile': None,
                                          'animation': 'rotate',
                                          'placeholder': False},
                            'ui_decoration': {'id': 'nis-artist-ui-decoration',
                                              'slot': 'ui_decoration',
                                              'labels': {'ru': 'Декор интерфейса · Тимур',
                                                         'kk': 'Интерфейс безендіруі · Тимур',
                                                         'en': 'Interface decoration · Тимур'},
                                              'asset': {'path': 'sets/nis-artist/v1/ui-decoration.svg',
                                                        'width': 256,
                                                        'height': 128},
                                              'attachment': None,
                                              'behavior_profile': None,
                                              'animation': 'none',
                                              'placeholder': False},
                            'companion': {'id': 'nis-artist-companion',
                                          'slot': 'companion',
                                          'labels': {'ru': 'Помощник · Тимур',
                                                     'kk': 'Көмекші · Тимур',
                                                     'en': 'Companion · Тимур'},
                                          'asset': {'path': 'sets/nis-artist/v1/companion-placeholder.svg',
                                                    'width': 512,
                                                    'height': 512},
                                          'attachment': None,
                                          'behavior_profile': 'nis-artist',
                                          'animation': 'none',
                                          'placeholder': True}}},
  'behavior': {'hero_id': 'nis-artist',
               'revision': 1,
               'temperament': 'Тимур — наблюдательный и мягкий собеседник; замечает детали и помогает '
                              'увидеть целую картину через конкретный фрагмент.',
               'tone': 'Образный, но понятный. Одна короткая зрительная метафора; учебный смысл всегда '
                       'важнее красивой формулировки.',
               'vocabulary': ['деталь', 'набросок', 'посмотрим', 'картина', 'попробовать'],
               'humor': 'Ненавязчивая игра с образами палитры и наброска; не романтизирует ошибку и не '
                        'уводит разговор в искусство.',
               'support_style': 'Выделяет одну деталь, которую стоит пересмотреть, и приглашает вернуться к '
                                'работе без давления.',
               'examples': {'ru': ['В задаче про узор остался незавершённый фрагмент. Давай вернёмся к этому '
                                   'наброску.',
                                   'Учитель обратил внимание на одну деталь условия. Посмотрим на неё чуть '
                                   'внимательнее.',
                                   'В последнем уроке был пример с повторением. Он помогает увидеть общую '
                                   'картину за отдельными шагами.'],
                            'kk': ['Өрнек туралы есепте аяқталмаған бөлік бар. Сол нобайға қайта оралайық.',
                                   'Мұғалім шарттың бір бөлігіне назар аударған. Оны сәл мұқият қарайық.',
                                   'Соңғы сабақта қайталауға қатысты мысал болды. Ол жеке қадамдардан тұтас '
                                   'көріністі байқауға көмектеседі.'],
                            'en': ["One part of the pattern task is still unfinished. Let's return to that "
                                   'sketch.',
                                   "Your teacher highlighted a detail in the condition. Let's take a closer "
                                   'look at it.',
                                   'The last lesson had an example of repetition. It helps connect '
                                   'individual steps into a bigger picture.']}},
  'asset_manifest': [{'path': 'sets/nis-artist/v1/head.png',
                      'bytes': 1131561,
                      'sha256': '30e5e83c324aef4dc79660f0d575a6c41e542de618d7f69a9323fe7710d0d788',
                      'content_type': 'image/png',
                      'slot': 'head',
                      'placeholder': False},
                     {'path': 'sets/nis-artist/v1/prop.png',
                      'bytes': 1489010,
                      'sha256': 'f4b0eb376b1b9cfd7da5773f25aeb1e8ae143767a54a849d3e5617b6b1393cbf',
                      'content_type': 'image/png',
                      'slot': 'prop',
                      'placeholder': False},
                     {'path': 'sets/nis-artist/v1/background.png',
                      'bytes': 2794101,
                      'sha256': '982eecfa7fbf454b421df6e5c5239649539708570ed721b25a717afd1161e099',
                      'content_type': 'image/png',
                      'slot': 'background',
                      'placeholder': False},
                     {'path': 'sets/nis-artist/v1/ui-loader.svg',
                      'bytes': 638,
                      'sha256': '2417d414651cbbd0553792cc7fd91eba5f74c546ee2f2cd58c21324e52475bab',
                      'content_type': 'image/svg+xml',
                      'slot': 'ui_loader',
                      'placeholder': False},
                     {'path': 'sets/nis-artist/v1/ui-decoration.svg',
                      'bytes': 709,
                      'sha256': '046ffb02604f7535cd21bbbaac0829d6b3f1b6b904f249d28ff86872481b3409',
                      'content_type': 'image/svg+xml',
                      'slot': 'ui_decoration',
                      'placeholder': False},
                     {'path': 'sets/nis-artist/v1/companion-placeholder.svg',
                      'bytes': 520,
                      'sha256': 'a28133b25dae605c5eed4db26163b7ae10f94f62a840b822e235ef1a7a731da7',
                      'content_type': 'image/svg+xml',
                      'slot': 'companion',
                      'placeholder': True}]}]


def seed_and_restore(apps, schema_editor):
    alias = schema_editor.connection.alias
    def manager(name):
        return apps.get_model("tower", name).objects.using(alias)
    versions = manager("CharacterSetVersion")
    publications = manager("CharacterSetPublication")
    definitions = manager("CosmeticDefinition")
    entitlements = manager("CharacterSetEntitlement")
    grants = manager("PlayerCharacterSet")
    items = manager("PlayerCosmetic")
    audit = manager("TowerAuditEvent")
    for snapshot in INITIAL_SETS:
        defaults = {key: value for key, value in snapshot.items() if key != "slug"}
        version, created = versions.get_or_create(slug=snapshot["slug"], defaults=defaults)
        if any(getattr(version, key) != value for key, value in defaults.items()):
            raise RuntimeError("Published character-set versions are immutable.")
        publication, new_publication = publications.get_or_create(hero_id=snapshot["hero_id"], defaults={"version_id": version.pk})
        current = publication.version
        for component in current.config["components"].values():
            item, _ = definitions.get_or_create(slug=component["id"], defaults={
                "slot": component["slot"], "labels": component["labels"], "price": 0,
                "acquisition": "character_set", "set_version_id": current.pk, "appearance": component,
            })
            if item.acquisition != "character_set" or item.slot != component["slot"]:
                raise RuntimeError("A character set cannot replace an existing cosmetic.")
        if created or new_publication:
            audit.create(event_type="character_set_publish", metadata={"hero": snapshot["hero_id"],
                "after": version.slug, "source": "migration_0006", "placeholder": True})
    available = {row.hero_id: row.version for row in publications.filter(is_active=True).select_related("version")}
    for card in manager("PlayerHero").filter(assembled_at__isnull=False).select_related("player", "hero").iterator(chunk_size=250):
        if card.fragments < card.hero.required_fragments:
            continue
        entitlement, _ = entitlements.get_or_create(player_id=card.player_id, hero_id=card.hero_id,
            defaults={"earned_at": card.assembled_at})
        if grants.filter(entitlement_id=entitlement.pk).exists():
            continue
        version = available.get(card.hero_id)
        if version is None:
            continue
        grant = grants.create(entitlement_id=entitlement.pk, version_id=version.pk)
        ids = []
        for component in version.config["components"].values():
            items.create(player_id=card.player_id, cosmetic_id=component["id"], source="character_set", character_set_id=grant.pk)
            ids.append(component["id"])
        audit.create(event_type="character_set_grant", student_id=card.player.student_id,
            metadata={"hero": card.hero_id, "version": version.slug, "items": sorted(ids), "source": "migration_0006"})


class Migration(migrations.Migration):
    dependencies = [("tower", "0005_character_set_ownership")]
    operations = [migrations.RunPython(seed_and_restore, migrations.RunPython.noop)]
