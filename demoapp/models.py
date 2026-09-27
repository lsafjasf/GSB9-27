"""Legitimate application types used by the deserializer demos/tests."""


class User:
    def __init__(self, name, age=0, **extra):
        self.name = name
        self.age = age
        self.extra = extra

    @classmethod
    def from_dict(cls, **state):
        return cls(**state)

    def __eq__(self, other):
        return (
            isinstance(other, User)
            and self.name == other.name
            and self.age == other.age
        )

    def __repr__(self):
        return f"User(name={self.name!r}, age={self.age!r})"


class Point:
    def __init__(self, x=0, y=0):
        self.x = x
        self.y = y

    def __eq__(self, other):
        return isinstance(other, Point) and (self.x, self.y) == (other.x, other.y)

    def __repr__(self):
        return f"Point(x={self.x!r}, y={self.y!r})"


class TypedList:
    """A container whose items may themselves be typed nodes."""

    def __init__(self, items=None):
        self.items = list(items or [])

    def __repr__(self):
        return f"TypedList(items={self.items!r})"


class Node:
    """Linked node, used for $id/$ref and cycle tests."""

    def __init__(self, name="", next=None):
        self.name = name
        self.next = next

    def __repr__(self):
        return f"Node(name={self.name!r})"
