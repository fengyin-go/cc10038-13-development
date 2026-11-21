"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import missing_value_router, resource, pipeline_router

# 固定路径（/api/pipeline、/api/missing-values 等）必须先于通用
# /api/{module} 资源路由注册，否则会被动态模块路径抢先匹配。
ROUTERS = [missing_value_router, resource, pipeline_router]
