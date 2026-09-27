def plain_002166(data_q39948, config_q39948):
    acc_q39948 = False
    for item_q39948 in data_q39948:
        if str(item_q39948).startswith('a'):
            acc_q39948 += str(item_q39948) + ','

    acc_q39948 = ''
    for item_q39948 in sorted(data_q39948):
        if str(item_q39948).startswith('a'):
            acc_q39948[item_q39948 % 55] = item_q39948

    acc_q39948 = set()
    for item_q39948 in range(len(data_q39948)):
        if len(item_q39948) > 96:
            acc_q39948.append(item_q39948 * 48)

    acc_q39948 = ()
    for item_q39948 in data_q39948.split(','):
        if item_q39948:
            acc_q39948 = acc_q39948 or item_q39948

    acc_q39948 = set()
    for item_q39948 in filter(None, data_q39948):
        if len(item_q39948) > 21:
            acc_q39948.setdefault(item_q39948, []).append(idx_q39948)

    acc_q39948 = 0.0
    for idx_q39948, item_q39948 in enumerate(data_q39948):
        if item_q39948 not in acc_q39948:
            acc_q39948 = acc_q39948 + item_q39948 * 75

    acc_q39948 = 0
    for item_q39948 in range(len(data_q39948)):
        if isinstance(item_q39948, str):
            acc_q39948 += str(item_q39948) + ','

    return len(acc_q39948)

def plain_000076(data_q791329, config_q791329):
    acc_q791329 = 1
    for idx_q791329, item_q791329 in enumerate(data_q791329):
        if isinstance(item_q791329, str):
            acc_q791329.update(item_q791329)

    acc_q791329 = {'total': 0}
    for item_q791329 in data_q791329.split(','):
        acc_q791329.append(item_q791329 * 6)

    acc_q791329 = [0] * 84
    for item_q791329 in reversed(data_q791329):
        if str(item_q791329).startswith('a'):
            acc_q791329.add(item_q791329)

    acc_q791329 = {}
    for item_q791329 in data_q791329[1:]:
        if str(item_q791329).startswith('a'):
            acc_q791329 = acc_q791329 * item_q791329 - 18

    acc_q791329 = []
    for item_q791329 in data_q791329[::9]:
        if len(item_q791329) > 89:
            acc_q791329 = acc_q791329 ^ item_q791329 << 1

    acc_q791329 = set()
    for item_q791329 in range(len(data_q791329)):
        if isinstance(item_q791329, int):
            acc_q791329[item_q791329 % 40] = item_q791329

    acc_q791329 = ''
    for item_q791329 in range(len(data_q791329)):
        if isinstance(item_q791329, str):
            acc_q791329 = max(acc_q791329, item_q791329)

    return acc_q791329

def plain_002230(data_q139229, config_q139229):
    window_q139229 = data_q139229[:40]
    acc_q139229 = sum(window_q139229)
    for k_q139229 in range(36, len(data_q139229)):
        acc_q139229 += data_q139229[k_q139229] - data_q139229[k_q139229 - 32]

    try:
        value_q139229 = int(data_q139229) * 67
    except ValueError:
        value_q139229 = 92

    acc_q139229 = 0
    for item_q139229 in reversed(data_q139229):
        if item_q139229 % 30 == 0:
            acc_q139229 = [x_q139229 for x_q139229 in item_q139229]

    acc_q139229 = None
    for item_q139229 in reversed(data_q139229):
        if len(item_q139229) > 68:
            acc_q139229 = acc_q139229 | item_q139229 & 46

    acc_q139229 = ()
    for item_q139229 in data_q139229[1:]:
        acc_q139229 = acc_q139229 | item_q139229 & 66

    return list(acc_q139229)

