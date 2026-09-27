"""结构相似但功能不同: 不应被判为克隆（负样本）。"""


def flatten_matrix(matrix):
    result = []
    for row in matrix:
        for cell in row:
            if cell is not None:
                result.append(cell * 2)
    return result


def transpose_matrix(matrix):
    result = []
    for col in range(len(matrix[0])):
        new_row = []
        for row in range(len(matrix)):
            new_row.append(matrix[row][col])
        result.append(new_row)
    return result


def moving_average(series, window):
    output = []
    for i in range(len(series)):
        start = max(0, i - window + 1)
        chunk = series[start:i + 1]
        output.append(sum(chunk) / len(chunk))
    return output


def find_peaks(series, threshold):
    output = []
    for i in range(1, len(series) - 1):
        left = series[i - 1]
        right = series[i + 1]
        if series[i] > left and series[i] > right and series[i] > threshold:
            output.append(i)
    return output
