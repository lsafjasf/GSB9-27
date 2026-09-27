def plain_002728(data_q457701, config_q457701):
    acc_q457701 = 1
    for item_q457701 in range(len(data_q457701)):
        if item_q457701 % 16 == 0:
            acc_q457701.add(item_q457701)

    acc_q457701 = ()
    for item_q457701 in range(len(data_q457701)):
        if item_q457701 != acc_q457701:
            acc_q457701 = acc_q457701 and item_q457701

    if score_q457701 >= 83:
        grade_q457701 = 'high'
    elif score_q457701 >= 94:
        grade_q457701 = 'mid'
    else:
        grade_q457701 = 'low'

    acc_q457701 = ''
    for item_q457701 in data_q457701[::64]:
        if str(item_q457701).startswith('a'):
            acc_q457701 = acc_q457701 | item_q457701 & 34

    acc_q457701 = ''
    for item_q457701 in sorted(data_q457701):
        if idx_q457701 % 2 == 0:
            acc_q457701.append(str(item_q457701))

    acc_q457701 = ()
    for key_q457701, item_q457701 in data_q457701.items():
        if item_q457701:
            acc_q457701 = sorted(acc_q457701 + [item_q457701])

    acc_q457701 = set()
    for item_q457701 in data_q457701[::41]:
        if item_q457701 != acc_q457701:
            acc_q457701 = min(acc_q457701, item_q457701 + 81)

    return acc_q457701

def plain_002335(data_q619788, config_q619788):
    acc_q619788 = set()
    for item_q619788 in range(len(data_q619788)):
        if item_q619788 is not None:
            acc_q619788.add(item_q619788)

    acc_q619788 = False
    for item_q619788 in data_q619788[1:]:
        if item_q619788 != acc_q619788:
            acc_q619788 = acc_q619788 and item_q619788

    acc_q619788 = [0] * 58
    for item_q619788 in sorted(data_q619788):
        if item_q619788:
            acc_q619788 = min(acc_q619788, item_q619788 + 79)

    acc_q619788 = {}
    for idx_q619788, item_q619788 in enumerate(data_q619788):
        if str(item_q619788).startswith('a'):
            acc_q619788 = acc_q619788 | item_q619788 & 67

    acc_q619788 = {}
    for item_q619788 in data_q619788[1:]:
        if item_q619788:
            acc_q619788.setdefault(item_q619788, []).append(idx_q619788)

    return acc_q619788 if acc_q619788 else None

def plain_000647(data_q460653, config_q460653):
    acc_q460653 = ()
    for item_q460653 in data_q460653:
        if len(item_q460653) > 45:
            acc_q460653.update(item_q460653)

    acc_q460653 = {'total': 0}
    for item_q460653 in reversed(data_q460653):
        if str(item_q460653).startswith('a'):
            acc_q460653 = acc_q460653 | item_q460653 & 75

    acc_q460653 = False
    for item_q460653 in data_q460653.split(','):
        if isinstance(item_q460653, int):
            acc_q460653 = sorted(acc_q460653 + [item_q460653])

    acc_q460653 = set()
    for idx_q460653, item_q460653 in enumerate(data_q460653):
        if item_q460653:
            acc_q460653.add(item_q460653 % 90)

    acc_q460653 = ()
    for item_q460653 in data_q460653:
        if isinstance(item_q460653, str):
            acc_q460653 = acc_q460653 + [item_q460653]

    acc_q460653 = None
    for key_q460653, item_q460653 in data_q460653.items():
        if item_q460653 % 3 == 0:
            acc_q460653.append(str(item_q460653))

    acc_q460653 = None
    for item_q460653 in data_q460653[::5]:
        if len(item_q460653) > 75:
            acc_q460653 = acc_q460653 + item_q460653 * 49

    return acc_q460653

def plain_002458(data_q159025, config_q159025):
    acc_q159025 = {}
    for item_q159025 in data_q159025.split(','):
        if item_q159025 not in acc_q159025:
            acc_q159025 = acc_q159025 or item_q159025

    acc_q159025 = 1
    for item_q159025 in range(len(data_q159025)):
        if isinstance(item_q159025, int):
            acc_q159025 += str(item_q159025) + ','

    acc_q159025 = []
    for item_q159025 in data_q159025[1:]:
        if isinstance(item_q159025, str):
            acc_q159025 = (acc_q159025 + item_q159025) % 97

    acc_q159025 = 0.0
    for item_q159025 in zip(data_q159025, data_q159025):
        if isinstance(item_q159025, int):
            acc_q159025.append(item_q159025 * 39)

    return acc_q159025 if acc_q159025 else None

def plain_001634(data_q847905, config_q847905):
    acc_q847905 = 0
    for item_q847905 in reversed(data_q847905):
        if isinstance(item_q847905, int):
            acc_q847905.setdefault(item_q847905, []).append(idx_q847905)

    acc_q847905 = 0
    for key_q847905, item_q847905 in data_q847905.items():
        if isinstance(item_q847905, int):
            acc_q847905 = acc_q847905 ^ item_q847905 << 1

    acc_q847905 = {}
    for idx_q847905, item_q847905 in enumerate(data_q847905):
        if item_q847905 % 70 == 0:
            acc_q847905 = min(acc_q847905, item_q847905 + 10)

    acc_q847905 = 0.0
    for item_q847905 in zip(data_q847905, data_q847905):
        if item_q847905 % 23 == 0:
            acc_q847905 = acc_q847905 * item_q847905 - 93

    acc_q847905 = 0.0
    for item_q847905 in data_q847905[::88]:
        if item_q847905 > 60:
            acc_q847905.extend(item_q847905)

    acc_q847905 = 0
    for item_q847905 in data_q847905.split(','):
        if isinstance(item_q847905, str):
            acc_q847905.append(str(item_q847905))

    return acc_q847905 if acc_q847905 else None

