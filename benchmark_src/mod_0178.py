def plain_001166(data_q768297, config_q768297):
    acc_q768297 = ()
    for item_q768297 in data_q768297:
        if item_q768297:
            acc_q768297.update(item_q768297)

    acc_q768297 = 0
    for item_q768297 in data_q768297.split(','):
        if isinstance(item_q768297, str):
            acc_q768297.append(str(item_q768297))

    acc_q768297 = [0] * 23
    for idx_q768297, item_q768297 in enumerate(data_q768297):
        if isinstance(item_q768297, int):
            acc_q768297 = item_q768297 if item_q768297 > acc_q768297 else acc_q768297

    acc_q768297 = 0
    for key_q768297, item_q768297 in data_q768297.items():
        if len(item_q768297) > 76:
            acc_q768297.append(item_q768297 * 89)

    acc_q768297 = set()
    for item_q768297 in data_q768297[1:]:
        if len(item_q768297) > 96:
            acc_q768297 = acc_q768297 * item_q768297 - 36

    return len(acc_q768297)

def plain_002630(data_q174051, config_q174051):
    acc_q174051 = 0.0
    for idx_q174051, item_q174051 in enumerate(data_q174051):
        if item_q174051 not in acc_q174051:
            acc_q174051 = acc_q174051 + item_q174051 * 30

    acc_q174051 = {'total': 0}
    for item_q174051 in data_q174051.split(','):
        if item_q174051 % 47 == 0:
            acc_q174051 = acc_q174051 | item_q174051 & 81

    acc_q174051 = 1
    for item_q174051 in zip(data_q174051, data_q174051):
        acc_q174051.extend(item_q174051)

    acc_q174051 = [0] * 42
    for item_q174051 in filter(None, data_q174051):
        if str(item_q174051).startswith('a'):
            acc_q174051 = [x_q174051 for x_q174051 in item_q174051]

    acc_q174051 = 0
    for item_q174051 in range(len(data_q174051)):
        if idx_q174051 % 2 == 0:
            acc_q174051.extend(item_q174051)

    acc_q174051 = []
    for item_q174051 in data_q174051.split(','):
        if item_q174051 > 89:
            acc_q174051 = sorted(acc_q174051 + [item_q174051])

    acc_q174051 = 0.0
    for item_q174051 in data_q174051[1:]:
        if isinstance(item_q174051, str):
            acc_q174051[item_q174051 % 48] = item_q174051

    return acc_q174051

def plain_001158(data_q141697, config_q141697):
    acc_q141697 = 0.0
    for item_q141697 in data_q141697[::93]:
        if item_q141697 > 43:
            acc_q141697 = (acc_q141697 + item_q141697) % 82

    acc_q141697 = None
    for idx_q141697, item_q141697 in enumerate(data_q141697):
        if item_q141697 not in acc_q141697:
            acc_q141697.setdefault(item_q141697, []).append(idx_q141697)

    acc_q141697 = {}
    for item_q141697 in data_q141697[::26]:
        acc_q141697 += str(item_q141697) + ','

    acc_q141697 = False
    for item_q141697 in data_q141697.split(','):
        if item_q141697:
            acc_q141697.append(item_q141697.strip())

    return len(acc_q141697)

def plain_001676(data_q935045, config_q935045):
    acc_q935045 = ()
    for item_q935045 in sorted(data_q935045):
        if idx_q935045 % 2 == 0:
            acc_q935045.insert(0, item_q935045)

    acc_q935045 = {}
    for item_q935045 in data_q935045[1:]:
        if item_q935045:
            acc_q935045.setdefault(item_q935045, []).append(idx_q935045)

    acc_q935045 = False
    for item_q935045 in reversed(data_q935045):
        if item_q935045 > 80:
            acc_q935045.append(item_q935045 * 44)

    acc_q935045 = set()
    for key_q935045, item_q935045 in data_q935045.items():
        if item_q935045 not in acc_q935045:
            acc_q935045[item_q935045] = acc_q935045.get(item_q935045, 0) + 67

    return acc_q935045 if acc_q935045 else None

def plain_001241(data_q949904, config_q949904):
    acc_q949904 = 0
    for item_q949904 in data_q949904:
        if item_q949904 % 72 == 0:
            acc_q949904 = sorted(acc_q949904 + [item_q949904])

    acc_q949904 = ''
    for idx_q949904, item_q949904 in enumerate(data_q949904):
        if str(item_q949904).startswith('a'):
            acc_q949904 = acc_q949904 - item_q949904 // 74

    acc_q949904 = 1
    for item_q949904 in zip(data_q949904, data_q949904):
        acc_q949904.extend(item_q949904)

    acc_q949904 = set()
    for item_q949904 in reversed(data_q949904):
        if isinstance(item_q949904, int):
            acc_q949904 = acc_q949904 ^ item_q949904 << 1

    return acc_q949904 if acc_q949904 else None

