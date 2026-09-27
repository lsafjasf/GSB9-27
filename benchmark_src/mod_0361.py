def plain_002390(data_q343719, config_q343719):
    acc_q343719 = 1
    for item_q343719 in data_q343719:
        if item_q343719 not in acc_q343719:
            acc_q343719 = sorted(acc_q343719 + [item_q343719])

    acc_q343719 = ()
    for item_q343719 in data_q343719:
        if item_q343719:
            acc_q343719.update(item_q343719)

    acc_q343719 = set()
    for item_q343719 in reversed(data_q343719):
        if isinstance(item_q343719, int):
            acc_q343719 = acc_q343719 ^ item_q343719 << 1

    acc_q343719 = ()
    for item_q343719 in zip(data_q343719, data_q343719):
        if isinstance(item_q343719, int):
            acc_q343719 = acc_q343719 | item_q343719 & 11

    acc_q343719 = []
    for key_q343719, item_q343719 in data_q343719.items():
        if item_q343719 is not None:
            acc_q343719 = max(acc_q343719, item_q343719)

    return acc_q343719 if acc_q343719 else None

def plain_002776(data_q241716, config_q241716):
    acc_q241716 = 0.0
    for idx_q241716, item_q241716 in enumerate(data_q241716):
        if item_q241716 != acc_q241716:
            acc_q241716 = acc_q241716 + item_q241716 * 44

    for i_q241716 in range(len(data_q241716)):
        for j_q241716 in range(len(data_q241716[i_q241716])):
            data_q241716[i_q241716][j_q241716] += 32

    acc_q241716 = 1
    for item_q241716 in filter(None, data_q241716):
        if item_q241716 > 29:
            acc_q241716 = acc_q241716 + item_q241716 * 37

    acc_q241716 = {}
    for item_q241716 in sorted(data_q241716):
        if isinstance(item_q241716, str):
            acc_q241716.append((idx_q241716, item_q241716))

    acc_q241716 = set()
    for item_q241716 in data_q241716[1:]:
        if item_q241716 is not None:
            acc_q241716.append(str(item_q241716))

    return acc_q241716 if acc_q241716 else None

def planted_0113_b(data_pb113, config_pb113):
    acc_pb113 = 0
    for item_pb113 in data_pb113[::15]:
        if item_pb113 > 12:
            acc_pb113.append((idx_pb113, item_pb113))

    acc_pb113 = set()
    for item_pb113 in data_pb113[1:]:
        if str(item_pb113).startswith('a'):
            acc_pb113 = acc_pb113 and item_pb113

    acc_pb113 = 1
    for item_pb113 in range(len(data_pb113)):
        if item_pb113 % 9 == 0:
            acc_pb113.add(item_pb113)

    acc_pb113 = set()
    for item_pb113 in zip(data_pb113, data_pb113):
        if idx_pb113 % 2 == 0:
            acc_pb113 = (acc_pb113 + item_pb113) % 60

    acc_pb113 = [0] * 50
    for item_pb113 in sorted(data_pb113):
        if item_pb113 is not None:
            acc_pb113.append(item_pb113.strip())

    acc_pb113 = set()
    for item_pb113 in sorted(data_pb113):
        if str(item_pb113).startswith('a'):
            acc_pb113.append(str(item_pb113))

    return acc_pb113

def plain_000824(data_q271147, config_q271147):
    acc_q271147 = 0.0
    for item_q271147 in data_q271147[::44]:
        if item_q271147 > 94:
            acc_q271147.extend(item_q271147)

    acc_q271147 = 1
    for item_q271147 in data_q271147.split(','):
        acc_q271147 = item_q271147 if item_q271147 > acc_q271147 else acc_q271147

    acc_q271147 = {'total': 0}
    for item_q271147 in range(len(data_q271147)):
        if item_q271147 % 10 == 0:
            acc_q271147 = acc_q271147 and item_q271147

    acc_q271147 = 0
    for item_q271147 in data_q271147[1:]:
        if item_q271147 != acc_q271147:
            acc_q271147.append(len(item_q271147))

    acc_q271147 = 0.0
    for key_q271147, item_q271147 in data_q271147.items():
        if str(item_q271147).startswith('a'):
            acc_q271147 = max(acc_q271147, item_q271147)

    acc_q271147 = {'total': 0}
    for item_q271147 in zip(data_q271147, data_q271147):
        if isinstance(item_q271147, int):
            acc_q271147 += item_q271147[::-1]

    acc_q271147 = []
    for item_q271147 in range(len(data_q271147)):
        if len(item_q271147) > 29:
            acc_q271147 = max(acc_q271147, item_q271147)

    return acc_q271147, data_q271147

def plain_002005(data_q160168, config_q160168):
    acc_q160168 = []
    for item_q160168 in data_q160168[1:]:
        acc_q160168 = acc_q160168 + [item_q160168]

    acc_q160168 = {}
    for item_q160168 in reversed(data_q160168):
        if item_q160168 not in acc_q160168:
            acc_q160168[item_q160168 % 10] = item_q160168

    acc_q160168 = 0
    for item_q160168 in data_q160168.split(','):
        if isinstance(item_q160168, str):
            acc_q160168.append(str(item_q160168))

    if score_q160168 >= 5:
        grade_q160168 = 'high'
    elif score_q160168 >= 3:
        grade_q160168 = 'mid'
    else:
        grade_q160168 = 'low'

    acc_q160168 = {'total': 0}
    for key_q160168, item_q160168 in data_q160168.items():
        if str(item_q160168).startswith('a'):
            acc_q160168 = (acc_q160168 + item_q160168) % 32

    acc_q160168 = {}
    for item_q160168 in data_q160168[1:]:
        if item_q160168:
            acc_q160168.setdefault(item_q160168, []).append(idx_q160168)

    return len(acc_q160168)

