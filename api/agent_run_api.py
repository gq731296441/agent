"""Agent 运行状态 API。"""

from fastapi import APIRouter, Query
from common.schemas import ApiResponse
from db import deep_storage
from utils.session_utils import validate_session_access, SESSION_ACCESS_DENIED_MSG

router = APIRouter(prefix="/agent", tags=["agent-runs"])


@router.get("/runs/{run_id}", response_model=ApiResponse)
async def get_agent_run_status(
    run_id: str,
    user_id: str = Query(..., description="用户ID"),
):
    run = await deep_storage.get_agent_run(run_id, user_id)
    if not run:
        return ApiResponse.error(msg="运行记录不存在", code=404)
    return ApiResponse.success(data=run)
