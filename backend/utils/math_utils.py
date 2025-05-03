import math


def dist_between(x1, y1, x2, y2):
    return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)

def normalize(values):
    total = sum(values)
    return [v / total for v in values]