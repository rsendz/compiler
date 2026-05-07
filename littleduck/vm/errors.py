"""Errors raised while a program is running."""


class VMRuntimeError(Exception):
    """A fault detected during execution: bad memory access, division by zero…"""

    def __init__(self, message, quad_number=None):
        super().__init__(message)
        self.message = message
        self.quad_number = quad_number

    def describe(self):
        if self.quad_number is None:
            return "Runtime error: %s" % self.message
        return "Runtime error (quadruple %d): %s" % (self.quad_number,
                                                     self.message)
