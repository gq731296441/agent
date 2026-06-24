def ensure_import(import_func, package_name, invalidate_prefixes=()):
    """尝试导入模块，失败时返回 None（不自动安装）。"""
    try:
        return import_func()
    except ImportError:
        return None
