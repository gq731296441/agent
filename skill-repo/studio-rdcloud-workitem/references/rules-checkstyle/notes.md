```json
[
  {
    "trigger": "触发器的名字",
    "rules": [
      {
        "condition": "条件的名字",
        "actionRules": [
          {
            "action": {
              "unSupport": "支持的操作"
            },
            "preCondition": "前提条件: true 为无条件支持"
          },
          {
            "action": {
              "support": "不支持的操作"
            },
            "preCondition": "前提条件: trustMode == false"
          },
          {
            "action": {
              "support": "支持的操作"
            },
            "preCondition": "前提条件: baseLineType == true"
          }
        ]
      }
    ]
  }        
]
```