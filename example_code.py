"""
测试示例 - 包含各种代码问题的 Python 文件
用于测试代码审查助手的功能
"""

# 问题 1: 缺少类型注解和文档字符串
def calculate_total(items):
    total = 0
    for item in items:
        total = total + item['price'] * item['quantity']  # 可以优化
    return total


# 问题 2: 潜在的除零错误
def calculate_average(numbers):
    return sum(numbers) / len(numbers)  # 如果 numbers 为空会报错


# 问题 3: SQL 注入风险
def get_user(username):
    import sqlite3
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    # 不安全的 SQL 拼接
    query = f"SELECT * FROM users WHERE username = '{username}'"
    cursor.execute(query)
    return cursor.fetchone()


# 问题 4: 资源泄漏
def read_file(filename):
    f = open(filename, 'r')  # 没有关闭文件
    content = f.read()
    return content


# 问题 5: 性能问题 - O(n²) 复杂度
def find_duplicates(items):
    duplicates = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i] == items[j]:
                duplicates.append(items[i])
    return duplicates


# 问题 6: 命名不规范
def DoSomething(x, y):
    MyVariable = x + y  # 变量命名不符合 Python 规范
    return MyVariable


# 问题 7: 异常处理不当
def parse_json(json_string):
    import json
    try:
        return json.loads(json_string)
    except:  # 捕获所有异常，不推荐
        return None


# 问题 8: 硬编码敏感信息
API_KEY = "sk-1234567890abcdef"  # 不应该硬编码
DATABASE_PASSWORD = "admin123"


# 问题 9: 可变默认参数
def add_item(item, items=[]):
    items.append(item)
    return items


# 问题 10: 未使用的导入和变量
import os
import sys
import random

unused_variable = 42


class UserManager:
    # 问题 11: 类缺少文档字符串
    def __init__(self):
        self.users = []

    # 问题 12: 方法过长，职责不单一
    def process_user(self, user_data):
        # 验证
        if not user_data.get('name'):
            return False
        if not user_data.get('email'):
            return False
        if '@' not in user_data['email']:
            return False

        # 处理
        user = {
            'name': user_data['name'],
            'email': user_data['email'],
            'created_at': 'now'
        }

        # 保存
        self.users.append(user)

        # 发送邮件
        print(f"Sending email to {user['email']}")

        # 记录日志
        print(f"User {user['name']} created")

        return True
