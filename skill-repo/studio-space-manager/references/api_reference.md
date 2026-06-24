# iCenter 空间管理员 API 参考

## API 概述

- **服务名称**：空间成员管理服务
- **基础 URL**：`https://icenterapi.zte.com.cn/zte-km-icenter-space`
- **认证方式**：环境变量 `coclaw_empno` 和 `coclaw_token`

## 查询管理员 API

### 接口地址
```
POST /spacemember/loadSpaceAdmins
```

### 完整 URL
```
https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/loadSpaceAdmins
```

### 请求头（Headers）

| 参数名 | 必填 | 值 | 说明 |
|--------|------|-----|------|
| Accept | 是 | `application/json, text/plain, */*` | 接受的内容类型 |
| Accept-Language | 是 | `zh-CN,zh;q=0.9,en;q=0.8` | 语言偏好 |
| Cache-Control | 是 | `no-cache` | 缓存控制 |
| Connection | 是 | `keep-alive` | 连接类型 |
| User-Agent | 是 | `co-claw-skill` | 客户端标识 |
| X-Auth-Value | 是 | `{token}` | 认证令牌 |
| X-Emp-No | 是 | `{emp_no}` | 当前用户工号 |
| X-Lang-Id | 是 | `zh_CN` | 语言 ID |
| X-Tenant-Id | 是 | `ZTE` | 租户 ID |

### 请求体（Body）

```json
{
  "employeeShortId": "0668000519",
  "spaceId": "1920485432",
  "all": "true",
  "environmentType": "browser",
  "system": "space-1920485432"
}
```

| 参数名 | 必填 | 类型 | 说明 |
|--------|------|------|------|
| employeeShortId | 是 | string | 当前操作人的工号 |
| spaceId | 是 | string | 目标空间 ID |
| all | 是 | string | 是否获取全部管理员（固定值 "true"） |
| environmentType | 是 | string | 环境类型（固定值 "browser"） |
| system | 是 | string | 系统标识 |

### 响应格式

#### 成功响应

```json
{
  "code": {
    "code": "0000",
    "msgId": "RetCode.Success",
    "msg": "Success"
  },
  "bo": [
    {
      "id": "0668000003",
      "employeeShortId": "0668000003",
      "name": "王健雄",
      "deptFullName": "缺省/缺省/北京兴云数科技术有限公司",
      "role": 1,
      "isAdmin": 1
    }
  ]
}
```

#### 响应字段说明

**code 对象**

| 字段 | 类型 | 说明 |
|------|------|------|
| code | string | 状态码，"0000" 表示成功 |
| msgId | string | 消息 ID |
| msg | string | 消息描述 |

**bo 数组（管理员列表）**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | **管理员工号** |
| name | string | **管理员姓名** |
| employeeShortId | string | 员工短 ID |
| deptFullName | string | 部门全称 |
| role | int | 角色标识 |
| isAdmin | int | 是否管理员（1=是） |

## 完整调用示例

### Python

```python
import os
import requests

API_URL = "https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/loadSpaceAdmins"

def get_space_admins(space_id):
    emp_no = os.getenv("coclaw_empno")
    token = os.getenv("coclaw_token")
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE"
    }
    
    request_body = {
        "employeeShortId": emp_no,
        "spaceId": space_id,
        "all": "true",
        "environmentType": "browser",
        "system": "space-1920485432"
    }
    
    response = requests.post(
        API_URL,
        json=request_body,
        headers=headers,
        timeout=30
    )
    
    result = response.json()
    if result.get("code", {}).get("code") == "0000":
        return result.get("bo", [])
    else:
        raise Exception(f"API error: {result.get('code', {}).get('msg')}")

# 使用示例
admins = get_space_admins("1920485432")
for admin in admins:
    print(f"工号: {admin['id']}, 姓名: {admin['name']}")
```

### curl

```bash
curl -X POST "https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/loadSpaceAdmins" \
  -H "X-Auth-Value: ${TOKEN}" \
  -H "X-Emp-No: ${EMP_NO}" \
  -H "Content-Type: application/json" \
  -d '{
    "employeeShortId": "'${EMP_NO}'",
    "spaceId": "1920485432",
    "all": "true",
    "environmentType": "browser",
    "system": "space-1920485432"
  }'
```

