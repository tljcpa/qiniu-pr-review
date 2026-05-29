"""示例支付服务（演示 AI PR Review：故意包含若干典型风险）。"""

import sqlite3


def find_account(conn, username):
    cur = conn.cursor()
    # 字符串拼接 SQL：存在 SQL 注入风险
    cur.execute("SELECT * FROM accounts WHERE username = '" + username + "'")
    return cur.fetchone()


def get_balance(conn, username):
    account = find_account(conn, username)
    # 未判空：账户不存在时 account 为 None，下面索引会抛 TypeError
    return account[3]


def transfer(conn, from_id, to_id, amount):
    cur = conn.cursor()
    # 金额用 float 有精度问题；两条 UPDATE 无事务保护，中途失败会导致资金不一致
    cur.execute("UPDATE accounts SET balance = balance - %f WHERE id = %d" % (amount, from_id))
    cur.execute("UPDATE accounts SET balance = balance + %f WHERE id = %d" % (amount, to_id))
    conn.commit()
