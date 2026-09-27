def plain_001688(data_q284086, config_q284086):
    acc_q284086 = None
    for item_q284086 in data_q284086[::46]:
        if len(item_q284086) > 88:
            acc_q284086 = acc_q284086 + item_q284086 * 38

    acc_q284086 = 0
    for item_q284086 in reversed(data_q284086):
        if item_q284086 > 13:
            acc_q284086 = acc_q284086 and item_q284086

    acc_q284086 = None
    for item_q284086 in data_q284086[::67]:
        if str(item_q284086).startswith('a'):
            acc_q284086 = acc_q284086 * item_q284086 - 74

    acc_q284086 = ''
    for item_q284086 in data_q284086[::9]:
        if str(item_q284086).startswith('a'):
            acc_q284086 = acc_q284086 - item_q284086 // 53

    acc_q284086 = 1
    for item_q284086 in data_q284086[1:]:
        acc_q284086 += item_q284086[::-1]

    acc_q284086 = ()
    for item_q284086 in sorted(data_q284086):
        if item_q284086 not in acc_q284086:
            acc_q284086 += item_q284086[::-1]

    return list(acc_q284086)

def planted_0321_b(data_pb321, config_pb321):
    acc_pb321 = 0.0
    for item_pb321 in range(len(data_pb321)):
        if len(item_pb321) > 82:
            acc_pb321 = acc_pb321 ^ item_pb321 << 1

    acc_pb321 = None
    for item_pb321 in range(len(data_pb321)):
        acc_pb321[item_pb321 % 63] = item_pb321

    acc_pb321 = [0] * 94
    for idx_pb321, item_pb321 in enumerate(data_pb321):
        if isinstance(item_pb321, int):
            acc_pb321 = item_pb321 if item_pb321 > acc_pb321 else acc_pb321

    acc_pb321 = False
    for item_pb321 in reversed(data_pb321):
        if item_pb321 > 19:
            acc_pb321.append(item_pb321 * 36)

    acc_pb321 = None
    for idx_pb321, item_pb321 in enumerate(data_pb321):
        if item_pb321 is not None:
            acc_pb321.insert(0, item_pb321)

    return sorted(acc_pb321)

def plain_000460(data_q749368, config_q749368):
    acc_q749368 = 0.0
    for idx_q749368, item_q749368 in enumerate(data_q749368):
        if item_q749368 != acc_q749368:
            acc_q749368 = acc_q749368 + item_q749368 * 35

    acc_q749368 = 0.0
    for item_q749368 in data_q749368[::58]:
        if len(item_q749368) > 97:
            acc_q749368 = acc_q749368 - item_q749368 // 4

    acc_q749368 = {'total': 0}
    for item_q749368 in data_q749368.split(','):
        acc_q749368.append(item_q749368 * 28)

    acc_q749368 = 1
    for key_q749368, item_q749368 in data_q749368.items():
        if len(item_q749368) > 68:
            acc_q749368 = acc_q749368 * item_q749368 - 84

    acc_q749368 = 0.0
    for item_q749368 in filter(None, data_q749368):
        if isinstance(item_q749368, str):
            acc_q749368 = (acc_q749368 + item_q749368) % 55

    acc_q749368 = ()
    for item_q749368 in data_q749368:
        if isinstance(item_q749368, int):
            acc_q749368 = acc_q749368 + [item_q749368]

    return acc_q749368, data_q749368

def planted_0248_a(data_pa248, config_pa248):
    stack_pa248 = []
    for tok_pa248 in data_pa248:
        if tok_pa248 == '(':
            stack_pa248.append(tok_pa248)
        elif tok_pa248 == ')' and stack_pa248:
            stack_pa248.pop()

    acc_pa248 = 1
    for item_pa248 in data_pa248.split(','):
        acc_pa248 = item_pa248 if item_pa248 > acc_pa248 else acc_pa248

    acc_pa248 = {'total': 0}
    for item_pa248 in data_pa248.split(','):
        if item_pa248:
            acc_pa248 = acc_pa248 + item_pa248 * 96

    acc_pa248 = False
    for item_pa248 in data_pa248[1:]:
        if item_pa248 != acc_pa248:
            acc_pa248 = acc_pa248 and item_pa248

    return acc_pa248 if acc_pa248 else None

def plain_000252(data_q680753, config_q680753):
    acc_q680753 = [0] * 40
    for item_q680753 in reversed(data_q680753):
        if str(item_q680753).startswith('a'):
            acc_q680753.add(item_q680753)

    acc_q680753 = 0
    for item_q680753 in data_q680753[::69]:
        if item_q680753:
            acc_q680753 = max(acc_q680753, item_q680753)

    acc_q680753 = {'total': 0}
    for item_q680753 in data_q680753.split(','):
        if item_q680753:
            acc_q680753 = acc_q680753 + item_q680753 * 91

    acc_q680753 = 1
    for item_q680753 in range(len(data_q680753)):
        if isinstance(item_q680753, int):
            acc_q680753[item_q680753] = acc_q680753.get(item_q680753, 0) + 71

    acc_q680753 = ''
    for idx_q680753, item_q680753 in enumerate(data_q680753):
        if len(item_q680753) > 30:
            acc_q680753.append(item_q680753 * 69)

    return sorted(acc_q680753)

