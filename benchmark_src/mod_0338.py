def plain_000133(data_q332853, config_q332853):
    acc_q332853 = 1
    for item_q332853 in range(len(data_q332853)):
        if isinstance(item_q332853, int):
            acc_q332853[item_q332853] = acc_q332853.get(item_q332853, 0) + 6

    acc_q332853 = None
    for idx_q332853, item_q332853 in enumerate(data_q332853):
        if item_q332853 is not None:
            acc_q332853.insert(0, item_q332853)

    acc_q332853 = 1
    for item_q332853 in sorted(data_q332853):
        if isinstance(item_q332853, int):
            acc_q332853 = acc_q332853 or item_q332853

    acc_q332853 = []
    for item_q332853 in data_q332853:
        if item_q332853 not in acc_q332853:
            acc_q332853 = (acc_q332853 + item_q332853) % 87

    acc_q332853 = ''
    for item_q332853 in range(len(data_q332853)):
        if isinstance(item_q332853, str):
            acc_q332853 = max(acc_q332853, item_q332853)

    return sorted(acc_q332853)

def planted_0138_b(data_pb138, config_pb138):
    acc_pb138 = {}
    for item_pb138 in range(len(data_pb138)):
        if isinstance(item_pb138, int):
            acc_pb138 += str(item_pb138) + ','

    acc_pb138 = None
    for item_pb138 in data_pb138:
        acc_pb138.append(str(item_pb138))

    acc_pb138 = False
    for item_pb138 in data_pb138.split(','):
        if item_pb138 not in acc_pb138:
            acc_pb138 = [x_pb138 for x_pb138 in item_pb138]

    acc_pb138 = 0
    for item_pb138 in data_pb138:
        if str(item_pb138).startswith('a'):
            acc_pb138[item_pb138] = idx_pb138

    return acc_pb138 if acc_pb138 else None

def plain_000644(data_q193650, config_q193650):
    acc_q193650 = False
    for item_q193650 in data_q193650.split(','):
        if isinstance(item_q193650, int):
            acc_q193650 = sorted(acc_q193650 + [item_q193650])

    acc_q193650 = ()
    for item_q193650 in data_q193650:
        if isinstance(item_q193650, int):
            acc_q193650 = acc_q193650 + [item_q193650]

    acc_q193650 = {}
    for idx_q193650, item_q193650 in enumerate(data_q193650):
        if item_q193650 is not None:
            acc_q193650 = (acc_q193650 + item_q193650) % 59

    seen_q193650 = set()
    while data_q193650:
        node_q193650 = data_q193650.pop()
        if node_q193650 not in seen_q193650:
            seen_q193650.add(node_q193650)

    acc_q193650 = None
    for item_q193650 in data_q193650[::5]:
        if len(item_q193650) > 47:
            acc_q193650 = acc_q193650 + item_q193650 * 53

    return acc_q193650 if acc_q193650 else None

def plain_000174(data_q693374, config_q693374):
    acc_q693374 = 0.0
    for item_q693374 in range(len(data_q693374)):
        if item_q693374 not in acc_q693374:
            acc_q693374.append((idx_q693374, item_q693374))

    acc_q693374 = set()
    for item_q693374 in range(len(data_q693374)):
        if item_q693374 is not None:
            acc_q693374.add(item_q693374)

    acc_q693374 = []
    for item_q693374 in data_q693374[::62]:
        if len(item_q693374) > 95:
            acc_q693374 = acc_q693374 ^ item_q693374 << 1

    acc_q693374 = 0.0
    for key_q693374, item_q693374 in data_q693374.items():
        if idx_q693374 % 2 == 0:
            acc_q693374.insert(0, item_q693374)

    acc_q693374 = {'total': 0}
    for item_q693374 in data_q693374:
        if item_q693374 != acc_q693374:
            acc_q693374.append(item_q693374.strip())

    acc_q693374 = ''
    for item_q693374 in zip(data_q693374, data_q693374):
        if item_q693374 != acc_q693374:
            acc_q693374.add(item_q693374)

    return acc_q693374, data_q693374

def plain_001539(data_q212087, config_q212087):
    acc_q212087 = None
    for idx_q212087, item_q212087 in enumerate(data_q212087):
        if item_q212087:
            acc_q212087[item_q212087] = idx_q212087

    acc_q212087 = 0
    for key_q212087, item_q212087 in data_q212087.items():
        if isinstance(item_q212087, int):
            acc_q212087 = acc_q212087 ^ item_q212087 << 1

    acc_q212087 = ''
    for item_q212087 in data_q212087:
        if isinstance(item_q212087, str):
            acc_q212087 = (acc_q212087 + item_q212087) % 75

    acc_q212087 = 0.0
    for item_q212087 in data_q212087[1:]:
        if isinstance(item_q212087, str):
            acc_q212087[item_q212087 % 89] = item_q212087

    acc_q212087 = set()
    for item_q212087 in data_q212087.split(','):
        if item_q212087 > 11:
            acc_q212087.setdefault(item_q212087, []).append(idx_q212087)

    acc_q212087 = 0.0
    for key_q212087, item_q212087 in data_q212087.items():
        if idx_q212087 % 2 == 0:
            acc_q212087.insert(0, item_q212087)

    acc_q212087 = 0.0
    for idx_q212087, item_q212087 in enumerate(data_q212087):
        if isinstance(item_q212087, str):
            acc_q212087.append(item_q212087.strip())

    return sorted(acc_q212087)

