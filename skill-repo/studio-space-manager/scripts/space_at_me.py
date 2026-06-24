"""
=================================================================
studio-space-manager - @我查询模块 (v2.9.0)
=================================================================
iCenter 空间@我页面查询技能

查询 iCenter 空间中@提及当前用户的消息列表，支持按日期/时间段筛选，自动提取与用户相关的页面内容摘要。

跨平台支持：
- ✅ Linux (Ubuntu/CentOS/Debian)
- ✅ Windows 10/11
- ✅ macOS (通过 Python)

使用方式：
  # 单日
  python3 space_at_me.py --date today       # 今天
  python3 space_at_me.py --date yesterday   # 昨天
  python3 space_at_me.py --date 前天
  python3 space_at_me.py --date 2026-03-05

  # 时段（起始日到今天）
  python3 space_at_me.py --date 本周
  python3 space_at_me.py --date 本月
  python3 space_at_me.py --date 今年

  # 区间（几号到几号）
  python3 space_at_me.py --date 2026-03-01~2026-03-10
  python3 space_at_me.py --date 3-1~3-10   # 当年 3月1日 到 3月10日

依赖：
- Python 3.6+
- requests
- markitdown（可选，用于内容摘要提取）

作者：Co-Claw Team
创建：2026-03-05
更新：2026-03-14
"""

import json
import os
import platform
import re
import tempfile
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from _platform_utils import configure_stdio_utf8

# 禁用 SSL 证书验证警告
try:
    import urllib3

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

# ---------------------------------------------------------------------------
# 步骤1：读取@我的页面内容（列表归一化、日期过滤、拉取页面并提取摘要）
# ---------------------------------------------------------------------------


