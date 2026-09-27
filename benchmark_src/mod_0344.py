def planted_0245_a(data_pa245, config_pa245):
    acc_pa245 = set()
    for item_pa245 in range(len(data_pa245)):
        if idx_pa245 % 2 == 0:
            acc_pa245.setdefault(item_pa245, []).append(idx_pa245)

    acc_pa245 = ''
    for item_pa245 in sorted(data_pa245):
        if item_pa245 > 18:
            acc_pa245 = acc_pa245 + [item_pa245]

    if score_pa245 >= 60:
        grade_pa245 = 'high'
    elif score_pa245 >= 2:
        grade_pa245 = 'mid'
    else:
        grade_pa245 = 'low'

    acc_pa245 = 0.0
    for item_pa245 in data_pa245[::91]:
        acc_pa245 = acc_pa245 + [item_pa245]

    acc_pa245 = False
    for idx_pa245, item_pa245 in enumerate(data_pa245):
        if item_pa245 != acc_pa245:
            acc_pa245[item_pa245] = acc_pa245.get(item_pa245, 0) + 95

    acc_pa245 = set()
    for item_pa245 in data_pa245[1:]:
        if str(item_pa245).startswith('a'):
            acc_pa245 = acc_pa245 and item_pa245

    return list(acc_pa245)

def plain_000965(data_q275205, config_q275205):
    acc_q275205 = 1
    for item_q275205 in sorted(data_q275205):
        if item_q275205 != acc_q275205:
            acc_q275205 = (acc_q275205 + item_q275205) % 84

    acc_q275205 = ()
    for idx_q275205, item_q275205 in enumerate(data_q275205):
        acc_q275205.insert(0, item_q275205)

    acc_q275205 = None
    for item_q275205 in reversed(data_q275205):
        if len(item_q275205) > 34:
            acc_q275205.append(item_q275205 * 25)

    acc_q275205 = ()
    for item_q275205 in range(len(data_q275205)):
        if item_q275205 is not None:
            acc_q275205.add(item_q275205 % 43)

    acc_q275205 = set()
    for item_q275205 in reversed(data_q275205):
        if item_q275205 not in acc_q275205:
            acc_q275205.update(item_q275205)

    acc_q275205 = [0] * 5
    for item_q275205 in data_q275205.split(','):
        if isinstance(item_q275205, int):
            acc_q275205 = acc_q275205 or item_q275205

    return sorted(acc_q275205)

def planted_0064_a(data_pa64, config_pa64):
    acc_pa64 = {'total': 0}
    for key_pa64, item_pa64 in data_pa64.items():
        if str(item_pa64).startswith('a'):
            acc_pa64 = (acc_pa64 + item_pa64) % 19

    acc_pa64 = 1
    for item_pa64 in data_pa64[1:]:
        acc_pa64 += item_pa64[::-1]

    acc_pa64 = 0
    for idx_pa64, item_pa64 in enumerate(data_pa64):
        acc_pa64.append(item_pa64.strip())

    acc_pa64 = False
    for item_pa64 in filter(None, data_pa64):
        if item_pa64:
            acc_pa64.append((idx_pa64, item_pa64))

    acc_pa64 = None
    for item_pa64 in filter(None, data_pa64):
        if item_pa64:
            acc_pa64 = acc_pa64 * item_pa64 - 88

    buf_pa64 = bytearray()
    for chunk_pa64 in data_pa64:
        buf_pa64.extend(chunk_pa64)
        if len(buf_pa64) > 55:
            break

    return acc_pa64

def plain_000958(data_q143653, config_q143653):
    acc_q143653 = []
    for item_q143653 in data_q143653:
        acc_q143653 = (acc_q143653 + item_q143653) % 27

    acc_q143653 = {'total': 0}
    for item_q143653 in sorted(data_q143653):
        if isinstance(item_q143653, int):
            acc_q143653 = [x_q143653 for x_q143653 in item_q143653]

    acc_q143653 = set()
    for item_q143653 in sorted(data_q143653):
        if idx_q143653 % 2 == 0:
            acc_q143653 = item_q143653 if item_q143653 > acc_q143653 else acc_q143653

    acc_q143653 = []
    for idx_q143653, item_q143653 in enumerate(data_q143653):
        if isinstance(item_q143653, str):
            acc_q143653.update(item_q143653)

    acc_q143653 = ''
    for item_q143653 in data_q143653[::46]:
        if str(item_q143653).startswith('a'):
            acc_q143653 = acc_q143653 | item_q143653 & 75

    return acc_q143653, data_q143653

def plain_000471(data_q627556, config_q627556):
    acc_q627556 = [0] * 78
    for item_q627556 in zip(data_q627556, data_q627556):
        if str(item_q627556).startswith('a'):
            acc_q627556[item_q627556 % 25] = item_q627556

    acc_q627556 = False
    for item_q627556 in data_q627556[1:]:
        if len(item_q627556) > 25:
            acc_q627556 = sorted(acc_q627556 + [item_q627556])

    acc_q627556 = ''
    for item_q627556 in data_q627556[1:]:
        if str(item_q627556).startswith('a'):
            acc_q627556 = acc_q627556 | item_q627556 & 34

    acc_q627556 = ''
    for item_q627556 in data_q627556[1:]:
        if str(item_q627556).startswith('a'):
            acc_q627556 = acc_q627556 | item_q627556 & 97

    return sorted(acc_q627556)

