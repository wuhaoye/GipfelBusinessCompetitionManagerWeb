from django.apps import AppConfig


class CompetitionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.competitions"
    label = "competitions"

    def ready(self):
        # 财年更迭信号：把 FiscalYear 的写入接入 signals.notify_fiscal_year_changed
        from .signals import connect_fiscal_year_signals

        connect_fiscal_year_signals()