## 错误码处理

| code 值 | 说明 | 处理建议 |
|---------|------|----------|
| 0000 | 成功 | 正常处理返回数据 |
| 9999 | 错误 | 检查错误消息内容 |

## 注意事项

1. **认证信息**：确保环境变量已正确设置
2. **权限要求**：调用者需要有查看空间管理员的权限
3. **网络要求**：确保能访问 `icenterapi.zte.com.cn`

---

## 查询成员 API（扩展）

### 1. 获取成员数量

#### 接口地址
```
POST /spacemember/getSpaceMemberCount
```

#### 完整 URL
```
https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/getSpaceMemberCount
```

#### 请求头（Headers）
同查询管理员 API

#### 请求体（Body）
```json
{
  "t": 1709312400000,
  "spaceId": "1920485432",
  "environmentType": "browser",
  "system": "space-1920485432"
}
```

| 参数名 | 必填 | 类型 | 说明 |
|--------|------|------|------|
| t | 是 | number | 当前时间戳（毫秒），如：int(time.time() * 1000) |
| spaceId | 是 | string | 目标空间 ID |
| environmentType | 是 | string | 环境类型（固定值 "browser"） |
| system | 是 | string | 系统标识 |

#### 响应格式

```json
{
  "code": {
    "code": "0000",
    "msgId": "RetCode.Success",
    "msg": "Success"
  },
  "bo": 25
}
```

**返回字段说明**
| 字段 | 类型 | 说明 |
|------|------|------|
| bo | int | 成员总数 |

---

### 2. 获取成员列表（分页）

#### 接口地址
```
GET /spacemember/loadMemberInfosWithPageBySpaceId
```

#### 完整 URL
```
https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/loadMemberInfosWithPageBySpaceId
```

#### 请求头（Headers）
同查询管理员 API

#### 请求参数（Query String）
| 参数名 | 必填 | 类型 | 说明 |
|--------|------|------|------|
| employeeShortId | 是 | string | 当前操作人的工号 |
| spaceId | 是 | string | 目标空间 ID |
| startIndex | 是 | int | 起始索引（第一页为0，第二页为30） |
| pageSize | 是 | int | 每页数量（建议30） |
| environmentType | 是 | string | 环境类型（固定值 "browser"） |
| system | 是 | string | 系统标识 |

#### 响应格式

```json
{
  "code": {
    "code": "0000",
    "msgId": "RetCode.Success",
    "msg": "Success"
  },
  "bo": [
    {
      "id": "0668000003",
      "employeeShortId": "0668000003",
      "employeeFullId": "0668000003",
      "name": "王健雄",
      "deptFullName": "缺省/缺省/北京兴云数科技术有限公司",
      "role": 1,
      "isAdmin": 1
    },
    {
      "id": "0668000004",
      "employeeShortId": "0668000004",
      "employeeFullId": "0668000004",
      "name": "张三",
      "deptFullName": "开发三团队/缺省/北京兴云数科技术有限公司",
      "role": 1,
      "isAdmin": 0
    }
  ]
}
```

**返回字段说明**
| 字段 | 类型 | 说明 |
|------|------|------|
| bo | array | 成员列表 |
| bo[].id | string | **成员工号** |
| bo[].name | string | **成员姓名** |
| bo[].isAdmin | int | 是否管理员（1=是，0=否） |

---

## 完整调用示例

### Python（扩展版）

