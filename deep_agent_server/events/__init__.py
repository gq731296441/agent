"""Event 处理器注册。"""

from deep_agent_server.events.bus import get_event_bus
from deep_agent_server.events.processors.audit import audit_processor
from deep_agent_server.events.processors.metrics import metrics_processor
from deep_agent_server.events.processors.tool_logs import tool_logs_processor


def register_processors() -> None:
    bus = get_event_bus()
    bus.register(metrics_processor)
    bus.register(tool_logs_processor)
    bus.register(audit_processor)
