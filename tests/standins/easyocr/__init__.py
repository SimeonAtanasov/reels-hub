"""Stand-in for easyocr in the tests: every image reads as one short made-up line, no model download."""


class Reader:
    def __init__(self, *a, **k):
        self.n = 0

    def readtext(self, path):
        self.n += 1
        return [([[0, 0], [1, 0], [1, 1], [0, 1]], f"TEST TEXT {self.n}", 0.9)]
