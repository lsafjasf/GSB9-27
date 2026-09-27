"""语句换序: 独立语句顺序被打乱，功能相同。"""


def build_report(data):
    header = "=== Report ==="
    lines = []
    total = sum(data)
    count = len(data)
    average = total / count if count else 0
    lines.append(header)
    lines.append("total: " + str(total))
    lines.append("average: " + str(average))
    return "\n".join(lines)


def make_summary(values):
    lines = []
    count = len(values)
    header = "=== Report ==="
    total = sum(values)
    average = total / count if count else 0
    lines.append("total: " + str(total))
    lines.append(header)
    lines.append("average: " + str(average))
    return "\n".join(lines)
