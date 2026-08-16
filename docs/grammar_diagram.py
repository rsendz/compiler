"""Render the Little Duck grammar as railroad diagrams.

    python3 -m pip install -r requirements-docs.txt
    python3 docs/grammar_diagram.py

Writes one SVG per section of the grammar, in two palettes -- so the README can
hand GitHub whichever one matches the reader's theme, and no single picture is
so tall that its text stops being legible once GitHub scales it to the page.

The productions below are transcribed from :mod:`littleduck.grammar`, with the
epsilon markers left out -- they exist to run a semantic action at a precise
point of a production and recognize nothing. Regenerate after changing the
grammar; the script is the only thing that keeps the picture honest.
"""

import io
import pathlib
import re

from railroad import (Choice, Comment, Diagram, NonTerminal, OneOrMore,
                      Optional, Sequence, Terminal, ZeroOrMore)

OUTPUT_DIRECTORY = pathlib.Path(__file__).resolve().parent

# Layout, in SVG user units.
MARGIN = 24
LABEL_HEIGHT = 30
RULE_GAP = 14
SECTION_GAP = 34


def token(text):
    """A literal the lexer produces: a reserved word or an operator."""
    return Terminal(text)


def rule(name):
    """A reference to another production."""
    return NonTerminal(name)


def separated(item, separator=','):
    """``item (separator item)*`` -- a comma-separated list of one or more."""
    return OneOrMore(item, token(separator))


def optional_list(item, separator=','):
    """A separated list that may also be empty."""
    return Optional(separated(item, separator))


# --- The grammar -----------------------------------------------------------

def sections():
    """The grammar, as ``(slug, title, [(name, diagram body)])`` sections.

    Built fresh on every call: rendering a diagram lays out its items in place,
    so the same objects cannot be drawn twice.
    """
    return [
        ("program", "Program and declarations", [
            ("Program", Sequence(
                token('program'), rule('IDENTIFIER'), token(';'),
                Optional(rule('VarSection')), ZeroOrMore(rule('Function')),
                token('main'), rule('Body'), token('end'))),
            ("VarSection", Sequence(token('var'), OneOrMore(rule('VarDecl')))),
            ("VarDecl", Sequence(
                rule('IdList'), token(':'), rule('Type'), token(';'))),
            ("IdList", separated(rule('Declarator'))),
            ("Declarator", Sequence(
                rule('IDENTIFIER'),
                Optional(Sequence(token('['), rule('CONST_INT'), token(']'))))),
            ("Type", Choice(
                0, token('int'), token('float'), token('string'),
                token('bool'))),
        ]),
        ("functions", "Functions", [
            ("Function", Sequence(
                rule('ReturnType'), rule('IDENTIFIER'),
                token('('), optional_list(rule('Param')), token(')'),
                token('['), Optional(rule('VarSection')), rule('Body'),
                token(']'), token(';'))),
            ("ReturnType", Choice(0, rule('Type'), token('void'))),
            ("Param", Sequence(rule('IDENTIFIER'), token(':'), rule('Type'))),
        ]),
        ("statements", "Statements", [
            ("Body", Sequence(
                token('{'), ZeroOrMore(rule('Statement')), token('}'))),
            ("Statement", Choice(
                0,
                rule('Declaration'), rule('Assignment'), rule('Condition'),
                rule('Loop'), rule('Call'), rule('Print'),
                rule('ReturnStatement'), rule('BreakStatement'))),
            ("Declaration", Sequence(
                token('var'), rule('IdList'), token(':'), rule('Type'),
                token(';'),
                Comment("visible until the end of the enclosing block"))),
            ("Assignment", Sequence(
                rule('IDENTIFIER'),
                Optional(Sequence(token('['), rule('Expression'), token(']'))),
                token('='), rule('Expression'), token(';'))),
            ("Condition", Sequence(
                token('if'), token('('), rule('Expression'), token(')'),
                rule('Body'),
                Optional(Sequence(token('else'), rule('Body'))), token(';'))),
            ("Loop", Sequence(
                token('do'), rule('Body'), token('while'),
                token('('), rule('Expression'), token(')'), token(';'))),
            ("Call", Sequence(
                rule('IDENTIFIER'), token('('),
                optional_list(rule('Expression')), token(')'), token(';'))),
            ("Print", Sequence(
                token('print'), token('('), separated(rule('Expression')),
                token(')'), token(';'))),
            ("ReturnStatement", Sequence(
                token('return'), Optional(rule('Expression')), token(';'))),
            ("BreakStatement", Sequence(token('break'), token(';'))),
        ]),
        ("expressions", "Expressions", [
            ("Expression", OneOrMore(rule('AndExpression'), token('or'))),
            ("AndExpression", OneOrMore(rule('NotExpression'), token('and'))),
            ("NotExpression", Sequence(
                ZeroOrMore(token('not')), rule('Comparison'))),
            ("Comparison", Sequence(
                rule('Exp'),
                Optional(Sequence(rule('RelOp'), rule('Exp'))),
                Comment("at most one, so a < b < c is rejected"))),
            ("RelOp", Choice(
                0, token('>'), token('<'), token('>='), token('<='),
                token('=='), token('!='))),
            ("Exp", OneOrMore(
                rule('Term'), Choice(0, token('+'), token('-')))),
            ("Term", OneOrMore(
                rule('Factor'), Choice(0, token('*'), token('/')))),
            ("Factor", Choice(
                0,
                Sequence(token('('), rule('Expression'), token(')')),
                Sequence(Optional(Choice(0, token('+'), token('-'))),
                         rule('Atom')))),
            ("Atom", Choice(
                0,
                Sequence(rule('IDENTIFIER'),
                         Optional(Sequence(token('['), rule('Expression'),
                                           token(']')))),
                rule('Constant'),
                Sequence(rule('IDENTIFIER'), token('('),
                         optional_list(rule('Expression')), token(')')))),
            ("Constant", Choice(
                0, rule('CONST_INT'), rule('CONST_FLOAT'), rule('CONST_STR'),
                token('true'), token('false'))),
        ]),
    ]