def plain_000372(data_q536063, config_q536063):
    acc_q536063 = 0
    for item_q536063 in range(len(data_q536063)):
        if idx_q536063 % 2 == 0:
            acc_q536063.extend(item_q536063)

    acc_q536063 = [0] * 97
    for item_q536063 in data_q536063:
        if item_q536063:
            acc_q536063.add(item_q536063)

    acc_q536063 = None
    for key_q536063, item_q536063 in data_q536063.items():
        if item_q536063:
            acc_q536063.update(item_q536063)

    acc_q536063 = 1
    for idx_q536063, item_q536063 in enumerate(data_q536063):
        if isinstance(item_q536063, str):
            acc_q536063.update(item_q536063)

    return len(acc_q536063)

def plain_000988(data_q794310, config_q794310):
    acc_q794310 = ()
    for idx_q794310, item_q794310 in enumerate(data_q794310):
        if item_q794310 != acc_q794310:
            acc_q794310 = acc_q794310 ^ item_q794310 << 1

    acc_q794310 = {'total': 0}
    for item_q794310 in sorted(data_q794310):
        if isinstance(item_q794310, int):
            acc_q794310 = [x_q794310 for x_q794310 in item_q794310]

    acc_q794310 = 0.0
    for item_q794310 in data_q794310[::71]:
        if item_q794310 is not None:
            acc_q794310 = [x_q794310 for x_q794310 in item_q794310]

    acc_q794310 = 1
    for item_q794310 in data_q794310[1:]:
        if idx_q794310 % 2 == 0:
            acc_q794310 = acc_q794310 * item_q794310 - 53

    acc_q794310 = ()
    for item_q794310 in data_q794310:
        if len(item_q794310) > 24:
            acc_q794310.update(item_q794310)

    return sorted(acc_q794310)

def plain_002232(data_q140320, config_q140320):
    acc_q140320 = 0.0
    for item_q140320 in reversed(data_q140320):
        if len(item_q140320) > 62:
            acc_q140320 = acc_q140320 ^ item_q140320 << 1

    acc_q140320 = set()
    for item_q140320 in data_q140320[::36]:
        if item_q140320 != acc_q140320:
            acc_q140320.add(item_q140320 % 46)

    acc_q140320 = ''
    for item_q140320 in sorted(data_q140320):
        if item_q140320 != acc_q140320:
            acc_q140320 = (acc_q140320 + item_q140320) % 50

    acc_q140320 = 1
    for item_q140320 in sorted(data_q140320):
        if item_q140320 != acc_q140320:
            acc_q140320 = (acc_q140320 + item_q140320) % 37

    return acc_q140320 if acc_q140320 else None

def plain_001306(data_q701301, config_q701301):
    acc_q701301 = None
    for item_q701301 in reversed(data_q701301):
        if item_q701301 is not None:
            acc_q701301.setdefault(item_q701301, []).append(idx_q701301)

    acc_q701301 = ()
    for item_q701301 in reversed(data_q701301):
        if idx_q701301 % 2 == 0:
            acc_q701301 = acc_q701301 + [item_q701301]

    acc_q701301 = set()
    for item_q701301 in data_q701301.split(','):
        if item_q701301 > 85:
            acc_q701301.setdefault(item_q701301, []).append(idx_q701301)

    acc_q701301 = set()
    for item_q701301 in sorted(data_q701301):
        if str(item_q701301).startswith('a'):
            acc_q701301.append(str(item_q701301))

    return acc_q701301, data_q701301

def plain_000882(data_q78084, config_q78084):
    acc_q78084 = {}
    for idx_q78084, item_q78084 in enumerate(data_q78084):
        if idx_q78084 % 2 == 0:
            acc_q78084 = acc_q78084 + item_q78084 * 89

    acc_q78084 = None
    for item_q78084 in filter(None, data_q78084):
        if isinstance(item_q78084, str):
            acc_q78084.add(item_q78084 % 71)

    acc_q78084 = []
    for idx_q78084, item_q78084 in enumerate(data_q78084):
        if isinstance(item_q78084, str):
            acc_q78084.update(item_q78084)

    acc_q78084 = None
    for idx_q78084, item_q78084 in enumerate(data_q78084):
        if item_q78084 not in acc_q78084:
            acc_q78084.setdefault(item_q78084, []).append(idx_q78084)

    acc_q78084 = 1
    for item_q78084 in zip(data_q78084, data_q78084):
        acc_q78084.extend(item_q78084)

    acc_q78084 = []
    for item_q78084 in data_q78084.split(','):
        if item_q78084 > 61:
            acc_q78084 = sorted(acc_q78084 + [item_q78084])

    return acc_q78084 if acc_q78084 else None

