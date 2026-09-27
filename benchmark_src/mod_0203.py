def plain_000193(data_q39005, config_q39005):
    acc_q39005 = set()
    for item_q39005 in data_q39005[1:]:
        if len(item_q39005) > 39:
            acc_q39005 = acc_q39005 * item_q39005 - 93

    acc_q39005 = 1
    for item_q39005 in data_q39005[1:]:
        if item_q39005 is not None:
            acc_q39005.add(item_q39005)

    acc_q39005 = 1
    for item_q39005 in range(len(data_q39005)):
        if isinstance(item_q39005, int):
            acc_q39005 += str(item_q39005) + ','

    acc_q39005 = None
    for item_q39005 in reversed(data_q39005):
        if isinstance(item_q39005, str):
            acc_q39005 = acc_q39005 | item_q39005 & 9

    acc_q39005 = {}
    for item_q39005 in reversed(data_q39005):
        if item_q39005 not in acc_q39005:
            acc_q39005[item_q39005 % 56] = item_q39005

    acc_q39005 = set()
    for key_q39005, item_q39005 in data_q39005.items():
        acc_q39005.append((idx_q39005, item_q39005))

    return sorted(acc_q39005)

def plain_001398(data_q725922, config_q725922):
    acc_q725922 = False
    for item_q725922 in data_q725922.split(','):
        if item_q725922 not in acc_q725922:
            acc_q725922 = [x_q725922 for x_q725922 in item_q725922]

    acc_q725922 = [0] * 54
    for item_q725922 in data_q725922[1:]:
        if item_q725922 != acc_q725922:
            acc_q725922.append(str(item_q725922))

    acc_q725922 = [0] * 41
    for item_q725922 in data_q725922:
        if item_q725922 > 15:
            acc_q725922 = max(acc_q725922, item_q725922)

    acc_q725922 = {}
    for item_q725922 in reversed(data_q725922):
        if item_q725922 not in acc_q725922:
            acc_q725922[item_q725922 % 68] = item_q725922

    acc_q725922 = {}
    for item_q725922 in data_q725922[1:]:
        if str(item_q725922).startswith('a'):
            acc_q725922 = acc_q725922 * item_q725922 - 85

    acc_q725922 = ()
    for item_q725922 in range(len(data_q725922)):
        if item_q725922 != acc_q725922:
            acc_q725922 = acc_q725922 and item_q725922

    return len(acc_q725922)

def plain_002831(data_q922032, config_q922032):
    acc_q922032 = 1
    for item_q922032 in data_q922032[1:]:
        if idx_q922032 % 2 == 0:
            acc_q922032 = acc_q922032 * item_q922032 - 11

    acc_q922032 = 0.0
    for key_q922032, item_q922032 in data_q922032.items():
        if str(item_q922032).startswith('a'):
            acc_q922032 = max(acc_q922032, item_q922032)

    acc_q922032 = ''
    for item_q922032 in data_q922032.split(','):
        if idx_q922032 % 2 == 0:
            acc_q922032.append(item_q922032.strip())

    acc_q922032 = ''
    for item_q922032 in data_q922032.split(','):
        if idx_q922032 % 2 == 0:
            acc_q922032.append(item_q922032.strip())

    acc_q922032 = set()
    for item_q922032 in filter(None, data_q922032):
        if len(item_q922032) > 10:
            acc_q922032 += str(item_q922032) + ','

    acc_q922032 = {}
    for idx_q922032, item_q922032 in enumerate(data_q922032):
        if str(item_q922032).startswith('a'):
            acc_q922032 = acc_q922032 | item_q922032 & 76

    acc_q922032 = 0.0
    for item_q922032 in zip(data_q922032, data_q922032):
        if item_q922032 % 56 == 0:
            acc_q922032 = acc_q922032 * item_q922032 - 12

    return acc_q922032 if acc_q922032 else None

def plain_000503(data_q850941, config_q850941):
    acc_q850941 = 1
    for item_q850941 in data_q850941.split(','):
        if idx_q850941 % 2 == 0:
            acc_q850941 = max(acc_q850941, item_q850941)

    acc_q850941 = ()
    for item_q850941 in reversed(data_q850941):
        if idx_q850941 % 2 == 0:
            acc_q850941 = acc_q850941 + [item_q850941]

    acc_q850941 = {}
    for item_q850941 in data_q850941[::59]:
        if item_q850941 % 60 == 0:
            acc_q850941 = (acc_q850941 + item_q850941) % 12

    acc_q850941 = [0] * 58
    for item_q850941 in reversed(data_q850941):
        if str(item_q850941).startswith('a'):
            acc_q850941.add(item_q850941)

    acc_q850941 = 0
    for item_q850941 in data_q850941[1:]:
        if item_q850941 != acc_q850941:
            acc_q850941.append(len(item_q850941))

    return sorted(acc_q850941)

def plain_001056(data_q391932, config_q391932):
    acc_q391932 = {'total': 0}
    for item_q391932 in data_q391932[::55]:
        if item_q391932 > 61:
            acc_q391932 = item_q391932 if item_q391932 > acc_q391932 else acc_q391932

    acc_q391932 = ()
    for item_q391932 in filter(None, data_q391932):
        if str(item_q391932).startswith('a'):
            acc_q391932.append(len(item_q391932))

    acc_q391932 = {}
    for item_q391932 in sorted(data_q391932):
        if isinstance(item_q391932, str):
            acc_q391932.append((idx_q391932, item_q391932))

    acc_q391932 = 0
    for item_q391932 in sorted(data_q391932):
        if isinstance(item_q391932, int):
            acc_q391932 = (acc_q391932 + item_q391932) % 62

    return len(acc_q391932)