# --- Palettes --------------------------------------------------------------

PALETTES = {
    'light': {
        'background': '#ffffff',
        'line': '#57606a',
        'heading': '#1f2328',
        'label': '#1f2328',
        'comment': '#6e7781',
        'terminal_fill': '#ddf4ff',
        'terminal_line': '#54aeff',
        'terminal_text': '#0a3069',
        'rule_fill': '#f6f8fa',
        'rule_line': '#afb8c1',
        'rule_text': '#1f2328',
        'divider': '#d1d9e0',
    },
    'dark': {
        'background': '#0d1117',
        'line': '#8b949e',
        'heading': '#e6edf3',
        'label': '#e6edf3',
        'comment': '#8b949e',
        'terminal_fill': '#0c2d6b',
        'terminal_line': '#388bfd',
        'terminal_text': '#cae8ff',
        'rule_fill': '#161b22',
        'rule_line': '#30363d',
        'rule_text': '#e6edf3',
        'divider': '#21262d',
    },
}

STYLESHEET = """
  svg.grammar {{ background: {background}; }}
  svg.grammar path {{ stroke-width: 2; stroke: {line}; fill: none; }}
  svg.grammar text {{
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 13px; text-anchor: middle;
  }}
  svg.grammar g.terminal rect {{
    stroke-width: 2; stroke: {terminal_line}; fill: {terminal_fill};
  }}
  svg.grammar g.terminal text {{ fill: {terminal_text}; font-weight: 600; }}
  svg.grammar g.non-terminal rect {{
    stroke-width: 2; stroke: {rule_line}; fill: {rule_fill};
  }}
  svg.grammar g.non-terminal text {{ fill: {rule_text}; }}
  svg.grammar text.comment {{
    font-style: italic; font-size: 12px; fill: {comment};
  }}
  svg.grammar text.heading {{
    text-anchor: start; font-size: 15px; font-weight: 700; fill: {heading};
    letter-spacing: 0.06em; text-transform: uppercase;
  }}
  svg.grammar text.rule-name {{
    text-anchor: start; font-size: 14px; font-weight: 700; fill: {label};
  }}
  svg.grammar line.divider {{ stroke: {divider}; stroke-width: 1; }}
"""


# --- Rendering -------------------------------------------------------------

def render(body):
    """Render one production, returning ``(width, height, svg fragment)``."""
    diagram = Diagram(body, type='complex')
    buffer = io.StringIO()
    diagram.writeSvg(buffer.write)
    svg = buffer.getvalue()
    header = re.match(r'<svg[^>]*>', svg).group(0)
    width = float(re.search(r'width="([\d.]+)"', header).group(1))
    height = float(re.search(r'height="([\d.]+)"', header).group(1))
    inner = svg[len(header):svg.rindex('</svg>')]
    # The stylesheet already says so, but a path with no fill attribute of its
    # own renders as a filled blob wherever the stylesheet does not reach it.
    inner = inner.replace('<path ', '<path fill="none" ')
    return width, height, inner


def build(title, rules, palette_name):
    """Compose one section's productions into a single SVG document."""
    palette = PALETTES[palette_name]
    rendered = [(name,) + render(body) for name, body in rules]

    width = max(item[1] for item in rendered) + 2 * MARGIN

    parts = ['<text class="heading" x="%d" y="%d">%s</text>'
             % (MARGIN, MARGIN + 14, title)]
    y = MARGIN + LABEL_HEIGHT

    for name, _, height, inner in rendered:
        parts.append('<text class="rule-name" x="%d" y="%.1f">%s</text>'
                     % (MARGIN, y + 12, name))
        y += 20
        parts.append('<g transform="translate(%d %.1f)">%s</g>'
                     % (MARGIN, y, inner))
        y += height + RULE_GAP
    y += MARGIN - RULE_GAP

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" class="grammar" '
        'width="%.1f" height="%.1f" viewBox="0 0 %.1f %.1f">\n'
        '<style>%s</style>\n'
        '<rect width="100%%" height="100%%" fill="%s"/>\n'
        '%s\n</svg>\n'
        % (width, y, width, y, STYLESHEET.format(**palette),
           palette['background'], '\n'.join(parts)))


def main():
    for palette_name in PALETTES:
        # Rebuilt per palette: rendering lays the items out in place, so a
        # diagram object cannot be drawn a second time.
        for slug, title, rules in sections():
            path = OUTPUT_DIRECTORY / ("grammar-%s-%s.svg"
                                       % (slug, palette_name))
            path.write_text(build(title, rules, palette_name))
            print("wrote %s" % path.name)


if __name__ == '__main__':
    main()
