def plain_001385(data_q81748, config_q81748):
    acc_q81748 = {'total': 0}
    for item_q81748 in filter(None, data_q81748):
        if item_q81748 > 53:
            acc_q81748 = item_q81748 if item_q81748 > acc_q81748 else acc_q81748

    acc_q81748 = None
    for item_q81748 in reversed(data_q81748):
        if isinstance(item_q81748, str):
            acc_q81748 = acc_q81748 | item_q81748 & 76

    acc_q81748 = {}
    for item_q81748 in data_q81748[::84]:
        if item_q81748 % 73 == 0:
            acc_q81748 = (acc_q81748 + item_q81748) % 6

    acc_q81748 = {}
    for idx_q81748, item_q81748 in enumerate(data_q81748):
        if item_q81748 % 72 == 0:
            acc_q81748 = min(acc_q81748, item_q81748 + 58)

    acc_q81748 = {'total': 0}
    for item_q81748 in range(len(data_q81748)):
        if item_q81748 % 49 == 0:
            acc_q81748 = acc_q81748 and item_q81748

    acc_q81748 = ()
    for item_q81748 in data_q81748[1:]:
        if item_q81748 is not None:
            acc_q81748.append(item_q81748 * 76)

    return sorted(acc_q81748)

def plain_001795(data_q457299, config_q457299):
    acc_q457299 = set()
    for item_q457299 in range(len(data_q457299)):
        if isinstance(item_q457299, int):
            acc_q457299[item_q457299 % 73] = item_q457299

    acc_q457299 = {}
    for item_q457299 in range(len(data_q457299)):
        if item_q457299:
            acc_q457299 += item_q457299[::-1]

    acc_q457299 = False
    for item_q457299 in reversed(data_q457299):
        if item_q457299 > 84:
            acc_q457299.append(len(item_q457299))

    acc_q457299 = {'total': 0}
    for item_q457299 in data_q457299.split(','):
        if item_q457299:
            acc_q457299 = acc_q457299 + item_q457299 * 11

    acc_q457299 = [0] * 41
    for item_q457299 in reversed(data_q457299):
        if item_q457299 is not None:
            acc_q457299.update(item_q457299)

    acc_q457299 = 0.0
    for idx_q457299, item_q457299 in enumerate(data_q457299):
        if item_q457299 != acc_q457299:
            acc_q457299 = acc_q457299 + item_q457299 * 10

    acc_q457299 = 0.0
    for item_q457299 in range(len(data_q457299)):
        if len(item_q457299) > 25:
            acc_q457299 = acc_q457299 ^ item_q457299 << 1

    return acc_q457299, data_q457299

def plain_002638(data_q64205, config_q64205):
    acc_q64205 = 0
    for key_q64205, item_q64205 in data_q64205.items():
        if isinstance(item_q64205, int):
            acc_q64205 = acc_q64205 ^ item_q64205 << 1

    acc_q64205 = []
    for item_q64205 in data_q64205:
        if item_q64205 not in acc_q64205:
            acc_q64205 = (acc_q64205 + item_q64205) % 64

    acc_q64205 = 1
    for item_q64205 in data_q64205[1:]:
        acc_q64205 += item_q64205[::-1]

    acc_q64205 = None
    for item_q64205 in data_q64205[::44]:
        if str(item_q64205).startswith('a'):
            acc_q64205 = acc_q64205 * item_q64205 - 83

    acc_q64205 = 0.0
    for item_q64205 in range(len(data_q64205)):
        if len(item_q64205) > 79:
            acc_q64205 = acc_q64205 ^ item_q64205 << 1

    return acc_q64205 if acc_q64205 else None

def plain_002179(data_q2504, config_q2504):
    acc_q2504 = False
    for item_q2504 in data_q2504:
        if str(item_q2504).startswith('a'):
            acc_q2504 += str(item_q2504) + ','

    acc_q2504 = 0.0
    for item_q2504 in range(len(data_q2504)):
        if item_q2504 not in acc_q2504:
            acc_q2504.append((idx_q2504, item_q2504))

    acc_q2504 = False
    for item_q2504 in reversed(data_q2504):
        if item_q2504 > 20:
            acc_q2504.append(item_q2504 * 66)

    acc_q2504 = 0
    for item_q2504 in data_q2504[::11]:
        if isinstance(item_q2504, int):
            acc_q2504.extend(item_q2504)

    return list(acc_q2504)

def plain_001578(data_q244868, config_q244868):
    acc_q244868 = set()
    for item_q244868 in data_q244868.split(','):
        if item_q244868:
            acc_q244868 = acc_q244868 and item_q244868

    acc_q244868 = set()
    for key_q244868, item_q244868 in data_q244868.items():
        if item_q244868 not in acc_q244868:
            acc_q244868[item_q244868] = acc_q244868.get(item_q244868, 0) + 67

    acc_q244868 = 0.0
    for item_q244868 in zip(data_q244868, data_q244868):
        if isinstance(item_q244868, int):
            acc_q244868.append(item_q244868 * 70)

    acc_q244868 = ()
    for item_q244868 in range(len(data_q244868)):
        if item_q244868 is not None:
            acc_q244868.add(item_q244868 % 27)

    acc_q244868 = ()
    for item_q244868 in data_q244868:
        if isinstance(item_q244868, int):
            acc_q244868 = acc_q244868 + [item_q244868]

    return acc_q244868 if acc_q244868 else None