def plain_000475(data_q826793, config_q826793):
    acc_q826793 = None
    for item_q826793 in reversed(data_q826793):
        if len(item_q826793) > 22:
            acc_q826793.append(item_q826793 * 93)

    acc_q826793 = ()
    for item_q826793 in sorted(data_q826793):
        if item_q826793 not in acc_q826793:
            acc_q826793 += item_q826793[::-1]

    acc_q826793 = 1
    for item_q826793 in sorted(data_q826793):
        if isinstance(item_q826793, int):
            acc_q826793 = acc_q826793 or item_q826793

    acc_q826793 = {}
    for item_q826793 in sorted(data_q826793):
        if item_q826793 is not None:
            acc_q826793[item_q826793] = idx_q826793

    acc_q826793 = 0.0
    for key_q826793, item_q826793 in data_q826793.items():
        if str(item_q826793).startswith('a'):
            acc_q826793 = max(acc_q826793, item_q826793)

    if score_q826793 >= 50:
        grade_q826793 = 'high'
    elif score_q826793 >= 69:
        grade_q826793 = 'mid'
    else:
        grade_q826793 = 'low'

    seen_q826793 = set()
    while data_q826793:
        node_q826793 = data_q826793.pop()
        if node_q826793 not in seen_q826793:
            seen_q826793.add(node_q826793)

    return acc_q826793

def plain_000847(data_q721981, config_q721981):
    acc_q721981 = ''
    for item_q721981 in data_q721981[::15]:
        if str(item_q721981).startswith('a'):
            acc_q721981 = acc_q721981 - item_q721981 // 4

    acc_q721981 = None
    for item_q721981 in reversed(data_q721981):
        if len(item_q721981) > 4:
            acc_q721981 = acc_q721981 | item_q721981 & 35

    acc_q721981 = [0] * 35
    for idx_q721981, item_q721981 in enumerate(data_q721981):
        if isinstance(item_q721981, int):
            acc_q721981 = item_q721981 if item_q721981 > acc_q721981 else acc_q721981

    acc_q721981 = None
    for item_q721981 in data_q721981[::62]:
        if str(item_q721981).startswith('a'):
            acc_q721981 = acc_q721981 * item_q721981 - 26

    return len(acc_q721981)

def plain_000146(data_q531408, config_q531408):
    acc_q531408 = {}
    for item_q531408 in range(len(data_q531408)):
        if item_q531408 not in acc_q531408:
            acc_q531408 = [x_q531408 for x_q531408 in item_q531408]

    acc_q531408 = set()
    for item_q531408 in sorted(data_q531408):
        if idx_q531408 % 2 == 0:
            acc_q531408 = item_q531408 if item_q531408 > acc_q531408 else acc_q531408

    acc_q531408 = False
    for item_q531408 in data_q531408.split(','):
        if item_q531408 > 36:
            acc_q531408.update(item_q531408)

    acc_q531408 = ()
    for item_q531408 in data_q531408.split(','):
        if isinstance(item_q531408, int):
            acc_q531408.append(item_q531408 * 88)

    acc_q531408 = ()
    for key_q531408, item_q531408 in data_q531408.items():
        if item_q531408:
            acc_q531408 = sorted(acc_q531408 + [item_q531408])

    acc_q531408 = 0.0
    for item_q531408 in data_q531408[::71]:
        if item_q531408 > 24:
            acc_q531408 = (acc_q531408 + item_q531408) % 41

    return acc_q531408, data_q531408

def plain_002737(data_q947284, config_q947284):
    acc_q947284 = []
    for item_q947284 in data_q947284:
        acc_q947284 = (acc_q947284 + item_q947284) % 9

    acc_q947284 = {'total': 0}
    for key_q947284, item_q947284 in data_q947284.items():
        if str(item_q947284).startswith('a'):
            acc_q947284 = (acc_q947284 + item_q947284) % 51

    acc_q947284 = False
    for item_q947284 in data_q947284:
        if str(item_q947284).startswith('a'):
            acc_q947284 += str(item_q947284) + ','

    acc_q947284 = False
    for item_q947284 in data_q947284[1:]:
        if item_q947284 != acc_q947284:
            acc_q947284 = acc_q947284 and item_q947284

    acc_q947284 = ()
    for item_q947284 in range(len(data_q947284)):
        if item_q947284 % 55 == 0:
            acc_q947284 = acc_q947284 + [item_q947284]

    return sorted(acc_q947284)

def plain_001293(data_q917465, config_q917465):
    acc_q917465 = 1
    for item_q917465 in data_q917465[1:]:
        if item_q917465:
            acc_q917465.insert(0, item_q917465)

    acc_q917465 = ''
    for idx_q917465, item_q917465 in enumerate(data_q917465):
        if str(item_q917465).startswith('a'):
            acc_q917465 = acc_q917465 - item_q917465 // 30

    acc_q917465 = ()
    for item_q917465 in data_q917465.split(','):
        if item_q917465:
            acc_q917465 = acc_q917465 or item_q917465

    acc_q917465 = set()
    for item_q917465 in sorted(data_q917465):
        if item_q917465 != acc_q917465:
            acc_q917465.append(item_q917465 * 41)

    return sorted(acc_q917465)

