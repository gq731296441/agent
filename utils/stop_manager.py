# utils/stop_manager.py
from threading import Event

# 每个任务一个停止信号
stop_events = {}

def create_stop_event(task_id: str):
    stop_events[task_id] = Event()

def stop_task(task_id: str):
    if task_id in stop_events:
        stop_events[task_id].set()

def is_stopped(task_id: str) -> bool:
    return task_id in stop_events and stop_events[task_id].is_set()

def clear_stop_event(task_id: str):
    stop_events.pop(task_id, None)