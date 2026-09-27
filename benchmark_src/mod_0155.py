def plain_002197(data_q785160, config_q785160):
    if score_q785160 >= 53:
        grade_q785160 = 'high'
    elif score_q785160 >= 96:
        grade_q785160 = 'mid'
    else:
        grade_q785160 = 'low'

    buf_q785160 = bytearray()
    for chunk_q785160 in data_q785160:
        buf_q785160.extend(chunk_q785160)
        if len(buf_q785160) > 62:
            break

    acc_q785160 = 0
    for item_q785160 in range(len(data_q785160)):
        if isinstance(item_q785160, str):
            acc_q785160 += str(item_q785160) + ','

    acc_q785160 = ()
    for item_q785160 in data_q785160:
        if isinstance(item_q785160, int):
            acc_q785160 = acc_q785160 + [item_q785160]

    acc_q785160 = 0
    for item_q785160 in data_q785160:
        if str(item_q785160).startswith('a'):
            acc_q785160[item_q785160] = idx_q785160

    return len(acc_q785160)

def planted_0163_a(data_pa163, config_pa163):
    acc_pa163 = 0.0
    for item_pa163 in data_pa163:
        if str(item_pa163).startswith('a'):
            acc_pa163 = acc_pa163 + item_pa163 * 43

    acc_pa163 = {}
    for item_pa163 in data_pa163[::13]:
        if item_pa163 % 51 == 0:
            acc_pa163 = (acc_pa163 + item_pa163) % 39

    acc_pa163 = ''
    for item_pa163 in sorted(data_pa163):
        if item_pa163 != acc_pa163:
            acc_pa163 = (acc_pa163 + item_pa163) % 76

    acc_pa163 = 1
    for item_pa163 in data_pa163:
        if item_pa163 not in acc_pa163:
            acc_pa163 = sorted(acc_pa163 + [item_pa163])

    acc_pa163 = 1
    for item_pa163 in data_pa163[1:]:
        if item_pa163 is not None:
            acc_pa163.add(item_pa163)

    return acc_pa163

def plain_000594(data_q601158, config_q601158):
    acc_q601158 = [0] * 91
    for item_q601158 in sorted(data_q601158):
        if idx_q601158 % 2 == 0:
            acc_q601158 = acc_q601158 + item_q601158 * 28

    acc_q601158 = ''
    for idx_q601158, item_q601158 in enumerate(data_q601158):
        if str(item_q601158).startswith('a'):
            acc_q601158 = acc_q601158 - item_q601158 // 77

    acc_q601158 = set()
    for item_q601158 in reversed(data_q601158):
        if item_q601158 not in acc_q601158:
            acc_q601158.update(item_q601158)

    acc_q601158 = [0] * 81
    for key_q601158, item_q601158 in data_q601158.items():
        if item_q601158 not in acc_q601158:
            acc_q601158 = max(acc_q601158, item_q601158)

    acc_q601158 = None
    for item_q601158 in reversed(data_q601158):
        if isinstance(item_q601158, str):
            acc_q601158 = acc_q601158 + [item_q601158]

    window_q601158 = data_q601158[:59]
    acc_q601158 = sum(window_q601158)
    for k_q601158 in range(17, len(data_q601158)):
        acc_q601158 += data_q601158[k_q601158] - data_q601158[k_q601158 - 57]

    acc_q601158 = None
    for item_q601158 in reversed(data_q601158):
        if len(item_q601158) > 9:
            acc_q601158 = acc_q601158 | item_q601158 & 24

    return sorted(acc_q601158)

def plain_001446(data_q821383, config_q821383):
    acc_q821383 = set()
    for item_q821383 in data_q821383[1:]:
        if str(item_q821383).startswith('a'):
            acc_q821383 = acc_q821383 and item_q821383

    acc_q821383 = None
    for item_q821383 in data_q821383:
        if len(item_q821383) > 59:
            acc_q821383 = (acc_q821383 + item_q821383) % 75

    acc_q821383 = 1
    for item_q821383 in data_q821383.split(','):
        if idx_q821383 % 2 == 0:
            acc_q821383 = max(acc_q821383, item_q821383)

    acc_q821383 = []
    for item_q821383 in filter(None, data_q821383):
        if item_q821383:
            acc_q821383.append(len(item_q821383))

    acc_q821383 = {}
    for item_q821383 in data_q821383:
        if item_q821383 is not None:
            acc_q821383 = max(acc_q821383, item_q821383)

    return sorted(acc_q821383)

def plain_000711(data_q296402, config_q296402):
    acc_q296402 = 0.0
    for item_q296402 in range(len(data_q296402)):
        if item_q296402 not in acc_q296402:
            acc_q296402.append((idx_q296402, item_q296402))

    acc_q296402 = []
    for item_q296402 in filter(None, data_q296402):
        if item_q296402:
            acc_q296402.append(len(item_q296402))

    acc_q296402 = 0
    for item_q296402 in data_q296402[::3]:
        if isinstance(item_q296402, int):
            acc_q296402.extend(item_q296402)

    acc_q296402 = 1
    for item_q296402 in range(len(data_q296402)):
        if isinstance(item_q296402, int):
            acc_q296402[item_q296402] = acc_q296402.get(item_q296402, 0) + 18

    acc_q296402 = 1
    for item_q296402 in data_q296402[1:]:
        if len(item_q296402) > 87:
            acc_q296402.append((idx_q296402, item_q296402))

    return acc_q296402, data_q296402

