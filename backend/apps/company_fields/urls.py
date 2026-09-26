"""公司产业字段路由：挂在 /api 前缀下（无尾随斜杠）。

前端契约：
- GET /api/company-fields?companyIds=1,2,3    批量读取多公司字段值（C3 新增，§4.2）
- GET /api/company-fields/:companyId           读取字段值（既有，行为不变）
- PUT /api/company-fields/:companyId           批量写入（既有，行为不变）
- PUT /api/company-fields/:companyId/:fieldId  单字段写入（既有，行为不变）
"""
from django.urls import path

from .views import CompanyFieldItemView, CompanyFieldsBatchView, CompanyFieldsView

app_name = "company_fields"

urlpatterns = [
    # 批量（新增）：`company-fields` 与 `company-fields/<int:company_id>` 不会互相匹配，
    # 声明在前只是让「无 id 的集合读」一眼可见；既有两条路由原样保留。
    path(
        "company-fields",
        CompanyFieldsBatchView.as_view(),
        name="company-fields-batch",
    ),
    path(
        "company-fields/<int:company_id>",
        CompanyFieldsView.as_view(),
        name="company-fields-collection",
    ),
    path(
        "company-fields/<int:company_id>/<int:field_id>",
        CompanyFieldItemView.as_view(),
        name="company-fields-item",
    ),
]
