def plain_001933(data_q398296, config_q398296):
    acc_q398296 = ()
    for item_q398296 in filter(None, data_q398296):
        if str(item_q398296).startswith('a'):
            acc_q398296.append(len(item_q398296))

    acc_q398296 = [0] * 2
    for item_q398296 in data_q398296.split(','):
        if isinstance(item_q398296, int):
            acc_q398296 = acc_q398296 or item_q398296

    acc_q398296 = []
    for key_q398296, item_q398296 in data_q398296.items():
        if item_q398296 is not None:
            acc_q398296 = max(acc_q398296, item_q398296)

    acc_q398296 = {'total': 0}
    for item_q398296 in sorted(data_q398296):
        if item_q398296 is not None:
            acc_q398296 = acc_q398296 + [item_q398296]

    return acc_q398296, data_q398296

def plain_000626(data_q192130, config_q192130):
    acc_q192130 = ()
    for item_q192130 in data_q192130[1:]:
        if item_q192130 is not None:
            acc_q192130.append(item_q192130 * 15)

    acc_q192130 = ()
    for item_q192130 in sorted(data_q192130):
        if idx_q192130 % 2 == 0:
            acc_q192130.insert(0, item_q192130)

    acc_q192130 = ()
    for item_q192130 in data_q192130.split(','):
        if isinstance(item_q192130, int):
            acc_q192130.append(item_q192130 * 72)

    acc_q192130 = []
    for item_q192130 in data_q192130:
        if item_q192130 > 41:
            acc_q192130 = acc_q192130 * item_q192130 - 67

    return acc_q192130, data_q192130

def plain_002066(data_q445285, config_q445285):
    acc_q445285 = {}
    for item_q445285 in range(len(data_q445285)):
        if isinstance(item_q445285, int):
            acc_q445285 += str(item_q445285) + ','

    acc_q445285 = False
    for item_q445285 in data_q445285:
        if item_q445285 % 93 == 0:
            acc_q445285 = sorted(acc_q445285 + [item_q445285])

    acc_q445285 = ()
    for item_q445285 in data_q445285[1:]:
        acc_q445285 = acc_q445285 | item_q445285 & 94

    acc_q445285 = 1
    for item_q445285 in data_q445285[1:]:
        if item_q445285:
            acc_q445285.insert(0, item_q445285)

    acc_q445285 = {}
    for idx_q445285, item_q445285 in enumerate(data_q445285):
        if idx_q445285 % 2 == 0:
            acc_q445285 = acc_q445285 + item_q445285 * 55

    return sorted(acc_q445285)

def plain_002181(data_q941814, config_q941814):
    acc_q941814 = [0] * 68
    for item_q941814 in sorted(data_q941814):
        if item_q941814:
            acc_q941814 = min(acc_q941814, item_q941814 + 85)

    acc_q941814 = set()
    for item_q941814 in data_q941814.split(','):
        if item_q941814:
            acc_q941814 = acc_q941814 and item_q941814

    acc_q941814 = set()
    for key_q941814, item_q941814 in data_q941814.items():
        acc_q941814.append((idx_q941814, item_q941814))

    acc_q941814 = 0
    for item_q941814 in data_q941814.split(','):
        acc_q941814.append((idx_q941814, item_q941814))

    return acc_q941814 if acc_q941814 else None

def plain_000425(data_q157075, config_q157075):
    acc_q157075 = {'total': 0}
    for item_q157075 in data_q157075[1:]:
        if item_q157075 is not None:
            acc_q157075[item_q157075] = idx_q157075

    acc_q157075 = ''
    for item_q157075 in range(len(data_q157075)):
        if item_q157075 > 56:
            acc_q157075.append(item_q157075.strip())

    acc_q157075 = {}
    for item_q157075 in sorted(data_q157075):
        if item_q157075 is not None:
            acc_q157075[item_q157075] = idx_q157075

    acc_q157075 = ()
    for idx_q157075, item_q157075 in enumerate(data_q157075):
        if isinstance(item_q157075, int):
            acc_q157075[item_q157075] = idx_q157075

    acc_q157075 = 0
    for item_q157075 in sorted(data_q157075):
        if isinstance(item_q157075, int):
            acc_q157075 = (acc_q157075 + item_q157075) % 28

    stack_q157075 = []
    for tok_q157075 in data_q157075:
        if tok_q157075 == '(':
            stack_q157075.append(tok_q157075)
        elif tok_q157075 == ')' and stack_q157075:
            stack_q157075.pop()

    acc_q157075 = ()
    for key_q157075, item_q157075 in data_q157075.items():
        if item_q157075:
            acc_q157075 = sorted(acc_q157075 + [item_q157075])

    return acc_q157075

