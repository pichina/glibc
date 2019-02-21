#! /usr/bin/python3
# Copyright (C) 2019 Free Software Foundation, Inc.
# This file is part of the GNU C Library.
#
# The GNU C Library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# The GNU C Library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with the GNU C Library; if not, see
# <http://www.gnu.org/licenses/>.

"""Verifies that installed headers do not use any obsolete constructs:
 * legacy BSD typedefs superseded by <stdint.h>:
   ushort uint ulong u_short u_int u_long u_intNN_t quad_t u_quad_t
   (sys/types.h is allowed to _define_ these types, but not to use them
    to define anything else).
"""

import argparse
import collections
import re
import sys

# Simplified lexical analyzer for C preprocessing tokens.
# Does not implement trigraphs.
# Does not implement backslash-newline in the middle of any lexical
#   item other than a string literal.
# Does not implement universal-character-names in identifiers.
# Does not implement header-names.
# Treats prefixed strings (e.g. L"...") as two tokens (L and "...")
# Accepts non-ASCII characters only within comments and strings.

# Caution: The order of the outermost alternation matters.
# STRING must be before BAD_STRING, CHARCONST before BAD_CHARCONST,
# BLOCK_COMMENT before BAD_BLOCK_COM before PUNCTUATOR, and OTHER must
# be last.
# Caution: There should be no capturing groups other than the named
# captures in the outermost alternation.

# For reference, these are all of the C punctuators as of C11:
#   [ ] ( ) { } , ; ? ~
#   ! != * *= / /= ^ ^= = ==
#   # ##
#   % %= %> %: %:%:
#   & &= &&
#   | |= ||
#   + += ++
#   - -= -- ->
#   . ...
#   : :>
#   < <% <: << <<= <=
#   > >= >> >>=

# The BAD_* tokens are not part of the official definition of pp-tokens;
# they match unclosed strings, character constants, and block comments,
# so that the regex engine doesn't have to backtrack all the way to the
# beginning of a broken construct and then emit dozens of junk tokens.

PP_TOKEN_RE_ = re.compile(r'''
    (?P<STRING>        \"(?:[^\"\\\r\n]|\\(?:[\r\n -~]|\r\n))*\")
   |(?P<BAD_STRING>    \"(?:[^\"\\\r\n]|\\[ -~])*)
   |(?P<CHARCONST>     \'(?:[^\'\\\r\n]|\\(?:[\r\n -~]|\r\n))*\')
   |(?P<BAD_CHARCONST> \'(?:[^\'\\\r\n]|\\[ -~])*)
   |(?P<BLOCK_COMMENT> /\*(?:\*(?!/)|[^*])*\*/)
   |(?P<BAD_BLOCK_COM> /\*(?:\*(?!/)|[^*])*\*?)
   |(?P<LINE_COMMENT>  //[^\r\n]*)
   |(?P<IDENT>         [_a-zA-Z][_a-zA-Z0-9]*)
   |(?P<PP_NUMBER>     \.?[0-9](?:[0-9a-df-oq-zA-DF-OQ-Z_.]|[eEpP][+-]?)*)
   |(?P<PUNCTUATOR>
       [,;?~(){}\[\]]
     | [!*/^=]=?
     | \#\#?
     | %(?:[=>]|:(?:%:)?)?
     | &[=&]?
     |\|[=|]?
     |\+[=+]?
     | -[=->]?
     |\.(?:\.\.)?
     | :>?
     | <(?:[%:]|<(?:=|<=?)?)?
     | >(?:=|>=?)?)
   |(?P<ESCNL>         \\(?:\r|\n|\r\n))
   |(?P<WHITESPACE>    [ \t\n\r\v\f]+)
   |(?P<OTHER>         .)
''', re.DOTALL | re.VERBOSE)
ENDLINE_RE_ = re.compile(r'\r|\n|\r\n')

# based on the sample code in the Python re documentation
Token_ = collections.namedtuple('Token', (
    'kind', 'text', 'line', 'column'))