```python
import os
import requests
import time

# API 配置
API_URL_MEMBER_COUNT = "https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/getSpaceMemberCount"
API_URL_MEMBERS = "https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/loadMemberInfosWithPageBySpaceId"

def get_auth_headers():
    emp_no = os.getenv("coclaw_empno")
    token = os.getenv("coclaw_token")
    
    return {
        "Accept": "application/json, text/plain, */*",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE"
    }

def get_member_count(space_id):
    headers = get_auth_headers()
    request_body = {
        "t": int(time.time() * 1000),
        "spaceId": space_id,
        "environmentType": "browser",
        "system": "space-1920485432"
    }
    
    response = requests.post(API_URL_MEMBER_COUNT, json=request_body, headers=headers)
    result = response.json()
    if result.get("code", {}).get("code") == "0000":
        return result.get("bo", 0)
    else:
        raise Exception(f"API error: {result.get('code', {}).get('msg')}")

def get_member_list(space_id, start_index=0, page_size=30):
    headers = get_auth_headers()
    params = {
        "employeeShortId": os.getenv("coclaw_empno"),
        "spaceId": space_id,
        "startIndex": start_index,
        "pageSize": page_size,
        "environmentType": "browser",
        "system": "space-1920485432"
    }
    
    response = requests.get(API_URL_MEMBERS, params=params, headers=headers)
    result = response.json()
    if result.get("code", {}).get("code") == "0000":
        return result.get("bo", [])
    else:
        raise Exception(f"API error: {result.get('code', {}).get('msg')}")

# 使用示例
space_id = "1920485432"
count = get_member_count(space_id)
print(f"成员总数: {count}")

# 分页获取所有成员
all_members = []
page_size = 30
for start_index in range(0, min(count, 1000), page_size):
    members = get_member_list(space_id, start_index, page_size)
    all_members.extend(members)
    print(f"已获取 {len(all_members)}/{count} 个成员")

# 打印成员列表
for member in all_members[:20]:  # 只打印前20个
    is_admin = "✓ 是" if member.get("isAdmin") == 1 else "否"
    print(f"工号: {member['id']}, 姓名: {member['name']}, 是否管理员: {is_admin}")

if len(all_members) > 20:
    print(f"\n... 还有 {len(all_members) - 20} 个成员未显示")
```

### curl（扩展版）

```bash
# 获取成员数量
curl -X POST "https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/getSpaceMemberCount" \
  -H "X-Auth-Value: ${TOKEN}" \
  -H "X-Emp-No: ${EMP_NO}" \
  -H "Content-Type: application/json" \
  -d '{
    "t": 1709312400000,
    "spaceId": "1920485432",
    "environmentType": "browser",
    "system": "space-1920485432"
  }'

# 获取成员列表（第一页）
curl -X GET "https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/loadMemberInfosWithPageBySpaceId?employeeShortId=${EMP_NO}&spaceId=1920485432&startIndex=0&pageSize=30&environmentType=browser&system=space-1920485432" \
  -H "X-Auth-Value: ${TOKEN}" \
  -H "X-Emp-No: ${EMP_NO}"
```

## 注意事项（扩展）

1. **分页获取**：成员数量较多时需要分页获取，每页30个
2. **最大数量**：建议最多获取1000个成员以避免性能问题
3. **成员排序**：返回的成员列表未排序，建议按 isAdmin 字段优先排序管理员
4. **显示限制**：对话中最多显示20个成员，需要提示还有多少成员未显示

---

## 设置成员角色 API

### 1. 设置单个成员角色

#### 接口地址
```
POST /spacemember/updateRoles
```

#### 完整 URL
```
https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/updateRoles?environmentType=browser&system=space-1920484537
```

#### 请求头（Headers）
同查询管理员 API

#### 请求参数（Query String）
| 参数名 | 必填 | 说明 |
|--------|------|------|
| environmentType | 是 | 环境类型（固定值 "browser"） |
| system | 是 | 系统标识 |

#### 请求体（Body）
```json
{
  "members": [
    {
      "employeeShortId": "10132838",
      "role": 0
    }
  ],
  "spaceId": "ca16509db4d644cdb17f19426b3473dc",
  "updateBy": "0668000519"
}
```

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| members | 是 | array | 成员列表 |
| members[].employeeShortId | 是 | string | 成员的工号ID |
| members[].role | 是 | int | 角色（0=普通成员，1=管理员） |
| spaceId | 是 | string | 空间ID |
| updateBy | 是 | string | 操作人的工号ID |

#### 响应格式

```json
{
  "code": {
    "code": "0000",
    "msgId": "RetCode.Success",
    "msg": "Success"
  },
  "bo": [],
  "other": null
}
```

**返回字段说明**
| 字段 | 类型 | 说明 |
|------|------|------|
| code.code | string | 状态码，0000 表示成功 |
| bo | array | 返回被修改的成员信息（可选） |