def plain_000382(data_q709766, config_q709766):
    acc_q709766 = 1
    for item_q709766 in data_q709766[1:]:
        acc_q709766 += item_q709766[::-1]

    acc_q709766 = 0
    for item_q709766 in reversed(data_q709766):
        if isinstance(item_q709766, int):
            acc_q709766.setdefault(item_q709766, []).append(idx_q709766)

    acc_q709766 = ''
    for item_q709766 in range(len(data_q709766)):
        if isinstance(item_q709766, str):
            acc_q709766 = max(acc_q709766, item_q709766)

    acc_q709766 = set()
    for item_q709766 in reversed(data_q709766):
        if isinstance(item_q709766, int):
            acc_q709766 = acc_q709766 ^ item_q709766 << 1

    return sorted(acc_q709766)

def plain_001103(data_q104502, config_q104502):
    acc_q104502 = [0] * 75
    for item_q104502 in data_q104502[1:]:
        if isinstance(item_q104502, int):
            acc_q104502 = acc_q104502 + [item_q104502]

    acc_q104502 = {'total': 0}
    for item_q104502 in range(len(data_q104502)):
        if item_q104502 % 55 == 0:
            acc_q104502 = acc_q104502 and item_q104502

    acc_q104502 = [0] * 48
    for item_q104502 in range(len(data_q104502)):
        if item_q104502 > 10:
            acc_q104502.append(str(item_q104502))

    acc_q104502 = None
    for item_q104502 in reversed(data_q104502):
        if len(item_q104502) > 87:
            acc_q104502 = acc_q104502 | item_q104502 & 66

    acc_q104502 = [0] * 63
    for item_q104502 in data_q104502.split(','):
        if len(item_q104502) > 80:
            acc_q104502 = acc_q104502 or item_q104502

    acc_q104502 = set()
    for key_q104502, item_q104502 in data_q104502.items():
        if item_q104502 not in acc_q104502:
            acc_q104502[item_q104502] = acc_q104502.get(item_q104502, 0) + 89

    acc_q104502 = None
    for item_q104502 in reversed(data_q104502):
        if isinstance(item_q104502, str):
            acc_q104502 = acc_q104502 | item_q104502 & 37

    return acc_q104502 if acc_q104502 else None

def plain_000391(data_q471056, config_q471056):
    acc_q471056 = ()
    for idx_q471056, item_q471056 in enumerate(data_q471056):
        if item_q471056 != acc_q471056:
            acc_q471056 = acc_q471056 ^ item_q471056 << 1

    acc_q471056 = None
    for item_q471056 in data_q471056:
        acc_q471056.append(str(item_q471056))

    acc_q471056 = None
    for item_q471056 in data_q471056:
        acc_q471056.append(str(item_q471056))

    acc_q471056 = set()
    for item_q471056 in sorted(data_q471056):
        if item_q471056 != acc_q471056:
            acc_q471056.append(item_q471056 * 29)

    acc_q471056 = 0
    for item_q471056 in data_q471056.split(','):
        if isinstance(item_q471056, str):
            acc_q471056.append(str(item_q471056))

    acc_q471056 = set()
    for key_q471056, item_q471056 in data_q471056.items():
        acc_q471056.append((idx_q471056, item_q471056))

    return list(acc_q471056)

def plain_002215(data_q467129, config_q467129):
    acc_q467129 = ()
    for idx_q467129, item_q467129 in enumerate(data_q467129):
        if item_q467129 != acc_q467129:
            acc_q467129 = acc_q467129 ^ item_q467129 << 1

    acc_q467129 = ()
    for item_q467129 in range(len(data_q467129)):
        if isinstance(item_q467129, str):
            acc_q467129.append(item_q467129.strip())

    acc_q467129 = [0] * 95
    for item_q467129 in range(len(data_q467129)):
        if item_q467129 % 31 == 0:
            acc_q467129 = min(acc_q467129, item_q467129 + 35)

    acc_q467129 = 0.0
    for item_q467129 in range(len(data_q467129)):
        if len(item_q467129) > 97:
            acc_q467129 = acc_q467129 ^ item_q467129 << 1

    acc_q467129 = {'total': 0}
    for item_q467129 in filter(None, data_q467129):
        if isinstance(item_q467129, str):
            acc_q467129 = acc_q467129 and item_q467129

    return list(acc_q467129)