def tokenize_c(file_contents):
    Token = Token_
    PP_TOKEN_RE = PP_TOKEN_RE_
    ENDLINE_RE = ENDLINE_RE_

    line_num = 1
    line_start = 0
    for mo in PP_TOKEN_RE.finditer(file_contents):
        kind = mo.lastgroup
        text = mo.group()
        line = line_num
        column = mo.start() - line_start
        # only these kinds can contain a newline
        if kind in ('WHITESPACE', 'BLOCK_COMMENT', 'LINE_COMMENT',
                    'STRING', 'CHARCONST', 'BAD_BLOCK_COM', 'ESCNL'):
            adj_line_start = 0
            for tmo in ENDLINE_RE.finditer(text):
                line_num += 1
                adj_line_start = tmo.end()
            if adj_line_start:
                line_start = mo.start() + adj_line_start
                if kind == 'WHITESPACE':
                    kind = 'NL'
        yield Token(kind, text, line, column + 1)

#
# Base and generic classes for individual checks.
#

class ConstructChecker:
    """Scan a stream of C preprocessing tokens and possibly report
       problems with them.  The REPORTER object passed to __init__ has
       one method, reporter.error(token, message), which should be
       called to indicate a problem detected at the position of TOKEN.
       If MESSAGE contains the four-character sequence '{!r}' then that
       will be replaced with a textual representation of TOKEN.
    """
    def __init__(self, reporter):
        self.reporter = reporter

    def examine(self, tok, directive):
        """Called once for each token in a header file.  'tok' will
           never be a BAD_STRING, BAD_CHARCONST, or BAD_BLOCK_COM, but
           can be a BLOCK_COMMENT, LINE_COMMENT, WHITESPACE, NL,
           ESCNL, or OTHER token.  'directive' is None if the current
           token is not part of a preprocessing directive line, or the
           name of the directive if it is.  (The '#' at the beginning
           of a directive line is supplied with directive equal to
           '<null>'.)

           Call self.reporter.error if a problem is detected.
        """
        raise NotImplementedError

    def eof(self):
        """Called once at the end of the stream.  Subclasses need only
           override this if it might have something to do."""
        pass

class NoCheck(ConstructChecker):
    """Generic checker class which doesn't do anything.  Substitute this
       class for a real checker when a particular check should be skipped
       for some file."""

    def examine(self, tok, directive):
        pass

#
# Check for obsolete type names.
#

# The obsolete type names we're looking for:
OBSOLETE_TYPE_RE_ = re.compile(r'''\A
  (__)?
  (   quad_t
    | register_t
    | u(?: short | int | long
         | _(?: char | short | int(?:[0-9]+_t)? | long | quad_t )))
\Z''', re.VERBOSE)

class ObsoleteNotAllowed(ConstructChecker):
    """Don't allow any use of the obsolete typedefs."""
    def examine(self, tok, directive):
        if OBSOLETE_TYPE_RE_.match(tok.text):
            self.reporter.error(tok, "use of {!r}")

class ObsoletePrivateDefinitionsAllowed(ConstructChecker):
    """Allow definitions of the private versions of the
       obsolete typedefs; that is, 'typedef [anything] __obsolete;'
    """
    def __init__(self, reporter):
        super().__init__(reporter)
        self.in_typedef = False
        self.prev_token = None

    def examine(self, tok, directive):
        # bits/types.h hides 'typedef' in a macro sometimes.
        if (tok.kind == 'IDENT'
            and tok.text in ('typedef', '__STD_TYPE')
            and directive is None):
            self.in_typedef = True
        elif tok.kind == 'PUNCTUATOR' and tok.text == ';' and self.in_typedef:
            self.in_typedef = False
            if self.prev_token.kind == 'IDENT':
                m = OBSOLETE_TYPE_RE_.match(self.prev_token.text)
                if m and m.group(1) != '__':
                    self.reporter.error(self.prev_token, "use of {!r}")
            self.prev_token = None
        else:
            self._check_prev()

        self.prev_token = tok

    def eof(self):
        self._check_prev()

    def _check_prev(self):
        if (self.prev_token is not None
            and self.prev_token.kind == 'IDENT'
            and OBSOLETE_TYPE_RE_.match(self.prev_token.text)):
            self.reporter.error(self.prev_token, "use of {!r}")

