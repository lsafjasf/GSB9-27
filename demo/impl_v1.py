"""demo 插件 v1 实现。"""


def ping(message):
    return f"pong:{message}"


def greet(name, punctuation="!"):
    return f"hello {name}{punctuation}"


def get_user(user_id):
    return {"id": user_id, "name": f"user{user_id}"}


def resize(path, width=100, fmt="png"):
    return {"path": path, "width": width, "fmt": fmt}


def sum2(a, b):
    return a + b


def legacy():
    return "legacy-result"


def upload(path, compress=False):
    return {"path": path, "compress": compress, "size": 42}


def connect(host):
    return f"conn://{host}"