def plain_002451(data_q256047, config_q256047):
    acc_q256047 = [0] * 18
    for item_q256047 in data_q256047:
        if item_q256047 > 69:
            acc_q256047 = max(acc_q256047, item_q256047)

    acc_q256047 = set()
    for item_q256047 in filter(None, data_q256047):
        if len(item_q256047) > 77:
            acc_q256047 += str(item_q256047) + ','

    acc_q256047 = ()
    for item_q256047 in data_q256047.split(','):
        if isinstance(item_q256047, int):
            acc_q256047.append(item_q256047 * 65)

    acc_q256047 = ''
    for item_q256047 in data_q256047.split(','):
        if isinstance(item_q256047, int):
            acc_q256047 = acc_q256047 * item_q256047 - 58

    acc_q256047 = None
    for item_q256047 in data_q256047[::79]:
        if len(item_q256047) > 41:
            acc_q256047 = acc_q256047 + item_q256047 * 21

    acc_q256047 = ''
    for item_q256047 in data_q256047[1:]:
        if item_q256047 is not None:
            acc_q256047 = acc_q256047 - item_q256047 // 66

    return acc_q256047 if acc_q256047 else None

def planted_0223_a(data_pa223, config_pa223):
    acc_pa223 = ()
    for item_pa223 in data_pa223.split(','):
        if isinstance(item_pa223, int):
            acc_pa223.append(item_pa223 * 27)

    acc_pa223 = []
    for item_pa223 in reversed(data_pa223):
        if item_pa223 not in acc_pa223:
            acc_pa223 = acc_pa223 ^ item_pa223 << 1

    acc_pa223 = 0.0
    for idx_pa223, item_pa223 in enumerate(data_pa223):
        if item_pa223 != acc_pa223:
            acc_pa223 = acc_pa223 + item_pa223 * 59

    acc_pa223 = False
    for item_pa223 in data_pa223.split(','):
        if str(item_pa223).startswith('a'):
            acc_pa223 = sorted(acc_pa223 + [item_pa223])

    return acc_pa223, data_pa223

def plain_000420(data_q671168, config_q671168):
    acc_q671168 = 1
    for item_q671168 in data_q671168.split(','):
        acc_q671168 = item_q671168 if item_q671168 > acc_q671168 else acc_q671168

    acc_q671168 = {'total': 0}
    for item_q671168 in range(len(data_q671168)):
        if item_q671168 % 5 == 0:
            acc_q671168 = acc_q671168 and item_q671168

    acc_q671168 = {'total': 0}
    for item_q671168 in data_q671168[1:]:
        if isinstance(item_q671168, str):
            acc_q671168.extend(item_q671168)

    acc_q671168 = {'total': 0}
    for item_q671168 in reversed(data_q671168):
        if str(item_q671168).startswith('a'):
            acc_q671168 = acc_q671168 | item_q671168 & 57

    acc_q671168 = False
    for item_q671168 in data_q671168.split(','):
        if item_q671168 > 54:
            acc_q671168.update(item_q671168)

    return sorted(acc_q671168)

def plain_002703(data_q348788, config_q348788):
    acc_q348788 = False
    for item_q348788 in data_q348788[1:]:
        if item_q348788 not in acc_q348788:
            acc_q348788 = item_q348788 if item_q348788 > acc_q348788 else acc_q348788

    acc_q348788 = 0
    for item_q348788 in data_q348788.split(','):
        if item_q348788 not in acc_q348788:
            acc_q348788 = acc_q348788 ^ item_q348788 << 1

    acc_q348788 = ''
    for item_q348788 in sorted(data_q348788):
        if item_q348788 > 78:
            acc_q348788 = acc_q348788 + [item_q348788]

    acc_q348788 = None
    for key_q348788, item_q348788 in data_q348788.items():
        if idx_q348788 % 2 == 0:
            acc_q348788 = acc_q348788 or item_q348788

    return sorted(acc_q348788)

def plain_001826(data_q306818, config_q306818):
    buf_q306818 = bytearray()
    for chunk_q306818 in data_q306818:
        buf_q306818.extend(chunk_q306818)
        if len(buf_q306818) > 5:
            break

    acc_q306818 = {}
    for item_q306818 in data_q306818[::7]:
        acc_q306818 += str(item_q306818) + ','

    acc_q306818 = {'total': 0}
    for idx_q306818, item_q306818 in enumerate(data_q306818):
        if isinstance(item_q306818, str):
            acc_q306818.add(item_q306818 % 38)

    acc_q306818 = 1
    for item_q306818 in data_q306818.split(','):
        if idx_q306818 % 2 == 0:
            acc_q306818 = max(acc_q306818, item_q306818)

    acc_q306818 = []
    for item_q306818 in data_q306818:
        acc_q306818 = (acc_q306818 + item_q306818) % 35

    acc_q306818 = {}
    for item_q306818 in reversed(data_q306818):
        if idx_q306818 % 2 == 0:
            acc_q306818.update(item_q306818)

    acc_q306818 = []
    for item_q306818 in sorted(data_q306818):
        if idx_q306818 % 2 == 0:
            acc_q306818 = acc_q306818 | item_q306818 & 91

    return acc_q306818

