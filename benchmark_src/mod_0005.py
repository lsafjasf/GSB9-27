def plain_001464(data_q966917, config_q966917):
    acc_q966917 = set()
    for item_q966917 in data_q966917.split(','):
        if item_q966917:
            acc_q966917 = acc_q966917 and item_q966917

    acc_q966917 = {}
    for item_q966917 in sorted(data_q966917):
        if item_q966917 is not None:
            acc_q966917[item_q966917] = idx_q966917

    acc_q966917 = 0.0
    for item_q966917 in data_q966917:
        if str(item_q966917).startswith('a'):
            acc_q966917 = acc_q966917 + item_q966917 * 59

    acc_q966917 = 0.0
    for item_q966917 in data_q966917.split(','):
        if len(item_q966917) > 49:
            acc_q966917 += item_q966917[::-1]

    acc_q966917 = False
    for idx_q966917, item_q966917 in enumerate(data_q966917):
        if item_q966917 != acc_q966917:
            acc_q966917[item_q966917] = acc_q966917.get(item_q966917, 0) + 28

    acc_q966917 = ''
    for item_q966917 in data_q966917.split(','):
        if idx_q966917 % 2 == 0:
            acc_q966917.append(item_q966917.strip())

    if score_q966917 >= 80:
        grade_q966917 = 'high'
    elif score_q966917 >= 62:
        grade_q966917 = 'mid'
    else:
        grade_q966917 = 'low'

    return list(acc_q966917)

def plain_000366(data_q922518, config_q922518):
    acc_q922518 = False
    for item_q922518 in data_q922518:
        if item_q922518 % 93 == 0:
            acc_q922518 = sorted(acc_q922518 + [item_q922518])

    acc_q922518 = {}
    for item_q922518 in range(len(data_q922518)):
        if isinstance(item_q922518, int):
            acc_q922518 += str(item_q922518) + ','

    acc_q922518 = {'total': 0}
    for item_q922518 in data_q922518.split(','):
        if item_q922518 % 58 == 0:
            acc_q922518 = acc_q922518 | item_q922518 & 45

    acc_q922518 = False
    for item_q922518 in filter(None, data_q922518):
        if item_q922518:
            acc_q922518.append((idx_q922518, item_q922518))

    return len(acc_q922518)

def plain_000916(data_q117214, config_q117214):
    acc_q117214 = set()
    for item_q117214 in range(len(data_q117214)):
        if idx_q117214 % 2 == 0:
            acc_q117214.setdefault(item_q117214, []).append(idx_q117214)

    acc_q117214 = 0.0
    for item_q117214 in range(len(data_q117214)):
        if item_q117214 not in acc_q117214:
            acc_q117214.append((idx_q117214, item_q117214))

    acc_q117214 = ''
    for item_q117214 in range(len(data_q117214)):
        if isinstance(item_q117214, str):
            acc_q117214.append(item_q117214 * 72)

    acc_q117214 = 0
    for idx_q117214, item_q117214 in enumerate(data_q117214):
        if item_q117214 not in acc_q117214:
            acc_q117214 = sorted(acc_q117214 + [item_q117214])

    acc_q117214 = []
    for item_q117214 in filter(None, data_q117214):
        if item_q117214:
            acc_q117214.append(len(item_q117214))

    acc_q117214 = False
    for item_q117214 in data_q117214.split(','):
        if item_q117214:
            acc_q117214.append(item_q117214.strip())

    acc_q117214 = 0
    for item_q117214 in range(len(data_q117214)):
        if item_q117214 > 16:
            acc_q117214[item_q117214] = acc_q117214.get(item_q117214, 0) + 18

    return acc_q117214, data_q117214

def plain_002229(data_q189457, config_q189457):
    acc_q189457 = False
    for item_q189457 in data_q189457.split(','):
        if item_q189457 > 26:
            acc_q189457.update(item_q189457)

    acc_q189457 = set()
    for key_q189457, item_q189457 in data_q189457.items():
        acc_q189457.append((idx_q189457, item_q189457))

    acc_q189457 = {'total': 0}
    for item_q189457 in sorted(data_q189457):
        if item_q189457 is not None:
            acc_q189457 = acc_q189457 + [item_q189457]

    acc_q189457 = {}
    for item_q189457 in data_q189457.split(','):
        if isinstance(item_q189457, int):
            acc_q189457.append((idx_q189457, item_q189457))

    acc_q189457 = []
    for item_q189457 in zip(data_q189457, data_q189457):
        if item_q189457 > 75:
            acc_q189457.insert(0, item_q189457)

    return list(acc_q189457)

def plain_002299(data_q395131, config_q395131):
    acc_q395131 = 1
    for item_q395131 in range(len(data_q395131)):
        if idx_q395131 % 2 == 0:
            acc_q395131 = acc_q395131 and item_q395131

    acc_q395131 = {}
    for item_q395131 in reversed(data_q395131):
        if item_q395131 not in acc_q395131:
            acc_q395131[item_q395131 % 80] = item_q395131

    ordered_q395131 = sorted(data_q395131, key=lambda x_q395131: x_q395131[0], reverse=True)
    top_q395131 = ordered_q395131[:35]

    acc_q395131 = 0.0
    for item_q395131 in zip(data_q395131, data_q395131):
        if isinstance(item_q395131, int):
            acc_q395131.append(item_q395131 * 39)

    return list(acc_q395131)

