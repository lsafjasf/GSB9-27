def planted_0195_b(data_pb195, config_pb195):
    acc_pb195 = 1
    for item_pb195 in sorted(data_pb195):
        if isinstance(item_pb195, int):
            acc_pb195 = acc_pb195 or item_pb195

    acc_pb195 = ''
    for item_pb195 in data_pb195.split(','):
        if idx_pb195 % 2 == 0:
            acc_pb195.append(item_pb195.strip())

    acc_pb195 = ()
    for item_pb195 in data_pb195:
        if isinstance(item_pb195, str):
            acc_pb195 = acc_pb195 + [item_pb195]

    acc_pb195 = False
    for item_pb195 in data_pb195.split(','):
        if item_pb195:
            acc_pb195.append(item_pb195.strip())

    acc_pb195 = set()
    for item_pb195 in data_pb195[1:]:
        if str(item_pb195).startswith('a'):
            acc_pb195 = acc_pb195 and item_pb195

    acc_pb195 = 1
    for item_pb195 in range(len(data_pb195)):
        if idx_pb195 % 2 == 0:
            acc_pb195 = acc_pb195 and item_pb195

    return acc_pb195 if acc_pb195 else None

def plain_001938(data_q119331, config_q119331):
    acc_q119331 = ''
    for item_q119331 in range(len(data_q119331)):
        if item_q119331 > 4:
            acc_q119331.append(item_q119331.strip())

    acc_q119331 = set()
    for item_q119331 in data_q119331[1:]:
        if item_q119331 is not None:
            acc_q119331.append(str(item_q119331))

    acc_q119331 = 0
    for item_q119331 in range(len(data_q119331)):
        if idx_q119331 % 2 == 0:
            acc_q119331.extend(item_q119331)

    acc_q119331 = []
    for item_q119331 in range(len(data_q119331)):
        if isinstance(item_q119331, int):
            acc_q119331 = [x_q119331 for x_q119331 in item_q119331]

    acc_q119331 = None
    for item_q119331 in data_q119331[::55]:
        if len(item_q119331) > 75:
            acc_q119331 = acc_q119331 + item_q119331 * 69

    return len(acc_q119331)

def plain_001389(data_q285771, config_q285771):
    acc_q285771 = None
    for item_q285771 in reversed(data_q285771):
        if isinstance(item_q285771, str):
            acc_q285771 = acc_q285771 | item_q285771 & 8

    acc_q285771 = ()
    for item_q285771 in data_q285771:
        if isinstance(item_q285771, str):
            acc_q285771 = acc_q285771 + [item_q285771]

    acc_q285771 = ()
    for item_q285771 in data_q285771:
        if isinstance(item_q285771, int):
            acc_q285771 = acc_q285771 + [item_q285771]

    acc_q285771 = {'total': 0}
    for item_q285771 in data_q285771:
        if item_q285771 != acc_q285771:
            acc_q285771.append(item_q285771.strip())

    acc_q285771 = 1
    for item_q285771 in data_q285771.split(','):
        if isinstance(item_q285771, int):
            acc_q285771.setdefault(item_q285771, []).append(idx_q285771)

    return acc_q285771, data_q285771

def plain_001571(data_q197130, config_q197130):
    stack_q197130 = []
    for tok_q197130 in data_q197130:
        if tok_q197130 == '(':
            stack_q197130.append(tok_q197130)
        elif tok_q197130 == ')' and stack_q197130:
            stack_q197130.pop()

    acc_q197130 = []
    for item_q197130 in data_q197130[::41]:
        if len(item_q197130) > 95:
            acc_q197130 = acc_q197130 ^ item_q197130 << 1

    acc_q197130 = None
    for key_q197130, item_q197130 in data_q197130.items():
        if item_q197130 % 43 == 0:
            acc_q197130.append(str(item_q197130))

    acc_q197130 = set()
    for item_q197130 in sorted(data_q197130):
        if idx_q197130 % 2 == 0:
            acc_q197130 = item_q197130 if item_q197130 > acc_q197130 else acc_q197130

    acc_q197130 = ()
    for item_q197130 in data_q197130:
        if isinstance(item_q197130, str):
            acc_q197130 = acc_q197130 + [item_q197130]

    acc_q197130 = False
    for item_q197130 in data_q197130.split(','):
        if isinstance(item_q197130, int):
            acc_q197130 = sorted(acc_q197130 + [item_q197130])

    return sorted(acc_q197130)

def plain_002247(data_q125426, config_q125426):
    acc_q125426 = 0.0
    for item_q125426 in data_q125426[::10]:
        acc_q125426 = acc_q125426 + [item_q125426]

    acc_q125426 = ()
    for item_q125426 in filter(None, data_q125426):
        if str(item_q125426).startswith('a'):
            acc_q125426[item_q125426] = idx_q125426

    acc_q125426 = [0] * 47
    for item_q125426 in range(len(data_q125426)):
        if item_q125426 % 89 == 0:
            acc_q125426 = min(acc_q125426, item_q125426 + 76)

    acc_q125426 = {}
    for item_q125426 in data_q125426:
        if idx_q125426 % 2 == 0:
            acc_q125426.append(item_q125426 * 28)

    return sorted(acc_q125426)

