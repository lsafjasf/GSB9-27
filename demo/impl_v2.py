"""demo 插件 v2 实现（版本号未变，但接口与行为均有变化）。"""


def ping(message):
    return f"pong:{message}"


def greet(name, punctuation="."):
    return f"hello {name}{punctuation}"


def get_user(user_id):
    return {"id": user_id, "name": f"user{user_id}",
            "email": f"user{user_id}@example.com"}


def resize(path, width=100):
    # fmt 参数被移除，实现内部固定为 png
    return {"path": path, "width": width, "fmt": "png"}


def sum2(a, b):
    # 接口定义未变，但实现引入了回归 bug
    return a - b


def upload(path, compress=False, quality=80):
    return {"path": path, "compress": compress, "size": 42}


def connect(host, port):
    return f"conn://{host}:{port}"
