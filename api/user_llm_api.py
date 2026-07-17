"""用户级 LLM 配置 API。"""

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, Body, Query
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from agent_server.llm import create_llm_from_config
from common.schemas import ApiResponse
from config import app_config
from db import deep_storage

router = APIRouter(prefix="/users/me", tags=["user-llm"])


class UserLlmConfigBody(BaseModel):
    model: str = Field(..., description="主模型名称")
    base_url: str = Field(..., description="主模型 API Base URL")
    api_key: Optional[str] = Field(
        default=None,
        description="主模型 API Key；留空表示不修改已保存的 Key",
    )
    fast_model: Optional[str] = Field(default=None, description="快速模型名称（可选）")
    fast_base_url: Optional[str] = Field(default=None, description="快速模型 Base URL（可选）")
    fast_api_key: Optional[str] = Field(
        default=None,
        description="快速模型 API Key；留空表示不修改已存 fast Key",
    )
    clear_fast: bool = Field(default=False, description="为 true 时清除快速模型配置")


class UserLlmTestBody(BaseModel):
    """测试连接。

    支持两种模式：
    - 传完整 model + base_url + api_key：直接测试，无需事先保存
    - 不传或部分传：从 DB 已保存配置回退
    """
    target: Literal["main", "fast"] = "main"
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = Field(default=None, description="API Key；留空则从 DB 读取已保存 Key")


async def _resolve_test_credentials(user_id: str, body: UserLlmTestBody):
    """解析测试凭证。

    优先使用 body 传入的完整凭证（支持未保存时测试）；
    body 未传则从 DB 已保存配置回退。
    """
    body_model = (body.model or "").strip()
    body_base_url = (body.base_url or "").strip()
    body_api_key = (body.api_key or "").strip()

    # 前端传了完整凭证 → 直接使用，无需查 DB
    if body_model and body_base_url and body_api_key:
        return body_model, body_base_url, body_api_key

    # 回退到 DB 已保存配置
    stored = await deep_storage.get_user_llm_config(user_id)
    if body.target == "fast":
        fast = stored.get("fast") or {}
        model = body_model or fast.get("model") or ""
        base_url = body_base_url or fast.get("base_url") or ""
        if fast.get("configured"):
            api_key = await deep_storage.get_user_secret(user_id, "llm_fast_api_key") or ""
        else:
            model = body_model or stored.get("model") or ""
            base_url = body_base_url or stored.get("base_url") or ""
            api_key = await deep_storage.get_user_secret(user_id, "llm_api_key") or ""
    else:
        model = body_model or stored.get("model") or ""
        base_url = body_base_url or stored.get("base_url") or ""
        api_key = await deep_storage.get_user_secret(user_id, "llm_api_key") or ""
    return model.strip(), base_url.strip(), api_key.strip()


@router.get("/llm-config", response_model=ApiResponse)
async def get_llm_config(user_id: str = Query(...)):
    data = await deep_storage.get_user_llm_config(user_id)
    return ApiResponse.success(data=data)


@router.put("/llm-config", response_model=ApiResponse)
async def put_llm_config(
    body: UserLlmConfigBody = Body(...),
    user_id: str = Query(...),
):
    if not body.model.strip() or not body.base_url.strip():
        return ApiResponse.error(msg="主模型 model、base_url 不能为空", code=400)

    existing = await deep_storage.get_user_llm_config(user_id)
    new_key = (body.api_key or "").strip()
    if not new_key and not existing.get("has_api_key"):
        return ApiResponse.error(msg="首次配置必须填写主模型 api_key", code=400)

    await deep_storage.set_user_llm_config(
        user_id,
        body.model.strip(),
        body.base_url.strip(),
        api_key=new_key if new_key else None,
    )

    if body.clear_fast:
        await deep_storage.clear_user_fast_llm_config(user_id)
    else:
        fast_model = (body.fast_model or "").strip() if body.fast_model is not None else None
        fast_base = (body.fast_base_url or "").strip() if body.fast_base_url is not None else None
        fast_key = (body.fast_api_key or "").strip() if body.fast_api_key is not None else None
        existing_fast = existing.get("fast") or {}

        if body.fast_model is not None or body.fast_base_url is not None or body.fast_api_key is not None:
            if not fast_model and not fast_base and not fast_key and not existing_fast.get("configured"):
                await deep_storage.clear_user_fast_llm_config(user_id)
            elif not fast_model and not fast_base and not fast_key:
                await deep_storage.clear_user_fast_llm_config(user_id)
            elif fast_model and fast_base:
                if not fast_key and not existing_fast.get("has_api_key"):
                    return ApiResponse.error(
                        msg="快速模型首次配置需填写 fast_api_key",
                        code=400,
                    )
                await deep_storage.set_user_fast_llm_config(
                    user_id,
                    model=fast_model,
                    base_url=fast_base,
                    api_key=fast_key if fast_key else None,
                )
            elif fast_model or fast_base:
                return ApiResponse.error(
                    msg="快速模型需同时填写 fast_model 与 fast_base_url",
                    code=400,
                )
            elif fast_key and existing_fast.get("model") and existing_fast.get("base_url"):
                await deep_storage.set_user_fast_llm_config(
                    user_id,
                    api_key=fast_key,
                )

    cfg = await deep_storage.get_user_llm_config(user_id)
    return ApiResponse.success(data=cfg, msg="已保存")


@router.post("/llm-config/test", response_model=ApiResponse)
async def test_llm_config(
    user_id: str = Query(...),
    body: UserLlmTestBody = Body(default_factory=UserLlmTestBody),
):
    model, base_url, api_key = await _resolve_test_credentials(user_id, body)
    if not model or not base_url or not api_key:
        return ApiResponse.error(
            msg="请先保存配置或填写完整的 model、base_url、api_key 后测试",
            code=400,
        )
    timeout = app_config.LLM_TEST_TIMEOUT_SECONDS
    try:
        llm = create_llm_from_config(
            model=model,
            base_url=base_url,
            api_key=api_key,
            streaming=False,
            use_reasoning=False,
        )
        resp = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content="Hi, reply with OK only.")]),
            timeout=timeout,
        )
        text = getattr(resp, "content", str(resp))
        return ApiResponse.success(
            data={
                "ok": True,
                "target": body.target,
                "reply_preview": str(text)[:200],
            }
        )
    except asyncio.TimeoutError:
        return ApiResponse.error(
            msg=(
                f"连接测试超时（{timeout}s），请检查："
                "1) 模型名称是否正确；"
                "2) Base URL 是否可达；"
                "3) API Key 是否有效"
            ),
            code=502,
        )
    except Exception as e:
        return ApiResponse.error(msg=f"连接测试失败: {e}", code=502)
