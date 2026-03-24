from django.urls import path
from django.views.generic import RedirectView
from . import views

urlpatterns = [
    # Student auth API
    path("api/auth/student-login", views.student_login, name="student_login"),
    path("api/auth/student-logout", views.student_logout, name="student_logout"),
    path("api/auth/student-me", views.student_me, name="student_me"),

    # Student session/tasks API
    path("api/student/active-session", views.student_active_session, name="student_active_session"),
    path("api/student/task/<int:task_id>", views.student_task_detail, name="student_task_detail"),
    path("api/student/task/<int:task_id>/submit", views.student_submit, name="student_submit"),
    path("api/student/task/<int:task_id>/hint/<int:level>", views.student_hint_level, name="student_hint_level"),

    # Student pages
    path("student/login/", views.student_login_page, name="student_login_page"),
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
    path("teacher/", views.teacher_dashboard_page, name="teacher_dashboard_page"),
    path("teacher/sessions/", views.teacher_sessions_page, name="teacher_sessions_page"),
    path("teacher/classes/", views.teacher_classes_page, name="teacher_classes_page"),
    path("teacher/students/", views.teacher_students_page, name="teacher_students_page"),
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
    path("api/teacher/sessions/<int:session_id>/classes/", views.teacher_session_classes_api, name="teacher_session_classes_api"),
    path("api/teacher/sessions/<int:session_id>/assign-classes/", views.teacher_session_assign_classes_api, name="teacher_session_assign_classes_api"),

    # Teacher tasks API
    path("api/teacher/sessions/<int:session_id>/tasks/", views.teacher_session_tasks_api, name="teacher_session_tasks_api"),
    path("api/teacher/tasks/<int:task_id>/", views.teacher_task_detail_api, name="teacher_task_detail_api"),

    # Testcases API
    path("api/teacher/tasks/<int:task_id>/tests/", views.teacher_task_tests_api, name="teacher_task_tests_api"),
    path("api/teacher/tests/<int:test_id>/", views.teacher_test_detail_api, name="teacher_test_detail_api"),

    # Code fragments API
    path("api/teacher/tasks/<int:task_id>/fragments/", views.teacher_task_fragments_api, name="teacher_task_fragments_api"),
    path("api/teacher/fragments/<int:frag_id>/", views.teacher_fragment_detail_api, name="teacher_fragment_detail_api"),

    # UI language
    path("set-ui-language/", views.set_ui_language, name="set_ui_language"),

    # Health
    path("healthz/", views.healthz, name="healthz"),
]