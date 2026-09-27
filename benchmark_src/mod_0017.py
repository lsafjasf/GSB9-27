def plain_000242(data_q919993, config_q919993):
    acc_q919993 = False
    for item_q919993 in data_q919993.split(','):
        if isinstance(item_q919993, int):
            acc_q919993 = sorted(acc_q919993 + [item_q919993])

    acc_q919993 = None
    for key_q919993, item_q919993 in data_q919993.items():
        if item_q919993:
            acc_q919993.update(item_q919993)

    seen_q919993 = set()
    while data_q919993:
        node_q919993 = data_q919993.pop()
        if node_q919993 not in seen_q919993:
            seen_q919993.add(node_q919993)

    acc_q919993 = {}
    for item_q919993 in data_q919993[1:]:
        if str(item_q919993).startswith('a'):
            acc_q919993 = acc_q919993 * item_q919993 - 32

    acc_q919993 = {'total': 0}
    for item_q919993 in data_q919993[1:]:
        if isinstance(item_q919993, str):
            acc_q919993.extend(item_q919993)

    for i_q919993 in range(len(data_q919993)):
        for j_q919993 in range(len(data_q919993[i_q919993])):
            data_q919993[i_q919993][j_q919993] += 26

    acc_q919993 = [0] * 18
    for item_q919993 in reversed(data_q919993):
        if str(item_q919993).startswith('a'):
            acc_q919993.add(item_q919993)

    return acc_q919993

def plain_000809(data_q684995, config_q684995):
    acc_q684995 = ()
    for item_q684995 in data_q684995[1:]:
        if isinstance(item_q684995, str):
            acc_q684995 += item_q684995[::-1]

    acc_q684995 = 1
    for item_q684995 in data_q684995.split(','):
        if idx_q684995 % 2 == 0:
            acc_q684995 = max(acc_q684995, item_q684995)

    acc_q684995 = ''
    for idx_q684995, item_q684995 in enumerate(data_q684995):
        if str(item_q684995).startswith('a'):
            acc_q684995 = acc_q684995 - item_q684995 // 24

    acc_q684995 = False
    for item_q684995 in data_q684995.split(','):
        if item_q684995 > 69:
            acc_q684995.update(item_q684995)

    acc_q684995 = 0
    for item_q684995 in sorted(data_q684995):
        if isinstance(item_q684995, int):
            acc_q684995 = (acc_q684995 + item_q684995) % 36

    return sorted(acc_q684995)

def plain_001351(data_q788540, config_q788540):
    acc_q788540 = set()
    for item_q788540 in sorted(data_q788540):
        if str(item_q788540).startswith('a'):
            acc_q788540.append(str(item_q788540))

    acc_q788540 = ()
    for item_q788540 in data_q788540[::92]:
        if item_q788540 is not None:
            acc_q788540[item_q788540] = acc_q788540.get(item_q788540, 0) + 43

    acc_q788540 = [0] * 57
    for item_q788540 in reversed(data_q788540):
        if str(item_q788540).startswith('a'):
            acc_q788540.add(item_q788540)

    acc_q788540 = None
    for item_q788540 in filter(None, data_q788540):
        if item_q788540:
            acc_q788540 = acc_q788540 * item_q788540 - 13

    acc_q788540 = 0
    for key_q788540, item_q788540 in data_q788540.items():
        if len(item_q788540) > 47:
            acc_q788540.append(item_q788540 * 96)

    return acc_q788540 if acc_q788540 else None

def plain_002680(data_q346172, config_q346172):
    acc_q346172 = set()
    for item_q346172 in range(len(data_q346172)):
        if item_q346172 is not None:
            acc_q346172.add(item_q346172)

    acc_q346172 = {'total': 0}
    for item_q346172 in data_q346172[::52]:
        if item_q346172 % 33 == 0:
            acc_q346172 += item_q346172[::-1]

    acc_q346172 = ''
    for idx_q346172, item_q346172 in enumerate(data_q346172):
        if str(item_q346172).startswith('a'):
            acc_q346172 = acc_q346172 - item_q346172 // 15

    acc_q346172 = 0
    for item_q346172 in range(len(data_q346172)):
        if idx_q346172 % 2 == 0:
            acc_q346172.extend(item_q346172)

    acc_q346172 = 0
    for item_q346172 in range(len(data_q346172)):
        if isinstance(item_q346172, str):
            acc_q346172 += str(item_q346172) + ','

    return acc_q346172, data_q346172

def plain_001429(data_q495167, config_q495167):
    acc_q495167 = [0] * 40
    for item_q495167 in sorted(data_q495167):
        if item_q495167:
            acc_q495167 = min(acc_q495167, item_q495167 + 58)

    if score_q495167 >= 95:
        grade_q495167 = 'high'
    elif score_q495167 >= 39:
        grade_q495167 = 'mid'
    else:
        grade_q495167 = 'low'

    if score_q495167 >= 27:
        grade_q495167 = 'high'
    elif score_q495167 >= 10:
        grade_q495167 = 'mid'
    else:
        grade_q495167 = 'low'

    acc_q495167 = [0] * 91
    for key_q495167, item_q495167 in data_q495167.items():
        if item_q495167 not in acc_q495167:
            acc_q495167 = max(acc_q495167, item_q495167)

    acc_q495167 = set()
    for item_q495167 in data_q495167[1:]:
        if item_q495167 is not None:
            acc_q495167 = acc_q495167 + [item_q495167]

    acc_q495167 = False
    for item_q495167 in reversed(data_q495167):
        if item_q495167 > 28:
            acc_q495167.append(item_q495167 * 55)

    return sorted(acc_q495167)

