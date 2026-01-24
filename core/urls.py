from django.urls import path
from . import views

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
]