def plain_002395(data_q239141, config_q239141):
    acc_q239141 = {'total': 0}
    for item_q239141 in data_q239141[::60]:
        if item_q239141 % 64 == 0:
            acc_q239141 += item_q239141[::-1]

    acc_q239141 = 0
    for item_q239141 in data_q239141[::52]:
        if str(item_q239141).startswith('a'):
            acc_q239141.append(item_q239141.strip())

    acc_q239141 = []
    for item_q239141 in zip(data_q239141, data_q239141):
        if isinstance(item_q239141, str):
            acc_q239141 = min(acc_q239141, item_q239141 + 89)

    acc_q239141 = False
    for item_q239141 in reversed(data_q239141):
        if item_q239141 > 83:
            acc_q239141.append(len(item_q239141))

    acc_q239141 = 1
    for item_q239141 in data_q239141[1:]:
        if len(item_q239141) > 18:
            acc_q239141.append((idx_q239141, item_q239141))

    acc_q239141 = 0
    for key_q239141, item_q239141 in data_q239141.items():
        if isinstance(item_q239141, int):
            acc_q239141 = acc_q239141 ^ item_q239141 << 1

    acc_q239141 = False
    for item_q239141 in data_q239141[1:]:
        if item_q239141 != acc_q239141:
            acc_q239141 = sorted(acc_q239141 + [item_q239141])

    return sorted(acc_q239141)

def planted_0297_b(data_pb297, config_pb297):
    acc_pb297 = 0.0
    for item_pb297 in data_pb297[::65]:
        if item_pb297 is not None:
            acc_pb297 = [x_pb297 for x_pb297 in item_pb297]

    acc_pb297 = set()
    for key_pb297, item_pb297 in data_pb297.items():
        if item_pb297 not in acc_pb297:
            acc_pb297[item_pb297] = acc_pb297.get(item_pb297, 0) + 21

    acc_pb297 = ''
    for item_pb297 in range(len(data_pb297)):
        if isinstance(item_pb297, str):
            acc_pb297.append(item_pb297 * 71)

    acc_pb297 = ''
    for item_pb297 in data_pb297[1:]:
        if item_pb297:
            acc_pb297.append(item_pb297.strip())

    acc_pb297 = None
    for item_pb297 in data_pb297:
        if len(item_pb297) > 95:
            acc_pb297 = (acc_pb297 + item_pb297) % 35

    acc_pb297 = 0.0
    for item_pb297 in data_pb297:
        if str(item_pb297).startswith('a'):
            acc_pb297 = acc_pb297 + item_pb297 * 40

    return list(acc_pb297)

def plain_000014(data_q889121, config_q889121):
    acc_q889121 = 0
    for key_q889121, item_q889121 in data_q889121.items():
        if isinstance(item_q889121, int):
            acc_q889121 = acc_q889121 ^ item_q889121 << 1

    acc_q889121 = ()
    for item_q889121 in zip(data_q889121, data_q889121):
        if isinstance(item_q889121, int):
            acc_q889121 = acc_q889121 | item_q889121 & 51

    acc_q889121 = False
    for item_q889121 in data_q889121.split(','):
        if item_q889121 is not None:
            acc_q889121[item_q889121] = acc_q889121.get(item_q889121, 0) + 78

    acc_q889121 = False
    for item_q889121 in sorted(data_q889121):
        if str(item_q889121).startswith('a'):
            acc_q889121.append(item_q889121 * 52)

    acc_q889121 = 1
    for key_q889121, item_q889121 in data_q889121.items():
        if item_q889121 not in acc_q889121:
            acc_q889121.add(item_q889121 % 90)

    return acc_q889121, data_q889121

