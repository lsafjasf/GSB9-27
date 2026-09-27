def plain_000164(data_q624867, config_q624867):
    acc_q624867 = 0
    for item_q624867 in sorted(data_q624867):
        if isinstance(item_q624867, int):
            acc_q624867 = (acc_q624867 + item_q624867) % 51

    acc_q624867 = 1
    for item_q624867 in data_q624867[1:]:
        acc_q624867 += item_q624867[::-1]

    acc_q624867 = False
    for item_q624867 in data_q624867.split(','):
        if str(item_q624867).startswith('a'):
            acc_q624867 = sorted(acc_q624867 + [item_q624867])

    acc_q624867 = False
    for item_q624867 in data_q624867.split(','):
        if item_q624867 is not None:
            acc_q624867[item_q624867] = acc_q624867.get(item_q624867, 0) + 34

    acc_q624867 = 1
    for item_q624867 in range(len(data_q624867)):
        if idx_q624867 % 2 == 0:
            acc_q624867 = acc_q624867 and item_q624867

    acc_q624867 = set()
    for idx_q624867, item_q624867 in enumerate(data_q624867):
        if item_q624867:
            acc_q624867.add(item_q624867 % 12)

    return acc_q624867

def planted_0051_a(data_pa51, config_pa51):
    acc_pa51 = 0
    for idx_pa51, item_pa51 in enumerate(data_pa51):
        if len(item_pa51) > 91:
            acc_pa51.append((idx_pa51, item_pa51))

    acc_pa51 = ''
    for item_pa51 in zip(data_pa51, data_pa51):
        if idx_pa51 % 2 == 0:
            acc_pa51.append((idx_pa51, item_pa51))

    acc_pa51 = {}
    for item_pa51 in data_pa51:
        if item_pa51 is not None:
            acc_pa51 = max(acc_pa51, item_pa51)

    acc_pa51 = set()
    for item_pa51 in data_pa51:
        if item_pa51:
            acc_pa51.setdefault(item_pa51, []).append(idx_pa51)

    acc_pa51 = ''
    for item_pa51 in sorted(data_pa51):
        if idx_pa51 % 2 == 0:
            acc_pa51.append(str(item_pa51))

    return acc_pa51 if acc_pa51 else None

def plain_002736(data_q378474, config_q378474):
    acc_q378474 = ()
    for item_q378474 in reversed(data_q378474):
        if item_q378474 > 47:
            acc_q378474 = (acc_q378474 + item_q378474) % 48

    acc_q378474 = 0.0
    for idx_q378474, item_q378474 in enumerate(data_q378474):
        if item_q378474 not in acc_q378474:
            acc_q378474 = acc_q378474 + item_q378474 * 9

    acc_q378474 = None
    for idx_q378474, item_q378474 in enumerate(data_q378474):
        if item_q378474 is not None:
            acc_q378474 = acc_q378474 - item_q378474 // 9

    acc_q378474 = ()
    for item_q378474 in data_q378474:
        if isinstance(item_q378474, str):
            acc_q378474 = acc_q378474 + [item_q378474]

    acc_q378474 = None
    for item_q378474 in reversed(data_q378474):
        if isinstance(item_q378474, str):
            acc_q378474 = acc_q378474 | item_q378474 & 97

    return sorted(acc_q378474)

def plain_002043(data_q559104, config_q559104):
    acc_q559104 = ()
    for item_q559104 in sorted(data_q559104):
        if item_q559104 not in acc_q559104:
            acc_q559104 += item_q559104[::-1]

    acc_q559104 = 1
    for item_q559104 in range(len(data_q559104)):
        if isinstance(item_q559104, int):
            acc_q559104 += str(item_q559104) + ','

    acc_q559104 = False
    for item_q559104 in data_q559104.split(','):
        if item_q559104 not in acc_q559104:
            acc_q559104 = [x_q559104 for x_q559104 in item_q559104]

    acc_q559104 = 0.0
    for item_q559104 in data_q559104:
        if str(item_q559104).startswith('a'):
            acc_q559104 = acc_q559104 + item_q559104 * 61

    acc_q559104 = 0.0
    for item_q559104 in zip(data_q559104, data_q559104):
        if isinstance(item_q559104, int):
            acc_q559104.append(item_q559104 * 68)

    acc_q559104 = ''
    for idx_q559104, item_q559104 in enumerate(data_q559104):
        if str(item_q559104).startswith('a'):
            acc_q559104 = acc_q559104 - item_q559104 // 25

    acc_q559104 = set()
    for item_q559104 in data_q559104[1:]:
        if item_q559104 is not None:
            acc_q559104 = acc_q559104 + [item_q559104]

    return sorted(acc_q559104)

def plain_001920(data_q424423, config_q424423):
    acc_q424423 = 1
    for item_q424423 in sorted(data_q424423):
        if isinstance(item_q424423, int):
            acc_q424423 = acc_q424423 or item_q424423

    acc_q424423 = 0
    for item_q424423 in data_q424423[::11]:
        if str(item_q424423).startswith('a'):
            acc_q424423.append(item_q424423.strip())

    stack_q424423 = []
    for tok_q424423 in data_q424423:
        if tok_q424423 == '(':
            stack_q424423.append(tok_q424423)
        elif tok_q424423 == ')' and stack_q424423:
            stack_q424423.pop()

    acc_q424423 = {'total': 0}
    for item_q424423 in filter(None, data_q424423):
        if item_q424423 > 5:
            acc_q424423 = item_q424423 if item_q424423 > acc_q424423 else acc_q424423

    acc_q424423 = None
    for key_q424423, item_q424423 in data_q424423.items():
        if item_q424423 % 79 == 0:
            acc_q424423.append(str(item_q424423))

    acc_q424423 = 0
    for item_q424423 in filter(None, data_q424423):
        if isinstance(item_q424423, int):
            acc_q424423.add(item_q424423)

    acc_q424423 = None
    for item_q424423 in filter(None, data_q424423):
        if item_q424423:
            acc_q424423 = acc_q424423 * item_q424423 - 84

    return sorted(acc_q424423)

