from django.urls import path
from . import views

urlpatterns = [
    path("api/auth/student-login", views.student_login),
    path("api/auth/student-logout", views.student_logout),
    path("api/auth/student-me", views.student_me),
]
