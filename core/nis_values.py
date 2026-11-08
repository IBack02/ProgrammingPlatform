"""Canonical NIS values available for lesson activities."""
from enum import Enum


class NisValue(str, Enum):
    RESPECT = "respect"
    HONESTY = "honesty"
    OPENNESS = "openness"
    FAMILY_VALUES_AND_TRADITIONS = "family_values_and_traditions"
    PATRIOTISM_AND_RESPONSIBILITY = "patriotism_and_responsibility"
    HEALTH_AND_WELLBEING = "health_and_wellbeing"
    HARD_WORK_AND_CREATIVITY = "hard_work_and_creativity"
    GLOBAL_CITIZENSHIP = "global_citizenship"


NIS_VALUES = {
    NisValue.RESPECT: {"kk": "Құрмет", "ru": "Уважение", "en": "Respect"},
    NisValue.HONESTY: {"kk": "Адалдық", "ru": "Честность", "en": "Honesty"},
    NisValue.OPENNESS: {"kk": "Ашықтық", "ru": "Открытость", "en": "Openness"},
    NisValue.FAMILY_VALUES_AND_TRADITIONS: {
        "kk": "Отбасылық құндылықтар мен дәстүрлер",
        "ru": "Семейные ценности и традиции",
        "en": "Family values and traditions",
    },
    NisValue.PATRIOTISM_AND_RESPONSIBILITY: {
        "kk": "Отаншылдық пен жауапкершілік",
        "ru": "Патриотизм и ответственность",
        "en": "Patriotism and responsibility",
    },
    NisValue.HEALTH_AND_WELLBEING: {
        "kk": "Денсаулық пен әл-ауқат",
        "ru": "Здоровье и благополучие",
        "en": "Health and well-being",
    },
    NisValue.HARD_WORK_AND_CREATIVITY: {
        "kk": "Еңбекқорлық пен шығармашылық",
        "ru": "Трудолюбие и творчество",
        "en": "Hard work and creativity",
    },
    NisValue.GLOBAL_CITIZENSHIP: {
        "kk": "Жаһандық азаматтық",
        "ru": "Глобальное гражданство",
        "en": "Global citizenship",
    },
}
