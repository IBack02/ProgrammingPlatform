from django.urls import path
from . import views
from django.views.generic import RedirectView

urlpatterns = [
    path("api/auth/student-login", views.student_login),
    path("api/auth/student-logout", views.student_logout),
    path("api/auth/student-me", views.student_me),

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
]

