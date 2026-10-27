from django.urls import path

from . import collection_auth_views, collection_views, companion_views, set_preview_views, views, wallet_views


app_name = "tower"

urlpatterns = [
    path("tower/", collection_views.journal, name="home"),
    path("tower/login/", collection_auth_views.login, name="collection_login"),
    path("tower/lock/", collection_auth_views.lock, name="collection_lock"),
    path("teacher/tower/password/<int:student_id>/reset/", collection_auth_views.reset, name="collection_password_reset"),
    path("tower/actions/<str:action>/", collection_views.student_action, name="student_action"),
    path("api/tower/actions/<str:action>/", collection_views.student_action, name="student_action_api"),
    path("api/tower/status/", views.status, name="status"),
    path("api/tower/wallet/", wallet_views.wallet_api, name="wallet"),
    path("api/tower/companion/source/", companion_views.learning_source, name="companion_source"),
    path("api/tower/companion/status/", companion_views.status, name="companion_status"),
    path("api/tower/companion/<str:action>/", companion_views.action, name="companion_action"),
    path("teacher/tower/wallets/", wallet_views.teacher_wallets, name="teacher_wallets"),
    path("api/teacher/tower/awards/", wallet_views.award_api, name="award"),
    path("api/teacher/tower/wallets/<int:student_id>/", wallet_views.teacher_history_api, name="teacher_history"),
    path("teacher/tower/", views.teacher_page, name="teacher"),
    path("teacher/tower/preview/", views.preview, name="preview"),
    path("teacher/tower/sets/preview/", set_preview_views.set_preview, name="set_preview"),
    path("api/teacher/tower/classes/", views.teacher_classes, name="teacher_classes"),
    path("api/teacher/tower/classes/<int:class_id>/", views.class_access, name="class_access"),
]