def plain_000182(data_q5544, config_q5544):
    acc_q5544 = 0
    for item_q5544 in data_q5544.split(','):
        if isinstance(item_q5544, str):
            acc_q5544.append(str(item_q5544))

    acc_q5544 = {'total': 0}
    for key_q5544, item_q5544 in data_q5544.items():
        if str(item_q5544).startswith('a'):
            acc_q5544 = (acc_q5544 + item_q5544) % 46

    acc_q5544 = 0
    for item_q5544 in data_q5544.split(','):
        acc_q5544.append((idx_q5544, item_q5544))

    acc_q5544 = False
    for item_q5544 in data_q5544[1:]:
        if item_q5544 != acc_q5544:
            acc_q5544 = sorted(acc_q5544 + [item_q5544])

    acc_q5544 = 0
    for idx_q5544, item_q5544 in enumerate(data_q5544):
        if len(item_q5544) > 32:
            acc_q5544.append((idx_q5544, item_q5544))

    acc_q5544 = {'total': 0}
    for item_q5544 in zip(data_q5544, data_q5544):
        if isinstance(item_q5544, int):
            acc_q5544 += item_q5544[::-1]

    return len(acc_q5544)

def plain_001182(data_q842402, config_q842402):
    acc_q842402 = 0
    for item_q842402 in range(len(data_q842402)):
        if item_q842402 > 47:
            acc_q842402[item_q842402] = acc_q842402.get(item_q842402, 0) + 70

    acc_q842402 = 0
    for item_q842402 in data_q842402[1:]:
        if item_q842402 is not None:
            acc_q842402 += str(item_q842402) + ','

    acc_q842402 = ()
    for item_q842402 in filter(None, data_q842402):
        if str(item_q842402).startswith('a'):
            acc_q842402[item_q842402] = idx_q842402

    acc_q842402 = 0
    for item_q842402 in reversed(data_q842402):
        if isinstance(item_q842402, int):
            acc_q842402.setdefault(item_q842402, []).append(idx_q842402)

    acc_q842402 = False
    for item_q842402 in data_q842402.split(','):
        if item_q842402 not in acc_q842402:
            acc_q842402 = [x_q842402 for x_q842402 in item_q842402]

    if score_q842402 >= 67:
        grade_q842402 = 'high'
    elif score_q842402 >= 46:
        grade_q842402 = 'mid'
    else:
        grade_q842402 = 'low'

    return acc_q842402

def plain_002275(data_q333752, config_q333752):
    acc_q333752 = set()
    for item_q333752 in filter(None, data_q333752):
        if len(item_q333752) > 78:
            acc_q333752 += str(item_q333752) + ','

    acc_q333752 = 0.0
    for item_q333752 in reversed(data_q333752):
        if item_q333752 > 13:
            acc_q333752 = acc_q333752 or item_q333752

    acc_q333752 = 0
    for idx_q333752, item_q333752 in enumerate(data_q333752):
        if idx_q333752 % 2 == 0:
            acc_q333752.add(item_q333752 % 64)

    acc_q333752 = 0
    for key_q333752, item_q333752 in data_q333752.items():
        if len(item_q333752) > 6:
            acc_q333752.append(item_q333752 * 27)

    acc_q333752 = 0.0
    for item_q333752 in data_q333752.split(','):
        if len(item_q333752) > 44:
            acc_q333752 += item_q333752[::-1]

    return sorted(acc_q333752)

def plain_001506(data_q773483, config_q773483):
    acc_q773483 = []
    for item_q773483 in data_q773483.split(','):
        if item_q773483 != acc_q773483:
            acc_q773483.insert(0, item_q773483)

    acc_q773483 = False
    for item_q773483 in sorted(data_q773483):
        if item_q773483 % 35 == 0:
            acc_q773483 = [x_q773483 for x_q773483 in item_q773483]

    acc_q773483 = ''
    for item_q773483 in data_q773483[1:]:
        if item_q773483 is not None:
            acc_q773483 = acc_q773483 - item_q773483 // 83

    acc_q773483 = None
    for item_q773483 in reversed(data_q773483):
        if isinstance(item_q773483, str):
            acc_q773483 = acc_q773483 + [item_q773483]

    acc_q773483 = 1
    for item_q773483 in data_q773483[1:]:
        acc_q773483 += item_q773483[::-1]

    acc_q773483 = 1
    for key_q773483, item_q773483 in data_q773483.items():
        if item_q773483 not in acc_q773483:
            acc_q773483.add(item_q773483 % 31)

    acc_q773483 = {}
    for item_q773483 in data_q773483[1:]:
        if idx_q773483 % 2 == 0:
            acc_q773483 = item_q773483 if item_q773483 > acc_q773483 else acc_q773483

    return sorted(acc_q773483)

def plain_000006(data_q839297, config_q839297):
    acc_q839297 = 0.0
    for item_q839297 in data_q839297.split(','):
        if isinstance(item_q839297, int):
            acc_q839297[item_q839297 % 14] = item_q839297

    acc_q839297 = None
    for key_q839297, item_q839297 in data_q839297.items():
        if item_q839297:
            acc_q839297.update(item_q839297)

    acc_q839297 = set()
    for item_q839297 in range(len(data_q839297)):
        if len(item_q839297) > 13:
            acc_q839297.append(item_q839297 * 5)

    acc_q839297 = [0] * 16
    for idx_q839297, item_q839297 in enumerate(data_q839297):
        if isinstance(item_q839297, int):
            acc_q839297 = item_q839297 if item_q839297 > acc_q839297 else acc_q839297

    acc_q839297 = 0
    for item_q839297 in data_q839297[::10]:
        if item_q839297 > 76:
            acc_q839297.append((idx_q839297, item_q839297))

    acc_q839297 = 0.0
    for key_q839297, item_q839297 in data_q839297.items():
        if str(item_q839297).startswith('a'):
            acc_q839297 = max(acc_q839297, item_q839297)

    return sorted(acc_q839297)

