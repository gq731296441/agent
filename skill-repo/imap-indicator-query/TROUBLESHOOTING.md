# TROUBLESHOOTING.md - 常见问题解决指南

## 🚨 认证问题

### Q1: 认证失败 - "缺少认证信息"
**现象**：返回错误 `{"status": false, "error": "缺少认证信息"}`
**解决方案**：
1. 设置环境变量：
   ```bash
   export coclaw_empno="00114732"
   export coclaw_token="your_auth_token"
   ```
2. 或在调用时显式传入参数：
   ```python
   result = query_indicator(
       indicator_name="MR交付周期（天）",
       query_type="项目组",
       query_name="ZXUFP",
       empno="00114732",
       token="your_token"
   )
   ```

### Q2: token过期或无效
**现象**：`认证失败: 获取公共令牌失败` 或 `登录失败`
**解决方案**：
1. 重新从企业OA系统获取新的认证token
2. token可能已过期，企业token通常24小时有效
3. 确认coclaw_token格式正确，没有多余空格

### Q3: yxdata-token缓存问题
**现象**：中途突然认证失败
**解决方案**：
1. 清除缓存重新认证：`auth = AuthManager()` 创建新实例
2. yxdata-token缓存8小时，过期前1小时自动刷新
3. 如需强制刷新，重启脚本进程

---

## 📊 数据查询问题

### Q4: 指标名称找不到
**现象**：`{"status": false, "error": "未找到指示器"}`
**解决方案**：
1. 先列出所有可用指标：
   ```python
   result = query_indicator_list_export()
   indicators = result['indicators']
   # 查看所有指标名称
   for ind in indicators:
       print(ind['name'])
   ```
2. 使用模糊匹配：尝试更短的关键词，如"MR交付"而不是"MR交付周期（天）"
3. 确认指标名称包含正确的括号和字

### Q5: 查询条件名称不存在
**现象**：`{"status": false, "error": "未找到项目组：XXX"}`
**解决方案**：
1. 检查输入的项目组/部门名称是否正确
2. 查看自身的权限：
   ```python
   result = query_user_auth(empno, token)
   user_perms = get_user_auth_types(result)
   print(user_perms['项目组级'])  # 查看有权限的项目组
   ```
3. 确认用户对该项目组有访问权限

### Q6: 返回数据为空
**现象**：查询成功但 `content: []`
**解决方案**：
1. 检查时间范围是否合理，可能该时段无数据
2. 该资源可能确实没有数据
3. 尝试扩大时间范围，或使用默认（不传时间参数）

---

## 🔒 权限问题

### Q7: 无权访问资源
**现象**：`{"status": false, "error": "无权访问 项目组：ZXUFP"}`
**解决方案**：
1. 使用查询权限接口确认当前用户的权限范围：
   ```python
   result = query_user_auth(empno, token)
   ```
2. 如果缺少所需权限，联系系统管理员添加
3. 确认查询类型与权限级别匹配（项目组级、部门级、中心级、分中心级）

---

## 🌐 Web界面的问题

### Q8: 无法访问Web界面 `http://127.0.0.1:5000`
**现象**：Connection refused 或 Cannot GET /
**解决方案**：
1. 确保已启动Web服务器：
   ```bash
   python server.py --port 5000
   ```
2. 检查端口是否被占用，更换端口：
   ```bash
   python server.py --port 8080
   # 访问 http://127.0.0.1:8080
   ```
3. 如果服务器运行在遥远程主机，使用正确的IP地址

### Q9: Web界面查询失败
**现象**：点击"开始查询"后显示错误
**解决方案**：
1. 检查浏览器控制台（F12）是否有网络错误
2. 确认已正确设置环境变量 `coclaw_empno` 和 `coclab_token`
3. API请求失败详细信息会显示在页面上方的消息框中

---

## 📦 依赖问题

### Q10: ImportError: No module named 'requests'
**解决方案**：
```bash
pip install -r requirements.txt
```

### Q11: 无法启动Flask服务器
**现象**：`ModuleNotFoundError: No module named 'flask'`
**解决方案**：
```bash
pip install flask
```

---

## 📈 数据可视化问题

### Q12: 图表无法显示
**现象**：数据返回但图表区域空白
**可能原因**：
1. 数据中没有检测到合适的数值字段
2. X轴字段识别错误
3. Chart.js库加载失败

**解决方案**：
1. 查看返回的 `chart` 字段确认 `x_field` 和 `series_fields`
2. 确保数据中包含数值类型的字段
3. 检查浏览器控制台是否有JavaScript错误

---

## 🕐 时间和时区问题

### Q13: 时间格式错误
**现象**：`Invalid date format` 或 `返回数据为空`
**解决方案**：
1. 时间格式必须为 `YYYY-MM-DD`，例如 `"2026-01-01"`
2. 不需要传递毫秒或时间部分
3. 如果不需要时间过滤，直接不传 `start_date` 和 `end_date`

---

## 🐛 已知限制

1. **to_user_passwd**：永久有效但敏感，任何情况下都不会在输出中显示
2. **yxdata-token**：8小时有效期，过期需重新认证
3. **缓存策略**：当前实现为内存缓存，进程重启后缓存丢失
4. **网络超时**：所有HTTP请求超时设置为30秒
5. **环境变量优先级**：显式传入参数 > 环境变量 > 默认值

---

## 🔧 调试技巧

### 启用详细日志
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### 查看原始响应
```python
result = query_indicator(...)
print(json.dumps(result, ensure_ascii=False, indent=2))
```

### 检查缓存状态
```python
auth = AuthManager()
print(f"YX Token: {auth.yxdata_token[:20] if auth.yxdata_token else 'None'}")
print(f"Expire: {auth.token_expire - time.time():.0f} seconds left")
```

---

## 📞 联系支持

如果以上方法都无法解决问题：

1. 收集错误信息完整截图
2. 记录复现步骤
3. 提供环境信息（操作系统、Python版本等）
4. 联系企业IT支持或系统管理员

---

**祝使用愉快！🎉**