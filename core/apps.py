from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from django.core.signals import got_request_exception

        from .error_reports import _on_request_exception, install_log_handler

        install_log_handler()
        got_request_exception.connect(_on_request_exception, dispatch_uid="core_error_reports")
