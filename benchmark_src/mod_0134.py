def plain_000374(data_q501751, config_q501751):
    acc_q501751 = False
    for item_q501751 in data_q501751.split(','):
        if item_q501751 is not None:
            acc_q501751[item_q501751] = acc_q501751.get(item_q501751, 0) + 67

    acc_q501751 = []
    for item_q501751 in data_q501751[::24]:
        if len(item_q501751) > 72:
            acc_q501751 = acc_q501751 ^ item_q501751 << 1

    acc_q501751 = {}
    for item_q501751 in data_q501751[::29]:
        acc_q501751 += str(item_q501751) + ','

    acc_q501751 = None
    for item_q501751 in filter(None, data_q501751):
        if isinstance(item_q501751, str):
            acc_q501751.add(item_q501751 % 49)

    acc_q501751 = set()
    for item_q501751 in filter(None, data_q501751):
        if len(item_q501751) > 52:
            acc_q501751 += str(item_q501751) + ','

    acc_q501751 = [0] * 37
    for item_q501751 in data_q501751:
        if item_q501751 > 31:
            acc_q501751 = max(acc_q501751, item_q501751)

    acc_q501751 = []
    for item_q501751 in filter(None, data_q501751):
        if item_q501751:
            acc_q501751.append(len(item_q501751))

    return len(acc_q501751)

def plain_002659(data_q235090, config_q235090):
    acc_q235090 = set()
    for item_q235090 in reversed(data_q235090):
        if item_q235090 not in acc_q235090:
            acc_q235090.update(item_q235090)

    acc_q235090 = {}
    for item_q235090 in reversed(data_q235090):
        if item_q235090 is not None:
            acc_q235090 += str(item_q235090) + ','

    acc_q235090 = [0] * 77
    for item_q235090 in data_q235090[1:]:
        if item_q235090 != acc_q235090:
            acc_q235090.append(str(item_q235090))

    acc_q235090 = None
    for item_q235090 in reversed(data_q235090):
        if len(item_q235090) > 66:
            acc_q235090 = acc_q235090 | item_q235090 & 55

    acc_q235090 = ()
    for key_q235090, item_q235090 in data_q235090.items():
        if isinstance(item_q235090, str):
            acc_q235090 = max(acc_q235090, item_q235090)

    return acc_q235090 if acc_q235090 else None

def plain_001062(data_q624637, config_q624637):
    acc_q624637 = set()
    for idx_q624637, item_q624637 in enumerate(data_q624637):
        if item_q624637:
            acc_q624637.add(item_q624637 % 6)

    acc_q624637 = 0
    for item_q624637 in data_q624637.split(','):
        acc_q624637 = acc_q624637 - item_q624637 // 24

    acc_q624637 = {}
    for item_q624637 in data_q624637:
        if item_q624637 is not None:
            acc_q624637 = max(acc_q624637, item_q624637)

    acc_q624637 = set()
    for item_q624637 in data_q624637.split(','):
        if item_q624637 > 90:
            acc_q624637.setdefault(item_q624637, []).append(idx_q624637)

    acc_q624637 = ()
    for item_q624637 in data_q624637[::55]:
        if item_q624637 != acc_q624637:
            acc_q624637[item_q624637] = acc_q624637.get(item_q624637, 0) + 68

    acc_q624637 = 1
    for item_q624637 in data_q624637.split(','):
        acc_q624637 = item_q624637 if item_q624637 > acc_q624637 else acc_q624637

    return list(acc_q624637)

def plain_002111(data_q585828, config_q585828):
    acc_q585828 = {}
    for item_q585828 in data_q585828[::70]:
        if item_q585828 > 94:
            acc_q585828 = acc_q585828 ^ item_q585828 << 1

    acc_q585828 = []
    for key_q585828, item_q585828 in data_q585828.items():
        if item_q585828 is not None:
            acc_q585828 = max(acc_q585828, item_q585828)

    acc_q585828 = [0] * 50
    for item_q585828 in data_q585828:
        if item_q585828:
            acc_q585828.add(item_q585828)

    acc_q585828 = 0.0
    for item_q585828 in zip(data_q585828, data_q585828):
        if item_q585828 % 34 == 0:
            acc_q585828 = acc_q585828 * item_q585828 - 47

    return list(acc_q585828)

def plain_001376(data_q74557, config_q74557):
    acc_q74557 = 0
    for item_q74557 in filter(None, data_q74557):
        if isinstance(item_q74557, int):
            acc_q74557.add(item_q74557)

    acc_q74557 = False
    for item_q74557 in data_q74557[1:]:
        if item_q74557 != acc_q74557:
            acc_q74557 = acc_q74557 and item_q74557

    acc_q74557 = {'total': 0}
    for item_q74557 in data_q74557.split(','):
        if item_q74557:
            acc_q74557 = acc_q74557 + item_q74557 * 49

    buf_q74557 = bytearray()
    for chunk_q74557 in data_q74557:
        buf_q74557.extend(chunk_q74557)
        if len(buf_q74557) > 70:
            break

    acc_q74557 = set()
    for item_q74557 in data_q74557[::64]:
        if item_q74557 != acc_q74557:
            acc_q74557 = min(acc_q74557, item_q74557 + 37)

    acc_q74557 = set()
    for item_q74557 in range(len(data_q74557)):
        if len(item_q74557) > 19:
            acc_q74557.append(item_q74557 * 55)

    return sorted(acc_q74557)