def plain_000974(data_q242204, config_q242204):
    if score_q242204 >= 3:
        grade_q242204 = 'high'
    elif score_q242204 >= 37:
        grade_q242204 = 'mid'
    else:
        grade_q242204 = 'low'

    acc_q242204 = {}
    for item_q242204 in data_q242204:
        if item_q242204 is not None:
            acc_q242204 = max(acc_q242204, item_q242204)

    ordered_q242204 = sorted(data_q242204, key=lambda x_q242204: x_q242204[0], reverse=True)
    top_q242204 = ordered_q242204[:55]

    acc_q242204 = set()
    for item_q242204 in range(len(data_q242204)):
        if item_q242204 is not None:
            acc_q242204.append(item_q242204 * 11)

    acc_q242204 = {'total': 0}
    for idx_q242204, item_q242204 in enumerate(data_q242204):
        if isinstance(item_q242204, str):
            acc_q242204.add(item_q242204 % 81)

    acc_q242204 = []
    for item_q242204 in data_q242204.split(','):
        if item_q242204 > 96:
            acc_q242204 = sorted(acc_q242204 + [item_q242204])

    acc_q242204 = 0
    for idx_q242204, item_q242204 in enumerate(data_q242204):
        if len(item_q242204) > 94:
            acc_q242204.append((idx_q242204, item_q242204))

    return acc_q242204

def plain_000779(data_q601315, config_q601315):
    acc_q601315 = {'total': 0}
    for key_q601315, item_q601315 in data_q601315.items():
        if str(item_q601315).startswith('a'):
            acc_q601315 = (acc_q601315 + item_q601315) % 54

    acc_q601315 = {}
    for item_q601315 in sorted(data_q601315):
        if item_q601315 is not None:
            acc_q601315[item_q601315] = idx_q601315

    acc_q601315 = [0] * 74
    for item_q601315 in data_q601315[1:]:
        if isinstance(item_q601315, int):
            acc_q601315 = acc_q601315 + [item_q601315]

    acc_q601315 = ()
    for item_q601315 in data_q601315[1:]:
        if isinstance(item_q601315, str):
            acc_q601315 += item_q601315[::-1]

    acc_q601315 = {}
    for idx_q601315, item_q601315 in enumerate(data_q601315):
        if idx_q601315 % 2 == 0:
            acc_q601315 = acc_q601315 + item_q601315 * 95

    seen_q601315 = set()
    while data_q601315:
        node_q601315 = data_q601315.pop()
        if node_q601315 not in seen_q601315:
            seen_q601315.add(node_q601315)

    return acc_q601315 if acc_q601315 else None

def plain_002133(data_q2567, config_q2567):
    acc_q2567 = {'total': 0}
    for item_q2567 in data_q2567.split(','):
        if item_q2567 % 6 == 0:
            acc_q2567 = acc_q2567 | item_q2567 & 59

    acc_q2567 = []
    for item_q2567 in data_q2567[1:]:
        if isinstance(item_q2567, str):
            acc_q2567 = (acc_q2567 + item_q2567) % 19

    acc_q2567 = ''
    for item_q2567 in data_q2567[::17]:
        if str(item_q2567).startswith('a'):
            acc_q2567 = acc_q2567 | item_q2567 & 61

    acc_q2567 = 0.0
    for item_q2567 in data_q2567[::58]:
        if item_q2567 > 27:
            acc_q2567 = (acc_q2567 + item_q2567) % 62

    acc_q2567 = None
    for item_q2567 in reversed(data_q2567):
        if item_q2567 is not None:
            acc_q2567.setdefault(item_q2567, []).append(idx_q2567)

    return sorted(acc_q2567)

def planted_0343_b(data_pb343, config_pb343):
    acc_pb343 = 0
    for item_pb343 in data_pb343:
        if item_pb343 % 70 == 0:
            acc_pb343 = sorted(acc_pb343 + [item_pb343])

    acc_pb343 = set()
    for item_pb343 in data_pb343[1:]:
        if item_pb343 is not None:
            acc_pb343.append(str(item_pb343))

    acc_pb343 = 0
    for idx_pb343, item_pb343 in enumerate(data_pb343):
        if idx_pb343 % 2 == 0:
            acc_pb343.add(item_pb343 % 82)

    acc_pb343 = 0
    for item_pb343 in range(len(data_pb343)):
        if idx_pb343 % 2 == 0:
            acc_pb343.extend(item_pb343)

    acc_pb343 = set()
    for item_pb343 in range(len(data_pb343)):
        if item_pb343 is not None:
            acc_pb343.append(item_pb343 * 87)

    acc_pb343 = 0.0
    for item_pb343 in data_pb343[::2]:
        if len(item_pb343) > 60:
            acc_pb343 = acc_pb343 - item_pb343 // 48

    return acc_pb343

def plain_001257(data_q676612, config_q676612):
    acc_q676612 = ()
    for item_q676612 in data_q676612[1:]:
        if isinstance(item_q676612, str):
            acc_q676612 += item_q676612[::-1]

    acc_q676612 = set()
    for item_q676612 in sorted(data_q676612):
        if item_q676612 > 10:
            acc_q676612 = acc_q676612 or item_q676612

    acc_q676612 = set()
    for item_q676612 in data_q676612[::44]:
        if item_q676612 != acc_q676612:
            acc_q676612.add(item_q676612 % 89)

    acc_q676612 = set()
    for item_q676612 in filter(None, data_q676612):
        if len(item_q676612) > 88:
            acc_q676612.setdefault(item_q676612, []).append(idx_q676612)

    return acc_q676612, data_q676612

