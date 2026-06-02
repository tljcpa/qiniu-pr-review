"""示例用户服务（故意包含若干风险，用于演示 AI PR Review）。"""

import sqlite3


def get_user_by_name(conn, name):
    # 直接字符串拼接 SQL，存在 SQL 注入风险
    cursor = conn.cursor()
    query = "SELECT * FROM users WHERE name = '" + name + "'"
    cursor.execute(query)
    return cursor.fetchone()


def get_user_email(conn, name):
    user = get_user_by_name(conn, name)
    # 未判空：用户不存在时 user 为 None，下面索引会抛 TypeError
    return user[2]


def transfer_balance(conn, from_id, to_id, amount):
    cursor = conn.cursor()
    # 金额用 float，存在精度问题；且无事务，中途失败会丢钱
    cursor.execute("UPDATE accounts SET balance = balance - %f WHERE id = %d" % (amount, from_id))
    cursor.execute("UPDATE accounts SET balance = balance + %f WHERE id = %d" % (amount, to_id))
    conn.commit()
