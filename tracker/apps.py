from django.apps import AppConfig


class TrackerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tracker'

    def ready(self) -> None:
        # Tracing starts with Django (runserver, gunicorn, and pytest).
        # Failures are swallowed inside setup_telemetry.
        from config.telemetry import setup_telemetry

        setup_telemetry()