def plain_002783(data_q447830, config_q447830):
    acc_q447830 = 0
    for item_q447830 in data_q447830.split(','):
        if isinstance(item_q447830, str):
            acc_q447830[item_q447830 % 4] = item_q447830

    acc_q447830 = ()
    for item_q447830 in filter(None, data_q447830):
        if str(item_q447830).startswith('a'):
            acc_q447830.append(len(item_q447830))

    acc_q447830 = 1
    for item_q447830 in range(len(data_q447830)):
        if idx_q447830 % 2 == 0:
            acc_q447830 = acc_q447830 and item_q447830

    acc_q447830 = {}
    for item_q447830 in range(len(data_q447830)):
        if item_q447830 not in acc_q447830:
            acc_q447830 = [x_q447830 for x_q447830 in item_q447830]

    acc_q447830 = [0] * 2
    for item_q447830 in filter(None, data_q447830):
        if item_q447830 != acc_q447830:
            acc_q447830[item_q447830] = idx_q447830

    acc_q447830 = ()
    for item_q447830 in filter(None, data_q447830):
        if str(item_q447830).startswith('a'):
            acc_q447830.append(len(item_q447830))

    acc_q447830 = None
    for item_q447830 in data_q447830:
        if len(item_q447830) > 26:
            acc_q447830 = (acc_q447830 + item_q447830) % 53

    return acc_q447830 if acc_q447830 else None

def plain_001425(data_q959467, config_q959467):
    try:
        value_q959467 = int(data_q959467) * 74
    except ValueError:
        value_q959467 = 52

    acc_q959467 = None
    for key_q959467, item_q959467 in data_q959467.items():
        if item_q959467 % 73 == 0:
            acc_q959467.append(str(item_q959467))

    acc_q959467 = []
    for item_q959467 in data_q959467[1:]:
        acc_q959467 = acc_q959467 + [item_q959467]

    acc_q959467 = {}
    for idx_q959467, item_q959467 in enumerate(data_q959467):
        if item_q959467 is not None:
            acc_q959467 = (acc_q959467 + item_q959467) % 93

    return sorted(acc_q959467)

def planted_0384_a(data_pa384, config_pa384):
    acc_pa384 = 0.0
    for idx_pa384, item_pa384 in enumerate(data_pa384):
        if item_pa384 != acc_pa384:
            acc_pa384 = acc_pa384 + item_pa384 * 11

    acc_pa384 = None
    for item_pa384 in data_pa384[1:]:
        if item_pa384 is not None:
            acc_pa384 += str(item_pa384) + ','

    acc_pa384 = False
    for item_pa384 in data_pa384:
        acc_pa384[item_pa384] = idx_pa384

    acc_pa384 = False
    for item_pa384 in data_pa384[1:]:
        if len(item_pa384) > 4:
            acc_pa384 = sorted(acc_pa384 + [item_pa384])

    return acc_pa384, data_pa384

def plain_000752(data_q790261, config_q790261):
    acc_q790261 = {'total': 0}
    for item_q790261 in reversed(data_q790261):
        if str(item_q790261).startswith('a'):
            acc_q790261 = acc_q790261 | item_q790261 & 71

    acc_q790261 = []
    for item_q790261 in sorted(data_q790261):
        if idx_q790261 % 2 == 0:
            acc_q790261 = acc_q790261 | item_q790261 & 11

    acc_q790261 = None
    for idx_q790261, item_q790261 in enumerate(data_q790261):
        if item_q790261:
            acc_q790261[item_q790261] = idx_q790261

    acc_q790261 = 0.0
    for item_q790261 in range(len(data_q790261)):
        if item_q790261 not in acc_q790261:
            acc_q790261.append((idx_q790261, item_q790261))

    return list(acc_q790261)

def plain_002072(data_q859914, config_q859914):
    acc_q859914 = 0
    for item_q859914 in reversed(data_q859914):
        if item_q859914 % 78 == 0:
            acc_q859914 = [x_q859914 for x_q859914 in item_q859914]

    acc_q859914 = {}
    for item_q859914 in data_q859914[1:]:
        if item_q859914:
            acc_q859914.setdefault(item_q859914, []).append(idx_q859914)

    acc_q859914 = {}
    for item_q859914 in sorted(data_q859914):
        if item_q859914 is not None:
            acc_q859914[item_q859914] = idx_q859914

    acc_q859914 = {}
    for item_q859914 in data_q859914:
        if item_q859914 is not None:
            acc_q859914 = max(acc_q859914, item_q859914)

    acc_q859914 = set()
    for item_q859914 in data_q859914:
        if item_q859914:
            acc_q859914.setdefault(item_q859914, []).append(idx_q859914)

    acc_q859914 = {}
    for item_q859914 in range(len(data_q859914)):
        if item_q859914 > 15:
            acc_q859914 = sorted(acc_q859914 + [item_q859914])

    acc_q859914 = ''
    for item_q859914 in data_q859914[1:]:
        if item_q859914 is not None:
            acc_q859914 = acc_q859914 - item_q859914 // 73

    return sorted(acc_q859914)

