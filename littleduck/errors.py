"""Error collection and reporting for every compilation phase.

The compiler never raises on a user error: each phase appends a message to the
shared :class:`ErrorLog` and keeps going, so a single run reports as many
problems as it can. Only :class:`TooManyErrors` interrupts the process, and it
does so to break out of a parser recovery loop, not to report a user error.
"""


class TooManyErrors(Exception):
    """Raised when syntax errors pile up past the recovery limit."""


class ErrorLog:
    """Holds the errors found by the lexer, the parser and the semantic pass."""

    def __init__(self):
        self.lexical = []
        self.syntax = []
        self.semantic = []
        self.recovery_notes = []

    def clear(self):
        self.lexical.clear()
        self.syntax.clear()
        self.semantic.clear()
        self.recovery_notes.clear()

    # -- Recording ---------------------------------------------------------
    def add_lexical(self, message):
        self.lexical.append(message)

    def add_syntax(self, message):
        self.syntax.append(message)

    def add_semantic(self, message, line=0):
        self.semantic.append(message + at_line(line))

    def add_recovery_note(self, message):
        self.recovery_notes.append(message)

    # -- Querying ----------------------------------------------------------
    @property
    def has_errors(self):
        return bool(self.lexical or self.syntax or self.semantic)

    def summary(self):
        return ("Total: %d lexical + %d syntax + %d semantic"
                % (len(self.lexical), len(self.syntax), len(self.semantic)))

    def report(self):
        """Return the full error report as a list of printable lines."""
        lines = []
        if self.lexical:
            lines.append("")
            lines.append("%d lexical error(s):" % len(self.lexical))
            lines.extend("  - " + e for e in self.lexical)
        if self.syntax:
            lines.append("")
            lines.append("%d syntax error(s):" % len(self.syntax))
            lines.extend("  - " + e for e in self.syntax)
            lines.extend(self.recovery_notes)
        if self.semantic:
            lines.append("")
            lines.append("%d semantic error(s):" % len(self.semantic))
            lines.extend("  - " + e for e in self.semantic)
        return lines


def at_line(line):
    """Format the ' (line N)' suffix appended to semantic error messages."""
    return (" (line %d)" % line) if line else ""
