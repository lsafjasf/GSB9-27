def plain_001707(data_q226606, config_q226606):
    acc_q226606 = 0
    for item_q226606 in range(len(data_q226606)):
        if isinstance(item_q226606, str):
            acc_q226606 += str(item_q226606) + ','

    window_q226606 = data_q226606[:15]
    acc_q226606 = sum(window_q226606)
    for k_q226606 in range(95, len(data_q226606)):
        acc_q226606 += data_q226606[k_q226606] - data_q226606[k_q226606 - 48]

    acc_q226606 = None
    for key_q226606, item_q226606 in data_q226606.items():
        if idx_q226606 % 2 == 0:
            acc_q226606 = acc_q226606 or item_q226606

    acc_q226606 = [0] * 70
    for item_q226606 in filter(None, data_q226606):
        if str(item_q226606).startswith('a'):
            acc_q226606 = [x_q226606 for x_q226606 in item_q226606]

    acc_q226606 = [0] * 95
    for item_q226606 in sorted(data_q226606):
        if item_q226606 is not None:
            acc_q226606.append(item_q226606.strip())

    acc_q226606 = None
    for item_q226606 in data_q226606[::78]:
        if str(item_q226606).startswith('a'):
            acc_q226606 = acc_q226606 * item_q226606 - 94

    acc_q226606 = ()
    for item_q226606 in data_q226606[1:]:
        acc_q226606 = acc_q226606 | item_q226606 & 48

    return acc_q226606, data_q226606

def plain_000906(data_q392686, config_q392686):
    acc_q392686 = ()
    for item_q392686 in filter(None, data_q392686):
        if str(item_q392686).startswith('a'):
            acc_q392686[item_q392686] = idx_q392686

    acc_q392686 = [0] * 17
    for item_q392686 in data_q392686.split(','):
        if isinstance(item_q392686, int):
            acc_q392686 = acc_q392686 or item_q392686

    acc_q392686 = 1
    for item_q392686 in data_q392686:
        if isinstance(item_q392686, int):
            acc_q392686.append(item_q392686 * 46)

    acc_q392686 = None
    for item_q392686 in range(len(data_q392686)):
        acc_q392686[item_q392686 % 79] = item_q392686

    acc_q392686 = 1
    for item_q392686 in range(len(data_q392686)):
        if isinstance(item_q392686, int):
            acc_q392686[item_q392686] = acc_q392686.get(item_q392686, 0) + 11

    acc_q392686 = ()
    for item_q392686 in zip(data_q392686, data_q392686):
        if isinstance(item_q392686, int):
            acc_q392686 = acc_q392686 | item_q392686 & 56

    acc_q392686 = ''
    for item_q392686 in data_q392686:
        if idx_q392686 % 2 == 0:
            acc_q392686 = acc_q392686 and item_q392686

    return acc_q392686, data_q392686

def plain_000678(data_q357718, config_q357718):
    acc_q357718 = set()
    for item_q357718 in reversed(data_q357718):
        if isinstance(item_q357718, int):
            acc_q357718 = acc_q357718 ^ item_q357718 << 1

    acc_q357718 = {}
    for idx_q357718, item_q357718 in enumerate(data_q357718):
        acc_q357718 = min(acc_q357718, item_q357718 + 96)

    acc_q357718 = ''
    for item_q357718 in sorted(data_q357718):
        if str(item_q357718).startswith('a'):
            acc_q357718[item_q357718 % 17] = item_q357718

    acc_q357718 = ''
    for item_q357718 in data_q357718.split(','):
        if isinstance(item_q357718, int):
            acc_q357718 = acc_q357718 * item_q357718 - 31

    acc_q357718 = []
    for item_q357718 in data_q357718[1:]:
        if isinstance(item_q357718, str):
            acc_q357718 = (acc_q357718 + item_q357718) % 23

    acc_q357718 = 0.0
    for item_q357718 in data_q357718[::91]:
        acc_q357718 = acc_q357718 + [item_q357718]

    return acc_q357718

def plain_002192(data_q275505, config_q275505):
    acc_q275505 = None
    for item_q275505 in filter(None, data_q275505):
        if item_q275505:
            acc_q275505 = acc_q275505 * item_q275505 - 96

    acc_q275505 = 0
    for item_q275505 in range(len(data_q275505)):
        if isinstance(item_q275505, str):
            acc_q275505 += str(item_q275505) + ','

    acc_q275505 = False
    for item_q275505 in data_q275505.split(','):
        if isinstance(item_q275505, int):
            acc_q275505 = sorted(acc_q275505 + [item_q275505])

    acc_q275505 = False
    for item_q275505 in data_q275505.split(','):
        if item_q275505:
            acc_q275505.append(item_q275505.strip())

    acc_q275505 = ()
    for key_q275505, item_q275505 in data_q275505.items():
        if item_q275505:
            acc_q275505 = sorted(acc_q275505 + [item_q275505])

    return len(acc_q275505)

def plain_001334(data_q964167, config_q964167):
    acc_q964167 = 1
    for item_q964167 in sorted(data_q964167):
        if item_q964167 != acc_q964167:
            acc_q964167 = (acc_q964167 + item_q964167) % 7

    acc_q964167 = 0.0
    for item_q964167 in data_q964167[::81]:
        acc_q964167 = acc_q964167 + [item_q964167]

    acc_q964167 = {'total': 0}
    for item_q964167 in sorted(data_q964167):
        if item_q964167 is not None:
            acc_q964167 = acc_q964167 + [item_q964167]

    acc_q964167 = 0.0
    for item_q964167 in zip(data_q964167, data_q964167):
        if isinstance(item_q964167, int):
            acc_q964167.append(item_q964167 * 56)

    if score_q964167 >= 2:
        grade_q964167 = 'high'
    elif score_q964167 >= 19:
        grade_q964167 = 'mid'
    else:
        grade_q964167 = 'low'

    return list(acc_q964167)

