"""Translations for the fixed text in the original Kazakh Word form."""


DOCUMENT_LABELS = {
    "kk": {"title": "Қысқа мерзімді жоспар", "minutes": "мин", "module": "Модуль", "modules": "Модульдер"},
    "ru": {"title": "Краткосрочный план урока", "minutes": "мин", "module": "Модуль", "modules": "Модули"},
    "en": {"title": "Short-term lesson plan", "minutes": "min", "module": "Module", "modules": "Modules"},
}

# Each entry targets a paragraph by row, grid column and paragraph index.
# A pair of strings keeps the bold label and italic guidance in a single paragraph.
FORM_TRANSLATIONS = {
    (0, 0, 0): ("Тема урока", "Lesson topic"),
    (0, 2, 0): (
        "Назарбаев Интеллектуальная школа естественно-математического направления города Кызылорды",
        "Nazarbayev Intellectual School of Science and Mathematics in Kyzylorda",
    ),
    (1, 2, 0): ("ФИО учителя", "Teacher’s full name"),
    (2, 0, 0): ("Дата урока ", "Lesson date "),
    (2, 2, 0): ("Класс ", "Class "),
    (3, 0, 0): ("Цель обучения", "Learning objective"),
    (4, 0, 0): ("Цель урока", "Lesson objective"),
    (5, 0, 0): ("Оценивание", "Assessment"),
    (5, 0, 1): ("Критерии и дескрипторы", "Criteria and descriptors"),
    (6, 0, 0): ("Языковые задачи", "Language objectives"),
    (7, 0, 0): ("Ценности NIS в действии", "NIS values in action"),
    (8, 0, 0): ("Межпредметные связи", "Cross-curricular links"),
    (9, 0, 0): ("Использование ИКТ", "Use of ICT"),
    (10, 0, 0): ("Предварительные знания", "Prior knowledge"),
    (11, 0, 0): ("Этапы урока", "Lesson stages"),
    (11, 1, 0): ("Запланированные активности", "Planned activities"),
    (11, 6, 0): ("Ресурсы", "Resources"),
    (12, 0, 0): ("Начало урока", "Beginning of the lesson"),
    (12, 0, 1): ("(время)", "(time)"),
    (13, 0, 0): ("Середина урока", "Middle of the lesson"),
    (13, 0, 1): ("(время)", "(time)"),
    (14, 0, 0): ("Конец урока", "End of the lesson"),
    (14, 0, 1): ("(время)", "(time)"),
    (15, 0, 0): ("Дополнительная информация для анализа", "Additional information for review"),
    (16, 0, 0): (
        ("Дифференциация. ", "Как вы поддержите учеников? Какие задания предложите тем, кто продвигается быстрее?"),
        ("Differentiation. ", "How will you support learners? What will you offer those who are ready for a greater challenge?"),
    ),
    (16, 3, 0): ("Оценивание", "Assessment"),
    (16, 3, 1): ("Как вы проверите знания учеников?", "How will you check what learners have understood?"),
    (16, 4, 0): ("Межпредметные связи", "Cross-curricular links"),
    (16, 4, 1): ("Правила безопасности", "Safety rules"),
    (16, 4, 2): ("Использование ИКТ", "Use of ICT"),
    (16, 4, 3): ("Ценности NIS в действии", "NIS values in action"),
    (18, 0, 0): ("Рефлексия", "Reflection"),
    (18, 0, 1): (
        "Удалось ли достичь целей урока и обучения? Чему сегодня научились ученики? Какой была учебная среда? "
        "Сработала ли дифференциация? Удалось ли эффективно использовать время? Какие изменения я внёс в план и почему?",
        "How well did the lesson meet its objectives? What did learners learn today? What was the learning environment like? "
        "Was differentiation effective? Did I use time well? What did I change in the plan and why?",
    ),
}