def _normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 atMsg/list 单条数据统一为：title, contentId, anchor, spaceId, atDateTime, fromEmp。
    用于列表展示与按日期过滤，以及调用内容接口时传参。
    """
    title = (
        item.get("title") or item.get("contentName") or item.get("contentTitle") or ""
    )
    content_id = item.get("contentId") or ""
    anchor = item.get("anchor") or item.get("businessId") or ""
    space_id = item.get("spaceId") or ""
    at_date_time = item.get("atDateTime") or item.get("createDate") or ""
    from_emp = item.get("fromEmp") or ""
    sourceType = item.get("sourceType") or "CONTENT"
    # 兼容旧字段
    space_name = item.get("spaceName") or item.get("space_name") or ""
    page_url = item.get("pageUrl") or item.get("url") or item.get("link") or ""
    return {
        "title": str(title).strip() or "（无标题）",
        "contentId": str(content_id).strip(),
        "anchor": str(anchor).strip(),
        "spaceId": str(space_id).strip(),
        "atDateTime": str(at_date_time).strip(),
        "fromEmp": str(from_emp).strip(),
        "spaceName": str(space_name).strip(),
        "pageUrl": str(page_url).strip(),
        "sourceType": str(sourceType).strip(),
    }


def _filter_by_date_range(
    items: List[Dict[str, Any]], start_iso: str, end_iso: str
) -> List[Dict[str, Any]]:
    """只保留 atDateTime 在 [start_iso, end_iso]（含首尾）的项。日期格式 YYYY-MM-DD。"""
    if not start_iso or not end_iso or len(start_iso) < 10 or len(end_iso) < 10:
        return items
    start_prefix = start_iso[:10]
    end_prefix = end_iso[:10]
    if start_prefix > end_prefix:
        start_prefix, end_prefix = end_prefix, start_prefix
    result = []
    for i in items:
        at = (i.get("atDateTime") or "")[:10]
        if at and start_prefix <= at <= end_prefix:
            result.append(i)
    return result


def _build_page_url(
    space_id: str, content_id: str, sourceType: str, anchor: str
) -> str:
    """
    根据 spaceId、contentId 生成 iCenter 页面链接（用于跳转）。
    - 若 sourceType 为 CONTENT，则追加 ?anchor=anchor 查询参数；
    - 若 sourceType 为 COMMENT，则追加 ?commentId=anchor 查询参数；
    - 其他情况仅返回基础链接。
    """
    if not space_id or not content_id:
        return ""
    base_url = f"https://i.zte.com.cn/#/shared/{space_id}/wiki/page/{content_id}/view"
    # 未提供 anchor 时直接返回基础链接
    if not anchor:
        return base_url
    st = (sourceType or "").upper()
    if st == "CONTENT":
        return f"{base_url}?anchor={anchor}"
    if st == "COMMENT":
        return f"{base_url}?commentId={anchor}"
    return base_url


def html_to_markdown(html: str) -> str:
    """
    使用 markitdown 库将 HTML 转为 Markdown。
    若未安装 markitdown 或转换失败，则退回为简单剥标签的纯文本。
    """
    if not html or not html.strip():
        return ""
    try:
        from markitdown import MarkItDown

        md = MarkItDown()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            f.write(html)
            path = f.name
        try:
            result = md.convert(path)
            out = (result.text_content or "").strip()
            return out if out else _html_to_plain_fallback(html)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception:
        return _html_to_plain_fallback(html)


def _html_to_plain_fallback(html: str) -> str:
    """markitdown 不可用时的回退：简单剥标签得到纯文本。"""
    if not html or not html.strip():
        return ""
    s = re.sub(r"<script[^>]*>[\s\S]*?</script>", "\n", html, flags=re.I)
    s = re.sub(r"<style[^>]*>[\s\S]*?</style>", "\n", s, flags=re.I)
    s = re.sub(r"</p>|</div>|</tr>|</li>|</h[1-6]>|<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;", " ", s)
    s = re.sub(r"&lt;", "<", s)
    s = re.sub(r"&gt;", ">", s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


def _filter_user_related_lines(
    lines: List[str], user_emp_no: str, anchor: str = ""
) -> List[str]:
    """
    从行列表中过滤出与当前用户相关的内容：含用户工号、@ 或 anchor 所在段落。
    """
    related = []
    anchor_nearby = []
    for i, ln in enumerate(lines):
        if user_emp_no and user_emp_no in ln:
            related.append(ln)
        elif "@" in ln or "＠" in ln:
            related.append(ln)
        if anchor and anchor in ln:
            start = max(0, i - 1)
            end = min(len(lines), i + 2)
            anchor_nearby.extend(lines[start:end])
    # 优先仅使用与用户直接相关的内容；无则用 anchor 附近（通常为 @ 所在块）
    if related:
        return related
    if anchor_nearby:
        return anchor_nearby
    return []


def _summary_from_content(content: str) -> str:
    """对已过滤出的内容做去空格，生成摘要（不截断，防止内容失真）。"""
    if not content or not content.strip():
        return "—"
    return re.sub(r"\s+", " ", content.strip())


def get_user_related_content(md_text: str, user_emp_no: str, anchor: str = "") -> str:
    """
    从 Markdown 内容中找出与当前用户相关的内容（含用户工号或 @ 的段落，或 anchor 附近）。
    返回过滤后的文本，无相关内容时返回空字符串。
    """
    if not md_text or not md_text.strip():
        return ""
    lines = [ln.strip() for ln in md_text.splitlines() if ln.strip()]
    if not lines:
        lines = [s.strip() for s in re.split(r"[。！？\n]+", md_text) if s.strip()]
    if not lines:
        return ""
    filtered = _filter_user_related_lines(lines, user_emp_no, anchor)
    if not filtered:
        return ""
    seen = set()
    unique = []
    for c in filtered:
        if c not in seen and len(c) > 2:
            seen.add(c)
            unique.append(c)
    return "\n".join(unique)


def extract_user_related_summary(
    md_text: str, user_emp_no: str = "", anchor: str = ""
) -> str:
    """
    提取页面的核心摘要，帮助用户快速了解被@的内容要点。
    策略：
    1. 提取页面开头包含@或关键信息的内容
    2. 提取包含动作词（需要/请/整改等）的段落
    3. 提取页面第一段描述性内容
    """
    if not md_text or not md_text.strip():
        return "—"

    # 清理并分段
    text = md_text
    text = re.sub(r"#{1,6}\s*", "", text)  # 移除标题标记
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)  # 移除链接格式
    text = re.sub(r"`([^`]+)`", r"\1", text)  # 移除行内代码

    # 按段落分割
    paragraphs = []
    for para in text.split("\n"):
        para = para.strip()
        if len(para) >= 10:
            # 跳过纯表格/列表行
            if para.startswith("|") or para.startswith("-"):
                continue
            paragraphs.append(para)

    if not paragraphs:
        return "—"

    # 策略1：查找包含 @ 或工号的开头段落
    for para in paragraphs[:5]:  # 只看前5段
        if "@" in para or "＠" in para:
            return _summary_from_content(para)[:100]
        if user_emp_no and user_emp_no in para:
            return _summary_from_content(para)[:100]

    # 策略2：查找包含动作词的段落
    action_keywords = [
        "需要",
        "请",
        "要求",
        "整改",
        "完成",
        "处理",
        "确认",
        "整合",
        "合并",
        "规范",
        "计划",
        "目标",
        "问题",
    ]
    for para in paragraphs[:5]:
        if any(kw in para for kw in action_keywords):
            return _summary_from_content(para)[:100]

    # 策略3：返回第一段
    return _summary_from_content(paragraphs[0])[:100]


# ---------------------------------------------------------------------------
# 步骤2：将内容组装成表格形式
# ---------------------------------------------------------------------------


def _format_at_me_list(items: List[Dict[str, Any]]) -> str:
    """格式化为简洁的 Markdown 表格：谁、时间、页面（标题+链接）、摘要。"""
    if not items:
        return "未找到空间@我的页面"

    # 按时间倒序
    sorted_items = sorted(items, key=lambda x: x.get("atDateTime", ""), reverse=True)

    lines = [
        "### 📋 空间 @ 我 汇总",
        "",
        "**点击标题跳转原文**",
        "",
        "| 👤 | ⏰ | 页面 | 📝 摘要 |",
        "|:---|:---|:---|:---|",
    ]

    # 内容摘要截断长度
    SUMMARY_MAX_LEN = 80

    for row in sorted_items:
        from_emp = (row.get("fromEmp") or "").strip()
        if not from_emp:
            from_emp = "—"
        # 提取工号部分（如有）
        if "0" in from_emp and len(from_emp) >= 10:
            # 尝试分离姓名和工号
            import re

            match = re.match(r"^(.+?)(\d{10})$", from_emp)
            if match:
                from_emp = f"{match.group(1)}`{match.group(2)}`"

        at = row.get("atDateTime") or ""
        if at:
            # 只保留月-日 时:分
            try:
                # 处理 2026-03-01T00:21:40 格式
                at_clean = at.replace("Z", "").replace("+00:00", "").split(".")[0]
                dt = datetime.fromisoformat(at_clean)
                at = dt.strftime("%m-%d %H:%M")
            except Exception:
                # 备用：直接字符串处理
                if " " in at:
                    at = at.split(" ")[1][:5]

        title = (row.get("title") or "").strip() or "（无标题）"
        title = title.replace("|", "\\|")

        link = _build_page_url(
            row.get("spaceId") or "",
            row.get("contentId") or "",
            row.get("sourceType") or "",
            row.get("anchor") or "",
        )
        title_cell = f"[{title}]({link})" if link else title

        summary = row.get("contentSummary") or ""
        summary = summary.replace("|", "\\|").replace("\n", " ").strip()
        if len(summary) > SUMMARY_MAX_LEN:
            summary = summary[:SUMMARY_MAX_LEN] + "..."
        if not summary:
            summary = "—"

        lines.append(f"| {from_emp} | {at} | {title_cell} | {summary} |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 接口与工具
# ---------------------------------------------------------------------------

# 统一从 system_param 复用 system 参数生成逻辑，避免在本文件中重复实现日期和哈希算法。
from system_param import build_icenter_api_url, build_system_param, get_user_and_token

# iCenter @我 列表接口（与前端请求一致）
AT_MSG_LIST_BASE = build_icenter_api_url("/zte-rd-icenter-advanced/atMsg/list")
# 页面 HTML 内容接口（contentId、spaceId、dataType=HTML）
CONTENT_HTML_BASE = build_icenter_api_url("/zte-rdcloud-space-content/content")


def get_beijing_date_iso() -> str:
    """返回当前北京时间的 YYYY-MM-DD，用于按日期过滤。"""
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    return f"{beijing_now.year}-{beijing_now.month:02d}-{beijing_now.day:02d}"


def get_beijing_yesterday_iso() -> str:
    """返回北京时间的昨天 YYYY-MM-DD。"""
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    yesterday = beijing_now - timedelta(days=1)
    return f"{yesterday.year}-{yesterday.month:02d}-{yesterday.day:02d}"


def get_beijing_day_before_yesterday_iso() -> str:
    """返回北京时间的前天 YYYY-MM-DD。"""
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    day_before = beijing_now - timedelta(days=2)
    return f"{day_before.year}-{day_before.month:02d}-{day_before.day:02d}"


def _parse_single_date_iso(s: str) -> Optional[str]:
    """将单日字符串解析为 YYYY-MM-DD，支持 YYYY-MM-DD、YYYYMMDD、M-D（当年）。"""
    s = s.strip()
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", s):
        parts = s.split("-")
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    if re.match(r"^\d{8}$", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    # M-D 或 MM-DD 视为当年
    if re.match(r"^\d{1,2}-\d{1,2}$", s):
        beijing_tz = timezone(timedelta(hours=8))
        year = datetime.now(beijing_tz).year
        parts = s.split("-")
        return f"{year}-{int(parts[0]):02d}-{int(parts[1]):02d}"
    return None


def resolve_date_range(date_str: str) -> Tuple[str, str]:
    """
    将用户输入解析为时间段 (start_iso, end_iso)，均为 YYYY-MM-DD。
    支持：
    - 单日：今天/昨天/前天、today/yesterday、或 YYYY-MM-DD；
    - 时段：本周、本月、今年（均为起始日到今天）；
    - 区间：start~end 或 start 到 end（如 2026-03-01~2026-03-10、3-1~3-10）。
    """
    beijing_tz = timezone(timedelta(hours=8))
    today_iso = get_beijing_date_iso()
    if not date_str or not date_str.strip():
        return today_iso, today_iso

    raw = date_str.strip()
    s = raw.lower()

    # 单日：今天、昨天、前天
    if s in ("today", "今天"):
        return today_iso, today_iso
    if s in ("yesterday", "昨天"):
        y = get_beijing_yesterday_iso()
        return y, y
    if s in ("前天", "day before yesterday"):
        d = get_beijing_day_before_yesterday_iso()
        return d, d

    # 时段：本周（周一至今）、本月（1号至今）、今年（1月1日至今）
    if s in ("本周", "this_week", "this week", "thisweek"):
        now = datetime.now(beijing_tz)
        # 周一为 0，周日为 6
        weekday = now.weekday()
        monday = now - timedelta(days=weekday)
        start_iso = f"{monday.year}-{monday.month:02d}-{monday.day:02d}"
        return start_iso, today_iso
    if s in ("本月", "this_month", "this month", "thismonth"):
        now = datetime.now(beijing_tz)
        start_iso = f"{now.year}-{now.month:02d}-01"
        return start_iso, today_iso
    if s in ("今年", "this_year", "this year", "thisyear"):
        now = datetime.now(beijing_tz)
        start_iso = f"{now.year}-01-01"
        return start_iso, today_iso

    # 区间：start~end 或 start 到 end
    sep = "~" if "~" in raw else "到"
    if sep in raw:
        parts = re.split(r"\s*~\s*|\s+到\s+", raw, maxsplit=1)
        if len(parts) == 2:
            start_iso = _parse_single_date_iso(parts[0])
            end_iso = _parse_single_date_iso(parts[1])
            if start_iso and end_iso:
                if start_iso <= end_iso:
                    return start_iso, end_iso
                return end_iso, start_iso

    # 单日：具体日期
    single = _parse_single_date_iso(raw)
    if single:
        return single, single

    return today_iso, today_iso


def resolve_date_to_iso(date_str: str) -> str:
    """
    将用户输入的日期解析为 YYYY-MM-DD（兼容旧逻辑，内部委托 resolve_date_range）。
    支持：今天/today、昨天/yesterday、前天、或具体日期 YYYY-MM-DD。
    """
    start_iso, _ = resolve_date_range(date_str or "today")
    return start_iso


class SpaceAtMeSkill:
    """
    访问空间@我页面 Skill。
    步骤1：读取@我的页面内容；步骤2：将内容组装成表格形式（由模型提炼后返回用户）。
    """

    def __init__(self, filter_date: Optional[str] = None) -> None:
        self.offset = int(os.environ.get("SPACE_AT_ME_OFFSET", "0"))
        self.limit = int(os.environ.get("SPACE_AT_ME_LIMIT", "100"))
        self.space_id = os.environ.get("SPACE_AT_ME_SPACE_ID", "undefined")
        raw = (
            filter_date
            if filter_date is not None
            else os.environ.get("SPACE_AT_ME_DATE", "today") or "today"
        ).strip()
        self.filter_start, self.filter_end = resolve_date_range(raw)
        self.fetch_html = os.environ.get("SPACE_AT_ME_FETCH_HTML", "1").strip() == "1"
        self.fetch_html_limit = int(
            os.environ.get("SPACE_AT_ME_FETCH_HTML_LIMIT", "100")
        )

    def _get_user_and_token(self) -> tuple:
        """返回 (user_id, token)，从环境变量 coclaw_empno、coclaw_token 获取。"""
        user_id, token = get_user_and_token(strict=False)
        return (user_id or "").strip(), (token or "").strip()

    def _build_headers(self, user_id: str, token: str) -> Dict[str, str]:
        """构建与 curl 一致的请求头（列表与内容接口通用）。"""
        return {
            "Accept": "application/json, text/plain",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Origin": "https://i.zte.com.cn",
            "Referer": "https://i.zte.com.cn/",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "X-Auth-Value": token,
            "X-Cookie-From-Cros": "Y",
            "X-Emp-No": user_id,
            "X-Lang-Id": "zh_CN",
            "X-Tenant-Id": "ZTE",
        }

    def fetch_at_me_list(self) -> Dict[str, Any]:
        """
        调用 iCenter atMsg/list 接口获取@我列表。
        返回: {"success": bool, "items": [...], "message": str}
        """
        user_id, token = self._get_user_and_token()
        if not user_id or not token:
            return {
                "success": False,
                "items": [],
                "message": "无法获取用户账号和Token，请确保已设置 coclaw_empno 和 coclaw_token 环境变量，如仍报错请重启 Co-Claw 服务后重试",
            }
        try:
            t_ms = int(time.time() * 1000)
            params = {
                "offset": self.offset,
                "limit": self.limit,
                "t": t_ms,
                "spaceId": self.space_id,
                "environmentType": "browser",
                "system": build_system_param("space"),
            }
            headers = self._build_headers(user_id, token)
            resp = requests.get(
                AT_MSG_LIST_BASE,
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            # 实际返回结构: { "bo": { "count": N, "list": [...] }, "code": {...} }
            bo = data.get("bo") or {}
            raw_list = bo.get("list") if isinstance(bo, dict) else []
            if not isinstance(raw_list, list):
                raw_list = []
            items = [_normalize_item(i) for i in raw_list]
            return {"success": True, "items": items, "message": "查询成功"}
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "items": [],
                "message": f"网络请求错误: {str(e)}",
            }
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "items": [],
                "message": f"响应解析错误: {str(e)}",
            }
        except Exception as e:
            return {
                "success": False,
                "items": [],
                "message": f"未知错误: {str(e)}",
            }

    def fetch_content_html(
        self, content_id: str, space_id: str, user_id: str, token: str
    ) -> Optional[str]:
        """
        调用内容接口获取页面 HTML。GET content/{contentId}?t=timestamp&spaceId=...&dataType=HTML
        """
        if not content_id or not space_id:
            return None
        try:
            t_ms = int(time.time() * 1000)
            url = f"{CONTENT_HTML_BASE}/{content_id}"
            params = {
                "t": t_ms,
                "spaceId": space_id,
                "environmentType": "browser",
                "system": build_system_param("space"),
                "dataType": "HTML",
            }
            headers = self._build_headers(user_id, token)
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            text = resp.text
            # 可能是 JSON：{"bo":{"contentBody":"<html>..."}} 或 {"data":"<html>..."} 或直接 HTML
            if text.strip().startswith("{"):
                try:
                    j = resp.json()
                    bo = j.get("bo") or {}
                    content = (
                        bo.get("contentBody")
                        or j.get("data")
                        or j.get("content")
                        or j.get("html")
                    )
                    return content if content is not None else text
                except Exception:
                    return text
            return text
        except Exception:
            return None

    def _read_at_me_content(self) -> tuple:
        """
        步骤1：读取@我的页面内容。
        返回 (success, items, error_message)。
        若 success 为 True，items 为过滤后的列表，且每条可能含 contentSummary（当 fetch_html 时）。
        """
        result = self.fetch_at_me_list()
        if not result["success"]:
            return False, [], f"查询失败: {result['message']}"
        items = result["items"]
        if self.filter_start and self.filter_end:
            items = _filter_by_date_range(items, self.filter_start, self.filter_end)
            if not items:
                if self.filter_start == self.filter_end:
                    return False, [], f"未找到日期为 {self.filter_start} 的空间@我页面"
                return (
                    False,
                    [],
                    f"未找到 {self.filter_start} 至 {self.filter_end} 时段内的空间@我页面",
                )
        if self.fetch_html and items:
            user_id, token = self._get_user_and_token()
            if user_id and token:
                for i, item in enumerate(items):
                    if i >= self.fetch_html_limit:
                        break
                    cid = item.get("contentId")
                    sid = item.get("spaceId")
                    if not cid or not sid:
                        continue
                    html = self.fetch_content_html(cid, sid, user_id, token)
                    md_text = html_to_markdown(html or "")
                    anchor = item.get("anchor") or ""
                    item["contentSummary"] = (
                        extract_user_related_summary(md_text, user_id, anchor)
                        if md_text
                        else "—"
                    )
        return True, items, ""

    def _assemble_table(self, items: List[Dict[str, Any]]) -> str:
        """步骤2：将内容组装成表格形式（谁、提及时间、页面标题、链接、内容摘要）。"""
        return _format_at_me_list(items)

    def execute(self) -> str:
        """按技能步骤执行：读取@我的页面内容 → 组装成表格 → 返回（供模型提炼后返回用户）。"""
        success, items, message = self._read_at_me_content()
        if not success:
            return message
        return self._assemble_table(items)


def main() -> None:
    configure_stdio_utf8()
    import argparse

    parser = argparse.ArgumentParser(description="查询空间@我的列表，支持按日期或时间段过滤。")
    parser.add_argument(
        "--date",
        "-d",
        default="today",
        metavar="DATE",
        help=(
            "过滤日期或时段：today/今天、yesterday/昨天、前天；"
            "本周、本月、今年；或 2026-03-01~2026-03-10、3-1~3-10（区间）"
        ),
    )
    args = parser.parse_args()
    skill = SpaceAtMeSkill(filter_date=args.date)
    print(skill.execute())


# =============================================================================
# 跨平台支持 - 平台检测
# =============================================================================


def detect_platform() -> Tuple[str, str, bool]:
    """
    检测当前操作系统平台。

    Returns:
        Tuple[系统类型, 系统版本, 是否为Windows]
        例如: ('Windows', '10', True) 或 ('Linux', '4.19.112-2.el8.x86_64', False)
    """
    system = platform.system()  # Windows, Linux, Darwin
    release = platform.release()
    is_windows = system == "Windows"
    return system, release, is_windows


def get_log_dir() -> str:
    """
    获取日志目录，支持跨平台运行。

    Returns:
        日志目录路径
    """
    system, _, is_windows = detect_platform()

    if is_windows or os.name != "posix":
        temp_base = tempfile.gettempdir()
        log_dir = os.path.join(temp_base, "studio-space-manager", "log")
    else:
        log_dir = os.path.join(
            os.path.expanduser("~"),
            ".openclaw",
            "workspace",
            "skills",
            "studio-space-manager",
            "log",
        )

    try:
        os.makedirs(log_dir, exist_ok=True)
        if not is_windows and os.name == "posix":
            os.chmod(log_dir, 0o700)
    except Exception:
        temp_base = tempfile.gettempdir()
        log_dir = os.path.join(temp_base, "studio-space-manager", "log")
        os.makedirs(log_dir, exist_ok=True)

    return log_dir


# 初始化平台信息
SYSTEM, SYSTEM_RELEASE, IS_WINDOWS = detect_platform()


if __name__ == "__main__":
    main()
