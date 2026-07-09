"""内置工具：查询员工信息（姓名、部门、邮箱等）。

调用 ZTE iCenter API 获取指定工号的员工详细信息。
"""

import httpx
from langchain.tools import tool
from pydantic import BaseModel, Field

from config import get_logger
from config.config import app_config

logger = get_logger(__name__)


class _EmployeeInfoInput(BaseModel):
    """查询员工信息的输入参数。"""
    employee_id: str = Field(
        description=(
            "员工工号(employeeShortId)，例如 '10344649'。"
            "不传时默认查询当前登录用户的信息。"
        ),
    )


def create_get_employee_info(user_id: str, user_token: str):
    """工厂函数：创建绑定了当前用户身份的 get_employee_info 工具。

    通过闭包注入 user_id/user_token，工具调用时无需从 RunnableConfig 获取。
    与 SpillContext 模式一致（上下文在构造时注入）。
    """
    _current_user_id = user_id
    _user_token = (user_token or "").strip()

    @tool("get_employee_info", args_schema=_EmployeeInfoInput)
    async def get_employee_info(employee_id: str = "") -> str:
        """查询中兴员工信息：姓名、部门、邮箱、职位等。

        根据员工工号查询组织架构中的员工详细信息。
        不传 employee_id 时默认查询当前登录用户的信息。
        使用场景：用户询问"我是谁"、"我的信息"、"查一下 xxx 的邮箱/部门"等。
        """
        target_emp_no = employee_id.strip() or _current_user_id

        if not _user_token:
            return "错误：缺少用户认证 Token，无法查询员工信息。请确认已登录。"

        api_url = app_config.EMP_INFO_API_URL
        headers = {
            "x-emp-no": target_emp_no,
            "X-Auth-Value": _user_token,
            "Content-Type": "application/json",
        }
        payload = {
            "employeeShortId": target_emp_no,
            "enableLabel": True,
            "keyword": target_emp_no,
        }

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                trust_env=False,
                timeout=httpx.Timeout(15.0),
            ) as client:
                response = await client.post(api_url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

        except httpx.TimeoutException:
            logger.warning(f"查询员工信息超时 employee_id={target_emp_no}")
            return f"查询员工 [{target_emp_no}] 信息超时，请稍后重试。"
        except httpx.HTTPStatusError as e:
            logger.warning(f"查询员工信息 HTTP 错误 employee_id={target_emp_no} status={e.response.status_code}")
            if e.response.status_code == 401:
                return "认证失败，Token 可能已过期，请重新登录。"
            elif e.response.status_code == 403:
                return "权限不足，无法查询该员工信息。"
            elif e.response.status_code == 404:
                return f"未找到员工 [{target_emp_no}] 的信息。"
            else:
                return f"查询员工 [{target_emp_no}] 信息失败 (HTTP {e.response.status_code})，请稍后重试。"
        except httpx.RequestError as e:
            logger.warning(f"查询员工信息网络错误 employee_id={target_emp_no} err={e}")
            return f"网络请求失败，请检查网络连接后重试。"
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"查询员工信息解析错误 employee_id={target_emp_no} err={e}")
            return f"解析返回数据失败：{e}"

        # 校验业务返回码
        code = data.get("code", {})
        if code.get("code") != "0000":
            msg = code.get("msg", "未知错误")
            logger.warning(f"查询员工信息业务错误 employee_id={target_emp_no} code={code.get('code')} msg={msg}")
            return f"获取员工信息失败: [{code.get('code')}] {msg}"

        # 提取员工信息
        bo_list = data.get("bo", [])
        if not bo_list:
            return f"未找到员工号为 {target_emp_no} 的员工信息"

        emp = bo_list[0]

        result = (
            f"员工信息:\n"
            f"  姓名: {emp.get('name', '')}\n"
            f"  英文名: {emp.get('nameEn', '')}\n"
            f"  工号: {emp.get('employeeShortId', '')}\n"
            f"  邮箱: {emp.get('email', '')}\n"
            f"  部门: {emp.get('deptFullName', '')}\n"
            f"  职位层级: {emp.get('postLevelNo', '')}\n"
            f"  工作地点: {emp.get('longWorkPlace', '')}\n"
        )
        logger.info(f"查询员工信息成功 employee_id={target_emp_no} name={emp.get('name', '')}")
        return result

    return get_employee_info