---

### 2. 批量设置成员角色

#### 请求体示例
```json
{
  "members": [
    {
      "employeeShortId": "10132838",
      "role": 1
    },
    {
      "employeeShortId": "200304117748",
      "role": 0
    }
  ],
  "spaceId": "ca16509db4d644cdb17f19426b3473dc",
  "updateBy": "0668000519"
}
```

支持在一次请求中设置多个成员的权限。

---

## 完整调用示例

### Python

```python
import os
import requests

API_URL_UPDATE_ROLES = "https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/updateRoles"

def get_auth_headers():
    emp_no = os.getenv("coclaw_empno")
    token = os.getenv("coclaw_token")
    
    return {
        "Accept": "application/json, text/plain, */*",
        "X-Auth-Value": token,
        "X-Emp-No": emp_no,
        "X-Lang-Id": "zh_CN",
        "X-Tenant-Id": "ZTE"
    }

def build_system_param(prefix: str = "space") -> str:
    import hashlib
    from datetime import datetime
    from datetime import timedelta, timezone
    
    # 简化的 system 参数生成
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = datetime.now(beijing_tz)
    date_str = f"{beijing_now.year}{beijing_now.month:02d}{beijing_now.day:02d}"
    
    # 简化的哈希计算
    hash_val = sum(ord(c) * (31 ** i) for i, c in enumerate(date_str)) & 0xFFFFFFFF
    if hash_val >= 0x80000000:
        hash_val -= 0x100000000
    return f"{prefix}-{hash_val}"

def set_member_role(space_id, employee_short_id, role, update_by):
    headers = get_auth_headers()
    
    request_body = {
        "members": [
            {
                "employeeShortId": employee_short_id,
                "role": role
            }
        ],
        "spaceId": space_id,
        "updateBy": update_by
    }
    
    params = {
        "environmentType": "browser",
        "system": build_system_param("space")
    }
    
    response = requests.post(
        API_URL_UPDATE_ROLES,
        params=params,
        headers=headers,
        json=request_body,
        timeout=30
    )
    
    result = response.json()
    if result.get("code", {}).get("code") == "0000":
        return {"success": True, "message": "设置成功"}
    else:
        raise Exception(f"API error: {result.get('code', {}).get('msg')}")

# 使用示例
# 设置为管理员
result = set_member_role(
    space_id="ca16509db4d644cdb17f19426b3473dc",
    employee_short_id="200304117748",
    role=1,
    update_by="0668000519"
)
print(result)

# 取消管理员
result = set_member_role(
    space_id="ca16509db4d644cdb17f19426b3473dc",
    employee_short_id="200304117748",
    role=0,
    update_by="0668000519"
)
print(result)
```

### curl

```bash
# 设置为管理员
curl -X POST "https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/updateRoles?environmentType=browser&system=space-1920484537" \
  -H "X-Auth-Value: ${TOKEN}" \
  -H "X-Emp-No: ${EMP_NO}" \
  -H "Content-Type: application/json" \
  -d '{
    "members": [{"employeeShortId": "200304117748", "role": 1}],
    "spaceId": "ca16509db4d644cdb17f19426b3473dc",
    "updateBy": "${EMP_NO}"
  }'

# 取消管理员
curl -X POST "https://icenterapi.zte.com.cn/zte-km-icenter-space/spacemember/updateRoles?environmentType=browser&system=space-1920484537" \
  -H "X-Auth-Value: ${TOKEN}" \
  -H "X-Emp-No: ${EMP_NO}" \
  -H "Content-Type: application/json" \
  -d '{
    "members": [{"employeeShortId": "200304117748", "role": 0}],
    "spaceId": "ca16509db4d644cdb17f19426b3473dc",
    "updateBy": "${EMP_NO}"
  }'
```

## 注意事项（设置角色）

1. **权限要求**：操作者需要有管理员权限才能修改他人角色
2. **角色说明**：role=1 表示管理员，role=0 表示普通成员
3. **批量操作**：members 数组可以包含多个成员，一次性设置
4. **自我限制**：通常不能将自己降为普通成员（需要其他管理员操作）
5. **系统参数**：system 参数需要使用与查询相同的生成规则
