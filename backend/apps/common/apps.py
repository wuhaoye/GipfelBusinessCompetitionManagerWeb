from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    label = "common"

    def ready(self):
        # C2 阶段 1：SQLite PRAGMA 调优（WAL / synchronous / busy_timeout）。
        # 必须在 django.setup() 期间接线，早于任何连接建立；函数自身幂等
        # （dispatch_uid + 模块标志），ready() 被重复调用不会重复 PRAGMA。
        from .db_pragmas import connect_db_pragmas

        connect_db_pragmas()

        # 等全部 app populate 完，再对已注册模型统一 connect 写操作信号
        # （审计落库 + 实时广播）
        from .signals import connect_all_signals

        connect_all_signals()