class ObsoletePublicDefinitionsAllowed(ConstructChecker):
    """Allow definitions of the public versions of the obsolete
       typedefs.  Only specific forms of definition are allowed:

           typedef __obsolete obsolete;  // identifiers must agree
           typedef __uintN_t u_intN_t;   // N must agree
           typedef unsigned long int ulong;
           typedef unsigned short int ushort;
           typedef unsigned int uint;
    """
    def __init__(self, reporter):
        super().__init__(reporter)
        self.typedef_tokens = []

    def examine(self, tok, directive):
        if tok.kind in ('WHITESPACE', 'BLOCK_COMMENT',
                        'LINE_COMMENT', 'NL', 'ESCNL'):
            pass

        elif (tok.kind == 'IDENT' and tok.text == 'typedef'
              and directive is None):
            if self.typedef_tokens:
                self.reporter.error(tok, "typedef inside typedef")
                self._reset()
            self.typedef_tokens.append(tok)

        elif tok.kind == 'PUNCTUATOR' and tok.text == ';':
            self._finish()

        elif self.typedef_tokens:
            self.typedef_tokens.append(tok)

    def eof(self):
        self._reset()

    def _reset(self):
        while self.typedef_tokens:
            tok = self.typedef_tokens.pop(0)
            if tok.kind == 'IDENT' and OBSOLETE_TYPE_RE_.match(tok.text):
                self.reporter.error(tok, "use of {!r}")

    def _finish(self):
        if not self.typedef_tokens: return
        if self.typedef_tokens[-1].kind == 'IDENT':
            m = OBSOLETE_TYPE_RE_.match(self.typedef_tokens[-1].text)
            if self._permissible_public_definition(m):
                self.typedef_tokens.clear()
        self._reset()

    def _permissible_public_definition(self, m):
        if not m: return False
        if m.group(1) == '__': return False
        name = m.group(2)
        tk = self.typedef_tokens
        ntok = len(tk)
        if ntok == 3 and tk[1].kind == 'IDENT':
            defn = tk[1].text
            n = OBSOLETE_TYPE_RE_.match(defn)
            if n and n.group(1) == '__' and n.group(2) == name:
                return True

            if (name[:5] == 'u_int' and name[-2:] == '_t'
                and defn[:6] == '__uint' and defn[-2:] == '_t'
                and name[5:-2] == defn[6:-2]):
                return True

            return False

        if (name == 'ulong' and ntok == 5
            and tk[1].kind == 'IDENT' and tk[1].text == 'unsigned'
            and tk[2].kind == 'IDENT' and tk[2].text == 'long'
            and tk[3].kind == 'IDENT' and tk[3].text == 'int'):
            return True

        if (name == 'ushort' and ntok == 5
            and tk[1].kind == 'IDENT' and tk[1].text == 'unsigned'
            and tk[2].kind == 'IDENT' and tk[2].text == 'short'
            and tk[3].kind == 'IDENT' and tk[3].text == 'int'):
            return True

        if (name == 'uint' and ntok == 4
            and tk[1].kind == 'IDENT' and tk[1].text == 'unsigned'
            and tk[2].kind == 'IDENT' and tk[2].text == 'int'):
            return True

        return False

def ObsoleteTypedefChecker(reporter, fname):
    """Factory: produce an instance of the appropriate
       obsolete-typedef checker for FNAME."""

    # The obsolete rpc/ and rpcsvc/ headers are allowed to use the
    # obsolete types, because it would be more trouble than it's
    # worth to remove them from headers that we intend to stop
    # installing eventually anyway.
    if (fname.startswith("rpc/")
        or fname.startswith("rpcsvc/")
        or "/rpc/" in fname
        or "/rpcsvc/" in fname):
        return NoCheck(reporter)

    # bits/types.h is allowed to define the __-versions of the
    # obsolete types.
    if (fname == "bits/types.h"
        or fname.endswith("/bits/types.h")):
        return ObsoletePrivateDefinitionsAllowed(reporter)

    # sys/types.h is allowed to use the __-versions of the
    # obsolete types, but only to define the unprefixed versions.
    if (fname == "sys/types.h"
        or fname.endswith("/sys/types.h")):
        return ObsoletePublicDefinitionsAllowed(reporter)

    return ObsoleteNotAllowed(reporter)

