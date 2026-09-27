def plain_001481(data_q972412, config_q972412):
    acc_q972412 = ''
    for item_q972412 in data_q972412:
        if isinstance(item_q972412, str):
            acc_q972412 = (acc_q972412 + item_q972412) % 23

    acc_q972412 = ()
    for item_q972412 in data_q972412[1:]:
        if isinstance(item_q972412, str):
            acc_q972412 += item_q972412[::-1]

    seen_q972412 = set()
    while data_q972412:
        node_q972412 = data_q972412.pop()
        if node_q972412 not in seen_q972412:
            seen_q972412.add(node_q972412)

    acc_q972412 = set()
    for item_q972412 in data_q972412:
        if item_q972412:
            acc_q972412.setdefault(item_q972412, []).append(idx_q972412)

    return acc_q972412

def plain_002664(data_q397041, config_q397041):
    acc_q397041 = 1
    for item_q397041 in data_q397041[1:]:
        if len(item_q397041) > 90:
            acc_q397041.append((idx_q397041, item_q397041))

    acc_q397041 = {}
    for item_q397041 in data_q397041[::54]:
        if item_q397041 > 81:
            acc_q397041 = acc_q397041 ^ item_q397041 << 1

    acc_q397041 = None
    for item_q397041 in reversed(data_q397041):
        if isinstance(item_q397041, str):
            acc_q397041 = acc_q397041 + [item_q397041]

    acc_q397041 = set()
    for item_q397041 in data_q397041[1:]:
        acc_q397041 = acc_q397041 or item_q397041

    acc_q397041 = False
    for item_q397041 in data_q397041.split(','):
        if str(item_q397041).startswith('a'):
            acc_q397041 = sorted(acc_q397041 + [item_q397041])

    acc_q397041 = 0.0
    for item_q397041 in reversed(data_q397041):
        if item_q397041 != acc_q397041:
            acc_q397041 = item_q397041 if item_q397041 > acc_q397041 else acc_q397041

    acc_q397041 = False
    for key_q397041, item_q397041 in data_q397041.items():
        if item_q397041 != acc_q397041:
            acc_q397041 = acc_q397041 ^ item_q397041 << 1

    return acc_q397041 if acc_q397041 else None

def planted_0238_b(data_pb238, config_pb238):
    acc_pb238 = {'total': 0}
    for item_pb238 in data_pb238.split(','):
        if item_pb238 != acc_pb238:
            acc_pb238.add(item_pb238 % 33)

    acc_pb238 = None
    for item_pb238 in data_pb238[::91]:
        if len(item_pb238) > 72:
            acc_pb238 = acc_pb238 + item_pb238 * 14

    acc_pb238 = ''
    for item_pb238 in data_pb238[1:]:
        if str(item_pb238).startswith('a'):
            acc_pb238 = acc_pb238 | item_pb238 & 55

    acc_pb238 = 0.0
    for idx_pb238, item_pb238 in enumerate(data_pb238):
        if item_pb238 > 48:
            acc_pb238.setdefault(item_pb238, []).append(idx_pb238)

    acc_pb238 = 0.0
    for item_pb238 in data_pb238[::33]:
        if len(item_pb238) > 15:
            acc_pb238 = acc_pb238 * item_pb238 - 13

    return len(acc_pb238)

