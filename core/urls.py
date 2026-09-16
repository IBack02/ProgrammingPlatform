from django.urls import path
from django.views.generic import RedirectView
from . import exam_views, game_views, module_import, peer_views, views

urlpatterns = [
    # Student auth API
    path("api/auth/student-login", views.student_login, name="student_login"),
    path("api/auth/student-logout", views.student_logout, name="student_logout"),
    path("api/auth/student-me", views.student_me, name="student_me"),

    # Student session/tasks API
    path("api/student/active-session", views.student_active_session, name="student_active_session"),
    path("api/student/dashboard", views.student_dashboard_data, name="student_dashboard_data"),
    path("api/student/task/<int:task_id>", views.student_task_detail, name="student_task_detail"),
    path("api/student/task/<int:task_id>/submit", views.student_submit, name="student_submit"),
    path("api/student/task/<int:task_id>/hint/<int:level>", views.student_hint_level, name="student_hint_level"),

    # Student pages
    path("student/login/", views.student_login_page, name="student_login_page"),
    path("student/change-pin/", views.student_change_pin_page, name="student_change_pin_page"),
    path("student/dashboard/", views.student_dashboard_page, name="student_dashboard_page"),
    path("student/", views.student_portal_page, name="student_portal_page"),
    path("student/logout/", views.student_logout_page, name="student_logout_page"),

    # Analytics
    path("admin-stats/", views.admin_stats_dashboard, name="admin_stats_dashboard"),
    path("admin-stats/student/<int:student_id>/", views.admin_student_profile, name="admin_student_profile"),

    # Teacher auth API
    path("api/auth/teacher-login", views.teacher_login, name="teacher_login"),
    path("api/auth/teacher-logout", views.teacher_logout, name="teacher_logout"),
    path("api/auth/teacher-me", views.teacher_me, name="teacher_me"),

    # Teacher pages
    path("teacher/login/", views.teacher_login_page, name="teacher_login_page"),
    path("teacher/change-pin/", views.teacher_change_pin_page, name="teacher_change_pin_page"),
    path("teacher/", views.teacher_dashboard_page, name="teacher_dashboard_page"),
    path("teacher/sessions/", views.teacher_sessions_page, name="teacher_sessions_page"),
    path("teacher/classes/", views.teacher_classes_page, name="teacher_classes_page"),
    path("teacher/students/", views.teacher_students_page, name="teacher_students_page"),
    path(
        "teacher/students/<int:student_id>/analytics/",
        views.teacher_student_profile,
        name="teacher_student_profile",
    ),
    path("teacher/tasks/", views.teacher_tasks_page, name="teacher_tasks_page"),
    path(
        "teacher/analytics/",
        RedirectView.as_view(url="/admin-stats/", permanent=False),
        name="teacher_analytics_redirect",
    ),

    # Teacher classes API
    path("api/teacher/classes/", views.teacher_classes_api, name="teacher_classes_api"),
    path("api/teacher/classes/<int:class_id>/", views.teacher_class_detail_api, name="teacher_class_detail_api"),

    # Teacher students API
    path("api/teacher/students/", views.teacher_students_api, name="teacher_students_api"),
    path("api/teacher/students/<int:student_id>/", views.teacher_student_detail_api, name="teacher_student_detail_api"),
    path("api/teacher/students/<int:student_id>/reset-pin/", views.teacher_student_reset_pin_api, name="teacher_student_reset_pin_api"),

    # Teacher sessions API
    path("api/teacher/sessions/", views.teacher_sessions_api, name="teacher_sessions_api"),
    path("api/teacher/sessions/<int:session_id>/", views.teacher_session_detail_api, name="teacher_session_detail_api"),
    path("api/teacher/sessions/<int:session_id>/clone/", views.teacher_session_clone_api, name="teacher_session_clone_api"),
    path("api/teacher/sessions/<int:session_id>/classes/", views.teacher_session_classes_api, name="teacher_session_classes_api"),
    path("api/teacher/sessions/<int:session_id>/assign-classes/", views.teacher_session_assign_classes_api, name="teacher_session_assign_classes_api"),
    path(
        "api/teacher/sessions/<int:session_id>/modules/import-json/",
        module_import.teacher_session_modules_import_api,
        name="teacher_session_modules_import_api",
    ),

    # Teacher tasks API
    path("api/teacher/sessions/<int:session_id>/tasks/", views.teacher_session_tasks_api, name="teacher_session_tasks_api"),
    path("api/teacher/tasks/<int:task_id>/", views.teacher_task_detail_api, name="teacher_task_detail_api"),
path(
    "api/teacher/sessions/<int:session_id>/theory-modules/",
    views.teacher_theory_modules_api,
    name="teacher_theory_modules_api",
),
path(
    "api/teacher/theory-modules/<int:module_id>/",
    views.teacher_theory_module_detail_api,
    name="teacher_theory_module_detail_api",
),
path(
    "api/teacher/theory-modules/<int:module_id>/blocks/",
    views.teacher_theory_blocks_api,
    name="teacher_theory_blocks_api",
),
path(
    "api/teacher/theory-blocks/<int:block_id>/",
    views.teacher_theory_block_detail_api,
    name="teacher_theory_block_detail_api",
),
path(
    "api/teacher/theory-modules/<int:module_id>/generate/",
    views.teacher_generate_theory_module_api,
    name="teacher_generate_theory_module_api",
),
path(
    "api/teacher/sessions/<int:session_id>/theory-quizzes/",
    views.teacher_theory_quizzes_api,
    name="teacher_theory_quizzes_api",
),
path(
    "api/teacher/theory-quizzes/<int:module_id>/",
    views.teacher_theory_quiz_detail_api,
    name="teacher_theory_quiz_detail_api",
),
path(
    "api/teacher/theory-quizzes/<int:module_id>/questions/",
    views.teacher_theory_quiz_questions_api,
    name="teacher_theory_quiz_questions_api",
),
path(
    "api/teacher/theory-quiz-questions/<int:question_id>/",
    views.teacher_theory_quiz_question_detail_api,
    name="teacher_theory_quiz_question_detail_api",
),


    # Testcases API
    path("api/teacher/tasks/<int:task_id>/tests/", views.teacher_task_tests_api, name="teacher_task_tests_api"),
    path("api/teacher/tests/<int:test_id>/", views.teacher_test_detail_api, name="teacher_test_detail_api"),

    # Code fragments API
    path("api/teacher/tasks/<int:task_id>/fragments/", views.teacher_task_fragments_api, name="teacher_task_fragments_api"),
    path("api/teacher/fragments/<int:frag_id>/", views.teacher_fragment_detail_api, name="teacher_fragment_detail_api"),

    # UI language
    path("set-ui-language/", views.set_ui_language, name="set_ui_language"),
    path("teacher/modules/", views.teacher_modules_page, name="teacher_modules_page"),
path(
    "api/student/theory-module/<int:module_id>",
    views.student_theory_module_detail,
    name="student_theory_module_detail",
),
path(
    "api/student/theory-quiz/<int:module_id>",
    views.student_theory_quiz_detail,
    name="student_theory_quiz_detail",
),
path(
    "api/student/theory-quiz/<int:module_id>/submit",
    views.student_theory_quiz_submit,
    name="student_theory_quiz_submit",
),

    # Lesson game modules
    path("api/teacher/sessions/<int:session_id>/game-modules/", game_views.teacher_game_modules_api, name="teacher_game_modules_api"),
    path("api/teacher/game-modules/<int:module_id>/", game_views.teacher_game_module_detail_api, name="teacher_game_module_detail_api"),
    path("api/teacher/game-modules/<int:module_id>/prompts/", game_views.teacher_game_prompts_api, name="teacher_game_prompts_api"),
    path("api/teacher/game-prompts/<int:prompt_id>/", game_views.teacher_game_prompt_detail_api, name="teacher_game_prompt_detail_api"),
    path("api/teacher/game-modules/<int:module_id>/wonder-questions/", game_views.teacher_wonder_questions_api, name="teacher_wonder_questions_api"),
    path("api/teacher/game-modules/<int:module_id>/wonder-questions/shuffle/", game_views.teacher_wonder_questions_shuffle_api, name="teacher_wonder_questions_shuffle_api"),
    path("api/teacher/wonder-questions/<int:question_id>/", game_views.teacher_wonder_question_detail_api, name="teacher_wonder_question_detail_api"),
    path("api/teacher/game-modules/<int:module_id>/tournament-stages/", game_views.teacher_tournament_stages_api, name="teacher_tournament_stages_api"),
    path("api/teacher/tournament-stages/<int:stage_id>/", game_views.teacher_tournament_stage_detail_api, name="teacher_tournament_stage_detail_api"),
    path("api/teacher/tournament-stages/<int:stage_id>/questions/", game_views.teacher_tournament_questions_api, name="teacher_tournament_questions_api"),
    path("api/teacher/tournament-questions/<int:question_id>/", game_views.teacher_tournament_question_detail_api, name="teacher_tournament_question_detail_api"),
    path("api/teacher/game-modules/<int:module_id>/rounds/open/", game_views.teacher_game_open_round_api, name="teacher_game_open_round_api"),
    path("api/teacher/game-rounds/<int:round_id>/start/", game_views.teacher_game_start_round_api, name="teacher_game_start_round_api"),
    path("api/teacher/game-rounds/<int:round_id>/finish/", game_views.teacher_game_finish_round_api, name="teacher_game_finish_round_api"),
    path("api/teacher/game-rounds/<int:round_id>/state/", game_views.teacher_game_round_state_api, name="teacher_game_round_state_api"),
    path("api/teacher/game-rounds/<int:round_id>/penalty/", game_views.teacher_game_round_penalty_api, name="teacher_game_round_penalty_api"),
    path("api/teacher/game-rounds/<int:round_id>/penalty/remove/", game_views.teacher_game_round_remove_penalty_api, name="teacher_game_round_remove_penalty_api"),

    path("api/student/game-module/<int:module_id>/", game_views.student_game_module_api, name="student_game_module_api"),
    path("api/student/game-module/<int:module_id>/ready/", game_views.student_game_ready_api, name="student_game_ready_api"),
    path("api/student/game-rounds/<int:round_id>/state/", game_views.student_game_round_state_api, name="student_game_round_state_api"),
    path("api/student/game-rounds/<int:round_id>/answer/", game_views.student_game_answer_api, name="student_game_answer_api"),
    path("api/student/game-rounds/<int:round_id>/letter/", game_views.student_game_letter_api, name="student_game_letter_api"),
    path("api/student/game-rounds/<int:round_id>/tournament-answer/", game_views.student_tournament_answer_api, name="student_tournament_answer_api"),

    # Exams
    path("teacher/exams/", exam_views.teacher_exams_page, name="teacher_exams_page"),
    path(
        "teacher/exams/attempts/<int:attempt_id>/grade/",
        exam_views.teacher_exam_attempt_grade_page,
        name="teacher_exam_attempt_grade_page",
    ),
    path("api/teacher/exams/", exam_views.teacher_exams_api, name="teacher_exams_api"),
    path("api/teacher/exams/import-json/", exam_views.teacher_exam_import_api, name="teacher_exam_import_api"),
    path("api/teacher/exams/<int:exam_id>/", exam_views.teacher_exam_detail_api, name="teacher_exam_detail_api"),
    path("api/teacher/exams/<int:exam_id>/questions/", exam_views.teacher_exam_questions_api, name="teacher_exam_questions_api"),
    path("api/teacher/exam-questions/<int:question_id>/", exam_views.teacher_exam_question_detail_api, name="teacher_exam_question_detail_api"),
    path("api/teacher/exams/<int:exam_id>/attempts/", exam_views.teacher_exam_attempts_api, name="teacher_exam_attempts_api"),
    path("api/teacher/exam-attempts/<int:attempt_id>/", exam_views.teacher_exam_attempt_detail_api, name="teacher_exam_attempt_detail_api"),
    path("api/teacher/exam-answers/<int:answer_id>/grade/", exam_views.teacher_exam_answer_grade_api, name="teacher_exam_answer_grade_api"),

    path("student/exams/", exam_views.student_exams_page, name="student_exams_page"),
    path("student/exam-results/<int:attempt_id>/", exam_views.student_exam_result_page, name="student_exam_result_page"),
    path("api/student/exams/", exam_views.student_exams_api, name="student_exams_api"),
    path("api/student/exams/<int:exam_id>/", exam_views.student_exam_detail_api, name="student_exam_detail_api"),
    path("api/student/exams/<int:exam_id>/start/", exam_views.student_exam_start_api, name="student_exam_start_api"),
    path("api/student/exams/<int:exam_id>/questions/<int:question_id>/answer/", exam_views.student_exam_answer_api, name="student_exam_answer_api"),
    path("api/student/exams/<int:exam_id>/integrity/", exam_views.student_exam_integrity_api, name="student_exam_integrity_api"),
    path("api/student/exams/<int:exam_id>/submit/", exam_views.student_exam_submit_api, name="student_exam_submit_api"),

    # Peer assessment
    path("teacher/assessment/", peer_views.teacher_peer_assessment_page, name="teacher_peer_assessment_page"),
    path("teacher/assessment/<int:session_id>/results/<int:attempt_id>/", peer_views.teacher_peer_result_page, name="teacher_peer_result_page"),
    path("api/teacher/peer-sessions/", peer_views.teacher_peer_sessions_api, name="teacher_peer_sessions_api"),
    path("api/teacher/peer-sessions/<int:session_id>/", peer_views.teacher_peer_session_detail_api, name="teacher_peer_session_detail_api"),
    path("api/teacher/peer-attempts/search/", peer_views.teacher_peer_attempt_search_api, name="teacher_peer_attempt_search_api"),
    path("api/teacher/peer-sessions/<int:session_id>/assignments/", peer_views.teacher_peer_assignments_api, name="teacher_peer_assignments_api"),
    path("api/teacher/peer-assignments/<int:assignment_id>/", peer_views.teacher_peer_assignment_detail_api, name="teacher_peer_assignment_detail_api"),
    path("api/teacher/peer-sessions/<int:session_id>/autofill/", peer_views.teacher_peer_autofill_api, name="teacher_peer_autofill_api"),
    path("api/teacher/peer-reviews/<int:review_id>/moderate/", peer_views.teacher_peer_review_moderate_api, name="teacher_peer_review_moderate_api"),

    path("student/peer-assessment/", peer_views.student_peer_assessment_page, name="student_peer_assessment_page"),
    path("api/student/peer-sessions/", peer_views.student_peer_sessions_api, name="student_peer_sessions_api"),
    path("api/student/peer-assignments/<int:assignment_id>/", peer_views.student_peer_assignment_detail_api, name="student_peer_assignment_detail_api"),
    path("api/student/peer-assignments/<int:assignment_id>/questions/<int:question_id>/review/", peer_views.student_peer_review_api, name="student_peer_review_api"),

    # Health
    path("healthz/", views.healthz, name="healthz"),
]
