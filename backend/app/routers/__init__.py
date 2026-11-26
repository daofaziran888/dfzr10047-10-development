"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。

渠道采用接口的固定路径要先于通用 ``/api/{module}`` 动态路由注册，
否则 ``/api/tmpl_adopt/apply`` 会被当成 ``/api/{module}/{entry_id}`` 抢先匹配。
"""
from __future__ import annotations

from app.routers import resource, tmpl_adopt

ROUTERS = [tmpl_adopt, resource]