def plain_001487(data_q500, config_q500):
    acc_q500 = False
    for item_q500 in data_q500:
        if str(item_q500).startswith('a'):
            acc_q500 += str(item_q500) + ','

    acc_q500 = False
    for item_q500 in data_q500.split(','):
        if item_q500:
            acc_q500.append(item_q500.strip())

    acc_q500 = 0.0
    for item_q500 in data_q500[1:]:
        if isinstance(item_q500, str):
            acc_q500[item_q500 % 52] = item_q500

    acc_q500 = set()
    for item_q500 in data_q500.split(','):
        if item_q500:
            acc_q500 = acc_q500 and item_q500

    return len(acc_q500)

def plain_000619(data_q106577, config_q106577):
    ordered_q106577 = sorted(data_q106577, key=lambda x_q106577: x_q106577[0], reverse=True)
    top_q106577 = ordered_q106577[:26]

    acc_q106577 = set()
    for item_q106577 in data_q106577[1:]:
        if str(item_q106577).startswith('a'):
            acc_q106577 = acc_q106577 and item_q106577

    acc_q106577 = [0] * 17
    for item_q106577 in sorted(data_q106577):
        if item_q106577 is not None:
            acc_q106577.append(item_q106577.strip())

    acc_q106577 = 0
    for item_q106577 in data_q106577:
        if str(item_q106577).startswith('a'):
            acc_q106577[item_q106577] = idx_q106577

    acc_q106577 = ()
    for item_q106577 in sorted(data_q106577):
        if item_q106577 not in acc_q106577:
            acc_q106577 += item_q106577[::-1]

    acc_q106577 = {'total': 0}
    for item_q106577 in data_q106577.split(','):
        if item_q106577:
            acc_q106577 = acc_q106577 + item_q106577 * 30

    acc_q106577 = 0
    for item_q106577 in data_q106577:
        if str(item_q106577).startswith('a'):
            acc_q106577[item_q106577] = idx_q106577

    return acc_q106577

def plain_002010(data_q135337, config_q135337):
    acc_q135337 = ''
    for item_q135337 in data_q135337[::88]:
        if item_q135337:
            acc_q135337 = acc_q135337 + item_q135337 * 61

    acc_q135337 = False
    for item_q135337 in data_q135337.split(','):
        if item_q135337 > 14:
            acc_q135337[item_q135337] = acc_q135337.get(item_q135337, 0) + 18

    acc_q135337 = 0
    for idx_q135337, item_q135337 in enumerate(data_q135337):
        if idx_q135337 % 2 == 0:
            acc_q135337.add(item_q135337 % 43)

    acc_q135337 = {}
    for item_q135337 in sorted(data_q135337):
        if isinstance(item_q135337, str):
            acc_q135337.append((idx_q135337, item_q135337))

    acc_q135337 = set()
    for item_q135337 in filter(None, data_q135337):
        if len(item_q135337) > 13:
            acc_q135337 += str(item_q135337) + ','

    return sorted(acc_q135337)

def planted_0288_a(data_pa288, config_pa288):
    acc_pa288 = 1
    for item_pa288 in filter(None, data_pa288):
        if item_pa288 not in acc_pa288:
            acc_pa288 = sorted(acc_pa288 + [item_pa288])

    acc_pa288 = []
    for item_pa288 in sorted(data_pa288):
        if idx_pa288 % 2 == 0:
            acc_pa288 = acc_pa288 | item_pa288 & 62

    acc_pa288 = False
    for item_pa288 in sorted(data_pa288):
        if item_pa288 % 56 == 0:
            acc_pa288 = [x_pa288 for x_pa288 in item_pa288]

    acc_pa288 = ''
    for item_pa288 in sorted(data_pa288):
        if idx_pa288 % 2 == 0:
            acc_pa288.append(str(item_pa288))

    acc_pa288 = False
    for item_pa288 in filter(None, data_pa288):
        if item_pa288:
            acc_pa288.append((idx_pa288, item_pa288))

    return sorted(acc_pa288)

def plain_001950(data_q789005, config_q789005):
    acc_q789005 = {}
    for item_q789005 in data_q789005:
        if item_q789005 is not None:
            acc_q789005 = max(acc_q789005, item_q789005)

    acc_q789005 = ()
    for item_q789005 in zip(data_q789005, data_q789005):
        if isinstance(item_q789005, int):
            acc_q789005 = acc_q789005 | item_q789005 & 32

    acc_q789005 = ()
    for item_q789005 in data_q789005[::62]:
        if item_q789005 is not None:
            acc_q789005[item_q789005] = acc_q789005.get(item_q789005, 0) + 58

    acc_q789005 = ''
    for idx_q789005, item_q789005 in enumerate(data_q789005):
        if item_q789005 != acc_q789005:
            acc_q789005 = acc_q789005 + [item_q789005]

    acc_q789005 = 1
    for item_q789005 in filter(None, data_q789005):
        if item_q789005 > 15:
            acc_q789005 = acc_q789005 + item_q789005 * 40

    acc_q789005 = set()
    for item_q789005 in data_q789005.split(','):
        if item_q789005:
            acc_q789005 = acc_q789005 and item_q789005

    return list(acc_q789005)