#
# Master control
#

MODELINE_RE_ = re.compile(r"""
  \A [^\n\r]*? -\*- [ \t]* (?:
      ([a-zA-Z0-9+-]+) [ \t]* |
      [^\n\r]*? mode: [ \t]* ([a-zA-Z0-9+-]+) ; [^\n\r]*?
  ) -\*-
""", re.VERBOSE)
def tagged_as_not_c_family(contents):
    """True if the first line of CONTENTS contains an emacs-style
       "mode" specification that identifies it as something other than
       C, C++, ObjC, or ObjC++.

       This does not implement the complete syntax of -*- ... -*- as
       understood by Emacs; in particular, it must appear on the very
       first line (Emacs will look at the second line of the file
       under some circumstances), and the -*- ... mode: MODE; ... -*-
       form only honors the leftmost occurrence of 'mode:' (comments
       in files.el from Emacs 26 indicate that processing 'mode:' more
       than once is a deprecated feature).  It doesn't look for a
       "Local Variables:" section at the end of the file, either.
       Finally, Elisp accepts arbitrary nonwhitespace characters in
       the name of a symbol, but we only allow [A-Za-z0-9+-].
    """
    m = MODELINE_RE_.match(contents)
    if not m: return False
    mode = (m.group(1) or m.group(2)).lower()
    return mode not in ("c", "c++", "objc", "objc++")

class HeaderChecker:
    def __init__(self):
        self.fname = None
        self.status = 0

    def error(self, tok, message):
        self.status = 1
        if '{!r}' in message:
            message = message.format(tok.text)
        sys.stderr.write("{}:{}:{}: error: {}\n".format(
            self.fname, tok.line, tok.column, message))

    def check(self, fname):
        self.fname = fname
        try:
            with open(fname, "rt") as fp:
                contents = fp.read()
        except OSError as e:
            sys.stderr.write("{}: {}\n".format(fname, e.strerror))
            self.status = 1
            return
        if tagged_as_not_c_family(contents):
            return

        typedef_checker = ObsoleteTypedefChecker(self, self.fname)

        at_bol = True
        current_directive = None
        Token = Token_
        for tok in tokenize_c(contents):
            # Track whether or not we are scanning a preprocessing directive.
            kind = tok.kind
            if kind == 'LINE_COMMENT' or kind == 'NL':
                at_bol = True
                current_directive = None
            else:
                if kind == 'PUNCTUATOR' and tok.text == '#' and at_bol:
                    current_directive = '<null>'
                at_bol = False
                if kind == 'IDENT' and current_directive == '<null>':
                    current_directive = tok.text

            # Report ill-formed tokens and rewrite them as their well-formed
            # equivalents, so the checkers don't have to worry about them.
            # (Rewriting instead of discarding provides better error recovery.)
            if kind == 'BAD_BLOCK_COM':
                self.error(tok, "unclosed block comment")
                tok = Token('BLOCK_COMMENT', tok.text, tok.line, tok.column)
            elif kind == 'BAD_STRING':
                self.error(tok, "unclosed string")
                tok = Token('STRING', tok.text, tok.line, tok.column)
            elif kind == 'BAD_CHARCONST':
                self.error(tok, "unclosed char constant")
                tok = Token('CHARCONST', tok.text, tok.line, tok.column)
            elif kind == 'OTHER':
                # Do not complain about OTHER tokens inside macro definitions.
                # $ and @ appear in macros defined by headers intended to be
                # included from assembly language, e.g. sysdeps/mips/sys/asm.h.
                if current_directive != 'define':
                    self.error(tok, "stray {!r} in program")

            typedef_checker.examine(tok, current_directive)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("headers", metavar="header", nargs="+",
                    help="one or more headers to scan for obsolete constructs")
    args = ap.parse_args()

    checker = HeaderChecker()
    for fname in args.headers:
        checker.check(fname)
    sys.exit(checker.status)

main()
