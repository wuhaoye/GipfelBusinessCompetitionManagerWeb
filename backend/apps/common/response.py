"""统一响应包装。

成功：{ code:0, message:"成功", data }
错误：{ code:<http或业务码>, message:"中文提示", data:null }

所有 REST 响应都经 JSONRenderer 包装为该格式。

大数安全：渲染前递归把绝对值超过 2^53 的 int 转为字符串（JS Number 精确
整数上限 2^53，10^23 级金额以 JSON number 出站会被前端 JSON.parse 静默
丢精度）；Decimal 按整数值→int / 小数→字符串转换。见 renderers.py。
"""
from __future__ import annotations

from rest_framework.renderers import JSONRenderer as DRFJSONRenderer

from apps.common.renderers import _convert_big_numbers


class JSONRenderer(DRFJSONRenderer):
    """把 DRF 响应数据统一包装为 { code, message, data }。

    - 视图直接返回 dict/list/None：视为 data，code=0, message="成功"
    - 视图显式返回已包装结构（含 'code' 键）：原样返回
    - 异常由 exception_handler 处理后同样走此渲染器
    - 大数兜底：>2^53 的 int / Decimal 出站前转字符串（不因转换异常阻断响应）
    """

    def render(self, data, accepted_media_type=None, renderer_context=None):
        try:
            data = _convert_big_numbers(data)
        except Exception:  # noqa: BLE001  兜底转换绝不阻断正常响应
            pass

        # 已包装（显式构造的 {code, message, data}）原样返回，避免双重包装。
        # 必须三键齐全才判定为已包装：仅凭 "code" in data 会误伤业务字段恰好
        # 叫 code 的单对象响应（如产业类型编号 / 股票代码）——那些响应会被
        # 跳过包装，前端拦截器拿到 code=<业务值> 而误报"请求失败"。
        if _is_wrapped(data):
            return super().render(data, accepted_media_type, renderer_context)

        wrapped = {
            "code": 0,
            "message": "成功",
            "data": data,
        }
        return super().render(wrapped, accepted_media_type, renderer_context)


def success(data=None, message: str = "成功"):
    """视图内显式构造成功响应（通常直接 return data 即可，此函数用于需要自定义 message）。"""
    return {"code": 0, "message": message, "data": data}


def error(code: int, message: str, data=None, error_code: str | None = None):
    """构造错误响应体（供异常处理器使用）。

    `error_code`：机器可读的稳定标识（取自 DRF 异常的 code，如 `must_change_password` /
    `token_version_mismatch` / `expired`）。前端据此区分语义——例如「必须先改密」与
    「会话已过期」都是 401，只靠中文文案判断既脆弱又容易误报（真机事故：强制改密门禁的
    401 被前端当成会话过期，清掉 token 后改密必然报「登录已过期」）。
    该字段为**增量**字段，老前端忽略它即可。
    """
    body = {"code": code, "message": message, "data": data}
    if error_code:
        body["errorCode"] = error_code
    return body


def _is_wrapped(data) -> bool:
    """判定响应数据是否为已包装结构 {code, message, data}。

    三键齐全才视为已包装：error()/success() 与异常处理器构造的信封均含
    code+message+data；业务模型不可能同时拥有这三个字段，因此不会误判
    （修复：产业类型/股票等带 code 业务字段的对象响应曾被跳过包装）。
    """
    return (
        isinstance(data, dict)
        and "code" in data
        and "message" in data
        and "data" in data
    )