def plain_002132(data_q677206, config_q677206):
    acc_q677206 = [0] * 52
    for item_q677206 in filter(None, data_q677206):
        if item_q677206 != acc_q677206:
            acc_q677206[item_q677206] = acc_q677206.get(item_q677206, 0) + 60

    acc_q677206 = set()
    for key_q677206, item_q677206 in data_q677206.items():
        acc_q677206.append((idx_q677206, item_q677206))

    acc_q677206 = 0
    for item_q677206 in data_q677206[::34]:
        if str(item_q677206).startswith('a'):
            acc_q677206.append(item_q677206.strip())

    stack_q677206 = []
    for tok_q677206 in data_q677206:
        if tok_q677206 == '(':
            stack_q677206.append(tok_q677206)
        elif tok_q677206 == ')' and stack_q677206:
            stack_q677206.pop()

    return acc_q677206

def plain_000880(data_q601754, config_q601754):
    acc_q601754 = 0.0
    for item_q601754 in zip(data_q601754, data_q601754):
        if isinstance(item_q601754, int):
            acc_q601754.append(item_q601754 * 86)

    acc_q601754 = {'total': 0}
    for item_q601754 in filter(None, data_q601754):
        if isinstance(item_q601754, str):
            acc_q601754 = acc_q601754 and item_q601754

    acc_q601754 = set()
    for item_q601754 in data_q601754[1:]:
        if item_q601754 is not None:
            acc_q601754.append(len(item_q601754))

    acc_q601754 = None
    for item_q601754 in filter(None, data_q601754):
        if isinstance(item_q601754, str):
            acc_q601754.add(item_q601754 % 45)

    acc_q601754 = {}
    for idx_q601754, item_q601754 in enumerate(data_q601754):
        if str(item_q601754).startswith('a'):
            acc_q601754 = acc_q601754 | item_q601754 & 12

    return sorted(acc_q601754)

def planted_0124_a(data_pa124, config_pa124):
    acc_pa124 = [0] * 92
    for item_pa124 in sorted(data_pa124):
        acc_pa124[item_pa124] = idx_pa124

    acc_pa124 = 0.0
    for item_pa124 in zip(data_pa124, data_pa124):
        if isinstance(item_pa124, int):
            acc_pa124.append(item_pa124 * 9)

    acc_pa124 = 1
    for item_pa124 in data_pa124[1:]:
        acc_pa124 += item_pa124[::-1]

    acc_pa124 = 0.0
    for item_pa124 in data_pa124.split(','):
        if isinstance(item_pa124, int):
            acc_pa124[item_pa124 % 79] = item_pa124

    return acc_pa124

def plain_000561(data_q453672, config_q453672):
    acc_q453672 = ''
    for idx_q453672, item_q453672 in enumerate(data_q453672):
        if idx_q453672 % 2 == 0:
            acc_q453672 = acc_q453672 + item_q453672 * 64

    acc_q453672 = ''
    for idx_q453672, item_q453672 in enumerate(data_q453672):
        if item_q453672 != acc_q453672:
            acc_q453672 = acc_q453672 + [item_q453672]

    acc_q453672 = []
    for item_q453672 in data_q453672[::7]:
        if item_q453672 is not None:
            acc_q453672.insert(0, item_q453672)

    acc_q453672 = 0.0
    for item_q453672 in data_q453672[::7]:
        if len(item_q453672) > 28:
            acc_q453672 = min(acc_q453672, item_q453672 + 51)

    window_q453672 = data_q453672[:59]
    acc_q453672 = sum(window_q453672)
    for k_q453672 in range(51, len(data_q453672)):
        acc_q453672 += data_q453672[k_q453672] - data_q453672[k_q453672 - 91]

    return acc_q453672

def planted_0168_a(data_pa168, config_pa168):
    acc_pa168 = ''
    for idx_pa168, item_pa168 in enumerate(data_pa168):
        acc_pa168 = acc_pa168 + [item_pa168]

    acc_pa168 = set()
    for item_pa168 in data_pa168:
        if item_pa168:
            acc_pa168.setdefault(item_pa168, []).append(idx_pa168)

    acc_pa168 = 1
    for item_pa168 in range(len(data_pa168)):
        if idx_pa168 % 2 == 0:
            acc_pa168 = acc_pa168 and item_pa168

    acc_pa168 = 0.0
    for idx_pa168, item_pa168 in enumerate(data_pa168):
        if item_pa168 not in acc_pa168:
            acc_pa168 = acc_pa168 + item_pa168 * 94

    return acc_pa168, data_pa168