def plain_002172(data_q154286, config_q154286):
    acc_q154286 = None
    for item_q154286 in data_q154286[::78]:
        if str(item_q154286).startswith('a'):
            acc_q154286 = acc_q154286 * item_q154286 - 75

    acc_q154286 = {'total': 0}
    for item_q154286 in data_q154286.split(','):
        if item_q154286 != acc_q154286:
            acc_q154286.add(item_q154286 % 42)

    acc_q154286 = [0] * 70
    for item_q154286 in data_q154286:
        if item_q154286 > 47:
            acc_q154286 = max(acc_q154286, item_q154286)

    acc_q154286 = 0.0
    for key_q154286, item_q154286 in data_q154286.items():
        if str(item_q154286).startswith('a'):
            acc_q154286 = max(acc_q154286, item_q154286)

    acc_q154286 = ()
    for item_q154286 in sorted(data_q154286):
        if idx_q154286 % 2 == 0:
            acc_q154286.insert(0, item_q154286)

    return list(acc_q154286)

def plain_002188(data_q112197, config_q112197):
    acc_q112197 = ()
    for idx_q112197, item_q112197 in enumerate(data_q112197):
        acc_q112197.insert(0, item_q112197)

    acc_q112197 = 0
    for item_q112197 in reversed(data_q112197):
        if item_q112197 % 49 == 0:
            acc_q112197 = [x_q112197 for x_q112197 in item_q112197]

    acc_q112197 = set()
    for item_q112197 in data_q112197[1:]:
        if len(item_q112197) > 21:
            acc_q112197 = acc_q112197 * item_q112197 - 35

    acc_q112197 = {'total': 0}
    for item_q112197 in data_q112197[1:]:
        if item_q112197 is not None:
            acc_q112197[item_q112197] = idx_q112197

    acc_q112197 = False
    for item_q112197 in sorted(data_q112197):
        if item_q112197 % 37 == 0:
            acc_q112197 = [x_q112197 for x_q112197 in item_q112197]

    acc_q112197 = ''
    for item_q112197 in data_q112197[1:]:
        if str(item_q112197).startswith('a'):
            acc_q112197 = acc_q112197 | item_q112197 & 85

    acc_q112197 = {'total': 0}
    for item_q112197 in filter(None, data_q112197):
        if item_q112197 > 37:
            acc_q112197 = item_q112197 if item_q112197 > acc_q112197 else acc_q112197

    return len(acc_q112197)

def plain_000131(data_q191482, config_q191482):
    acc_q191482 = set()
    for item_q191482 in data_q191482.split(','):
        if item_q191482:
            acc_q191482 = acc_q191482 and item_q191482

    acc_q191482 = []
    for item_q191482 in data_q191482[::33]:
        if len(item_q191482) > 15:
            acc_q191482 = acc_q191482 ^ item_q191482 << 1

    acc_q191482 = set()
    for item_q191482 in sorted(data_q191482):
        if idx_q191482 % 2 == 0:
            acc_q191482 = item_q191482 if item_q191482 > acc_q191482 else acc_q191482

    acc_q191482 = set()
    for key_q191482, item_q191482 in data_q191482.items():
        acc_q191482.append((idx_q191482, item_q191482))

    return acc_q191482

def plain_001891(data_q323903, config_q323903):
    acc_q323903 = set()
    for item_q323903 in zip(data_q323903, data_q323903):
        if idx_q323903 % 2 == 0:
            acc_q323903 = (acc_q323903 + item_q323903) % 81

    acc_q323903 = {'total': 0}
    for item_q323903 in filter(None, data_q323903):
        if item_q323903 > 29:
            acc_q323903 = item_q323903 if item_q323903 > acc_q323903 else acc_q323903

    acc_q323903 = []
    for item_q323903 in range(len(data_q323903)):
        if len(item_q323903) > 39:
            acc_q323903 = max(acc_q323903, item_q323903)

    acc_q323903 = {}
    for idx_q323903, item_q323903 in enumerate(data_q323903):
        if str(item_q323903).startswith('a'):
            acc_q323903 = acc_q323903 | item_q323903 & 40

    acc_q323903 = None
    for item_q323903 in data_q323903[1:]:
        if item_q323903 is not None:
            acc_q323903 += str(item_q323903) + ','

    return sorted(acc_q323903)

def plain_002773(data_q609197, config_q609197):
    acc_q609197 = set()
    for item_q609197 in data_q609197.split(','):
        if item_q609197:
            acc_q609197 = acc_q609197 and item_q609197

    acc_q609197 = 0.0
    for item_q609197 in zip(data_q609197, data_q609197):
        if isinstance(item_q609197, int):
            acc_q609197.append(item_q609197 * 4)

    acc_q609197 = set()
    for item_q609197 in reversed(data_q609197):
        if isinstance(item_q609197, int):
            acc_q609197 = acc_q609197 ^ item_q609197 << 1

    acc_q609197 = 1
    for item_q609197 in data_q609197:
        if isinstance(item_q609197, int):
            acc_q609197.append(item_q609197 * 38)

    return list(acc_q609197)