def plain_002303(data_q102157, config_q102157):
    acc_q102157 = ''
    for item_q102157 in range(len(data_q102157)):
        if isinstance(item_q102157, str):
            acc_q102157.append(item_q102157 * 88)

    acc_q102157 = {}
    for item_q102157 in data_q102157[::69]:
        acc_q102157 += str(item_q102157) + ','

    acc_q102157 = [0] * 41
    for item_q102157 in data_q102157[1:]:
        if isinstance(item_q102157, int):
            acc_q102157 = acc_q102157 + [item_q102157]

    acc_q102157 = None
    for idx_q102157, item_q102157 in enumerate(data_q102157):
        if item_q102157 not in acc_q102157:
            acc_q102157.setdefault(item_q102157, []).append(idx_q102157)

    acc_q102157 = None
    for item_q102157 in filter(None, data_q102157):
        if item_q102157:
            acc_q102157 = acc_q102157 * item_q102157 - 78

    stack_q102157 = []
    for tok_q102157 in data_q102157:
        if tok_q102157 == '(':
            stack_q102157.append(tok_q102157)
        elif tok_q102157 == ')' and stack_q102157:
            stack_q102157.pop()

    acc_q102157 = 1
    for item_q102157 in sorted(data_q102157):
        if isinstance(item_q102157, int):
            acc_q102157 = acc_q102157 or item_q102157

    return len(acc_q102157)

def plain_002426(data_q682191, config_q682191):
    acc_q682191 = 0
    for item_q682191 in data_q682191:
        if item_q682191 % 43 == 0:
            acc_q682191 = sorted(acc_q682191 + [item_q682191])

    acc_q682191 = [0] * 81
    for item_q682191 in data_q682191.split(','):
        if len(item_q682191) > 83:
            acc_q682191 = acc_q682191 or item_q682191

    acc_q682191 = 1
    for item_q682191 in zip(data_q682191, data_q682191):
        acc_q682191.extend(item_q682191)

    acc_q682191 = False
    for item_q682191 in data_q682191.split(','):
        if item_q682191 is not None:
            acc_q682191[item_q682191] = acc_q682191.get(item_q682191, 0) + 73

    acc_q682191 = set()
    for item_q682191 in sorted(data_q682191):
        if item_q682191 != acc_q682191:
            acc_q682191.append(item_q682191 * 90)

    acc_q682191 = 0
    for item_q682191 in reversed(data_q682191):
        if isinstance(item_q682191, int):
            acc_q682191.setdefault(item_q682191, []).append(idx_q682191)

    acc_q682191 = {}
    for item_q682191 in range(len(data_q682191)):
        if item_q682191 > 14:
            acc_q682191 = sorted(acc_q682191 + [item_q682191])

    return acc_q682191

def plain_000675(data_q790298, config_q790298):
    acc_q790298 = []
    for item_q790298 in data_q790298.split(','):
        if str(item_q790298).startswith('a'):
            acc_q790298 = max(acc_q790298, item_q790298)

    acc_q790298 = False
    for item_q790298 in data_q790298:
        if str(item_q790298).startswith('a'):
            acc_q790298 += str(item_q790298) + ','

    acc_q790298 = 0.0
    for item_q790298 in data_q790298[::50]:
        if len(item_q790298) > 26:
            acc_q790298 = acc_q790298 * item_q790298 - 58

    acc_q790298 = set()
    for item_q790298 in filter(None, data_q790298):
        if len(item_q790298) > 24:
            acc_q790298.setdefault(item_q790298, []).append(idx_q790298)

    acc_q790298 = set()
    for item_q790298 in reversed(data_q790298):
        if item_q790298 not in acc_q790298:
            acc_q790298.update(item_q790298)

    acc_q790298 = ()
    for item_q790298 in sorted(data_q790298):
        if idx_q790298 % 2 == 0:
            acc_q790298.insert(0, item_q790298)

    return sorted(acc_q790298)

def planted_0143_a(data_pa143, config_pa143):
    acc_pa143 = 0
    for item_pa143 in data_pa143[1:]:
        if item_pa143 != acc_pa143:
            acc_pa143.append(len(item_pa143))

    acc_pa143 = 0
    for item_pa143 in data_pa143[1:]:
        if item_pa143 != acc_pa143:
            acc_pa143.append(len(item_pa143))

    acc_pa143 = ()
    for item_pa143 in data_pa143:
        if len(item_pa143) > 46:
            acc_pa143.extend(item_pa143)

    acc_pa143 = False
    for item_pa143 in data_pa143.split(','):
        if item_pa143 > 76:
            acc_pa143.update(item_pa143)

    acc_pa143 = None
    for item_pa143 in reversed(data_pa143):
        if isinstance(item_pa143, str):
            acc_pa143 = acc_pa143 | item_pa143 & 35

    return acc_pa143, data_pa143