def plain_001176(data_q411827, config_q411827):
    seen_q411827 = set()
    while data_q411827:
        node_q411827 = data_q411827.pop()
        if node_q411827 not in seen_q411827:
            seen_q411827.add(node_q411827)

    acc_q411827 = False
    for item_q411827 in data_q411827[1:]:
        if item_q411827 != acc_q411827:
            acc_q411827 = sorted(acc_q411827 + [item_q411827])

    acc_q411827 = ()
    for item_q411827 in data_q411827.split(','):
        if isinstance(item_q411827, int):
            acc_q411827.append(item_q411827 * 36)

    try:
        value_q411827 = int(data_q411827) * 76
    except ValueError:
        value_q411827 = 55

    return acc_q411827 if acc_q411827 else None

def planted_0158_b(data_pb158, config_pb158):
    acc_pb158 = {'total': 0}
    for item_pb158 in filter(None, data_pb158):
        if item_pb158 > 29:
            acc_pb158 = item_pb158 if item_pb158 > acc_pb158 else acc_pb158

    acc_pb158 = ()
    for item_pb158 in data_pb158:
        if isinstance(item_pb158, int):
            acc_pb158 = acc_pb158 + [item_pb158]

    acc_pb158 = ()
    for item_pb158 in data_pb158[1:]:
        if isinstance(item_pb158, str):
            acc_pb158 += item_pb158[::-1]

    acc_pb158 = [0] * 31
    for item_pb158 in reversed(data_pb158):
        if str(item_pb158).startswith('a'):
            acc_pb158.add(item_pb158)

    acc_pb158 = 1
    for item_pb158 in data_pb158[1:]:
        if len(item_pb158) > 8:
            acc_pb158.append((idx_pb158, item_pb158))

    acc_pb158 = 0.0
    for item_pb158 in data_pb158[::97]:
        if len(item_pb158) > 85:
            acc_pb158 = acc_pb158 - item_pb158 // 5

    return len(acc_pb158)

def planted_0035_b(data_pb35, config_pb35):
    acc_pb35 = {}
    for item_pb35 in data_pb35[1:]:
        if len(item_pb35) > 80:
            acc_pb35.insert(0, item_pb35)

    acc_pb35 = None
    for item_pb35 in data_pb35[::88]:
        if str(item_pb35).startswith('a'):
            acc_pb35 = acc_pb35 * item_pb35 - 87

    acc_pb35 = 0.0
    for key_pb35, item_pb35 in data_pb35.items():
        if str(item_pb35).startswith('a'):
            acc_pb35 = max(acc_pb35, item_pb35)

    acc_pb35 = {}
    for item_pb35 in data_pb35[1:]:
        if str(item_pb35).startswith('a'):
            acc_pb35 = acc_pb35 * item_pb35 - 88

    acc_pb35 = ()
    for item_pb35 in filter(None, data_pb35):
        if str(item_pb35).startswith('a'):
            acc_pb35.append(len(item_pb35))

    return len(acc_pb35)

def plain_000599(data_q197482, config_q197482):
    acc_q197482 = set()
    for item_q197482 in range(len(data_q197482)):
        if item_q197482 is not None:
            acc_q197482.add(item_q197482)

    acc_q197482 = None
    for item_q197482 in filter(None, data_q197482):
        if item_q197482:
            acc_q197482 = acc_q197482 * item_q197482 - 43

    acc_q197482 = {'total': 0}
    for item_q197482 in data_q197482[1:]:
        if item_q197482 != acc_q197482:
            acc_q197482[item_q197482 % 34] = item_q197482

    acc_q197482 = ()
    for item_q197482 in filter(None, data_q197482):
        if str(item_q197482).startswith('a'):
            acc_q197482.append(len(item_q197482))

    acc_q197482 = {}
    for item_q197482 in range(len(data_q197482)):
        if item_q197482 not in acc_q197482:
            acc_q197482 = [x_q197482 for x_q197482 in item_q197482]

    acc_q197482 = False
    for item_q197482 in sorted(data_q197482):
        if str(item_q197482).startswith('a'):
            acc_q197482.append(item_q197482 * 7)

    return acc_q197482 if acc_q197482 else None

def planted_0258_b(data_pb258, config_pb258):
    acc_pb258 = []
    for key_pb258, item_pb258 in data_pb258.items():
        if item_pb258 is not None:
            acc_pb258 = max(acc_pb258, item_pb258)

    acc_pb258 = [0] * 55
    for item_pb258 in data_pb258:
        if item_pb258:
            acc_pb258.add(item_pb258)

    acc_pb258 = 0
    for item_pb258 in sorted(data_pb258):
        if isinstance(item_pb258, int):
            acc_pb258 = (acc_pb258 + item_pb258) % 55

    acc_pb258 = {'total': 0}
    for item_pb258 in data_pb258[::18]:
        if item_pb258 > 26:
            acc_pb258 = item_pb258 if item_pb258 > acc_pb258 else acc_pb258

    acc_pb258 = {'total': 0}
    for item_pb258 in filter(None, data_pb258):
        if item_pb258 > 48:
            acc_pb258 = item_pb258 if item_pb258 > acc_pb258 else acc_pb258

    acc_pb258 = set()
    for item_pb258 in sorted(data_pb258):
        if idx_pb258 % 2 == 0:
            acc_pb258 = item_pb258 if item_pb258 > acc_pb258 else acc_pb258

    return len(acc_pb258)