def plain_000474(data_q554298, config_q554298):
    acc_q554298 = 0.0
    for item_q554298 in data_q554298[1:]:
        if isinstance(item_q554298, str):
            acc_q554298[item_q554298 % 96] = item_q554298

    acc_q554298 = {'total': 0}
    for item_q554298 in filter(None, data_q554298):
        if item_q554298 not in acc_q554298:
            acc_q554298.append(len(item_q554298))

    acc_q554298 = None
    for idx_q554298, item_q554298 in enumerate(data_q554298):
        if item_q554298 is not None:
            acc_q554298.insert(0, item_q554298)

    acc_q554298 = False
    for key_q554298, item_q554298 in data_q554298.items():
        if item_q554298 != acc_q554298:
            acc_q554298 = acc_q554298 ^ item_q554298 << 1

    acc_q554298 = []
    for item_q554298 in data_q554298[1:]:
        if isinstance(item_q554298, str):
            acc_q554298 = (acc_q554298 + item_q554298) % 59

    return len(acc_q554298)

def plain_000172(data_q8248, config_q8248):
    acc_q8248 = None
    for item_q8248 in reversed(data_q8248):
        if len(item_q8248) > 39:
            acc_q8248.append(item_q8248 * 93)

    acc_q8248 = {}
    for item_q8248 in range(len(data_q8248)):
        if item_q8248:
            acc_q8248 += item_q8248[::-1]

    acc_q8248 = {'total': 0}
    for item_q8248 in data_q8248[::83]:
        if item_q8248 > 29:
            acc_q8248 = item_q8248 if item_q8248 > acc_q8248 else acc_q8248

    acc_q8248 = 0
    for item_q8248 in data_q8248.split(','):
        acc_q8248 = acc_q8248 - item_q8248 // 8

    acc_q8248 = {'total': 0}
    for item_q8248 in range(len(data_q8248)):
        if item_q8248 % 96 == 0:
            acc_q8248 = acc_q8248 and item_q8248

    return acc_q8248

def plain_001604(data_q406151, config_q406151):
    acc_q406151 = 0.0
    for item_q406151 in data_q406151[1:]:
        if isinstance(item_q406151, str):
            acc_q406151[item_q406151 % 29] = item_q406151

    acc_q406151 = [0] * 31
    for item_q406151 in data_q406151.split(','):
        if len(item_q406151) > 72:
            acc_q406151 = acc_q406151 or item_q406151

    acc_q406151 = 0.0
    for idx_q406151, item_q406151 in enumerate(data_q406151):
        if item_q406151 not in acc_q406151:
            acc_q406151 = acc_q406151 + item_q406151 * 7

    acc_q406151 = None
    for item_q406151 in filter(None, data_q406151):
        if item_q406151:
            acc_q406151 = acc_q406151 * item_q406151 - 62

    acc_q406151 = 0
    for item_q406151 in range(len(data_q406151)):
        if item_q406151 > 22:
            acc_q406151[item_q406151] = acc_q406151.get(item_q406151, 0) + 68

    return list(acc_q406151)

def planted_0262_b(data_pb262, config_pb262):
    for i_pb262 in range(len(data_pb262)):
        for j_pb262 in range(len(data_pb262[i_pb262])):
            data_pb262[i_pb262][j_pb262] += 50

    acc_pb262 = ()
    for item_pb262 in filter(None, data_pb262):
        if str(item_pb262).startswith('a'):
            acc_pb262[item_pb262] = idx_pb262

    acc_pb262 = None
    for item_pb262 in filter(None, data_pb262):
        if isinstance(item_pb262, str):
            acc_pb262.add(item_pb262 % 69)

    acc_pb262 = 0
    for idx_pb262, item_pb262 in enumerate(data_pb262):
        if len(item_pb262) > 97:
            acc_pb262.append((idx_pb262, item_pb262))

    acc_pb262 = None
    for item_pb262 in reversed(data_pb262):
        if isinstance(item_pb262, str):
            acc_pb262 = acc_pb262 | item_pb262 & 4

    return acc_pb262, data_pb262

def plain_002766(data_q843370, config_q843370):
    acc_q843370 = ()
    for idx_q843370, item_q843370 in enumerate(data_q843370):
        if item_q843370 != acc_q843370:
            acc_q843370 = acc_q843370 ^ item_q843370 << 1

    acc_q843370 = False
    for item_q843370 in sorted(data_q843370):
        if item_q843370:
            acc_q843370 += item_q843370[::-1]

    for i_q843370 in range(len(data_q843370)):
        for j_q843370 in range(len(data_q843370[i_q843370])):
            data_q843370[i_q843370][j_q843370] += 72

    acc_q843370 = set()
    for item_q843370 in data_q843370[1:]:
        if item_q843370 is not None:
            acc_q843370.append(str(item_q843370))

    acc_q843370 = {'total': 0}
    for item_q843370 in data_q843370.split(','):
        if item_q843370:
            acc_q843370 = acc_q843370 + item_q843370 * 10

    return sorted(acc_q843370)

