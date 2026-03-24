UI_TRANSLATIONS = {
    "ru": {
        "teacher_portal": "Портал учителя",
        "dashboard": "Панель",
        "sessions": "Сессии",
        "classes": "Классы",
        "students": "Ученики",
        "tasks": "Задачи",
        "analytics": "Аналитика",
        "interface_language": "Язык интерфейса",
        "russian": "Русский",
        "kazakh": "Қазақша",
    },
    "kk": {
        "teacher_portal": "Мұғалім порталы",
        "dashboard": "Басқару панелі",
        "sessions": "Сессиялар",
        "classes": "Сыныптар",
        "students": "Оқушылар",
        "tasks": "Тапсырмалар",
        "analytics": "Аналитика",
        "interface_language": "Интерфейс тілі",
        "russian": "Русский",
        "kazakh": "Қазақша",
    },
}

DEFAULT_UI_LANG = "ru"
SUPPORTED_UI_LANGS = {"ru", "kk"}


def get_ui_lang(request):
    lang = request.session.get("ui_lang") or request.COOKIES.get("ui_lang") or DEFAULT_UI_LANG
    if lang not in SUPPORTED_UI_LANGS:
        lang = DEFAULT_UI_LANG
    return lang