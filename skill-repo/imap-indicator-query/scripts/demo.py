# -*- coding: utf-8 -*-
"""
@author: 00114732
@time: 2026/3/30 16:00
"""
import requests

url = "https://aip.zte.com.cn/middle/api/login"

payload = {
    "username": "YXdata-skill",
    "password": "xxxx"
}
headers = {
    "Content-Type": "application/json"
   }

response = requests.request("POST", url, json=payload, headers=headers)

print(response.text)