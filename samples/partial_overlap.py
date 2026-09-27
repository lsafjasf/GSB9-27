"""部分重叠: 两个函数共享一段校验逻辑，其余部分不同。"""


def import_users(rows):
    users = []
    skipped = 0
    for row in rows:
        if "name" not in row or "email" not in row:
            skipped += 1
            continue
        name = str(row["name"]).strip()
        email = str(row["email"]).strip().lower()
        if not name or "@" not in email:
            skipped += 1
            continue
        users.append({"name": name, "email": email, "role": "user"})
    return users, skipped


def import_customers(rows):
    customers = []
    skipped = 0
    for row in rows:
        if "name" not in row or "email" not in row:
            skipped += 1
            continue
        name = str(row["name"]).strip()
        email = str(row["email"]).strip().lower()
        if not name or "@" not in email:
            skipped += 1
            continue
        customers.append({"name": name, "email": email, "vip": False})
    for cust in customers:
        cust["tags"] = []
    return customers