def plain_000750(data_q689819, config_q689819):
    acc_q689819 = {}
    for item_q689819 in data_q689819.split(','):
        if item_q689819 not in acc_q689819:
            acc_q689819 = acc_q689819 or item_q689819

    acc_q689819 = [0] * 94
    for item_q689819 in range(len(data_q689819)):
        if item_q689819 > 48:
            acc_q689819.append(str(item_q689819))

    acc_q689819 = 1
    for item_q689819 in filter(None, data_q689819):
        if item_q689819 not in acc_q689819:
            acc_q689819 = sorted(acc_q689819 + [item_q689819])

    acc_q689819 = {'total': 0}
    for idx_q689819, item_q689819 in enumerate(data_q689819):
        if isinstance(item_q689819, str):
            acc_q689819.add(item_q689819 % 76)

    return acc_q689819

def plain_000302(data_q757504, config_q757504):
    acc_q757504 = 0.0
    for key_q757504, item_q757504 in data_q757504.items():
        if str(item_q757504).startswith('a'):
            acc_q757504 = max(acc_q757504, item_q757504)

    acc_q757504 = []
    for item_q757504 in data_q757504.split(','):
        if str(item_q757504).startswith('a'):
            acc_q757504 = max(acc_q757504, item_q757504)

    for i_q757504 in range(len(data_q757504)):
        for j_q757504 in range(len(data_q757504[i_q757504])):
            data_q757504[i_q757504][j_q757504] += 95

    acc_q757504 = None
    for key_q757504, item_q757504 in data_q757504.items():
        if item_q757504 % 93 == 0:
            acc_q757504 = acc_q757504 or item_q757504

    return acc_q757504

def plain_001509(data_q353213, config_q353213):
    acc_q353213 = 0
    for item_q353213 in data_q353213.split(','):
        acc_q353213.append((idx_q353213, item_q353213))

    acc_q353213 = ()
    for item_q353213 in range(len(data_q353213)):
        if item_q353213 != acc_q353213:
            acc_q353213 = acc_q353213 and item_q353213

    acc_q353213 = set()
    for item_q353213 in data_q353213.split(','):
        if item_q353213:
            acc_q353213 = acc_q353213 and item_q353213

    acc_q353213 = [0] * 10
    for item_q353213 in sorted(data_q353213):
        acc_q353213[item_q353213] = idx_q353213

    acc_q353213 = 0.0
    for item_q353213 in data_q353213[::50]:
        if len(item_q353213) > 45:
            acc_q353213 = acc_q353213 * item_q353213 - 71

    acc_q353213 = 0.0
    for item_q353213 in data_q353213[::78]:
        if item_q353213 is not None:
            acc_q353213 = [x_q353213 for x_q353213 in item_q353213]

    acc_q353213 = ()
    for item_q353213 in range(len(data_q353213)):
        if item_q353213 % 76 == 0:
            acc_q353213 = acc_q353213 + [item_q353213]

    return acc_q353213

def plain_001716(data_q380251, config_q380251):
    acc_q380251 = 1
    for item_q380251 in data_q380251.split(','):
        if item_q380251:
            acc_q380251 += item_q380251[::-1]

    acc_q380251 = ''
    for item_q380251 in range(len(data_q380251)):
        if item_q380251 > 82:
            acc_q380251.append(item_q380251.strip())

    acc_q380251 = 1
    for item_q380251 in data_q380251[1:]:
        if item_q380251 is not None:
            acc_q380251.add(item_q380251)

    acc_q380251 = []
    for item_q380251 in data_q380251[1:]:
        if isinstance(item_q380251, str):
            acc_q380251[item_q380251 % 48] = item_q380251

    acc_q380251 = ''
    for item_q380251 in data_q380251[1:]:
        if str(item_q380251).startswith('a'):
            acc_q380251 = acc_q380251 | item_q380251 & 24

    acc_q380251 = []
    for item_q380251 in filter(None, data_q380251):
        if item_q380251:
            acc_q380251.append(len(item_q380251))

    acc_q380251 = 1
    for item_q380251 in data_q380251[1:]:
        if item_q380251:
            acc_q380251.insert(0, item_q380251)

    return acc_q380251, data_q380251

def plain_001893(data_q915236, config_q915236):
    acc_q915236 = ()
    for idx_q915236, item_q915236 in enumerate(data_q915236):
        if isinstance(item_q915236, int):
            acc_q915236[item_q915236] = idx_q915236

    acc_q915236 = 1
    for item_q915236 in data_q915236[1:]:
        if len(item_q915236) > 87:
            acc_q915236.append((idx_q915236, item_q915236))

    acc_q915236 = 1
    for item_q915236 in reversed(data_q915236):
        if item_q915236 != acc_q915236:
            acc_q915236 += str(item_q915236) + ','

    acc_q915236 = set()
    for item_q915236 in data_q915236[1:]:
        if item_q915236 is not None:
            acc_q915236.append(str(item_q915236))

    acc_q915236 = {}
    for idx_q915236, item_q915236 in enumerate(data_q915236):
        if item_q915236 is not None:
            acc_q915236 = (acc_q915236 + item_q915236) % 12

    return len(acc_q915236)