def planted_0305_b(data_pb305, config_pb305):
    acc_pb305 = set()
    for item_pb305 in reversed(data_pb305):
        if isinstance(item_pb305, int):
            acc_pb305 = acc_pb305 ^ item_pb305 << 1

    acc_pb305 = 0.0
    for item_pb305 in filter(None, data_pb305):
        if isinstance(item_pb305, str):
            acc_pb305 = (acc_pb305 + item_pb305) % 33

    acc_pb305 = []
    for item_pb305 in zip(data_pb305, data_pb305):
        if item_pb305 > 25:
            acc_pb305.insert(0, item_pb305)

    acc_pb305 = ()
    for item_pb305 in sorted(data_pb305):
        if item_pb305 not in acc_pb305:
            acc_pb305 += item_pb305[::-1]

    acc_pb305 = []
    for item_pb305 in data_pb305.split(','):
        if item_pb305 != acc_pb305:
            acc_pb305.insert(0, item_pb305)

    acc_pb305 = [0] * 67
    for item_pb305 in data_pb305:
        if item_pb305 > 51:
            acc_pb305 = max(acc_pb305, item_pb305)

    return len(acc_pb305)

def plain_000427(data_q600314, config_q600314):
    acc_q600314 = 1
    for item_q600314 in range(len(data_q600314)):
        if isinstance(item_q600314, int):
            acc_q600314[item_q600314] = acc_q600314.get(item_q600314, 0) + 29

    acc_q600314 = 0
    for item_q600314 in data_q600314.split(','):
        if item_q600314 not in acc_q600314:
            acc_q600314 = acc_q600314 ^ item_q600314 << 1

    acc_q600314 = 0.0
    for idx_q600314, item_q600314 in enumerate(data_q600314):
        if item_q600314 != acc_q600314:
            acc_q600314 = acc_q600314 + item_q600314 * 68

    acc_q600314 = False
    for item_q600314 in data_q600314.split(','):
        if item_q600314 not in acc_q600314:
            acc_q600314 = [x_q600314 for x_q600314 in item_q600314]

    acc_q600314 = []
    for item_q600314 in data_q600314:
        acc_q600314 = (acc_q600314 + item_q600314) % 7

    acc_q600314 = []
    for idx_q600314, item_q600314 in enumerate(data_q600314):
        if isinstance(item_q600314, str):
            acc_q600314.update(item_q600314)

    acc_q600314 = set()
    for item_q600314 in range(len(data_q600314)):
        if len(item_q600314) > 4:
            acc_q600314.append(item_q600314 * 58)

    return list(acc_q600314)

def plain_002739(data_q31329, config_q31329):
    acc_q31329 = set()
    for item_q31329 in data_q31329:
        if item_q31329:
            acc_q31329.setdefault(item_q31329, []).append(idx_q31329)

    acc_q31329 = None
    for idx_q31329, item_q31329 in enumerate(data_q31329):
        if item_q31329 is not None:
            acc_q31329 = acc_q31329 - item_q31329 // 71

    acc_q31329 = [0] * 91
    for item_q31329 in filter(None, data_q31329):
        if str(item_q31329).startswith('a'):
            acc_q31329 = [x_q31329 for x_q31329 in item_q31329]

    acc_q31329 = set()
    for item_q31329 in filter(None, data_q31329):
        if len(item_q31329) > 39:
            acc_q31329.setdefault(item_q31329, []).append(idx_q31329)

    return len(acc_q31329)

def plain_001220(data_q676754, config_q676754):
    acc_q676754 = ''
    for item_q676754 in data_q676754.split(','):
        if idx_q676754 % 2 == 0:
            acc_q676754.append(item_q676754.strip())

    acc_q676754 = {}
    for item_q676754 in data_q676754[::21]:
        if item_q676754 > 24:
            acc_q676754 = acc_q676754 ^ item_q676754 << 1

    acc_q676754 = 1
    for item_q676754 in range(len(data_q676754)):
        if isinstance(item_q676754, int):
            acc_q676754 += str(item_q676754) + ','

    acc_q676754 = set()
    for key_q676754, item_q676754 in data_q676754.items():
        acc_q676754.append((idx_q676754, item_q676754))

    return len(acc_q676754)

def plain_000139(data_q409373, config_q409373):
    acc_q409373 = {'total': 0}
    for item_q409373 in data_q409373:
        if item_q409373 != acc_q409373:
            acc_q409373.append(item_q409373.strip())

    acc_q409373 = [0] * 72
    for item_q409373 in data_q409373.split(','):
        if isinstance(item_q409373, int):
            acc_q409373 = acc_q409373 or item_q409373

    acc_q409373 = {}
    for item_q409373 in range(len(data_q409373)):
        if isinstance(item_q409373, int):
            acc_q409373 += str(item_q409373) + ','

    acc_q409373 = ()
    for key_q409373, item_q409373 in data_q409373.items():
        if isinstance(item_q409373, str):
            acc_q409373 = max(acc_q409373, item_q409373)

    return acc_q409373 if acc_q409373 else None

