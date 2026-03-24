from django.urls import path
from . import views
from django.views.generic import RedirectView

urlpatterns = [
    path("api/auth/student-login", views.student_login),
    path("api/auth/student-logout", views.student_logout),
    path("api/auth/student-me", views.student_me),
    path("teacher/sessions/", views.teacher_sessions_page, name="teacher_sessions_page"),
    path("api/student/active-session", views.student_active_session),
    path("api/student/task/<int:task_id>", views.student_task_detail),
    path("api/student/task/<int:task_id>/submit", views.student_submit),
    path("api/student/task/<int:task_id>/hint/<int:level>", views.student_hint_level),

    path("student/login/", views.student_login_page),
    path("student/", views.student_portal_page),
    path("student/logout/", views.student_logout_page),
    path("admin-stats/", views.admin_stats_dashboard, name="admin_stats_dashboard"),
    path("admin-stats/student/<int:student_id>/", views.admin_student_profile, name="admin_student_profile"),
# Teacher auth API
    path("api/auth/teacher-login", views.teacher_login),
    path("api/auth/teacher-logout", views.teacher_logout),
    path("api/auth/teacher-me", views.teacher_me),
path("api/teacher/sessions/", views.teacher_sessions_api),
path("api/teacher/sessions/<int:session_id>/", views.teacher_session_detail_api),
path("api/teacher/sessions/<int:session_id>/classes/", views.teacher_session_classes_api),
path("api/teacher/sessions/<int:session_id>/assign-classes/", views.teacher_session_assign_classes_api),
# Teacher pages
    path("teacher/login/", views.teacher_login_page),
    path("teacher/", views.teacher_dashboard_page),
    path("teacher/", views.teacher_dashboard_page),
    path("teacher/sessions/", views.teacher_sessions_page),
    path("teacher/classes/", views.teacher_classes_page),
    path("teacher/students/", views.teacher_students_page),
    path("teacher/tasks/", views.teacher_tasks_page),
    path("teacher/analytics/", RedirectView.as_view(url="/admin-stats/", permanent=False)),
    path("healthz/", views.healthz),
    path("api/teacher/classes/", views.teacher_classes_api),
    path("api/teacher/classes/<int:class_id>/", views.teacher_class_detail_api),
    path("api/teacher/students/", views.teacher_students_api),
    path("api/teacher/students/<int:student_id>/", views.teacher_student_detail_api),
    path("api/teacher/students/<int:student_id>/reset-pin/", views.teacher_student_reset_pin_api),
# Tasks (per session)
path("api/teacher/sessions/<int:session_id>/tasks/", views.teacher_session_tasks_api),

# Task detail
path("api/teacher/tasks/<int:task_id>/", views.teacher_task_detail_api),

# Testcases
path("api/teacher/tasks/<int:task_id>/tests/", views.teacher_task_tests_api),
path("api/teacher/tests/<int:test_id>/", views.teacher_test_detail_api),

# Code fragments
path("api/teacher/tasks/<int:task_id>/fragments/", views.teacher_task_fragments_api),
path("api/teacher/fragments/<int:frag_id>/", views.teacher_fragment_detail_api),
path("set-ui-language/", views.set_ui_language, name="set_ui_language"),
]

