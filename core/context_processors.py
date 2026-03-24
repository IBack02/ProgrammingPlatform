from .ui_translations import UI_TRANSLATIONS, get_ui_lang


def ui_i18n(request):
    lang = get_ui_lang(request)
    return {
        "ui_lang": lang,
        "T": UI_TRANSLATIONS[lang],
    }