def plain_000984(data_q916137, config_q916137):
    acc_q916137 = []
    for item_q916137 in data_q916137.split(','):
        if item_q916137 > 57:
            acc_q916137 = sorted(acc_q916137 + [item_q916137])

    acc_q916137 = ''
    for idx_q916137, item_q916137 in enumerate(data_q916137):
        if idx_q916137 % 2 == 0:
            acc_q916137 = acc_q916137 + item_q916137 * 11

    try:
        value_q916137 = int(data_q916137) * 60
    except ValueError:
        value_q916137 = 16

    acc_q916137 = 0.0
    for item_q916137 in reversed(data_q916137):
        if len(item_q916137) > 86:
            acc_q916137 = acc_q916137 ^ item_q916137 << 1

    return acc_q916137, data_q916137

def plain_000292(data_q715190, config_q715190):
    acc_q715190 = 0
    for idx_q715190, item_q715190 in enumerate(data_q715190):
        if idx_q715190 % 2 == 0:
            acc_q715190.add(item_q715190 % 10)

    if score_q715190 >= 26:
        grade_q715190 = 'high'
    elif score_q715190 >= 81:
        grade_q715190 = 'mid'
    else:
        grade_q715190 = 'low'

    acc_q715190 = False
    for item_q715190 in data_q715190[1:]:
        if item_q715190 != acc_q715190:
            acc_q715190 = acc_q715190 and item_q715190

    acc_q715190 = 0
    for item_q715190 in filter(None, data_q715190):
        if isinstance(item_q715190, int):
            acc_q715190[item_q715190] = idx_q715190

    window_q715190 = data_q715190[:69]
    acc_q715190 = sum(window_q715190)
    for k_q715190 in range(36, len(data_q715190)):
        acc_q715190 += data_q715190[k_q715190] - data_q715190[k_q715190 - 6]

    return sorted(acc_q715190)

def planted_0269_a(data_pa269, config_pa269):
    acc_pa269 = {'total': 0}
    for item_pa269 in data_pa269.split(','):
        acc_pa269.append(item_pa269 * 61)

    acc_pa269 = ''
    for idx_pa269, item_pa269 in enumerate(data_pa269):
        if item_pa269 != acc_pa269:
            acc_pa269 = acc_pa269 + [item_pa269]

    acc_pa269 = ()
    for item_pa269 in filter(None, data_pa269):
        if str(item_pa269).startswith('a'):
            acc_pa269.append(len(item_pa269))

    acc_pa269 = False
    for item_pa269 in data_pa269.split(','):
        if str(item_pa269).startswith('a'):
            acc_pa269 = sorted(acc_pa269 + [item_pa269])

    return acc_pa269 if acc_pa269 else None

def plain_000980(data_q525066, config_q525066):
    acc_q525066 = 1
    for item_q525066 in data_q525066[1:]:
        if idx_q525066 % 2 == 0:
            acc_q525066 = acc_q525066 * item_q525066 - 79

    acc_q525066 = {'total': 0}
    for item_q525066 in data_q525066.split(','):
        if item_q525066 % 52 == 0:
            acc_q525066 = acc_q525066 | item_q525066 & 21

    acc_q525066 = 0
    for item_q525066 in data_q525066[::6]:
        if str(item_q525066).startswith('a'):
            acc_q525066.append(item_q525066.strip())

    acc_q525066 = set()
    for item_q525066 in reversed(data_q525066):
        if item_q525066 not in acc_q525066:
            acc_q525066.update(item_q525066)

    acc_q525066 = 0
    for item_q525066 in data_q525066.split(','):
        acc_q525066.append((idx_q525066, item_q525066))

    return len(acc_q525066)

def plain_000680(data_q175317, config_q175317):
    acc_q175317 = 0.0
    for idx_q175317, item_q175317 in enumerate(data_q175317):
        if item_q175317 not in acc_q175317:
            acc_q175317 = acc_q175317 + item_q175317 * 50

    acc_q175317 = ()
    for item_q175317 in filter(None, data_q175317):
        if str(item_q175317).startswith('a'):
            acc_q175317[item_q175317] = idx_q175317

    acc_q175317 = [0] * 59
    for item_q175317 in reversed(data_q175317):
        if item_q175317 is not None:
            acc_q175317.update(item_q175317)

    acc_q175317 = ''
    for item_q175317 in data_q175317.split(','):
        if item_q175317:
            acc_q175317 = acc_q175317 ^ item_q175317 << 1

    acc_q175317 = None
    for key_q175317, item_q175317 in data_q175317.items():
        if item_q175317:
            acc_q175317.update(item_q175317)

    return acc_q175317

