"""比赛与财年模型。"""
from django.db import models


class Competition(models.Model):
    """比赛（租户根）。删除时级联删除所有子资源。"""

    STATUS_CHOICES = [("ACTIVE", "ACTIVE"), ("CLOSED", "CLOSED")]

    name = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=16, default="ACTIVE", choices=STATUS_CHOICES)
    # 地图背景图 JSON 字符串 {url, filename, width, height}
    map_background = models.TextField(null=True, blank=True)
    # 股票系统全局配置 JSON（null = DEFAULT_STOCK_CONFIG）
    stock_config = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "competitions"

    def __str__(self):
        return self.name


class FiscalYear(models.Model):
    STATUS_CHOICES = [("ACTIVE", "ACTIVE"), ("CLOSED", "CLOSED")]

    competition = models.ForeignKey(
        Competition,
        on_delete=models.CASCADE,
        related_name="fiscal_years",
    )
    year = models.IntegerField()
    status = models.CharField(max_length=16, default="ACTIVE", choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "fiscal_years"
        unique_together = (("competition", "year"),)
        indexes = [models.Index(fields=["competition", "updated_at"])]

    def save(self, *args, **kwargs):
        """保存前记录库中的原状态，供财年更迭信号判定迁移（ACTIVE→CLOSED 等）。

        Django 的 post_save 不提供字段级 diff，而「财年更迭」恰恰是**状态迁移**语义
        （views 里原先只能自己比对 prev_status，仅在接口路径上成立；这里覆盖 save()
        后，ORM / admin / 归档导入等所有写路径都能得到一致的迁移判定）。

        代价是每次更新多一次主键查询；财年写入是低频操作（一年一次），可以接受。
        """
        if self.pk:
            self._fy_prev_status = (
                type(self).objects.filter(pk=self.pk).values_list("status", flat=True).first()
            )
        super().save(*args, **kwargs)