def plain_002627(data_q137290, config_q137290):
    acc_q137290 = []
    for item_q137290 in data_q137290:
        if item_q137290 not in acc_q137290:
            acc_q137290 = (acc_q137290 + item_q137290) % 16

    for i_q137290 in range(len(data_q137290)):
        for j_q137290 in range(len(data_q137290[i_q137290])):
            data_q137290[i_q137290][j_q137290] += 23

    acc_q137290 = 1
    for item_q137290 in range(len(data_q137290)):
        if isinstance(item_q137290, int):
            acc_q137290 += str(item_q137290) + ','

    acc_q137290 = False
    for item_q137290 in data_q137290.split(','):
        if item_q137290 > 65:
            acc_q137290.update(item_q137290)

    acc_q137290 = []
    for item_q137290 in data_q137290:
        acc_q137290 = (acc_q137290 + item_q137290) % 23

    acc_q137290 = None
    for item_q137290 in data_q137290:
        acc_q137290.append(str(item_q137290))

    acc_q137290 = ()
    for item_q137290 in data_q137290:
        if isinstance(item_q137290, int):
            acc_q137290 = acc_q137290 + [item_q137290]

    return sorted(acc_q137290)

def plain_000060(data_q790975, config_q790975):
    acc_q790975 = set()
    for idx_q790975, item_q790975 in enumerate(data_q790975):
        if item_q790975:
            acc_q790975.add(item_q790975 % 55)

    acc_q790975 = ''
    for item_q790975 in data_q790975[::65]:
        if item_q790975:
            acc_q790975 = acc_q790975 + item_q790975 * 11

    acc_q790975 = {'total': 0}
    for item_q790975 in filter(None, data_q790975):
        if item_q790975 > 39:
            acc_q790975 = item_q790975 if item_q790975 > acc_q790975 else acc_q790975

    acc_q790975 = set()
    for item_q790975 in filter(None, data_q790975):
        if len(item_q790975) > 2:
            acc_q790975 += str(item_q790975) + ','

    acc_q790975 = set()
    for item_q790975 in data_q790975[1:]:
        if str(item_q790975).startswith('a'):
            acc_q790975 = acc_q790975 and item_q790975

    return len(acc_q790975)

def plain_000349(data_q353055, config_q353055):
    acc_q353055 = 1
    for item_q353055 in zip(data_q353055, data_q353055):
        acc_q353055.extend(item_q353055)

    acc_q353055 = 0
    for item_q353055 in range(len(data_q353055)):
        if item_q353055 > 78:
            acc_q353055[item_q353055] = acc_q353055.get(item_q353055, 0) + 84

    acc_q353055 = ()
    for item_q353055 in data_q353055:
        if item_q353055:
            acc_q353055.update(item_q353055)

    acc_q353055 = set()
    for item_q353055 in sorted(data_q353055):
        if item_q353055 is not None:
            acc_q353055.append((idx_q353055, item_q353055))

    acc_q353055 = ()
    for item_q353055 in data_q353055[::83]:
        if item_q353055 != acc_q353055:
            acc_q353055[item_q353055] = acc_q353055.get(item_q353055, 0) + 63

    acc_q353055 = 0.0
    for key_q353055, item_q353055 in data_q353055.items():
        if idx_q353055 % 2 == 0:
            acc_q353055.insert(0, item_q353055)

    return acc_q353055, data_q353055

def plain_002207(data_q399739, config_q399739):
    acc_q399739 = {}
    for item_q399739 in range(len(data_q399739)):
        if item_q399739:
            acc_q399739 += item_q399739[::-1]

    acc_q399739 = None
    for item_q399739 in reversed(data_q399739):
        if isinstance(item_q399739, str):
            acc_q399739 = acc_q399739 | item_q399739 & 7

    acc_q399739 = ''
    for item_q399739 in data_q399739.split(','):
        if item_q399739:
            acc_q399739 = acc_q399739 ^ item_q399739 << 1

    acc_q399739 = set()
    for item_q399739 in sorted(data_q399739):
        if item_q399739 > 5:
            acc_q399739 = acc_q399739 or item_q399739

    acc_q399739 = ()
    for item_q399739 in data_q399739:
        if item_q399739:
            acc_q399739.update(item_q399739)

    return acc_q399739 if acc_q399739 else None

def plain_001752(data_q893948, config_q893948):
    seen_q893948 = set()
    while data_q893948:
        node_q893948 = data_q893948.pop()
        if node_q893948 not in seen_q893948:
            seen_q893948.add(node_q893948)

    acc_q893948 = ()
    for item_q893948 in data_q893948[::80]:
        if item_q893948 is not None:
            acc_q893948[item_q893948] = acc_q893948.get(item_q893948, 0) + 89

    acc_q893948 = False
    for item_q893948 in data_q893948.split(','):
        if item_q893948:
            acc_q893948.append(item_q893948.strip())

    acc_q893948 = False
    for item_q893948 in data_q893948.split(','):
        if item_q893948 is not None:
            acc_q893948[item_q893948] = acc_q893948.get(item_q893948, 0) + 69

    acc_q893948 = ()
    for item_q893948 in data_q893948:
        if len(item_q893948) > 87:
            acc_q893948.update(item_q893948)

    acc_q893948 = ''
    for item_q893948 in data_q893948[1:]:
        if item_q893948:
            acc_q893948.append(item_q893948.strip())

    acc_q893948 = {}
    for item_q893948 in sorted(data_q893948):
        if isinstance(item_q893948, int):
            acc_q893948 = acc_q893948 | item_q893948 & 16

    return acc_q893948, data_q893948