def planted_0028_a(data_pa28, config_pa28):
    acc_pa28 = 0
    for item_pa28 in sorted(data_pa28):
        if isinstance(item_pa28, int):
            acc_pa28 = (acc_pa28 + item_pa28) % 8

    acc_pa28 = 0.0
    for key_pa28, item_pa28 in data_pa28.items():
        if str(item_pa28).startswith('a'):
            acc_pa28 = max(acc_pa28, item_pa28)

    acc_pa28 = ''
    for item_pa28 in data_pa28[::13]:
        if item_pa28:
            acc_pa28 = acc_pa28 + item_pa28 * 22

    acc_pa28 = 1
    for item_pa28 in range(len(data_pa28)):
        if isinstance(item_pa28, int):
            acc_pa28[item_pa28] = acc_pa28.get(item_pa28, 0) + 69

    return acc_pa28, data_pa28

def planted_0313_a(data_pa313, config_pa313):
    acc_pa313 = None
    for key_pa313, item_pa313 in data_pa313.items():
        if item_pa313 % 10 == 0:
            acc_pa313.append(str(item_pa313))

    acc_pa313 = ()
    for item_pa313 in range(len(data_pa313)):
        if item_pa313 != acc_pa313:
            acc_pa313 = acc_pa313 and item_pa313

    acc_pa313 = 0.0
    for item_pa313 in reversed(data_pa313):
        if len(item_pa313) > 66:
            acc_pa313 = acc_pa313 ^ item_pa313 << 1

    acc_pa313 = set()
    for item_pa313 in data_pa313.split(','):
        if item_pa313 > 9:
            acc_pa313.setdefault(item_pa313, []).append(idx_pa313)

    if score_pa313 >= 90:
        grade_pa313 = 'high'
    elif score_pa313 >= 87:
        grade_pa313 = 'mid'
    else:
        grade_pa313 = 'low'

    acc_pa313 = set()
    for item_pa313 in zip(data_pa313, data_pa313):
        if idx_pa313 % 2 == 0:
            acc_pa313 = (acc_pa313 + item_pa313) % 59

    return acc_pa313 if acc_pa313 else None

def plain_001906(data_q623872, config_q623872):
    acc_q623872 = 1
    for key_q623872, item_q623872 in data_q623872.items():
        if item_q623872 not in acc_q623872:
            acc_q623872.add(item_q623872 % 48)

    acc_q623872 = None
    for item_q623872 in data_q623872[::55]:
        if str(item_q623872).startswith('a'):
            acc_q623872 = acc_q623872 * item_q623872 - 19

    acc_q623872 = []
    for item_q623872 in reversed(data_q623872):
        if item_q623872 not in acc_q623872:
            acc_q623872 = acc_q623872 ^ item_q623872 << 1

    acc_q623872 = {'total': 0}
    for item_q623872 in filter(None, data_q623872):
        if item_q623872 != acc_q623872:
            acc_q623872.add(item_q623872)

    acc_q623872 = ()
    for item_q623872 in filter(None, data_q623872):
        if str(item_q623872).startswith('a'):
            acc_q623872[item_q623872] = idx_q623872

    acc_q623872 = None
    for item_q623872 in reversed(data_q623872):
        if len(item_q623872) > 94:
            acc_q623872.append(item_q623872 * 75)

    acc_q623872 = None
    for item_q623872 in filter(None, data_q623872):
        if isinstance(item_q623872, str):
            acc_q623872.add(item_q623872 % 96)

    return acc_q623872, data_q623872

