"""Жанры FB2 (коды коллекций Флибусты/Либрусека): русские названия и группы."""

GROUPS: list[tuple[str, dict[str, str]]] = [
    ("Сетевая и любительская литература", {
        "network_literature": "Сетевая литература", "fan_translation": "Любительский перевод",
        "fanfiction": "Фанфик", "unfinished": "Недописанное", "computer_translation": "Машинный перевод",
    }),
    ("Фантастика и фэнтези", {
        "sf": "Научная фантастика", "sf_fantasy": "Фэнтези", "sf_action": "Боевая фантастика",
        "sf_heroic": "Героическая фантастика", "sf_space": "Космическая фантастика",
        "sf_social": "Социальная фантастика", "sf_horror": "Ужасы", "sf_humor": "Юмористическая фантастика",
        "sf_history": "Альтернативная история", "sf_detective": "Детективная фантастика",
        "sf_cyberpunk": "Киберпанк", "sf_postapocalyptic": "Постапокалипсис", "sf_mystic": "Мистика",
        "sf_epic": "Эпическая фантастика", "sf_etc": "Фантастика: прочее", "sf_fantasy_city": "Городское фэнтези",
        "sf_litrpg": "ЛитРПГ", "sf_realrpg": "РеалРПГ", "sf_technofantasy": "Технофэнтези",
        "sf_stimpank": "Стимпанк", "sf_su": "Советская фантастика", "popadancy": "Попаданцы",
        "russian_fantasy": "Русское фэнтези", "slavic_fantasy": "Славянское фэнтези",
        "magic_school": "Магическая академия", "everyday_fantasy": "Бытовое фэнтези",
        "asian_fantasy": "Азиатское фэнтези", "adventure_fantasy": "Приключенческое фэнтези",
        "fairy_fantasy": "Сказочная фантастика", "dark_fantasy": "Тёмное фэнтези",
        "historical_fantasy": "Историческое фэнтези", "fantasy_det": "Фэнтези-детектив",
        "boyar_anime": "Бояръ-аниме", "hronoopera": "Хроноопера", "dystopian": "Антиутопия",
        "utopia": "Утопия", "foreign_sf": "Зарубежная фантастика", "nsf": "Ненаучная фантастика",
        "fantasy_alt_hist": "Альтернативная история (фэнтези)", "sf_industrial_magic": "Индустриальная магия",
        "sf_writing": "Фантастика",
    }),
    ("Любовные романы", {
        "love": "Любовные романы", "love_sf": "Любовное фэнтези, фантастика",
        "love_contemporary": "Современные любовные романы", "love_short": "Короткие любовные романы",
        "love_erotica": "Эротика", "love_hard": "Порно", "love_history": "Исторические любовные романы",
        "love_detective": "Остросюжетные любовные романы", "home_sex": "Эротика, секс",
    }),
    ("Проза, юмор, драматургия", {
        "prose": "Проза", "prose_contemporary": "Современная проза", "prose_classic": "Классическая проза",
        "prose_history": "Историческая проза", "prose_rus_classic": "Русская классическая проза",
        "prose_su_classics": "Советская классическая проза", "prose_military": "О войне",
        "prose_counter": "Контркультура", "prose_magic": "Магический реализм", "prose_abs": "Фантасмагория",
        "prose_game": "Игровая проза", "prose_neformatny": "Неформатная проза", "foreign_prose": "Зарубежная проза",
        "literature_18": "Литература XVIII века", "literature_19": "Литература XIX века",
        "literature_20": "Литература XX века", "story": "Рассказ", "great_story": "Повесть",
        "epistolary_fiction": "Эпистолярная проза", "gothic_novel": "Готический роман", "notes": "Заметки",
        "aphorisms": "Афоризмы", "humor": "Юмор", "humor_prose": "Юмористическая проза",
        "humor_verse": "Юмористические стихи", "humor_anecdote": "Анекдоты", "humor_satire": "Сатира",
        "comedy": "Комедия", "dramaturgy": "Драматургия", "drama": "Драма", "tragedy": "Трагедия",
        "screenplays": "Сценарии", "vaudeville": "Водевиль", "drama_antique": "Античная драма",
        "dorama": "Дорама", "comics": "Комиксы", "other": "Прочее",
    }),
    ("Детективы и триллеры", {
        "detective": "Детективы", "det_classic": "Классический детектив", "det_action": "Боевик",
        "det_police": "Полицейский детектив", "det_history": "Исторический детектив",
        "det_crime": "Криминальный детектив", "det_espionage": "Шпионский детектив",
        "det_hard": "Крутой детектив", "det_irony": "Иронический детектив", "det_lady": "Дамский детектив",
        "det_political": "Политический детектив", "det_maniac": "Про маньяков", "det_su": "Советский детектив",
        "det_artifact": "Детектив об артефактах", "det_other": "Детектив", "thriller": "Триллер",
        "thriller_mystery": "Мистический триллер",
    }),
    ("Приключения", {
        "adventure": "Приключения", "adv_history": "Исторические приключения",
        "adv_geo": "Путешествия и география", "adv_maritime": "Морские приключения",
        "adv_animal": "Природа и животные", "adv_indian": "Вестерн, про индейцев",
        "adv_story": "Авантюрный роман", "adv_modern": "Приключения в современном мире",
        "tale_chivalry": "Рыцарский роман", "travel_notes": "Путевые заметки",
    }),
    ("Поэзия", {
        "poetry": "Поэзия", "poetry_classical": "Классическая поэзия", "poetry_modern": "Современная поэзия",
        "poetry_rus_classical": "Классическая русская поэзия", "poetry_rus_modern": "Современная русская поэзия",
        "poetry_for_classical": "Классическая зарубежная поэзия", "poetry_for_modern": "Современная зарубежная поэзия",
        "poetry_east": "Поэзия Востока", "lyrics": "Лирика", "poem": "Поэма", "song_poetry": "Песенная поэзия",
        "epic": "Былины, эпопея", "limerick": "Лимерики", "palindromes": "Палиндромы",
    }),
    ("Детская литература", {
        "children": "Детская литература", "child_prose": "Детская проза", "child_adv": "Детские приключения",
        "child_tale": "Сказки", "child_sf": "Детская фантастика", "child_det": "Детский детектив",
        "child_verse": "Детские стихи", "child_education": "Образовательная литература",
        "child_classical": "Классическая детская литература", "foreign_children": "Зарубежная детская литература",
        "child_tale_foreign_writers": "Сказки зарубежных писателей",
        "child_tale_russian_writers": "Сказки русских писателей", "child_tale_rus": "Русские сказки",
        "child_adv_animal": "Детские книги о животных", "child_sf_fantasy": "Детское фэнтези",
        "child_det_children_detectives": "Детский детектив: дети-сыщики", "child_sf_horror": "Детские ужастики",
        "child_sf_space": "Детская космическая фантастика", "child_sf_hronoopera": "Детская хроноопера",
        "child_prose_humor": "Детская юмористическая проза", "child_prose_history": "Детская историческая проза",
        "child_prose_romantic": "Подростковая романтика", "child_det_other": "Детский детектив: прочее",
        "child_det_animal_detectives": "Детский детектив о животных", "child_dramaturgy": "Детская драматургия",
        "child_folklore": "Детский фольклор", "modern_tale": "Современная сказка",
    }),
    ("Фольклор и старинная литература", {
        "folk_tale": "Народные сказки", "folklore": "Фольклор", "folk_songs": "Народные песни",
        "proverbs": "Пословицы", "antique": "Старинная литература", "antique_myths": "Мифы, легенды",
        "antique_east": "Древневосточная литература", "antique_european": "Европейская старинная литература",
        "antique_ant": "Античная литература", "antique_russian": "Древнерусская литература",
        "foreign_antique": "Средневековая литература",
    }),
    ("Документальное и наука", {
        "nonfiction": "Документальная литература", "nonf_biography": "Биографии и мемуары",
        "Биографии и мемуары": "Биографии и мемуары (без кода)", "nonf_publicism": "Публицистика",
        "nonf_criticism": "Критика", "nonf_military": "Военная документалистика",
        "nonf_biography_writers": "Биографии писателей", "nonf_biography_historical": "Исторические биографии",
        "nonf_biography_military_figures": "Биографии военных", "nonf_biography_celebrities": "Биографии знаменитостей",
        "about_musicians": "О музыкантах", "sci_history": "История", "sci_psychology": "Психология",
        "sci_psychology_popular": "Популярная психология", "sci_philosophy": "Философия",
        "sci_politics": "Политика", "sci_culture": "Культурология", "sci_religion": "Религиоведение",
        "sci_linguistic": "Языкознание", "sci_philology": "Филология", "sci_popular": "Научпоп",
        "sci_medicine": "Медицина", "sci_medicine_alternative": "Альтернативная медицина",
        "sci_pedagogy": "Педагогика", "sci_juris": "Юриспруденция", "sci_biology": "Биология",
        "sci_tech": "Техника", "sci_theories": "Теории", "sci_social_studies": "Обществознание",
        "sci_transport": "Транспорт", "sci_phys": "Физика", "sci_cosmos": "Космос", "sci_math": "Математика",
        "sci_geo": "География", "sci_economy": "Экономика (наука)", "sci_zoo": "Зоология", "sci_chem": "Химия",
        "sci_state": "Государство и право", "sci_ecology": "Экология", "sci_botany": "Ботаника",
        "sci_oriental": "Востоковедение", "sci_radio": "Радиоэлектроника", "sci_veterinary": "Ветеринария",
        "sci_build": "Строительство", "sci_metal": "Металлургия", "sci_textbook": "Учебники",
        "science": "Научная литература", "military_special": "Военное дело",
        "military_weapon": "Оружие", "military_history": "Военная история",
    }),
    ("Религия и эзотерика", {
        "religion": "Религия", "religion_esoterics": "Эзотерика", "religion_self": "Самосовершенствование",
        "religion_christianity": "Христианство", "religion_orthodoxy": "Православие",
        "religion_budda": "Буддизм", "religion_judaism": "Иудаизм", "religion_paganism": "Язычество",
        "religion_islam": "Ислам", "religion_catholicism": "Католицизм", "religion_protestantism": "Протестантизм",
        "religion_hinduism": "Индуизм", "religion_rel": "Религия: прочее", "astrology": "Астрология",
    }),
    ("Дом, справочники, бизнес, искусство", {
        "home": "Дом и семья", "home_health": "Здоровье", "home_cooking": "Кулинария",
        "home_sport": "Спорт", "home_crafts": "Хобби, ремёсла", "home_pets": "Домашние животные",
        "home_garden": "Сад и огород", "home_diy": "Сделай сам", "home_entertain": "Развлечения",
        "home_collecting": "Коллекционирование", "family": "Семья", "ref_encyc": "Энциклопедии",
        "ref_ref": "Справочники", "ref_guide": "Руководства", "ref_dict": "Словари", "reference": "Справочная литература",
        "geo_guides": "Путеводители", "popular_business": "Бизнес", "org_behavior": "Корпоративная культура",
        "economics_ref": "Экономика (справочники)", "economics": "Экономика", "banking": "Финансы",
        "auto_regulations": "Автомобили и ПДД", "auto_business": "Автомобильный бизнес",
        "computers": "Компьютеры", "comp_db": "Базы данных", "comp_www": "Интернет", "comp_hard": "Компьютерное железо",
        "comp_osnet": "ОС и сети", "tbg_higher": "Учебники для вузов",
        "tbg_school": "Школьные учебники", "tbg_secondary": "Учебники для техникумов", "tbg_computers": "Учебники по информатике",
        "design": "Искусство и дизайн", "cine": "Кино", "music": "Музыка", "theatre": "Театр",
        "painting": "Живопись", "art_criticism": "Искусствоведение", "art_world_culture": "Мировая художественная культура",
        "architecture_book": "Архитектура", "periodic": "Журналы, газеты", "equ_history": "История техники",
    }),
]

_ORDER = ["Любовные романы", "Фантастика и фэнтези", "Проза, юмор, драматургия", "Детективы и триллеры",
          "Приключения", "Сетевая и любительская литература", "Поэзия", "Детская литература",
          "Фольклор и старинная литература", "Документальное и наука", "Религия и эзотерика",
          "Дом, справочники, бизнес, искусство"]
GROUPS.sort(key=lambda g: _ORDER.index(g[0]) if g[0] in _ORDER else 99)

GENRES: dict[str, str] = {code: name for _t, g in GROUPS for code, name in g.items()}
GROUP_OF: dict[str, str] = {code: t for t, g in GROUPS for code in g}
OTHER_GROUP = "Прочие жанры"


def genre_name(code: str) -> str:
    return GENRES.get(code, code)


def genre_names(codes: str | None) -> list[str]:
    return [genre_name(c) for c in (codes or "").split(":") if c]