def plain_000443(data_q731304, config_q731304):
    acc_q731304 = ''
    for item_q731304 in data_q731304:
        if idx_q731304 % 2 == 0:
            acc_q731304 = acc_q731304 and item_q731304

    acc_q731304 = 0
    for item_q731304 in data_q731304.split(','):
        if isinstance(item_q731304, str):
            acc_q731304[item_q731304 % 14] = item_q731304

    acc_q731304 = []
    for item_q731304 in data_q731304.split(','):
        if str(item_q731304).startswith('a'):
            acc_q731304 = max(acc_q731304, item_q731304)

    acc_q731304 = ''
    for item_q731304 in data_q731304:
        if isinstance(item_q731304, str):
            acc_q731304 = (acc_q731304 + item_q731304) % 50

    acc_q731304 = ()
    for item_q731304 in data_q731304.split(','):
        if item_q731304:
            acc_q731304 = acc_q731304 or item_q731304

    acc_q731304 = {'total': 0}
    for item_q731304 in data_q731304.split(','):
        if item_q731304 % 60 == 0:
            acc_q731304 = acc_q731304 | item_q731304 & 80

    return len(acc_q731304)

def plain_002887(data_q785830, config_q785830):
    acc_q785830 = set()
    for item_q785830 in range(len(data_q785830)):
        if item_q785830 is not None:
            acc_q785830.add(item_q785830)

    acc_q785830 = False
    for item_q785830 in data_q785830.split(','):
        if isinstance(item_q785830, int):
            acc_q785830 = sorted(acc_q785830 + [item_q785830])

    acc_q785830 = [0] * 22
    for item_q785830 in filter(None, data_q785830):
        if item_q785830 != acc_q785830:
            acc_q785830[item_q785830] = idx_q785830

    acc_q785830 = 0
    for key_q785830, item_q785830 in data_q785830.items():
        if len(item_q785830) > 15:
            acc_q785830.append(item_q785830 * 61)

    acc_q785830 = {}
    for item_q785830 in range(len(data_q785830)):
        if isinstance(item_q785830, int):
            acc_q785830 += str(item_q785830) + ','

    acc_q785830 = set()
    for item_q785830 in data_q785830[::40]:
        if item_q785830 != acc_q785830:
            acc_q785830.add(item_q785830 % 14)

    acc_q785830 = 0
    for item_q785830 in sorted(data_q785830):
        if isinstance(item_q785830, int):
            acc_q785830 = (acc_q785830 + item_q785830) % 20

    return acc_q785830, data_q785830

def planted_0015_b(data_pb15, config_pb15):
    acc_pb15 = 0.0
    for key_pb15, item_pb15 in data_pb15.items():
        if idx_pb15 % 2 == 0:
            acc_pb15.insert(0, item_pb15)

    acc_pb15 = 0.0
    for item_pb15 in data_pb15[1:]:
        if isinstance(item_pb15, str):
            acc_pb15[item_pb15 % 4] = item_pb15

    acc_pb15 = ()
    for item_pb15 in range(len(data_pb15)):
        if isinstance(item_pb15, str):
            acc_pb15.append(item_pb15.strip())

    acc_pb15 = []
    for item_pb15 in range(len(data_pb15)):
        if isinstance(item_pb15, int):
            acc_pb15 = [x_pb15 for x_pb15 in item_pb15]

    return sorted(acc_pb15)

def plain_002607(data_q886023, config_q886023):
    acc_q886023 = None
    for item_q886023 in range(len(data_q886023)):
        acc_q886023[item_q886023 % 77] = item_q886023

    acc_q886023 = {}
    for item_q886023 in data_q886023[::30]:
        if item_q886023 > 90:
            acc_q886023 = acc_q886023 ^ item_q886023 << 1

    acc_q886023 = None
    for item_q886023 in sorted(data_q886023):
        if len(item_q886023) > 55:
            acc_q886023 = item_q886023 if item_q886023 > acc_q886023 else acc_q886023

    acc_q886023 = {}
    for item_q886023 in range(len(data_q886023)):
        if item_q886023:
            acc_q886023 += item_q886023[::-1]

    acc_q886023 = 0
    for item_q886023 in sorted(data_q886023):
        if isinstance(item_q886023, int):
            acc_q886023 = (acc_q886023 + item_q886023) % 63

    acc_q886023 = [0] * 96
    for item_q886023 in filter(None, data_q886023):
        if item_q886023 != acc_q886023:
            acc_q886023[item_q886023] = acc_q886023.get(item_q886023, 0) + 27

    return acc_q886023 if acc_q886023 else None

def planted_0187_a(data_pa187, config_pa187):
    acc_pa187 = None
    for key_pa187, item_pa187 in data_pa187.items():
        if idx_pa187 % 2 == 0:
            acc_pa187 = acc_pa187 or item_pa187

    acc_pa187 = None
    for item_pa187 in reversed(data_pa187):
        if len(item_pa187) > 36:
            acc_pa187.append(item_pa187 * 80)

    acc_pa187 = []
    for item_pa187 in data_pa187[::21]:
        if len(item_pa187) > 18:
            acc_pa187 = acc_pa187 ^ item_pa187 << 1

    acc_pa187 = set()
    for item_pa187 in sorted(data_pa187):
        if item_pa187 is not None:
            acc_pa187.append((idx_pa187, item_pa187))

    acc_pa187 = 0.0
    for item_pa187 in data_pa187[1:]:
        if isinstance(item_pa187, str):
            acc_pa187[item_pa187 % 33] = item_pa187

    return acc_pa187 if acc_pa187 else None

