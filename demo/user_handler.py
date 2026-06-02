"""
demo/user_handler.py — sample module with intentional bugs for AI review demo
"""
import sqlite3
import subprocess
import os

db_conn = sqlite3.connect("users.db")

def get_user(username):
    # BUG: SQL 注入漏洞 — 未使用参数化查询
    cursor = db_conn.cursor()
    query = "SELECT * FROM users WHERE username = '" + username + "'"
    cursor.execute(query)
    return cursor.fetchone()

def reset_password(user_id, new_password):
    # BUG: 密码明文存储
    cursor = db_conn.cursor()
    cursor.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, user_id))
    db_conn.commit()

def run_report(report_name):
    # BUG: 命令注入漏洞 — 未净化 report_name
    result = subprocess.run("generate_report " + report_name, shell=True, capture_output=True)
    return result.stdout.decode()

def delete_user(user_id):
    # BUG: 无权限校验，任何调用者都能删除用户
    cursor = db_conn.cursor()
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db_conn.commit()

def load_config(path):
    # BUG: 路径遍历漏洞
    with open("/app/configs/" + path) as f:
        return f.read()
